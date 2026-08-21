# Logbook — M5: Fix pack post-review (sbloccare l'end-to-end)

Vedi [`logbook.md`](logbook.md) per il quadro generale e
[`review_tecnica.md`](review_tecnica.md) (issue [#15](https://github.com/danielesalpietro/caliper-cad/issues/15))
per la specifica completa di ogni criticita' (C1-C8, C10) chiusa qui.
Handoff completo: [`handoff_m5.md`](handoff_m5.md), issue
[#17](https://github.com/danielesalpietro/caliper-cad/issues/17).

Metodo applicato ad ogni blocco, senza eccezioni: (1) test che riproduce
il difetto e fallisce sul codice pre-fix (output rosso incollato sotto);
(2) fix minimale; (3) stesso test verde; (4) script aggiunto a
`.github/workflows/regression.yml`.

## Blocco A — C1: contratto dimensionale per-feature (P2a)

**Problema**: `run_and_measure.py` confrontava `max(bbox_x, bbox_y)` col
nominale — corretto per un tampone pieno (`[v14]`), sbagliato per il
pezzo reale di M3 (foro in blocco ospite piu' largo del nominale per
schema, `sketch_schema.py`). Nessun PASS dimensionale era possibile per
costruzione.

**Fix**: `presets.json` dichiara `dimensional_check: "gauge"` per
`thread` — bbox-vs-nominale non si applica piu', sostituito da un
sanity check (`bbox_z ~= engagement_length_mm`, `bbox_x/y >= diametro
maggiore`). Comportamento legacy (bbox-vs-nominale) invariato per spec
senza il campo. `generate_and_verify.apply_preset()` estende
l'arricchimento esistente (non un canale nuovo) per forwardare
`dimensional_check`/`engagement_length_mm`.

**File**: `services/verifier/executor/run_and_measure.py`,
`services/orchestrator/presets.json`,
`services/orchestrator/generate_and_verify.py` (`apply_preset`).

**Test nuovo**: `services/verifier/executor/verify_dimensional_contract.py` (TC-M5-1).

**Rosso (pre-fix)** — foro M6 in blocco 20×20×8, spec con
`dimensional_check: "gauge"`:

```
--- 1. Foro M6 in blocco 20x20x8, spec con contratto 'gauge' (P2a) ---
{
  "execution": "FAIL",
  ...
  "dimensional_check": {
    "nominal_mm": 6.0,
    "measured_diameter_mm": 20.0,
    "tolerance_mm": 0.3,
    "delta_mm": 14.0,
    "status": "FAIL"
  },
  "generated_part_step_path": "job_gauge.step"
}
Atteso: PASS (nessun bbox-vs-nominale per contratto 'gauge'): FALLITO
```

**Verde (post-fix)**: PASS, sanity check PASS (`bbox_z=8.0 ~=
engagement_length_mm=8.0`, `bbox_x/y=20.0 >= 6.0`); GO sweep reale sul
pezzo esportato PASS, residuo **0.305925mm³** (soglia 0.5mm³). Caso
legacy (senza `dimensional_check`) invariato: FAIL bbox-vs-nominale.
Caso inverso (bbox_z=12 vs engagement=8): FAIL di sanity con errore
esplicito.

**Regressione**: 3/3 script esistenti che toccano `run_and_measure.py`
(`verify_run_and_measure_export.py`, `verify_gauge_check_part_source.py`,
più `verify_gauge_check_loop_wiring.py`, `verify_sketch_first_strategy.py`,
`verify_sketch_compiler_thread.py`) — verdi senza modifiche.

## Blocco B — C2: NO-GO nel loop

**Problema**: `generate_and_verify.py` chiamava solo il calibro GO dopo
un PASS di `/verify` — un foro sovradimensionato passava lo sweep GO
senza interferenza e il loop lo dichiarava PASS: meta' della banda di
tolleranza non era mai controllata.

**Fix**: dopo un GO PASS, il loop chiama anche `gauge_nogo_step` dello
stesso preset con semantica INVERTITA (interferenza rilevata = pezzo
OK; nessuna interferenza = FAIL, errore stabile
`gauge_check_nogo_no_interference`). PASS solo se GO passa E NO-GO
interferisce. TIMEOUT sul NO-GO trattato come FAIL (fail-safe, nessuna
interferenza CONFERMATA).

**File**: `services/orchestrator/generate_and_verify.py`
(`gauge_nogo_job_for_preset`, blocco principale in `main()`).

**Test**: esteso `services/orchestrator/verify_gauge_check_loop_wiring.py`
con gli scenari F/G (TC-M5-2).

**Rosso (pre-fix)**:

```
=== F. [M5, C2] GO PASS + NO-GO senza interferenza -> FAIL ===
Atteso: NO-GO chiamato dopo ogni GO PASS ...: FALLITO (exit=0, gauge_calls=1, nogo_step_used=None)

=== G. [M5, C2] GO PASS + NO-GO con interferenza -> PASS ===
Atteso: successo solo dopo GO PASS E NO-GO con interferenza (2 chiamate gauge-check, GO poi NOGO): FALLITO (exit=0, gauge_calls=1)
```

Pre-fix: 1 sola chiamata gauge-check (solo GO), PASS silenzioso — esattamente il bug C2.

**Verde (post-fix)**: scenario F — 4 chiamate (GO+NOGO × 2 tentativi,
uscita anticipata), verdetto finale FAIL. Scenario G — 2 chiamate (GO
poi NOGO), PASS solo con entrambi verificati.

**Regressione**: scenari A/B/D/E dello stesso file e
`verify_sketch_first_strategy.py` aggiornati per il nuovo conteggio di
chiamate gauge-check (ora sempre GO+NOGO su un PASS) — dichiarato,
tutti verdi. 21/21 script totali verdi.

## Blocco C — C3: `snap_fit` non deve rompere l'orchestratore

**Problema**: `gauge_check_job_for_preset()` pretendeva `gauge_go_step`
per QUALUNQUE `gauge_check_mode` — ma `min_distance` (usato da
`snap_fit`) non usa un calibro fisico. Qualunque spec `snap_fit`
mandava `main()` in `ValueError("Preset incoerente...")` prima ancora
di generare.

**Fix**: nuova `min_distance_job_for_preset()` costruisce il job
`/gauge-check` (`mode: "min_distance"`) dai `measurement_points` del
preset (`point_a_mm`/`point_b_mm`/`nominal_mm`/`tolerance_mm`);
`gauge_check_job_for_preset()` ritorna `None` per `mode == "min_distance"`
invece di sollevare. Nessun NO-GO per questo mode (non ha senso: non
c'e' un secondo calibro).

**File**: `services/orchestrator/generate_and_verify.py`
(`min_distance_job_for_preset`, `gauge_check_job_for_preset`, setup in `main()`).

**Test nuovo**: `services/orchestrator/verify_snap_fit_min_distance_wiring.py` (TC-M5-3).

**Rosso (pre-fix)**:

```
=== A. spec snap_fit -> job min_distance costruito dai measurement_points, PASS ===
Preset incoerente per il gauge-check: preset con gauge_check_mode ma senza gauge_go_step — schema del preset incoerente
Atteso: nessun ValueError ...: FALLITO (exit=1, error=None, job_sent=None)

=== B. spec snap_fit, min_distance FAIL ripetuto -> gestito come un FAIL qualunque ===
Preset incoerente per il gauge-check: preset con gauge_check_mode ma senza gauge_go_step — schema del preset incoerente
Atteso: nessuna eccezione, FAIL gestito dal loop ...: FALLITO (exit=1, error=None, gauge_calls=0)
```

**Verde (post-fix)**: scenario A — nessun `ValueError`, job
`/gauge-check` con `mode: "min_distance"` e i punti ESATTI del preset
(`point_a_mm=[-6.0,0.0,8.0]`, `point_b_mm=[-6.0,0.0,8.3]`,
`nominal_mm=0.3`, `tolerance_mm=0.1`), PASS immediato. Scenario B — FAIL
gestito come qualunque altro (uscita anticipata dopo 2 tentativi
identici), nessuna eccezione. Scenario C (preset senza
`measurement_points`) — `ValueError` esplicito, gestito con `return 1`.

## Blocco D — C7: split esecuzione/verdetto (P5)

**Problema**: `run_and_measure.py` eseguiva `exec(code)` NELLO STESSO
processo che poi scriveva il verdetto (`result.json`) — codice non
fidato poteva scrivere un `result.json` contraffatto
(`execution: "PASS"`) e chiamare `os._exit(0)` prima di qualunque
controllo reale.

**Fix (P5)**: split in due processi.
- `run_and_measure.py` (non fidato): `exec(code)` + solo export STEP
  in `GENERATED_PARTS_DIR`, scrive uno stato di esportazione interno
  (`export_status.json`, mai il verdetto pubblico).
- `measure_verdict.py` (nuovo, fidato, MAI `exec` di codice esterno):
  reimporta lo STEP da zero (non riusa mai misure riportate dal
  processo non fidato), misura, scrive `result.json` — l'UNICO
  scrittore, chiamato SEMPRE da `watcher.py` (indipendentemente da cosa
  il processo non fidato ha scritto altrove), sovrascrivendo
  incondizionatamente qualunque file gia' presente.

**File nuovo**: `services/verifier/executor/measure_verdict.py`.
**File modificati**: `run_and_measure.py` (solo export),
`watcher.py` (`process_code_job` orchestra i due stadi),
`Dockerfile` (COPY del nuovo script).

**Test nuovo**: `services/verifier/executor/verify_verdict_integrity.py` (TC-M5-5).

**Rosso (pre-fix)** — fixture avversariale (codice che scrive un
result.json contraffatto e chiama `os._exit(0)`), eseguita contro il
contratto a un solo processo:

```json
{
  "execution": "PASS",
  "error": null,
  "measurements": {"is_valid": true, "bbox_x_mm": 6.0, "bbox_y_mm": 6.0, "bbox_z_mm": 8.0},
  "dimensional_check": null,
  "generated_part_step_path": "totally-fake-copied-from-models.step"
}
```

Il PASS contraffatto viene accettato integralmente.

**Verde (post-fix)**, stessa fixture attraverso i due stadi:

```json
{
  "execution": "FAIL",
  "error": "il processo non fidato non ha prodotto uno stato di esportazione leggibile",
  "measurements": null,
  "dimensional_check": null,
  "generated_part_step_path": null
}
```

Regressione (caso valido attraverso la stessa sequenza a due stadi):
PASS reale, STEP esportato, measurements popolati — schema pubblico di
`result.json` invariato.

**Regressione**: `verify_run_and_measure_export.py` e
`verify_dimensional_contract.py` aggiornati per chiamare la sequenza a
due stadi (dichiarato) — verdi. Tutti gli altri script che passano da
`gauge_check.py` invariati (non toccato da questo blocco).

**Riserva onesta**: la rimozione della leggibilita' di `/models` dal
solo processo `run_and_measure.py` (richiesta dall'handoff) non e'
realizzabile con `docker-compose.yml` da solo — il mount si applica
all'intero container `verifier-executor`, condiviso da tutti i
sottoprocessi; richiederebbe un container/namespace separato per questo
stadio, cambio architetturale piu' grande, fuori scope qui. La difesa
reale (separazione esecuzione/verdetto + sovrascrittura incondizionata)
non dipende da questo, vedi `run_and_measure.py` per la nota completa.

## Blocco E — C4: strategia `param_first` (P3) + fix sketch-first

**Problema 1**: `NUMERIC_TOLERANCE_MM=1e-6`/`NUMERIC_TOLERANCE_DEG=1e-3`
pretendevano precisione da double IEEE (il punto di radice M6 e'
`2.1339745962155613mm` — arrotondato a 4 decimali, gia' fuori soglia).
**Problema 2**: `is_crest` in `sketch_compiler.py` usava una soglia
(`1e-6`) piu' stretta di `NUMERIC_TOLERANCE_MM`, aprendo una finestra
di quasi-tangenza (punti "cresta" validi per lo schema ma non
riconosciuti come tali dal compilatore, niente overlap OCC).
**Problema 3**: sketch-first per `thread` e' ridondante — il profilo a
V e' interamente determinato da `pitch_mm`+angolo, gia' nel preset.

**Fix**: `NUMERIC_TOLERANCE_MM` → `1e-3`, `NUMERIC_TOLERANCE_DEG` →
`0.1`; `is_crest` agganciato a `CROSS_FIELD_TOLERANCE_MM` (stessa
soglia della consistenza sketch/operation, non un terzo numero);
nuova strategia `L2_STRATEGY=param_first` —
`build_thread_sketch_spec_from_params()`/`compile_thread_params_to_code()`
in `sketch_compiler.py` costruiscono la spec sketch canonica dalla
STESSA trigonometria di `generate_thread_gauge.build_thread_plug()`
(`H = pitch/(2*tan(angolo/2))`) e riusano `compile_thread_sketch_to_code()`
— nessuna seconda via geometrica.

**File**: `services/orchestrator/sketch_schema.py`,
`services/orchestrator/sketch_compiler.py` (nuove funzioni +
fix `is_crest`), `services/orchestrator/generate_and_verify.py`
(`L2_STRATEGY="param_first"`, `generate_code_for_attempt`).

**Test nuovo**: `services/orchestrator/verify_param_first.py` (TC-M5-7).

**Rosso (pre-fix)**:

Caso 2 (coordinate a 4 decimali):
```
Errori schema (PRE-FIX, tolleranza 1e-6/1e-3): ["dimension 'angle' su ['l_flank_in', 'l_flank_out']: dichiarato 60.0 gradi, ma le coordinate implicano 60.001456 gradi (differenza > 0.001 gradi) — quota inconsistente con la geometria dichiarata"]
```

Caso 3 (cresta a `r_major - 5e-4`, gia' oggi invalida per lo schema
stesso, prima ancora di arrivare al compilatore):
```
SketchValidationError: dimension 'angle' su ['l_flank_in', 'l_flank_out']: dichiarato 60.0 gradi, ma le coordinate implicano 60.028660 gradi (differenza > 0.001 gradi) — quota inconsistente con la geometria dichiarata
```

**Verde (post-fix)**:

```
--- 1. param_first produce ESATTAMENTE lo stesso codice del percorso sketch-first a mano ---
Atteso: codice CadQuery identico (stessa trigonometria, stessa spec canonica): OK
GO sweep: PASS, residuo=0.305925mm3 (atteso <= 0.5mm3): OK
NO-GO sweep: interferenza=20.158069mm3 (atteso rilevata, > 1mm3): OK

--- 2. sketch-first: coordinate arrotondate a 4 decimali ---
p_root.x = 2.134; errori schema: []
Atteso: nessun errore di schema (NUMERIC_TOLERANCE_MM allargata): OK

--- 3. Punto di cresta a r_major - 5e-4: overlap applicato nel codice ---
Atteso: spec valida per lo schema E overlap applicato: OK
```

I numeri (GO residuo ≈0.3059mm³, NO-GO ≈20.158mm³) corrispondono
esattamente ai valori anticipati in `docs/handoff_m5.md` — riscontro
diretto tra la specifica e l'implementazione.

**Regressione**: 9/9 script orchestrator verdi (nessuna regressione
sulle tolleranze allargate ne' sull'overlap di cresta).

## Blocco F — C5: memoria virtuale onesta e revocabile

**Problema 1**: `SPEC_KEY_FIELDS` non includeva `tolerance`/`pitch` —
"M6 tol 0.05" e "M6 tol 0.5" collassavano sulla stessa strategia.
**Problema 2**: `count_virtual_failures` contava i TENTATIVI, non i
CASI — un solo run sfortunato (3 tentativi falliti dello stesso
`case_id`) superava da solo la soglia di esclusione.
**Problema 3**: nessuna distinzione tra FAIL geometrici e FAIL di
generazione/JSON/schema — questi ultimi non dicono nulla sulla
strategia geometrica ma contavano comunque.
**Problema 4**: nessuna versione del checker nei record — un fix di un
bug del verificatore (es. `[v14]`) non azzerava mai il pregiudizio
accumulato, lasciandolo permanente.

**Fix**: `SPEC_KEY_FIELDS += ("tolerance", "pitch")`;
`count_virtual_failures` conta `case_id` distinti con almeno un FAIL
`failure_class == "geometric"`; `failure_class` (`"geometric"|"generation"`)
scritto esplicitamente da `generate_and_verify.py` ad ogni
`record_attempt` (mai dedotto parsando stringhe); `checker_version`
(hash sha256 troncato di `gauge_check.py`+`run_and_measure.py`+
`measure_verdict.py`, calcolato a runtime in `retry_policy.py`) su ogni
record — solo i FAIL della versione CORRENTE contano.

**File**: `services/orchestrator/virtual_memory.py`,
`services/orchestrator/retry_policy.py` (`CHECKER_VERSION`,
`RetryBudget.record_attempt`), `services/orchestrator/generate_and_verify.py`
(`failure_class` ad ogni sito di FAIL).

**Test**: esteso `services/orchestrator/verify_virtual_memory.py` con
gli scenari F/G/H/I (TC-M5-6).

**Rosso (pre-fix)**:

```
PRE-FIX: 3 FAIL dello stesso case_id -> conteggio (tentativi, non casi): 3 (atteso dal fix: 1)
PRE-FIX: 5 record 'generation' (nessun campo failure_class esisteva) contati come geometrici: 5 exclude= False
```

**Verde (post-fix)**:

```
3 FAIL dello stesso case_id -> conteggio casi: 1 (atteso 1)
2 case_id distinti -> conteggio casi: 2 (atteso 2)
5 record FAIL failure_class='generation' -> conteggio casi geometrici: 0 (atteso 0)
2 FAIL con checker_version vecchia -> conteggio con la versione corrente: 0 (atteso 0)
Stesso log, contato ESPLICITAMENTE con la vecchia versione: 2 (atteso 2)
5 record legacy (pre-M5, senza failure_class/checker_version) -> conteggio: 0 (atteso 0)
```

**Regressione**: fixture di `verify_virtual_memory.py` (scenari A-E,
gia' esistenti da M4) e `verify_virtual_memory_loop_gate.py` aggiornate
per includere `failure_class="geometric"`/`checker_version` — dichiarato
esplicitamente, come richiesto dall'handoff. Senza l'aggiornamento,
`verify_virtual_memory_loop_gate.py` falliva con un tentativo di rete
reale (la strategia non veniva piu' esclusa, il loop procedeva a
chiamare Flowise per davvero) — verificato e corretto.

## Blocco G — C6: id stabili e offset persistito (stream-agent)

**Problema**: `abs(hash(key)) % (2**63)` in `services/stream-agent/app.py`
usa `hash(str)`, randomizzato per processo (`PYTHONHASHSEED`, non
fissato ne' nel Dockerfile ne' in `docker-compose.yml`) — a ogni
riavvio ogni caso gia' indicizzato riceveva un id NUOVO, l'upsert non
deduplicava mai. `_virtual_log_offset` viveva solo in RAM — ogni
riavvio rileggeva l'intero log dall'inizio.

**Fix**: `deterministic_point_id(key)` usa `uuid.uuid5(NAMESPACE_URL,
key)` (deterministico per costruzione, mai `hash()` nativo);
`_virtual_log_offset` persistito su file (`VIRTUAL_LOG_OFFSET_PATH`,
default `VIRTUAL_LOG_PATH + ".offset"`), riletto all'avvio.

**File**: `services/stream-agent/app.py`.

**Test nuovo**: `services/stream-agent/verify_stream_agent_ids.py` (TC-M5-4).

**Rosso (pre-fix)** — due sottoprocessi con `PYTHONHASHSEED` diversi:

```
hash() con PYTHONHASHSEED=0: 2818271613423141132, PYTHONHASHSEED=1: 7990530236383595253
```

Offset dopo un "riavvio" (nuovo processo): `0` (non persistito, invece
degli attesi 450 dal run precedente).

**Verde (post-fix)**:

```
deterministic_point_id() con PYTHONHASHSEED=0: a99422bd-e03c-5284-9d9b-7e9905ee7bc9, PYTHONHASHSEED=1: a99422bd-e03c-5284-9d9b-7e9905ee7bc9
Offset dopo il primo processo: 450; offset riletto da un nuovo processo ('riavvio'): 450
```

Nessuna istanza Qdrant/Ollama richiesta per questi controlli (funzioni
pure + persistenza su file) — coerente con la riserva onesta gia'
dichiarata in M4: il codice di indicizzazione reale resta non eseguito
contro un cluster vivo in questa sessione.

## Blocco H — quick win C10

1. **`check_result_assigned` accetta `ast.AnnAssign`**
   (`services/verifier/app.py`): `result: cq.Workplane = ...` (PEP 526)
   bocciato come falso FAIL prima del fix, brucia un tentativo di retry
   per codice altrimenti valido.

   Test nuovo: `services/verifier/verify_result_variable_annassign.py` (TC-M5-8).

   Rosso: `{'status': 'FAIL', 'detail': "no assignment to a variable named 'result' found"}`.
   Verde: `{'status': 'PASS', 'detail': None}`; nessuna regressione su
   `result = ...` semplice; `result: cq.Workplane` senza valore resta
   FAIL (corretto: nessuna assegnazione reale).

2. **CI trigger anche su `claude/**`** (`.github/workflows/regression.yml`):
   prima di M5 i branch di sessione non eseguivano la regressione fino
   all'apertura di una PR — una regressione a meta' sessione restava
   invisibile fino a quel momento.

3. **Cleanup `/exec/parts`** (`services/verifier/executor/watcher.py`):
   gap dichiarato in PR #11, mai chiuso — nessun pezzo generato veniva
   mai rimosso, crescita illimitata su un processo a lunga vita.
   `cleanup_generated_parts()` rimuove i file piu' vecchi di
   `GENERATED_PARTS_RETENTION_SECONDS` (default 24h), scansionato ogni
   `GENERATED_PARTS_CLEANUP_INTERVAL_SECONDS` (300s) nel loop principale.

   Test nuovo: `services/verifier/executor/verify_generated_parts_cleanup.py`.
   Verde: file oltre la ritenzione rimosso, file entro la ritenzione
   mantenuto, cartella inesistente non solleva.

4. **`docker-compose.yml`**: bind `127.0.0.1:` sulle porte pubblicate di
   `qdrant` (6333/6334), `ollama` (11434), `verifier` (8600),
   `stream-agent` (8500) — prima di M5 pubblicate su tutte le interfacce
   senza autenticazione (il verifier accetta codice CadQuery arbitrario
   da chiunque raggiunga la porta). `flowise`/`open-webui`/`dashboard`
   restano su tutte le interfacce, per scelta esplicita: sono le UI
   pensate per accesso da browser. Immagine Flowise pinnata a
   `flowiseai/flowise:3.1.4` (ultima versione stabile verificata su
   Docker Hub al momento di questa milestone) invece di `latest`.

   Verificato con `docker compose config` (Docker Compose v5.1.1,
   disponibile in questo sandbox): tutte e 4 le porte risolvono con
   `host_ip: 127.0.0.1` nel config risolto, immagine `flowiseai/flowise:3.1.4`
   confermata. Esecuzione reale (`docker compose up`) resta scope M7.

## Criterio di accettazione — verifica finale

Tutti gli script rieseguiti in sequenza (21 totali: 14 esistenti M1-M4 +
7 nuovi/estesi da M5), nessuna regressione:

| Script | Esito |
|---|---|
| verify_gauge_check.py (M1) | PASS |
| verify_gauge_check_real_gauges.py (M1) | PASS |
| verify_gauge_check_tc1.py (M2) | PASS |
| verify_gauge_check_tc2.py (M2) | PASS |
| verify_gauge_check_tc3.py (M2) | PASS |
| verify_retry_policy.py (M2) | PASS |
| verify_run_and_measure_export.py (M3, aggiornato M5/C7) | PASS |
| verify_gauge_check_part_source.py (M3) | PASS |
| verify_sketch_schema.py (M3) | PASS |
| verify_sketch_compiler_thread.py (M3) | PASS |
| verify_sketch_first_strategy.py (M3, aggiornato M5/C2) | PASS |
| verify_gauge_check_loop_wiring.py (M3, esteso M5/C2 — TC-M5-2) | PASS |
| verify_virtual_memory.py (M4, esteso M5/C5 — TC-M5-6) | PASS |
| verify_virtual_memory_loop_gate.py (M4, aggiornato M5/C5) | PASS |
| verify_dimensional_contract.py (M5/C1 — TC-M5-1) | PASS |
| verify_snap_fit_min_distance_wiring.py (M5/C3 — TC-M5-3) | PASS |
| verify_stream_agent_ids.py (M5/C6 — TC-M5-4) | PASS |
| verify_verdict_integrity.py (M5/C7 — TC-M5-5) | PASS |
| verify_param_first.py (M5/C4 — TC-M5-7) | PASS |
| verify_result_variable_annassign.py (M5/C10 — TC-M5-8) | PASS |
| verify_generated_parts_cleanup.py (M5/C10) | PASS |

Rieseguibile con una copia-incolla dei comandi in ogni sezione sopra, o
per intero via `.github/workflows/regression.yml` (26 step).

## Riserve oneste (dichiarate, non taciute)

- **C8 (budget CPU non portabile)**: NON affrontato in questa milestone
  — l'handoff lo elencava come "M5 (pinning thread) + M6/M7 (rimisura)"
  nel piano generale, ma il Blocco H di `docs/handoff_m5.md` non lo
  include tra i suoi 8 sotto-blocchi espliciti; nessun fix applicato qui,
  resta aperto per M6/M7 dove esiste un ambiente reale su cui rimisurare.
- **C7, riserva su `/models`**: vedi Blocco D sopra — la rimozione della
  leggibilita' di `/models` dal solo processo non fidato non e'
  realizzabile con `docker-compose.yml` da solo (mount a livello
  container, non per-sottoprocesso); la difesa reale (sovrascrittura
  incondizionata del verdetto da parte del processo fidato) non dipende
  da questo.
- **`_indexed_files` in stream-agent** (C10 secondario): resta un set in
  RAM non bounded — non affrontato qui (fuori dai 4 punti espliciti del
  Blocco H). Con l'id ora deterministico, un riavvio non crea piu'
  duplicati (upsert idempotente sullo stesso id), solo un re-embedding
  ridondante dei casi gia' indicizzati — spreco, non incorrettezza.
- **Nessuna istanza Ollama/Qdrant/Flowise viva** in questa sessione
  (stesso limite di sandbox di M1-M4) — tutti i nuovi test (incluso
  quello di stream-agent) verificano funzioni pure/persistenza su file,
  mai una chiamata di rete reale.
- **MEASURE_VERDICT_TIMEOUT_SECONDS** (watcher.py, nuovo): valore di
  partenza (15s, stesso ordine di grandezza di
  `SUBPROCESS_TIMEOUT_SECONDS`), non misurato su un worst-case reale —
  da rivedere alla prima misura reale (regola #3 di
  `docs/piano_recupero.md`), non alzato a intuito.
