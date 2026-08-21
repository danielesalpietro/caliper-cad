"""
CALIPER — verifica manuale del contratto dimensionale per-feature (M5, C1).

Vedi docs/review_tecnica.md (C1) e docs/handoff_m5.md, Blocco A: il
confronto bbox-vs-nominale in run_and_measure.py (feature == "thread")
assume che il pezzo SIA il cilindro filettato (vero per il tampone [v14]),
ma da M3 il pezzo generato e' un FORO filettato in un blocco ospite PIU'
LARGO del nominale per costruzione (sketch_schema.py impone
host.size_mm[0:2] > major_diameter_mm) — nessun PASS dimensionale e'
possibile per costruzione col contratto vecchio.

Fix (P2a): il preset dichiara il contratto (`dimensional_check: "gauge"`
per "thread") — bbox-vs-nominale non si applica piu', sostituito da un
sanity check (bbox_z ~= engagement_length_mm, bbox_x/y >= diametro
maggiore). La misura dimensionale vera resta il collaudo Go/No-Go
(Blocco B). Il comportamento legacy (bbox-vs-nominale) resta per spec
SENZA il campo dimensional_check — nessuna regressione per gli script
M1/M2 esistenti (verify_run_and_measure_export.py).

Tre casi:
1. Foro M6 in blocco ospite 20x20x8 (stesso pezzo di
   verify_sketch_compiler_thread.py, compilato da sketch_compiler.py) con
   spec arricchita dal preset (dimensional_check="gauge",
   engagement_length_mm=8.0) attraverso un job run_and_measure.py REALE:
   PRIMA del fix, FAIL dimensionale (bbox 20mm vs nominale M6 6.0+-0.3) —
   dimostrato sotto come output rosso; DOPO il fix, PASS di /verify e,
   sul pezzo esportato, GO sweep reale PASS (residuo <= 0.5mm3, stesso
   calibro versionato di verify_sketch_compiler_thread.py).
2. Stessa spec ma SENZA dimensional_check (comportamento legacy): resta
   FAIL bbox-vs-nominale, invariato — nessuna regressione per gli script
   che non dichiarano il contratto.
3. bbox_z sbagliato (blocco 20x20x12 ma engagement_length_mm=8 dichiarato
   nella spec): FAIL di sanity, con l'errore che nomina la discrepanza.

Uso: python verify_dimensional_contract.py
Richiede cadquery installato (vedi verify_gauge_check.py).
"""

import json
import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "orchestrator"))

from sketch_compiler import compile_thread_sketch_to_code  # noqa: E402
from sketch_schema import assert_valid_sketch_spec  # noqa: E402

RUN_AND_MEASURE = os.path.join(os.path.dirname(__file__), "run_and_measure.py")
MEASURE_VERDICT = os.path.join(os.path.dirname(__file__), "measure_verdict.py")
GAUGE_CHECK_PATH = os.path.join(os.path.dirname(__file__), "gauge_check.py")
GAUGES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "gauges")

PITCH_MM = 1.0
ANGLE_DEG = 60.0
MAJOR_D_MM = 6.0
R_MAJOR = MAJOR_D_MM / 2.0
H = PITCH_MM / (2 * math.tan(math.radians(ANGLE_DEG / 2)))
R_MINOR = R_MAJOR - H
ENGAGEMENT_LENGTH_MM = 8.0


def thread_m6_sketch_spec():
    """Stessa spec (foro M6 in blocco ospite 20x20x8) di
    verify_sketch_compiler_thread.hand_written_thread_spec() — ripetuta
    qui esplicitamente per restare leggibile da sola, stessa disciplina
    gia' applicata li'."""
    return {
        "feature": "thread",
        "sketch": {
            "points": [
                {"id": "p_crest_a", "x": R_MAJOR, "y": -PITCH_MM / 2},
                {"id": "p_crest_b", "x": R_MAJOR, "y": PITCH_MM / 2},
                {"id": "p_root", "x": R_MINOR, "y": 0.0},
            ],
            "lines": [
                {"id": "l_flank_in", "start": "p_crest_a", "end": "p_root"},
                {"id": "l_flank_out", "start": "p_root", "end": "p_crest_b"},
                {"id": "l_close", "start": "p_crest_b", "end": "p_crest_a"},
            ],
            "arcs": [],
            "dimensions": [
                {"type": "distance", "refs": ["p_crest_a", "p_crest_b"], "value_mm": PITCH_MM, "label": "pitch"},
                {"type": "angle", "refs": ["l_flank_in", "l_flank_out"], "value_deg": ANGLE_DEG, "label": "thread_profile_angle"},
            ],
        },
        "operation": {
            "type": "helical_thread_cut",
            "host": {"type": "block", "size_mm": [20.0, 20.0, ENGAGEMENT_LENGTH_MM]},
            "major_diameter_mm": MAJOR_D_MM,
            "pitch_mm": PITCH_MM,
            "engagement_length_mm": ENGAGEMENT_LENGTH_MM,
            "right_handed": True,
        },
    }


