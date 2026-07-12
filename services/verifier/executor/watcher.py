"""
CALIPER — Livello 3, watcher dell'executor isolato.

Processo a lunga vita nel container `verifier-executor`
(network_mode: none — nessuna rete, ne' in ne' out, vedi
docker-compose.yml). Comunica col servizio `verifier` SOLO tramite un
volume condiviso: legge i job da /exec/jobs/, scrive i risultati in
/exec/results/. Ogni job viene eseguito in un sottoprocesso separato
(run_and_measure.py) con limiti di CPU/memoria propri e un timeout,
cosi' un job che va in loop infinito o alloca troppa memoria non
degrada i job successivi.
"""

import glob
import json
import os
import subprocess
import time

JOBS_DIR = "/exec/jobs"
RESULTS_DIR = "/exec/results"
POLL_INTERVAL_SECONDS = 0.5
SUBPROCESS_TIMEOUT_SECONDS = 15


def process_job(job_path: str):
    job_id = os.path.splitext(os.path.basename(job_path))[0]
    result_path = os.path.join(RESULTS_DIR, f"{job_id}.json")

    try:
        subprocess.run(
            ["python", "/app/run_and_measure.py", job_path, result_path],
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            capture_output=True,
        )
    except subprocess.TimeoutExpired:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(
                {"execution": "FAIL", "error": f"timeout dopo {SUBPROCESS_TIMEOUT_SECONDS}s", "measurements": None, "dimensional_check": None},
                f,
            )

    if not os.path.exists(result_path):
        # run_and_measure.py e' morto senza scrivere nulla (es. crash interprete)
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(
                {"execution": "FAIL", "error": "il sottoprocesso non ha prodotto un risultato", "measurements": None, "dimensional_check": None},
                f,
            )

    os.remove(job_path)


def main():
    os.makedirs(JOBS_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    print("verifier-executor: in ascolto su", JOBS_DIR, flush=True)
    while True:
        for job_path in glob.glob(os.path.join(JOBS_DIR, "*.json")):
            process_job(job_path)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
