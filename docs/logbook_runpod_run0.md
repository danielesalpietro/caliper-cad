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

## Ripresa dopo l'incidente

Il pod **non** ha ricreato il container da zero: era un riavvio del
processo principale (probabile restart policy RunPod), non una
riprovisioning. Overlay effimero **sopravvissuto per intero**:
`build-essential`, Flowise npm-installato, `/root/.caliper_env` (tutte
e tre le credenziali), `/root/.git-credentials`. `start.sh` è ripartito
da capo (comportamento atteso: entrypoint del container), quindi il
repo su `/workspace/caliper-cad` era tornato su `develop` — riallineato
al branch di sessione con `git reset --hard
origin/claude/m6-runpod-bringup-run0` dopo aver pushato dal clone
locale (che non dipende dal pod).

`sshd` invece **non** è ripartito da solo (richiede `PUBLIC_KEY` nel
template RunPod, non impostata — la chiave era stata aggiunta a mano
su un file non persistente). Riavviato a mano dal web terminal RunPod.
Le host key SSH erano state rigenerate la prima volta su
`/workspace/ssh` (Network Volume MooseFS) e sshd le rifiutava
(`Permissions 0666 ... too open` — MooseFS forza permessi larghi,
stesso bug già risolto una volta nel commit `52fed61` e mai persistito
perché quel fix era anch'esso su overlay effimero). Fix di questo run:
`ssh-keygen -A` genera le host key in `/etc/ssh` (overlay, ma va bene:
l'identità della host key cambia a ogni riavvio pod comunque, non è un
problema di sicurezza per un accesso solo-chiave).

## Flowise — account e API key

Flowise 3.x usa un vero sistema di account (registrazione con
email/password, JWT, DB in `/workspace/flowise/database.sqlite`), non
le vecchie var d'ambiente `FLOWISE_USERNAME`/`PASSWORD` di Flowise
1.x/2.x (quelle nel `supervisord.conf` risultano quindi inefficaci con
questa versione — da segnalare, non bloccante). La creazione
dell'account (inserimento password) non è un'azione automatizzabile
lato mio: l'ha completata l'utente via browser (RunPod Connect, porta
3000, `/organization-setup`), poi generato una API key da
Settings → API Keys. Chiave salvata in `/root/.caliper_env`
(`FLOWISE_API_KEY`), verificata (`GET /api/v1/chatflows` → 200).

**Import chatflow L2.5**: `python3 services/flowise/import_chatflows.py`
(con `FLOWISE_URL=http://localhost:3000`, `FLOWISE_API_KEY` in env) →
`Importato: 'CALIPER - L2.5 Specification Normalization'`. Idempotente
per costruzione (salta se già presente), non ri-testato in questo run.

## Chatflow L2 costruiti e versionati

Costruiti programmaticamente (non a mano in UI) con uno script Python
(`docs/m6-run0-support/build_l2_chatflows.py`) che riusa come
template i nodi reali del chatflow L2.5 già versionato
(`promptTemplate`, `llmChain`, `structuredOutputParser`) e il nodo
`chatOpenAI` reale esportato da un chatflow "tmp" costruito
dall'utente in UI (unico modo per ottenere un nodo ChatOpenAI con
`credential` valorizzato — vedi sezione precedente sui permessi
dell'API key). Schema del nodo `chatOpenAI` preso da
`GET /api/v1/nodes/chatOpenAI` (salvato in
`docs/m6-run0-support/flowise-node-schema-chatOpenAI.json`).

- **`services/flowise/chatflows/l2-generation-cadquery.json`** —
  `"CALIPER - L2 Generation (CadQuery)"` (strategia `free_code`):
  Prompt Template -> ChatOpenAI (gpt-4o-mini, temp 0) -> LLM Chain,
  output testo libero (codice CadQuery che deve assegnare `result`,
  vincolo verificato in `services/verifier/executor/run_and_measure.py`).
- **`services/flowise/chatflows/l2-generation-sketch-first.json`** —
  `"CALIPER - L2 Generation (Sketch-First)"` (oggi implementato solo
  per `param_first`, condiviso con `sketch_first` per nome/env var
  come da `generate_and_verify.py`): stessa catena + Structured Output
  Parser con i 4 campi richiesti da
  `sketch_compiler.build_thread_sketch_spec_from_params()`
  (`major_diameter_mm`, `pitch_mm`, `engagement_length_mm`,
  `host_xy_mm`).

`manifest.json` aggiornato con entrambe le entry. Importati con lo
strumento di produzione esistente (`services/flowise/import_chatflows.py`,
non un client ad-hoc): `Importato: 'CALIPER - L2 Generation (CadQuery)'`,
`Importato: 'CALIPER - L2 Generation (Sketch-First)'`.

## Bug reale trovato e risolto — `call_flowise_l2` col parser strutturato

Test dal vivo (spec: `feature=thread, nominal=M6, pitch=1.0,
tolerance=0.3, engagement_length_mm=8.0`) su entrambi i chatflow appena
creati:

- **free_code**: risposta con campo `"text"` presente (atteso), codice
  Python plausibile ma con `.thread(...)` — metodo che **non esiste**
  nell'API reale di CadQuery (hallucination del modello). Non è un bug
  del chatflow: è esattamente il tipo di errore che il loop di retry
  deve intercettare a `/verify` — nessun fix qui, comportamento atteso
  della strategia `free_code` (motivo per cui esiste `param_first`).
- **param_first**: risposta `{"json": {"major_diameter_mm": 6,
  "pitch_mm": 1, "engagement_length_mm": 8, "host_xy_mm": 10}, ...}`
  — **nessun campo `"text"`**. `call_flowise_l2()` (riga 326 prima del
  fix) faceva `return data.get("text", "")`, quindi avrebbe restituito
  stringa vuota — `json.loads("")` in `generate_code_for_attempt()`
  avrebbe fallito con `JSONDecodeError` per OGNI chiamata param-first,
  a prescindere dalla qualità della risposta del modello. Conferma
  esattamente la "riserva onesta" già scritta nel codice
  (`generate_and_verify.py`: "verificata SOLO con call_flowise_l2
  mockata... nessuna istanza Flowise viva in questa sessione").

**Causa**: Flowise 3.1.4, quando l'LLM Chain ha uno Structured Output
Parser collegato, mette il risultato parsato in `data["json"]` nella
risposta di `/api/v1/prediction/{id}`, non in `data["text"]` (che
manca del tutto in quel caso). Comportamento non documentato nel
codice esistente (mai testato dal vivo, come dichiarato onestamente).

**Fix minimale** (`services/orchestrator/generate_and_verify.py`,
`call_flowise_l2`): preferisce `data["text"]` se presente (invariato
per `free_code`), altrimenti usa `json.dumps(data["json"])` se
presente, altrimenti stringa vuota (comportamento legacy per risposte
inattese). Nessun'altra riga toccata. Diff completo (unico file
applicativo modificato in questo run):

```diff
--- a/services/orchestrator/generate_and_verify.py
+++ b/services/orchestrator/generate_and_verify.py
@@ -323,7 +323,16 @@ def call_flowise_l2(chatflow_id: str, spec_json: str, temperature: float | None
     req.add_header("Authorization", f"Bearer {FLOWISE_API_KEY}")
     with urllib.request.urlopen(req, timeout=90) as resp:
         data = json.loads(resp.read().decode("utf-8"))
-    return data.get("text", "")
+    # [M6, verificato dal vivo contro Flowise 3.1.4] quando il
+    # chatflow usa uno Structured Output Parser (param_first/
+    # sketch_first), la prediction API restituisce il risultato in
+    # "json", non in "text" (che manca del tutto). "text" resta
+    # prioritario per free_code (LLM Chain senza output parser).
+    if "text" in data:
+        return data["text"]
+    if "json" in data:
+        return json.dumps(data["json"])
+    return ""
```

**Verde dopo il fix** (stessa chiamata, tramite
`call_flowise_l2()` patchata): `{"major_diameter_mm": 6, "pitch_mm":
1, "engagement_length_mm": 8, "host_xy_mm": 10}` — `json.loads()`
sul risultato ha successo, `host_xy_mm=10 > major_diameter_mm=6`
(vincolo di `build_thread_sketch_spec_from_params` rispettato).

Evidenza grezza delle chiamate (prima/dopo) non salvata in file a
parte in questo run (solo negli stdout di sessione, riportati sopra
per intero) — se serve per la relazione tecnica, rieseguibile in
30 secondi con i due chatflow ID: `ac6650ea-510a-4b9e-8d5e-0748c61368ca`
(free_code), `f16015b1-000a-4a5d-a8b2-aa5078d3d88c` (sketch-first).

## Artefatti salvati per la relazione tecnica

In `docs/m6-run0-support/`:
- `build_l2_chatflows.py` — script generatore dei due chatflow L2.
- `flowise-node-schema-chatOpenAI.json` — schema nodo ChatOpenAI da
  Flowise (`GET /api/v1/nodes/chatOpenAI`), usato per costruire i nodi.
- `fingerprint-m6.json` — fingerprint ambiente (vedi Passo 0 sopra),
  catturato dopo l'installazione di Flowise.

## Passo 2 — flag `--confirm` (Rischio #5)

Implementato con lo stesso metodo rosso→verde delle altre milestone:
`services/orchestrator/verify_confirm_flag.py` (nuovo, stesso stile di
mock di `verify_virtual_memory_loop_gate.py`: `call_flowise_l2`/
`resolve_chatflow_id` mockate, nessuna rete reale, `builtins.input`
mockato per simulare la risposta umana).

**Rosso (pre-fix)**: scenario A (`--confirm`, risposta `"n"`) — atteso
`return code=1, chiamate a L2=0`, osservato `return code=0, chiamate a
L2=1` (il flag non esisteva, veniva ignorato silenziosamente):
FALLITO.

**Fix**: in `main()`, subito dopo l'arricchimento della spec coi
preset (PRIMA di `resolve_chatflow_id` — un rifiuto non deve costare
nessuna chiamata di rete), se `--confirm` è tra gli argv stampa la spec
arricchita e chiede `y/n` via `input()`; se la risposta non è `y`,
ritorna 1 senza proseguire. Nessuna modifica al comportamento di
default (flag assente = comportamento legacy invariato, confermato
dagli altri `verify_*.py` esistenti che non lo passano mai e
continuano a passare).

**Verde (post-fix)**: entrambi gli scenari OK (`n` → 0 chiamate a L2,
`y` → generazione procede). Nessuna regressione sugli altri test
dell'orchestrator già esistenti (`verify_virtual_memory_loop_gate.py`,
`verify_retry_policy.py`, `verify_sketch_first_strategy.py` tutti OK).
Script aggiunto a `.github/workflows/regression.yml` (sezione M6
nuova, in fondo al file).