def run_and_measure(code, spec, tmp, tag, parts_dir):
    # [M5, C7 — vedi docs/review_tecnica.md] Due stadi ora, non uno:
    # run_and_measure.py esporta solo lo STEP, measure_verdict.py
    # (fidato) scrive il verdetto — stessa sequenza di
    # watcher.py::process_code_job(), vedi verify_verdict_integrity.py.
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


def run_gauge_check_go(part_step_path, generated_dir, work_dir, tag):
    job = {
        "gauge_check": {
            "part_step_path": part_step_path,
            "gauge_step_path": "thread_M6_GO_ISO68-1.step",
            "part_source": "generated",
            "mode": "sweep",
            "sweep": {"steps": 21, "start_offset_mm": 0.0, "end_offset_mm": ENGAGEMENT_LENGTH_MM, "pitch_mm": PITCH_MM},
        }
    }
    job_path = os.path.join(work_dir, f"job_{tag}.json")
    result_path = os.path.join(work_dir, f"result_{tag}.json")
    checkpoint_path = os.path.join(work_dir, f"checkpoint_{tag}.json")
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump(job, f)
    env = dict(os.environ)
    env["GAUGE_CHECK_GENERATED_PARTS_ROOT"] = generated_dir
    env["GAUGE_CHECK_GAUGES_ROOT"] = os.path.abspath(GAUGES_DIR)
    subprocess.run(
        [sys.executable, GAUGE_CHECK_PATH, job_path, result_path, checkpoint_path],
        env=env, check=True, capture_output=True,
    )
    with open(result_path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    ok = True
    sketch_spec = thread_m6_sketch_spec()
    assert_valid_sketch_spec(sketch_spec)
    thread_code = compile_thread_sketch_to_code(sketch_spec)

    with tempfile.TemporaryDirectory() as tmp:
        parts_dir = os.path.join(tmp, "parts")

        print("--- 1. Foro M6 in blocco 20x20x8, spec con contratto 'gauge' (P2a) ---")
        spec_gauge = {
            "feature": "thread", "nominal": "M6", "tolerance": 0.3,
            "dimensional_check": "gauge", "engagement_length_mm": ENGAGEMENT_LENGTH_MM,
        }
        r1 = run_and_measure(thread_code, spec_gauge, tmp, "gauge", parts_dir)
        print(json.dumps(r1, indent=2, ensure_ascii=False))
        case1_ok = r1["execution"] == "PASS" and r1.get("generated_part_step_path")
        print("Atteso: PASS (nessun bbox-vs-nominale per contratto 'gauge'):", "OK" if case1_ok else "FALLITO")
        ok = ok and case1_ok

        if case1_ok:
            print("\n-> Collaudo Go/No-Go REALE sul pezzo esportato (calibro GO M6)...")
            r1_go = run_gauge_check_go(r1["generated_part_step_path"], parts_dir, tmp, "go")
            print(json.dumps(r1_go, indent=2, ensure_ascii=False))
            go_ok = r1_go["execution"] == "PASS" and r1_go["gauge_check"]["interference_volume_mm3"] <= 0.5
            print("Atteso: GO sweep PASS, residuo <= 0.5mm3:", "OK" if go_ok else "FALLITO")
            ok = ok and go_ok

        print("\n--- 2. Stesso pezzo, spec SENZA 'dimensional_check' (comportamento legacy) ---")
        spec_legacy = {"feature": "thread", "nominal": "M6", "tolerance": 0.3}
        r2 = run_and_measure(thread_code, spec_legacy, tmp, "legacy", parts_dir)
        print(json.dumps(r2, indent=2, ensure_ascii=False))
        case2_ok = r2["execution"] == "FAIL" and r2["dimensional_check"]["status"] == "FAIL"
        print(
            "Atteso: FAIL bbox-vs-nominale invariato (nessuna regressione per spec senza contratto):",
            "OK" if case2_ok else "FALLITO",
        )
        ok = ok and case2_ok

        print("\n--- 3. bbox_z sbagliato (blocco 20x20x12, engagement_length_mm=8 dichiarato) ---")
        code_wrong_z = "import cadquery as cq\nresult = cq.Workplane('XY').box(20, 20, 12)\n"
        spec_wrong_z = {
            "feature": "thread", "nominal": "M6", "tolerance": 0.3,
            "dimensional_check": "gauge", "engagement_length_mm": ENGAGEMENT_LENGTH_MM,
        }
        r3 = run_and_measure(code_wrong_z, spec_wrong_z, tmp, "wrongz", parts_dir)
        print(json.dumps(r3, indent=2, ensure_ascii=False))
        case3_ok = r3["execution"] == "FAIL" and r3["dimensional_check"]["status"] == "FAIL"
        print("Atteso: FAIL di sanity (bbox_z=12 != engagement_length_mm=8):", "OK" if case3_ok else "FALLITO")
        ok = ok and case3_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
