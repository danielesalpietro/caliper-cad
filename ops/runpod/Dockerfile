# CALIPER — immagine monolitica per pod RunPod (M6, vedi docs/piano_recupero.md §3-4).
#
# PERCHE' UNA SOLA IMMAGINE: i pod RunPod sono container senza
# Docker-in-Docker — il docker-compose.yml (topologia di produzione, M7)
# non puo' girare dentro un pod. Questa immagine impacchetta gli stessi
# componenti, ALLE STESSE VERSIONI PINNATE, come processi supervisionati
# in un unico container. La topologia reale (isolamento verifier-executor,
# network_mode: none, socket proxy) resta scope di M7 sulla RTX 3090 —
# divergenza dichiarata, non taciuta (vedi piano, §3/M6).
#
# Build context: la RADICE del repository (serve services/ e config/):
#   docker build -f ops/runpod/Dockerfile .
# Pubblicata su GHCR da .github/workflows/publish-images.yml — mai
# buildata a mano su un pod.
#
# Versioni pinnate (verificate sui registry il 2026-08-21 — non "latest"):
#   Flowise 3.1.4 (npm) · Ollama 0.32.15 · Qdrant v1.19.0 ·
#   CadQuery 2.8.0 (stessa dei Dockerfile di produzione) · Node 20 ·
#   Python 3.11 (parita' con python:3.11-slim usata dai servizi).
#
# I modelli Ollama NON sono nell'immagine: vengono scaricati al primo
# avvio in /workspace/ollama-models (network volume) e persistono tra
# pod diversi — vedi start.sh.

# Base ubuntu:24.04 (glibc 2.39): necessaria per copiare il binario
# Qdrant dall'immagine ufficiale (build Debian recente) senza errori
# GLIBC. La GPU arriva dal runtime NVIDIA di RunPod (driver iniettati);
# Ollama porta con se' le proprie librerie ggml/CUDA nel tgz ufficiale —
# non serve l'immagine base CUDA completa.
FROM qdrant/qdrant:v1.19.0 AS qdrant-src
FROM ollama/ollama:0.32.15 AS ollama-src

FROM ubuntu:24.04

ARG FLOWISE_VERSION=3.1.4
ARG OLLAMA_VERSION=0.32.15
ARG CADQUERY_VERSION=2.8.0

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1

# --- Sistema: Python 3.11 (deadsnakes, parita' col resto del progetto),
# --- Node 20 (Flowise/Claude Code), libgl1 (OCP/CadQuery), utilita'.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq gnupg software-properties-common \
        libgl1 libglib2.0-0 openssh-server \
        build-essential \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-dev \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*
# build-essential: richiesto da node-gyp (better-sqlite3, dipendenza
# nativa di Flowise) — la sua assenza, mascherata dal vecchio "|| true",
# e' la causa radice di "Flowise/claude assenti dal pod" trovata nel
# run0 (docs/logbook_runpod_run0.md).

# --- Ollama: binario + librerie GPU copiati dall'immagine ufficiale
# --- pinnata via bind-mount (l'endpoint tgz di ollama.com ha risposto
# --- 404 per l'URL versionato al primo build CI — run 32530465233 —
# --- quindi stessa tecnica gia' usata per Qdrant: l'immagine Docker
# --- ufficiale e' l'artefatto versionato piu' affidabile). Il find
# --- tollera le differenze di layout tra versioni (/bin vs /usr/bin);
# --- i driver arrivano dal runtime NVIDIA del pod.
RUN --mount=type=bind,from=ollama-src,source=/,target=/mnt/ollama \
    set -e; \
    BIN="$(find /mnt/ollama/usr/bin /mnt/ollama/usr/local/bin /mnt/ollama/bin -maxdepth 1 -name ollama -type f 2>/dev/null | head -1)"; \
    test -n "$BIN" || { echo "binario ollama non trovato nell'immagine ollama-src"; find /mnt/ollama -maxdepth 3 -name 'ollama*' 2>/dev/null; exit 1; }; \
    cp -a "$BIN" /usr/bin/ollama; \
    if [ -d /mnt/ollama/usr/lib/ollama ]; then cp -a /mnt/ollama/usr/lib/ollama /usr/lib/ollama; fi; \
    if [ -d /mnt/ollama/usr/local/lib/ollama ]; then cp -a /mnt/ollama/usr/local/lib/ollama /usr/local/lib/ollama; fi; \
    /usr/bin/ollama --version

# --- Qdrant: binario + config + static copiati dall'immagine ufficiale
# --- pinnata (evita di indovinare i nomi degli asset di release).
COPY --from=qdrant-src /qdrant /opt/qdrant

# --- Flowise (pinnato — la review ha bocciato ':latest', C10/P6) e
# --- Claude Code CLI (la sessione M6 gira DENTRO il pod, vedi piano §4).
# NIENTE "|| true": nella prima versione mascherava il fallimento di
# npm install (node-gyp/better-sqlite3 senza toolchain) e l'immagine
# usciva "verde" ma SENZA Flowise ne' claude — bug reale trovato nel
# run0 (docs/logbook_runpod_run0.md). Ora: node-gyp puntato al
# python3.11 (il 3.12 di sistema non ha piu' distutils), install
# separate, verifica HARD di entrambe le CLI — se mancano, la build
# fallisce, come deve.
ENV NPM_CONFIG_PYTHON=/usr/bin/python3.11
RUN npm install -g flowise@${FLOWISE_VERSION} \
    && npm install -g @anthropic-ai/claude-code \
    && flowise --version \
    && claude --version

# --- Venv Python: stessi pin dei requirements dei servizi + cadquery
# --- del Dockerfile di verifier-executor + supervisor per il process tree.
RUN python3.11 -m venv /opt/venv \
    && /opt/venv/bin/pip install --default-timeout=300 \
        cadquery==${CADQUERY_VERSION} \
        fastapi==0.115.0 "uvicorn[standard]==0.30.6" pydantic==2.9.2 \
        qdrant-client==1.11.1 requests==2.32.3 httpx==0.27.2 \
        supervisor==4.2.5

# --- Snapshot del repo dentro l'immagine (coerenza: l'immagine e' sempre
# --- eseguibile anche offline). start.sh preferisce un clone vivo su
# --- /workspace (aggiornabile senza rebuild) e usa questo come fallback.
COPY . /opt/caliper

ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin

RUN chmod +x /opt/caliper/ops/runpod/*.sh

# /workspace e' il mount point del Network Volume RunPod (persistente).
WORKDIR /workspace

CMD ["/opt/caliper/ops/runpod/start.sh"]
