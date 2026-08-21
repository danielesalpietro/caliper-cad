"""
CALIPER — verifica manuale di gauge_check.py, TC3 (M2).

Giunto a scatto (snap-fit) — distanza minima esatta tra due facce del
PEZZO STESSO (non un secondo calibro fisico, a differenza di TC1/TC2),
identificate come le facce piu' vicine a due "punti di misura"
dichiarati — vedi docs/logbook_fase2.md, TC3, e la modalita'
'min_distance' in gauge_check.py.

Pezzo di controllo: un unico solido monolitico con due feature di
riferimento a distanza nota — un dente di ritenzione (tooth) e uno
sbalzo di arresto (overhang) collegato alla base tramite un braccio
separato (non a contatto col dente), con un gap d'aria nominale
dichiarato tra le due facce affacciate. Non e' una simulazione della
deflessione elastica del dente durante l'innesto (quella resta
esplicitamente fuori scope, serve un solver FEA dedicato — vedi
docs/logbook_fase1.md, "Domanda aperta") — qui si misura solo la
distanza geometrica nominale tra due feature note, coerente con la
descrizione originale di TC3 ("calibro virtuale... confrontato con le
quote teoriche").

Due varianti:
- "nominal": gap 0.3mm — dentro tolleranza (nominale 0.3 ± 0.1) -> PASS
- "tight":   gap 0.1mm — fuori tolleranza -> FAIL (e verifica che lo
  snapping alla faccia piu' vicina segua la faccia REALE anche quando i
  punti di misura dichiarati non toccano piu' esattamente la geometria,
  vedi face_snap_distance_* nel risultato)

Uso: python verify_gauge_check_tc3.py
Richiede cadquery installato (pip install cadquery==2.8.0).
"""

import json
import os
import subprocess
import sys
import tempfile

GAUGE_CHECK_PATH = os.path.join(os.path.dirname(__file__), "gauge_check.py")
GAUGES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "gauges")

# Coordinate del "dente" (tooth, a x=-6) e dell'appoggio/sbalzo
# (support/overhang, a x=+6), collegati alla base da un ponte separato —
# vedi build_snap_fit_part() per la geometria completa. I punti di
# misura sono dichiarati come se provenissero dalla spec L2.5 (vedi
# services/orchestrator/presets.json, preset "snap_fit").
TOOTH_TOP_Z = 8.0  # base(3) + tooth height(5)
BRIDGE_TOP_Z = 11.0  # base(3) + support height(8)
NOMINAL_GAP_MM = 0.3
TOLERANCE_MM = 0.1

POINT_A_MM = [-6.0, 0.0, TOOTH_TOP_Z]
POINT_B_NOMINAL_MM = [-6.0, 0.0, TOOTH_TOP_Z + NOMINAL_GAP_MM]


def build_snap_fit_part(models_dir: str, gap_mm: float, tag: str) -> str:
    import cadquery as cq

    base = cq.Workplane("XY").box(20, 10, 3, centered=(True, True, False))
    tooth = cq.Workplane("XY").workplane(offset=3).center(-6, 0).box(4, 4, 5, centered=(True, True, False))
    support = cq.Workplane("XY").workplane(offset=3).center(6, 0).box(4, 4, 8, centered=(True, True, False))
    overhang_bottom_z = TOOTH_TOP_Z + gap_mm
    overhang_height = BRIDGE_TOP_Z - overhang_bottom_z
    overhang = (
        cq.Workplane("XY")
        .workplane(offset=overhang_bottom_z)
        .center(-6, 0)
        .box(4, 4, overhang_height, centered=(True, True, False))
    )
    bridge = (
        cq.Workplane("XY")
        .workplane(offset=BRIDGE_TOP_Z - 1)
        .center(0, 0)
        .box(16, 4, 1, centered=(True, True, False))
    )
    solid = base.union(tooth).union(support).union(bridge).union(overhang)

    obj = solid.val()
    if not obj.isValid():
        raise RuntimeError(f"solido di controllo non valido (tag={tag})")

    path = os.path.join(models_dir, f"snap_fit_{tag}.step")
    cq.exporters.export(solid, path)
    return path


