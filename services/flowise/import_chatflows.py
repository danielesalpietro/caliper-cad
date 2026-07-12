"""
CALIPER — provisioning: importa in Flowise i chatflow versionati in
chatflows/, cosi' chi clona il repo li trova gia' pronti invece di
doverli ricostruire nodo per nodo dalla UI.

Idempotente: se un chatflow con lo stesso nome esiste gia', lo salta.
Se FLOWISE_API_KEY non e' impostata, esce con un messaggio informativo
(non un errore) — l'API key va generata una volta a mano nella UI di
Flowise (Settings -> API Keys) dopo il primo avvio, non puo' esistere
prima che un utente si sia autenticato la prima volta.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

FLOWISE_URL = os.getenv("FLOWISE_URL", "http://flowise:3000").rstrip("/")
API_KEY = os.getenv("FLOWISE_API_KEY", "").strip()
CHATFLOWS_DIR = os.path.join(os.path.dirname(__file__), "chatflows")
MANIFEST_PATH = os.path.join(CHATFLOWS_DIR, "manifest.json")


def api_request(method: str, path: str, body: dict | None = None):
    url = f"{FLOWISE_URL}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {API_KEY}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_for_flowise(retries: int = 20, delay: int = 3):
    for _ in range(retries):
        try:
            urllib.request.urlopen(f"{FLOWISE_URL}/api/v1/ping", timeout=5)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(delay)
    return False


def main():
    if not API_KEY:
        print(
            "FLOWISE_API_KEY non impostata — salto l'import automatico.\n"
            "Per abilitarlo: apri Flowise, Settings -> API Keys, crea una "
            "chiave, mettila in .env come FLOWISE_API_KEY, poi rilancia:\n"
            "  docker compose up flowise-init"
        )
        return 0

    if not wait_for_flowise():
        print(f"Flowise non raggiungibile su {FLOWISE_URL}, abort.")
        return 1

    if not os.path.isfile(MANIFEST_PATH):
        print(f"Nessun manifest.json trovato in {CHATFLOWS_DIR}, niente da importare.")
        return 0

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    try:
        existing = api_request("GET", "/api/v1/chatflows")
    except urllib.error.HTTPError as e:
        print(f"Impossibile leggere i chatflow esistenti (HTTP {e.code}) — API key valida?")
        return 1

    existing_names = {c["name"] for c in existing}

    for entry in manifest:
        name = entry["name"]
        if name in existing_names:
            print(f"'{name}' esiste gia', skip.")
            continue

        flow_path = os.path.join(CHATFLOWS_DIR, entry["file"])
        with open(flow_path, "r", encoding="utf-8") as f:
            flow_data = f.read()

        try:
            api_request(
                "POST",
                "/api/v1/chatflows",
                {"name": name, "flowData": flow_data, "type": "CHATFLOW"},
            )
            print(f"Importato: '{name}'")
        except urllib.error.HTTPError as e:
            print(f"Import fallito per '{name}': HTTP {e.code} - {e.read().decode('utf-8', 'ignore')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
