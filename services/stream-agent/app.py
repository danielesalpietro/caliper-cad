"""
CALIPER — Livello 7 grounding agent
------------------------------------
Adattato da NORTHSTREAM (danielesalpietro/NORTHSTREAM, stream-agent) —
vedi Rischio #10 in docs/architettura-prototipo-mesh-llm.md: la sorgente
consuma uno stream Kafka in continuo, qui viene sostituita con un indexer
che legge periodicamente i casi congelati del Livello 6 da disco (nessun
evento in tempo reale, un dataset statico che cresce nel tempo).

Ogni caso del Livello 6 e' un file JSON in DATASET_DIR (uno per caso,
vedi lo schema in docs/architettura-prototipo-mesh-llm.md, sezione
Livello 6). Vengono incorporati con Ollama e indicizzati in Qdrant.

[M4, vedi docs/logbook_fase4.md e GitHub issue #5] Secondo indexer per il
log del collaudo virtuale (retry_log.jsonl esteso, vedi
services/orchestrator/retry_policy.py e virtual_memory.py): stessa
pipeline di embedding, MA collezione Qdrant SEPARATA
(COLLECTION_VIRTUAL != COLLECTION_PHYSICAL) — il firewall
simulato/fisico deciso in docs/logbook_fase4.md ("due collezioni
separate, mai fuse") vale anche qui, non solo nello storage su disco.
Ogni record recuperato, da entrambe le collezioni, porta un campo
'source' esplicito ("physical" o "virtual") nel payload strutturato
restituito dalle API — non solo incorporato nel testo dell'embedding.
Riserva onesta (stessa di M1-M3, vedi docs/handoff_m4.md): nessuna
istanza Ollama/Qdrant viva e' stata verificata in questa sessione — il
codice sotto e' scritto e revisionato, non eseguito contro un cluster
reale.

Espone:
  GET  /health              -> stato servizio + numero casi indicizzati
                                (fisici e virtuali, separatamente)
  GET  /cases                -> ultimi N casi FISICI indicizzati (debug)
  GET  /virtual-cases        -> ultimi N record VIRTUALI indicizzati (debug)
  POST /reindex               -> forza una rilettura di DATASET_DIR e del
                                  log virtuale
  POST /chat                   -> risponde a una domanda usando i casi
                                   del Livello 6 E il log virtuale come
                                   contesto, ciascuno etichettato source
  POST /compare                  -> stessa domanda con/senza contesto,
                                     affiancate (il confronto e' il punto)

Il filtro esatto sui campi strutturati (feature, nominal, tolerance...)
menzionato nell'architettura per il Livello 7 richiede lo schema del
Livello 2.5, non ancora definito — per ora la ricerca e' solo semantica
su Qdrant. TODO una volta che lo schema L2.5 esiste. La regola anti-bias
di M4 ("N fallimenti virtuali richiedono corroborazione fisica") e'
implementata lato orchestratore (services/orchestrator/virtual_memory.py),
PRIMA di generare — questo servizio resta un agente di grounding/lettura,
non il punto dove si decide se escludere una strategia.
"""

import glob
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

import requests
from fastapi import FastAPI
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "granite4:1b")
EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "granite-embedding:30m")
DATASET_DIR = os.getenv("DATASET_DIR", "/data/dataset")
# [M4] Percorso del log del collaudo virtuale (retry_log.jsonl esteso),
# montato read-only nel container — vedi docker-compose.yml. Un solo
# file JSONL append-only (non una directory di file, a differenza del
# Livello 6): l'indexer legge in modo incrementale per byte-offset, vedi
# index_virtual_log_once().
VIRTUAL_LOG_PATH = os.getenv("VIRTUAL_LOG_PATH", "/data/virtual_log/retry_log.jsonl")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
COLLECTION_PHYSICAL = "caliper_l6_dataset"
COLLECTION_VIRTUAL = "caliper_virtual_log"
MAX_BUFFER = 500

app = FastAPI(title="CALIPER Livello 7 — grounding agent")

indexed_cases = deque(maxlen=MAX_BUFFER)
indexed_virtual = deque(maxlen=MAX_BUFFER)
qdrant = QdrantClient(url=QDRANT_URL)
_ready_collections: set[str] = set()
_indexed_files = set()
_virtual_log_offset = 0


def ensure_collection(collection_name: str, vector_size: int):
    if collection_name in _ready_collections:
        return
    existing = [c.name for c in qdrant.get_collections().collections]
    if collection_name not in existing:
        qdrant.create_collection(
            collection_name=collection_name,
            vectors_config=qmodels.VectorParams(
                size=vector_size, distance=qmodels.Distance.COSINE
            ),
        )
    _ready_collections.add(collection_name)


