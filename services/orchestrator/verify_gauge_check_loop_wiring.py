"""
CALIPER — verifica manuale del collegamento /gauge-check al loop reale (M3).

Testa la LOGICA del loop in generate_and_verify.py (stessa classe di mock
gia' usata in verify_retry_policy.py per M2, esplicitamente permessa
dall'handoff di questa milestone: sostituisce Flowise/verifier/gauge-check
con risposte pre-costruite per validare il CONTROLLO del loop, non la
qualita' della generazione — quella resta l'oggetto della milestone e
richiede un'istanza Flowise viva, non disponibile in questo sandbox, vedi
docs/logbook_fase3.md). Nessuna pretesa di validare qui l'esecuzione
end-to-end reale.

Sette scenari (A-E di M3, F/G aggiunti in M5 per C2 — vedi
docs/review_tecnica.md e docs/handoff_m5.md):

A. /verify PASS + GO PASS + NO-GO con interferenza al primo tentativo ->
   successo immediato, un solo record nel budget. [M5] Da questa
   milestone il PASS richiede ANCHE il NO-GO (deve interferire) — prima
   bastava il solo GO (vedi F/G sotto per i due casi dedicati al NO-GO).
B. /verify PASS ma gauge-check (GO) TIMEOUT (con checkpoint che
   classifica SWEEP_TIMEOUT_EARLY) al tentativo 1, poi GO+NO-GO PASS al
   tentativo 2 -> verifica che retry_context.directive nella spec
   inviata a L2 al tentativo 2 sia esattamente SWEEP_TIMEOUT_EARLY (non
   un numero grezzo, vedi retry_policy.py) e che il loop recuperi
   correttamente.
C. Feature senza gauge_check_mode nel preset (clearance_fit) -> /verify
   PASS basta, gauge-check MAI chiamato (preset non ancora esteso a
   questa feature, fuori scope M3).
D. gauge-check (GO) FAIL con lo stesso errore per 2 tentativi
   consecutivi -> uscita anticipata (stessa RetryBudget di M2, ora
   esercitata da un fallimento del gauge-check invece che di /verify);
   il NO-GO non viene mai raggiunto se il GO fallisce.
E. La spec inoltrata a /verify (via call_verifier) e' quella del
   tentativo corrente, non un dizionario vuoto — verifica il bug trovato
   e corretto in preparazione a questa milestone (vedi docstring di
   generate_and_verify.py, punto 1).
F. [M5, C2] GO PASS + NO-GO SENZA interferenza -> FAIL con errore stabile
   gauge_check_nogo_no_interference (foro sovradimensionato, mai
   rilevato dal solo GO prima di questo fix).
G. [M5, C2] GO PASS + NO-GO CON interferenza -> PASS (entrambi i calibri
   verificati, caso "felice" del fix).

Uso: python verify_gauge_check_loop_wiring.py
Nessuna dipendenza esterna (nessun cadquery/fastapi, solo stdlib +
retry_policy.py, gia' nella stessa directory).
"""

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import generate_and_verify as gv  # noqa: E402


class FakeCalls:
    """Coda di risposte pre-costruite per call_verifier/call_gauge_check,
    una per tentativo — e registro di tutte le chiamate fatte, per
    assertion sugli argomenti passati (spec inoltrata, part_step_path
    usato, ecc.)."""

    def __init__(self, verify_responses, gauge_responses=None):
        self.verify_responses = list(verify_responses)
        self.gauge_responses = list(gauge_responses or [])
        self.verify_calls = []  # (code, spec)
        self.gauge_calls = []  # (part_step_path, gauge_job)
        self.flowise_calls = []  # spec_json inviata a L2

    def call_flowise_l2(self, chatflow_id, spec_json, temperature=None):
        self.flowise_calls.append(spec_json)
        return "# codice finto, solo per testare il loop\nimport cadquery as cq\nresult = cq.Workplane('XY').box(1,1,1)\n"

    def call_verifier(self, code, spec=None):
        self.verify_calls.append((code, spec))
        return self.verify_responses.pop(0)

    def call_gauge_check(self, part_step_path, gauge_job):
        self.gauge_calls.append((part_step_path, gauge_job))
        return self.gauge_responses.pop(0)


