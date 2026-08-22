"""
CALIPER — Livello 3, processo FIDATO di misura e verdetto (M5, C7).

Vedi docs/review_tecnica.md (C7) e run_and_measure.py per il perche' del
processo NON fidato. Questo script NON esegue mai codice non fidato
(nessun exec()) — legge SOLO lo stato di esportazione scritto da
run_and_measure.py (export_status.json, trattato come mera diagnostica,
mai come verdetto: vedi sotto) e lo STEP che quel processo ha eventualmente
esportato, lo REIMPORTA e ri-misura da zero (validita', bounding box,
confronto dimensionale) — non riusa mai le misure che un processo non
fidato potrebbe aver riportato.

E' l'UNICO scrittore del verdetto pubblico (result.json, stesso schema
di prima del fix: execution/error/measurements/dimensional_check/
generated_part_step_path) — chiamato SEMPRE da watcher.py dopo
run_and_measure.py (salvo timeout di quest'ultimo, vedi watcher.py) e
scrive SEMPRE result.json incondizionatamente, sovrascrivendo qualunque
file gia' presente in quel path: e' questo — non la segretezza del path
— cio' che rende un verdetto contraffatto dal codice non fidato
innocuo, vedi verify_verdict_integrity.py.

Uso: python measure_verdict.py <job.json> <export_status.json> <result.json>
"""

import json
import os
import resource
import sys

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

GENERATED_PARTS_DIR = os.environ.get("GENERATED_PARTS_DIR", "/exec/parts")


def set_limits():
    # Import STEP + bbox/misura dimensionale e' economico rispetto a
    # exec(code) di codice libero, ma questo processo elabora comunque
    # un file (lo STEP) la cui forma e' influenzata dal codice non
    # fidato del processo precedente — stessi limiti difensivi per
    # coerenza, non perche' misurati su un worst-case proprio (nessun
    # caso reale ha ancora richiesto piu' di questo, vedi
    # docs/logbook_fase5.md).
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))


def parse_nominal_mm(nominal):
    if isinstance(nominal, str) and nominal.upper().startswith("M"):
        try:
            return float(nominal[1:].split("x")[0])
        except ValueError:
            return None
    return None


def write_result(path, result):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f)


def compute_dimensional_check(feature, spec, measurements):
    """Stessa logica di prima di M5 (vedi docs/review_tecnica.md, C1),
    ora eseguita qui invece che nel processo non fidato — invariata
    altrimenti: dimensional_check assente/altro valore = comportamento
    legacy bbox-vs-nominale; 'gauge' = sanity check (bbox_z ~=
    engagement_length_mm, bbox_x/y >= diametro maggiore), la misura
    dimensionale vera resta il collaudo Go/No-Go."""
    if feature != "thread" or not measurements:
        return None, None  # (dimensional_check, execution_override)

    nominal_mm = parse_nominal_mm(spec.get("nominal", ""))

    if spec.get("dimensional_check") == "gauge":
        engagement_length_mm = spec.get("engagement_length_mm")
        bbox_x, bbox_y, bbox_z = measurements["bbox_x_mm"], measurements["bbox_y_mm"], measurements["bbox_z_mm"]
        errors = []
        if engagement_length_mm is not None and abs(bbox_z - engagement_length_mm) > 1e-3:
            errors.append(f"bbox_z={bbox_z}mm non corrisponde a engagement_length_mm={engagement_length_mm}mm")
        if nominal_mm is not None and (bbox_x < nominal_mm or bbox_y < nominal_mm):
            errors.append(f"bbox_x/y=({bbox_x},{bbox_y})mm piu' stretto del diametro maggiore nominale {nominal_mm}mm")
        check = {
            "mode": "gauge_sanity",
            "nominal_mm": nominal_mm,
            "engagement_length_mm": engagement_length_mm,
            "bbox_x_mm": bbox_x,
            "bbox_y_mm": bbox_y,
            "bbox_z_mm": bbox_z,
            "status": "FAIL" if errors else "PASS",
            "errors": errors,
        }
        return check, ("FAIL" if errors else None)

    tolerance = spec.get("tolerance")
    measured_diameter = max(measurements["bbox_x_mm"], measurements["bbox_y_mm"])
    if nominal_mm is None or tolerance is None:
        return None, None
    delta = abs(measured_diameter - nominal_mm)
    within = delta <= tolerance
    check = {
        "mode": "bbox_vs_nominal",
        "nominal_mm": nominal_mm,
        "measured_diameter_mm": measured_diameter,
        "tolerance_mm": tolerance,
        "delta_mm": round(delta, 4),
        "status": "PASS" if within else "FAIL",
    }
    return check, (None if within else "FAIL")


def main():
    job_path, export_status_path, result_path = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)
    spec = job.get("spec") or {}

    result = {
        "execution": "FAIL",
        "error": None,
        "measurements": None,
        "dimensional_check": None,
        "generated_part_step_path": None,
    }

    # [M5, C7] Lo stato di esportazione e' diagnostica, non verdetto: se
    # e' assente o illeggibile (il processo non fidato e' morto senza
    # scriverlo, o ha scritto altro), il FAIL e' comunque quello reale
    # (nessuno STEP verificabile), non un'eccezione che risale.
    try:
        with open(export_status_path, "r", encoding="utf-8") as f:
            export_status = json.load(f)
    except (OSError, json.JSONDecodeError):
        result["error"] = "il processo non fidato non ha prodotto uno stato di esportazione leggibile"
        write_result(result_path, result)
        return

    if not export_status.get("export_ok"):
        result["error"] = export_status.get("error") or "esportazione STEP non riuscita"
        write_result(result_path, result)
        return

    step_rel = export_status.get("generated_part_step_path")
    if not step_rel:
        result["error"] = "stato di esportazione senza generated_part_step_path nonostante export_ok"
        write_result(result_path, result)
        return

    import cadquery as cq  # import qui, dopo set_limits()

    # Reimporta lo STEP da zero: NON riusa is_valid/bbox eventualmente
    # riportati dal processo non fidato — l'unica fonte di verita' e' il
    # file STEP stesso (vedi docstring del modulo).
    try:
        solid = cq.importers.importStep(os.path.join(GENERATED_PARTS_DIR, step_rel)).val()
    except Exception as e:
        result["error"] = f"import STEP fallito: {type(e).__name__}: {e}"
        write_result(result_path, result)
        return

    try:
        is_valid = solid.isValid()
        bb = solid.BoundingBox()
        measurements = {
            "is_valid": is_valid,
            "bbox_x_mm": round(bb.xlen, 4),
            "bbox_y_mm": round(bb.ylen, 4),
            "bbox_z_mm": round(bb.zlen, 4),
        }
    except Exception as e:
        result["error"] = f"misurazione fallita: {type(e).__name__}: {e}"
        write_result(result_path, result)
        return

    result["measurements"] = measurements
    result["generated_part_step_path"] = step_rel
    if not is_valid:
        result["execution"] = "FAIL"
        result["error"] = "geometria non manifold/watertight"
        write_result(result_path, result)
        return

    result["execution"] = "PASS"
    dimensional_check, execution_override = compute_dimensional_check(spec.get("feature"), spec, measurements)
    result["dimensional_check"] = dimensional_check
    if execution_override:
        result["execution"] = execution_override

    write_result(result_path, result)


if __name__ == "__main__":
    set_limits()
    main()