def embed_text(text: str):
    resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def case_to_text(case: dict) -> str:
    """Testo per l'embedding di UN caso del Livello 6 (fisico, misura
    reale — vedi architettura). 'source=physical' e' incluso anche nel
    testo (segnale semantico), non solo nel payload strutturato."""
    spec = case.get("specifica_strutturata") or case.get("spec") or {}
    esito = case.get("esito") or case.get("outcome") or "?"
    prompt = case.get("prompt", "")
    return (
        f"source=physical prompt={prompt} spec={json.dumps(spec, default=str)} "
        f"esito={esito} materiale={case.get('materiale', '?')} "
        f"macchina={case.get('macchina', '?')}"
    )


def virtual_record_to_text(record: dict) -> str:
    """Equivalente di case_to_text() ma per UN record del log del
    collaudo virtuale (retry_policy.RetryBudget.record_attempt) — vedi
    docstring del modulo. 'source=virtual' e' incluso esplicitamente nel
    testo per lo stesso motivo di case_to_text()."""
    return (
        f"source=virtual case_id={record.get('case_id', '?')} "
        f"feature={record.get('feature', '?')} spec_key={record.get('spec_key', '?')} "
        f"attempt={record.get('attempt', '?')} directive={record.get('directive_used', '?')} "
        f"esito={record.get('outcome', '?')} errore={record.get('outcome_error', '?')}"
    )


