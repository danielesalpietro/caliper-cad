"""
CALIPER — verifica manuale dell'esportazione STEP in run_and_measure.py (M3).

Vedi docs/logbook_fase3.md e la docstring di run_and_measure.py: /models
è montato READ-ONLY in verifier-executor (docker-compose.yml, riservato
ai pezzi di riferimento statici di M1/M2) — un pezzo appena generato non
può finirci. Questo script verifica il fix: il pezzo validato viene
esportato sotto una radice separata e SCRIVIBILE (GENERATED_PARTS_DIR,
in produzione /exec/parts, qui sovrascritta via env per il test), con
generated_part_step_path nel result JSON.

Due casi, con codice CadQuery scritto a mano (non generato da un LLM,
stesso stile degli altri verify_*.py in questa directory):
1. Geometria valida, nessun preset: PASS, STEP esportato, path presente
   nel result.
2. Geometria valida ma fuori tolleranza dimensionale (preset "thread"):
   l'esportazione avviene comunque (avviene PRIMA del controllo
   dimensionale, che può ancora ribaltare 'execution' a FAIL) —
   generated_part_step_path resta presente: il pezzo esiste davvero sul
   disco anche se il caso non passa il confronto con la specifica.

[M5, C7] Da questa milestone run_and_measure.py non scrive piu' il
verdetto da solo (split esecuzione/verdetto, vedi la sua docstring e
verify_verdict_integrity.py) — questo script chiama ora anche
measure_verdict.py come secondo stadio, stessa sequenza del watcher.

Uso: python verify_run_and_measure_export.py
Richiede cadquery installato (vedi verify_gauge_check.py per il dettaglio).
"""

import json
import os
import subprocess
import sys
import tempfile

RUN_AND_MEASURE = os.path.join(os.path.dirname(__file__), "run_and_measure.py")
MEASURE_VERDICT = os.path.join(os.path.dirname(__file__), "measure_verdict.py")

CODE_VALID = """
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 5)
"""


def run(code, spec, tmp, tag, parts_dir):
    # [M5, C7 — vedi docs/review_tecnica.md] run_and_measure.py non
    # scrive piu' il verdetto finale da solo (esporta solo lo STEP, vedi
    # la sua docstring) — measure_verdict.py (processo fidato) e' ora
    # sempre il secondo passo, stessa sequenza usata da
    # watcher.py::process_code_job(). Vedi anche verify_verdict_integrity.py.
    job_path = os.path.join(tmp, f"job_{tag}.json")
    export_status_path = os.path.join(tmp, f"export_status_{tag}.json")
    result_path = os.path.join(tmp, f"result_{tag}.json")
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump({"code": code, "spec": spec}, f)
    env = dict(os.environ)
    env["GENERATED_PARTS_DIR"] = parts_dir
    subprocess.run([sys.executable, RUN_AND_MEASURE, job_path, export_status_path], env=env, check=True, capture_output=True)
    subprocess.run(
        [sys.executable, MEASURE_VERDICT, job_path, export_status_path, result_path],
        env=env, check=True, capture_output=True,
    )
    with open(result_path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        parts_dir = os.path.join(tmp, "parts")

        print("--- 1. Geometria valida, nessun preset ---")
        r1 = run(CODE_VALID, {}, tmp, "ok", parts_dir)
        print(json.dumps(r1, indent=2, ensure_ascii=False))
        exported = os.path.join(parts_dir, "job_ok.step")
        case1_ok = r1["execution"] == "PASS" and r1["generated_part_step_path"] == "job_ok.step" and os.path.exists(exported)
        print("Atteso: PASS, generated_part_step_path='job_ok.step', file presente su disco:", "OK" if case1_ok else "FALLITO")
        ok = ok and case1_ok

        print("\n--- 2. Geometria valida ma fuori tolleranza dimensionale (bbox 10mm vs nominale M6) ---")
        r2 = run(CODE_VALID, {"feature": "thread", "nominal": "M6", "tolerance": 0.3}, tmp, "dimfail", parts_dir)
        print(json.dumps(r2, indent=2, ensure_ascii=False))
        case2_ok = (
            r2["execution"] == "FAIL"
            and r2["dimensional_check"]["status"] == "FAIL"
            and r2["generated_part_step_path"] == "job_dimfail.step"
        )
        print(
            "Atteso: FAIL dimensionale ma generated_part_step_path presente "
            "(esportazione avvenuta prima del controllo dimensionale):",
            "OK" if case2_ok else "FALLITO",
        )
        ok = ok and case2_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
