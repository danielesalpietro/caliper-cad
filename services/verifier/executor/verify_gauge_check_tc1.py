"""
CALIPER — verifica manuale di gauge_check.py, TC1 (M2).

Accoppiamento albero-mozzo a giochi (clearance fit) — vedi
docs/logbook_fase2.md, TC1. Stesso stile di verify_gauge_check_real_gauges.py
(M1): script eseguibile a mano, output incollato come prova diretta, non
un test automatico in una suite (il progetto non ne ha ancora una).

Pezzo di controllo: un mozzo (blocco 20x20mm) con un foro passante liscio
al diametro nominale 8.0mm, profondita' 12mm — non generato da un LLM
(vedi criterio di accettazione M2 in docs/logbook_fase2.md, "geometrie
note, disegnate convenzionalmente").

Verifica sia static_interference (equivalente al controllo gia' fatto in
M1 per TC2) sia sweep lineare (nessuna rotazione, pitch_mm assente):
- calibro GO (Ø7.8mm) deve PASSARE sia statico sia in ogni step dello
  sweep lungo l'intera corsa d'inserimento;
- calibro NO-GO (Ø8.2mm) deve FALLIRE sia statico sia sweep.

Nota onesta sul valore aggiunto dello sweep qui: la spina (15mm) e' piu'
lunga del foro di controllo (12mm) — un singolo controllo statico che
copre l'intera profondita' del foro in un colpo solo rileva GIA' un
eventuale strozzamento locale lungo la corsa (un boolean intersect e' un
controllo di insieme, non "cieco" tra le due estremita'). Il valore
indipendente dello sweep qui non e' quindi "rileva difetti che il
controllo statico non vede" (non dimostrato da questo script), ma il
checkpoint per-step per la diagnostica di TIMEOUT gia' documentato in
docs/logbook_fase2.md — verificato sotto controllando che
first_interference_step sia popolato correttamente per il caso NO-GO.

Uso: python verify_gauge_check_tc1.py
Richiede cadquery installato (pip install cadquery==2.8.0, vedi
verify_gauge_check.py per il dettaglio fuori Docker).
"""

import json
import os
import subprocess
import sys
import tempfile

GAUGE_CHECK_PATH = os.path.join(os.path.dirname(__file__), "gauge_check.py")
GAUGES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "gauges")

BORE_DIAMETER_MM = 8.0
HUB_SIZE_MM = 20.0
HUB_DEPTH_MM = 12.0
PIN_LENGTH_MM = 15.0  # deve combaciare con LENGTH_MM in generate_pin_gauge.py

# Corsa dello sweep: dalla spina quasi del tutto fuori (solo la punta
# impegnata) alla spina completamente inserita e oltre, coerente col
# principio "sweep = simula l'inserimento reale, non solo la posizione
# finale" di docs/logbook_fase2.md, TC1.
SWEEP_START_OFFSET_MM = HUB_DEPTH_MM
SWEEP_END_OFFSET_MM = -(PIN_LENGTH_MM - HUB_DEPTH_MM)
SWEEP_STEPS = 16


def build_test_hub(models_dir: str):
    import cadquery as cq

    hub = (
        cq.Workplane("XY")
        .box(HUB_SIZE_MM, HUB_SIZE_MM, HUB_DEPTH_MM)
        .translate((0, 0, HUB_DEPTH_MM / 2))
        .faces(">Z")
        .workplane()
        .hole(BORE_DIAMETER_MM, HUB_DEPTH_MM)
    )
    path = os.path.join(models_dir, "hub_bore_8mm.step")
    cq.exporters.export(hub, path)
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


def sweep_job(part_rel, gauge_rel):
    return {
        "gauge_check": {
            "part_step_path": part_rel,
            "gauge_step_path": gauge_rel,
            "mode": "sweep",
            "sweep": {
                "start_offset_mm": SWEEP_START_OFFSET_MM,
                "end_offset_mm": SWEEP_END_OFFSET_MM,
                "steps": SWEEP_STEPS,
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

        build_test_hub(models_dir)
        part_rel = "hub_bore_8mm.step"

        print("=== TC1 — static_interference (baseline, come M1/TC2) ===")
        for tag, gauge_rel, expect in (
            ("go_static", "pin_D8_GO_clearance.step", "PASS"),
            ("nogo_static", "pin_D8_NOGO_clearance.step", "FAIL"),
        ):
            job = {"gauge_check": {"part_step_path": part_rel, "gauge_step_path": gauge_rel}}
            result, _ = run_gauge_check(job, models_dir, work_dir, tag)
            print(f"\n--- {tag} ---")
            print(json.dumps(result, indent=2, ensure_ascii=False))
            got = result["execution"]
            print(f"Atteso {expect}, ottenuto {got}:", "OK" if got == expect else "FALLITO")
            ok = ok and (got == expect)

        print("\n=== TC1 — sweep lineare lungo l'asse d'inserimento ===")
        result_go, _ = run_gauge_check(sweep_job(part_rel, "pin_D8_GO_clearance.step"), models_dir, work_dir, "go_sweep")
        print("\n--- GO sweep ---")
        print(json.dumps(result_go, indent=2, ensure_ascii=False))
        go_ok = (
            result_go["execution"] == "PASS"
            and result_go["gauge_check"]["sweep"]["steps_completed"] == SWEEP_STEPS
            and result_go["gauge_check"]["sweep"]["first_interference_step"] is None
        )
        print(
            f"Atteso PASS su tutti i {SWEEP_STEPS} step, nessuna interferenza:",
            "OK" if go_ok else "FALLITO",
        )
        ok = ok and go_ok

        result_nogo, result_nogo_path = run_gauge_check(
            sweep_job(part_rel, "pin_D8_NOGO_clearance.step"), models_dir, work_dir, "nogo_sweep"
        )
        print("\n--- NOGO sweep ---")
        print(json.dumps(result_nogo, indent=2, ensure_ascii=False))
        nogo_sweep = result_nogo["gauge_check"]["sweep"]
        nogo_ok = (
            result_nogo["execution"] == "FAIL"
            and nogo_sweep["first_interference_step"] is not None
            and nogo_sweep["steps_completed"] <= SWEEP_STEPS
        )
        print(
            f"Atteso FAIL con first_interference_step valorizzato (uscita anticipata "
            f"allo step {nogo_sweep['first_interference_step']} su {SWEEP_STEPS}):",
            "OK" if nogo_ok else "FALLITO",
        )
        ok = ok and nogo_ok

        # Determinismo dello sweep (criterio gia' applicato in M1 alla
        # static_interference, esteso qui alla nuova modalita').
        _, result_nogo_path_2 = run_gauge_check(
            sweep_job(part_rel, "pin_D8_NOGO_clearance.step"), models_dir, work_dir, "nogo_sweep_repeat"
        )
        with open(result_nogo_path, "rb") as f1, open(result_nogo_path_2, "rb") as f2:
            identical = f1.read() == f2.read()
        print("\n--- Determinismo sweep (stesso job, due esecuzioni) ---")
        print("Output byte per byte identico:", "OK" if identical else "FALLITO")
        ok = ok and identical

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
