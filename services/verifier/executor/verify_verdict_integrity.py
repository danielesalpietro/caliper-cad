"""
CALIPER — verifica manuale dell'integrita' del verdetto (M5, C7, P5).

Vedi docs/review_tecnica.md (C7): fino a M4, run_and_measure.py eseguiva
exec(code) E scriveva il verdetto finale (result.json) nello STESSO
processo — il codice non fidato poteva quindi scrivere un result.json
CONTRAFFATTO ("execution": "PASS") e chiamare os._exit(0) per uscire
prima che qualunque controllo reale avvenisse. Fix (P5): run_and_measure.py
(non fidato) esegue exec(code) ed esporta SOLO lo STEP, non scrive mai il
verdetto; measure_verdict.py (fidato, mai exec di codice esterno) e' ora
l'UNICO scrittore del verdetto, chiamato SEMPRE (vedi watcher.py) e
scrive SEMPRE result.json incondizionatamente.

Due casi, esercitando ESATTAMENTE la sequenza di due sottoprocessi che
watcher.py::process_code_job() ora usa (stessi due script, stesso ordine,
stessa convenzione di path — non una reimplementazione parallela):

1. Fixture avversariale: il codice eseguito da run_and_measure.py
   CALCOLA da solo il path di result.json (stessa convenzione di
   RESULTS_DIR/<job_id>.json che il watcher userebbe — un attaccante
   che conosce il codice open-source del progetto puo' derivarla, non e'
   un'assunzione ottimistica), ci scrive un verdetto PASS contraffatto e
   chiama os._exit(0) per uscire immediatamente (nessun codice successivo
   di run_and_measure.py viene mai eseguito, export_status.json non
   viene mai scritto). Atteso: measure_verdict.py, chiamato comunque
   SUBITO DOPO (come fa watcher.py, incondizionatamente), sovrascrive il
   file con un verdetto FAIL reale — il PASS contraffatto non sopravvive.
2. Regressione: caso valido normale (stesso codice di
   verify_run_and_measure_export.py) attraverso la STESSA sequenza a due
   stadi -> PASS, con measurements/generated_part_step_path popolati
   esattamente come prima del fix (stesso schema pubblico di result.json,
   solo il processo che lo scrive e' cambiato).

Uso: python verify_verdict_integrity.py
Richiede cadquery installato (vedi verify_gauge_check.py).
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


def run_two_stage(code, spec, tmp, tag, parts_dir, results_dir):
    """Stessa sequenza di watcher.py::process_code_job() (M5): scrive il
    job, esegue run_and_measure.py (non fidato) su un export_status
    dedicato, poi SEMPRE measure_verdict.py (fidato) sul result_path
    finale — mai il contrario, mai condizionato su cosa il primo
    processo ha scritto altrove."""
    job_path = os.path.join(tmp, f"job_{tag}.json")
    export_status_path = os.path.join(results_dir, f"{tag}.export_status.json")
    result_path = os.path.join(results_dir, f"{tag}.json")
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump({"code": code, "spec": spec}, f)

    env = dict(os.environ)
    env["GENERATED_PARTS_DIR"] = parts_dir

    subprocess.run([sys.executable, RUN_AND_MEASURE, job_path, export_status_path], env=env, capture_output=True)
    # [M5, C7] SEMPRE, incondizionatamente — stessa disciplina di
    # watcher.py: non si controlla il return code di run_and_measure.py
    # ne' si salta questa chiamata se result_path esiste gia'.
    subprocess.run([sys.executable, MEASURE_VERDICT, job_path, export_status_path, result_path], env=env, capture_output=True)

    with open(result_path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        parts_dir = os.path.join(tmp, "parts")
        results_dir = os.path.join(tmp, "results")
        os.makedirs(results_dir)

        print("--- 1. Fixture avversariale: codice che contraffa result.json e chiama os._exit(0) ---")
        tag = "adversarial-job"
        forged_result_path = os.path.join(results_dir, f"{tag}.json")
        adversarial_code = f"""
import json, os, sys
forged_path = {forged_result_path!r}
with open(forged_path, "w", encoding="utf-8") as f:
    json.dump({{
        "execution": "PASS",
        "error": None,
        "measurements": {{"is_valid": True, "bbox_x_mm": 6.0, "bbox_y_mm": 6.0, "bbox_z_mm": 8.0}},
        "dimensional_check": None,
        "generated_part_step_path": "totally-fake-copied-from-models.step",
    }}, f)
os._exit(0)  # uscita immediata: nessun codice successivo di run_and_measure.py viene mai eseguito
"""
        r1 = run_two_stage(adversarial_code, {}, tmp, tag, parts_dir, results_dir)
        print(json.dumps(r1, indent=2, ensure_ascii=False))
        case1_ok = r1["execution"] == "FAIL" and r1.get("generated_part_step_path") != "totally-fake-copied-from-models.step"
        print(
            "Atteso: il PASS contraffatto NON sopravvive, verdetto finale FAIL "
            "(measure_verdict.py sovrascrive incondizionatamente):",
            "OK" if case1_ok else "FALLITO",
        )
        ok = ok and case1_ok

        print("\n--- 2. Regressione: caso valido attraverso la stessa sequenza a due stadi ---")
        tag2 = "valid-job"
        r2 = run_two_stage(CODE_VALID, {}, tmp, tag2, parts_dir, results_dir)
        print(json.dumps(r2, indent=2, ensure_ascii=False))
        # job_path e' "job_<tag>.json" (vedi run_two_stage) -> job_id =
        # "job_<tag>" (run_and_measure.py deriva lo step name dal
        # basename del job_path, invariato dal fix).
        expected_step_name = f"job_{tag2}.step"
        exported = os.path.join(parts_dir, expected_step_name)
        case2_ok = (
            r2["execution"] == "PASS"
            and r2["generated_part_step_path"] == expected_step_name
            and os.path.exists(exported)
            and r2["measurements"]["is_valid"] is True
        )
        print("Atteso: PASS reale, STEP esportato, measurements popolati (nessuna regressione):", "OK" if case2_ok else "FALLITO")
        ok = ok and case2_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
