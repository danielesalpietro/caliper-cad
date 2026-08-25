#!/bin/bash
# [Prompt to Part] Watcher persistente per lo slicing on-demand dal
# download G-code della pagina web -- stesso pattern job/result su
# volume condiviso gia' in uso da verifier-executor (watcher.py),
# scelto per lo stesso motivo: la dashboard non ha (e non deve avere)
# accesso Docker per invocare `docker compose run prusaslicer` da
# sola, e questo binario ha troppe dipendenze GTK/WebKit per essere
# copiato nell'immagine dashboard (verificato dal vivo: 19 librerie
# mancanti in python:3.11-slim, tra cui libgtk-3, libwebkit2gtk).
#
# Il servizio "prusaslicer" esistente in docker-compose.yml (CLI
# manuale via `docker compose run`, profilo "tools") NON e' toccato:
# questo e' un servizio parallelo con lo stesso identico binario,
# entrypoint diverso. network_mode: none anche qui -- comunica SOLO
# tramite /jobs, nessun motivo per avere rete.
set -u
JOBS_DIR="/jobs"
CONFIG="/config/caliper-pla.ini"

echo "prusaslicer-watcher: in ascolto su $JOBS_DIR"

while true; do
  for f in "$JOBS_DIR"/*.stl; do
    [ -e "$f" ] || continue
    base="${f%.stl}"
    done_marker="$base.done"
    if [ ! -e "$done_marker" ]; then
      if /app/prusa-slicer --export-gcode --load "$CONFIG" "$f" -o "$base.gcode" >"$base.log" 2>&1; then
        echo "ok" >"$done_marker"
      else
        echo "error" >"$done_marker"
      fi
    fi
  done
  sleep 1
done
