# Logbook — Fase 7 (M7): topologia Docker reale su RTX 3090

Branch: `claude/m7-docker-3090-run0` (da `develop`@`fb24e37`, che include
tutto M1-M6 + PR #29/#30). Ambiente: workstation HP Z8 G4 dell'utente
(`berlin-3eie`), RTX 3090 24GB, 32 core reali, Docker 29.7.2 + Compose
v5.5.0, bare metal (non un pod condiviso/oversubscribed come RunPod).
Fingerprint completo in `runs/20260825-133500-m7-run0/env_fingerprint.json`.

Riferimento: `docs/piano_recupero.md` §3/M7, issue #19. Criterio di
accettazione: suite TC-E2E-1..7 verde in topologia container + 3
verifiche di isolamento attive documentate + primo G-code prodotto.

## Metodologia

Stessa disciplina di M6: rosso osservato prima del fix, fix minimale
sul componente giusto (mai un workaround runtime non committato),
verde riprodotto. Ogni bug qui e' per definizione una giuntura mai
esercitata (C9) — nessuno di questi errori era visibile in M6, dove
Flowise/Ollama/Qdrant giravano nativi nello stesso pod, non in
container Docker separati con volumi e reti reali.

## Passo 1 — Prima build Docker della storia del progetto

`docker compose build`: le 4 immagini di servizio (`verifier`,
`verifier-executor`, `stream-agent`, `dashboard`) buildano pulite al
primo colpo (`cadquery==2.8.0` compila/installa senza errori, ~110s
dominati dal download delle wheel OCP/VTK). Nessun bug qui — il primo
bug e' apparso al boot, non alla build.

## Passo 2 — `docker compose up`: 1° bug reale (Flowise crasha su ogni boot)

**Rosso**: `caliper-flowise` non arriva mai a "healthy" —
`TypeError: this.db.exec is not a function` in
`connect-sqlite3/lib/connect-sqlite3.js:56`, dentro
`SessionPersistance.js` (inizializzazione sessioni JWT, path
`DATABASE_TYPE` di default = sqlite).

**Causa** (letta a sorgente dentro il container, non assunta):
`flowiseai/flowise:3.1.4` spedisce `connect-sqlite3@0.9.17`, la cui
API e' cambiata — si aspetta che l'opzione `db` sia gia' un'istanza
`sqlite3.Database` aperta (e' diventata `peerDependency`, vedi
`package.json` del pacchetto nell'immagine). Il codice di Flowise
(`SessionPersistance.js`) le passa invece una stringa nuda
(`db: 'database.sqlite'`), comportamento della vecchia API
(pre-0.9.x), che apriva il file da se'. Bug upstream reale
dell'immagine ufficiale, non nostro codice.

**Fix**: `ops/docker/flowise/Dockerfile` — build locale sopra
l'immagine pinnata, un `RUN node -e ...` patcha solo quella riga
(apre `sqlite3.Database` da se' se riceve una stringa; `sqlite3` e'
gia' presente come dipendenza sorella, usata da Flowise stesso per il
proprio datastore). Guard esplicita: se la riga da patchare non c'e'
piu' (versione futura cambiata), la build fallisce rumorosamente
invece di no-op silenzioso. `docker-compose.yml`: `flowise` ora fa
`build:` invece di `image:` diretta.

**Verde**: rebuild, `docker logs caliper-flowise` pulito — "All
initialization steps completed successfully!", healthcheck
`healthy`. (Restano nei log due errori non fatali, per-nodo, di
risoluzione pacchetti su nodi mai usati da questo progetto —
`AWSBedrock`/`@smithy`, `ReActAgentLLM`/`langgraph-checkpoint` — non
toccati, non bloccano il resto del nodes-pool.)

## Passo 3 — Smoke test di ogni servizio

Tutti verdi dopo il fix Flowise: `verifier` (`/health`), `stream-agent`
(`/health`), `qdrant` (`/healthz`), `dashboard` (200), `flowise`
(`/api/v1/ping` → pong). Bootstrap Flowise (account, API key,
credential OpenAI) con `ops/runpod/flowise_bootstrap.py`, riusato
com'e' (solo stdlib, nessun path RunPod-specifico grazie a
`CALIPER_ENV_FILE`/`FLOWISE_URL` gia' overridabili) — stessa sequenza
gia' nota da M6 (bootstrap → import chatflow via `flowise-init` →
ribootstrap idempotente per agganciare la credential). 3 chatflow
importati, credential agganciata ai 2 chatflow L2.

## Passo 4 — Isolamento attivo (criterio M7 #4, tutte e 3 verificate)

1. **Rete da dentro `verifier-executor`**: tentativo di connessione
   TCP a `8.8.8.8:53` → `OSError: [Errno 101] Network is unreachable`
   (non un DNS/timeout — nessuna rotta affatto, `network_mode: none`
   confermato a livello kernel).
2. **POST al `docker-socket-proxy`**: `POST /containers/create` dalla
   dashboard → `403 Forbidden` ("Request forbidden by administrative
   rules", `POST=0` attivo).
3. **Path traversal su `/gauge-check`**: `part_step_path:
   "../../../../../../etc/passwd"` → rifiutato via HTTP,
   `"path fuori dalla radice consentita (/models)"` (la guard di
   `resolve_under_root()` in `gauge_check.py`, mai esercitata via HTTP
   reale finora, funziona come da progetto).

## Passo 5 — Mount `billa05/prusacli` + primo G-code

**Verificato dal vivo** (era un TODO nel compose, "dedotti, mai
confermati"): l'immagine ha entrypoint di default `/bin/bash`
(`--help` veniva interpretato da bash stesso, non dal binario) — il
binario reale e' `/app/prusa-slicer` (PrusaSlicer 2.9.2), non
`prusa-slicer-console` come ipotizzato nel commento di
`caliper-pla.ini`. Fix: `entrypoint: ["/app/prusa-slicer"]` nel
compose. I path di mount (`/models`, `/gcode`, `/config`) erano gia'
corretti.

**2° bug reale**: l'immagine non ha `OCCTWrapper.so` — non puo'
importare STEP direttamente (`Cannot load OCCTWrapper.so`). Limite
dell'immagine di terze parti, non nostro codice. Workaround
applicato (non un fix all'immagine, fuori dal nostro controllo):
conversione STEP→STL con CadQuery (gia' disponibile
nell'immagine `verifier-executor`) prima dello slicing — 2 righe
Python, `cq.importers.importStep()` → `cq.exporters.export()`.

**Primo G-code della storia del progetto**: slicing del pezzo PASS di
E2E-2 (M6, `parts/2c32dde3-...step`, gia' nel repo) con
`config/prusaslicer/caliper-pla.ini`. Riuscito al primo colpo dopo il
fix entrypoint+STL: `runs/20260825-133500-m7-run0/e2e2_thread_M6.gcode`
(310KB, 276.11mm filamento, PrusaSlicer 2.9.2).

## Passo 6 — Rimisura budget CPU (criterio M7 #6/C8) — 3° e 4° bug reali

**I numeri di M6 non si trasferiscono, esattamente come previsto dal
piano (regola #3).** Primo tentativo di E2E-2 con la spec di M6, zero
modifiche: FAIL identico al SIGSEGV di M6
("il processo non fidato non ha prodotto uno stato di esportazione
leggibile"). Diagnosticato a mano (stessa disciplina di M6: invocato
`run_and_measure.py` isolato, non assunto):

- **Con i default puri** (`CALIPER_AS_LIMIT_MB=2048`, nessun override
  CPU): `exit=139` (SIGSEGV), quasi istantaneo (~1s). Il commento nel
  codice ("il default 2GB e' tarato sui container di
  produzione/docker-compose") era una previsione mai verificata in un
  container reale — falsa qui.
- **Con `CALIPER_AS_LIMIT_MB=16384`** (valore di M6): SIGSEGV sparisce,
  ma `exit=137` (SIGKILL) a ~2.4s — `CALIPER_CPU_LIMIT_S` default
  (10s) scatta. **Causa**: RLIMIT_CPU somma il tempo di TUTTI i thread
  combinati — con 32 core REALI (bare metal, non un mismatch
  cgroup/nproc come su RunPod) l'aggregato cresce molto piu' in fretta
  a parita' di lavoro. Misurato (3 run, job normale `pitch=1.0`):
  CPU totale 12.7-13.7s — supera 10s anche sul caso BASE, mai successo
  su M6 (dove solo `pitch=0.05`, un caso pesante, lo superava).
- **Gauge-check (sweep GO, stesso pezzo di E2E-8/M6)**: col limite
  ereditato da M6 (140s) → SIGKILL sistematico a ~140.6-141s (misura
  invalidata: il processo viene ucciso PRIMA di finire, non e' il
  tempo reale necessario). Rimisurato con limite alto (500s) per
  vedere il vero completamento: **267-268s CPU totale**, wall
  ~37.1-37.5s (M6: 91.4s CPU, ~20.7s wall — stesso pezzo, stesso
  sweep, quasi lo stesso wall-clock, ma quasi 3x la CPU aggregata).

**Nuovi default** (worst-case misurato × 1.5, stessa convenzione di
E2E-8/M6), applicati in `docker-compose.yml` come `environment:` del
servizio `verifier-executor` (non un fix runtime non committato):

| Variabile | M6 (pod RunPod) | M7 (Z8, 32 core reali) |
|---|---|---|
| `CALIPER_AS_LIMIT_MB` | 16384 | 16384 (invariato, confermato necessario anche qui) |
| `CALIPER_STACK_LIMIT_MB` | 2 | 2 (invariato) |
| `CALIPER_CPU_LIMIT_S` | 10 (mai ricalibrato) | **25** (worst-case 13.71s × 1.5) |
| `GAUGE_CHECK_CPU_LIMIT_SECONDS` | 140 | **405** (worst-case 267.9s × 1.5) |

`GAUGE_CHECK_TIMEOUT_SECONDS` (watcher.py, wall-clock esterno) **non
toccato**: e' un timeout di wall-clock, non di CPU aggregata — il
completamento reale (~37.5s wall) resta ben sotto il default (210s),
nessun conflitto nonostante il budget CPU interno sia salito.

**Verificato dopo il fix**: E2E-2 rieseguito attraverso la pipeline
HTTP reale con i nuovi default → **PASS, numeri IDENTICI a M6** (GO
0.305925mm³, NO-GO 20.158069mm³) — la ricalibrazione non cambia
l'esito geometrico, solo il budget entro cui il calcolo puo' finire.

## Passo 7 — Suite TC-E2E-1..7 nella topologia reale

**5° bug reale**, trovato eseguendo E2E-1: il chatflow L2.5 versionato
(`services/flowise/chatflows/l25-specification-normalization.json`)
ha il nodo ChatOllama con `baseUrl: "http://localhost:11434/"` —
valido quando Flowise girava nativo su RunPod (stesso pod =
`localhost` reale), rotto qui dove Flowise e Ollama sono container
separati sulla rete `caliper-ai` (**non** un blocco SSRF: l'errore era
`TypeError: fetch failed`, host genuinamente irraggiungibile). Fix:
`baseUrl` → `http://ollama:11434/`, applicato al file versionato E
all'istanza Flowise live (via API, `PUT /api/v1/chatflows/:id` con
cookie di sessione — il DELETE con Bearer API key ha dato `403
Forbidden`, RBAC diverso per quell'endpoint, non approfondito oltre:
il PUT autenticato via login ha funzionato).

**6° bug reale**, trovato eseguendo E2E-5: `data/virtual_log` e'
montato `:ro` in compose (per scelta — l'indexer non deve poter
scrivere nel log dell'orchestratore), ma il sidecar `.offset` che
rende l'indicizzazione incrementale persistente ai riavvii (fix C6,
M5) veniva scritto proprio li' accanto → `Read-only file system`.
`VIRTUAL_LOG_OFFSET_PATH` era gia' overridabile via env (nessun
cambio al codice, solo compose): aggiunto volume dedicato
`stream_agent_state`, scrivibile, per il solo file di offset.

| TC | Esito | Note |
|---|---|---|
| E2E-1 | **PASS** (dopo fix baseUrl) | `{"feature":"thread","nominal":"M6","pitch":1,"tolerance":0.3,"tolerance_type":"","measured_as":""}` — "non indovinare" confermato anche qui |
| E2E-2 | **PASS** (dopo ricalibrazione budget) | Numeri identici a M6: GO 0.305925mm³, NO-GO 20.158069mm³, STEP su disco |
| E2E-3 | **PASS sui criteri meccanici** | 2 tentativi, `unrecoverable_virtual`, `case_id` stabile — stessa riserva onesta di M6: causa e' `CALIPER_CPU_LIMIT_S=25` insufficiente per questo caso pesante (`pitch=0.05`), non un rifiuto geometrico locale; confermato exit=137 (SIGKILL) a mano, non un crash |
| E2E-4 | **PASS** | Foro liscio Ø7: GO PASS (0.0mm³), NO-GO PASS/0.0mm³ (nessuna interferenza) → conferma C2 (regola letta nel sorgente, orchestratore bypassato come da specifica del test) |
| E2E-5 | **PASS** (dopo fix volume offset) | `docker restart caliper-stream-agent` reale: virtual 7→7, physical 1→1, nessun duplicato, offset riletto da disco su processo fresco |
| E2E-6 | **PASS** | 3 fallimenti virtuali (soglia 2) + 1 fallimento fisico corroborante → esclusione applicata, chiamate a Flowise invariate (11→11, 0 nuove) |
| E2E-7 | **PASS** | `GAUGE_CHECK_TIMEOUT_SECONDS=5` temporaneo (container isolato, non quello di produzione) → `TIMEOUT` strutturato, `preflight_diagnostics`+`last_checkpoint` (step 1/20) popolati |

Criterio M7 di accettazione: **soddisfatto** — TC-E2E-1..7 verdi,
3 verifiche di isolamento attive documentate, primo G-code prodotto.

## Riepilogo bug trovati (tutti C9 — giunture mai esercitate)

1. Flowise 3.1.4: crash sistematico al boot Docker (connect-sqlite3) — fix in `ops/docker/flowise/Dockerfile`.
2. `docker-compose.yml` `prusaslicer`: entrypoint sbagliato (bash invece del binario) — fix `entrypoint:`.
3. `billa05/prusacli`: non importa STEP nativamente (manca OCCTWrapper.so) — workaround, conversione STL a monte (limite dell'immagine terze parti, non risolvibile lato nostro senza ricostruire l'immagine).
4. `CALIPER_AS_LIMIT_MB`/`CALIPER_CPU_LIMIT_S`/`GAUGE_CHECK_CPU_LIMIT_SECONDS`: i default (M6 o codice) non reggono su un host a 32 core reali — ricalibrati e committati in `docker-compose.yml`.
5. Chatflow L2.5: `baseUrl` Ollama puntava a `localhost`, rotto nella topologia multi-container — fix nel chatflow versionato + istanza live.
6. `stream-agent`: offset C6 scritto in un mount `:ro` — fix con volume dedicato scrivibile.

Nessuno di questi era visibile in M6 (Flowise nativo, non Docker;
nessuna rete container; nessun mount `:ro`; nessun 32-core reale). Il
piano lo aveva previsto esplicitamente (§1, regola 1: "ogni giuntura
esercitata per la prima volta ha rivelato un bug").

## Riserve oneste

- `CALIPER_CPU_LIMIT_S=25` e' calibrato sul caso BASE (`pitch=1.0`),
  non sul caso pesante di E2E-3 (`pitch=0.05`) — stessa situazione di
  M6 con `GAUGE_CHECK_CPU_LIMIT_SECONDS` prima di E2E-8: se si vuole
  che `pitch=0.05` fallisca per davvero (o passi) invece che per
  timeout, serve una rimisura dedicata su quel caso specifico — non
  fatta qui, stessa decisione lasciata al supervisore gia' presa (e
  non presa) in M6.
- Il DELETE via Bearer API key sui chatflow ha dato `403 Forbidden`
  mentre il PUT via cookie di login e' passato — RBAC di Flowise 3.x
  non del tutto mappato, non approfondito (il workaround via login ha
  funzionato, non serviva altro per chiudere il test).
- `.github/workflows/publish-images.yml` non pubblica ancora
  l'immagine Flowise patchata su GHCR (solo le 4 immagini di
  servizio) — se si vuole `docker-compose.ghcr.yml` completo anche per
  Flowise, va aggiunta li'; non fatto qui, fuori dallo scope minimo di
  M7 (il build locale nel compose e' sufficiente per il criterio di
  accettazione).
- Bench L2.5/scelta modello (aperta da M6): non ripetuta qui, fuori
  scope M7.

---

*Sessione eseguita da Claude Code su richiesta dell'utente, sulla Z8
G4 privata (SSH diretto, non un pod). Nessuna PR aperta/mergiata da
questa sessione — branch pushato, harvest in
`runs/20260825-133500-m7-run0/`, in attesa di verifica/decisione
dell'utente.*