def run_scenario(spec, verify_responses, gauge_responses, argv_spec=None):
    fake = FakeCalls(verify_responses, gauge_responses)
    orig = {
        "resolve_chatflow_id": gv.resolve_chatflow_id,
        "call_flowise_l2": gv.call_flowise_l2,
        "call_verifier": gv.call_verifier,
        "call_gauge_check": gv.call_gauge_check,
        "FLOWISE_API_KEY": gv.FLOWISE_API_KEY,
    }
    gv.resolve_chatflow_id = lambda strategy="free_code": "chatflow-test"
    gv.call_flowise_l2 = fake.call_flowise_l2
    gv.call_verifier = fake.call_verifier
    gv.call_gauge_check = fake.call_gauge_check
    gv.FLOWISE_API_KEY = "test-key"

    argv_backup = sys.argv
    sys.argv = ["generate_and_verify.py", json.dumps(argv_spec if argv_spec is not None else spec)]
    try:
        exit_code = gv.main()
    finally:
        sys.argv = argv_backup
        for k, v in orig.items():
            setattr(gv, k, v)

    return exit_code, fake


def verify_result(status, checks=None, dimensional_check=None, generated_part_step_path="job-x.step"):
    return {
        "status": status,
        "checks": checks or [{"name": "execution_and_geometry", "status": status, "detail": None}],
        "measurements": {"is_valid": True, "bbox_x_mm": 6.0, "bbox_y_mm": 6.0, "bbox_z_mm": 8.0},
        "dimensional_check": dimensional_check,
        "generated_part_step_path": generated_part_step_path if status == "PASS" else None,
    }


def gauge_result(status, mode="sweep", last_checkpoint=None):
    return {
        "status": status,
        "error": "gauge_check_timeout" if status == "TIMEOUT" else None,
        "gauge_check": {
            "status": status,
            "mode": mode,
            "interference_volume_mm3": 0.0 if status == "PASS" else None,
            "preflight_diagnostics": {"max_entity_tolerance_mm": 0.0001},
            "last_checkpoint": last_checkpoint,
        },
    }


THREAD_SPEC = {"feature": "thread", "nominal": "M6", "tolerance": 0.3}


