"""
CALIPER — Livello 3, watcher dell'executor isolato.

Processo a lunga vita nel container `verifier-executor`
(network_mode: none — nessuna rete, ne' in ne' out, vedi
docker-compose.yml). Comunica col servizio `verifier` SOLO tramite un
volume condiviso: legge i job da /exec/jobs/, scrive i risultati in
/exec/results/. Ogni job viene eseguito in un sottoprocesso separato
con limiti di CPU/memoria propri e un timeout, cosi' un job che va in
loop infinito o alloca troppa memoria non degrada i job successivi.

Due tipi di job, instradati in base alla chiave presente nel JSON —
mai nello stesso sottoprocesso, con timeout indipendenti (vedi
docs/handoff_m1.md, "Vincoli gia' decisi": il gauge-check non deve
condividere budget con exec() di codice non fidato):

- "code"         -> run_and_measure.py (non fidato, esegue exec(code) e
                     ESPORTA SOLO lo STEP) + measure_verdict.py (fidato,
                     mai exec di codice esterno, SCRIVE il verdetto) —
                     due processi separati da M5 (vedi C7 in
                     docs/review_tecnica.md e le loro docstring): questo
                     watcher chiama SEMPRE measure_verdict.py dopo
                     run_and_measure.py (salvo timeout di quest'ultimo,
                     vedi process_code_job()) — measure_verdict.py e'
                     l'UNICO che scrive result.json, incondizionatamente,
                     sovrascrivendo qualunque file gia' presente li' (la
                     difesa reale contro un verdetto contraffatto dal
                     codice non fidato, non la segretezza del path).
- "gauge_check"  -> gauge_check.py (M1, calibro Go/No-Go virtuale,
                     vedi docs/logbook_fase1.md)
"""

import glob
import json
import os
import subprocess
import time

JOBS_DIR = "/exec/jobs"
RESULTS_DIR = "/exec/results"
CHECKPOINTS_DIR = "/exec/checkpoints"
POLL_INTERVAL_SECONDS = 0.5
SUBPROCESS_TIMEOUT_SECONDS = 15

# [M5, C7] Timeout del processo FIDATO (measure_verdict.py): mai exec di
# codice esterno, solo import STEP + bbox/confronto dimensionale — piu'
# economico di exec(code), ma non ancora misurato su un worst-case reale
# in questa milestone (nessuna istanza viva, stessa riserva onesta gia'
# applicata altrove). Stesso ordine di grandezza di SUBPROCESS_TIMEOUT_SECONDS
# come punto di partenza, non un numero misurato — da rivedere alla prima
# misura reale (regola #3 di docs/piano_recupero.md), non alzato a intuito.
MEASURE_VERDICT_TIMEOUT_SECONDS = 15

# [M5, C10 — vedi docs/review_tecnica.md] Gap dichiarato in PR #11, mai
# chiuso: run_and_measure.py esporta ogni pezzo generato sotto
# /exec/parts (stesso volume di JOBS_DIR/RESULTS_DIR) e non lo ha mai
# ripulito — crescita illimitata su un processo a lunga vita. Stessa
# cartella di GENERATED_PARTS_DIR in run_and_measure.py/gauge_check.py
# (sovrascrivibile via env per lo stesso motivo). Ritenzione generosa
# (default 24h): un pezzo serve al gauge-check subito dopo /verify, non
# per giorni — non un numero misurato su un worst-case, solo "abbastanza
# lungo da non cancellare un pezzo ancora in uso da un loop di retry
# realistico".
GENERATED_PARTS_DIR = os.environ.get("GENERATED_PARTS_DIR", "/exec/parts")
GENERATED_PARTS_RETENTION_SECONDS = int(os.environ.get("GENERATED_PARTS_RETENTION_SECONDS", str(24 * 3600)))
# Non ad ogni ciclo di poll (0.5s): una scansione periodica della
# cartella basta, il costo di scandirla ad ogni poll sarebbe sprecato.
GENERATED_PARTS_CLEANUP_INTERVAL_SECONDS = 300

