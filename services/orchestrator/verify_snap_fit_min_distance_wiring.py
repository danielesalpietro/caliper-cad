"""
CALIPER — verifica manuale del collegamento min_distance/snap_fit al loop
reale (M5, C3).

Vedi docs/review_tecnica.md (C3) e docs/handoff_m5.md, Blocco C:
generate_and_verify.gauge_check_job_for_preset() pretendeva SEMPRE
gauge_go_step per qualunque gauge_check_mode — ma "min_distance" (usato
da "snap_fit") non usa un calibro fisico, il "calibro virtuale" e' tra
due punti dello STESSO pezzo (measurement_points nel preset, vedi
presets.json e gauge_check.py::run_min_distance). Prima di questo fix,
QUALUNQUE spec "snap_fit" mandava main() in ValueError ("Preset
incoerente per il gauge-check") prima ancora di generare — peggio che
"defined: false" (sembra supportato, crasha a runtime).

Stessa classe di mock di verify_gauge_check_loop_wiring.py: Flowise/
verifier/gauge-check sostituiti da risposte pre-costruite, per validare
la LOGICA del loop (costruzione del job da measurement_points, gestione
dell'esito), non la generazione reale.

Tre scenari:
A. Spec "snap_fit" attraverso il loop -> nessun ValueError; il job
   /gauge-check costruito da main() ha mode="min_distance" e i punti
   ESATTI di measurement_points.retention_gap dal preset (assert sui
   contenuti, non solo "non crasha"); PASS del min_distance -> PASS
   immediato (nessun NO-GO per questo mode, vedi Blocco B/C2).
B. Stesso preset, esito FAIL del min_distance -> gestito dal loop come
   qualunque altro FAIL (classify_checkpoint/RetryBudget, uscita
   anticipata dopo 2 tentativi identici) — nessuna eccezione, nessun
   trattamento speciale.
C. Preset con gauge_check_mode="min_distance" ma measurement_points
   vuoto/assente -> ValueError esplicito (nessun fallback silenzioso),
   stesso stile di errore "Preset incoerente" gia' in uso per gli altri
   mode.

Uso: python verify_snap_fit_min_distance_wiring.py
Nessuna dipendenza esterna (stdlib + retry_policy.py, gia' nella stessa
directory).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import generate_and_verify as gv  # noqa: E402


class FakeCalls:
    def __init__(self, verify_responses, gauge_responses=None):
        self.verify_responses = list(verify_responses)
        self.gauge_responses = list(gauge_responses or [])
        self.verify_calls = []
        self.gauge_calls = []

    def call_flowise_l2(self, chatflow_id, spec_json, temperature=None):
        return "# codice finto\nimport cadquery as cq\nresult = cq.Workplane('XY').box(1,1,1)\n"

    def call_verifier(self, code, spec=None):
        self.verify_calls.append((code, spec))
        return self.verify_responses.pop(0)

    def call_gauge_check(self, part_step_path, gauge_job):
        self.gauge_calls.append((part_step_path, gauge_job))
        return self.gauge_responses.pop(0)


def run_scenario(spec, verify_responses, gauge_responses, presets_override=None):
    fake = FakeCalls(verify_responses, gauge_responses)
    orig = {
        "resolve_chatflow_id": gv.resolve_chatflow_id,
        "call_flowise_l2": gv.call_flowise_l2,
        "call_verifier": gv.call_verifier,
        "call_gauge_check": gv.call_gauge_check,
        "FLOWISE_API_KEY": gv.FLOWISE_API_KEY,
        "load_presets": gv.load_presets,
    }
    gv.resolve_chatflow_id = lambda strategy="free_code": "chatflow-test"
    gv.call_flowise_l2 = fake.call_flowise_l2
    gv.call_verifier = fake.call_verifier
    gv.call_gauge_check = fake.call_gauge_check
    gv.FLOWISE_API_KEY = "test-key"
    if presets_override is not None:
        gv.load_presets = lambda: presets_override

    argv_backup = sys.argv
    sys.argv = ["generate_and_verify.py", json.dumps(spec)]
    error = None
    exit_code = None
    try:
        exit_code = gv.main()
    except Exception as e:  # noqa: BLE001 - vogliamo catturare anche il ValueError pre-fix
        error = e
    finally:
        sys.argv = argv_backup
        for k, v in orig.items():
            setattr(gv, k, v)

    return exit_code, error, fake


def verify_result(status):
    return {
        "status": status,
        "checks": [{"name": "execution_and_geometry", "status": status, "detail": None}],
        "measurements": {"is_valid": True, "bbox_x_mm": 20.0, "bbox_y_mm": 20.0, "bbox_z_mm": 8.3},
        "dimensional_check": None,
        "generated_part_step_path": "job-snap.step" if status == "PASS" else None,
    }


def min_distance_result(status):
    return {
        "status": status,
        "error": None,
        "gauge_check": {
            "status": status,
            "mode": "min_distance",
            "interference_volume_mm3": None,
            "preflight_diagnostics": {"max_entity_tolerance_mm": 0.0001},
            "min_distance": {"measured_mm": 0.3 if status == "PASS" else 0.9},
        },
    }


SNAP_FIT_SPEC = {"feature": "snap_fit"}


def main():
    ok = True

    print("=== A. spec snap_fit -> job min_distance costruito dai measurement_points, PASS ===")
    exit_code, error, fake = run_scenario(
        SNAP_FIT_SPEC,
        verify_responses=[verify_result("PASS")],
        gauge_responses=[min_distance_result("PASS")],
    )
    job_sent = fake.gauge_calls[0][1] if fake.gauge_calls else None
    expected_min_distance = {
        "point_a_mm": [-6.0, 0.0, 8.0],
        "point_b_mm": [-6.0, 0.0, 8.3],
        "nominal_mm": 0.3,
        "tolerance_mm": 0.1,
    }
    a_ok = (
        error is None
        and exit_code == 0
        and job_sent is not None
        and job_sent.get("mode") == "min_distance"
        and "gauge_step_path" not in job_sent
        and job_sent.get("min_distance") == expected_min_distance
    )
    print(
        f"Atteso: nessun ValueError (oggi: 'Preset incoerente per il gauge-check' — dimostralo), "
        f"job mode='min_distance' coi punti esatti del preset, PASS immediato: {'OK' if a_ok else 'FALLITO'} "
        f"(exit={exit_code}, error={error!r}, job_sent={job_sent})"
    )
    ok = ok and a_ok

    print("\n=== B. spec snap_fit, min_distance FAIL ripetuto -> gestito come un FAIL qualunque (uscita anticipata) ===")
    exit_code, error, fake = run_scenario(
        SNAP_FIT_SPEC,
        verify_responses=[verify_result("PASS"), verify_result("PASS"), verify_result("PASS")],
        gauge_responses=[min_distance_result("FAIL"), min_distance_result("FAIL"), min_distance_result("FAIL")],
    )
    b_ok = error is None and exit_code == 1 and len(fake.gauge_calls) == 2  # uscita anticipata dopo 2 ripetizioni
    print(
        f"Atteso: nessuna eccezione, FAIL gestito dal loop (uscita anticipata dopo 2 tentativi identici): "
        f"{'OK' if b_ok else 'FALLITO'} (exit={exit_code}, error={error!r}, gauge_calls={len(fake.gauge_calls)})"
    )
    ok = ok and b_ok

    print("\n=== C. preset min_distance senza measurement_points -> ValueError esplicito (nessun fallback silenzioso) ===")
    broken_presets = {
        "snap_fit": {"defined": True, "gauge_check_mode": "min_distance", "measurement_points": {}},
        "thread": {"defined": False},
        "clearance_fit": {"defined": False},
        "press_fit": {"defined": False},
        "hole": {"defined": False},
        "boss": {"defined": False},
        "other": {"defined": False},
    }
    exit_code, error, fake = run_scenario(
        SNAP_FIT_SPEC,
        verify_responses=[],
        gauge_responses=[],
        presets_override=broken_presets,
    )
    c_ok = error is None and exit_code == 1  # gestito con return 1 e messaggio, non una eccezione che risale
    print(
        f"Atteso: 'Preset incoerente' gestito con return 1 (non un'eccezione non catturata): "
        f"{'OK' if c_ok else 'FALLITO'} (exit={exit_code}, error={error!r})"
    )
    ok = ok and c_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
