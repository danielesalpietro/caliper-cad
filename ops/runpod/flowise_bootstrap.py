#!/usr/bin/env python3
"""CALIPER — bootstrap automatico di Flowise 3.x (zero passaggi manuali).

Automatizza la catena che nel run0 era manuale (vedi
docs/logbook_runpod_run0.md): registrazione account admin, login, API
key, credential OpenAI, aggancio della credential ai chatflow L2.
Idempotente: rieseguibile a ogni boot, fa solo cio' che manca.

ENDPOINT VERIFICATI DAL SORGENTE flowise@3.1.4 (tarball npm, non
indovinati — file citati per provenienza):
  POST /api/v1/account/register  body {user:{name,email,credential}}
       (dist/enterprise/routes/account.route.js + account.service.js:
        il campo password si chiama 'credential')
  POST /api/v1/auth/login        body {email,password} -> JWT via cookie
       (dist/index.js app.post + passport/index.js usernameField:'email')
  POST /api/v1/apikey            body {keyName} [auth JWT]
       (dist/routes/apikey/index.js + controllers/apikey: body.keyName)
  GET/POST /api/v1/credentials   body {name,credentialName,plainDataObj}
       (dist/routes/credentials/index.js)
  GET/PUT  /api/v1/chatflows/:id (aggiorna flowData col credential id)

LIMITE DICHIARATO: flusso costruito sugli endpoint verificati a sorgente
ma NON ancora eseguito contro un'istanza viva al momento della scrittura
— la prima esecuzione reale (sul pod) e' la sua validazione; ogni step
logga richiesta/esito per diagnosi rapida.

Input (env): FLOWISE_URL (default http://localhost:3000),
  FLOWISE_USERNAME (email; default caliper-admin@caliper.local),
  FLOWISE_PASSWORD (obbligatoria), OPENAI_API_KEY (opzionale: senza, la
  credential/chatflow-patch viene saltata con warning).
Output: /workspace/.caliper_env aggiornato (chmod 600) con
  FLOWISE_API_KEY e FLOWISE_CREDENTIAL_ID_OPENAI; exit 0 se tutto ok.
"""
import http.cookiejar
import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("FLOWISE_URL", "http://localhost:3000").rstrip("/")
EMAIL = os.environ.get("FLOWISE_USERNAME", "caliper-admin@caliper.local")
PASSWORD = os.environ.get("FLOWISE_PASSWORD", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
ENV_FILE = os.environ.get("CALIPER_ENV_FILE", "/workspace/.caliper_env")
APIKEY_NAME = "caliper-orchestrator"
CREDENTIAL_NAME = "CALIPER-CAD"
L2_CHATFLOW_NAMES = (
    "CALIPER - L2 Generation (CadQuery)",
    "CALIPER - L2 Generation (Sketch-First)",
)

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def call(method, path, body=None, timeout=30):
    req = urllib.request.Request(f"{BASE}{path}", method=method)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with opener.open(req, data=data, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        return e.code, raw[:400]


def log(msg):
    print(f"[flowise-bootstrap] {msg}", flush=True)


def wait_for_flowise(max_seconds=300):
    for _ in range(max_seconds // 5):
        status, _ = call("GET", "/api/v1/ping")
        if status == 200:
            return True
        time.sleep(5)
    return False


def ensure_account_and_login():
    # Prima tenta il login (account gia' esistente, caso pod riavviato).
    status, resp = call("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD})
    if status == 200:
        log(f"login ok ({EMAIL})")
        return True
    log(f"login fallito ({status}) — provo la registrazione (istanza vuota?)")
    status, resp = call(
        "POST",
        "/api/v1/account/register",
        {"user": {"name": "CALIPER Admin", "email": EMAIL, "credential": PASSWORD}},
    )
    log(f"register -> {status}")
    if status not in (200, 201):
        log(f"register fallita: {resp}")
        return False
    status, resp = call("POST", "/api/v1/auth/login", {"email": EMAIL, "password": PASSWORD})
    if status == 200:
        log("login ok dopo registrazione")
        return True
    log(f"login post-registrazione fallito ({status}): {resp}")
    return False


def ensure_api_key():
    status, keys = call("GET", "/api/v1/apikey")
    if status == 200 and isinstance(keys, list):
        for k in keys:
            if k.get("keyName") == APIKEY_NAME and k.get("apiKey"):
                log(f"api key '{APIKEY_NAME}' gia' presente")
                return k["apiKey"]
    status, created = call("POST", "/api/v1/apikey", {"keyName": APIKEY_NAME})
    if status in (200, 201):
        # la risposta puo' essere l'oggetto o la lista aggiornata
        items = created if isinstance(created, list) else [created]
        for k in items:
            if isinstance(k, dict) and k.get("keyName") == APIKEY_NAME and k.get("apiKey"):
                log(f"api key '{APIKEY_NAME}' creata")
                return k["apiKey"]
    log(f"creazione api key fallita ({status}): {created}")
    return None


def ensure_openai_credential():
    if not OPENAI_KEY:
        log("OPENAI_API_KEY assente — salto credential e patch chatflow (E2E-1/2 resteranno bloccati)")
        return None
    status, creds = call("GET", "/api/v1/credentials?credentialName=openAIApi")
    if status == 200 and isinstance(creds, list):
        for c in creds:
            if c.get("name") == CREDENTIAL_NAME:
                log(f"credential '{CREDENTIAL_NAME}' gia' presente ({c['id']})")
                return c["id"]
    status, created = call(
        "POST",
        "/api/v1/credentials",
        {"name": CREDENTIAL_NAME, "credentialName": "openAIApi",
         "plainDataObj": {"openAIApiKey": OPENAI_KEY}},
    )
    if status in (200, 201) and isinstance(created, dict) and created.get("id"):
        log(f"credential '{CREDENTIAL_NAME}' creata ({created['id']})")
        return created["id"]
    log(f"creazione credential fallita ({status}): {created}")
    return None


def patch_chatflows_credential(credential_id):
    status, flows = call("GET", "/api/v1/chatflows")
    if status != 200 or not isinstance(flows, list):
        log(f"lettura chatflows fallita ({status})")
        return False
    ok = True
    for flow in flows:
        if flow.get("name") not in L2_CHATFLOW_NAMES:
            continue
        try:
            flow_data = json.loads(flow["flowData"])
            changed = False
            for node in flow_data.get("nodes", []):
                if node.get("data", {}).get("name") == "chatOpenAI":
                    if node["data"].get("credential") != credential_id:
                        node["data"]["credential"] = credential_id
                        changed = True
                    inputs = node["data"].get("inputs")
                    if isinstance(inputs, dict) and inputs.get("credential") != credential_id:
                        inputs["credential"] = credential_id
                        changed = True
            if not changed:
                log(f"chatflow '{flow['name']}': credential gia' corretta")
                continue
            status, resp = call("PUT", f"/api/v1/chatflows/{flow['id']}",
                                {"flowData": json.dumps(flow_data)})
            if status == 200:
                log(f"chatflow '{flow['name']}': credential agganciata")
            else:
                log(f"chatflow '{flow['name']}': PUT fallita ({status}): {resp}")
                ok = False
        except (KeyError, json.JSONDecodeError) as e:
            log(f"chatflow '{flow.get('name')}': flowData non parsabile: {e}")
            ok = False
    return ok


def update_env_file(entries):
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            lines = f.read().splitlines()
    for key, value in entries.items():
        if value is None:
            continue
        pattern = re.compile(rf"^(export\s+)?{re.escape(key)}=")
        lines = [l for l in lines if not pattern.match(l)]
        lines.append(f"export {key}='{value}'")
    with open(ENV_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(ENV_FILE, stat.S_IRUSR | stat.S_IWUSR)


def main():
    if not PASSWORD:
        log("FLOWISE_PASSWORD assente — impossibile procedere (impostala nel template o in /workspace/.caliper_env)")
        return 1
    if not wait_for_flowise():
        log("Flowise non risponde su /api/v1/ping entro il timeout")
        return 1
    if not ensure_account_and_login():
        return 1
    api_key = ensure_api_key()
    credential_id = ensure_openai_credential()
    if credential_id:
        patch_chatflows_credential(credential_id)
    update_env_file({
        "FLOWISE_API_KEY": api_key,
        "FLOWISE_CREDENTIAL_ID_OPENAI": credential_id,
        "FLOWISE_USERNAME": EMAIL,
        "FLOWISE_PASSWORD": PASSWORD,
    })
    log(f"env aggiornato: {ENV_FILE}")
    if api_key is None:
        return 1
    log("bootstrap completato")
    return 0


if __name__ == "__main__":
    sys.exit(main())
