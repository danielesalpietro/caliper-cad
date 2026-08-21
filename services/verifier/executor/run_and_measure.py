"""
CALIPER — Livello 3, esecutore isolato per un singolo job.

Invocato come processo separato (mai in-process nel watcher), con limiti
di risorse impostati PRIMA di eseguire il codice non fidato. Il
container che lo ospita ha network_mode: none (vedi docker-compose.yml)
— questo script non presume l'isolamento di rete, lo eredita dal
container, ma applica comunque limiti di CPU/memoria per singolo job
perche' il watcher e' un processo a lunga vita e job successivi non
devono poter degradarsi a vicenda.

Uso: python run_and_measure.py <job.json> <result.json>

[M3] Esportazione STEP del pezzo validato (gauge_check.py, vedi
docs/logbook_fase3.md): /models e' montato READ-ONLY in
verifier-executor (docker-compose.yml, per i pezzi di riferimento
statici di M1/M2 — l'AI non doveva entrarci) — un pezzo appena generato
non puo' finire li'. Va invece scritto sotto /exec/parts, sottocartella
dello stesso volume verifier_exec gia' condiviso e scrivibile in questo
container (nessun nuovo mount). gauge_check.py risolve questa cartella
con una root separata (GENERATED_PARTS_ROOT, part_source="generated"),
mai confusa con /models (part_source="models", invariato per gli
script M1/M2 esistenti) — vedi gauge_check.py per il motivo per cui le
due radici restano distinte anche se ora coesistono nello stesso
volume.
"""

import json
import os
import resource
import sys

# OpenBLAS (usato da numpy/cadquery) prealloca un pool di memoria per
# thread in base ai core visibili, non alla dimensione del problema —
# senza questo, anche uno sweep minuscolo puo' esaurire un limite di
# memoria per processo apparentemente generoso. Va impostato prima che
# numpy venga importato (quindi qui, prima di "import cadquery" nel
# codice eseguito piu' sotto).
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

# Sovrascrivibile via env per uso/test fuori Docker — stesso pattern
# gia' in uso in gauge_check.py per MODELS_ROOT/GAUGES_ROOT.
GENERATED_PARTS_DIR = os.environ.get("GENERATED_PARTS_DIR", "/exec/parts")


def set_limits():
    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))  # 10s di CPU
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))  # 2GB memoria


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


def main():
    job_path, result_path = sys.argv[1], sys.argv[2]
    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)
    code = job["code"]
    spec = job.get("spec") or {}

    result = {"execution": "FAIL", "error": None, "measurements": None, "dimensional_check": None}

    namespace = {}
    try:
        exec(code, namespace)  # nosec - codice non fidato, isolato per processo/container
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        write_result(result_path, result)
        return

    obj = namespace.get("result")
    if obj is None:
        result["error"] = "'result' non trovato dopo l'esecuzione"
        write_result(result_path, result)
        return

    try:
        solid = obj.val() if hasattr(obj, "val") else obj
        is_valid = solid.isValid()
        bb = solid.BoundingBox()
        measurements = {
            "is_valid": is_valid,
            "bbox_x_mm": round(bb.xlen, 4),
            "bbox_y_mm": round(bb.ylen, 4),
            "bbox_z_mm": round(bb.zlen, 4),
        }
        result["measurements"] = measurements
        result["execution"] = "PASS" if is_valid else "FAIL"
        if not is_valid:
            result["error"] = "geometria non manifold/watertight"
    except Exception as e:
        result["error"] = f"misurazione fallita: {type(e).__name__}: {e}"
        write_result(result_path, result)
        return

    # Esporta il pezzo validato come STEP, cosi' un gauge-check successivo
    # (M3, vedi docstring del modulo) puo' importarlo — solo se la
    # geometria e' valida: un solido non manifold non e' comunque
    # gauge-checkabile, non ha senso esportarlo.
    if result["execution"] == "PASS":
        job_id = os.path.splitext(os.path.basename(job_path))[0]
        try:
            os.makedirs(GENERATED_PARTS_DIR, exist_ok=True)
            step_rel = f"{job_id}.step"
            solid.exportStep(os.path.join(GENERATED_PARTS_DIR, step_rel))
            result["generated_part_step_path"] = step_rel
        except Exception as e:
            # Nessun pezzo esportato = nessun gauge-check possibile a
            # valle: e' un FAIL reale, non un dettaglio da inghiottire in
            # silenzio (stessa disciplina di "misurazione fallita" sopra).
            result["execution"] = "FAIL"
            result["error"] = f"esportazione STEP fallita: {type(e).__name__}: {e}"
            result["generated_part_step_path"] = None
            write_result(result_path, result)
            return
    else:
        result["generated_part_step_path"] = None

    # Confronto dimensionale — solo per "thread" per ora, unico feature
    # con preset definito (vedi services/orchestrator/presets.json)
    feature = spec.get("feature")
    if feature == "thread" and result["measurements"]:
        nominal_mm = parse_nominal_mm(spec.get("nominal", ""))
        tolerance = spec.get("tolerance")
        measured_diameter = max(measurements["bbox_x_mm"], measurements["bbox_y_mm"])
        if nominal_mm is not None and tolerance is not None:
            delta = abs(measured_diameter - nominal_mm)
            within = delta <= tolerance
            result["dimensional_check"] = {
                "nominal_mm": nominal_mm,
                "measured_diameter_mm": measured_diameter,
                "tolerance_mm": tolerance,
                "delta_mm": round(delta, 4),
                "status": "PASS" if within else "FAIL",
            }
            if not within:
                result["execution"] = "FAIL"

    write_result(result_path, result)


if __name__ == "__main__":
    set_limits()
    main()
