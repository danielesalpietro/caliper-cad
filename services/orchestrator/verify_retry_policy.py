"""
CALIPER — verifica manuale di retry_policy.py e del loop di retry in
generate_and_verify.py (M2).

Stesso stile degli script verify_gauge_check*.py in
services/verifier/executor/: eseguibile a mano, output incollato come
prova diretta. Qui non serve cadquery — solo la logica di
classificazione/budget, testabile senza un'istanza Flowise/verifier
viva (nessuna delle due e' disponibile in questo sandbox — vedi
docs/handoff_m2.md).

Copre:
1. classify_checkpoint(): le quattro soglie (SWEEP_TIMEOUT_EARLY/LATE,
   TOPOLOGY_TOLERANCE_ANOMALY, RETRY_GENERIC), inclusi i casi limite
   (nessun checkpoint, nessuna soglia superata).
2. RetryBudget: uscita anticipata su 2 ripetizioni consecutive dello
   stesso errore, log strutturato per tentativo (case_id/attempt/
   directive_used/outcome).
3. main() di generate_and_verify.py con call_flowise_l2/call_verifier
   MOCKATE (nessuna rete reale) — tre scenari: FAIL poi PASS al secondo
   tentativo, budget esaurito dopo 3 FAIL diversi, uscita anticipata su
   2 FAIL consecutivi con lo stesso errore.

[M3] Le fake_verify* qui sotto ora accettano il parametro 'spec' (kwarg,
ignorato) — call_verifier() lo manda davvero da M3 in poi (bug corretto,
vedi docs/logbook_fase3.md e generate_and_verify.py), senza cambiare
cosa questo script verifica (loop di retry, feature "other" senza
preset -> nessun gauge-check coinvolto qui, vedi
verify_gauge_check_loop_wiring.py per quella parte).

Uso: python verify_retry_policy.py
"""

import importlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))


def test_classify_checkpoint():
    from retry_policy import classify_checkpoint

    ok = True

    r = classify_checkpoint(None)
    print("Nessun gauge_check (FAIL semplice, es. sintassi/dimensionale):", r)
    ok = ok and r == "RETRY_GENERIC"

    r = classify_checkpoint({"preflight_diagnostics": {}, "last_checkpoint": None})
    print("gauge_check senza last_checkpoint ne' tolleranza anomala:", r)
    ok = ok and r == "RETRY_GENERIC"

    r = classify_checkpoint({"last_checkpoint": {"step": 2, "total_steps": 21}})
    print("step 2/21 (~0.095, < 0.33):", r)
    ok = ok and r == "SWEEP_TIMEOUT_EARLY"

    r = classify_checkpoint({"last_checkpoint": {"step": 15, "total_steps": 21}})
    print("step 15/21 (~0.71, >= 0.33):", r)
    ok = ok and r == "SWEEP_TIMEOUT_LATE"

    r = classify_checkpoint({"preflight_diagnostics": {"max_entity_tolerance_mm": 0.2}, "last_checkpoint": None})
    print("max_entity_tolerance_mm=0.2 (> soglia 0.05):", r)
    ok = ok and r == "TOPOLOGY_TOLERANCE_ANOMALY"

    # priorita': tolleranza anomala vince anche se c'e' un checkpoint di sweep
    r = classify_checkpoint(
        {"preflight_diagnostics": {"max_entity_tolerance_mm": 0.2}, "last_checkpoint": {"step": 15, "total_steps": 21}}
    )
    print("tolleranza anomala + checkpoint sweep (deve vincere la tolleranza):", r)
    ok = ok and r == "TOPOLOGY_TOLERANCE_ANOMALY"

    print("=== classify_checkpoint:", "OK" if ok else "FALLITO", "===\n")
    return ok


