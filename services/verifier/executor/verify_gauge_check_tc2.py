"""
CALIPER — verifica manuale di gauge_check.py, TC2 (M2).

Filettatura metrica ISO — sweep elicoidale sincronizzato al passo,
riusando i calibri REALI Ø5.7/Ø6.3 di M1 (thread_M6_GO/NOGO_ISO68-1.step
— tampone filettato esterno, per verificare un FORO filettato). Vedi
docs/logbook_fase2.md, TC2, e docs/handoff_m2.md.

Pezzo di controllo: qui, a differenza di verify_gauge_check_real_gauges.py
(M1, foro LISCIO — insufficiente per validare uno sweep elicoidale), un
foro filettato SINTETICO ma geometricamente coerente: un blocco con la
cavità = complemento booleano di un tampone filettato costruito alla
misura NOMINALE (Ø6.0mm) con la STESSA funzione
generate_thread_gauge.build_thread_plug() usata per i calibri stessi —
garantisce che passo/profilo/verso dell'elica del "foro" e dei calibri
siano esattamente coerenti (stessa parametrizzazione), non due
costruzioni indipendenti che potrebbero disallinearsi in fase. Non è un
foro filettato "reale" prodotto dalla pipeline L2 (quello è M3) — vedi
riserva onesta più sotto.

Moto di avvitamento (screw motion): Location(t=(0,0,z), asse=(0,0,1),
angolo) con angolo = (z / pitch) * 360 — rotazione attorno all'asse
seguita da traslazione, verificato empiricamente essere la composizione
corretta (vedi commit di questa milestone). Il verso (segno) è stato
determinato empiricamente confrontando le due possibilità contro il
calibro GO: un verso sbagliato produce interferenza massiccia anche a
piena registrazione di fase, quello corretto no (vedi
HELICAL_SWEEP_VOLUME_EPSILON_MM3 in gauge_check.py per il dettaglio
numerico misurato).

Uso: python verify_gauge_check_tc2.py
Richiede cadquery installato (pip install cadquery==2.8.0).
"""

import json
import os
import subprocess
import sys
import tempfile

GAUGE_CHECK_PATH = os.path.join(os.path.dirname(__file__), "gauge_check.py")
GAUGES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "gauges")

PITCH_MM = 1.0  # deve combaciare col preset "thread" in presets.json
ANGLE_DEG = 60.0
LENGTH_MM = 8.0  # deve combaciare con LENGTH_MM in generate_thread_gauge.py
NOMINAL_DIAMETER_MM = 6.0
BLOCK_SIZE_MM = 20.0

SWEEP_STEPS = 21
SWEEP_START_OFFSET_MM = 0.0
SWEEP_END_OFFSET_MM = LENGTH_MM


def build_nominal_thread_cavity(models_dir: str):
    """Blocco con una cavità filettata M6 NOMINALE (tolleranza zero) —
    complemento booleano diretto del tampone esterno alla stessa misura,
    costruito con la stessa build_thread_plug() dei calibri reali (vedi
    docstring del modulo per il perché questo garantisce coerenza di
    fase/passo/verso invece di una costruzione indipendente)."""
    sys.path.insert(0, os.path.abspath(GAUGES_DIR))
    from generate_thread_gauge import build_thread_plug  # noqa: E402

    import cadquery as cq

    block = cq.Workplane("XY").box(BLOCK_SIZE_MM, BLOCK_SIZE_MM, LENGTH_MM, centered=(True, True, False))
    nominal_solid, _, _ = build_thread_plug(NOMINAL_DIAMETER_MM, PITCH_MM, LENGTH_MM, ANGLE_DEG)
    cavity = block.cut(nominal_solid)

    path = os.path.join(models_dir, "block_thread_M6_nominal_cavity.step")
    cq.exporters.export(cavity, path)
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
                "pitch_mm": PITCH_MM,
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

        build_nominal_thread_cavity(models_dir)
        part_rel = "block_thread_M6_nominal_cavity.step"

        print("=== TC2 — sweep elicoidale (avvitamento sincronizzato al passo) ===")
        result_go, _ = run_gauge_check(sweep_job(part_rel, "thread_M6_GO_ISO68-1.step"), models_dir, work_dir, "go")
        print("\n--- Calibro GO (Ø5.7mm) ---")
        print(json.dumps(result_go, indent=2, ensure_ascii=False))
        go_sweep = result_go["gauge_check"]["sweep"]
        go_ok = (
            result_go["execution"] == "PASS"
            and go_sweep["steps_completed"] == SWEEP_STEPS
            and go_sweep["first_interference_step"] is None
        )
        print(
            f"Atteso PASS su tutti i {SWEEP_STEPS} step (nessuna interferenza reale, solo il "
            f"residuo noto di costruzione finita entro {go_sweep['volume_epsilon_mm3']}mm3 — "
            f"massimo osservato qui: {result_go['gauge_check']['interference_volume_mm3']}mm3):",
            "OK" if go_ok else "FALLITO",
        )
        ok = ok and go_ok

        result_nogo, result_nogo_path = run_gauge_check(
            sweep_job(part_rel, "thread_M6_NOGO_ISO68-1.step"), models_dir, work_dir, "nogo"
        )
        print("\n--- Calibro NO-GO (Ø6.3mm) ---")
        print(json.dumps(result_nogo, indent=2, ensure_ascii=False))
        nogo_sweep = result_nogo["gauge_check"]["sweep"]
        nogo_ok = result_nogo["execution"] == "FAIL" and nogo_sweep["first_interference_step"] is not None
        print(
            f"Atteso FAIL con interferenza rilevata (step {nogo_sweep['first_interference_step']} "
            f"su {SWEEP_STEPS}):",
            "OK" if nogo_ok else "FALLITO",
        )
        ok = ok and nogo_ok

        _, result_nogo_path_2 = run_gauge_check(
            sweep_job(part_rel, "thread_M6_NOGO_ISO68-1.step"), models_dir, work_dir, "nogo_repeat"
        )
        with open(result_nogo_path, "rb") as f1, open(result_nogo_path_2, "rb") as f2:
            identical = f1.read() == f2.read()
        print("\n--- Determinismo sweep elicoidale (stesso job, due esecuzioni) ---")
        print("Output byte per byte identico:", "OK" if identical else "FALLITO")
        ok = ok and identical

    print(
        "\nRiserva onesta: il pezzo di controllo è un foro filettato SINTETICO "
        "(complemento booleano del tampone nominale), non un foro filettato reale "
        "prodotto dalla pipeline L2 — vedi docstring del modulo. Il residuo "
        "geometrico misurato sul calibro GO (~0.31mm3, entro l'epsilon di 0.5mm3) "
        "deriva dalle estremità piatte non smussate dello sweep elicoidale finito, "
        "non da un errore di fase — vedi HELICAL_SWEEP_VOLUME_EPSILON_MM3 in "
        "gauge_check.py."
    )
    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
