# CALIPER su RunPod — immagine pod, template, harvest

Contesto e motivazioni: `docs/piano_recupero.md` (§3/M6, §4, §5). Qui il
riferimento operativo.

## Immagine

`ghcr.io/danielesalpietro/caliper-pod` — costruita e pubblicata da
`.github/workflows/publish-images.yml` a ogni push rilevante. Una sola
immagine con l'intero stack a versioni pinnate (i pod RunPod non hanno
Docker-in-Docker, il compose non può girarci dentro):

| Componente | Versione | Note |
|---|---|---|
| Python | 3.11 (deadsnakes) | parità con `python:3.11-slim` dei servizi |
| CadQuery | 2.8.0 | stessa del Dockerfile di `verifier-executor` |
| Flowise | 3.1.4 (npm, pinnata) | niente `latest` (review C10/P6) |
| Ollama | 0.32.15 | librerie GPU incluse nel tgz ufficiale |
| Qdrant | v1.19.0 | binario copiato dall'immagine ufficiale |
| Node | 20 | Flowise + Claude Code CLI |
| vLLM | 0.27.1 | **non nell'immagine**: `install_vllm.sh` on-demand |

Servizi CALIPER (verifier, executor/watcher, stream-agent) girano dal
checkout vivo del repo su `/workspace/caliper-cad` (aggiornabile senza
rebuild); lo snapshot baked in `/opt/caliper` è il fallback. Dashboard e
docker-socket-proxy non girano nel pod (senza Docker non hanno oggetto).

**Divergenza dichiarata dalla topologia di produzione**: nel pod
`verifier-executor` non è isolato in un container senza rete (restano i
RLIMIT per-job). Le verifiche di isolamento attive sono scope M7
(RTX 3090, `docker-compose.yml` + `docker-compose.ghcr.yml`).

## Template RunPod (campi da compilare, ~10 minuti)

| Campo | Valore |
|---|---|
| Container Image | `ghcr.io/danielesalpietro/caliper-pod:git-<shortsha>` (o `:latest` da develop) |
| Docker Command | *(vuoto — l'immagine ha già `CMD start.sh`)* |
| Container Disk | 60 GB |
| Volume | Network Volume `caliper-artifacts` (100 GB), mount path `/workspace` |
| Expose HTTP Ports | 3000, 3010, 6333, 8000, 8500, 8600, 8700, 11434 |
| Expose TCP Ports | **22** (SSH diretto — vedi "Accesso al pod" sotto) |
| GPU | 1× RTX 4090 24GB, Secure Cloud, datacenter EU con Network Volume |
| Env/Secrets | `FLOWISE_USERNAME`, `FLOWISE_PASSWORD`, `FLOWISE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, opzionale `CALIPER_GIT_REF` (default `develop`) |

## Accesso al pod — due canali, entrambi supportati

1. **Claude Code CLI dentro il pod** (canale operativo primario): già
   installata nell'immagine; la sessione M6 gira lì (web terminal →
   `claude`, autenticazione via `ANTHROPIC_API_KEY` dai Secrets).
2. **SSH diretto** (ispezione umana, scp, VS Code Remote): **nessuna
   chiave è inclusa nell'immagine** — sarebbe un leak su un registry.
   `start.sh` avvia `sshd` (porta 22, solo chiave, mai password) solo se
   trova la env `PUBLIC_KEY` con la tua chiave pubblica: RunPod la
   inietta automaticamente se hai registrato la chiave in *Account →
   Settings → SSH Public Keys*, oppure impostala a mano come env del
   template. Le host key del server sono generate al primo avvio e
   **persistite sul volume** (`/workspace/ssh`): stesso fingerprint tra
   pod diversi, niente warning a ogni ricreazione.

**Visibilità GHCR**: il primo push crea il package come privato. Per il
pull da RunPod senza credenziali: GitHub → Packages → `caliper-pod` →
Package settings → Change visibility → Public. In alternativa,
configurare le credenziali registry nel template RunPod.

## Ciclo di vita del pod

1. Avvio: `start.sh` prepara `/workspace`, clona/aggiorna il repo, crea
   i symlink (`/exec`, `/models`, `/gauges`, `/app`), scrive il
   fingerprint, avvia supervisord; i modelli Ollama (`granite4:1b`,
   `granite-embedding:30m`) vengono scaricati al primo avvio su
   `/workspace/ollama-models` e **persistono sul volume**.
2. Lavoro: la sessione Claude Code gira **dentro il pod** (web terminal
   → `claude`, con `ANTHROPIC_API_KEY` dai Secrets). Le trascrizioni dei
   test case vanno salvate in `/workspace/caliper-runs/incoming/`.
3. Harvest (obbligatorio, anche a metà sessione): `harvest.sh <tag>
   [--push]` — raccoglie log, retry_log, STEP generati, export chatflow
   Flowise, fingerprint; `MANIFEST.json` con sha256; esce non-zero se
   manca un artefatto obbligatorio. **Con harvest rosso il pod non si
   spegne.** `--push` committa la copia curata (testo/JSON) in
   `runs/<timestamp>-<tag>/` sul branch corrente.
4. Spegnimento: solo dopo harvest verde + push confermato.

## vLLM (preferenza dichiarata dall'utente)

`install_vllm.sh` crea un venv dedicato **sul volume**
(`/workspace/venv-vllm`, vLLM 0.27.1) e lo script
`/workspace/serve_vllm.sh <modello-hf>` (OpenAI-compatible su :8700).
Ruolo: serving dei modelli locali per il test di fattibilità Rischio #1
(M6-extra) e via di fuga designata se il bug ChatOllama di Flowise
(rischio v10) si ripresenta live — il nodo ChatOpenAI con baseURL custom
punta a vLLM. Il bring-up M6 resta su Ollama: è il sistema così come è
scritto, e M6 testa quello.