## Scoperta — SIGSEGV in `run_and_measure.py` su questo pod (rilevante per E2E-2/C8)

Rilanciando `verify_param_first.py` per controllare regressioni (non
collegato a `--confirm`) ho trovato che **fallisce con un crash nativo**,
non con un errore Python: il sottoprocesso che esegue
`services/verifier/executor/run_and_measure.py` (con l'interprete
corretto, `/opt/venv/bin/python3`, cadquery 2.8.0 presente e
funzionante per operazioni semplici — verificato con un box banale,
OK) muore con `SIGSEGV` durante la compilazione reale del codice
param-first (taglio elicoidale filettato via OCC). Non risolto con
`OMP_NUM_THREADS=1`/`OPENBLAS_NUM_THREADS=1` impostati nella shell
(ma `run_two_stage()` in `verify_param_first.py` costruisce il proprio
`env` per il sottoprocesso — non confermato se lo eredita).

**Non indagato oltre in questo run** (fuori scope rispetto al task
`--confirm` in corso): potenzialmente la stessa classe di problema
anticipata da C8 nell'handoff M6 ("verifica se OCC ignora quei limiti
e satura comunque i core" su 256 vCPU) — o un problema distinto
(crash vero, non solo lentezza/budget CPU). **Blocca potenzialmente
E2E-2** (richiede `/verify` reale su codice param-first) se si
ripresenta lì. Da investigare come primo passo prima di tentare
E2E-2/E2E-8, non assumere che sia già coperto dalla ricalibrazione C8
prevista — potrebbe essere un problema diverso e più serio (crash,
non solo budget).

## Prossimi passi

1. **Prioritario**: investigare il SIGSEGV sopra prima di tentare
   E2E-2 — potrebbe bloccare l'intera suite `/verify` reale, non solo
   il budget CPU di C8.
2. Eseguire la suite TC-E2E-1…9, con `harvest.sh` dopo ognuno.
3. Nota per il Dockerfile/supervisord.conf (fuori scope M6, da
   segnalare al supervisore): `FLOWISE_USERNAME`/`FLOWISE_PASSWORD` in
   `supervisord.conf` non hanno effetto su Flowise 3.x — l'account va
   creato una volta e persiste su `/workspace/flowise` (volume), quindi
   non è un problema per pod successivi finché il volume non cambia.
4. Segnalare al supervisore il fix a `call_flowise_l2` per revisione —
   applicato in autonomia in questa sessione seguendo la regola M6
   ("fallimento reale → fix minimale, documenta, dichiara"), ma è un
   cambio a codice applicativo condiviso, non solo a config/chatflow.
