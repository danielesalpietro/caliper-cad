# Logbook — M3: Pipeline sketch-first → compilazione → collaudo

Vedi [`logbook.md`](logbook.md) per il quadro generale. Dipende da M2
(controlli validati su geometrie note, PR [#8](https://github.com/danielesalpietro/caliper-cad/pull/8)) — vedi
[`logbook_fase2.md`](logbook_fase2.md).

Prompt di handoff pronto per la sessione che implementa questa milestone:
[`handoff_m3.md`](handoff_m3.md).

## Obiettivo (mantenuto, con ambito ristretto)

Proposta originale: ingegnerizzare i prompt per forzare i modelli a
produrre solo vincoli di sketch 2D, collegare l'output al compilatore per
estrudere il 3D; milestone: prima esecuzione end-to-end completa.

**Valutazione:** l'idea è buona e va tenuta — restringe la superficie
libera che l'LLM può inventare (profili, coordinate, chiamate API
inesistenti — vedi Rischio #1, #3, #5, e l'aneddoto reale su
`texture_thread()`/`clearance=` inventati) a un insieme dichiarativo di
vincoli (punti, linee, archi, quote, tipo di vincolo), più facile da
validare sintatticamente **prima ancora** di raggiungere il kernel
geometrico — coerente con l'indicazione già in README §3.1 di vincolare
l'output allo schema a livello di decoding, non solo dopo generazione.

## Cosa cambia rispetto alla proposta originale

1. **Non è un nuovo componente, è una modalità del Livello 2 esistente.**
   `services/orchestrator/generate_and_verify.py` collega già L2.5→L2 fuori
   da Flowise (decisione presa in v13, per gli stessi bug di
   interpolazione variabili già documentati). "Sketch-first" va aggiunto
   come strategia alternativa/componibile per il nodo L2 (coerente col
   modello a nodi tipizzati e sostituibili di §4 dell'architettura), non
   come riscrittura.
2. **La milestone end-to-end si applica al solo preset `thread`.** È
   l'unico con `defined: true` in `presets.json` oggi. Rivendicare
   "end-to-end completo" senza specificare l'ambito ripeterebbe l'errore
   già evitato altrove nel progetto (non inventare capacità non
   testata) — press_fit/snap_fit/boss restano `defined: false` finché non
   hanno sia un preset di geometria sia un calibro di riferimento (M1).
3. **Il compilatore (kernel geometrico) esiste già** —
   `run_and_measure.py` esegue CadQuery isolato. "Layer 2" della proposta
   originale non è un nuovo componente da costruire, è il collegamento tra
   l'output vincoli-2D e il codice CadQuery che il kernel già sa eseguire
   (estrusione/rivoluzione a partire dai vincoli, non da codice libero
   generato dall'LLM).
4. **Direzione a lungo termine per il retry su timeout del gauge-check
   (vedi [`logbook_fase2.md`](logbook_fase2.md#come-il-checkpoint-arriva-al-livello-2-in-retry-senza-farlo-spiegare)).**
   Oggi un hint di retry può arrivare a L2 solo come frase canned in un
   prompt testuale, perché L2 genera codice libero. Con lo schema
   sketch-first di questa milestone, lo stesso enum di classificazione
   (es. `SWEEP_TIMEOUT_EARLY`) potrà invece clampare direttamente un campo
   numerico dei vincoli (es. limite al numero di segmenti del profilo) —
   più deterministico di un'istruzione testuale. Non bloccante per M3, ma
   da tenere presente nello schema dei vincoli fin dall'inizio.
5. **[post-M2] Il gauge-check va finalmente collegato al loop reale, non
   solo invocato da script di verifica manuali.** M2 ha lasciato una
   riserva onesta: `generate_and_verify.py` chiama solo `/verify`, mai
   `/gauge-check` — `classify_checkpoint` ricade quindi sempre su
   `RETRY_GENERIC` in pratica. Il criterio di accettazione di M3 (prompt
   → vincoli → STEP → **collaudo Go/No-Go** → log) richiede che questo
   collegamento esista per davvero, non che resti un'invocazione manuale
   — è la prima volta che `/gauge-check` viene chiamato da un
   orchestratore, non da un umano che lancia uno script.
6. **[post-M2, bug trovato e corretto in questa preparazione]**
   L'endpoint HTTP `POST /gauge-check` (`services/verifier/app.py`) non
   era mai stato aggiornato quando M2 ha aggiunto le modalità `sweep` e
   `min_distance` a `gauge_check.py`: accettava solo
   `part_step_path`/`gauge_step_path`, senza `mode` — via HTTP era
   raggiungibile solo `static_interference`. Gli script di verifica di M2
   non se ne sono accorti perché parlano direttamente con
   `gauge_check.py`, bypassando l'endpoint. Corretto prima di scrivere
   questo handoff (`GaugeCheckRequest` ora porta `mode`/`sweep`/
   `min_distance`, verificato con un test end-to-end reale: richiesta →
   job → `gauge_check.py` → risultato, PASS confermato sui calibri M6) —
   altrimenti M3 ci sarebbe sbattuto contro al primo tentativo di
   chiamare `/gauge-check` in modalità `sweep` per TC2.
7. **Dipendenza nuova, non presente in M1/M2: un'istanza Flowise viva.**
   M1 e M2 sono stati verificati interamente fuori Flowise (script che
   parlano direttamente con `gauge_check.py`/`run_and_measure.py`, mai
   una vera generazione L2). Il criterio di accettazione di M3 richiede
   "prompt testuale → vincoli di sketch 2D" — cioè una generazione LLM
   reale. Se l'ambiente di questa sessione non ha un'istanza Flowise
   raggiungibile con una API key valida (stesso limite già incontrato per
   Docker in M1/M2), l'esecuzione end-to-end **non è simulabile
   onestamente con un mock** come è stato fatto per il retry loop in M2
   (lì il mock testava la logica del loop, non la generazione stessa) —
   va dichiarato esplicitamente come bloccante, non aggirato.

## Milestone (criterio di accettazione, ristretto)

Prima esecuzione end-to-end riuscita, **limitata al preset `thread`
(M6, ISO 68-1)**: prompt testuale → vincoli di sketch 2D strutturati →
compilazione a STEP → collaudo Go/No-Go (M1/M2) → log PASS/FAIL. Non
rivendica copertura di altre feature class.

## Stato

- [x] **[pre-M3]** `POST /gauge-check` esteso con `mode`/`sweep`/
      `min_distance` (era rimasto fermo a `static_interference` di M1) —
      verificato con un test end-to-end reale (richiesta → job →
      `gauge_check.py` → risultato PASS sui calibri M6 reali)
- [x] **Bug trovato prima di collegare il loop (stessa disciplina di
      M1/M2): `run_and_measure.py` non esportava mai il pezzo generato
      come STEP**, e `/models` è montato **read-only** in
      `verifier-executor` — un pezzo appena generato non poteva finirci.
      Aggiunta una radice separata e scrivibile,
      `/exec/parts` (sottocartella del volume `verifier_exec` già in
      uso, nessun nuovo mount), con `part_source: "models"|"generated"`
      in `gauge_check.py`/`POST /gauge-check` per distinguere sempre i
      pezzi di riferimento statici (M1/M2) da quelli appena generati —
      mai confusi, stesso spirito del firewall `source: virtual|physical`
      di M4. Verificato con
      `services/verifier/executor/verify_gauge_check_part_source.py`
      (radici separate, nessuna regressione sul default `models`,
      `part_source` non valido rifiutato esplicitamente) e con
      `services/verifier/executor/verify_run_and_measure_export.py`
      (STEP esportato solo su geometria valida).
- [x] **Secondo bug trovato: `GAUGE_CHECK_HTTP_TIMEOUT_SECONDS` in
      `services/verifier/app.py` era rimasto a 60s** (tarato sul
      placeholder M1 di 45s esterno) e non era mai stato aggiornato
      quando M2 ha ricalibrato il timeout del watcher a 150s — l'endpoint
      HTTP avrebbe rinunciato ad aspettare prima che il watcher
      dichiarasse un vero TIMEOUT diagnosticabile. Portato a 200s.
- [x] **Terzo bug: `call_verifier()` in `generate_and_verify.py` non
      mandava mai `spec` al verifier** — il confronto dimensionale in
      `run_and_measure.py` (feature `"thread"`) non scattava mai
      attraverso l'orchestratore, solo negli script di verifica manuali.
      Corretto: ora invia la spec del tentativo corrente.
- [x] Collegato `/gauge-check` al loop di `generate_and_verify.py` per
      davvero: dopo un PASS di `/verify`, se il preset della feature
      definisce `gauge_check_mode` (solo `thread` in questa milestone),
      il loop chiama anche `/gauge-check` (mode `sweep`, calibro GO) sul
      pezzo appena esportato — il caso è PASS solo se **entrambi**
      passano; un FAIL/TIMEOUT del gauge-check alimenta
      `classify_checkpoint` per il tentativo successivo, invece di
      ricadere sempre su `RETRY_GENERIC`. `presets.json` (`thread`) esteso
      con `gauge_check_mode`/`pitch_mm`/`engagement_length_mm`/
      `sweep_steps` (stessi valori già verificati in
      `verify_gauge_check_tc2.py`) — `engagement_length_mm` ha finalmente
      una casa reale, non più solo un placeholder locale a
      `generate_thread_gauge.py`. Verificato in
      `services/orchestrator/verify_gauge_check_loop_wiring.py` (5
      scenari: successo diretto, recupero dopo TIMEOUT con directive
      corretta nel retry_context, feature senza gauge-check, uscita
      anticipata su FAIL ripetuto, spec inoltrata correttamente) — stessa
      classe di mock già usata in M2 per `verify_retry_policy.py` (logica
      del loop, non la generazione).
- [x] Schema JSON dei vincoli di sketch 2D definito
      (`services/orchestrator/sketch_schema.py`): punti/linee/archi/quote
      con tipo di vincolo (`distance`/`angle`/`radius`), validato in
      quattro livelli (struttura, riferimenti, topologia — polilinea
      chiusa —, consistenza numerica quota↔coordinate e
      sketch↔operation) **prima** di raggiungere il kernel geometrico
      (README §3.1). Verificato in `verify_sketch_schema.py` (8 casi,
      incluso un bug reale trovato scrivendo il primo caso a mano: il
      calcolo dell'angolo tra due linee adiacenti dava il supplementare,
      120° invece di 60°, per un errore di convenzione sul verso dei
      vettori — corretto). Campo `engagement_length_mm` presente
      nell'`operation` fin dall'inizio, come richiesto dal punto 4 sopra.
- [x] Compilazione vincoli-2D → CadQuery verificata (non solo "a STEP":
      l'intera catena, vedi sotto) —
      `services/orchestrator/sketch_compiler.py` produce testo (mai
      esegue cadquery lui stesso — resta fuori dal confine di fiducia di
      Rischio #9, il codice prodotto passa dallo stesso `/verify` isolato
      del codice libero). Caso di prova **scritto a mano** (M6, ISO
      68-1) in `verify_sketch_compiler_thread.py`: spec → validazione →
      compilazione → `exec()` → STEP → collaudo Go/No-Go **reale** sui
      calibri M6 versionati (non sintetici) — GO **PASS** su tutti i 21
      step (residuo 0.305925mm³, praticamente identico ai 0.305928mm³ già
      documentati in TC2), NO-GO **FAIL** con interferenza rilevata
      (20.158069mm³ vs 20.158363mm³ di TC2) — stessa geometria del
      percorso già validato in M2, ora costruita da vincoli dichiarativi.
      **Bug reale trovato scrivendo questo caso di prova:** un blocco
      ospite più profondo della lunghezza di impegno lascia materiale
      pieno non filettato oltre l'impegno — il calibro ci sbatteva contro
      durante lo sweep (falsa interferenza, ~8.4mm³ al primo step).
      Vincolo aggiunto allo schema: il blocco ospite deve avere
      profondità **esattamente uguale** a `engagement_length_mm` in
      questa milestone (foro passante, stessa scelta già validata in
      `verify_gauge_check_tc2.py` — un foro cieco più profondo del
      filetto resta fuori scope, richiederebbe un controcavo che il
      compilatore non modella ancora).
- [x] Strategia "sketch-first" aggiunta a `generate_and_verify.py` come
      modalità **componibile** del nodo L2 (`L2_STRATEGY=sketch_first`,
      default invariato `free_code`) — non una riscrittura: stesso loop,
      stesso protocollo `/verify`+`/gauge-check`, cambia solo come si
      ottiene il codice da verificare. Un errore di generazione/
      validazione/compilazione (JSON malformato, spec che non passa lo
      schema, feature non ancora supportata dal compilatore) è un FAIL
      immediato del tentativo, **senza nemmeno chiamare `/verify`**,
      classificato `RETRY_GENERIC` (mai un hint inventato — `retry_policy.py`
      non è stato toccato, come da vincolo). Verificato in
      `verify_sketch_first_strategy.py` (4 scenari, con `call_flowise_l2`
      mockata: successo con codice compilato — non JSON grezzo — inviato
      a `/verify`, testo non-JSON, spec che non valida lo schema, feature
      non supportata).
- [ ] **Meccanismo di conferma umana della specifica** — dipendenza già
      aperta dal Livello 2.5 (vedi architettura), non toccata in questa
      milestone: fuori dall'ambito diretto del collaudo Go/No-Go, non
      affrontata per mancanza di tempo/scope, resta lavoro futuro esplicito.
- [ ] **Prima esecuzione end-to-end reale sul preset `thread` — NON
      raggiunta.** Nessuna istanza Flowise raggiungibile in questo
      sandbox (né `FLOWISE_URL`/`FLOWISE_API_KEY` impostate, né un
      demone Docker disponibile — stesso limite già incontrato in M1/M2)
      — verificato esplicitamente (`curl` a `localhost:3000` fallisce,
      `docker ps` non trova il socket). Come richiesto dall'handoff: non
      aggirato con un mock della generazione stessa (i mock usati sopra
      testano solo la *logica del loop*, mai rivendicata come esecuzione
      reale). Il criterio di accettazione della milestone (prompt
      testuale → vincoli → STEP → collaudo → log, con una vera
      generazione L2) resta quindi **parzialmente raggiunto**: tutta la
      catena a valle della generazione (schema, compilatore, wiring del
      gauge-check, loop di retry) è costruita e verificata con casi
      scritti a mano; la generazione L2 reale (via chatflow "sketch-first"
      — non versionato in `services/flowise/chatflows/`, vive solo in
      un'istanza Flowise configurata a mano) resta esplicitamente non
      verificata, primo compito per chi eredita M3 con accesso a Flowise.
