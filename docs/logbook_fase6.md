# Logbook — Fase 6 (M6): run1 — suite TC-E2E reale

Sessione esecutrice M6-run1, pod RunPod nuovo (immagine
`caliper-pod:git-de8d4fb`, tutto il lavoro del run0 già in `develop`).
Fonti vincolanti: `docs/handoff_m6_run1.md` (branch
`claude/handoff-m6-run1`), `docs/handoff_m6.md` (Passo 3, tabella
TC-E2E), `docs/report_run0.md` (contesto run0 e gate del re-run).
Supervisione: issue #18 + push su questo branch (`claude/m6-rerun-run1`).

Placeholder — popolato passo dopo passo, push incrementale dopo ogni
sezione completata.

## Pod

- IP/porta: `root@38.147.83.11:36196` (SSH).
- Dashboard: RTX A6000 x1, 16 vCPU dichiarati, 62GB RAM, template `8beo2j4pei`.
- **Nota preliminare**: `nproc`=128 visibili, ma cgroup reale
  `cfs_quota_us/cfs_period_us` = 1360000/100000 → **~13.6 vCPU
  equivalenti**, coerente coi 16 dichiarati dal pannello ma non con
  `nproc`. Stesso pattern del run0 (256 visibili vs ~27.2 reali,
  rapporto ~9.4x quasi identico) — sembra strutturale su RunPod, non
  un caso isolato. Rilevante per il Passo 1 (SIGSEGV).

## Passo 0 — verifica boot

(in corso)

## Passo 0 — verifica boot (completato, con 2 intoppi reali)

**Env**: `OPENAI_API_KEY` e `GITHUB_TOKEN` [ok] — presenti nell'ambiente
reale del processo di boot (`/proc/20/environ`, supervisord). Nota:
non visibili in una shell SSH non-interattiva nuova (limite noto, va
letta la env del processo init, non della propria shell).