def main():
    ok = True

    print("=== A. /verify PASS + GO PASS + NO-GO con interferenza al tentativo 1 ===")
    # [M5, C2] Da questa milestone il PASS richiede ANCHE il NO-GO
    # (deve interferire) — vedi Blocco F/G sotto per i due casi nuovi
    # dedicati al NO-GO; qui si aggiorna solo il numero di chiamate
    # attese per lo scenario "tutto liscio al tentativo 1".
    exit_code, fake = run_scenario(
        THREAD_SPEC,
        verify_responses=[verify_result("PASS")],
        gauge_responses=[gauge_result("PASS"), gauge_result("FAIL")],
    )
    a_ok = exit_code == 0 and len(fake.verify_calls) == 1 and len(fake.gauge_calls) == 2
    print("Atteso: successo immediato, 1 verify + 2 gauge-check (GO poi NO-GO):", "OK" if a_ok else "FALLITO", f"(exit={exit_code})")
    ok = ok and a_ok

    print("\n=== B. gauge-check (GO) TIMEOUT early al tentativo 1, recupero al tentativo 2 ===")
    exit_code, fake = run_scenario(
        THREAD_SPEC,
        verify_responses=[verify_result("PASS"), verify_result("PASS")],
        gauge_responses=[
            gauge_result("TIMEOUT", last_checkpoint={"step": 2, "total_steps": 20}),  # 2/20 = 0.1 < 0.33 -> EARLY
            gauge_result("PASS"),  # GO al tentativo 2
            gauge_result("FAIL"),  # NO-GO al tentativo 2 (deve interferire per il PASS finale)
        ],
    )
    directive_in_retry = None
    if len(fake.flowise_calls) >= 2:
        second_spec = json.loads(fake.flowise_calls[1])
        directive_in_retry = second_spec.get("retry_context", {}).get("directive")
    b_ok = exit_code == 0 and len(fake.gauge_calls) == 3 and directive_in_retry == "SWEEP_TIMEOUT_EARLY"
    print(
        f"Atteso: recupero al tentativo 2, directive=SWEEP_TIMEOUT_EARLY nella spec di retry (trovato: {directive_in_retry}):",
        "OK" if b_ok else "FALLITO",
    )
    ok = ok and b_ok

    print("\n=== C. feature senza gauge_check_mode (clearance_fit) -> gauge-check mai chiamato ===")
    exit_code, fake = run_scenario(
        {"feature": "clearance_fit", "nominal": "D8", "tolerance": 0.2},
        verify_responses=[verify_result("PASS")],
        gauge_responses=[],
    )
    c_ok = exit_code == 0 and len(fake.gauge_calls) == 0
    print("Atteso: PASS solo con /verify, 0 chiamate a gauge-check:", "OK" if c_ok else "FALLITO", f"(exit={exit_code})")
    ok = ok and c_ok

    print("\n=== D. gauge-check FAIL ripetuto (stesso errore) -> uscita anticipata ===")
    exit_code, fake = run_scenario(
        THREAD_SPEC,
        verify_responses=[verify_result("PASS"), verify_result("PASS"), verify_result("PASS")],
        gauge_responses=[gauge_result("FAIL"), gauge_result("FAIL"), gauge_result("FAIL")],
    )
    d_ok = exit_code == 1 and len(fake.gauge_calls) == 2  # uscita anticipata dopo 2 ripetizioni, MAI il 3o tentativo
    print(
        f"Atteso: uscita anticipata dopo 2 tentativi (non arriva al 3o), exit=1 (trovato gauge_calls={len(fake.gauge_calls)}):",
        "OK" if d_ok else "FALLITO",
    )
    ok = ok and d_ok

    print("\n=== F. [M5, C2] GO PASS + NO-GO senza interferenza -> FAIL (foro sovradimensionato mai rilevato dal solo GO) ===")
    exit_code, fake = run_scenario(
        THREAD_SPEC,
        verify_responses=[verify_result("PASS"), verify_result("PASS")],
        gauge_responses=[gauge_result("PASS"), gauge_result("PASS"), gauge_result("PASS"), gauge_result("PASS")],
    )
    nogo_step_used = fake.gauge_calls[1][1].get("gauge_step_path") if len(fake.gauge_calls) >= 2 else None
    f_ok = exit_code == 1 and len(fake.gauge_calls) == 4 and nogo_step_used == "thread_M6_NOGO_ISO68-1.step"
    print(
        f"Atteso: NO-GO chiamato dopo ogni GO PASS (4 chiamate: GO+NOGO x2 tentativi, poi uscita anticipata), "
        f"verdetto finale FAIL — PRIMA di M5 il NO-GO non era mai chiamato e questo caso dava PASS silenzioso al "
        f"tentativo 1 (1 sola chiamata gauge-check): {'OK' if f_ok else 'FALLITO'} "
        f"(exit={exit_code}, gauge_calls={len(fake.gauge_calls)}, nogo_step_used={nogo_step_used})"
    )
    ok = ok and f_ok

    print("\n=== G. [M5, C2] GO PASS + NO-GO con interferenza -> PASS (entrambi i calibri verificati) ===")
    exit_code, fake = run_scenario(
        THREAD_SPEC,
        verify_responses=[verify_result("PASS")],
        gauge_responses=[gauge_result("PASS"), gauge_result("FAIL")],
    )
    g_ok = exit_code == 0 and len(fake.gauge_calls) == 2 and fake.gauge_calls[1][1].get("gauge_step_path") == "thread_M6_NOGO_ISO68-1.step"
    print(
        f"Atteso: successo solo dopo GO PASS E NO-GO con interferenza (2 chiamate gauge-check, GO poi NOGO): "
        f"{'OK' if g_ok else 'FALLITO'} (exit={exit_code}, gauge_calls={len(fake.gauge_calls)})"
    )
    ok = ok and g_ok

    print("\n=== E. la spec inoltrata a /verify e' quella reale del tentativo, non vuota (bug corretto) ===")
    exit_code, fake = run_scenario(
        THREAD_SPEC,
        verify_responses=[verify_result("PASS")],
        gauge_responses=[gauge_result("PASS"), gauge_result("FAIL")],
    )
    _, sent_spec = fake.verify_calls[0]
    e_ok = (
        sent_spec is not None
        and sent_spec.get("feature") == "thread"
        and sent_spec.get("nominal") == "M6"
        and sent_spec.get("thread_standard") == "ISO 68-1"  # arricchito dal preset
    )
    print(f"Atteso: spec non nulla con feature/nominal/preset applicato (trovato: {sent_spec}):", "OK" if e_ok else "FALLITO")
    ok = ok and e_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
