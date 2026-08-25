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
import io
import json
import os
import re
import struct
import subprocess
import tempfile
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="CALIPER — dashboard")

ORCHESTRATOR_DIR = "/orchestrator"
EXEC_PARTS_DIR = "/exec/parts"
GENERATE_TIMEOUT_SECONDS = 180
SLICER_JOBS_DIR = "/jobs"
SLICER_PROFILE_PATH = "/config/caliper-pla.ini"
SLICER_POLL_TIMEOUT_SECONDS = 60

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
        # [M7] Flowise e' l'unico servizio la cui porta INTERNA (non solo
        # quella pubblicata sull'host) cambia con FLOWISE_PORT — il
        # compose passa PORT=${FLOWISE_PORT} al container, quindi
        # cambiare quella variabile sposta anche l'indirizzo su cui
        # ascolta dentro la rete Docker. Una `health_url` statica
        # (com'era: "http://flowise:3000/...") punta a una porta morta
        # non appena FLOWISE_PORT si discosta dal default — mai
        # esercitato prima di cambiarla per davvero la prima volta.
        "health_url_env": "FLOWISE_PORT",
        "health_default_port": 3000,
        "health_path": "/api/v1/ping",
        "open_url_env": "FLOWISE_PORT",
        "open_default_port": 3000,
        "open_path": "",
        "gated_by_public_access": True,
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
        "gated_by_public_access": True,
    },
]

CONTAINER_BY_ID = {s["id"]: s["container"] for s in SERVICES}

# [Prompt to Part / accesso pubblico] Docker Compose non ha un vero
# switch ON/OFF sul bind di una porta pubblicata, solo un indirizzo:
# "0.0.0.0" (ON, raggiungibile dall'esterno) o "127.0.0.1" (OFF, solo
# host/altri container). La dashboard stessa NON e' mai gated da
# questo flag (decisione dell'utente, sessione 2026-08-25): se lo
# fosse, un OFF messo da remoto toglierebbe l'accesso anche al pannello
# che dovrebbe rimetterlo ON, obbligando a rientrare via SSH.
PUBLIC_ACCESS = os.environ.get("PUBLIC_ACCESS", "0.0.0.0").strip()
PUBLIC_ACCESS_ON = PUBLIC_ACCESS != "127.0.0.1"

PROMPT_TO_PART_MODE = os.environ.get("PROMPT_TO_PART_MODE", "RW").strip().upper()


def compute_open_url(svc: dict) -> str | None:
    if svc.get("gated_by_public_access") and not PUBLIC_ACCESS_ON:
        return None
    if svc.get("open_fixed_port"):
        port = svc["open_fixed_port"]
    elif svc.get("open_url_env"):
        port = os.environ.get(svc["open_url_env"]) or svc.get("open_default_port")
    else:
        return None
    if not port:
        return None
    return f"http://localhost:{port}{svc.get('open_path', '')}"


def compute_health_url(svc: dict) -> str | None:
    if svc.get("health_url"):
        return svc["health_url"]
    if svc.get("health_url_env"):
        # Stesso pattern di compute_open_url, ma sull'hostname interno
        # della rete Docker (svc["id"] coincide col nome del servizio nel
        # compose per costruzione, vedi SERVICES sopra) invece di
        # "localhost" — qui serve chiamare il container, non aprirlo nel
        # browser dell'utente.
        port = os.environ.get(svc["health_url_env"]) or svc.get("health_default_port")
        if not port:
            return None
        return f"http://{svc['id']}:{port}{svc.get('health_path', '')}"
    return None


async def check_status(svc: dict, client: httpx.AsyncClient) -> str:
    health_url = compute_health_url(svc)
    if health_url:
        try:
            r = await client.get(health_url, timeout=HEALTH_TIMEOUT_SECONDS)
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
            "public_access_off": bool(s.get("gated_by_public_access") and not PUBLIC_ACCESS_ON),
        }
        for s in SERVICES
    ]


@app.get("/api/config")
def get_config():
    return {"public_access": "on" if PUBLIC_ACCESS_ON else "off", "prompt_to_part_mode": PROMPT_TO_PART_MODE}


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
    if PROMPT_TO_PART_MODE != "RW":
        raise HTTPException(403, f"Prompt to Part e' in sola lettura (PROMPT_TO_PART_MODE={PROMPT_TO_PART_MODE})")
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


