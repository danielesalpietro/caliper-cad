"""
CALIPER — Livello 3, esecutore ISOLATO e NON FIDATO di un singolo job.

Invocato come processo separato (mai in-process nel watcher), con limiti
di risorse impostati PRIMA di eseguire il codice non fidato. Il
container che lo ospita ha network_mode: none (vedi docker-compose.yml)
— questo script non presume l'isolamento di rete, lo eredita dal
container, ma applica comunque limiti di CPU/memoria per singolo job
perche' il watcher e' un processo a lunga vita e job successivi non
devono poter degradarsi a vicenda.

[M5, C7 — vedi docs/review_tecnica.md, proposta P5] Fino a M4 questo
script eseguiva exec(code) E scriveva il verdetto finale (result.json)
nello STESSO processo: il codice non fidato poteva quindi leggere
sys.argv, scrivere un result.json contraffatto ("execution": "PASS") e
chiamare os._exit(0) per uscire prima che qualunque controllo reale
avvenisse — un PASS falsificabile dal codice sotto giudizio (vedi
verify_verdict_integrity.py per la fixture avversariale). Split adottato:

- QUESTO processo (non fidato) esegue exec(code) ed esporta SOLO lo STEP
  del solido 'result' — non scrive MAI il verdetto finale (result.json):
  scrive solo uno "stato di esportazione" interno (export_status.json,
  mai il nome/formato del verdetto pubblico) che measure_verdict.py (il
  processo FIDATO, mai exec di codice esterno, vedi la sua docstring)
  legge come semplice DIAGNOSTICA, non come fonte di verita' — l'unica
  fonte di verita' e' lo STEP effettivamente scritto su disco, che
  measure_verdict.py re-importa e ri-misura indipendentemente da zero.
- watcher.py chiama SEMPRE measure_verdict.py dopo questo script (a meno
  di timeout, vedi la sua docstring) e measure_verdict.py e' l'UNICO
  scrittore di result.json, incondizionatamente (sovrascrive qualunque
  file gia' presente li' — questo, non la segretezza del path, e' la
  difesa reale contro il PASS contraffatto: anche se il codice non
  fidato indovina il path di result.json e ci scrive comunque prima di
  uscire, measure_verdict.py lo sovrascrive subito dopo con il proprio
  verdetto, calcolato da zero).

Uso: python run_and_measure.py <job.json> <export_status.json>

[M3] Esportazione STEP del pezzo validato (gauge_check.py, vedi
docs/logbook_fase3.md): /models e' montato READ-ONLY in
verifier-executor (docker-compose.yml, per i pezzi di riferimento
statici di M1/M2 — l'AI non doveva entrarci) — un pezzo appena generato
non puo' finire li'. Va invece scritto sotto /exec/parts, sottocartella
dello stesso volume verifier_exec gia' condiviso e scrivibile in questo
container (nessun nuovo mount). gauge_check.py risolve questa cartella
con una root separata (GENERATED_PARTS_ROOT, part_source="generated"),
mai confusa con /models (part_source="models", invariato per gli
script M1/M2 esistenti) — vedi gauge_check.py per il motivo per cui le
due radici restano distinte anche se ora coesistono nello stesso
volume.

Riserva onesta (C7, non risolta qui): l'handoff chiede anche di togliere
la leggibilita' di /models a QUESTO processo specifico (non gli serve,
non fa gauge-check) — ma /models e' montato a livello dell'intero
container verifier-executor (docker-compose.yml), condiviso da tutti i
sottoprocessi (questo, measure_verdict.py, gauge_check.py): docker-compose
non offre un modo per montare un volume solo per UN sottoprocesso dentro
lo stesso container, servirebbe un container/namespace separato per
questo stadio — cambio architetturale piu' grande, fuori scope per
questa milestone (non e' un'assunzione silenziosa: la vera difesa contro
la falsificazione resta la separazione processo-che-esegue /
processo-che-giudica sopra, non l'irraggiungibilita' di /models).
"""

import json
import os
import resource
import sys

