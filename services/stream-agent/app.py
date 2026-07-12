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

Espone:
  GET  /health              -> stato servizio + numero casi indicizzati
  GET  /cases                -> ultimi N casi indicizzati (debug)
  POST /reindex               -> forza una rilettura di DATASET_DIR
  POST /chat                   -> risponde a una domanda usando i casi
                                   del Livello 6 come contesto
  POST /compare                  -> stessa domanda con/senza contesto,
                                     affiancate (il confronto e' il punto)

Il filtro esatto sui campi strutturati (feature, nominal, tolerance...)
menzionato nell'architettura per il Livello 7 richiede lo schema del
Livello 2.5, non ancora definito — per ora la ricerca e' solo semantica
su Qdrant. TODO una volta che lo schema L2.5 esiste.
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
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
COLLECTION = "caliper_l6_dataset"
MAX_BUFFER = 500

app = FastAPI(title="CALIPER Livello 7 — grounding agent")

indexed_cases = deque(maxlen=MAX_BUFFER)
qdrant = QdrantClient(url=QDRANT_URL)
_collection_ready = False
_indexed_files = set()


def ensure_collection(vector_size: int):
    global _collection_ready
    if _collection_ready:
        return
    existing = [c.name for c in qdrant.get_collections().collections]
    if COLLECTION not in existing:
        qdrant.create_collection(
            collection_name=COLLECTION,
            vectors_config=qmodels.VectorParams(
                size=vector_size, distance=qmodels.Distance.COSINE
            ),
        )
    _collection_ready = True


def embed_text(text: str):
    resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def case_to_text(case: dict) -> str:
    spec = case.get("specifica_strutturata") or case.get("spec") or {}
    esito = case.get("esito") or case.get("outcome") or "?"
    prompt = case.get("prompt", "")
    return (
        f"prompt={prompt} spec={json.dumps(spec, default=str)} "
        f"esito={esito} materiale={case.get('materiale', '?')} "
        f"macchina={case.get('macchina', '?')}"
    )


def index_dataset_once():
    """Scan DATASET_DIR for new/changed case files and index them."""
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
            ensure_collection(len(vector))
            qdrant.upsert(
                collection_name=COLLECTION,
                points=[
                    qmodels.PointStruct(
                        id=abs(hash(path)) % (2**63),
                        vector=vector,
                        payload={"text": text, "source_file": path},
                    )
                ],
            )
            indexed_cases.append(
                {
                    "file": path,
                    "text": text,
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            )
            _indexed_files.add(key)
        except Exception as e:
            print(f"embedding/upsert failed for {path}: {e}")


def index_loop():
    while True:
        index_dataset_once()
        time.sleep(POLL_INTERVAL_SECONDS)


threading.Thread(target=index_loop, daemon=True).start()


class ChatRequest(BaseModel):
    question: str
    top_k: int = 5


def search_context(question: str, top_k: int):
    try:
        vector = embed_text(question)
        ensure_collection(len(vector))
        hits = qdrant.search(collection_name=COLLECTION, query_vector=vector, limit=top_k)
        return [h.payload["text"] for h in hits]
    except Exception as e:
        print(f"semantic search failed, falling back to recent buffer: {e}")
        return [c["text"] for c in list(indexed_cases)[-top_k:]]


def call_ollama(prompt: str) -> str:
    resp = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={"model": CHAT_MODEL, "prompt": prompt, "stream": False},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def build_grounded_prompt(question: str, context: list) -> str:
    context_block = "\n".join(context) if context else "(no validated CALIPER cases found)"
    return (
        "You are a CAD verification assistant. Answer the question using "
        "ONLY the validated CALIPER cases (Livello 6 dataset) listed below "
        "as context. If the context does not contain the answer, say "
        "explicitly that no matching case exists - do not guess.\n\n"
        f"Context (validated cases):\n{context_block}\n\n"
        f"Question: {question}\nAnswer:"
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "indexed_cases": len(indexed_cases),
        "dataset_dir": DATASET_DIR,
        "chat_model": CHAT_MODEL,
        "embed_model": EMBED_MODEL,
    }


@app.get("/cases")
def cases(limit: int = 20):
    return list(indexed_cases)[-limit:]


@app.post("/reindex")
def reindex():
    before = len(indexed_cases)
    index_dataset_once()
    return {"indexed_before": before, "indexed_after": len(indexed_cases)}


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
