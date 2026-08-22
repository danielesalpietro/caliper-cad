#!/usr/bin/env bash
# CALIPER — avvio del pod RunPod (entrypoint dell'immagine caliper-pod).
#
# Idempotente: prepara il layout persistente su /workspace (Network
# Volume), aggiorna/clona il repo, crea i symlink che fanno funzionare i
# path hardcoded dei servizi (/exec, /models, /gauges, /app) senza
# toccare il codice, poi lancia supervisord con l'intero stack.
#
# Env riconosciute (via RunPod template/Secrets):
#   CALIPER_GIT_REF   branch/ref da usare (default: develop)
#   GITHUB_TOKEN      per clone/push se il repo e' privato (opzionale)
#   FLOWISE_USERNAME / FLOWISE_PASSWORD / FLOWISE_API_KEY
#   OPENAI_API_KEY    per il Chatflow L2 (configurata poi nella UI Flowise)
#   ANTHROPIC_API_KEY per Claude Code CLI dentro il pod
set -euo pipefail

WORKSPACE=/workspace
REPO_DIR="$WORKSPACE/caliper-cad"
REPO_URL_DEFAULT="https://github.com/danielesalpietro/caliper-cad"
CALIPER_GIT_REF="${CALIPER_GIT_REF:-develop}"

echo "== CALIPER pod start $(date -u +%FT%TZ) =="

# --- Layout persistente sul volume -----------------------------------
mkdir -p \
  "$WORKSPACE/logs" \
  "$WORKSPACE/ollama-models" \
  "$WORKSPACE/qdrant/storage" \
  "$WORKSPACE/flowise" \
  "$WORKSPACE/exec/jobs" "$WORKSPACE/exec/results" "$WORKSPACE/exec/checkpoints" "$WORKSPACE/exec/parts" \
  "$WORKSPACE/data/models" "$WORKSPACE/data/dataset" "$WORKSPACE/data/virtual_log" \
  "$WORKSPACE/gcode" \
  "$WORKSPACE/caliper-runs/incoming"

# --- Repo: clone vivo sul volume, fallback allo snapshot nell'immagine -
clone_url="$REPO_URL_DEFAULT"
if [ -n "${GITHUB_TOKEN:-}" ]; then
  clone_url="https://x-access-token:${GITHUB_TOKEN}@github.com/danielesalpietro/caliper-cad"
fi
if [ -d "$REPO_DIR/.git" ]; then
  git -C "$REPO_DIR" fetch origin "$CALIPER_GIT_REF" || echo "WARN: fetch fallito, uso il checkout esistente"
  git -C "$REPO_DIR" checkout -B "$CALIPER_GIT_REF" "origin/$CALIPER_GIT_REF" || true
elif git clone --branch "$CALIPER_GIT_REF" "$clone_url" "$REPO_DIR"; then
  echo "repo clonato ($CALIPER_GIT_REF)"
else
  echo "WARN: clone fallito — uso lo snapshot baked nell'immagine"
  cp -a /opt/caliper "$REPO_DIR"
fi
echo "repo: $(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo 'snapshot immagine')"

# --- Symlink per i path hardcoded dei servizi (nessuna modifica al codice):
#   /exec    -> volume condiviso job/result (app.py, watcher.py)
#   /models  -> pezzi STEP statici          (gauge_check.py, MODELS_ROOT)
#   /gauges  -> calibri versionati          (gauge_check.py, GAUGES_ROOT)
#   /app     -> executor                    (watcher.py lancia "python /app/...")
ln -sfn "$WORKSPACE/exec" /exec
ln -sfn "$WORKSPACE/data/models" /models
ln -sfn "$REPO_DIR/config/gauges" /gauges
ln -sfn "$REPO_DIR/services/verifier/executor" /app

# --- Fingerprint dell'ambiente (regola #3 del piano: numeri per ambiente)
"$REPO_DIR/ops/runpod/env_fingerprint.sh" > "$WORKSPACE/caliper-runs/fingerprint-$(date -u +%Y%m%d-%H%M%S).json" || \
  echo "WARN: fingerprint fallito (non bloccante)"

# --- Modelli Ollama: pull al primo avvio, poi persistono sul volume ---
export OLLAMA_MODELS="$WORKSPACE/ollama-models"
export OLLAMA_HOST=0.0.0.0:11434
(
  # in background: aspetta che ollama sia su, poi assicura i modelli
  for _ in $(seq 1 60); do
    curl -fsS http://localhost:11434/api/tags >/dev/null 2>&1 && break
    sleep 2
  done
  ollama pull granite4:1b || echo "WARN: pull granite4:1b fallito"
  ollama pull granite-embedding:30m || echo "WARN: pull granite-embedding:30m fallito"
) &

# --- SSH opzionale (accesso umano diretto; il canale operativo primario
# resta Claude Code CLI nel pod). NESSUNA chiave e' baked nell'immagine:
# la chiave PUBBLICA arriva a runtime via env PUBLIC_KEY — RunPod la
# inietta automaticamente dalle SSH Public Keys dell'account, oppure la
# si imposta a mano come env del template. Host key persistite sul
# volume: lo stesso fingerprint sopravvive tra pod diversi (niente
# warning MITM a ogni pod nuovo). Solo chiave, mai password.
if [ -n "${PUBLIC_KEY:-}" ]; then
  mkdir -p /root/.ssh && chmod 700 /root/.ssh
  printf '%s\n' "$PUBLIC_KEY" > /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
  mkdir -p "$WORKSPACE/ssh" /run/sshd
  for t in ed25519 ecdsa; do
    [ -f "$WORKSPACE/ssh/ssh_host_${t}_key" ] || ssh-keygen -q -t "$t" -f "$WORKSPACE/ssh/ssh_host_${t}_key" -N ""
  done
  /usr/sbin/sshd \
    -o "HostKey=$WORKSPACE/ssh/ssh_host_ed25519_key" \
    -o "HostKey=$WORKSPACE/ssh/ssh_host_ecdsa_key" \
    -o "PermitRootLogin=prohibit-password" \
    -o "PasswordAuthentication=no" \
    && echo "sshd avviato (porta 22, solo chiave)"
else
  echo "PUBLIC_KEY assente — sshd non avviato (resta il web terminal / Claude Code CLI)"
fi

# supervisord espande %(ENV_x)s e fallisce se la variabile non esiste:
# default espliciti per quelle opzionali.
export FLOWISE_USERNAME="${FLOWISE_USERNAME:-}"
export FLOWISE_PASSWORD="${FLOWISE_PASSWORD:-}"
export FLOWISE_SECRETKEY_OVERWRITE="${FLOWISE_SECRETKEY_OVERWRITE:-}"

echo "== avvio supervisord =="
exec /opt/venv/bin/supervisord -n -c "$REPO_DIR/ops/runpod/supervisord.conf"
