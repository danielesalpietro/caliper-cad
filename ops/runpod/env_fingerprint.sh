#!/usr/bin/env bash
# CALIPER — fingerprint dell'ambiente di esecuzione (JSON su stdout).
# Regola #3 del piano di recupero: ogni numero ambiente-dipendente
# (budget CPU, timeout) vale solo insieme al fingerprint dell'ambiente
# in cui e' stato misurato. Incluso obbligatoriamente in ogni harvest.
set -uo pipefail

json_escape() { sed 's/\\/\\\\/g; s/"/\\"/g' | tr '\n' ' '; }

REPO_DIR="${REPO_DIR:-/workspace/caliper-cad}"

nproc_v="$(nproc 2>/dev/null || echo '?')"
mem_v="$(free -m 2>/dev/null | awk '/^Mem:/{print $2"MB"}' || echo '?')"
gpu_v="$(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>/dev/null | head -1 | json_escape || echo 'nessuna GPU visibile')"
py_v="$(/opt/venv/bin/python --version 2>&1 | json_escape)"
cq_v="$(/opt/venv/bin/pip show cadquery 2>/dev/null | awk '/^Version:/{print $2}' || echo '?')"
node_v="$(node --version 2>/dev/null || echo '?')"
flowise_v="$(npm ls -g flowise --depth=0 2>/dev/null | grep -o 'flowise@[0-9.]*' || echo '?')"
ollama_v="$(ollama --version 2>/dev/null | json_escape || echo '?')"
qdrant_v="$(/opt/qdrant/qdrant --version 2>/dev/null | json_escape || echo '?')"
git_v="$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
branch_v="$(git -C "$REPO_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"

cat <<EOF
{
  "timestamp_utc": "$(date -u +%FT%TZ)",
  "hostname": "$(hostname)",
  "nproc": "$nproc_v",
  "memory": "$mem_v",
  "gpu": "$gpu_v",
  "python": "$py_v",
  "cadquery": "$cq_v",
  "node": "$node_v",
  "flowise": "$flowise_v",
  "ollama": "$ollama_v",
  "qdrant": "$qdrant_v",
  "repo_commit": "$git_v",
  "repo_branch": "$branch_v",
  "runpod_pod_id": "${RUNPOD_POD_ID:-non impostato}",
  "runpod_gpu_count": "${RUNPOD_GPU_COUNT:-non impostato}"
}
EOF
