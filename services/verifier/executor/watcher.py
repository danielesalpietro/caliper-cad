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

- "code"         -> run_and_measure.py (invariato)
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

# Timeout ESTERNO del gauge-check, indipendente da SUBPROCESS_TIMEOUT_SECONDS
# (usato solo per exec(code)). Placeholder non ancora tarato empiricamente
# sul worst-case osservato — la calibrazione e' scope di M2 (vedi
# docs/logbook_fase2.md, "Timeout e isolamento computazionale"). Margine
# esterno maggiore del limite CPU interno di gauge_check.py (30s), stesso
# rapporto gia' in uso per run_and_measure.py (10s interno / 15s esterno).
GAUGE_CHECK_TIMEOUT_SECONDS = 45


def _write_result(result_path, data):
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _empty_result(error):
    return {"execution": "FAIL", "error": error, "measurements": None, "dimensional_check": None}


def process_code_job(job_path: str, result_path: str):
    try:
        subprocess.run(
            ["python", "/app/run_and_measure.py", job_path, result_path],
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        _write_result(result_path, _empty_result(f"timeout dopo {SUBPROCESS_TIMEOUT_SECONDS}s"))

    if not os.path.exists(result_path):
        # run_and_measure.py e' morto senza scrivere nulla (es. crash interprete)
        _write_result(result_path, _empty_result("il sottoprocesso non ha prodotto un risultato"))


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
        # e' il checkpoint pre-flight scritto da gauge_check.py PRIMA del
        # boolean pesante (vedi docs/logbook_fase2.md). Puo' non esistere se
        # il timeout e' scattato ancora prima (es. import STEP molto lento).
        preflight = None
        if os.path.exists(checkpoint_path):
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                preflight = json.load(f).get("preflight_diagnostics")

        result = _empty_result("gauge_check_timeout")
        result["gauge_check"] = {
            "status": "TIMEOUT",
            "mode": "static_interference",
            "part_step_path": gauge_check_spec.get("part_step_path"),
            "gauge_step_path": gauge_check_spec.get("gauge_step_path"),
            "interference_volume_mm3": None,
            "timeout_seconds": GAUGE_CHECK_TIMEOUT_SECONDS,
            "preflight_diagnostics": preflight,
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
        process_code_job(job_path, result_path)
    else:
        _write_result(result_path, _empty_result("job non valido: attesa la chiave 'code' o 'gauge_check'"))

    os.remove(job_path)


def main():
    os.makedirs(JOBS_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(CHECKPOINTS_DIR, exist_ok=True)
    print("verifier-executor: in ascolto su", JOBS_DIR, flush=True)
    while True:
        for job_path in glob.glob(os.path.join(JOBS_DIR, "*.json")):
            process_job(job_path)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
