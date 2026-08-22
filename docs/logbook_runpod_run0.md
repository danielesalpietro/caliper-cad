# Logbook — RunPod run 0: bring-up ambiente (pre-M6)

Sessione di bootstrap/diagnosi sul pod RunPod (`root@213.192.2.75:40154`,
container `84bacb71e8d0`, RTX 3090 24GB, 256 vCPU) eseguita via SSH da
sessione supervisore esterna, prima di entrare nella suite TC-E2E di M6
(vedi [`handoff_m6.md`](handoff_m6.md), branch
`origin/claude/review-tecnica`, non ancora mergiato in `develop`).

Obiettivo di questo run: portare lo stack a uno stato in cui il Passo 0
(smoke test) e il Passo 1 (Flowise) dell'handoff M6 siano eseguibili.
Non è ancora l'esecuzione dei TC-E2E.

## Accesso

Chiave nuova generata in locale (`~/.ssh/id_ed25519_caliper_pod`),
pubblica aggiunta manualmente a `/root/.ssh/authorized_keys` sul pod
(la chiave `claude-supervisor-caliper` menzionata in una trascrizione
precedente non aveva la privata corrispondente disponibile in locale).
Connessione verificata: `ssh -p 40154 root@213.192.2.75`.

## Bug trovato e risolto — Flowise non partiva sotto supervisord

**Sintomo**: `ps aux` non mostrava alcun processo Flowise; porta 3000
non in ascolto. `supervisord.log`:

```
spawnerr: can't find command '/usr/bin/flowise'
gave up: flowise entered FATAL state, too many start retries too quickly
```

**Causa reale**: `/usr/bin/flowise` è un symlink
(`-> ../lib/node_modules/flowise/bin/run`) verso un pacchetto npm globale
mai installato con successo su questa istanza del pod — l'immagine
(`Dockerfile`, riga 76: `npm install -g flowise@3.1.4 ...`) prevede
l'installazione in build, ma su questo container il pacchetto risultava
assente (`/usr/lib/node_modules/` conteneva solo `npm` e `corepack`).
Non era un problema di permessi su `/workspace/flowise` (verificati:
`777`, scrivibili) come inizialmente ipotizzato.

Reinstallando a mano (`npm install -g flowise@3.1.4`) sono emersi due
problemi ambientali distinti, in sequenza:

1. **`distutils` mancante**: build nativa di `better-sqlite3` via
   node-gyp fallisce (`ModuleNotFoundError: No module named 'distutils'`)
   perché il `python3` di sistema è 3.12.3 (Ubuntu 24.04), che ha
   rimosso `distutils`. Fix: puntare node-gyp su `python3.11` (presente
   e con `distutils` ancora disponibile, seppur deprecato) via
   `npm_config_python=/usr/bin/python3.11`.
2. **Toolchain di compilazione assente**: dopo il fix Python, node-gyp
   falliva con `Error: not found: make` — l'immagine non ha
   `build-essential` (mancavano `make`, `g++`, `gcc`). Fix:
   `apt-get install -y build-essential`.

**Verifica**: `/usr/bin/flowise --version` risponde `3.1.4` dopo la
seconda reinstallazione (vedi esito sotto — completato durante questo
run).

**Nota per il Dockerfile**: se il problema si ripresenta su pod futuri
dalla stessa immagine, `build-essential` va aggiunto esplicitamente
alle dipendenze apt del Dockerfile (oggi assente), non solo assunto
presente nell'immagine Node base.

## Smoke test — stato ambiente (Passo 0 handoff M6)

`bash ops/runpod/env_fingerprint.sh` → salvato in
`/workspace/caliper-runs/incoming/fingerprint-m6.json`:

| Componente | Stato |
|---|---|
| ollama | `0.32.15`, modelli presenti: `granite4:1b`, `granite-embedding:30m` |
| qdrant | `1.19.0`, porta 6333 risponde 200 |
| verifier (8600) | `/health` → 200 |
| stream-agent (8500) | `/health` → 200 |
| flowise (3000) | non ancora verificato post-fix (vedi sezione successiva) |
| repo | clone reale (non snapshot baked): `develop`, tracking `origin/develop`, `fe64855` |
| CPU/RAM | 256 vCPU, 1031817MB RAM, GPU RTX 3090 24576MiB |

