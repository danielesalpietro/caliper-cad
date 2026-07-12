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


class VerifyRequest(BaseModel):
    code: str
    spec: dict | None = None


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
