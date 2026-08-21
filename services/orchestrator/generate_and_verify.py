"""
CALIPER — orchestratore Livello 2 -> Livello 3 (genera, poi verifica)
------------------------------------------------------------------------
Script esterno, non un nodo Flowise (Rischio #9): niente Agent, niente
Custom Tool dentro il canvas — quei nodi hanno bug documentati
sull'interpolazione delle variabili (issue FlowiseAI/Flowise #4470,
#5150), stessa categoria dei bug gia' incontrati (ReActAgent,
ChatOllama). Questo script chiama l'API REST di Flowise per il Chatflow
L2 (generazione), poi il verifier (Livello 3) sul codice ottenuto.

Retry automatico (M2, vedi docs/logbook_fase2.md e
services/orchestrator/retry_policy.py): fino a MAX_RETRY_ATTEMPTS
tentativi, con uscita anticipata se lo stesso errore si ripete su 2
tentativi consecutivi. La Policy di retry in
docs/architettura-prototipo-mesh-llm.md richiede "variazione tra un
tentativo e l'altro, non semplice ripetizione" — qui realizzata in due
modi indipendenti: (1) un "retry_context" con un enunciato canned,
scritto da un umano (mai composto dal modello, mai numeri grezzi — vedi
retry_policy.py) iniettato nella spec inviata a L2; (2) temperatura
crescente via overrideConfig sulla chiamata Flowise (vedi
call_flowise_l2). **Riserva onesta:** (2) presuppone che il nodo
ChatOpenAI del Chatflow L2 sia configurato per accettare un override di
temperatura per-richiesta — non verificato in questa sessione (nessuna
istanza Flowise viva nel sandbox, stesso limite gia' incontrato per
Docker in M1) — se il nodo ignora overrideConfig, resta comunque attiva
la variazione (1). Idem per "il prompt legge SOLO directive_text" (vedi
docs/logbook_fase2.md): dipende dal template del Chatflow L2 lato
Flowise, non modificato in questa sessione (fuori scope, vedi
services/flowise/chatflows/).

Prima di chiamare L2, arricchisce la specifica con il preset della
feature (presets.json) — es. per "thread" aggiunge angolo del profilo
e norma di riferimento, che lo schema L2.5 da solo non contiene. Vedi
Rischio in docs/architettura-prototipo-mesh-llm.md: senza questo, L2
non ha la geometria minima per costruire un profilo reale e si ferma
(comportamento corretto del modello, non un bug — ma bloccante).

Uso:
    python generate_and_verify.py '{"feature": "thread", "nominal": "M6", ...}'

Variabili d'ambiente:
    FLOWISE_URL       default http://localhost:3000
    VERIFIER_URL      default http://localhost:8600
    FLOWISE_API_KEY   obbligatoria
    L2_CHATFLOW_NAME  default "CALIPER - L2 Generation (CadQuery)"
    L2_CHATFLOW_ID    se impostata, salta la ricerca per nome
"""

import json
import os
import sys
import urllib.error
import urllib.request

from retry_policy import MAX_RETRY_ATTEMPTS, RetryBudget, classify_checkpoint, directive_text_for

PRESETS_PATH = os.path.join(os.path.dirname(__file__), "presets.json")

FLOWISE_URL = os.getenv("FLOWISE_URL", "http://localhost:3000").rstrip("/")
VERIFIER_URL = os.getenv("VERIFIER_URL", "http://localhost:8600").rstrip("/")
FLOWISE_API_KEY = os.getenv("FLOWISE_API_KEY", "").strip()
L2_CHATFLOW_NAME = os.getenv("L2_CHATFLOW_NAME", "CALIPER - L2 Generation (CadQuery)")
L2_CHATFLOW_ID = os.getenv("L2_CHATFLOW_ID", "").strip()

# Temperatura per tentativo (attempt 1-indexed) — variazione (2) descritta
# sopra. Valori indicativi, non calibrati su un batch reale (nessuna
# istanza Flowise viva per misurarne l'effetto in questa sessione).
RETRY_TEMPERATURES = [0.0, 0.3, 0.6]


