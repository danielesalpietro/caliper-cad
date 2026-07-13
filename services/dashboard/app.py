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
"""

import asyncio
import os
import struct

import httpx
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="CALIPER — dashboard")

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


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
