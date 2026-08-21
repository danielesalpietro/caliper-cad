"""
CALIPER — verifica manuale di gauge_check.py contro i calibri REALI (M1).

Complementare a verify_gauge_check.py (che usa geometrie sintetiche
usa-e-getta per validare solo il meccanismo): qui il calibro e' quello
davvero versionato in config/gauges/ (thread_M6_GO_ISO68-1.step /
thread_M6_NOGO_ISO68-1.step, generato da generate_thread_gauge.py) — la
prima volta che questi file passano attraverso il protocollo job/result
reale, non solo attraverso un import diretto.

Il "pezzo" di controllo qui NON e' un foro filettato (costruirne uno
fedele e' scope di M3 — pipeline sketch-first -> compilazione ->
collaudo, non di M1): e' un blocco con un foro liscio passante al
diametro nominale 6.0mm. Basta a validare la semantica GO/NO-GO in modo
onesto e verificabile a mano:
  - il tampone GO (inviluppo Ø5.7mm) deve passare in un foro Ø6.0mm
    senza interferenza (e' piu' piccolo del foro) -> PASS atteso
  - il tampone NO-GO (inviluppo Ø6.3mm) deve interferire con un foro
    Ø6.0mm (e' piu' grande del foro) -> FAIL atteso, con volume di
    interferenza calcolabile analiticamente

Uso: python verify_gauge_check_real_gauges.py
Richiede cadquery installato (vedi verify_gauge_check.py per il dettaglio).
"""

import json
import math
import os
import subprocess
import sys
import tempfile

GAUGE_CHECK_PATH = os.path.join(os.path.dirname(__file__), "gauge_check.py")
GAUGES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "gauges")

BORE_DIAMETER_MM = 6.0
BLOCK_SIZE_MM = 20.0
BLOCK_THICKNESS_MM = 15.0  # >= lunghezza calibro, con margine
GAUGE_LENGTH_MM = 8.0  # deve combaciare con LENGTH_MM in generate_thread_gauge.py


def build_test_part(models_dir: str):
    import cadquery as cq

    # Il calibro (vedi generate_thread_gauge.py) e' costruito con
    # Workplane("XY").circle(...).extrude(length) -> occupa z in
    # [0, length_mm], non centrato sull'origine (verificato importando
    # il file reale: bbox z = [~0, 8.0]). Il blocco va allineato allo
    # stesso range, non centrato come farebbe box() di default, altrimenti
    # il volume di sovrapposizione atteso (calcolato piu' sotto) non
    # corrisponderebbe a quello davvero misurato.
    block = (
        cq.Workplane("XY")
        .box(BLOCK_SIZE_MM, BLOCK_SIZE_MM, BLOCK_THICKNESS_MM)
        .translate((0, 0, BLOCK_THICKNESS_MM / 2))
        .faces(">Z")
        .workplane()
        .hole(BORE_DIAMETER_MM, BLOCK_THICKNESS_MM)
    )
    path = os.path.join(models_dir, "block_bore_6mm.step")
    cq.exporters.export(block, path)
    return path


def run_gauge_check(part_rel: str, gauge_rel: str, models_dir: str, work_dir: str, tag: str):
    job = {"gauge_check": {"part_step_path": part_rel, "gauge_step_path": gauge_rel}}
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
        return json.load(f)


def main():
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        models_dir = os.path.join(tmp, "models")
        work_dir = os.path.join(tmp, "work")
        os.makedirs(models_dir)
        os.makedirs(work_dir)

        build_test_part(models_dir)

        print("--- Calibro GO (Ø5.7mm) contro foro liscio Ø6.0mm ---")
        result_go = run_gauge_check("block_bore_6mm.step", "thread_M6_GO_ISO68-1.step", models_dir, work_dir, "go")
        print(json.dumps(result_go, indent=2, ensure_ascii=False))
        go_ok = result_go["execution"] == "PASS" and result_go["gauge_check"]["interference_volume_mm3"] == 0.0
        print("Esito atteso PASS, volume 0.0:", "OK" if go_ok else "FALLITO")
        ok = ok and go_ok

        print("\n--- Calibro NO-GO (Ø6.3mm) contro lo stesso foro Ø6.0mm ---")
        result_nogo = run_gauge_check("block_bore_6mm.step", "thread_M6_NOGO_ISO68-1.step", models_dir, work_dir, "nogo")
        print(json.dumps(result_nogo, indent=2, ensure_ascii=False))
        # A differenza del caso sintetico in verify_gauge_check.py (pin
        # PIENO, volume di interferenza calcolabile in forma chiusa), qui
        # il calibro NO-GO e' un TAMPONE FILETTATO: solo le creste
        # dell'elica raggiungono il diametro di inviluppo 6.3mm, il resto
        # del profilo a V sta sotto — un volume di interferenza esatto
        # richiederebbe integrare l'intersezione del profilo a V lungo
        # l'elica, calcolo fuori scope per uno script di verifica manuale.
        # Qui si verifica invece che il volume misurato sia (a) positivo
        # (interferenza rilevata, non solo lo status) e (b) plausibile:
        # sotto il limite superiore ottenuto assumendo — irrealisticamente
        # — un cilindro PIENO al diametro di inviluppo per l'intera
        # lunghezza del calibro (nessun profilo a V puo' interferire piu'
        # di un cilindro pieno alla stessa dimensione).
        upper_bound_volume = math.pi * (3.15**2 - 3.0**2) * GAUGE_LENGTH_MM
        measured_volume = result_nogo["gauge_check"]["interference_volume_mm3"]
        nogo_ok = result_nogo["execution"] == "FAIL" and 0 < measured_volume < upper_bound_volume
        print(
            f"Esito atteso FAIL, volume > 0 e < limite superiore {upper_bound_volume:.4f}mm3 "
            f"(cilindro pieno equivalente) — misurato {measured_volume}mm3:",
            "OK" if nogo_ok else "FALLITO",
        )
        ok = ok and nogo_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
