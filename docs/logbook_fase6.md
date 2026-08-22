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