# OpenBLAS (usato da numpy/cadquery) prealloca un pool di memoria per
# thread in base ai core visibili, non alla dimensione del problema —
# senza questo, anche uno sweep minuscolo puo' esaurire un limite di
# memoria per processo apparentemente generoso. Va impostato prima che
# numpy venga importato (quindi qui, prima di "import cadquery" nel
# codice eseguito piu' sotto).
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
# [run0 RunPod, docs/logbook_runpod_run0.md] VTK (dipendenza di OCP)
# ha un SUO pool SMP, dimensionato sui core VISIBILI — su un pod con
# nproc=256 ma quota cgroup ~27 vCPU il pool esplode comunque anche
# con OpenBLAS/OMP a 1. Stessa disciplina: 1 di default, prima di
# qualunque import di cadquery/OCP.
os.environ.setdefault("VTK_SMP_MAX_THREADS", "1")

# Sovrascrivibile via env per uso/test fuori Docker — stesso pattern
# gia' in uso in gauge_check.py per MODELS_ROOT/GAUGES_ROOT.
GENERATED_PARTS_DIR = os.environ.get("GENERATED_PARTS_DIR", "/exec/parts")

# Limite di memoria per job, sovrascrivibile via env [run0 RunPod]:
# il default 2GB e' tarato sui container di produzione (docker-compose,
# core limitati); su host con centinaia di core visibili le librerie
# native prenotano stack/pool per thread in base a nproc e 2GB di
# address space non bastano nemmeno a partire (SIGSEGV/allocazione TLS
# fallita — vedi docs/logbook_runpod_run0.md). Il default NON cambia:
# chi ha quel problema alza il limite nel template/env del pod con
# giustificazione, invece di toccare il codice.
CALIPER_AS_LIMIT_MB = int(os.environ.get("CALIPER_AS_LIMIT_MB", "2048"))
# Se impostata, limita anche lo stack: glibc usa RLIMIT_STACK come
# dimensione di default degli stack dei pthread — con 256 thread da
# 8MB sono 2GB di address space prenotati solo di stack. 2 (MB) e' un
# valore sensato sul pod; di default NON viene toccato nulla.
CALIPER_STACK_LIMIT_MB = os.environ.get("CALIPER_STACK_LIMIT_MB", "")


def set_limits():
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))  # 10s di CPU
    as_bytes = CALIPER_AS_LIMIT_MB * 1024**2
    resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
    if CALIPER_STACK_LIMIT_MB:
        stack_bytes = int(CALIPER_STACK_LIMIT_MB) * 1024**2
        resource.setrlimit(resource.RLIMIT_STACK, (stack_bytes, stack_bytes))


def write_export_status(path, status):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(status, f)


def main():
    job_path, export_status_path = sys.argv[1], sys.argv[2]
    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)
    code = job["code"]

    # [M5, C7] Nessun campo qui non e' un verdetto: "export_ok" dice solo
    # se QUESTO processo e' riuscito a esportare uno STEP, mai se il
    # pezzo e' dimensionalmente corretto — quella decisione, e ogni
    # campo che il chiamante vedra' come "execution"/PASS/FAIL, spetta
    # solo a measure_verdict.py, sulla base dello STEP reimportato da
    # zero, non su questi campi presi per buoni.
    status = {"export_ok": False, "error": None, "generated_part_step_path": None}

    namespace = {}
    try:
        exec(code, namespace)  # nosec - codice non fidato, isolato per processo/container
    except Exception as e:
        status["error"] = f"{type(e).__name__}: {e}"
        write_export_status(export_status_path, status)
        return

    obj = namespace.get("result")
    if obj is None:
        status["error"] = "'result' non trovato dopo l'esecuzione"
        write_export_status(export_status_path, status)
        return

    try:
        solid = obj.val() if hasattr(obj, "val") else obj
        job_id = os.path.splitext(os.path.basename(job_path))[0]
        os.makedirs(GENERATED_PARTS_DIR, exist_ok=True)
        step_rel = f"{job_id}.step"
        solid.exportStep(os.path.join(GENERATED_PARTS_DIR, step_rel))
        status["export_ok"] = True
        status["generated_part_step_path"] = step_rel
    except Exception as e:
        status["error"] = f"esportazione STEP fallita: {type(e).__name__}: {e}"

    write_export_status(export_status_path, status)


if __name__ == "__main__":
    set_limits()
    main()
