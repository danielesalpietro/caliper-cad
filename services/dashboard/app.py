"""
CALIPER — dashboard: panoramica read-only dello stack locale.
------------------------------------------------------------------------
Non e' piu' una pagina nginx puramente statica come la vecchia
services/landing/dashboard.html: questo servizio fa controlli di stato
lato server verso gli altri container (evita CORS — Ollama/Qdrant/Flowise
non lo abilitano di default — e rispetta il principio "un servizio non
chiama un altro senza un motivo reale") e legge i log tramite
docker-socket-proxy, mai toccando /var/run/docker.sock direttamente.

Nessun controllo di scrittura: il proxy espone solo GET su /containers
(lista, inspect, logs) — POST=0 nel suo container blocca ogni azione
(start/stop/restart/exec/create/delete), anche se questo servizio
provasse a chiamarla. Non esistono endpoint per farlo: e' una scelta
esplicita, non solo un'omissione — vedi la voce di rischio corrispondente
in docs/architettura-prototipo-mesh-llm.md.

Eccezione di rete: a differenza degli altri servizi su caliper-public,
questo e' multi-homed (caliper-ai + caliper-public) proprio perche' deve
raggiungere sia gli altri container che il proxy Docker.

[Prompt to Part] /api/normalize e /api/generate: la pagina statica
static/prompt-to-part.html chiama la pipeline vera da qui, stesso
principio "un servizio non chiama un altro senza un motivo reale" gia'
in uso sopra. /api/normalize chiama il chatflow L2.5 (Ollama, gratis).
/api/generate rilancia services/orchestrator/generate_and_verify.py
(montato read-only, MAI riscritto qui: e' il codice gia' validato dalla
suite TC-E2E, non c'e' motivo di duplicarne la logica) come subprocess
e ne fa il parsing dell'output — poi converte lo STEP prodotto in STL
(cadquery, stesso ruolo di verifier-executor) per il viewer. NESSUN
controllo di accesso qui (pagina pubblica, nessun limite di frequenza):
scelta esplicita dell'utente per accelerare, tracciata in issue #35 —
non un'omissione.
"""

import asyncio
import base64
import json
import os
import re
import struct
import subprocess
import tempfile

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="CALIPER — dashboard")

ORCHESTRATOR_DIR = "/orchestrator"
EXEC_PARTS_DIR = "/exec/parts"
GENERATE_TIMEOUT_SECONDS = 180

DOCKER_PROXY_URL = os.environ.get("DOCKER_PROXY_URL", "http://docker-socket-proxy:2375")
HEALTH_TIMEOUT_SECONDS = 1.5
LOGS_TIMEOUT_SECONDS = 5.0
LOGS_TAIL_LINES = "300"

# Ordine intenzionale: livello di pipeline piu' basso (L1) in cima, verso
# il basso via via i livelli piu' alti (vedi diagramma ASCII in
# docs/architettura-prototipo-mesh-llm.md).
SERVICES = [
    {
        "id": "flowise",
        "container": "caliper-flowise",
        "name": "Flowise",
        "level": "L1 / L2.5 / L2",
        "description": "motore di esecuzione — input, normalizzazione specifica, generazione",
        "health_url": "http://flowise:3000/api/v1/ping",
        "open_url_env": "FLOWISE_PORT",
        "open_default_port": 3000,
        "open_path": "",
    },
    {
        "id": "verifier",
        "container": "caliper-verifier",
        "name": "Verifier",
        "level": "L3",
        "description": "verificatore deterministico — controlli statici + esecuzione/misura",
        "health_url": "http://verifier:8600/health",
        "open_fixed_port": 8600,
        "open_path": "/health",
    },
    {
        "id": "verifier-executor",
        "container": "caliper-verifier-executor",
        "name": "Verifier executor",
        "level": "L3",
        "description": "esecutore isolato (network_mode: none) — nessuna porta pubblicata",
        "health_url": None,
    },
    {
        "id": "prusaslicer",
        "container": "caliper-prusaslicer",
        "name": "PrusaSlicer",
        "level": "L4",
        "description": "slicing — CLI on-demand (profilo 'tools'), non un servizio long-running",
        "health_url": None,
        "on_demand": True,
    },
    {
        "id": "stream-agent",
        "container": "caliper-stream-agent",
        "name": "Stream agent",
        "level": "L7",
        "description": "grounding agent — indicizza il dataset congelato",
        "health_url": "http://stream-agent:8500/health",
        "open_fixed_port": 8500,
        "open_path": "/health",
    },
    {
        "id": "qdrant",
        "container": "caliper-qdrant",
        "name": "Qdrant",
        "level": "L7",
        "description": "vector store",
        "health_url": "http://qdrant:6333/healthz",
        "open_fixed_port": 6333,
        "open_path": "/dashboard",
    },
    {
        "id": "ollama",
        "container": "caliper-ollama",
        "name": "Ollama",
        "level": "L7",
        "description": "modelli locali (embedding + chat)",
        "health_url": "http://ollama:11434/",
        "open_fixed_port": 11434,
        "open_path": "/",
    },
    {
        "id": "open-webui",
        "container": "caliper-open-webui",
        "name": "Open WebUI",
        "level": "consumo",
        "description": "chat — interroga il Livello 7",
        "health_url": "http://open-webui:8080/health",
        "open_url_env": "OPEN_WEBUI_PORT",
        "open_default_port": 3010,
        "open_path": "",
    },
]

