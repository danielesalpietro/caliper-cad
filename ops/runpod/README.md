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
| Env/Secrets | vedi **Variabili d'ambiente obbligatorie** sotto — vanno messe con **nome = valore** nel template |

## Variabili d'ambiente obbligatorie (nome = valore nel template)

**RunPod NON eredita variabili né Secrets: ogni variabile va dichiarata
esplicitamente** come *Environment Variable* del template, con nome **e**
valore. Una variabile solo nominata (senza valore) o assente non arriva
alla shell — è la causa dei blocchi visti sul primo pod. `start.sh` ora
stampa all'avvio quali mancano (cerca `[MANCA]` in
`/workspace/logs/…` o nell'output del terminale).

| Variabile | Bloccante? | Serve a |
|---|---|---|
| `OPENAI_API_KEY` | **SÌ (bloccante)** | generazione L2 reale (nodo ChatOpenAI del chatflow) — senza, E2E-1/E2E-2 non partono |
| `ANTHROPIC_API_KEY` | **SÌ (bloccante)** | la sessione `claude` che esegue M6 dentro il pod |
| `GITHUB_TOKEN` (scope `repo`) | sì per push/harvest e per il bus di supervisione | clone con push abilitato, `harvest.sh --push`, `agent_bus.sh` |
| `FLOWISE_USERNAME` / `FLOWISE_PASSWORD` | consigliata | login Flowise (altrimenti istanza aperta) |
| `FLOWISE_API_KEY` | dopo il primo login | import dei chatflow versionati e chiamate REST dell'orchestratore |
| `PUBLIC_KEY` | opzionale | avvio automatico di `sshd` (SSH umano) |
| `CALIPER_GIT_REF` | opzionale (default `develop`) | branch da cui parte il pod |

## Problemi noti sul primo pod e correzioni

Tutti osservati sul primo bring-up reale (2026-08-22) e corretti in
`ops/runpod/` — elencati perché la classe "giuntura mai esercitata →
bug" è esattamente ciò che la review (C9) aveva previsto.

| Problema osservato | Causa | Correzione | Stato |
|---|---|---|---|
| Variabili/Secrets non arrivano al pod | RunPod non eredita env: vanno messe con nome=valore nel template | tabella sopra + `start.sh` stampa `[MANCA]` all'avvio | ✅ documentato/diagnostica |
| `sshd` non parte ("bad permissions → no hostkeys") | host key su `/workspace` (MooseFS forza `0666`), sshd le rifiuta | host key in `/etc/ssh` via `ssh-keygen -A`, copia persistita sul volume | ✅ `start.sh` |
| `claude: not found` in shell | install npm globale non nel PATH interattivo / fallita in build | auto-reinstall + `PATH` npm globale via `/etc/profile.d/npmbin.sh` | ✅ `start.sh` |
| Flowise non compare nei processi | avvio lento (30–60s) o path non scrivibile | path già su `/workspace/flowise` nel `supervisord.conf`; controllare `/workspace/logs/flowise.log` | ⚠️ verificare per-pod |
| `groups: cannot find name for group ID 109` | GID iniettato da RunPod senza nome in `/etc/group` | cosmetico, nessun effetto | ℹ️ ignorabile |
| Supervisore non raggiunge il pod | egress della sandbox TLS-only (SSH reset) + policy 403 su `*.proxy.runpod.net` | canale via GitHub: `ops/runpod/agent_bus.sh` (bus di comandi) | ✅ workaround |

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
