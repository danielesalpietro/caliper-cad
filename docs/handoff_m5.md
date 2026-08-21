# Handoff — M5: Fix pack post-review (sbloccare l'end-to-end)

Prompt pronto per la sessione che implementa M5. Copiabile così com'è come
primo messaggio di una nuova sessione Claude Code.

**Tripwire (regola post-issue #9):** se nel tuo checkout non vedi
`docs/review_tecnica.md`, `docs/piano_recupero.md` e questo file, sei sul
branch sbagliato — parti da `origin/claude/review-tecnica` (contiene
`develop` + la review + il piano), non ricostruire il contesto alla cieca:
`git checkout -B <tuo-branch> origin/claude/review-tecnica`.

---

Riprendi il progetto CALIPER (danielesalpietro/caliper-cad) — layer di
verifica deterministica per geometrie CAD generate da LLM.

## Ruolo di questa milestone

La review tecnica (`docs/review_tecnica.md`, issue #15) ha trovato che il
percorso end-to-end reale è **bloccato per costruzione** (criticità C1) e
altre criticità concrete (C2–C8, C10). M5 è il fix pack che le chiude in
sandbox, **prima** del bring-up su infrastruttura reale (M6, RunPod — vedi
`docs/piano_recupero.md`). Le decisioni di design sono già prese dal
supervisore e motivate nella review (proposte P2a/P3/P5): implementale,
non ridiscuterle — se trovi un'evidenza tecnica che le contraddice,
fermati e segnalalo sull'issue invece di deviare in silenzio.

## Ordine di lettura

1. `docs/review_tecnica.md` — sezioni C1–C8, C10 e proposte P2–P5: è la
   specifica di questa milestone.
2. `docs/piano_recupero.md` — §1 (metodologia, vincolante: rosso prima di
   verde) e §3/M5 (scope e test case).
3. Il codice che tocchi: `services/verifier/executor/run_and_measure.py`,
   `gauge_check.py`, `watcher.py`, `services/verifier/app.py`,
   `services/orchestrator/generate_and_verify.py`, `retry_policy.py`,
   `virtual_memory.py`, `sketch_schema.py`, `sketch_compiler.py`,
   `services/stream-agent/app.py`, `presets.json`,
   `.github/workflows/regression.yml`, `docker-compose.yml`.

## Metodo, vincolante per ogni blocco

Per ogni fix: (1) scrivi prima il test che riproduce il difetto e
**dimostra che fallisce sul codice attuale** (incolla l'output rosso nel
logbook di fase); (2) fix minimale; (3) test verde; (4) aggiungi lo script
a `.github/workflows/regression.yml`. Nessuna riscrittura oltre il
necessario: i vincoli storici restano (gauge-check in subprocess separato,
enum/frasi canned di `retry_policy.py` intoccati, mai numeri grezzi nel
prompt di L2, niente motore fisico/GPU).

Crea `docs/logbook_fase5.md` (stesso stile delle fasi 1–4) e tienilo
aggiornato blocco per blocco.

## Blocchi di lavoro, in ordine di priorità

Se contesto/tempo finiscono, fermati al confine di un blocco e dichiara
esattamente dove — non lasciare un blocco a metà.

### Blocco A — C1: contratto dimensionale per-feature (P2a) [bloccante]

`run_and_measure.py:131-150` confronta `max(bbox_x, bbox_y)` col nominale:
corretto per un tampone pieno (caso v14), sbagliato per il pezzo reale di
M3 (foro in blocco ospite più largo del nominale per schema). Fix deciso:

- il preset dichiara il contratto: nuovo campo per `thread` in
  `presets.json` (es. `dimensional_check: "gauge"`) — il confronto
  bbox-vs-nominale **non** si applica; al suo posto un sanity check:
  `bbox_z ≈ engagement_length_mm` (tolleranza 1e-3) e `bbox_x/y ≥`
  diametro maggiore. La misura dimensionale vera del foro è il collaudo
  GO/NO-GO (Blocco B).
- comportamento legacy (bbox-vs-nominale) resta per spec **senza** preset
  o con contratto non dichiarato — gli script M1/M2 esistenti non devono
  regredire.
- la spec passata a `run_and_measure` deve portare l'informazione
  necessaria (l'orchestratore già arricchisce con il preset — estendi
  l'arricchimento, non creare un canale nuovo).

**Test TC-M5-1** (nuovo `verify_dimensional_contract.py`): il codice del
foro M6 in blocco 20×20×8 (riusa la spec a mano di
`verify_sketch_compiler_thread.py`) attraverso un job `run_and_measure`
reale con spec `thread M6 tol 0.3` → oggi FAIL dimensionale (dimostralo),
dopo il fix PASS + GO sweep PASS sul pezzo esportato (residuo ≤ 0.5mm³).
Aggiungi il caso inverso: blocco con bbox_z sbagliato (es. 12mm vs
engagement 8) → FAIL di sanity.

### Blocco B — C2: NO-GO nel loop [bloccante]

`generate_and_verify.py:157-167` chiama solo il calibro GO. Fix: dopo un
GO PASS, chiama anche il NO-GO (`gauge_nogo_step` del preset, stesso mode)
con **semantica invertita**: interferenza rilevata = pezzo OK; nessuna
interferenza = pezzo FAIL con errore stabile
`gauge_check_nogo_no_interference`. Il caso è PASS solo con GO che passa E
NO-GO che interferisce. Il FAIL NO-GO alimenta `classify_checkpoint` come
gli altri (ricadrà su RETRY_GENERIC: nessun hint nuovo inventato).

**Test TC-M5-2** (estendi `verify_gauge_check_loop_wiring.py` o script
nuovo): scenario mock con GO PASS + NO-GO senza interferenza → verdetto
finale FAIL (oggi: PASS silenzioso — dimostralo prima). E scenario felice:
GO PASS + NO-GO con interferenza → PASS.

### Blocco C — C3: `snap_fit` non deve rompere l'orchestratore [bloccante]

`generate_and_verify.py:172-173` pretende `gauge_go_step` per qualunque
`gauge_check_mode` — ma `min_distance` non usa calibro. Fix: per
`mode == "min_distance"` costruisci il job dai `measurement_points` del
preset (`presets.json:30-38`: `point_a_mm`, `point_b_mm`, `nominal_mm`,
`tolerance_mm` → campo `min_distance` del job), niente `gauge_step_path`;
il requisito `gauge_go_step`/`gauge_nogo_step` resta solo per i mode a
calibro.

**Test TC-M5-3**: spec `snap_fit` attraverso il loop mockato → oggi
`ValueError`/"Preset incoerente" (dimostralo), dopo il fix il job
`/gauge-check` contiene `mode: "min_distance"` e i punti del preset
(assert sui contenuti), e un esito FAIL/PASS del min_distance viene
gestito dal loop come gli altri.

### Blocco D — C7: split esecuzione/verdetto (P5)

`run_and_measure.py` esegue `exec(code)` nello stesso processo che scrive
il result: il codice non fidato può contraffare un PASS (review C7). Fix:

- processo 1 (non fidato, limiti attuali): `exec(code)` + solo export
  STEP del solido `result` su un path deciso dal chiamante. Non scrive
  mai il result JSON. Rimuovi la leggibilità di `/models` a questo
  processo (non gli serve: non fa gauge-check).
- processo 2 (fidato, mai `exec` di codice esterno): importa lo STEP
  esportato, fa validità/bbox/sanity dimensionale, scrive il result.
- `watcher.py` orchestra i due passi; il timeout complessivo per il job
  "code" resta quello attuale (15s esterno) salvo misura contraria — se
  l'import+misura sfora, misura il nuovo worst-case e documentalo (regola
  #3 del piano), non alzare a intuito.

**Test TC-M5-5** (`verify_verdict_integrity.py`): fixture avversariale —
codice che scrive un result JSON `execution: PASS` contraffatto sul path
atteso e chiama `os._exit(0)` → oggi il PASS contraffatto viene accettato
(dimostralo), dopo il fix il verdetto finale è FAIL. Più regressione: il
caso valido normale continua a passare identico.

### Blocco E — C4: strategia `param_first` (P3) + fix sketch-first

- Nuova strategia `L2_STRATEGY=param_first` in `generate_and_verify.py`:
  L2 restituisce **solo** JSON di parametri
  (`{"major_diameter_mm", "pitch_mm", "engagement_length_mm", "host_xy_mm"}`),
  validati con range fisici (positivi, `r_minor > 0` con l'angolo del
  preset, host > diametro); il compilatore costruisce internamente la
  spec sketch canonica (stessa trigonometria di
  `generate_thread_gauge.build_thread_plug`) e riusa
  `compile_thread_sketch_to_code` — nessuna seconda via geometrica.
- Fix sketch-first: `NUMERIC_TOLERANCE_MM` → 1e-3, `NUMERIC_TOLERANCE_DEG`
  → 0.1 (`sketch_schema.py:46-47`); `is_crest` in `sketch_compiler.py:114`
  agganciato alla stessa `CROSS_FIELD_TOLERANCE_MM` (chiude la finestra di
  non-manifold: qualunque punto accettato come cresta dal cross-check
  riceve l'overlap).

**Test TC-M5-7** (`verify_param_first.py`): parametri M6 → STEP con gli
stessi numeri del caso a mano (GO residuo ≈ 0.3059mm³, NO-GO ≈ 20.158mm³,
stessi riferimenti di `verify_sketch_compiler_thread.py`); una spec sketch
con coordinate arrotondate a 4 decimali oggi FAIL di schema (dimostralo),
dopo il fix valida; un punto di cresta a `r_major - 5e-4` compila con
overlap applicato.

### Blocco F — C5 (parte codice): memoria virtuale onesta e revocabile

In `virtual_memory.py` / `retry_policy.py`:

1. `SPEC_KEY_FIELDS` += `tolerance`, `pitch` (`virtual_memory.py:46`);
2. `count_virtual_failures` conta **case_id distinti** con almeno un FAIL
   geometrico, non i tentativi;
3. gli errori non geometrici (generazione/JSON/schema — riconoscibili
   dall'`outcome_error`) non contano per l'esclusione: aggiungi un campo
   esplicito al record (es. `failure_class: "geometric"|"generation"`)
   scritto da `generate_and_verify.py`, invece di parsare stringhe;
4. ogni record nuovo porta `checker_version` (hash corto di
   `gauge_check.py` + `run_and_measure.py`, calcolato a runtime);
   `should_exclude_strategy` conta solo i FAIL della versione corrente —
   un fix del checker azzera il pregiudizio, come deciso in P4.

Compatibilità: i record vecchi senza i campi nuovi non devono rompere la
lettura (vengono semplicemente esclusi dal conteggio, comportamento
conservativo fail-open verso la generazione).

**Test TC-M5-6** (estendi `verify_virtual_memory.py`): 3 FAIL dello stesso
`case_id` → conta 1; 2 case distinti → conta 2; record `generation` →
esclusi; record con `checker_version` diversa → esclusi; fixture M4
esistente ancora verde (aggiorna la fixture se serve, dichiarandolo).

### Blocco G — C6: id stabili e offset persistito (stream-agent)

`services/stream-agent/app.py:168,222`: sostituisci
`abs(hash(...)) % (2**63)` con id deterministico dal contenuto
(`uuid.uuid5(NAMESPACE_URL, key)` in forma stringa, o int da
`hashlib.md5(key).hexdigest()`); persisti `_virtual_log_offset` su file
accanto al log (riletto all'avvio); `_indexed_files` ricostruibile o con
bound. Nessuna istanza Qdrant serve per il test: la funzione id è pura.

**Test TC-M5-4** (`verify_stream_agent_ids.py`, senza Qdrant): due
subprocess con `PYTHONHASHSEED` diversi calcolano gli id della stessa
fixture → identici (oggi: diversi, dimostralo); offset scritto/riletto da
file tra due "riavvii" simulati; collisione `abs()` non più possibile per
costruzione (id derivati da digest).

### Blocco H — quick win C10

1. `check_result_assigned` (`services/verifier/app.py:105-117`): accetta
   anche `ast.AnnAssign` con target `result`. Test in uno script nuovo o
   esistente: `result: cq.Workplane = ...` → PASS (oggi FAIL, dimostralo).
2. CI anche sui branch di sessione: `regression.yml` trigger `push` su
   `develop` **e** `claude/**` (le PR restano com'erano).
3. Cleanup `/exec/parts`: il watcher rimuove gli STEP generati più vecchi
   di `GENERATED_PARTS_RETENTION_SECONDS` (default generoso, es. 24h) a
   ogni ciclo — gap dichiarato in PR #11, mai chiuso.
4. `docker-compose.yml`: bind `127.0.0.1:` sulle porte pubblicate di
   qdrant, ollama, verifier, stream-agent (flowise/open-webui/dashboard a
   discrezione, documenta la scelta); pinna l'immagine Flowise a una
   versione esplicita invece di `latest` (scegli l'ultima stabile
   corrente e scrivila nel logbook). Verifica con `docker compose config`
   (l'esecuzione reale è scope M7).

## Cosa NON fare

- Non toccare: l'isolamento del gauge-check (subprocess separato, timeout
  indipendenti), l'enum/le frasi canned di `retry_policy.py`, il firewall
  due-collezioni di M4, i calibri STEP versionati.
- Non "sistemare" altro che incontri per strada: se trovi un bug fuori
  scope, aprilo come nota sull'issue, non allargare la PR implicita.
- Niente PR, niente merge: push sul tuo branch e commento finale
  sull'issue M5.

## Criterio di accettazione

- Gli 8 test nuovi (TC-M5-1…8, come numerati in `docs/piano_recupero.md`)
  verdi, ognuno con l'output rosso pre-fix documentato in
  `docs/logbook_fase5.md`;
- i 14 script esistenti verdi senza regressioni (riesegui tutto, non solo
  i tuoi);
- `regression.yml` aggiornato con i nuovi script;
- `docs/logbook_fase5.md` completo (un blocco = una sezione, con i numeri).

## A fine lavoro

- Aggiorna `docs/logbook_fase5.md` e la tabella milestone in
  `docs/logbook.md` (aggiungi la riga M5).
- Commenta l'esito **per intero** sull'issue GitHub di M5 (commento
  autosufficiente: cosa fatto, output dei test, riserve oneste).
- Commit e push sul branch assegnato a questa sessione. NON aprire PR.
- Il supervisore rieseguirà i test indipendentemente prima del gate M6:
  scrivi il logbook in modo che la riesecuzione sia un copia-incolla.
