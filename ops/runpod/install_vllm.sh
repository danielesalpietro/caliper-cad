#!/usr/bin/env bash
# CALIPER — installazione on-demand di vLLM sul pod (preferenza espressa
# dall'utente: a parita' di funzione, vLLM rispetto ad altri framework).
#
# PERCHE' NON E' NELL'IMMAGINE: vLLM + torch CUDA pesano ~8-10GB — dentro
# l'immagine raddoppierebbero il pull di ogni pod. Qui si installa in un
# venv DEDICATO SUL NETWORK VOLUME (/workspace/venv-vllm): si paga una
# volta sola e persiste tra pod diversi. Venv separato da /opt/venv per
# non toccare le dipendenze pinnate dei servizi CALIPER (cadquery ecc.).
#
# RUOLO NEL PIANO (docs/piano_recupero.md):
#   - M6 bring-up: lo stack esistente resta su Ollama cosi' com'e' — il
#     sistema sotto test e' quello scritto (stream-agent parla l'API
#     nativa Ollama, il chatflow L2.5 usa ChatOllama). Sostituirlo PRIMA
#     della prima esecuzione viva cambierebbe il sistema sotto test.
#   - M6-extra (test di fattibilita' Rischio #1): vLLM E' il framework di
#     serving scelto per i modelli locali candidati (7-14B) — endpoint
#     OpenAI-compatible su :8700, interrogabile da Flowise via nodo
#     ChatOpenAI con baseURL custom (che tra l'altro aggira il bug
#     documentato del nodo ChatOllama, rischio v10 in architettura).
#   - Se il bug ChatOllama si ripresenta live in M6, la migrazione di
#     L2.5 a un endpoint OpenAI-compatible servito da vLLM e' la via di
#     fuga designata — decisione gia' registrata, non da improvvisare.
#
# Uso: install_vllm.sh            (installa/aggiorna il venv)
#      serve_vllm.sh <modello-hf> (avvia il server — vedi in fondo)
set -euo pipefail

VLLM_VERSION="${VLLM_VERSION:-0.27.1}"   # ultima stabile PyPI al 2026-08-21
VENV=/workspace/venv-vllm

if [ ! -x "$VENV/bin/python" ]; then
  python3.11 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install "vllm==${VLLM_VERSION}"
"$VENV/bin/python" -c "import vllm; print('vLLM', vllm.__version__)"

cat > /workspace/serve_vllm.sh <<'EOF'
#!/usr/bin/env bash
# Avvia vLLM in OpenAI-compatible mode sulla porta 8700.
# Es.: ./serve_vllm.sh Qwen/Qwen2.5-Coder-7B-Instruct
set -euo pipefail
MODEL="${1:?Uso: serve_vllm.sh <modello-hf>}"
exec /workspace/venv-vllm/bin/python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --host 0.0.0.0 --port 8700 \
  --download-dir /workspace/hf-models
EOF
chmod +x /workspace/serve_vllm.sh
echo "OK — vLLM ${VLLM_VERSION} in $VENV; avvio: /workspace/serve_vllm.sh <modello-hf>"