def index_dataset_once():
    """Scan DATASET_DIR (Livello 6, fisico) for new/changed case files
    and index them into COLLECTION_PHYSICAL."""
    if not os.path.isdir(DATASET_DIR):
        return
    for path in sorted(glob.glob(os.path.join(DATASET_DIR, "*.json"))):
        mtime = os.path.getmtime(path)
        key = f"{path}:{mtime}"
        if key in _indexed_files:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                case = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"skipping {path}, unreadable: {e}")
            continue

        text = case_to_text(case)
        try:
            vector = embed_text(text)
            ensure_collection(COLLECTION_PHYSICAL, len(vector))
            qdrant.upsert(
                collection_name=COLLECTION_PHYSICAL,
                points=[
                    qmodels.PointStruct(
                        id=abs(hash(path)) % (2**63),
                        vector=vector,
                        payload={"text": text, "source": "physical", "source_file": path},
                    )
                ],
            )
            indexed_cases.append(
                {
                    "file": path,
                    "text": text,
                    "source": "physical",
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            )
            _indexed_files.add(key)
        except Exception as e:
            print(f"embedding/upsert failed for {path}: {e}")


def index_virtual_log_once():
    """Legge le righe NUOVE (per byte-offset, il file e' append-only —
    vedi RetryBudget._append_log in retry_policy.py) del log del
    collaudo virtuale e le indicizza in COLLECTION_VIRTUAL. MAI la
    stessa collezione del Livello 6 (vedi docstring del modulo)."""
    global _virtual_log_offset
    if not os.path.isfile(VIRTUAL_LOG_PATH):
        return
    try:
        with open(VIRTUAL_LOG_PATH, "r", encoding="utf-8") as f:
            f.seek(_virtual_log_offset)
            new_lines = f.readlines()
            _virtual_log_offset = f.tell()
    except OSError as e:
        print(f"unreadable virtual log {VIRTUAL_LOG_PATH}: {e}")
        return

    for line in new_lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"skipping malformed virtual log line: {e}")
            continue
        if record.get("source") != "virtual":
            # Difesa in profondita': non dovrebbe accadere (RetryBudget
            # scrive sempre source="virtual"), ma se il firewall a monte
            # avesse un bug, meglio scartare qui che indicizzare un
            # record ambiguo in una collezione dichiarata "virtual".
            print(f"skipping virtual log record without source='virtual': {record}")
            continue

        text = virtual_record_to_text(record)
        point_id = abs(hash(f"{record.get('case_id')}:{record.get('attempt')}")) % (2**63)
        try:
            vector = embed_text(text)
            ensure_collection(COLLECTION_VIRTUAL, len(vector))
            qdrant.upsert(
                collection_name=COLLECTION_VIRTUAL,
                points=[
                    qmodels.PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "text": text,
                            "source": "virtual",
                            "case_id": record.get("case_id"),
                            "feature": record.get("feature"),
                            "spec_key": record.get("spec_key"),
                        },
                    )
                ],
            )
            indexed_virtual.append(
                {
                    "case_id": record.get("case_id"),
                    "text": text,
                    "source": "virtual",
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception as e:
            print(f"embedding/upsert failed for virtual record {record.get('case_id')}: {e}")


def index_loop():
    while True:
        index_dataset_once()
        index_virtual_log_once()
        time.sleep(POLL_INTERVAL_SECONDS)


threading.Thread(target=index_loop, daemon=True).start()


class ChatRequest(BaseModel):
    question: str
    top_k: int = 5


def _search_collection(collection_name: str, source: str, vector, top_k: int):
    try:
        hits = qdrant.search(collection_name=collection_name, query_vector=vector, limit=top_k)
        return [{"text": h.payload["text"], "source": h.payload.get("source", source)} for h in hits]
    except Exception as e:
        print(f"semantic search on {collection_name} failed: {e}")
        return []


def search_context(question: str, top_k: int):
    """Interroga ENTRAMBE le collezioni (Livello 6 fisico + log
    virtuale) — mai una query unica su una collezione fusa, vedi
    docstring del modulo. Ogni elemento restituito porta 'source'
    esplicito, non solo il testo: il discriminatore deve sopravvivere
    fino alla risposta finale (M4, criterio non negoziabile)."""
    try:
        vector = embed_text(question)
    except Exception as e:
        print(f"embedding failed, falling back to recent buffers: {e}")
        physical_fallback = [{"text": c["text"], "source": "physical"} for c in list(indexed_cases)[-top_k:]]
        virtual_fallback = [{"text": c["text"], "source": "virtual"} for c in list(indexed_virtual)[-top_k:]]
        return physical_fallback + virtual_fallback

    physical_hits = _search_collection(COLLECTION_PHYSICAL, "physical", vector, top_k)
    virtual_hits = _search_collection(COLLECTION_VIRTUAL, "virtual", vector, top_k)
    combined = physical_hits + virtual_hits
    if not combined:
        physical_fallback = [{"text": c["text"], "source": "physical"} for c in list(indexed_cases)[-top_k:]]
        virtual_fallback = [{"text": c["text"], "source": "virtual"} for c in list(indexed_virtual)[-top_k:]]
        combined = physical_fallback + virtual_fallback
    return combined


def call_ollama(prompt: str) -> str:
    resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={"model": CHAT_MODEL, "prompt": prompt, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def build_grounded_prompt(question: str, context: list) -> str:
    if context:
        context_block = "\n".join(f"[{item['source']}] {item['text']}" for item in context)
    else:
        context_block = "(no validated CALIPER cases found)"
    return (
        "You are a CAD verification assistant. Answer the question using "
        "the CALIPER context below. Each line is tagged [physical] (Livello 6, "
        "real physical measurement, ground truth) or [virtual] (simulated "
        "gauge-check/sweep result, a fast but simulated signal, NEVER a "
        "substitute for physical validation). Prefer [physical] evidence when "
        "the two disagree, and say so explicitly if they do. If the context "
        "does not contain the answer, say explicitly that no matching case "
        "exists - do not guess.\n\n"
        f"Context:\n{context_block}\n\n"
        f"Question: {question}\nAnswer:"
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "indexed_cases_physical": len(indexed_cases),
        "indexed_records_virtual": len(indexed_virtual),
        "dataset_dir": DATASET_DIR,
        "virtual_log_path": VIRTUAL_LOG_PATH,
        "chat_model": CHAT_MODEL,
        "embed_model": EMBED_MODEL,
    }


@app.get("/cases")
def cases(limit: int = 20):
    return list(indexed_cases)[-limit:]


@app.get("/virtual-cases")
def virtual_cases(limit: int = 20):
    return list(indexed_virtual)[-limit:]


@app.post("/reindex")
def reindex():
    before_physical, before_virtual = len(indexed_cases), len(indexed_virtual)
    index_dataset_once()
    index_virtual_log_once()
    return {
        "physical_before": before_physical,
        "physical_after": len(indexed_cases),
        "virtual_before": before_virtual,
        "virtual_after": len(indexed_virtual),
    }


@app.post("/chat")
def chat(req: ChatRequest):
    context = search_context(req.question, req.top_k)
    answer = call_ollama(build_grounded_prompt(req.question, context))
    return {"answer": answer, "context_used": context}


@app.post("/compare")
def compare(req: ChatRequest):
    baseline_answer = call_ollama(f"Question: {req.question}\nAnswer:")
    context = search_context(req.question, req.top_k)
    grounded_answer = call_ollama(build_grounded_prompt(req.question, context))
    return {
        "question": req.question,
        "model": CHAT_MODEL,
        "without_dataset_context": baseline_answer,
        "with_dataset_context": grounded_answer,
        "context_used": context,
    }


# OpenAI-compatible surface so Open WebUI can talk to this agent as just
# another chat model, side by side with the raw Ollama model it already
# has via its native Ollama connection.
GROUNDED_MODEL_ID = "caliper-l7-grounded"


@app.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {"id": GROUNDED_MODEL_ID, "object": "model", "owned_by": "caliper"}
        ],
    }


class OpenAIChatRequest(BaseModel):
    model: str = GROUNDED_MODEL_ID
    messages: list
    stream: bool = False


@app.post("/v1/chat/completions")
def chat_completions(req: OpenAIChatRequest):
    question = req.messages[-1]["content"] if req.messages else ""
    context = search_context(question, 5)
    answer = call_ollama(build_grounded_prompt(question, context))

    return {
        "id": "chatcmpl-caliper",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": GROUNDED_MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