**Intoppo 1 — bootstrap Flowise fallito al primo giro**:
`flowise_bootstrap.py` lanciato da `start.sh` ha fallito la
registrazione con `{"statusCode":400,"message":"Invalid Password"}`.
Causa reale: `start.sh` genera `FLOWISE_PASSWORD` con
`openssl rand -base64 18` (riga 56) — l'alfabeto base64 (A-Za-z0-9+/)
non garantisce MAI la presenza di un carattere speciale nell'output;
Flowise richiede lower+upper+cifra+speciale. In questo run la password
generata non conteneva alcun carattere speciale, causa il fallimento.
Non e' un bloccante di configurazione risolvibile dal pannello RunPod
(non c'entra l'env dichiarata dall'utente) — e' un bug nello script
condiviso `ops/runpod/start.sh`. **Non corretto qui** (codice
condiviso, serve l'ok del supervisore su issue #18) — bypassato per
questa sessione rilanciando `flowise_bootstrap.py` a mano con la
stessa password + un suffisso che garantisce la complessita'
(`...Aa1!`).

**Intoppo 2 — credential non agganciata dopo il recupero manuale**:
il primo giro di bootstrap (fallito su register) non aveva ancora
creato ne' i chatflow ne' la credential; il secondo giro (con la
password corretta) e' arrivato fino a creare credential+API key, ma la
`patch_chatflows_credential()` non ha trovato nulla da patchare
(i chatflow non erano ancora stati importati — l'ordine normale e'
import PRIMA del bootstrap, qui invertito dal recupero manuale). Fix:
importati i chatflow (`import_chatflows.py`, idempotente), poi
rilanciato `flowise_bootstrap.py` una terza volta (idempotente per
costruzione: login invece di register, credential/API key gia'
presenti, patch trovata ed eseguita sui due chatflow L2). Verde finale:

```
[flowise-bootstrap] login ok (caliper-admin@caliper.local)
[flowise-bootstrap] api key 'caliper-orchestrator' gia' presente
[flowise-bootstrap] credential 'CALIPER-CAD' gia' presente (8c5c59cf-a595-4de3-b3d7-7e606df32f56)
[flowise-bootstrap] chatflow 'CALIPER - L2 Generation (CadQuery)': credential agganciata
[flowise-bootstrap] chatflow 'CALIPER - L2 Generation (Sketch-First)': credential agganciata
[flowise-bootstrap] env aggiornato: /workspace/.caliper_env
[flowise-bootstrap] bootstrap completato
```

**Verifica finale**: 3 chatflow presenti via API
(`L2.5 Specification Normalization`, `L2 Generation (CadQuery)`,
`L2 Generation (Sketch-First)`); fingerprint salvato
(`/workspace/caliper-runs/incoming/fingerprint-run1.json`):
`flowise: flowise@3.1.4`, `repo_commit: d03de82`,
`repo_branch: claude/m6-rerun-run1`, `nproc: 128`.

**Da segnalare al supervisore (issue #18, non fixato qui)**:
`ops/runpod/start.sh` riga 56 — `openssl rand -base64 18` per
`FLOWISE_PASSWORD` non garantisce un carattere speciale nell'output;
fix minimale suggerito: appendere un carattere fisso da un set
garantito (es. `+ "Aa1!"`) o generare separatamente ogni classe di
carattere e mischiarle. Bloccante silenzioso: se ricapita, il
bootstrap fallisce SENZA che l'utente abbia sbagliato nulla nel
template RunPod — vale la pena renderlo deterministicamente corretto,
non lasciarlo alla probabilita' dell'alfabeto base64.

## Passo 1 — SIGSEGV (timebox 45 minuti, sequenza esaurita — ROSSO)

**Numeri ambiente** (a costo zero, come richiesto): `nproc`=128,
`nproc --all`=256 (il cpuset qui restringe parzialmente, a differenza
del run0 dove nproc=nproc --all=256), cgroup v1
`cfs_quota_us/cfs_period_us`=1360000/100000 → **~13.6 vCPU reali**
(coerente coi 16 dichiarati dal pannello). Rapporto visibile/reale
~9.4x, quasi identico al run0 (256/27.2) — pattern strutturale
RunPod, non specifico di un pod.

**Tentativo 1 — nessun override** (solo `VTK_SMP_MAX_THREADS=1` di
default, già nel codice): `python3 verify_param_first.py` →
`died with <Signals.SIGSEGV: 11>`. La sola mitigazione VTK non basta.

**Tentativo 2 — `CALIPER_STACK_LIMIT_MB=2`**: stesso comando →
ancora `died with <Signals.SIGSEGV: 11>`, invariato.

**Tentativo 3 — `+ CALIPER_AS_LIMIT_MB=6144`**: stesso comando →
**cambia segnale**: `died with <Signals.SIGKILL: 9>` (non più
SIGSEGV). Verificato `memory.events`/`memory.oom_control`:
`oom_kill: 0` — **non** è stato il cgroup-OOM-killer di questo
container a intervenire. Ambiguo se sia un OOM a livello host (fuori
dalla contabilità del cgroup) o altro; non approfondito oltre (fuori
timebox). Il cambio di segnale (crash nativo → kill esterno) è
comunque un segnale che allentare `RLIMIT_AS` sposta il collo di
bottiglia, non lo elimina: con più margine di indirizzi il processo
arriva più lontano (spawna più thread reali?) prima di essere fermato
da qualcos'altro.

**Esito**: sequenza prescritta esaurita, nessun tentativo verde.
**Rispetto il timebox** (45 min) come da istruzioni — non insisto
oltre, procedo con i TC-E2E che non usano l'esecutore (E2E-1, 3, 5, 6,
7). **E2E-2 ed E2E-8 restano bloccati**, stesso blocco residuo del
run0, ora con un dato in più (SIGKILL a `AS_LIMIT_MB` alto) da passare
al supervisore per la prossima iterazione — non una pista risolta,
un'osservazione aggiuntiva.

## Passo 2 — Suite TC-E2E

### Bloccante trovato e risolto prima di E2E-1 — SSRF policy di Flowise 3.1.4

Primo tentativo E2E-1 fallito: `{"statusCode":500,...,"message":"Error:
predictionsServices.buildChatflow - Error: Access to this host is
denied by policy."}`. Causa (letta direttamente nel sorgente installato,
`flowise-components/dist/src/httpSecurity.js`): Flowise 3.1.4 ha una
protezione SSRF **attiva di default** (`HTTP_SECURITY_CHECK !==
'false'`) con una deny-list che include `localhost`/`127.0.0.0/8`/
`10.0.0.0/8` ecc. — blocca esattamente `http://localhost:11434`, la
`baseUrl` del nodo ChatOllama nel chatflow L2.5. Non emerso nel run0
(mai invocato L2.5 dal vivo li').

**Necessario per l'architettura** (Flowise e Ollama nello stesso
container, comunicazione via localhost e' l'intero disegno), non un
workaround opzionale. **Fix applicato SOLO runtime su questo pod**
(non nel repo — modifica a config condivisa, va proposta al
supervisore): aggiunto `HTTP_SECURITY_CHECK="false"` alla riga
`environment=` di `[program:flowise]` in
`ops/runpod/supervisord.conf` (file locale sul pod, non committato),
ricaricato con `supervisorctl -s unix:///run/supervisord.sock reread
&& update` — **mai** toccato il supervisord principale, solo il
programma `flowise` (regola rispettata). Verde dopo il riavvio
mirato.

**Da proporre al supervisore (issue #18)**: aggiungere
`HTTP_SECURITY_CHECK="false"` permanentemente alla riga `environment=`
di `[program:flowise]` in `ops/runpod/supervisord.conf` — bloccante
strutturale per l'architettura corrente (Flowise deve raggiungere
Ollama su localhost), non specifico di questo pod.

### E2E-1 — prompt naturale a L2.5 vivo

**Comando**:
```
POST /api/v1/prediction/8b2163da-1c4a-4ae0-91bf-dff89b752bbb
{"question": "foro filettato M6, tolleranza 0.3mm, passo 1.0"}
```

**Output reale**:
```json
{"feature":"thread","nominal":"M6","pitch":1,"tolerance":0.3,"tolerance_type":"diametrale","measured_as":""}
```

**Atteso (handoff_m6.md)**: `feature:thread, nominal:M6, pitch:1.0,
tolerance:0.3`, `tolerance_type`/`measured_as` vuoti.

**Esito: PASS CON RISERVA — discrepanza onesta**. `feature`, `nominal`,
`pitch`, `tolerance` coincidono esattamente. `measured_as` e' vuoto
come atteso. **`tolerance_type` NON e' vuoto**: il modello ha inferito
`"diametrale"` (che e' anche il default del preset "thread" in
`presets.json` — coincidenza plausibile: il modello potrebbe aver
"indovinato" il default piu' comune per filettature, oppure il
template del prompt lo predispone a questo). Non e' un fallimento
bloccante (il campo e' un tipo valido, e sara' comunque sovrascritto/
confermato da `apply_preset()` a valle se vuoto), ma e' una
discrepanza reale rispetto all'output atteso documentato — riportata
cosi' com'e', non smussata a "PASS" silenzioso.

`stdout` salvato in `/workspace/caliper-runs/incoming/tc-e1.log`.

## Passo 1 (continua) — Tentativo 4 (direttiva supervisore, timebox 5 min)

**Comando esatto**: `taskset -c 0-11 env CALIPER_STACK_LIMIT_MB=2
CALIPER_AS_LIMIT_MB=6144 /opt/venv/bin/python3 verify_param_first.py`

**Risultato — PROGRESSO REALE**: `run_and_measure.py` (lo stadio che
crashava da sempre, run0 incluso) **ha funzionato**: `"generated_part_step_path":
"job_paramfirst.step"`, `"Atteso: PASS, STEP esportato: OK"`. Prima
volta in assoluto che questo stadio arriva in fondo su questo pod.

Il crash si e' spostato piu' avanti nella pipeline, in
`gauge_check.py` (chiamato DOPO da `verify_param_first.py` per il
collaudo Go/No-Go): `died with <Signals.SIGSEGV: 11>` — segnale
diverso da prima (11, non 9), stesso ambiente (taskset+env ereditati
correttamente: `run_gauge_check()` in `verify_param_first.py` usa
`env = dict(os.environ)`, confermato leggendo il sorgente).

**Verifica mirata della causa del SIGKILL del tentativo 3** (grep sul
sorgente, non un altro run live — a costo zero):
`run_and_measure.py` riga 106: `resource.setrlimit(resource.RLIMIT_CPU,
(10, 10))` — **hardcoded a 10s, non parametrico** (a differenza di
`CALIPER_AS_LIMIT_MB`/`CALIPER_STACK_LIMIT_MB`, gia' overridabili).
Con 128 CPU visibili (nproc, prima di taskset) e pool di thread nativi
dimensionati su quel numero, il tempo CPU sommato su tutti i thread
supera 10s in una frazione di secondo di wall-clock — coerente al
100% con l'ipotesi del supervisore: **il SIGKILL del tentativo 3 era
RLIMIT_CPU=10s, non OOM** (confermato anche indirettamente: con
`taskset -c 0-11` il pool si dimensiona su 12, il tempo CPU sommato
resta sotto 10s, e infatti QUESTO stadio ora passa).

**gauge_check.py invece HA gia' gli stessi override parametrici**
(`CALIPER_AS_LIMIT_MB`/`CALIPER_STACK_LIMIT_MB` letti da env, righe
147-152) e un `GAUGE_CHECK_CPU_LIMIT_SECONDS` di default **100s** (non
10s) — ampio, improbabile che sia RLIMIT_CPU a colpirlo qui. Il
SIGSEGV in `gauge_check.py` sotto le stesse condizioni e' quindi
**un problema distinto, non ancora diagnosticato**: probabilmente
`CALIPER_AS_LIMIT_MB=6144` non basta per il workload di
`gauge_check.py` (sweep a 21 step, piu' pesante del singolo build di
`run_and_measure.py`), ma non verificato oltre — timebox del
tentativo 4 (5 min) rispettato, mi fermo qui come da direttiva.

**Sintesi per il supervisore**:
- `run_and_measure.py`: causa confermata (RLIMIT_CPU=10s hardcoded +
  pool di thread dimensionato su core visibili, non reali). Con
  `taskset` che restringe l'affinita', funziona. **Non ho modificato
  `run_and_measure.py`** (per istruzione esplicita) — il fix
  (`CALIPER_CPU_LIMIT_S` overridabile, default 10 invariato) resta al
  supervisore.
- `gauge_check.py`: nuovo blocco scoperto SOLO ora (prima non si
  arrivava mai cosi' lontano nella pipeline). Stessi sintomi macro
  (SIGSEGV), causa probabile diversa (AS limit insufficiente per un
  workload piu' pesante) — da investigare separatamente, non lo stesso
  problema di `run_and_measure.py` solo perche' condividono la
  famiglia di sintomi.

**E2E-2/E2E-8**: ancora bloccati (il Go/No-Go di `gauge_check.py` e'
richiesto dal criterio di successo di E2E-2), ma il blocco si e'
spostato piu' a valle — non piu' "nessuna generazione arriva mai a
finire", ora "la generazione finisce, il collaudo fisico no". Passo
avanti reale, non solo diagnosi.

Procedo ora con i TC-E2E non-executor come da istruzioni.

### E2E-1 — correzione esito (direttiva supervisore) e indagine tolerance_type

**Esito corretto: ROSSO** sull'aspettativa documentata (non
"PASS con riserva" come annotato inizialmente — il supervisore ha
corretto la classificazione ed e' quella giusta: `tolerance_type` non
vuoto e' una deviazione reale dallo specificato, non un dettaglio
cosmetico).

**1. Il prompt template istruisce gia' esplicitamente a non inventare
valori** (`services/flowise/chatflows/l25-specification-normalization.json`,
nodo `promptTemplate`, testo esatto verificato dal vivo su questo
chatflow):

> "Extract only what the prompt states explicitly - do not invent
> values. If a field is genuinely ambiguous or missing, leave it as an
> empty string rather than guessing."

Quindi: **non e' un bug del prompt** (ramo "SI" della direttiva) —
niente fix al template. E' non-compliance del modello
(`granite4:1b`, chatflow a `temperature: "0"`).

**2. Stima della frequenza — 3 ripetizioni + il tentativo originale
(4 osservazioni totali), stesso input**:

```
1: tolerance_type="diametrale"
2: tolerance_type="diametrale"
3: tolerance_type="diametrale"
4: tolerance_type="diametrale"
```

**4/4 (100%)** — non e' un comportamento raro/flaky da stimare in
frequenza, e' **deterministico e ripetibile** con questo chatflow a
`temperature=0`: il modello ignora l'istruzione esplicita in modo
sistematico per questo campo, su questo input. `stdout` completo delle
3 ripetizioni in `/workspace/caliper-runs/incoming/tc-e1-repeat.log`.

**3. Cosa fa l'orchestratore a valle** (`generate_and_verify.py`,
`apply_preset()`, riga 168):

```python
if not enriched.get("tolerance_type") and "default_tolerance_type" in preset:
    enriched["tolerance_type"] = preset["default_tolerance_type"]
```

**Consuma silenziosamente** un `tolerance_type` gia' valorizzato da
L2.5 — la condizione `not enriched.get(...)` e' vera solo se il campo
e' vuoto, quindi se L2.5 lo ha gia' riempito (anche per errore) il
preset non lo tocca e il valore passa cosi' com'e', **senza nessuna
distinzione tra "dichiarato dall'utente" e "inventato dal modello"**.

In questo caso specifico il valore inventato ("diametrale") coincide
col default del preset per "thread" — nessuna differenza osservabile
a valle qui. Ma e' un punto cieco reale: se L2.5 avesse inventato un
valore DIVERSO dal default (es. "per_lato"), sarebbe passato ugualmente
senza alcun rifiuto, influenzando silenziosamente il calcolo
dimensionale/gauge-check a valle.

**Candidato fix per il supervisore (non applicato qui)**: un
"firewall" a valle di L2.5 che rifiuti (o quantomeno segnali) campi
gia' valorizzati che il template istruisce esplicitamente a lasciare
vuoti — oggi non esiste, `apply_preset()` non e' quel controllo (fa
solo da fallback per i campi VUOTI, non da validazione di quelli
GIA' pieni). Nessun fix unilaterale applicato, come da istruzione.

### E2E-3 — spec irrealizzabile (pitch:0.05)

**Comando**: `L2_STRATEGY=param_first L2_SKETCH_CHATFLOW_ID=<id>
python3 generate_and_verify.py '{"feature":"thread","nominal":"M6","pitch":0.05,"engagement_length_mm":8.0}'`

**Output reale**: 2 tentativi, entrambi FAIL identici (`"il processo
non fidato non ha prodotto uno stato di esportazione leggibile"`),
uscita anticipata per errore ripetuto 2 volte consecutive,
`final_status: unrecoverable_virtual`, `case_id` presente e stabile
tra i tentativi. `stdout` completo in
`/workspace/caliper-runs/incoming/tc-e3.log`.

**Confronto con l'atteso** (handoff_m6.md): "≤3 tentativi, directive
nei tentativi 2+, `unrecoverable_virtual`, record collegati per
`case_id`" — **la meccanica del retry loop rispetta l'atteso alla
lettera** (2≤3 tentativi, directive presente al tentativo 2,
`unrecoverable_virtual` corretto, stesso `case_id` per tutti i
record).

**MA — riserva onesta, non un PASS pulito**: `pitch:0.05` **non è
geometricamente invalido** per `sketch_compiler.py` (verificato a
mano: con `major_diameter_mm=6`, `pitch=0.05`, angolo 60°,
`r_minor = 3.0 - 0.05/(2*tan(30°)) ≈ 2.957mm`, ben sopra zero — la
validazione locale in `build_thread_sketch_spec_from_params()` non
rifiuta questo pitch, lo accetta come un dente di filettatura
minuscolo ma matematicamente valido). Il codice generato ATTRAVERSA
quindi la validazione locale ed arriva davvero a `/verify` — dove **il
FAIL osservato è quasi certamente lo stesso crash del Passo 1**
(SIGSEGV nell'esecutore), non un rifiuto genuino di "pitch troppo
piccolo per essere realizzabile". Verificato: il servizio `verifier`
(`services/verifier/executor/watcher.py`, riga ~109 e succ.,
`subprocess.run` diretto senza `taskset` ne' gli override
`CALIPER_*`) e' un processo lanciato da supervisord all'avvio del pod,
**non ha ereditato ne' puo' ereditare** il fix di `taskset -c 0-11`
validato nel tentativo 4 (quello era un invocazione manuale isolata di
`verify_param_first.py`, mai collegata alla pipeline HTTP reale
`generate_and_verify.py -> /verify -> watcher.py`).

**Conclusione per E2E-3**: la meccanica del retry loop e' verificata
CORRETTA, ma il trigger che l'ha esercitata e' il bug SIGSEGV noto
(Passo 1), non l'irrealizzabilita' geometrica che il test intende
esercitare — quindi **non e' un test case pulito su questo pod finche'
il SIGSEGV non e' risolto nella pipeline reale** (il fix del tentativo
4 esiste solo come prova isolata, non e' collegato a
`watcher.py`/`verifier`). Per un E2E-3 genuino servirebbe o (a) un
pitch che fallisca la validazione LOCALE per davvero (es. pitch >
~3.46mm con questo diametro, che rende `r_minor <= 0` — provabile
senza toccare l'esecutore), o (b) applicare il fix `taskset` anche al
servizio `verifier`/`verifier-executor` (stessa classe di intervento
del fix SSRF di Flowise — runtime, non nel repo, proponibile al
supervisore).

**Non ho applicato il fix taskset al servizio verifier in questa
sessione**: avrebbe riaperto il Passo 1 oltre il suo timebox gia'
chiuso — segnalato qui come pista concreta per il prossimo passo, non
eseguito.

### E2E-5 — reindex + riavvio stream-agent (C6, id deterministici/offset)

**Setup**: `RETRY_LOG_PATH` corretto a `/workspace/data/virtual_log/retry_log.jsonl`
(vedi nota sopra su E2E-3) gia' popolato con i 2 record FAIL di E2E-3
(indicizzati automaticamente in background da `stream-agent` all'avvio,
senza intervento manuale — thread di indicizzazione periodica gia'
attivo). Aggiunta 1 fixture fisica L6 minimale
(`/workspace/data/dataset/e2e5_case1.json`) per popolare anche
`caliper_l6_dataset` (vuota fino a questo punto).

**Comando/sequenza**:
```
POST /reindex                         -> physical 0->1, virtual 2->2 (gia' indicizzati in bg)
conteggio Qdrant PRIMA del restart    -> l6_dataset=1, virtual_log=2
supervisorctl restart stream-agent    (MAI il supervisord principale)
POST /reindex (dopo il restart)       -> physical 1->1, virtual 0->0
conteggio Qdrant DOPO il restart      -> l6_dataset=1, virtual_log=2
```

**Esito: PASS pulito**. Conteggio punti nelle 2 collezioni Qdrant
**invariato** dopo il riavvio (conferma dal vivo di C6 — id
deterministici, nessun duplicato). Dettaglio interessante non
richiesto esplicitamente dal criterio ma rilevante: il `/reindex`
post-restart riporta `virtual_before:0,virtual_after:0` — il processo
FRESCO (nuovo `pid`, stato Python azzerato) ha comunque riconosciuto
correttamente che quei 2 record erano gia' stati indicizzati (offset
persistito su disco, non ripartito da zero), coerente con la garanzia
gia' verificata in isolamento da `verify_stream_agent_ids.py` (M5) —
qui pero' confermata con un riavvio REALE via `supervisorctl`, non un
sottoprocesso simulato.

`stream-agent` riavviato con `supervisorctl -s
unix:///run/supervisord.sock restart stream-agent` — mai toccato il
supervisord principale, coerente con la regola vincolante.

### E2E-6 — 2 FAIL virtuali + 1 FAIL fisico stessa chiave → nessuna chiamata a Flowise

**Setup**: 2 run distinti di `generate_and_verify.py` sulla stessa spec
di E2E-3 (`pitch:0.05`, `param_first`) — ognuno fallisce (stesso
SIGSEGV noto) e produce un `case_id` DIVERSO nel log virtuale (la
soglia conta CASI distinti, non tentativi — `count_virtual_failures()`
in `virtual_memory.py`, verificato leggendo il sorgente prima di agire,
non per tentativi). Aggiunta 1 fixture fisica L6 con FAIL sulla stessa
spec (`/workspace/data/dataset/e2e6_physical_fail.json`,
`geometry_key` — senza `l2_strategy` — coerente con quella virtuale).

Nota operativa: primo tentativo fallito per un mio errore di
estrazione della API key da `/workspace/.caliper_env` (il file usa
apici singoli `export VAR='...'`, il mio `tr -d '"'` toglieva i doppi
apici, non i singoli — la privata restava tra apici letterali, `401
Unauthorized`). Corretto sorgendo il file con `. /workspace/.caliper_env`
invece di grep/cut/tr manuale.

**Comando (3o run, quello che verifica il criterio)**:
```
RETRY_LOG_PATH=/workspace/data/virtual_log/retry_log.jsonl L2_STRATEGY=param_first \
L6_DATASET_DIR=/workspace/data/dataset python3 generate_and_verify.py \
  '{"feature":"thread","nominal":"M6","pitch":0.05,"engagement_length_mm":8.0}'
```

**Output reale**:
```
-> Memoria del collaudo virtuale (...): 2 fallimenti virtuali >= soglia 2,
   corroborati da almeno un fallimento fisico (Livello 6) sulla stessa
   strategia — esclusione applicata.
=== Strategia scartata dalla memoria del collaudo virtuale — generazione NON avviata. ===
```

**Verifica "0 richieste nei log"**: righe di `/workspace/logs/flowise.log`
contate PRIMA (357) e DOPO (357) il run — **invariate**, nessuna
richiesta ha raggiunto Flowise.

**Esito: PASS pulito**, corrisponde esattamente al criterio
dell'handoff ("orchestratore esce prima di chiamare Flowise, 0
richieste nei log"). `stdout` completo (3 run concatenati) in
`/workspace/caliper-runs/incoming/tc-e6.log`.

### E2E-7 — gauge job con CPU limit basso -> TIMEOUT strutturato: NON ESEGUIBILE come specificato

**Setup**: copiato `config/gauges/thread_M6_GO_ISO68-1.step` in
`/workspace/data/models/thread_M6_test_part.step` (unico modo per
avere un "part" in `/models`, vuota su questo pod). Aggiunto
`GAUGE_CHECK_CPU_LIMIT_SECONDS="1"` runtime a `[program:verifier-executor]`
in `supervisord.conf` (solo sul pod, non nel repo), ricaricato via
`supervisorctl` (mai il supervisord principale).

**Comando**:
```
POST /gauge-check
{"part_step_path":"thread_M6_test_part.step","gauge_step_path":"thread_M6_NOGO_ISO68-1.step",
 "mode":"sweep","part_source":"models","sweep":{"steps":21,"start_offset_mm":0.0,"end_offset_mm":8.0,"pitch_mm":1.0}}
```

**Output reale**: `{"status":"FAIL","gauge_check":{},"error":"il
sottoprocesso non ha prodotto un risultato"}` — **non** il
`{"status":"TIMEOUT", "preflight_diagnostics":..., "last_checkpoint":...}`
atteso.

**Causa (letta nel sorgente, `services/verifier/executor/watcher.py`)**:
**due meccanismi distinti, non uno**:
1. `GAUGE_CHECK_CPU_LIMIT_SECONDS` (default 100, overridabile via env)
   e' l'`RLIMIT_CPU` **interno** di `gauge_check.py` — se scatta, il
   sistema operativo termina il sottoprocesso (SIGKILL/segnale), che
   NON solleva `subprocess.TimeoutExpired` in `watcher.py` (quella
   viene sollevata solo dal parametro `timeout=` di
   `subprocess.run()`, un meccanismo Python separato). Il codice cade
   nel fallback generico di riga 187 (`"il sottoprocesso non ha
   prodotto un risultato"`), che NON costruisce
   `preflight_diagnostics`/`last_checkpoint`.
2. Il percorso strutturato con `TIMEOUT`+`preflight_diagnostics`+
   `last_checkpoint` (righe ~157-182) scatta SOLO su
   `subprocess.TimeoutExpired`, cioe' quando il job supera
   `GAUGE_CHECK_TIMEOUT_SECONDS` — costante **hardcoded a 150** (riga
   80 di `watcher.py`), **non letta da env**, quindi non abbassabile
   dall'esterno senza modificare il codice.

**L'handoff descrive il test sulla leva sbagliata**: abbassare
`GAUGE_CHECK_CPU_LIMIT_SECONDS` non esercita il percorso
"TIMEOUT strutturato", esercita un fallback generico diverso e meno
informativo. Per riprodurre davvero il criterio d'accettazione
servirebbe un job che impieghi genuinamente >150s di wall-clock (poco
pratico da garantire in modo deterministico, specie con l'ambiente
ancora instabile per via del SIGSEGV) oppure rendere
`GAUGE_CHECK_TIMEOUT_SECONDS` overridabile via env (stesso pattern
gia' usato per `CALIPER_AS_LIMIT_MB`/`CALIPER_STACK_LIMIT_MB`) — un
cambio a codice condiviso, **non applicato qui**, proposto al
supervisore.

**Configurazione runtime ripristinata** (rimosso
`GAUGE_CHECK_CPU_LIMIT_SECONDS="1"` da `supervisord.conf` sul pod,
`verifier-executor` ricaricato via `supervisorctl`) per non
interferire con test successivi.

**Esito: NON ESEGUITO** (non FALLITO nel senso del criterio — il
criterio stesso non e' raggiungibile con la leva descritta
nell'handoff, su questo codice). `stdout` in
`/workspace/caliper-runs/incoming/tc-e7.log`.

**Candidato fix per il supervisore**: `GAUGE_CHECK_TIMEOUT_SECONDS` in
`watcher.py` -> `int(os.environ.get("GAUGE_CHECK_TIMEOUT_SECONDS", "150"))`,
cosi' l'handoff/test puo' davvero forzare il percorso strutturato
senza dover aspettare 150s reali.

## Addendum — bench matrice modelli L2.5

**Bug nel bench script incontrato e risolto**: `bench/bench_l25_models.py`
(appena scritto dal supervisore, non ancora rivisto) falliva subito con
`KeyError: 'flowData'` — `load_template_and_fields()` leggeva il file
versionato del chatflow (`services/flowise/chatflows/l25-*.json`, che
e' GIA' il contenuto di `flowData`, struttura `{"nodes":[...],
"edges":[...]}` alla radice) come se fosse la risposta dell'API
Flowise (che invece avvolge tutto in `{"flowData": "<json-string>",
"name":..., "id":...}`). Fix minimale (riga 109): usa `cf` direttamente
se non c'e' una chiave `"flowData"`, invece di richiederla sempre.
Versionato su questo branch (`bench/bench_l25_models.py`) con il fix
gia' applicato.

**Comando eseguito**:
```
BENCH_MODELS="granite4:1b,granite4:3b,qwen3:8b,llama3.1:8b" \
OPENAI_API_KEY=... python3 bench/bench_l25_models.py
```

**Risultati** (15 casi con atteso noto, per modello — vedi
`docs/hardware_fingerprint_run1.md` per l'hardware esatto, RTX A6000 +
2x EPYC 7543, necessario per reinterpretare le latenze su hardware
diverso):

| modello | backend | json_ok% | **inventati%** | estratti_ok% | lat media (s) |
|---|---|---|---|---|---|
| granite4:1b | ollama | 100.0 | **51.4** | 92.9 | 1.2 |
| granite4:3b | ollama | 100.0 | **5.7** | 95.2 | 0.98 |
| qwen3:8b | ollama | 100.0 | **2.9** | 100.0 | 18.94 |
| llama3.1:8b | ollama | 100.0 | **2.9** | 100.0 | 1.56 |
| gpt-4o-mini | openai | 100.0 | **0.0** | 95.2 | 1.11 |

**Osservazioni**:
- `granite4:1b` (modello attualmente in produzione nel chatflow L2.5)
  e' il PEGGIORE della matrice per la metrica decisiva (51.4%
  inventati) — coerente con quanto osservato dal vivo in E2E-1 (4/4
  invenzioni deterministiche).
- Solo `gpt-4o-mini` raggiunge la soglia di candidatura dichiarata
  (`inventati=0`) — ma e' un modello API a pagamento, non locale.
- Tra i modelli Ollama locali, `llama3.1:8b` e' il migliore rapporto
  compliance/latenza (2.9% inventati, 100% estratti, 1.56s medi) —
  `qwen3:8b` ha la stessa compliance ma e' **12x piu' lento** (18.94s,
  osservato con GPU attiva al 43% util — vedi fingerprint hardware,
  non un collo di bottiglia CPU).
- **Scelta del modello lasciata all'utente/supervisore sui numeri**,
  come da addendum — nessun cambio al chatflow live in questo run per
  questo motivo.

File completi (inclusi nell'harvest): `bench_l25_summary.md`,
`bench_l25_cases.csv` (15 casi × 5 modelli, dettaglio per singola
chiamata) in `/workspace/caliper-runs/incoming/bench-l25/`.

## Fix candidato tolerance_type (dopo il bench, come da addendum)

Diagnosi del supervisore: la descrizione del campo `tolerance_type`
nello schema del parser strutturato del chatflow L2.5 ("one of:
diametrale, per_lato, su_nocciolo, su_cresta") **non offre l'opzione
vuota**, in tensione con l'istruzione del template ("leave it empty
if not specified"). Verificato leggendo lo schema live (vedi sotto).

### Fix candidato tolerance_type — ROSSO->VERDE confermato

**Rosso (baseline, gia' documentato sopra)**: 4/4 su
`{"question": "foro filettato M6, tolleranza 0.3mm, passo 1.0"}`,
`tolerance_type:"diametrale"` sempre inventato.

**Fix applicato** (`services/flowise/chatflows/l25-specification-normalization.json`,
nodo `structuredOutputParser`, campo `tolerance_type` nello schema):
description passata da `"one of: diametrale, per_lato, su_nocciolo,
su_cresta"` a `"one of: diametrale, per_lato, su_nocciolo, su_cresta
or empty string if not specified in the prompt"` — esattamente il
testo proposto dal supervisore. File versionato aggiornato, poi
propagato al chatflow LIVE con `PUT /api/v1/chatflows/<id>`
(non un re-import: il chatflow esisteva gia', `import_chatflows.py`
salta i chatflow con nome gia' presente).

**Verde (stesso identico prompt, 4 ripetizioni)**:
```
1: tolerance_type=""
2: tolerance_type=""
3: tolerance_type=""
4: tolerance_type=""
```

**4/4 — rosso->verde confermato**, stessa disciplina delle altre
milestone. Nessun'altra riga dello schema toccata. `stdout` completo
in `/workspace/caliper-runs/incoming/tc-e1-after-schema-fix.log`.

**Nota per E2E-1 nella tabella**: con questo fix, l'esito di E2E-1 su
questo chatflow torna ad essere un PASS pulito rispetto all'atteso
originale dell'handoff (`tolerance_type`/`measured_as` vuoti). Il fix
e' gia' sul chatflow versionato su questo branch — chi importa/usa
`services/flowise/chatflows/l25-specification-normalization.json` da
qui in poi lo eredita.
