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
- [ ] Collegare `/gauge-check` al loop di `generate_and_verify.py` per
      davvero (oggi chiama solo `/verify`) — prerequisito perché
      `classify_checkpoint` smetta di ricadere sempre su `RETRY_GENERIC`
      e perché il criterio di accettazione di M3 (collaudo Go/No-Go nel
      loop) sia soddisfatto
- [ ] Schema JSON dei vincoli di sketch 2D definito (punti, linee, archi,
      quote, tipo di vincolo) — con validazione a livello di schema, non
      solo dopo la generazione
- [ ] Meccanismo di conferma umana della specifica (dipendenza già aperta
      dal Livello 2.5, vedi architettura — non ancora progettato,
      riguarda anche questa milestone)
- [ ] Strategia "sketch-first" aggiunta a `generate_and_verify.py` come
      modalità alternativa del nodo L2
- [ ] Compilazione vincoli-2D → CadQuery → STEP verificata
- [ ] Prima esecuzione end-to-end sul preset `thread` documentata con
      esito reale (non solo test sintetico) — **richiede un'istanza
      Flowise viva**: se non disponibile, dichiararlo esplicitamente come
      bloccante, non aggirare con un mock della generazione stessa
