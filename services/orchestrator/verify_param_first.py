"""
CALIPER — verifica manuale della strategia "param_first" e dei fix di
tolleranza sketch-first (M5, C4/P3 — vedi docs/review_tecnica.md).

Tre controlli:

1. compile_thread_params_to_code() sui parametri M6 produce ESATTAMENTE
   lo stesso codice CadQuery del percorso sketch-first "a mano" gia'
   validato in verify_sketch_compiler_thread.py (stessa trigonometria,
   stessa spec canonica costruita internamente — non una seconda via
   geometrica, vedi sketch_compiler.py) — non solo "numeri simili", il
   codice generato e' identico carattere per carattere.
2. Una spec sketch-first con le coordinate del punto di radice
   arrotondate a 4 decimali (2.1340 invece di 2.1339745962155613) — PRIMA
   di M5 (NUMERIC_TOLERANCE_MM=1e-6) questo FALLIVA la validazione dello
   schema (l'LLM avrebbe dovuto emettere trigonometria a precisione
   piena); DOPO il fix (1e-3) valida.
3. Un punto di cresta a r_major - 5e-4 (entro CROSS_FIELD_TOLERANCE_MM,
   quindi una spec VALIDA per lo schema) riceve comunque l'overlap di
   stabilizzazione OCC nel codice compilato — PRIMA di M5 (soglia
   1e-6 in sketch_compiler.py) questo punto non veniva riconosciuto come
   cresta, aprendo la finestra di quasi-tangenza che CUT_OVERLAP_MM
   esiste per evitare.

Uso: python verify_param_first.py
Richiede cadquery per il controllo 1 (esecuzione/gauge-check reale);
2 e 3 sono puramente schema/compilatore (nessuna dipendenza pesante).
"""

import json
import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from sketch_compiler import compile_thread_params_to_code  # noqa: E402
from sketch_schema import validate_sketch_spec  # noqa: E402
from verify_sketch_compiler_thread import (  # noqa: E402
    ENGAGEMENT_LENGTH_MM,
    GAUGES_DIR,
    hand_written_thread_spec,
)
from sketch_compiler import compile_thread_sketch_to_code  # noqa: E402

RUN_AND_MEASURE = os.path.join(os.path.dirname(__file__), "..", "verifier", "executor", "run_and_measure.py")
MEASURE_VERDICT = os.path.join(os.path.dirname(__file__), "..", "verifier", "executor", "measure_verdict.py")
GAUGE_CHECK_PATH = os.path.join(os.path.dirname(__file__), "..", "verifier", "executor", "gauge_check.py")

PITCH_MM = 1.0
MAJOR_D_MM = 6.0