CONTAINER_BY_ID = {s["id"]: s["container"] for s in SERVICES}


def compute_open_url(svc: dict) -> str | None:
    if svc.get("open_fixed_port"):
        port = svc["open_fixed_port"]
    elif svc.get("open_url_env"):
        port = os.environ.get(svc["open_url_env"]) or svc.get("open_default_port")
    else:
        return None
    if not port:
        return None
    return f"http://localhost:{port}{svc.get('open_path', '')}"


async def check_status(svc: dict, client: httpx.AsyncClient) -> str:
    if svc.get("health_url"):
        try:
            r = await client.get(svc["health_url"], timeout=HEALTH_TIMEOUT_SECONDS)
            return "up" if r.status_code < 400 else "down"
        except (httpx.HTTPError, OSError):
            return "down"
    # Nessun endpoint HTTP (verifier-executor, prusaslicer): unico segnale
    # disponibile e' lo stato del container, letto in sola lettura dal
    # proxy Docker (mai da questo servizio direttamente).
    try:
        r = await client.get(
            f"{DOCKER_PROXY_URL}/containers/{svc['container']}/json",
            timeout=HEALTH_TIMEOUT_SECONDS,
        )
        if r.status_code == 404:
            return "not-found"
        return "up" if r.json().get("State", {}).get("Running") else "down"
    except (httpx.HTTPError, OSError, ValueError):
        return "unknown"


def demux_docker_logs(raw: bytes) -> str:
    """Il Docker Engine API multipla stdout/stderr in frame con un header
    di 8 byte (tipo stream + dimensione) quando il container non ha un
    tty allocato — il caso di tutti i servizi in docker-compose.yml qui.
    Se il formato non torna (container con tty, o payload inatteso),
    ritorna il contenuto grezzo invece di alzare un errore: e' un
    visualizzatore di diagnostica, non deve rompersi su un edge case."""
    out = []
    i, n = 0, len(raw)
    try:
        while i + 8 <= n:
            size = struct.unpack(">I", raw[i + 4 : i + 8])[0]
            i += 8
            chunk = raw[i : i + size]
            if len(chunk) != size:
                raise ValueError("frame troncato")
            i += size
            out.append(chunk.decode("utf-8", errors="replace"))
    except (struct.error, ValueError):
        return raw.decode("utf-8", errors="replace")
    if i != n and not out:
        return raw.decode("utf-8", errors="replace")
    return "".join(out)


@app.get("/api/services")
def get_services():
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "level": s["level"],
            "description": s["description"],
            "open_url": compute_open_url(s),
            "on_demand": s.get("on_demand", False),
        }
        for s in SERVICES
    ]


@app.get("/api/status")
async def get_status():
    async with httpx.AsyncClient() as client:
        statuses = await asyncio.gather(*(check_status(s, client) for s in SERVICES))
    return [{"id": s["id"], "status": status} for s, status in zip(SERVICES, statuses)]


@app.get("/api/logs/{service_id}", response_class=PlainTextResponse)
async def get_logs(service_id: str):
    container = CONTAINER_BY_ID.get(service_id)
    if not container:
        return PlainTextResponse("servizio sconosciuto", status_code=404)
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(
                f"{DOCKER_PROXY_URL}/containers/{container}/logs",
                params={"stdout": "1", "stderr": "1", "tail": LOGS_TAIL_LINES, "timestamps": "1"},
                timeout=LOGS_TIMEOUT_SECONDS,
            )
        except (httpx.HTTPError, OSError) as e:
            return PlainTextResponse(f"impossibile raggiungere il proxy Docker: {e}", status_code=502)
    if r.status_code == 404:
        return PlainTextResponse(f"container '{container}' non trovato (mai avviato?)", status_code=404)
    if r.status_code >= 400:
        return PlainTextResponse(f"errore dal proxy Docker: HTTP {r.status_code}", status_code=502)
    text = demux_docker_logs(r.content)
    return PlainTextResponse(text or "(nessun log)")


class NormalizeRequest(BaseModel):
    prompt: str


class GenerateRequest(BaseModel):
    spec: dict


def _resolve_flowise_chatflow_id(flows: list, name_fragment: str) -> str | None:
    for f in flows:
        if name_fragment in f.get("name", ""):
            return f.get("id")
    return None