# Timeout ESTERNO del gauge-check, indipendente da SUBPROCESS_TIMEOUT_SECONDS
# (usato solo per exec(code)). Tarato empiricamente in M2 (vedi
# docs/logbook_fase2.md, "Timeout e isolamento computazionale"): worst-case
# misurato per uno sweep elicoidale completo di TC2 e' ~65.5s di CPU-time,
# limite interno di gauge_check.py alzato a 100s (margine ~1.5x) — questo
# timeout esterno deve superare quel limite interno anche nel caso peggiore
# realistico (un solo core disponibile nel container, dove wall-clock ~
# CPU-time, a differenza del sandbox di misura che aveva piu' core), stesso
# rapporto ~1.5x gia' in uso per run_and_measure.py (10s interno / 15s
# esterno) e per il placeholder precedente di M1 (30s interno / 45s esterno).
# Overridabile via env [run1, E2E-7]: il percorso "TIMEOUT strutturato"
# (preflight_diagnostics + last_checkpoint) scatta SOLO su
# subprocess.TimeoutExpired, cioe' su QUESTO timeout — col valore
# hardcoded il test E2E-7 non poteva esercitarlo senza un job che
# impiegasse davvero >150s (vedi docs/logbook_fase6.md, E2E-7).
# Default 210 = limite interno di gauge_check (140, ricalibrato C8 sul
# worst-case run1 di 91.35s) x lo stesso rapporto ~1.5 di sempre.
GAUGE_CHECK_TIMEOUT_SECONDS = int(os.environ.get("GAUGE_CHECK_TIMEOUT_SECONDS", "210"))


def _write_result(result_path, data):
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _empty_result(error):
    return {"execution": "FAIL", "error": error, "measurements": None, "dimensional_check": None}


