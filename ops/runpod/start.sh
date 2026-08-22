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
set -uo pipefail

WORKSPACE=/workspace
REPO_DIR="$WORKSPACE/caliper-cad"
REPO_URL_DEFAULT="https://github.com/danielesalpietro/caliper-cad"
CALIPER_GIT_REF="${CALIPER_GIT_REF:-develop}"

# --selftest: valida la configurazione ed esce non-zero se manca una
# variabile BLOCCANTE — cosi' un pod mal configurato fallisce subito e a
# voce alta, invece di restare acceso a pagamento in stato inutile
# (azione correttiva #2, docs/retrospettiva_m6_bringup.md). Usato anche
# dal boot-smoke di CI (.github/workflows/pod-boot-smoke.yml).
if [ "${1:-}" = "--selftest" ]; then
  rc=0
  for v in OPENAI_API_KEY ANTHROPIC_API_KEY; do
    if [ -z "${!v:-}" ]; then echo "SELFTEST [BLOCCANTE MANCANTE] $v"; rc=1; else echo "SELFTEST [ok] $v"; fi
  done
  for v in GITHUB_TOKEN FLOWISE_USERNAME FLOWISE_PASSWORD; do
    [ -z "${!v:-}" ] && echo "SELFTEST [warn] $v assente (non bloccante)"
  done
  command -v claude >/dev/null 2>&1 && echo "SELFTEST [ok] claude CLI" || { echo "SELFTEST [manca] claude CLI"; rc=1; }
  command -v ollama >/dev/null 2>&1 && echo "SELFTEST [ok] ollama" || { echo "SELFTEST [manca] ollama"; rc=1; }
  [ -x /opt/qdrant/qdrant ] && echo "SELFTEST [ok] qdrant" || { echo "SELFTEST [manca] qdrant"; rc=1; }
  command -v flowise >/dev/null 2>&1 && echo "SELFTEST [ok] flowise" || { echo "SELFTEST [manca] flowise"; rc=1; }
  /opt/venv/bin/python -c "import cadquery" 2>/dev/null && echo "SELFTEST [ok] cadquery" || { echo "SELFTEST [manca] cadquery"; rc=1; }
  echo "SELFTEST rc=$rc"; exit $rc
fi

echo "== CALIPER pod start $(date -u +%FT%TZ) =="

# --- Diagnostica env: RunPod NON eredita le variabili, vanno messe
# --- ESPLICITAMENTE (nome=valore) come Environment Variables del
# --- template. Le stampiamo subito cosi' un pod mal configurato lo dice
# --- da solo nel log, invece di fallire in modo oscuro piu' avanti.
echo "-- Variabili d'ambiente attese (vedi ops/runpod/README.md) --"
for v in GITHUB_TOKEN OPENAI_API_KEY ANTHROPIC_API_KEY FLOWISE_USERNAME FLOWISE_PASSWORD FLOWISE_API_KEY PUBLIC_KEY; do
  val="${!v:-}"
  if [ -n "$val" ]; then echo "  [ok]      $v (${#val} char)"; else echo "  [MANCA]   $v"; fi
done

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

# --- Claude Code CLI: auto-heal. L'immagine la installa via npm -g, ma
# sul primo pod reale e' risultata "not found" (install fallita in build
# o bin globale fuori dal PATH interattivo). La reinstalliamo se manca,
# cosi' `claude` funziona da subito nel web terminal del pod.
if ! command -v claude >/dev/null 2>&1; then
  echo "claude CLI assente — reinstallo (@anthropic-ai/claude-code)"
  npm install -g @anthropic-ai/claude-code >/dev/null 2>&1 || echo "WARN: install claude fallita"
fi
# npm bin globale nel PATH anche per le shell interattive future.
NPM_BIN="$(npm prefix -g 2>/dev/null)/bin"
if [ -d "$NPM_BIN" ] && ! grep -q "$NPM_BIN" /etc/profile.d/npmbin.sh 2>/dev/null; then
  echo "export PATH=\"$NPM_BIN:\$PATH\"" > /etc/profile.d/npmbin.sh
fi
command -v claude >/dev/null 2>&1 && echo "claude: $(claude --version 2>/dev/null | head -1)" || echo "claude: NON disponibile"

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
  mkdir -p /run/sshd
  # Host key su /etc/ssh (filesystem del container), NON sul Network
  # Volume: MooseFS (mfs#...runpod.net su /workspace) forza i permessi a
  # 0666 e sshd rifiuta una host key world-readable ("bad permissions ->
  # no hostkeys available -> exiting") — bug reale osservato sul primo
  # pod. Persistiamo invece una copia sul volume e la reidratiamo con
  # chmod 600 in /etc/ssh, cosi' il fingerprint resta stabile tra pod
  # senza incorrere nei permessi del volume.
  if [ -d "$WORKSPACE/ssh" ] && ls "$WORKSPACE"/ssh/ssh_host_* >/dev/null 2>&1; then
    cp -f "$WORKSPACE"/ssh/ssh_host_* /etc/ssh/ 2>/dev/null || true
  fi
  ssh-keygen -A  # crea in /etc/ssh solo le host key mancanti, permessi corretti
  chmod 600 /etc/ssh/ssh_host_*_key 2>/dev/null || true
  mkdir -p "$WORKSPACE/ssh" && cp -f /etc/ssh/ssh_host_*_key /etc/ssh/ssh_host_*_key.pub "$WORKSPACE/ssh/" 2>/dev/null || true
  /usr/sbin/sshd \
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

# --- Bus di comando col supervisore (azione correttiva #4): avvio
# automatico se GITHUB_TOKEN c'e', cosi' il supervisore torna a poter
# guidare via GitHub senza paste manuali. Silenzioso se il token manca.
if [ -n "${GITHUB_TOKEN:-}" ] && [ -f "$REPO_DIR/ops/runpod/agent_bus.sh" ]; then
  if ! pgrep -f agent_bus.sh >/dev/null 2>&1; then
    GITHUB_TOKEN="$GITHUB_TOKEN" nohup bash "$REPO_DIR/ops/runpod/agent_bus.sh" \
      > "$WORKSPACE/logs/agent_bus.log" 2>&1 &
    echo "agent_bus avviato (bus GitHub col supervisore)"
  fi
else
  echo "GITHUB_TOKEN assente — agent_bus non avviato (supervisione via push manuali)"
fi

echo "== avvio supervisord =="
exec /opt/venv/bin/supervisord -n -c "$REPO_DIR/ops/runpod/supervisord.conf"