@app.post("/api/normalize")
async def normalize(req: NormalizeRequest):
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(400, "prompt vuoto")
    api_key = os.environ.get("FLOWISE_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(503, "FLOWISE_API_KEY non configurata su questo servizio")
    flowise_url = os.environ.get("FLOWISE_URL", f"http://flowise:{os.environ.get('FLOWISE_PORT', '3000')}")
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{flowise_url}/api/v1/chatflows", headers=headers, timeout=10)
            r.raise_for_status()
            flows = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(502, f"impossibile leggere i chatflow da Flowise: {e}") from e
        chatflow_id = _resolve_flowise_chatflow_id(flows, "L2.5")
        if not chatflow_id:
            raise HTTPException(503, "chatflow L2.5 non trovato — importato?")
        try:
            r = await client.post(
                f"{flowise_url}/api/v1/prediction/{chatflow_id}",
                headers=headers,
                json={"question": prompt},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise HTTPException(502, f"chiamata al chatflow L2.5 fallita: {e}") from e
    spec = data.get("json")
    if spec is None:
        # [E2E-1, docs/logbook_fase6.md] Flowise 3.1.4 con Structured
        # Output Parser mette la risposta in data["json"], non
        # data["text"] -- se manca, il parser ha fallito o il chatflow
        # non ha un output parser strutturato collegato.
        raise HTTPException(502, "risposta L2.5 senza campo 'json' strutturato")
    return {"spec": spec}


def _extract_json_after(text: str, marker: str, start_from: int = 0):
    """Trova il primo blocco JSON bilanciato dopo `marker` nell'output
    testuale di generate_and_verify.py (che stampa gia' json.dumps(...,
    indent=2) per ogni esito -- qui solo letto, mai riscritto)."""
    idx = text.find(marker, start_from)
    if idx == -1:
        return None, -1
    brace_idx = text.find("{", idx)
    if brace_idx == -1:
        return None, -1
    depth = 0
    for i in range(brace_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[brace_idx : i + 1]), i + 1
                except json.JSONDecodeError:
                    return None, i + 1
    return None, -1


def _parse_generate_stdout(stdout: str) -> dict:
    result: dict = {"status": "UNKNOWN"}

    m = re.search(r"case_id:\s*([0-9a-fA-F-]+)", stdout)
    if m:
        result["case_id"] = m.group(1)

    preset, _ = _extract_json_after(stdout, "-> Preset applicato:")
    if preset:
        result["preset"] = {
            "name": preset.get("feature"),
            "standard": preset.get("thread_standard"),
            "engagement_length_mm": preset.get("engagement_length_mm"),
        }

    verify, _ = _extract_json_after(stdout, "--- Esito verifica ---")
    if verify:
        result["verify"] = verify
        result["step_path"] = verify.get("generated_part_step_path")

    go, _ = _extract_json_after(stdout, "--- Esito gauge-check ---")
    if go:
        result["go"] = go

    nogo, _ = _extract_json_after(stdout, "--- Esito gauge-check (NO-GO) ---")
    if nogo:
        result["nogo"] = nogo

    if "Strategia scartata dalla memoria del collaudo virtuale" in stdout:
        result["status"] = "EXCLUDED"
    elif re.search(r"=== PASS al tentativo \d+/\d+", stdout):
        result["status"] = "PASS"
    elif "final_status: unrecoverable_virtual" in stdout:
        result["status"] = "FAIL"

    return result


def _step_to_stl_b64(step_rel_path: str) -> str | None:
    step_path = os.path.join(EXEC_PARTS_DIR, step_rel_path)
    if not os.path.isfile(step_path):
        return None
    import cadquery as cq  # lazy: pesante, serve solo qui

    with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        shape = cq.importers.importStep(step_path)
        cq.exporters.export(shape, tmp_path)
        with open(tmp_path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    finally:
        os.unlink(tmp_path)


@app.post("/api/generate")
async def generate(req: GenerateRequest):
    env = dict(os.environ)
    env.setdefault("L2_STRATEGY", "param_first")
    proc = await asyncio.to_thread(
        subprocess.run,
        ["python3", "generate_and_verify.py", json.dumps(req.spec)],
        cwd=ORCHESTRATOR_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=GENERATE_TIMEOUT_SECONDS,
    )
    result = _parse_generate_stdout(proc.stdout)
    result["log_tail"] = proc.stdout[-4000:]
    if proc.returncode not in (0, 1):
        result.setdefault("status", "ERROR")
        result["error"] = f"exit {proc.returncode}: {proc.stderr[-1000:]}"
    if result.get("status") == "PASS" and result.get("step_path"):
        try:
            result["stl_base64"] = _step_to_stl_b64(result["step_path"])
        except Exception as e:  # noqa: BLE001 - conversione best-effort, non deve rompere una PASS reale
            result["stl_conversion_error"] = str(e)
    return result


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
