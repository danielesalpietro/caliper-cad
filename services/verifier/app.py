"""
CALIPER — Livello 3, verificatore geometrico a doppia modalita'
------------------------------------------------------------------------
Script deterministico, NON un LLM (vedi Rischio #9: questo servizio non
vive dentro Flowise, viene richiamato da fuori come Custom Tool/chiamata
HTTP). Due fasi in sequenza:

1. Controllo statico (sempre, veloce, nessuna dipendenza pesante):
   sintassi Python (ast.parse), presenza di "import cadquery", presenza
   di una variabile 'result'.
2. Esecuzione + misura (solo se la fase 1 passa): il codice viene
   scritto come "job" su un volume condiviso e processato dal container
   `verifier-executor` — separato, network_mode: none (nessuna rete ne'
   in ne' out), limiti di CPU/memoria per job (vedi executor/). Questo
   servizio non esegue mai direttamente codice non fidato nel proprio
   processo: scrive il job, aspetta il risultato, lo legge.

Il confronto dimensionale (misura vs specifica L2.5) e' implementato
solo per feature="thread" per ora, unica con un preset definito (vedi
services/orchestrator/presets.json) — vedi Stato attuale in
docs/architettura-prototipo-mesh-llm.md.

LIMITE NOTO (fase 1): ast.parse() da solo non basta a rilevare ogni
"commento" LLM privo del prefisso '#' — una riga come "MISSING: feature"
e' sintatticamente Python valido (bare variable annotation, PEP 526).
Non e' una garanzia strutturale contro un output fatto *solo* di righe
in quella forma.
"""

import ast
import json
import os
import time
import uuid

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="CALIPER Livello 3 — verificatore")

JOBS_DIR = "/exec/jobs"
RESULTS_DIR = "/exec/results"
EXEC_TIMEOUT_SECONDS = 30
EXEC_POLL_INTERVAL_SECONDS = 0.3

# Attesa lato HTTP per il gauge-check (M1, vedi docs/logbook_fase1.md).
# Deve superare con margine il timeout esterno del watcher
# (GAUGE_CHECK_TIMEOUT_SECONDS in executor/watcher.py). [M3, bug trovato
# collegando il loop reale: questo valore era rimasto a 60s (tarato sul
# placeholder di M1, 45s esterno) e non e' mai stato aggiornato quando M2
# ha ricalibrato empiricamente il timeout del watcher a 150s (vedi
# docs/logbook_fase2.md, worst-case misurato ~65.5s CPU per lo sweep
# elicoidale completo di TC2) — l'endpoint HTTP avrebbe rinunciato ad
# aspettare PRIMA che il watcher dichiarasse un vero TIMEOUT diagnosticabile,
# restituendo il generico "nessuna risposta dall'executor" invece del
# risultato TIMEOUT strutturato (con preflight/last_checkpoint) che
# retry_policy.classify_checkpoint si aspetta. Stesso rapporto ~1.3x gia'
# in uso altrove tra timeout interno/esterno.
GAUGE_CHECK_HTTP_TIMEOUT_SECONDS = 200


class VerifyRequest(BaseModel):
    code: str
    spec: dict | None = None


class GaugeCheckRequest(BaseModel):
    part_step_path: str
    # Assente per "min_distance" (nessun secondo solido, vedi gauge_check.py)
    gauge_step_path: str | None = None
    mode: str = "static_interference"
    # [M3] "models" (default, invariato) o "generated" — vedi
    # gauge_check.py per la distinzione tra le due radici.
    part_source: str = "models"
    # Passati a gauge_check.py cosi' come sono, senza validazione qui —
    # la validazione (campi richiesti per modalita', valori ammessi) resta
    # nel job/result su volume condiviso, stesso confine di fiducia gia'
    # in uso per "code"/"spec" sopra (vedi Rischio #9: verifier non e' il
    # posto per eseguire o interpretare, solo per instradare).
    sweep: dict | None = None
    min_distance: dict | None = None


def check_python_syntax(code: str):
    try:
        tree = ast.parse(code)
        return {"name": "python_syntax", "status": "PASS", "detail": None}, tree
    except SyntaxError as e:
        detail = f"{e.msg} at line {e.lineno}, column {e.offset}"
        return {"name": "python_syntax", "status": "FAIL", "detail": detail}, None


def check_cadquery_import(tree):
    if tree is None:
        return {"name": "cadquery_import", "status": "SKIPPED", "detail": "syntax invalid, cannot check"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "cadquery" for alias in node.names):
                return {"name": "cadquery_import", "status": "PASS", "detail": None}
        if isinstance(node, ast.ImportFrom) and node.module == "cadquery":
            return {"name": "cadquery_import", "status": "PASS", "detail": None}
    return {"name": "cadquery_import", "status": "FAIL", "detail": "no 'import cadquery' statement found"}


