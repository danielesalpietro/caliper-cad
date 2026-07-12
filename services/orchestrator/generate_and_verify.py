"""
CALIPER — orchestratore Livello 2 -> Livello 3 (genera, poi verifica)
------------------------------------------------------------------------
Script esterno, non un nodo Flowise (Rischio #9): niente Agent, niente
Custom Tool dentro il canvas — quei nodi hanno bug documentati
sull'interpolazione delle variabili (issue FlowiseAI/Flowise #4470,
#5150), stessa categoria dei bug gia' incontrati (ReActAgent,
ChatOllama). Questo script chiama l'API REST di Flowise per il Chatflow
L2 (generazione), poi il verifier (Livello 3) sul codice ottenuto.

Un solo passaggio per ora — NESSUN retry automatico. La Policy di retry
in docs/architettura-prototipo-mesh-llm.md richiede "variazione tra un
tentativo e l'altro, non semplice ripetizione": con il nodo ChatOpenAI
di L2 impostato a temperature=0, ripetere la stessa chiamata
produrrebbe lo stesso identico output — la strategia di variazione
(temperatura crescente? feedback del verifier iniettato nel prompt?)
resta una decisione aperta, non presa qui.

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

PRESETS_PATH = os.path.join(os.path.dirname(__file__), "presets.json")

FLOWISE_URL = os.getenv("FLOWISE_URL", "http://localhost:3000").rstrip("/")
VERIFIER_URL = os.getenv("VERIFIER_URL", "http://localhost:8600").rstrip("/")
FLOWISE_API_KEY = os.getenv("FLOWISE_API_KEY", "").strip()
L2_CHATFLOW_NAME = os.getenv("L2_CHATFLOW_NAME", "CALIPER - L2 Generation (CadQuery)")
L2_CHATFLOW_ID = os.getenv("L2_CHATFLOW_ID", "").strip()


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


def call_flowise_l2(chatflow_id: str, spec_json: str) -> str:
    req = urllib.request.Request(
        f"{FLOWISE_URL}/api/v1/prediction/{chatflow_id}",
        data=json.dumps({"question": spec_json}).encode("utf-8"),
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
    spec_json = json.dumps(enriched_spec, ensure_ascii=False)

    try:
        chatflow_id = resolve_chatflow_id()
    except (RuntimeError, urllib.error.HTTPError) as e:
        print(f"Impossibile risolvere il Chatflow L2: {e}")
        return 1

    print(f"-> Chatflow L2: {chatflow_id}")
    print("-> Genero il codice (Livello 2)...")
    try:
        code = call_flowise_l2(chatflow_id, spec_json)
    except urllib.error.HTTPError as e:
        print(f"Generazione fallita: HTTP {e.code} - {e.read().decode('utf-8', 'ignore')}")
        return 1

    print("\n--- Codice generato ---")
    print(code)

    print("\n-> Verifico (Livello 3)...")
    result = call_verifier(code)

    print("\n--- Esito verifica ---")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
