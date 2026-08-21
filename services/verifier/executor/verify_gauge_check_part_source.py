"""
CALIPER — verifica manuale di gauge_check.py: part_source "generated" (M3).

Verifica il fix descritto in docs/logbook_fase3.md: /models e' montato
READ-ONLY in verifier-executor (docker-compose.yml), quindi un pezzo
appena esportato da run_and_measure.py non puo' finirci — deve invece
essere risolto sotto una radice SEPARATA (GENERATED_PARTS_ROOT, qui
simulata via GAUGE_CHECK_GENERATED_PARTS_ROOT, in produzione
/exec/parts). Tre controlli:

1. part_source="generated" (default nel job scritto da
   run_and_measure.py, vedi la sua docstring) risolve correttamente
   sotto GENERATED_PARTS_ROOT, anche se lo stesso file NON esiste sotto
   MODELS_ROOT — prova diretta che le due radici sono davvero separate,
   non solo nominalmente.
2. part_source assente (default "models") continua a risolvere sotto
   MODELS_ROOT — nessuna regressione per gli script M1/M2 esistenti
   (verify_gauge_check_real_gauges.py, verify_gauge_check_tc1/2/3.py),
   che non passano mai questo campo.
3. part_source non valido viene rifiutato esplicitamente con un errore
   (non un fallback silenzioso su una radice a caso).

Uso: python verify_gauge_check_part_source.py
Richiede cadquery installato (vedi verify_gauge_check.py per il dettaglio).
"""

import json
import os
import subprocess
import sys
import tempfile

GAUGE_CHECK_PATH = os.path.join(os.path.dirname(__file__), "gauge_check.py")
GAUGES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "gauges")

BORE_DIAMETER_MM = 6.0
BLOCK_SIZE_MM = 20.0
BLOCK_THICKNESS_MM = 15.0


def build_test_part(out_dir: str, filename: str):
    import cadquery as cq

    block = (
        cq.Workplane("XY")
        .box(BLOCK_SIZE_MM, BLOCK_SIZE_MM, BLOCK_THICKNESS_MM)
        .translate((0, 0, BLOCK_THICKNESS_MM / 2))
        .faces(">Z")
        .workplane()
        .hole(BORE_DIAMETER_MM, BLOCK_THICKNESS_MM)
    )
    path = os.path.join(out_dir, filename)
    cq.exporters.export(block, path)
    return path


def run_gauge_check(job_extra: dict, models_dir: str, generated_dir: str, gauges_dir: str, work_dir: str, tag: str):
    job = {
        "gauge_check": {
            "part_step_path": "block_bore_6mm.step",
            "gauge_step_path": "thread_M6_GO_ISO68-1.step",
            **job_extra,
        }
    }
    job_path = os.path.join(work_dir, f"job_{tag}.json")
    result_path = os.path.join(work_dir, f"result_{tag}.json")
    checkpoint_path = os.path.join(work_dir, f"checkpoint_{tag}.json")
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump(job, f)

    env = dict(os.environ)
    env["GAUGE_CHECK_MODELS_ROOT"] = models_dir
    env["GAUGE_CHECK_GENERATED_PARTS_ROOT"] = generated_dir
    env["GAUGE_CHECK_GAUGES_ROOT"] = os.path.abspath(gauges_dir)

    subprocess.run(
        [sys.executable, GAUGE_CHECK_PATH, job_path, result_path, checkpoint_path],
        env=env,
        check=True,
        capture_output=True,
    )
    with open(result_path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        models_dir = os.path.join(tmp, "models")
        generated_dir = os.path.join(tmp, "generated_parts")  # simula /exec/parts
        work_dir = os.path.join(tmp, "work")
        os.makedirs(models_dir)
        os.makedirs(generated_dir)
        os.makedirs(work_dir)

        # Il pezzo esiste SOLO sotto generated_dir, mai sotto models_dir —
        # cosi' un eventuale bug che risolvesse comunque sotto MODELS_ROOT
        # fallirebbe con "import STEP fallito" invece di dare un falso OK.
        build_test_part(generated_dir, "block_bore_6mm.step")

        print("--- 1. part_source='generated', pezzo SOLO in GENERATED_PARTS_ROOT ---")
        result = run_gauge_check({"part_source": "generated"}, models_dir, generated_dir, GAUGES_DIR, work_dir, "gen")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        gen_ok = (
            result["execution"] == "PASS"
            and result["gauge_check"]["interference_volume_mm3"] == 0.0
            and result["gauge_check"]["part_source"] == "generated"
        )
        print("Atteso PASS, risolto sotto GENERATED_PARTS_ROOT:", "OK" if gen_ok else "FALLITO")
        ok = ok and gen_ok

        print("\n--- 2. part_source assente (default 'models'), pezzo SOLO in MODELS_ROOT ---")
        build_test_part(models_dir, "block_bore_6mm.step")
        os.remove(os.path.join(generated_dir, "block_bore_6mm.step"))
        result = run_gauge_check({}, models_dir, generated_dir, GAUGES_DIR, work_dir, "default")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        default_ok = (
            result["execution"] == "PASS"
            and result["gauge_check"]["interference_volume_mm3"] == 0.0
            and result["gauge_check"]["part_source"] == "models"
        )
        print("Atteso PASS (nessuna regressione M1/M2), risolto sotto MODELS_ROOT:", "OK" if default_ok else "FALLITO")
        ok = ok and default_ok

        print("\n--- 3. part_source non valido, rifiutato esplicitamente ---")
        result = run_gauge_check({"part_source": "nonsense"}, models_dir, generated_dir, GAUGES_DIR, work_dir, "bad")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        rejected_ok = result["execution"] == "FAIL" and "part_source non valido" in (result.get("error") or "")
        print("Atteso FAIL con errore esplicito (non un fallback silenzioso):", "OK" if rejected_ok else "FALLITO")
        ok = ok and rejected_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