def run_gauge_check(job: dict, models_dir: str, work_dir: str, tag: str):
    job_path = os.path.join(work_dir, f"job_{tag}.json")
    result_path = os.path.join(work_dir, f"result_{tag}.json")
    checkpoint_path = os.path.join(work_dir, f"checkpoint_{tag}.json")
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump(job, f)

    env = dict(os.environ)
    env["GAUGE_CHECK_MODELS_ROOT"] = models_dir
    env["GAUGE_CHECK_GAUGES_ROOT"] = os.path.abspath(GAUGES_DIR)

    subprocess.run(
        [sys.executable, GAUGE_CHECK_PATH, job_path, result_path, checkpoint_path],
        env=env,
        check=True,
        capture_output=True,
    )
    with open(result_path, "r", encoding="utf-8") as f:
        return json.load(f), result_path


def min_distance_job(part_rel: str, point_b_mm):
    return {
        "gauge_check": {
            "part_step_path": part_rel,
            "mode": "min_distance",
            "min_distance": {
                "point_a_mm": POINT_A_MM,
                "point_b_mm": point_b_mm,
                "nominal_mm": NOMINAL_GAP_MM,
                "tolerance_mm": TOLERANCE_MM,
            },
        }
    }


def main():
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        models_dir = os.path.join(tmp, "models")
        work_dir = os.path.join(tmp, "work")
        os.makedirs(models_dir)
        os.makedirs(work_dir)

        print("=== TC3 — distanza minima (dente di ritenzione / sbalzo di arresto) ===")

        build_snap_fit_part(models_dir, NOMINAL_GAP_MM, "nominal")
        result_nom, result_nom_path = run_gauge_check(
            min_distance_job("snap_fit_nominal.step", POINT_B_NOMINAL_MM), models_dir, work_dir, "nominal"
        )
        print("\n--- Gap nominale (0.3mm, dentro tolleranza 0.3±0.1) ---")
        print(json.dumps(result_nom, indent=2, ensure_ascii=False))
        md_nom = result_nom["gauge_check"]["min_distance"]
        nom_ok = result_nom["execution"] == "PASS" and abs(md_nom["measured_mm"] - NOMINAL_GAP_MM) < 1e-6
        print(f"Atteso PASS, distanza misurata ~{NOMINAL_GAP_MM}mm (misurata {md_nom['measured_mm']}):", "OK" if nom_ok else "FALLITO")
        ok = ok and nom_ok

        # Determinismo (stesso criterio gia' applicato in M1/TC1/TC2)
        _, result_nom_path_2 = run_gauge_check(
            min_distance_job("snap_fit_nominal.step", POINT_B_NOMINAL_MM), models_dir, work_dir, "nominal_repeat"
        )
        with open(result_nom_path, "rb") as f1, open(result_nom_path_2, "rb") as f2:
            identical = f1.read() == f2.read()
        print("\n--- Determinismo (stesso job, due esecuzioni) ---")
        print("Output byte per byte identico:", "OK" if identical else "FALLITO")
        ok = ok and identical

        # Variante fuori tolleranza: gap 0.1mm invece di 0.3mm, ma i punti
        # di misura dichiarati restano quelli "nominali" (z=8.3) — la
        # faccia reale ora e' a z=8.1: verifica che lo snapping segua la
        # faccia REALE (face_snap_distance_b > 0), non il punto dichiarato.
        build_snap_fit_part(models_dir, 0.1, "tight")
        result_tight, _ = run_gauge_check(
            min_distance_job("snap_fit_tight.step", POINT_B_NOMINAL_MM), models_dir, work_dir, "tight"
        )
        print("\n--- Gap fuori tolleranza (0.1mm reale, punto di misura dichiarato invariato) ---")
        print(json.dumps(result_tight, indent=2, ensure_ascii=False))
        md_tight = result_tight["gauge_check"]["min_distance"]
        tight_ok = (
            result_tight["execution"] == "FAIL"
            and abs(md_tight["measured_mm"] - 0.1) < 1e-6
            and md_tight["face_snap_distance_b_mm"] > 0
        )
        print(
            f"Atteso FAIL, distanza reale misurata ~0.1mm (misurata {md_tight['measured_mm']}), "
            f"snap alla faccia reale non al punto dichiarato (face_snap_distance_b="
            f"{md_tight['face_snap_distance_b_mm']}mm > 0):",
            "OK" if tight_ok else "FALLITO",
        )
        ok = ok and tight_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
