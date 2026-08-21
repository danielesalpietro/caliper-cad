"""
CALIPER — verifica manuale end-to-end di sketch_compiler.py (M3).

Caso di prova SCRITTO A MANO (non generato da un LLM — coerente con
l'handoff di questa milestone: senza un'istanza Flowise viva in questo
sandbox, la parte verificabile e' "compilazione vincoli-2D -> CadQuery ->
STEP", non l'esecuzione end-to-end reale con L2, vedi
docs/logbook_fase3.md).

Copre l'intera catena nuova di M3 con un solo caso, sugli stessi calibri
REALI gia' versionati (non sintetici):

  spec sketch-first (vincoli, scritti a mano) --sketch_schema.py-->
  validata --sketch_compiler.py--> codice CadQuery (testo) --exec()-->
  solido reale --gauge_check.py (stesso subprocess usato in produzione,
  part_source="generated")--> PASS/FAIL contro i calibri M6 GO/NO-GO

Atteso, per confronto diretto con TC2 (docs/logbook_fase2.md, gia'
verificato con una cavita' filettata costruita a mano con CadQuery
libero): stessa semantica GO/NO-GO, sullo STESSO foro filettato M6
nominale, ora pero' costruito passando dallo sketch-first e dal
compilatore invece che da CadQuery libero — se i numeri corrispondono,
il compilatore produce la STESSA geometria del percorso gia' validato,
non una diversa per caso.

Uso: python verify_sketch_compiler_thread.py
Richiede cadquery installato (vedi services/verifier/executor/verify_gauge_check.py).
"""

import json
import math
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from sketch_compiler import compile_thread_sketch_to_code  # noqa: E402
from sketch_schema import assert_valid_sketch_spec  # noqa: E402

GAUGE_CHECK_PATH = os.path.join(os.path.dirname(__file__), "..", "verifier", "executor", "gauge_check.py")
GAUGES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "config", "gauges")

PITCH_MM = 1.0
ANGLE_DEG = 60.0
MAJOR_D_MM = 6.0
R_MAJOR = MAJOR_D_MM / 2.0
H = PITCH_MM / (2 * math.tan(math.radians(ANGLE_DEG / 2)))
R_MINOR = R_MAJOR - H
ENGAGEMENT_LENGTH_MM = 8.0  # deve combaciare col preset "thread" in presets.json


def hand_written_thread_spec():
    """Stessa spec di verify_sketch_schema.valid_thread_spec() — ripetuta
    qui esplicitamente (non importata) perche' e' IL caso di prova scritto
    a mano richiesto dall'handoff, non un dettaglio di validazione dello
    schema: deve restare leggibile da sola."""
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
        env=env,
        check=True,
        capture_output=True,
    )
    with open(result_path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    ok = True
    spec = hand_written_thread_spec()

    print("--- 1. Validazione schema ---")
    assert_valid_sketch_spec(spec)  # solleva se invalida — nessun try/except, deve passare
    print("Spec valida: OK")

    print("\n--- 2. Compilazione vincoli-2D -> codice CadQuery ---")
    code = compile_thread_sketch_to_code(spec)
    print(code)

    print("--- 3. Esecuzione del codice compilato (exec, stesso contratto di run_and_measure.py) ---")
    import cadquery as cq  # noqa: F401  (serve nel namespace di exec())

    namespace = {}
    exec(code, namespace)  # nosec - stesso confine di fiducia di run_and_measure.py, qui solo per verifica manuale
    result_obj = namespace["result"]
    solid = result_obj.val()
    is_valid = solid.isValid()
    bb = solid.BoundingBox()
    print(f"is_valid={is_valid}  bbox=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})  volume={solid.Volume():.3f}mm3")
    exec_ok = is_valid and abs(bb.xlen - 20.0) < 1e-3 and abs(bb.ylen - 20.0) < 1e-3 and abs(bb.zlen - ENGAGEMENT_LENGTH_MM) < 1e-3
    print(f"Atteso: solido valido, bbox del blocco ospite (20,20,{ENGAGEMENT_LENGTH_MM}):", "OK" if exec_ok else "FALLITO")
    ok = ok and exec_ok

    with tempfile.TemporaryDirectory() as tmp:
        generated_dir = os.path.join(tmp, "generated_parts")
        work_dir = os.path.join(tmp, "work")
        os.makedirs(generated_dir)
        os.makedirs(work_dir)

        part_rel = "sketch_first_thread_M6.step"
        cq.exporters.export(result_obj, os.path.join(generated_dir, part_rel))
        print(f"\n--- 4. STEP esportato: {part_rel} ---")

        print("\n--- 5. Collaudo Go/No-Go REALE (gauge_check.py, calibri M6 versionati) ---")
        result_go = run_gauge_check(part_rel, "thread_M6_GO_ISO68-1.step", generated_dir, work_dir, "go")
        print("\n--- Calibro GO (sweep elicoidale, 21 step) ---")
        print(json.dumps(result_go, indent=2, ensure_ascii=False))
        go_sweep = result_go["gauge_check"]["sweep"]
        go_ok = (
            result_go["execution"] == "PASS"
            and go_sweep["steps_completed"] == 21
            and go_sweep["first_interference_step"] is None
        )
        print("Atteso: PASS su tutti i 21 step (stesso esito di TC2):", "OK" if go_ok else "FALLITO")
        ok = ok and go_ok

        result_nogo = run_gauge_check(part_rel, "thread_M6_NOGO_ISO68-1.step", generated_dir, work_dir, "nogo")
        print("\n--- Calibro NO-GO (sweep elicoidale, 21 step) ---")
        print(json.dumps(result_nogo, indent=2, ensure_ascii=False))
        nogo_sweep = result_nogo["gauge_check"]["sweep"]
        nogo_ok = result_nogo["execution"] == "FAIL" and nogo_sweep["first_interference_step"] is not None
        print("Atteso: FAIL con interferenza rilevata (stesso esito di TC2):", "OK" if nogo_ok else "FALLITO")
        ok = ok and nogo_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
