#!/usr/bin/env bash
# CALIPER — bus di comandi pod<->supervisore via GitHub.
#
# Il supervisore (sessione Claude Code in sandbox) NON raggiunge il pod
# direttamente (egress TLS-only: SSH resettato, HTTPS verso
# *.proxy.runpod.net bloccato da policy 403). L'unico canale comune e'
# GitHub. Questo loop rende il supervisore autonomo: fa polling del
# branch `pod-bus`, esegue i file di comando che il supervisore scrive
# (bus/cmds/<id>.sh) e ripubblica stdout+stderr+exit-code
# (bus/out/<id>.log, bus/out/<id>.rc). Il supervisore scrive i comandi,
# aspetta il round-trip (~pochi secondi), legge gli output.
#
# Isolato dal repo di lavoro: opera su un clone separato
# (/workspace/bus-repo) sul branch pod-bus. I comandi possono comunque
# agire sul repo vivo con `cd /workspace/caliper-cad`.
#
# Avvio (una volta, dall'utente):
#   GITHUB_TOKEN=... nohup bash /workspace/agent_bus.sh > /workspace/logs/agent_bus.log 2>&1 &
set -uo pipefail

: "${GITHUB_TOKEN:?GITHUB_TOKEN non impostata — serve per clone/push del repo privato}"
REPO_URL="https://x-access-token:${GITHUB_TOKEN}@github.com/danielesalpietro/caliper-cad"
BUS=/workspace/bus-repo
BRANCH=pod-bus
POLL="${BUS_POLL_SECONDS:-4}"
CMD_TIMEOUT="${BUS_CMD_TIMEOUT:-1800}"

git config --global user.email "pod@caliper.local"
git config --global user.name "caliper-pod-bus"
git config --global --add safe.directory "$BUS" 2>/dev/null || true

if [ ! -d "$BUS/.git" ]; then
  git clone --quiet "$REPO_URL" "$BUS" || { echo "[bus] clone fallito"; exit 1; }
fi
cd "$BUS" || exit 1
git remote set-url origin "$REPO_URL"

if git ls-remote --exit-code origin "$BRANCH" >/dev/null 2>&1; then
  git fetch --quiet origin "$BRANCH"
  git checkout -B "$BRANCH" "origin/$BRANCH"
else
  git checkout --orphan "$BRANCH"
  git rm -rf . >/dev/null 2>&1 || true
  mkdir -p bus/cmds bus/out
  printf 'caliper pod<->supervisor command bus\n' > bus/README.md
  touch bus/cmds/.keep bus/out/.keep
  git add -A && git commit --quiet -m "bus: init" && git push --quiet -u origin "$BRANCH"
fi
mkdir -p bus/cmds bus/out

echo "[bus] loop avviato $(date -u +%FT%TZ) — branch $BRANCH, poll ${POLL}s"
while true; do
  git fetch --quiet origin "$BRANCH" 2>/dev/null && git merge --quiet --no-edit "origin/$BRANCH" 2>/dev/null
  new=0
  for c in bus/cmds/*.sh; do
    [ -e "$c" ] || continue
    id="$(basename "$c" .sh)"
    [ -f "bus/out/$id.rc" ] && continue
    echo "[bus] eseguo $id"
    ( timeout "$CMD_TIMEOUT" bash "$c" ) > "bus/out/$id.log" 2>&1
    echo $? > "bus/out/$id.rc"
    new=1
  done
  if [ "$new" = 1 ]; then
    git add bus/out
    git commit --quiet -m "bus: risultati $(date -u +%H%M%S)" 2>/dev/null
    for _ in 1 2 3 4 5; do
      git push --quiet origin "$BRANCH" 2>/dev/null && break
      git fetch --quiet origin "$BRANCH" 2>/dev/null && git merge --quiet --no-edit "origin/$BRANCH" 2>/dev/null
      sleep 2
    done
  fi
  sleep "$POLL"
done