def check_result_assigned(tree):
    if tree is None:
        return {"name": "result_variable", "status": "SKIPPED", "detail": "syntax invalid, cannot check"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "result":
                    return {"name": "result_variable", "status": "PASS", "detail": None}
    return {
        "name": "result_variable",
        "status": "FAIL",
        "detail": "no assignment to a variable named 'result' found",
    }


def run_execution_check(code: str, spec: dict | None):
    os.makedirs(JOBS_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)

    job_id = str(uuid.uuid4())
    job_path = os.path.join(JOBS_DIR, f"{job_id}.json")
    result_path = os.path.join(RESULTS_DIR, f"{job_id}.json")

    with open(job_path, "w", encoding="utf-8") as f:
        json.dump({"code": code, "spec": spec or {}}, f)

    waited = 0.0
    while waited < EXEC_TIMEOUT_SECONDS:
        if os.path.exists(result_path):
            with open(result_path, "r", encoding="utf-8") as f:
                result = json.load(f)
            os.remove(result_path)
            return result
        time.sleep(EXEC_POLL_INTERVAL_SECONDS)
        waited += EXEC_POLL_INTERVAL_SECONDS

    return {
        "execution": "FAIL",
        "error": f"nessuna risposta dall'executor entro {EXEC_TIMEOUT_SECONDS}s",
        "measurements": None,
        "dimensional_check": None,
        "generated_part_step_path": None,
    }


def run_gauge_check_job(
    part_step_path: str,
    gauge_step_path: str | None,
    mode: str,
    sweep: dict | None,
    min_distance: dict | None,
    part_source: str = "models",
):
    """Scrive un job "gauge_check" sul volume condiviso e attende il risultato.

    Percorso separato da run_execution_check(): niente 'code', l'input
    sono STEP noti/statici (vedi docs/logbook_fase1.md, criterio di
    accettazione M1 — l'AI non entra in questa milestone). Il watcher
    dell'executor instrada questo job a gauge_check.py, in un
    sottoprocesso indipendente da exec(code), con un timeout proprio
    (vedi executor/watcher.py, GAUGE_CHECK_TIMEOUT_SECONDS).

    mode/sweep/min_distance inoltrati cosi' come sono (vedi
    GaugeCheckRequest) — gauge_check.py e' l'unico posto che li valida
    davvero (VALID_MODES, campi richiesti per modalita'), coerente con
    "verifier non esegue/interpreta, solo instrada" (Rischio #9).
    **[M3] Prima di questa correzione l'endpoint accettava solo
    static_interference** (mode non esisteva nel job scritto qui) —
    sweep/min_distance di M2 erano raggiungibili solo dagli script di
    verifica manuali che parlano direttamente con gauge_check.py, mai
    dall'API HTTP che un orchestratore reale dovrebbe chiamare.
    """
    os.makedirs(JOBS_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)

    job_id = str(uuid.uuid4())
    job_path = os.path.join(JOBS_DIR, f"{job_id}.json")
    result_path = os.path.join(RESULTS_DIR, f"{job_id}.json")

    job = {"gauge_check": {"part_step_path": part_step_path, "mode": mode, "part_source": part_source}}
    if gauge_step_path is not None:
        job["gauge_check"]["gauge_step_path"] = gauge_step_path
    if sweep is not None:
        job["gauge_check"]["sweep"] = sweep
    if min_distance is not None:
        job["gauge_check"]["min_distance"] = min_distance

    with open(job_path, "w", encoding="utf-8") as f:
        json.dump(job, f)

    waited = 0.0
    while waited < GAUGE_CHECK_HTTP_TIMEOUT_SECONDS:
        if os.path.exists(result_path):
            with open(result_path, "r", encoding="utf-8") as f:
                result = json.load(f)
            os.remove(result_path)
            return result
        time.sleep(EXEC_POLL_INTERVAL_SECONDS)
        waited += EXEC_POLL_INTERVAL_SECONDS

    return {
        "execution": "FAIL",
        "error": f"nessuna risposta dall'executor entro {GAUGE_CHECK_HTTP_TIMEOUT_SECONDS}s",
        "measurements": None,
        "dimensional_check": None,
        "gauge_check": None,
    }


@app.get("/health")
def health():
    return {"status": "ok", "level": "L3-static-and-execution"}


@app.post("/verify")
def verify(req: VerifyRequest):
    syntax_check, tree = check_python_syntax(req.code)
    static_checks = [
        syntax_check,
        check_cadquery_import(tree),
        check_result_assigned(tree),
    ]
    static_pass = all(c["status"] == "PASS" for c in static_checks)

    if not static_pass:
        return {"status": "FAIL", "checks": static_checks, "execution": None}

    exec_result = run_execution_check(req.code, req.spec)
    exec_check = {
        "name": "execution_and_geometry",
        "status": exec_result["execution"],
        "detail": exec_result.get("error"),
    }
    checks = static_checks + [exec_check]

    overall = "PASS" if exec_result["execution"] == "PASS" else "FAIL"
    return {
        "status": overall,
        "checks": checks,
        "measurements": exec_result.get("measurements"),
        "dimensional_check": exec_result.get("dimensional_check"),
        # [M3] path (relativo a GENERATED_PARTS_ROOT in gauge_check.py,
        # part_source="generated") del pezzo appena esportato da
        # run_and_measure.py — None se il PASS non c'e' stato o
        # l'esportazione e' fallita, vedi la sua docstring.
        "generated_part_step_path": exec_result.get("generated_part_step_path"),
    }


@app.post("/gauge-check")
def gauge_check(req: GaugeCheckRequest):
    """Livello 3, fase 3 (M1 static_interference, M2 sweep/min_distance)
    — calibro Go/No-Go virtuale.

    Percorso indipendente da /verify: qui non c'e' codice da eseguire,
    solo STEP noti/statici da confrontare — vedi docs/logbook_fase1.md e
    docs/logbook_fase2.md per i criteri di accettazione di M1/M2.
    part_step_path e' relativo a /models (montato read-only in
    verifier-executor da ${DATA_DIR:-./data}/models), gauge_step_path
    (assente per mode="min_distance") e' relativo a /gauges
    (config/gauges/, versionato in git, MAI generato dall'IA — vedi
    config/gauges/README.md).
    """
    result = run_gauge_check_job(
        req.part_step_path, req.gauge_step_path, req.mode, req.sweep, req.min_distance, req.part_source
    )
    gc = result.get("gauge_check") or {}
    return {
        "status": gc.get("status", result["execution"]),
        "gauge_check": gc,
        "error": result.get("error"),
    }
