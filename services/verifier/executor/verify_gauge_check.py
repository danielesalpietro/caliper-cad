"""
CALIPER — verifica manuale di gauge_check.py (M1).

Non e' un test automatico integrato in una suite (il progetto non ne ha
ancora una — vedi il resto del repository, verificato finora con script
ad-hoc e risultati incollati nei logbook, es. v12/v14 in
docs/architettura-prototipo-mesh-llm.md). Questo script segue lo stesso
stile: eseguibile a mano, stampa un esito leggibile, pensato per essere
rilanciato e il suo output incollato in un logbook come prova diretta.

Genera geometrie SINTETICHE (non i calibri reali M6 — quelli non esistono
ancora, vedi config/gauges/README.md) solo per validare il MECCANISMO:

1. Un pin che passa in un anello (nessuna interferenza attesa -> PASS)
2. Un pin sovradimensionato nello stesso anello (interferenza attesa,
   con volume noto analiticamente -> FAIL)
3. Determinismo: stesso job eseguito due volte, output byte per byte
   identico (criterio di accettazione esplicito di M1, vedi
   docs/logbook_fase1.md)

Uso: python verify_gauge_check.py
Richiede cadquery installato (nel container verifier-executor c'e' gia'
— vedi Dockerfile; per uso locale fuori Docker: pip install cadquery==2.8.0).
"""

import json
import math
import os
import subprocess
import sys
import tempfile

GAUGE_CHECK_PATH = os.path.join(os.path.dirname(__file__), "gauge_check.py")


def build_fixtures(models_dir: str, gauges_dir: str):
    import cadquery as cq

    # Pin diametro 5.7mm, dentro un anello con foro 6.0mm -> nessuna interferenza
    cq.exporters.export(cq.Workplane("XY").cylinder(20, 5.7 / 2), os.path.join(models_dir, "pin_ok.step"))

    # Pin diametro 6.3mm (0.3mm oversize sul raggio) -> interferenza attesa
    cq.exporters.export(cq.Workplane("XY").cylinder(20, 6.3 / 2), os.path.join(models_dir, "pin_oversize.step"))

    # Calibro ad anello, foro 6.0mm, spessore 10mm
    ring = cq.Workplane("XY").circle(15).circle(6.0 / 2).extrude(10)
    cq.exporters.export(ring, os.path.join(gauges_dir, "ring_6mm.step"))


def run_gauge_check(job: dict, models_dir: str, gauges_dir: str, work_dir: str, tag: str):
    job_path = os.path.join(work_dir, f"job_{tag}.json")
    result_path = os.path.join(work_dir, f"result_{tag}.json")
    checkpoint_path = os.path.join(work_dir, f"checkpoint_{tag}.json")
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump(job, f)

    env = dict(os.environ)
    env["GAUGE_CHECK_MODELS_ROOT"] = models_dir
    env["GAUGE_CHECK_GAUGES_ROOT"] = gauges_dir

    subprocess.run(
        [sys.executable, GAUGE_CHECK_PATH, job_path, result_path, checkpoint_path],
        env=env,
        check=True,
        capture_output=True,
    )
    with open(result_path, "r", encoding="utf-8") as f:
        return json.load(f), result_path


def main():
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        models_dir = os.path.join(tmp, "models")
        gauges_dir = os.path.join(tmp, "gauges")
        work_dir = os.path.join(tmp, "work")
        os.makedirs(models_dir)
        os.makedirs(gauges_dir)
        os.makedirs(work_dir)

        build_fixtures(models_dir, gauges_dir)

        # 1. Caso senza interferenza -> PASS atteso
        job_ok = {"gauge_check": {"part_step_path": "pin_ok.step", "gauge_step_path": "ring_6mm.step"}}
        result_ok, _ = run_gauge_check(job_ok, models_dir, gauges_dir, work_dir, "ok")
        print("--- Caso 1: pin entro tolleranza ---")
        print(json.dumps(result_ok, indent=2, ensure_ascii=False))
        pass_ok = result_ok["execution"] == "PASS" and result_ok["gauge_check"]["interference_volume_mm3"] == 0.0
        print("Esito atteso PASS, volume 0.0:", "OK" if pass_ok else "FALLITO")
        ok = ok and pass_ok

        # 2. Caso con interferenza -> FAIL atteso, volume noto analiticamente
        # overlap radiale (3.15 - 3.0) su una corona circolare, altezza 10mm:
        # area = pi*(3.15^2 - 3.0^2); volume = area * 10
        expected_volume = math.pi * (3.15**2 - 3.0**2) * 10
        job_bad = {"gauge_check": {"part_step_path": "pin_oversize.step", "gauge_step_path": "ring_6mm.step"}}
        result_bad, result_bad_path = run_gauge_check(job_bad, models_dir, gauges_dir, work_dir, "bad")
        print("\n--- Caso 2: pin oversize ---")
        print(json.dumps(result_bad, indent=2, ensure_ascii=False))
        measured_volume = result_bad["gauge_check"]["interference_volume_mm3"]
        volume_ok = result_bad["execution"] == "FAIL" and abs(measured_volume - expected_volume) < 0.01
        print(f"Esito atteso FAIL, volume ~{expected_volume:.4f}mm3 (misurato {measured_volume}):", "OK" if volume_ok else "FALLITO")
        ok = ok and volume_ok

        # 3. Determinismo: stesso job, due esecuzioni, output identico
        _, result_bad_path_2 = run_gauge_check(job_bad, models_dir, gauges_dir, work_dir, "bad_repeat")
        with open(result_bad_path, "rb") as f1, open(result_bad_path_2, "rb") as f2:
            identical = f1.read() == f2.read()
        print("\n--- Caso 3: determinismo (stesso job, due esecuzioni) ---")
        print("Output byte per byte identico:", "OK" if identical else "FALLITO")
        ok = ok and identical

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