def process_code_job(job_path: str, job_id: str, result_path: str):
    # [M5, C7] Pulizia difensiva: un result_path gia' presente (es.
    # riavvio con job_id riusato, o — nello scenario avversariale che
    # questo split esiste per chiudere — un verdetto contraffatto
    # scritto direttamente dal codice non fidato, che conosce la stessa
    # convenzione di naming di RESULTS_DIR) non deve sopravvivere fino a
    # qui: measure_verdict.py scrivera' comunque il proprio verdetto
    # sotto, ma partire puliti evita ogni ambiguita' se anche quello
    # dovesse non arrivare a scrivere (vedi sotto).
    if os.path.exists(result_path):
        os.remove(result_path)

    export_status_path = os.path.join(RESULTS_DIR, f"{job_id}.export_status.json")
    if os.path.exists(export_status_path):
        os.remove(export_status_path)

    try:
        subprocess.run(
            ["python", "/app/run_and_measure.py", job_path, export_status_path],
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        # Il processo NON fidato non ha completato entro il suo budget —
        # nessuno STEP verificabile puo' esistere in modo affidabile
        # (potrebbe essere stato ucciso a meta' scrittura): stesso
        # trattamento di prima di M5, nessun tentativo di misurare un
        # export incompleto.
        _write_result(result_path, _empty_result(f"timeout dopo {SUBPROCESS_TIMEOUT_SECONDS}s"))
        if os.path.exists(export_status_path):
            os.remove(export_status_path)
        return

    # [M5, C7] measure_verdict.py (processo FIDATO, mai exec di codice
    # esterno) viene chiamato SEMPRE qui, indipendentemente da cosa
    # run_and_measure.py ha scritto o provato a scrivere altrove — e'
    # l'UNICO scrittore del verdetto pubblico, incondizionatamente (vedi
    # la sua docstring e verify_verdict_integrity.py per la fixture
    # avversariale che questo chiude).
    try:
        subprocess.run(
            ["python", "/app/measure_verdict.py", job_path, export_status_path, result_path],
            timeout=MEASURE_VERDICT_TIMEOUT_SECONDS,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        _write_result(result_path, _empty_result(f"timeout del processo fidato dopo {MEASURE_VERDICT_TIMEOUT_SECONDS}s"))

    if os.path.exists(export_status_path):
        os.remove(export_status_path)

    if not os.path.exists(result_path):
        # measure_verdict.py e' morto senza scrivere nulla (es. crash interprete)
        _write_result(result_path, _empty_result("il sottoprocesso fidato non ha prodotto un risultato"))


def process_gauge_check_job(job_path: str, result_path: str, job_id: str, gauge_check_spec: dict):
    checkpoint_path = os.path.join(CHECKPOINTS_DIR, f"{job_id}.json")

    try:
        subprocess.run(
            ["python", "/app/gauge_check.py", job_path, result_path, checkpoint_path],
            timeout=GAUGE_CHECK_TIMEOUT_SECONDS,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        # SIGKILL non lascia nulla da ispezionare: l'unico dato disponibile
        # e' il checkpoint scritto da gauge_check.py PRIMA dell'operazione
        # pesante (vedi docs/logbook_fase2.md, "Formato del log su TIMEOUT").
        # Puo' non esistere se il timeout e' scattato ancora prima (es.
        # import STEP molto lento). Generico rispetto alla modalita' (M2
        # aggiunge "sweep"/"min_distance" a "static_interference" di M1):
        # il checkpoint stesso porta "mode" e, per uno sweep interrotto a
        # meta', "last_checkpoint" con l'ultimo step TENTATO — vedi
        # gauge_check.py per il formato completo scritto ad ogni step.
        checkpoint = {}
        if os.path.exists(checkpoint_path):
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                checkpoint = json.load(f)

        result = _empty_result("gauge_check_timeout")
        result["gauge_check"] = {
            "status": "TIMEOUT",
            "mode": checkpoint.get("mode", gauge_check_spec.get("mode", "static_interference")),
            "part_step_path": gauge_check_spec.get("part_step_path"),
            "gauge_step_path": gauge_check_spec.get("gauge_step_path"),
            "interference_volume_mm3": None,
            "timeout_seconds": GAUGE_CHECK_TIMEOUT_SECONDS,
            "preflight_diagnostics": checkpoint.get("preflight_diagnostics"),
            "last_checkpoint": checkpoint.get("last_checkpoint"),
            "source": "virtual",
        }
        _write_result(result_path, result)

    if not os.path.exists(result_path):
        _write_result(result_path, _empty_result("il sottoprocesso non ha prodotto un risultato"))

    if os.path.exists(checkpoint_path):
        os.remove(checkpoint_path)


def process_job(job_path: str):
    job_id = os.path.splitext(os.path.basename(job_path))[0]
    result_path = os.path.join(RESULTS_DIR, f"{job_id}.json")

    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)

    if "gauge_check" in job:
        process_gauge_check_job(job_path, result_path, job_id, job["gauge_check"])
    elif "code" in job:
        process_code_job(job_path, job_id, result_path)
    else:
        _write_result(result_path, _empty_result("job non valido: attesa la chiave 'code' o 'gauge_check'"))

    os.remove(job_path)


def cleanup_generated_parts(now: float | None = None):
    """[M5, C10] Rimuove sotto GENERATED_PARTS_DIR i file piu' vecchi di
    GENERATED_PARTS_RETENTION_SECONDS — gap dichiarato in PR #11, mai
    chiuso (vedi sopra). Non solleva se la cartella non esiste ancora
    (nessun job "code" e' mai stato processato) ne' per un singolo file
    illeggibile/gia' rimosso da un'altra iterazione (best-effort, non
    e' un'operazione critica per la correttezza del verdetto)."""
    now = now if now is not None else time.time()
    if not os.path.isdir(GENERATED_PARTS_DIR):
        return
    for name in os.listdir(GENERATED_PARTS_DIR):
        path = os.path.join(GENERATED_PARTS_DIR, name)
        try:
            if now - os.path.getmtime(path) > GENERATED_PARTS_RETENTION_SECONDS:
                os.remove(path)
        except OSError:
            continue


def main():
    os.makedirs(JOBS_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(CHECKPOINTS_DIR, exist_ok=True)
    print("verifier-executor: in ascolto su", JOBS_DIR, flush=True)
    last_cleanup = 0.0
    while True:
        for job_path in glob.glob(os.path.join(JOBS_DIR, "*.json")):
            process_job(job_path)
        now = time.time()
        if now - last_cleanup >= GENERATED_PARTS_CLEANUP_INTERVAL_SECONDS:
            cleanup_generated_parts(now)
            last_cleanup = now
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