def _resolve_under_root(root: str, relative_path: str) -> str:
    """Stessa guardia di gauge_check.py (resolve_under_root): rifiuta
    ogni path che uscirebbe dalla radice consentita -- classe di bug
    verificata dal vivo in M7 (isolamento attivo, path traversal su
    /gauge-check), stesso trattamento qui per i download."""
    root_real = os.path.realpath(root)
    candidate_real = os.path.realpath(os.path.join(root, relative_path))
    if candidate_real != root_real and not candidate_real.startswith(root_real + os.sep):
        raise HTTPException(400, f"path fuori dalla radice consentita ({root}): {relative_path!r}")
    return candidate_real


def _step_to_stl_bytes(step_rel_path: str) -> bytes | None:
    step_path = _resolve_under_root(EXEC_PARTS_DIR, step_rel_path)
    if not os.path.isfile(step_path):
        return None
    import cadquery as cq  # lazy: pesante, serve solo qui

    with tempfile.NamedTemporaryFile(suffix=".stl", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        shape = cq.importers.importStep(step_path)
        cq.exporters.export(shape, tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        os.unlink(tmp_path)


def _step_to_stl_b64(step_rel_path: str) -> str | None:
    data = _step_to_stl_bytes(step_rel_path)
    return base64.b64encode(data).decode("ascii") if data is not None else None


@app.get("/api/download/step/{filename}")
def download_step(filename: str):
    step_path = _resolve_under_root(EXEC_PARTS_DIR, filename)
    if not os.path.isfile(step_path):
        raise HTTPException(404, "STEP non trovato (mai generato, o volume scaduto)")
    with open(step_path, "rb") as f:
        data = f.read()
    return Response(
        content=data,
        media_type="model/step",
        headers={"Content-Disposition": f'attachment; filename="{os.path.basename(filename)}"'},
    )


@app.get("/api/download/stl/{filename}")
def download_stl(filename: str):
    try:
        data = _step_to_stl_bytes(filename)
    except Exception as e:  # noqa: BLE001 - conversione best-effort per un download
        raise HTTPException(500, f"conversione STEP->STL fallita: {e}") from e
    if data is None:
        raise HTTPException(404, "STEP non trovato (mai generato, o volume scaduto)")
    stl_name = os.path.splitext(os.path.basename(filename))[0] + ".stl"
    return Response(
        content=data,
        media_type="model/stl",
        headers={"Content-Disposition": f'attachment; filename="{stl_name}"'},
    )


def _safe_job_id(raw: str) -> str:
    base = os.path.splitext(os.path.basename(raw))[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", base):
        raise HTTPException(400, f"id non valido per un job di slicing: {raw!r}")
    return base


def _load_slicer_profile_params() -> dict:
    """Legge config/prusaslicer/caliper-pla.ini com'e' (mai riscritto
    qui) -- stessi parametri che il watcher passa davvero a
    prusa-slicer via --load, non una copia che potrebbe disallinearsi."""
    params: dict[str, str] = {}
    try:
        with open(SLICER_PROFILE_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                params[k.strip()] = v.strip()
    except OSError:
        pass
    return params


def _slice_stl_to_gcode(job_id: str, stl_bytes: bytes) -> tuple[bytes | None, str | None]:
    """Protocollo job/result su volume condiviso con slicer-watcher
    (ops/docker/prusaslicer/watch.sh) -- stesso pattern di
    verifier-executor/watcher.py, stessa ragione: nessuna rete tra
    dashboard e il container che fa il lavoro pesante."""
    base = os.path.join(SLICER_JOBS_DIR, job_id)
    stl_path, gcode_path, done_path, log_path = base + ".stl", base + ".gcode", base + ".done", base + ".log"
    for p in (gcode_path, done_path, log_path):
        if os.path.exists(p):
            os.remove(p)
    with open(stl_path, "wb") as f:
        f.write(stl_bytes)
    deadline = time.time() + SLICER_POLL_TIMEOUT_SECONDS
    while not os.path.exists(done_path):
        if time.time() > deadline:
            return None, f"timeout ({SLICER_POLL_TIMEOUT_SECONDS}s) in attesa dello slicer-watcher"
        time.sleep(0.4)
    with open(done_path, encoding="utf-8") as f:
        status = f.read().strip()
    if status != "ok" or not os.path.isfile(gcode_path):
        err = ""
        if os.path.isfile(log_path):
            with open(log_path, encoding="utf-8", errors="replace") as f:
                err = f.read()[-1000:]
        return None, err or "slicing fallito, nessun log"
    with open(gcode_path, "rb") as f:
        return f.read(), None


def _parse_gcode_summary(gcode_bytes: bytes) -> dict:
    """PrusaSlicer scrive i propri parametri come commenti nel G-code --
    letti qui, non ricalcolati (vedi anche docs/logbook_fase7.md, primo
    G-code del progetto). "filament used"/"estimated printing time" sono
    nel riepilogo che PrusaSlicer APPENDE in fondo al file dopo lo
    slicing (verificato dal vivo: offset ~300KB su 310KB, un semplice
    "leggi i primi/ultimi N byte" li ha mancati due volte -- dopo quel
    riepilogo il file continua con un dump completo di TUTTI i
    parametri del profilo (centinaia di righe, "; prusaslicer_config =
    start...end"), che da solo occupa piu' degli ultimi byte letti
    prima. Fix: decodifica tutto, taglia via il dump di config PRIMA di
    cercare (e' l'unica cosa davvero grande nel file), poi cerca sul
    resto."""
    text = gcode_bytes.decode("utf-8", errors="replace")
    config_start = text.find("; prusaslicer_config = start")
    if config_start != -1:
        text = text[:config_start]
    summary = {}
    m = re.search(r"filament used \[mm\]\s*=\s*([\d.]+)", text)
    if m:
        summary["filament_used_mm"] = float(m.group(1))
    m = re.search(r"estimated printing time.*=\s*(.+)", text)
    if m:
        summary["estimated_printing_time"] = m.group(1).strip()
    return summary


@app.get("/api/download/gcode/{filename}")
async def download_gcode(filename: str):
    try:
        stl_bytes = _step_to_stl_bytes(filename)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"conversione STEP->STL fallita: {e}") from e
    if stl_bytes is None:
        raise HTTPException(404, "STEP non trovato (mai generato, o volume scaduto)")
    job_id = _safe_job_id(filename)
    gcode_bytes, err = await asyncio.to_thread(_slice_stl_to_gcode, job_id, stl_bytes)
    if gcode_bytes is None:
        raise HTTPException(502, f"slicing fallito: {err}")
    return Response(
        content=gcode_bytes,
        media_type="text/x-gcode",
        headers={"Content-Disposition": f'attachment; filename="{job_id}.gcode"'},
    )


@app.post("/api/generate")
async def generate(req: GenerateRequest):
    if PROMPT_TO_PART_MODE != "RW":
        raise HTTPException(403, f"Prompt to Part e' in sola lettura (PROMPT_TO_PART_MODE={PROMPT_TO_PART_MODE})")
    env = dict(os.environ)
    env.setdefault("L2_STRATEGY", "param_first")
    started_at = time.time()
    proc = await asyncio.to_thread(
        subprocess.run,
        ["python3", "generate_and_verify.py", json.dumps(req.spec)],
        cwd=ORCHESTRATOR_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=GENERATE_TIMEOUT_SECONDS,
    )
    elapsed_seconds = round(time.time() - started_at, 2)
    result = _parse_generate_stdout(proc.stdout)
    result["log_tail"] = proc.stdout[-4000:]
    result["elapsed_seconds"] = elapsed_seconds
    result["spec"] = req.spec
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    if proc.returncode not in (0, 1):
        result.setdefault("status", "ERROR")
        result["error"] = f"exit {proc.returncode}: {proc.stderr[-1000:]}"
    if result.get("status") == "PASS" and result.get("step_path"):
        try:
            result["stl_base64"] = _step_to_stl_b64(result["step_path"])
        except Exception as e:  # noqa: BLE001 - conversione best-effort, non deve rompere una PASS reale
            result["stl_conversion_error"] = str(e)
    return result


class ReportRequest(BaseModel):
    result: dict


def _fmt_dt(iso_str: str | None) -> tuple[str, str]:
    if iso_str:
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        except ValueError:
            dt = datetime.now(timezone.utc)
    else:
        dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S UTC")


def _render_report_pdf(payload: dict) -> bytes:
    """Rapporto in stile disegno tecnico (cornice, blocco titoli in
    basso, timbro di stato) -- NON un disegno certificato, e' un
    riepilogo automatico di quello che generate_and_verify.py ha
    davvero prodotto/misurato in questa chiamata. Nessun dato inventato
    qui: solo cio' che e' gia' in `payload` (la stessa risposta di
    /api/generate, il client la rimanda com'e')."""
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as pdfcanvas

    INK = HexColor("#16211D")
    MUTED = HexColor("#56655D")
    LINE = HexColor("#C9D1C8")
    ACCENT = HexColor("#B4692F")
    PASS_C = HexColor("#2E7D4F")
    FAIL_C = HexColor("#A8412A")

    status = payload.get("status", "UNKNOWN")
    status_color = PASS_C if status == "PASS" else FAIL_C
    spec = payload.get("spec") or {}
    preset = payload.get("preset") or {}
    verify = payload.get("verify") or {}
    go = payload.get("go") or {}
    nogo = payload.get("nogo") or {}
    case_id = payload.get("case_id") or "—"
    elapsed = payload.get("elapsed_seconds")
    date_str, time_str = _fmt_dt(payload.get("generated_at"))

    buf = io.BytesIO()
    W, H = A4
    c = pdfcanvas.Canvas(buf, pagesize=A4)
    m = 14 * mm

    c.setStrokeColor(LINE)
    c.setLineWidth(1)
    c.rect(m, m, W - 2 * m, H - 2 * m)

    # ---- header ----
    top = H - m
    c.setFont("Helvetica-Bold", 20)
    c.setFillColor(INK)
    c.drawString(m + 6 * mm, top - 14 * mm, "CALIPER")
    caliper_w = c.stringWidth("CALIPER", "Helvetica-Bold", 20)
    c.setFillColor(ACCENT)
    c.drawString(m + 6 * mm + caliper_w, top - 14 * mm, "-CAD")
    c.setStrokeColor(ACCENT)
    c.setLineWidth(1.6)
    mx, my = m + 6 * mm, top - 20 * mm
    c.line(mx, my, mx + 10 * mm, my)
    c.line(mx, my, mx, my - 3 * mm)
    c.line(mx + 10 * mm, my, mx + 10 * mm, my - 3 * mm)
    c.setFont("Helvetica", 9)
    c.setFillColor(MUTED)
    c.drawString(m + 6 * mm, top - 25 * mm, "verification report — generate_and_verify.py")

    c.setFont("Helvetica", 9)
    c.setFillColor(INK)
    c.drawRightString(W - m - 6 * mm, top - 10 * mm, f"case {case_id}")
    c.setFillColor(MUTED)
    c.drawRightString(W - m - 6 * mm, top - 15 * mm, f"{date_str}   {time_str}")
    c.drawRightString(W - m - 6 * mm, top - 20 * mm, "sheet 1/1")

    c.setStrokeColor(LINE)
    c.setLineWidth(0.75)
    c.line(m + 6 * mm, top - 29 * mm, W - m - 6 * mm, top - 29 * mm)

    # ---- body: two columns ----
    col_top = top - 36 * mm
    col_w = (W - 2 * m - 18 * mm) / 2
    left_x = m + 6 * mm
    right_x = left_x + col_w + 6 * mm

    def section(x, y, title, rows, width):
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(ACCENT)
        c.drawString(x, y, title.upper())
        c.setStrokeColor(LINE)
        c.setLineWidth(0.5)
        c.line(x, y - 2 * mm, x + width, y - 2 * mm)
        yy = y - 7 * mm
        c.setFont("Helvetica", 8.5)
        for label, value in rows:
            c.setFillColor(MUTED)
            c.drawString(x, yy, str(label))
            c.setFillColor(INK)
            c.drawRightString(x + width, yy, str(value) if value not in (None, "") else "—")
            yy -= 5.4 * mm
        return yy

    y1 = section(left_x, col_top, "specification (L2.5)", list(spec.items()) or [("—", "—")], col_w)
    y1 -= 5 * mm
    preset_rows = [
        ("preset", preset.get("name")),
        ("standard", preset.get("standard")),
        (
            "engagement length",
            f"{preset.get('engagement_length_mm')} mm" if preset.get("engagement_length_mm") is not None else None,
        ),
    ]
    y1b = section(left_x, y1, "preset applied", preset_rows, col_w)
    y1b -= 5 * mm
    slicer_params = payload.get("slicer_params") or {}
    slicer_summary = payload.get("slicer_summary") or {}
    slicer_rows = [
        ("layer height", f"{slicer_params['layer_height']} mm" if "layer_height" in slicer_params else None),
        ("perimeters", slicer_params.get("perimeters")),
        ("fill density", slicer_params.get("fill_density")),
        ("nozzle diameter", f"{slicer_params['nozzle_diameter']} mm" if "nozzle_diameter" in slicer_params else None),
        ("filament", slicer_params.get("filament_type")),
        (
            "filament used",
            f"{slicer_summary['filament_used_mm']} mm" if slicer_summary.get("filament_used_mm") is not None else None,
        ),
        ("est. print time", slicer_summary.get("estimated_printing_time")),
    ]
    if any(v is not None for _, v in slicer_rows):
        section(left_x, y1b, "slicing (prusaslicer, PLA profile)", slicer_rows, col_w)

    verify_rows = [(chk.get("name"), chk.get("status")) for chk in (verify.get("checks") or [])]
    y2 = section(right_x, col_top, "verification (L3 — /verify)", verify_rows or [("—", "—")], col_w)
    y2 -= 5 * mm

    gauge_rows = []
    for label, gauge_result in (("GO", go), ("NO-GO", nogo)):
        if not gauge_result:
            continue
        gc = gauge_result.get("gauge_check") or {}
        vol = gc.get("interference_volume_mm3")
        gauge_rows.append((f"{label} gauge", gauge_result.get("status")))
        gauge_rows.append((f"{label} interference", f"{vol} mm³" if vol is not None else None))
    section(right_x, y2, "go / no-go gauge check", gauge_rows or [("—", "—")], col_w)

    # ---- title block ----
    tb_h, tb_x, tb_y, tb_w = 30 * mm, m, m, W - 2 * m
    c.setStrokeColor(LINE)
    c.setLineWidth(1)
    c.rect(tb_x, tb_y, tb_w, tb_h)
    cell_w = tb_w / 5

    def tb_cell(i, label, value, value_color=INK):
        x = tb_x + i * cell_w
        if i > 0:
            c.setStrokeColor(LINE)
            c.line(x, tb_y, x, tb_y + tb_h)
        c.setFont("Helvetica", 6.5)
        c.setFillColor(MUTED)
        c.drawString(x + 2 * mm, tb_y + tb_h - 5 * mm, label.upper())
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(value_color)
        c.drawString(x + 2 * mm, tb_y + 4 * mm, str(value))

    tb_cell(0, "drawn by", "CALIPER-CAD (auto)")
    tb_cell(1, "checked by", "gauge_check.py")
    tb_cell(2, "date", date_str)
    tb_cell(3, "elapsed", f"{elapsed}s" if elapsed is not None else "—")
    sx = tb_x + 4 * cell_w
    c.setStrokeColor(status_color)
    c.setLineWidth(1.6)
    c.rect(sx + 2 * mm, tb_y + 3 * mm, cell_w - 4 * mm, tb_h - 6 * mm)
    c.setFont("Helvetica-Bold", 13)
    c.setFillColor(status_color)
    c.drawCentredString(sx + cell_w / 2, tb_y + tb_h / 2 - 2, str(status))

    c.setFont("Helvetica-Oblique", 6.5)
    c.setFillColor(MUTED)
    c.drawCentredString(
        W / 2,
        6 * mm,
        "Automated verification report (boolean gauge check, CadQuery/OCC) — not a certified engineering "
        "drawing or a licensed engineer's stamp.",
    )

    c.showPage()
    c.save()
    return buf.getvalue()


@app.post("/api/report")
def report(req: ReportRequest):
    payload = dict(req.result or {})
    payload["slicer_params"] = _load_slicer_profile_params()
    # Stima di stampa best-effort: uno slicing live dello stesso STEP, se
    # disponibile -- il PDF resta comunque generabile senza (solo i
    # parametri statici del profilo, niente stima). Mai lasciare che un
    # fallimento qui rompa un report altrimenti valido.
    step_path = payload.get("step_path")
    if payload.get("status") == "PASS" and step_path:
        try:
            stl_bytes = _step_to_stl_bytes(step_path)
            if stl_bytes is not None:
                gcode_bytes, _err = _slice_stl_to_gcode(_safe_job_id(step_path) + "-report", stl_bytes)
                if gcode_bytes is not None:
                    payload["slicer_summary"] = _parse_gcode_summary(gcode_bytes)
        except Exception:  # noqa: BLE001 - la stima e' un extra, non un requisito del report
            pass
    pdf_bytes = _render_report_pdf(payload)
    case_id = payload.get("case_id") or "caliper"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="caliper-report-{case_id}.pdf"'},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