def run_two_stage(code, tmp, tag, parts_dir):
    job_path = os.path.join(tmp, f"job_{tag}.json")
    export_status_path = os.path.join(tmp, f"export_{tag}.json")
    result_path = os.path.join(tmp, f"result_{tag}.json")
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump({"code": code, "spec": {}}, f)
    env = dict(os.environ)
    env["GENERATED_PARTS_DIR"] = parts_dir
    subprocess.run([sys.executable, RUN_AND_MEASURE, job_path, export_status_path], env=env, check=True, capture_output=True)
    subprocess.run(
        [sys.executable, MEASURE_VERDICT, job_path, export_status_path, result_path],
        env=env, check=True, capture_output=True,
    )
    with open(result_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_gauge_check(part_step_path, gauge_step_path, generated_dir, work_dir, tag):
    job = {
        "gauge_check": {
            "part_step_path": part_step_path,
            "gauge_step_path": gauge_step_path,
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

    print("--- 1. param_first produce ESATTAMENTE lo stesso codice del percorso sketch-first a mano ---")
    params = {
        "major_diameter_mm": MAJOR_D_MM,
        "pitch_mm": PITCH_MM,
        "engagement_length_mm": ENGAGEMENT_LENGTH_MM,
        "host_xy_mm": 20.0,
    }
    code_param_first = compile_thread_params_to_code(params, profile_angle_deg=60.0)
    code_hand_written = compile_thread_sketch_to_code(hand_written_thread_spec())
    case1_code_ok = code_param_first == code_hand_written
    print("Atteso: codice CadQuery identico (stessa trigonometria, stessa spec canonica):", "OK" if case1_code_ok else "FALLITO")
    ok = ok and case1_code_ok

    with tempfile.TemporaryDirectory() as tmp:
        parts_dir = os.path.join(tmp, "parts")
        os.makedirs(parts_dir)
        r1 = run_two_stage(code_param_first, tmp, "paramfirst", parts_dir)
        print(json.dumps(r1, indent=2, ensure_ascii=False))
        case1_exec_ok = r1["execution"] == "PASS" and r1.get("generated_part_step_path")
        print("Atteso: PASS, STEP esportato:", "OK" if case1_exec_ok else "FALLITO")
        ok = ok and case1_exec_ok

        if case1_exec_ok:
            go = run_gauge_check(r1["generated_part_step_path"], "thread_M6_GO_ISO68-1.step", parts_dir, tmp, "go")
            go_ok = go["execution"] == "PASS"
            go_residual = go["gauge_check"]["interference_volume_mm3"]
            print(f"GO sweep: {'PASS' if go_ok else 'FAIL'}, residuo={go_residual}mm3 (atteso <= 0.5mm3): {'OK' if go_ok else 'FALLITO'}")
            ok = ok and go_ok

            nogo = run_gauge_check(r1["generated_part_step_path"], "thread_M6_NOGO_ISO68-1.step", parts_dir, tmp, "nogo")
            nogo_ok = nogo["execution"] == "FAIL" and nogo["gauge_check"]["interference_volume_mm3"] > 1.0
            print(
                f"NO-GO sweep: interferenza={nogo['gauge_check']['interference_volume_mm3']}mm3 "
                f"(atteso rilevata, > 1mm3, stesso ordine di grandezza del caso a mano): {'OK' if nogo_ok else 'FALLITO'}"
            )
            ok = ok and nogo_ok

    print("\n--- 2. sketch-first: coordinate arrotondate a 4 decimali (era FAIL di schema, ora valida) ---")
    r_major = MAJOR_D_MM / 2.0
    h = PITCH_MM / (2 * math.tan(math.radians(30.0)))
    r_minor_rounded = round(r_major - h, 4)  # 2.1340, non 2.1339745962155613
    rounded_spec = {
        "feature": "thread",
        "sketch": {
            "points": [
                {"id": "p_crest_a", "x": r_major, "y": -PITCH_MM / 2},
                {"id": "p_crest_b", "x": r_major, "y": PITCH_MM / 2},
                {"id": "p_root", "x": r_minor_rounded, "y": 0.0},
            ],
            "lines": [
                {"id": "l_flank_in", "start": "p_crest_a", "end": "p_root"},
                {"id": "l_flank_out", "start": "p_root", "end": "p_crest_b"},
                {"id": "l_close", "start": "p_crest_b", "end": "p_crest_a"},
            ],
            "arcs": [],
            "dimensions": [
                {"type": "distance", "refs": ["p_crest_a", "p_crest_b"], "value_mm": PITCH_MM, "label": "pitch"},
                {"type": "angle", "refs": ["l_flank_in", "l_flank_out"], "value_deg": 60.0, "label": "thread_profile_angle"},
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
    errors = validate_sketch_spec(rounded_spec)
    print(f"p_root.x = {r_minor_rounded} (arrotondato a 4 decimali, esatto sarebbe {r_major - h!r}); errori schema: {errors}")
    case2_ok = errors == []
    print("Atteso: nessun errore di schema (NUMERIC_TOLERANCE_MM allargata):", "OK" if case2_ok else "FALLITO")
    ok = ok and case2_ok

    print("\n--- 3. Punto di cresta a r_major - 5e-4 (valido per lo schema): overlap applicato nel codice ---")
    crest_reduced = r_major - 5e-4  # 2.9995, entro CROSS_FIELD_TOLERANCE_MM (1e-3)
    spec_crest_reduced = {
        "feature": "thread",
        "sketch": {
            "points": [
                {"id": "p_crest_a", "x": crest_reduced, "y": -PITCH_MM / 2},
                {"id": "p_crest_b", "x": crest_reduced, "y": PITCH_MM / 2},
                {"id": "p_root", "x": r_major - h, "y": 0.0},
            ],
            "lines": [
                {"id": "l_flank_in", "start": "p_crest_a", "end": "p_root"},
                {"id": "l_flank_out", "start": "p_root", "end": "p_crest_b"},
                {"id": "l_close", "start": "p_crest_b", "end": "p_crest_a"},
            ],
            "arcs": [],
            "dimensions": [
                {"type": "distance", "refs": ["p_crest_a", "p_crest_b"], "value_mm": PITCH_MM, "label": "pitch"},
                {"type": "angle", "refs": ["l_flank_in", "l_flank_out"], "value_deg": 60.0, "label": "thread_profile_angle"},
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
    errors3 = validate_sketch_spec(spec_crest_reduced)
    print(f"Errori schema per crest={crest_reduced}: {errors3} (atteso: nessuno, e' entro CROSS_FIELD_TOLERANCE_MM)")
    code3 = compile_thread_sketch_to_code(spec_crest_reduced)
    expected_x = round(crest_reduced + 0.05, 10)  # CUT_OVERLAP_MM = 0.05, vedi sketch_compiler.py
    overlap_applied = f"{expected_x!r}" in code3 or repr(round(crest_reduced + 0.05, 4)) in code3
    print(f"Atteso: il codice compilato contiene la coordinata CON overlap ({crest_reduced} + 0.05), non quella esatta")
    case3_ok = errors3 == [] and (repr(crest_reduced + 0.05) in code3) and (repr(crest_reduced) not in code3.replace(repr(crest_reduced + 0.05), ""))
    print("Atteso: spec valida per lo schema E overlap applicato (era invisibile prima di M5):", "OK" if case3_ok else "FALLITO")
    ok = ok and case3_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