## Bloccanti reali per proseguire con M6 (non risolvibili in autonomia)

Credenziali/segreti assenti nell'ambiente del pod, verificati con
`[ -n "$VAR" ]` in shell non interattiva:

- **`OPENAI_API_KEY`** — assente. Bloccante per E2E-1/E2E-2 (nodo
  ChatOpenAI del chatflow L2) e per la costruzione dei chatflow L2
  stessi (Passo 1).
- **`GITHUB_TOKEN`** — assente. Non bloccante per il lavoro (confermato
  dalla sessione supervisore): serve solo per il bus diagnostico
  opzionale e per `harvest.sh m6-final --push` a fine lavoro. Va
  richiesto come PAT scope `repo` prima del push finale.
- **`ANTHROPIC_API_KEY`** — assente. Non applicabile a questo run:
  l'esecuzione avviene via shell SSH diretta da sessione esterna, non
  da una sessione `claude` lanciata dentro il pod.
- **`FLOWISE_USERNAME` / `FLOWISE_PASSWORD`** — assenti. Necessarie per
  il primo avvio di Flowise (crea l'account admin al primo boot).
  Impostabili con valori arbitrari in questo run (istanza Flowise
  vuota, nessun dato preesistente) e comunicate a chi userà poi la UI
  via RunPod *Connect* — non richiedono un segreto esterno al pod.

## Flowise — installazione completata

`npm_config_python=/usr/bin/python3.11 npm install -g flowise@3.1.4`
(dopo il fix `build-essential` sopra) è andata a buon fine: `3203
packages in 15m`, `/usr/bin/flowise --version` → `flowise/3.1.4
linux-x64 node-v20.20.2`. Non ancora verificato *up* su porta 3000 sotto
supervisord (vedi incidente sotto — l'ambiente si è disconnesso prima
della verifica).

## Credenziali configurate in questo run

Scritte in `/root/.caliper_env` sul pod (root-only, `chmod 600`), mai
salvate nella history di shell (trasmesse via stdin, non come argomenti
di processo):

- `OPENAI_API_KEY` — fornita dall'utente.
- `GITHUB_TOKEN` (PAT scope `repo`) — fornita dall'utente, verificata
  (200 su `api.github.com/repos/danielesalpietro/caliper-cad`), e
  configurata come `credential.helper store` sul repo del pod: push
  autonomi autorizzati dall'utente sul branch di sessione (mai
  `develop`, mai PR/merge).
- `FLOWISE_USERNAME=caliper-admin` / `FLOWISE_PASSWORD` (generata con
  `openssl rand -base64 18`) — non è un segreto esterno, è la prima
  registrazione admin di un'istanza Flowise vuota su questo pod.

**Se il pod va perso** (vedi incidente sotto), queste tre voci vanno
rifornite/rigenerate al prossimo bring-up: `OPENAI_API_KEY` e
`GITHUB_TOKEN` dall'utente, `FLOWISE_USERNAME/PASSWORD` rigenerabili
liberamente (istanza vuota).

## Branch di sessione

Creato `claude/m6-runpod-bringup-run0` da `origin/develop`
(`fe64855`), sia sul repo del pod (`/workspace/caliper-cad`) sia in
locale — per garantire che il lavoro sia recuperabile anche se il pod
resta irraggiungibile (vedi sotto).

## Incidente — riavvio supervisord ha causato la perdita della sessione SSH

Per far leggere `FLOWISE_USERNAME`/`FLOWISE_PASSWORD` (impostate *dopo*
l'avvio di supervisord, quindi non presenti nel suo `os.environ` — le
espansioni `%(ENV_x)s` di supervisord si risolvono sull'ambiente del
processo master al momento dello start del figlio, non vengono
ri-lette da una shell esterna) ho terminato il processo supervisord
esistente (`kill -TERM 495`) per rilanciarlo con le nuove variabili,
**senza ripassare da `start.sh`** (che avrebbe fatto
`git checkout -B develop origin/develop`, buttandomi fuori dal branch
di sessione appena creato sul pod).

