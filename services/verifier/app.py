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
# (GAUGE_CHECK_TIMEOUT_SECONDS in executor/watcher.py, oggi 45s) — stesso
# rapporto gia' in uso sopra (30s qui vs 15s di SUBPROCESS_TIMEOUT_SECONDS
# nel watcher per exec(code)).
GAUGE_CHECK_HTTP_TIMEOUT_SECONDS = 60


class VerifyRequest(BaseModel):
    code: str
    spec: dict | None = None


class GaugeCheckRequest(BaseModel):
    part_step_path: str
    gauge_step_path: str


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
    }


def run_gauge_check_job(part_step_path: str, gauge_step_path: str):
    """Scrive un job "gauge_check" sul volume condiviso e attende il risultato.

    Percorso separato da run_execution_check(): niente 'code', l'input
    sono due STEP noti/statici (vedi docs/logbook_fase1.md, criterio di
    accettazione M1 — l'AI non entra in questa milestone). Il watcher
    dell'executor instrada questo job a gauge_check.py, in un
    sottoprocesso indipendente da exec(code), con un timeout proprio
    (vedi executor/watcher.py, GAUGE_CHECK_TIMEOUT_SECONDS).
    """
    os.makedirs(JOBS_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)

    job_id = str(uuid.uuid4())
    job_path = os.path.join(JOBS_DIR, f"{job_id}.json")
    result_path = os.path.join(RESULTS_DIR, f"{job_id}.json")

    with open(job_path, "w", encoding="utf-8") as f:
        json.dump({"gauge_check": {"part_step_path": part_step_path, "gauge_step_path": gauge_step_path}}, f)

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
    }


@app.post("/gauge-check")
def gauge_check(req: GaugeCheckRequest):
    """Livello 3, fase 3 (M1) — calibro Go/No-Go virtuale.

    Percorso indipendente da /verify: qui non c'e' codice da eseguire,
    solo due STEP noti/statici (un pezzo, un calibro) da confrontare per
    interferenza statica esatta — vedi docs/logbook_fase1.md, criterio
    di accettazione M1. part_step_path e' relativo a /models (montato
    read-only in verifier-executor da ${DATA_DIR:-./data}/models),
    gauge_step_path e' relativo a /gauges (config/gauges/, versionato in
    git, MAI generato dall'IA — vedi config/gauges/README.md).
    """
    result = run_gauge_check_job(req.part_step_path, req.gauge_step_path)
    gc = result.get("gauge_check") or {}
    return {
        "status": gc.get("status", result["execution"]),
        "gauge_check": gc,
        "error": result.get("error"),
    }
