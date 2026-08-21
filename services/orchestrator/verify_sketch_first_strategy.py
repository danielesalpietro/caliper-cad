"""
CALIPER — verifica manuale della strategia "sketch_first" nel loop (M3).

Stessa classe di mock di verify_gauge_check_loop_wiring.py (permessa
dall'handoff di questa milestone: valida la LOGICA del loop —
generate_code_for_attempt(), la classificazione degli errori di
generazione/validazione/compilazione — non la generazione reale, che
richiederebbe un chatflow Flowise "sketch-first" non disponibile in
questo sandbox, vedi docs/logbook_fase3.md). call_flowise_l2 e' mockata
per restituire testo pre-costruito (JSON valido, JSON malformato, JSON
che non valida lo schema) al posto di una vera risposta L2.

Quattro scenari:

A. call_flowise_l2 restituisce vincoli sketch-first VALIDI (M6, stessa
   spec di verify_sketch_compiler_thread.py) -> compilati, /verify PASS,
   gauge-check PASS -> successo al tentativo 1, /verify e /gauge-check
   chiamati con IL CODICE COMPILATO (non testo grezzo).
B. call_flowise_l2 restituisce testo che non e' JSON -> FAIL immediato
   di generazione, /verify MAI chiamato, classificato RETRY_GENERIC.
C. call_flowise_l2 restituisce JSON sintatticamente valido ma che non
   passa sketch_schema.validate_sketch_spec (angolo dichiarato
   inconsistente con le coordinate) -> stesso trattamento di B.
D. feature non supportata da sketch_first (es. "clearance_fit") ->
   errore ad OGNI tentativo (stesso errore ripetuto) -> uscita anticipata
   dopo 2 tentativi, mai una chiamata a /verify.

Uso: python verify_sketch_first_strategy.py
Nessuna dipendenza esterna (stdlib + retry_policy.py + sketch_schema.py,
gia' nella stessa directory).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import generate_and_verify as gv  # noqa: E402

VALID_THREAD_SKETCH = {
    "feature": "thread",
    "sketch": {
        "points": [
            {"id": "p_crest_a", "x": 3.0, "y": -0.5},
            {"id": "p_crest_b", "x": 3.0, "y": 0.5},
            {"id": "p_root", "x": 2.1339745962155613, "y": 0.0},
        ],
        "lines": [
            {"id": "l_flank_in", "start": "p_crest_a", "end": "p_root"},
            {"id": "l_flank_out", "start": "p_root", "end": "p_crest_b"},
            {"id": "l_close", "start": "p_crest_b", "end": "p_crest_a"},
        ],
        "arcs": [],
        "dimensions": [
            {"type": "distance", "refs": ["p_crest_a", "p_crest_b"], "value_mm": 1.0, "label": "pitch"},
            {"type": "angle", "refs": ["l_flank_in", "l_flank_out"], "value_deg": 60.0, "label": "thread_profile_angle"},
        ],
    },
    "operation": {
        "type": "helical_thread_cut",
        "host": {"type": "block", "size_mm": [20.0, 20.0, 8.0]},
        "major_diameter_mm": 6.0,
        "pitch_mm": 1.0,
        "engagement_length_mm": 8.0,
        "right_handed": True,
    },
}

INVALID_ANGLE_THREAD_SKETCH = json.loads(json.dumps(VALID_THREAD_SKETCH))
INVALID_ANGLE_THREAD_SKETCH["sketch"]["dimensions"][1]["value_deg"] = 90.0  # inconsistente con le coordinate (60 reali)


class FakeCalls:
    def __init__(self, flowise_texts, verify_responses=None, gauge_responses=None):
        self.flowise_texts = list(flowise_texts)
        self.verify_responses = list(verify_responses or [])
        self.gauge_responses = list(gauge_responses or [])
        self.verify_calls = []
        self.gauge_calls = []

    def call_flowise_l2(self, chatflow_id, spec_json, temperature=None):
        return self.flowise_texts.pop(0)

    def call_verifier(self, code, spec=None):
        self.verify_calls.append((code, spec))
        return self.verify_responses.pop(0)

    def call_gauge_check(self, part_step_path, gauge_job):
        self.gauge_calls.append((part_step_path, gauge_job))
        return self.gauge_responses.pop(0)


def run_scenario(spec, flowise_texts, verify_responses=None, gauge_responses=None):
    fake = FakeCalls(flowise_texts, verify_responses, gauge_responses)
    orig = {
        "resolve_chatflow_id": gv.resolve_chatflow_id,
        "call_flowise_l2": gv.call_flowise_l2,
        "call_verifier": gv.call_verifier,
        "call_gauge_check": gv.call_gauge_check,
        "FLOWISE_API_KEY": gv.FLOWISE_API_KEY,
        "L2_STRATEGY": gv.L2_STRATEGY,
    }
    gv.resolve_chatflow_id = lambda strategy="free_code": "sketch-chatflow-test"
    gv.call_flowise_l2 = fake.call_flowise_l2
    gv.call_verifier = fake.call_verifier
    gv.call_gauge_check = fake.call_gauge_check
    gv.FLOWISE_API_KEY = "test-key"
    gv.L2_STRATEGY = "sketch_first"

    argv_backup = sys.argv
    sys.argv = ["generate_and_verify.py", json.dumps(spec)]
    try:
        exit_code = gv.main()
    finally:
        sys.argv = argv_backup
        for k, v in orig.items():
            setattr(gv, k, v)

    return exit_code, fake


def verify_result(status, generated_part_step_path="job-x.step"):
    return {
        "status": status,
        "checks": [{"name": "execution_and_geometry", "status": status, "detail": None}],
        "measurements": {"is_valid": True},
        "dimensional_check": None,
        "generated_part_step_path": generated_part_step_path if status == "PASS" else None,
    }


def gauge_result(status):
    return {
        "status": status,
        "error": None,
        "gauge_check": {"status": status, "mode": "sweep", "interference_volume_mm3": 0.0 if status == "PASS" else None, "preflight_diagnostics": {}},
    }


THREAD_SPEC = {"feature": "thread", "nominal": "M6", "tolerance": 0.3}


def main():
    ok = True

    print("=== A. sketch-first valido -> compilato, /verify + gauge-check PASS ===")
    exit_code, fake = run_scenario(
        THREAD_SPEC,
        flowise_texts=[json.dumps(VALID_THREAD_SKETCH)],
        verify_responses=[verify_result("PASS")],
        gauge_responses=[gauge_result("PASS")],
    )
    sent_code = fake.verify_calls[0][0] if fake.verify_calls else None
    a_ok = (
        exit_code == 0
        and len(fake.verify_calls) == 1
        and sent_code is not None
        and "import cadquery" in sent_code
        and "_thread_pin" in sent_code  # e' codice COMPILATO, non il JSON grezzo
    )
    print(f"Atteso: successo, /verify chiamato con codice CadQuery compilato (non JSON grezzo): {'OK' if a_ok else 'FALLITO'} (exit={exit_code})")
    ok = ok and a_ok

    print("\n=== B. L2 (sketch-first) restituisce testo non-JSON -> FAIL di generazione, /verify mai chiamato ===")
    exit_code, fake = run_scenario(
        THREAD_SPEC,
        flowise_texts=["questo non e' JSON, e' testo libero come se L2 avesse ignorato le istruzioni"] * 3,
    )
    b_ok = exit_code == 1 and len(fake.verify_calls) == 0
    print(f"Atteso: FAIL, 0 chiamate a /verify: {'OK' if b_ok else 'FALLITO'} (exit={exit_code}, verify_calls={len(fake.verify_calls)})")
    ok = ok and b_ok

    print("\n=== C. JSON valido ma spec che non passa sketch_schema (angolo inconsistente) ===")
    exit_code, fake = run_scenario(
        THREAD_SPEC,
        flowise_texts=[json.dumps(INVALID_ANGLE_THREAD_SKETCH)] * 3,
    )
    c_ok = exit_code == 1 and len(fake.verify_calls) == 0
    print(f"Atteso: FAIL, 0 chiamate a /verify: {'OK' if c_ok else 'FALLITO'} (exit={exit_code}, verify_calls={len(fake.verify_calls)})")
    ok = ok and c_ok

    print("\n=== D. feature non supportata da sketch_first (clearance_fit) -> mai una chiamata a /verify ===")
    exit_code, fake = run_scenario(
        {"feature": "clearance_fit", "nominal": "D8", "tolerance": 0.2},
        flowise_texts=[json.dumps(VALID_THREAD_SKETCH)] * 3,  # non dovrebbe nemmeno arrivare a leggerlo
    )
    d_ok = exit_code == 1 and len(fake.verify_calls) == 0
    print(f"Atteso: FAIL (feature non supportata), 0 chiamate a /verify: {'OK' if d_ok else 'FALLITO'} (exit={exit_code}, verify_calls={len(fake.verify_calls)})")
    ok = ok and d_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