**Esito**: la connessione SSH è caduta subito dopo il `kill`
(`Connection to ... closed by remote host`), e i tentativi di
riconnessione successivi hanno dato `Connection refused` /
`Connection timed out` sulla porta 40154 — nessuna risposta.

**Ipotesi (non confermata)**: `start.sh` termina con
`exec /opt/venv/bin/supervisord -n -c ...`, cioè *sostituisce* il
proprio processo con supervisord — se quel processo era anche
l'entrypoint/PID 1 del container, ucciderlo può aver fatto terminare
(o riavviare, a seconda della restart policy RunPod) l'intero
container. `df -h` di inizio sessione mostrava `overlay` (root fs, incl.
`/root`, `/usr/lib/node_modules`, i pacchetti apt installati) come
**non persistente**, a differenza di `/workspace` (Network Volume MooseFS,
persistente). Se il container è ripartito da immagine:

- **Persi (overlay)**: la mia chiave SSH in `/root/.ssh/authorized_keys`
  (aggiunta a mano, non via `PUBLIC_KEY` env — quel meccanismo di
  `start.sh` reinietta solo la chiave del template RunPod, se
  presente), Flowise appena installato (`/usr/lib/node_modules`),
  `build-essential`, `/root/.caliper_env` con le tre credenziali sopra,
  `sshd` avviato a mano dall'utente (fuori da supervisord).
- **Sopravvivono (Network Volume)**: repo git in
  `/workspace/caliper-cad` (branch di sessione incluso, se già
  committato — **non lo era** al momento dell'incidente, solo
  checkout locale senza commit), modelli Ollama, dati Qdrant, log in
  `/workspace/logs`, `fingerprint-m6.json` in
  `/workspace/caliper-runs/incoming/`.

**Lezione per il prossimo bring-up**: non terminare mai il processo
supervisord "principale" (verificare prima con `ps -o ppid= -p <pid>`
se è figlio diretto di PID 1, o se `start.sh` è ancora nel process
tree) per applicare variabili d'ambiente nuove — usare invece
`supervisorctl` con una sezione `[unix_http_server]`/`[supervisorctl]`
nel `.conf` (oggi assente: `supervisorctl status` dava "Error: .ini
file does not include supervisorctl section"), oppure scrivere le
variabili in un file sorgente **prima** che `start.sh`/supervisord
partano per la prima volta (richiede un riavvio pod comunque, ma
controllato dall'utente via dashboard RunPod, non un `kill` dall'interno).

**Stato a fine di questo run**: pod irraggiungibile via SSH
(`root@213.192.2.75:40154`, connection refused/timeout). Serve verifica
lato utente sulla dashboard RunPod (stato del pod: running/exited) ed
eventuale riavvio manuale. Se `/workspace` è davvero persistito, il
prossimo run riparte dal repo clonato, Ollama coi modelli già scaricati,
Qdrant coi dati esistenti — ma rifà da capo: chiave SSH,
`build-essential`, npm install Flowise, credenziali in
`/root/.caliper_env`.

## Prossimi passi

1. **Utente**: verificare stato pod su dashboard RunPod, riavviare se
   necessario.
2. Se il pod riparte da `start.sh`: verificare se
   `FLOWISE_USERNAME`/`FLOWISE_PASSWORD`/`OPENAI_API_KEY` sono ora nei
   Secrets del template RunPod (eviterebbe di doverle re-impostare a
   mano ogni volta) — altrimenti rifornirle come in questo run.
3. Rigenerare la chiave SSH locale e farla autorizzare di nuovo (o
   verificare se `PUBLIC_KEY` del template RunPod for basta).
4. Reinstallare `build-essential` + Flowise (stessa procedura
   documentata sopra, dovrebbe essere più veloce con la cache npm se
   `~/.npm` non è sul volume — verificare).
5. Da lì, riprendere da dove interrotto: Flowise up, chatflow L2/L2.5,
   flag `--confirm`, suite TC-E2E-1…9, C8, harvest, logbook_fase6.md,
   commento su issue #18.
