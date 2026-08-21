#!/usr/bin/env bash
# CALIPER — harvest degli output del pod PRIMA dello spegnimento.
#
# Regola #4 del piano di recupero (docs/piano_recupero.md §5): nessun pod
# si spegne senza harvest verde. Due copie indipendenti:
#   1. /workspace/caliper-runs/<timestamp>-<tag>/  (Network Volume,
#      sopravvive al pod) — tutto, binari inclusi;
#   2. runs/<timestamp>-<tag>/ committata e pushata nel repo — solo gli
#      artefatti testuali/JSON curati (i binari restano sul volume,
#      elencati nel MANIFEST con sha256).
#
# Esce NON-ZERO se un artefatto obbligatorio manca: un harvest rosso
# significa "non spegnere il pod, capire prima cosa manca".
#
# Uso: harvest.sh <tag> [--push]
#   <tag>    etichetta del run (es. "tc-e2e-2")
#   --push   committa la copia curata sul branch corrente del repo e pusha
set -uo pipefail

TAG="${1:?Uso: harvest.sh <tag> [--push]}"
PUSH="${2:-}"
WORKSPACE=/workspace
REPO_DIR="$WORKSPACE/caliper-cad"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
RUN_DIR="$WORKSPACE/caliper-runs/$STAMP-$TAG"
FAILURES=0

mkdir -p "$RUN_DIR"
echo "== harvest '$TAG' -> $RUN_DIR =="

collect() { # collect <sorgente> <obbligatorio:yes|no>
  local src="$1" required="$2"
  if [ -e "$src" ]; then
    cp -a "$src" "$RUN_DIR/" && echo "OK   $src"
  else
    if [ "$required" = "yes" ]; then
      echo "MANCA (obbligatorio): $src"
      FAILURES=$((FAILURES + 1))
    else
      echo "assente (opzionale): $src"
    fi
  fi
}

# --- Artefatti obbligatori --------------------------------------------
"$REPO_DIR/ops/runpod/env_fingerprint.sh" > "$RUN_DIR/env_fingerprint.json" \
  || { echo "MANCA (obbligatorio): env_fingerprint"; FAILURES=$((FAILURES+1)); }
collect "$WORKSPACE/data/virtual_log/retry_log.jsonl" yes
collect "$WORKSPACE/logs" yes

# --- Artefatti attesi ma non sempre presenti --------------------------
collect "$WORKSPACE/exec/parts" no          # STEP generati (binari: restano solo sul volume)
collect "$WORKSPACE/caliper-runs/incoming" no  # trascrizioni TC-E2E lasciate dalla sessione
collect "$WORKSPACE/data/dataset" no        # eventuali fixture/casi L6 usati nel run

# --- Export dei chatflow Flowise (diventano codice versionabile) ------
if [ -n "${FLOWISE_API_KEY:-}" ]; then
  if curl -fsS -H "Authorization: Bearer $FLOWISE_API_KEY" \
       http://localhost:3000/api/v1/chatflows -o "$RUN_DIR/flowise_chatflows_export.json"; then
    echo "OK   export chatflows Flowise"
  else
    echo "MANCA (obbligatorio se Flowise e' parte del run): export chatflows"
    FAILURES=$((FAILURES + 1))
  fi
else
  echo "assente: FLOWISE_API_KEY non impostata — export chatflows saltato (dichiararlo nel logbook)"
fi

# --- MANIFEST con sha256 di tutto -------------------------------------
/opt/venv/bin/python - "$RUN_DIR" <<'PY'
import hashlib, json, os, sys
run_dir = sys.argv[1]
entries = []
for root, _, files in os.walk(run_dir):
    for name in sorted(files):
        if name == "MANIFEST.json":
            continue
        path = os.path.join(root, name)
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        entries.append({
            "path": os.path.relpath(path, run_dir),
            "bytes": os.path.getsize(path),
            "sha256": h.hexdigest(),
        })
with open(os.path.join(run_dir, "MANIFEST.json"), "w", encoding="utf-8") as f:
    json.dump({"files": entries, "count": len(entries)}, f, indent=2)
print(f"MANIFEST.json: {len(entries)} file")
PY

# --- Copia curata nel repo (solo testo/JSON, size-capped) su --push ---
if [ "$PUSH" = "--push" ]; then
  DEST="$REPO_DIR/runs/$STAMP-$TAG"
  mkdir -p "$DEST"
  # testo/JSON fino a 2MB per file; i binari restano sul volume
  find "$RUN_DIR" -type f \( -name '*.json' -o -name '*.jsonl' -o -name '*.log' -o -name '*.txt' -o -name '*.md' \) -size -2M \
    -exec cp --parents -t "$DEST" --no-preserve=mode {} + 2>/dev/null \
    || (cd "$RUN_DIR" && find . -type f \( -name '*.json' -o -name '*.jsonl' -o -name '*.log' -o -name '*.txt' -o -name '*.md' \) -size -2M | while read -r f; do
          mkdir -p "$DEST/$(dirname "$f")" && cp "$RUN_DIR/$f" "$DEST/$f"
        done)
  git -C "$REPO_DIR" add "runs/$STAMP-$TAG"
  git -C "$REPO_DIR" commit -m "runs: harvest $STAMP-$TAG (pod RunPod)" \
    && git -C "$REPO_DIR" push \
    && echo "OK   push copia curata su git" \
    || { echo "FALLITO: commit/push della copia curata"; FAILURES=$((FAILURES + 1)); }
fi

echo
if [ "$FAILURES" -gt 0 ]; then
  echo "== HARVEST ROSSO: $FAILURES artefatti obbligatori mancanti — NON spegnere il pod =="
  exit 1
fi
echo "== HARVEST VERDE: $RUN_DIR completo (volume). Push git: ${PUSH:-no} =="