def flowise_get(path: str):
    req = urllib.request.Request(f"{FLOWISE_URL}{path}", method="GET")
    req.add_header("Authorization", f"Bearer {FLOWISE_API_KEY}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def apply_preset(spec: dict) -> dict:
    with open(PRESETS_PATH, "r", encoding="utf-8") as f:
        presets = json.load(f)

    feature = spec.get("feature", "")
    preset = presets.get(feature)
    if not preset or not preset.get("defined"):
        return spec

    enriched = dict(spec)
    if "standard" in preset:
        enriched.setdefault("thread_standard", preset["standard"])
    if "profile_angle_deg" in preset:
        enriched.setdefault("thread_profile_angle_deg", preset["profile_angle_deg"])
    if not enriched.get("tolerance_type") and "default_tolerance_type" in preset:
        enriched["tolerance_type"] = preset["default_tolerance_type"]
    return enriched


def resolve_chatflow_id() -> str:
    if L2_CHATFLOW_ID:
        return L2_CHATFLOW_ID
    chatflows = flowise_get("/api/v1/chatflows")
    for c in chatflows:
        if c["name"] == L2_CHATFLOW_NAME:
            return c["id"]
    raise RuntimeError(
        f"Nessun chatflow chiamato '{L2_CHATFLOW_NAME}' trovato. "
        f"Imposta L2_CHATFLOW_ID esplicitamente, o controlla il nome."
    )


def call_flowise_l2(chatflow_id: str, spec_json: str, temperature: float | None = None) -> str:
    body = {"question": spec_json}
    if temperature is not None:
        # Variazione (2), vedi docstring del modulo — riserva onesta: non
        # verificato che il nodo ChatOpenAI del Chatflow L2 legga questo
        # override (nessuna istanza Flowise viva in questa sessione).
        body["overrideConfig"] = {"temperature": temperature}
    req = urllib.request.Request(
        f"{FLOWISE_URL}/api/v1/prediction/{chatflow_id}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {FLOWISE_API_KEY}")
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("text", "")


def call_verifier(code: str) -> dict:
    req = urllib.request.Request(
        f"{VERIFIER_URL}/verify",
        data=json.dumps({"code": code}).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def failure_error_string(result: dict) -> str | None:
    """Estrae una stringa d'errore stabile da un risultato /verify FAIL,
    per il confronto 'same_error_as_previous' di RetryBudget."""
    exec_check = next((c for c in result.get("checks", []) if c["name"] == "execution_and_geometry"), None)
    if exec_check and exec_check.get("detail"):
        return exec_check["detail"]
    failed = [c for c in result.get("checks", []) if c["status"] == "FAIL"]
    if failed:
        return failed[0]["name"]
    return "verify_fail_unknown"


def main():
    if len(sys.argv) < 2:
        print("Uso: python generate_and_verify.py '<spec JSON L2.5>'")
        return 1
    if not FLOWISE_API_KEY:
        print("FLOWISE_API_KEY non impostata.")
        return 1

    spec = json.loads(sys.argv[1])
    enriched_spec = apply_preset(spec)
    if enriched_spec != spec:
        print("-> Preset applicato:")
        print(json.dumps(enriched_spec, indent=2, ensure_ascii=False))

    try:
        chatflow_id = resolve_chatflow_id()
    except (RuntimeError, urllib.error.HTTPError) as e:
        print(f"Impossibile risolvere il Chatflow L2: {e}")
        return 1
    print(f"-> Chatflow L2: {chatflow_id}")

    budget = RetryBudget()
    print(f"-> case_id: {budget.case_id}")

    directive, directive_text = None, None
    previous_error = None
    result = None

    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        attempt_spec = dict(enriched_spec)
        if attempt > 1:
            # Vedi docstring del modulo: 'retry_context' e' la variazione
            # (1), sempre indipendente dalla temperatura crescente (2) —
            # entrambe attive ad ogni retry, mai una sostituisce l'altra.
            attempt_spec["retry_context"] = {
                "attempt": attempt,
                "previous_error": previous_error,
                "directive": directive,
                "directive_text": directive_text,
            }
        spec_json = json.dumps(attempt_spec, ensure_ascii=False)
        temperature = RETRY_TEMPERATURES[min(attempt - 1, len(RETRY_TEMPERATURES) - 1)]

        print(f"\n=== Tentativo {attempt}/{MAX_RETRY_ATTEMPTS} (temperature={temperature}) ===")
        if attempt > 1:
            print(f"-> directive: {directive} — {directive_text}")

        print("-> Genero il codice (Livello 2)...")
        try:
            code = call_flowise_l2(chatflow_id, spec_json, temperature=temperature)
        except urllib.error.HTTPError as e:
            print(f"Generazione fallita: HTTP {e.code} - {e.read().decode('utf-8', 'ignore')}")
            return 1

        print("\n--- Codice generato ---")
        print(code)

        print("\n-> Verifico (Livello 3)...")
        result = call_verifier(code)
        print("\n--- Esito verifica ---")
        print(json.dumps(result, indent=2, ensure_ascii=False))

        if result["status"] == "PASS":
            budget.record_attempt(attempt, directive_used=directive, outcome="PASS", outcome_error=None)
            print(f"\n=== PASS al tentativo {attempt}/{MAX_RETRY_ATTEMPTS} ===")
            return 0

        # FAIL: classifica per il PROSSIMO tentativo. gauge_check e'
        # popolato solo se questo loop finisce per chiamare /gauge-check
        # in futuro (M3, non ancora integrato qui, vedi
        # docs/handoff_m2.md) — oggi ricade sempre su RETRY_GENERIC,
        # comportamento corretto e non silenzioso (vedi retry_policy.py).
        outcome_error = failure_error_string(result)
        directive = classify_checkpoint(result.get("gauge_check"))
        directive_text = directive_text_for(directive)
        record = budget.record_attempt(attempt, directive_used=directive, outcome="FAIL", outcome_error=outcome_error)
        previous_error = outcome_error

        if budget.should_stop_early():
            print(
                f"\n=== Uscita anticipata: stesso errore/directive ripetuto "
                f"{2} volte consecutive ({outcome_error}) ==="
            )
            break
        if attempt == MAX_RETRY_ATTEMPTS:
            print(f"\n=== Budget di retry esaurito ({MAX_RETRY_ATTEMPTS} tentativi) ===")
            break

    print(f"\n=== final_status: unrecoverable_virtual (case_id={budget.case_id}, source=virtual) ===")
    print("-> Richiede intervento umano (fallback gia' previsto per la Fase A).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