def test_retry_budget_early_exit(log_path):
    from retry_policy import RetryBudget

    ok = True
    budget = RetryBudget(case_id="case-early-exit")

    budget.record_attempt(1, directive_used="SWEEP_TIMEOUT_EARLY", outcome="FAIL", outcome_error="gauge_check_timeout")
    print("Dopo tentativo 1 (FAIL, SWEEP_TIMEOUT_EARLY): should_stop_early =", budget.should_stop_early())
    ok = ok and budget.should_stop_early() is False

    budget.record_attempt(2, directive_used="SWEEP_TIMEOUT_EARLY", outcome="FAIL", outcome_error="gauge_check_timeout")
    print("Dopo tentativo 2 (stesso directive/errore): should_stop_early =", budget.should_stop_early())
    ok = ok and budget.should_stop_early() is True
    ok = ok and budget.history[-1]["same_error_as_previous"] is True

    with open(log_path, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    logged = [r for r in lines if r["case_id"] == "case-early-exit"]
    print(f"Record loggati su file per case-early-exit: {len(logged)} (attesi 2)")
    ok = ok and len(logged) == 2

    print("=== RetryBudget (uscita anticipata + log su file):", "OK" if ok else "FALLITO", "===\n")
    return ok


def test_retry_budget_no_early_exit_on_different_errors():
    from retry_policy import RetryBudget

    budget = RetryBudget(case_id="case-different-errors")
    budget.record_attempt(1, directive_used="SWEEP_TIMEOUT_EARLY", outcome="FAIL", outcome_error="err_a")
    budget.record_attempt(2, directive_used="SWEEP_TIMEOUT_LATE", outcome="FAIL", outcome_error="err_b")
    ok = budget.should_stop_early() is False
    print("Due FAIL con directive/errore DIVERSI: should_stop_early =", budget.should_stop_early())
    print("=== RetryBudget (nessuna uscita anticipata su errori diversi):", "OK" if ok else "FALLITO", "===\n")
    return ok


class _FakeHTTPError(Exception):
    pass


def test_main_loop(monkeypatch_module):
    """Mocka call_flowise_l2/call_verifier/resolve_chatflow_id dentro
    generate_and_verify e testa main() end-to-end senza rete reale."""
    os.environ["FLOWISE_API_KEY"] = "fake-key-for-test"
    gav = importlib.import_module("generate_and_verify")
    importlib.reload(gav)

    ok = True

    def fail_result(detail):
        return {
            "status": "FAIL",
            "checks": [
                {"name": "python_syntax", "status": "PASS", "detail": None},
                {"name": "cadquery_import", "status": "PASS", "detail": None},
                {"name": "result_variable", "status": "PASS", "detail": None},
                {"name": "execution_and_geometry", "status": "FAIL", "detail": detail},
            ],
        }

    pass_result = {"status": "PASS", "checks": [{"name": "execution_and_geometry", "status": "PASS", "detail": None}]}

    # --- Scenario 1: FAIL al tentativo 1, PASS al tentativo 2 ---
    calls = {"flowise": 0, "verify": 0}

    def fake_flowise(chatflow_id, spec_json, temperature=None):
        calls["flowise"] += 1
        return f"# fake code, attempt {calls['flowise']}"

    def fake_verify(code, spec=None):
        calls["verify"] += 1
        return fail_result("dimensional_check fuori tolleranza") if calls["verify"] == 1 else pass_result

    gav.resolve_chatflow_id = lambda strategy="free_code": "fake-chatflow-id"
    gav.call_flowise_l2 = fake_flowise
    gav.call_verifier = fake_verify
    sys.argv = ["generate_and_verify.py", '{"feature": "other"}']
    rc = gav.main()
    print(f"\nScenario 1 (FAIL poi PASS): return code={rc} (atteso 0), tentativi flowise={calls['flowise']} (atteso 2)")
    ok = ok and rc == 0 and calls["flowise"] == 2

    # --- Scenario 2: budget esaurito, 3 errori DIVERSI (nessuna uscita anticipata) ---
    calls2 = {"flowise": 0, "verify": 0}
    errors = ["errore_1", "errore_2", "errore_3"]

    def fake_flowise2(chatflow_id, spec_json, temperature=None):
        calls2["flowise"] += 1
        return f"# fake code, attempt {calls2['flowise']}"

    def fake_verify2(code, spec=None):
        i = calls2["verify"]
        calls2["verify"] += 1
        return fail_result(errors[i])

    gav.call_flowise_l2 = fake_flowise2
    gav.call_verifier = fake_verify2
    rc2 = gav.main()
    print(f"Scenario 2 (3 errori diversi, budget esaurito): return code={rc2} (atteso 1), tentativi={calls2['flowise']} (atteso 3)")
    ok = ok and rc2 == 1 and calls2["flowise"] == 3

    # --- Scenario 3: uscita anticipata, stesso errore 2 volte consecutive ---
    calls3 = {"flowise": 0}

    def fake_flowise3(chatflow_id, spec_json, temperature=None):
        calls3["flowise"] += 1
        return f"# fake code, attempt {calls3['flowise']}"

    def fake_verify3(code, spec=None):
        return fail_result("stesso_errore_sempre")

    gav.call_flowise_l2 = fake_flowise3
    gav.call_verifier = fake_verify3
    rc3 = gav.main()
    print(f"Scenario 3 (stesso errore 2 volte, uscita anticipata): return code={rc3} (atteso 1), tentativi={calls3['flowise']} (attesi 2, non 3)")
    ok = ok and rc3 == 1 and calls3["flowise"] == 2

    print("\n=== main() con retry (mock, nessuna rete reale):", "OK" if ok else "FALLITO", "===\n")
    return ok


def main():
    ok = True

    with tempfile.TemporaryDirectory() as tmp:
        log_path = os.path.join(tmp, "retry_log.jsonl")
        os.environ["RETRY_LOG_PATH"] = log_path

        ok = test_classify_checkpoint() and ok
        ok = test_retry_budget_early_exit(log_path) and ok
        ok = test_retry_budget_no_early_exit_on_different_errors() and ok
        ok = test_main_loop(None) and ok

    print("=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
