# Handoff — M3: Pipeline sketch-first → compilazione → collaudo

Prompt pronto per la sessione che implementa M3. Copiabile così com'è
come primo messaggio di una nuova sessione Claude Code.

Premessa, per chi lo eredita di seconda mano: M1 e M2 sono stati
revisionati criticamente e in modo indipendente (non solo letti —
rieseguiti, con gli stessi numeri riprodotti) prima di scrivere questo
handoff. In preparazione a M3 ho trovato e corretto un altro bug reale,
stessa categoria di quello del `Dockerfile` in M1: l'endpoint HTTP
`POST /gauge-check` non era mai stato aggiornato quando M2 ha aggiunto
le modalità `sweep`/`min_distance` a `gauge_check.py` — accettava solo
`static_interference`. Corretto e verificato end-to-end (richiesta →
job → `gauge_check.py` → risultato reale sui calibri M6) prima di
scrivere questo documento.

---

Riprendi il progetto CALIPER (danielesalpietro/caliper-cad) — layer di
verifica deterministica per geometrie CAD generate da LLM.

## Da dove partire (importante, non ovvio)

Il lavoro di M1+M2 **non è ancora mergiato in `develop`** — è a catena
su due branch/PR non ancora unite:

- PR [#7](https://github.com/danielesalpietro/caliper-cad/pull/7) — M1, `claude/handoff-m1-docs-bm01i6` → `develop`
- PR [#8](https://github.com/danielesalpietro/caliper-cad/pull/8) — M2, `claude/handoff-m2-docs-9kyeg0` → branch di M1

Parti dal branch `claude/handoff-m2-docs-9kyeg0` (che contiene già tutto
M1+M2+la correzione dell'endpoint), non da `develop`:
`git checkout -B <tuo-branch> origin/claude/handoff-m2-docs-9kyeg0`. Se
nel frattempo #7 e #8 sono state mergiate, riparti da `develop` —
verifica con `git log --oneline -5` che tu veda i file elencati sotto
prima di procedere.

## Ordine di lettura

1. `docs/logbook.md` — quadro generale, revisione critica
2. `docs/logbook_fase3.md` — la milestone M3, oggetto di questo compito
   (include già le correzioni fatte in preparazione — non ripartire da
   zero)
3. GitHub issue [#4](https://github.com/danielesalpietro/caliper-cad/issues/4) (M3)
4. `docs/logbook_fase1.md` e `docs/logbook_fase2.md` — cosa M1/M2 hanno
   consegnato
5. `services/orchestrator/generate_and_verify.py` — il loop che estendi
   (oggi chiama solo `/verify`, mai `/gauge-check` — vedi sotto)
6. `services/orchestrator/retry_policy.py` — contratto di retry già
   implementato, non riscriverlo
7. `services/verifier/app.py` — `POST /gauge-check` ora accetta
   `mode`/`sweep`/`min_distance` (corretto in preparazione a questo
   handoff)
8. `services/orchestrator/presets.json` — solo `thread` (M6, ISO 68-1)
   ha un calibro reale; `clearance_fit`/`snap_fit` hanno preset ma
   calibri/measurement_points illustrativi

## Verifica prima di iniziare (non dare per scontato l'ambiente)

A differenza di M1/M2 — completamente verificabili con `pip install
cadquery` fuori Docker — M3 richiede un ingrediente nuovo: **una
generazione L2 reale**, cioè un'istanza Flowise raggiungibile con una
API key valida (`FLOWISE_URL`, `FLOWISE_API_KEY` — vedi
`generate_and_verify.py`). Controlla subito se è disponibile in questa
sessione:

- **Se sì**: procedi con l'esecuzione end-to-end reale, è il criterio di
  accettazione di M3.
- **Se no**: non aggirarlo con un mock della generazione stessa (il mock
  usato in M2 per `verify_retry_policy.py` testava la *logica del loop*,
  non la generazione — qui la generazione è l'oggetto della milestone).
  Dichiara esplicitamente il blocco, scopo giù il lavoro a ciò che è
  verificabile senza Flowise (schema dei vincoli, compilazione
  vincoli→CadQuery→STEP con un caso di prova scritto a mano, non
  generato da un LLM) e segna l'esecuzione end-to-end reale come
  esplicitamente non fatta — stessa disciplina già applicata ai gap
  Docker in M1/M2.

## Cosa M1+M2 hanno già consegnato (non ripartire da zero)

- Calibri reali per `thread` M6 (tampone GO/NO-GO) e `clearance_fit`
  (spina GO/NO-GO Ø8, illustrativo) in `config/gauges/`.
- `gauge_check.py`: tre modalità (`static_interference`, `sweep`,
  `min_distance`), timeout calibrato empiricamente (100s interno/150s
  esterno), checkpoint pre-step per diagnosticare un TIMEOUT senza
  post-mortem su SIGKILL.
- `retry_policy.py`: budget 3 tentativi, uscita anticipata, enum di
  classificazione (`classify_checkpoint`) + frasi canned
  (`DIRECTIVE_TEXTS`) — **usalo così com'è**, non reinventarlo.
- `generate_and_verify.py`: loop di retry già funzionante, con due
  variazioni indipendenti per tentativo (directive testuale +
  temperatura crescente via `overrideConfig`, quest'ultima non ancora
  verificata contro un'istanza Flowise viva).
- `POST /gauge-check`: ora accetta `mode`/`sweep`/`min_distance` (fix di
  preparazione a questo handoff).

## Compito: implementare M3

Deliverable, dalla checklist "Stato" di `docs/logbook_fase3.md`:

1. **Collegare `/gauge-check` al loop reale.** Oggi `generate_and_verify.py`
   chiama solo `/verify` — `classify_checkpoint` ricade quindi sempre su
   `RETRY_GENERIC`. Dopo un PASS di `/verify` (sintassi/esecuzione/bbox),
   il loop deve chiamare anche `/gauge-check` (mode `sweep` per `thread`,
   usando `gauge_go_step`/`gauge_nogo_step` dal preset) prima di
   dichiarare il caso PASS per davvero.
2. **Schema JSON dei vincoli di sketch 2D** — punti, linee, archi, quote,
   tipo di vincolo. Validazione a livello di schema (JSON Schema o
   equivalente), non solo dopo la generazione (vedi README §3.1).
   Considera fin da subito un campo per la lunghezza di impegno/profondità
   (placeholder aperto da M1: `engagement_length_mm` in
   `generate_thread_gauge.py`/`manifest.json` — questa è l'occasione di
   dargli finalmente una casa nello schema reale).
3. **Modalità "sketch-first" in `generate_and_verify.py`**, come
   strategia alternativa componibile del nodo L2 — non una riscrittura.
4. **Compilazione vincoli-2D → CadQuery → STEP**, verificata su un caso
   di prova scritto a mano prima di fidarsi dell'output di un LLM.
5. **Prima esecuzione end-to-end sul preset `thread` (M6)**, documentata
   con esito reale — prompt → vincoli → STEP → collaudo Go/No-Go (via il
   collegamento del punto 1) → log PASS/FAIL. Ambito esplicitamente
   limitato a `thread`, non rivendicare copertura di `clearance_fit`/
   `snap_fit` (i loro calibri/measurement_points sono ancora
   illustrativi, non normativi).

## Vincoli già decisi, non rinegoziabili senza confronto esplicito con l'utente

- Niente motore fisico/GPU (vedi `docs/logbook.md`).
- `retry_policy.py` non si riscrive: enum fisso, frasi canned scritte da
  un umano, mai numeri grezzi nel prompt di L2.
- Il gauge-check resta in subprocess separato con timeout indipendente
  da `exec(code)` — non toccarne l'isolamento per "semplificare"
  l'integrazione col loop.
- Se aggiungi file eseguiti dentro `verifier-executor`, aggiorna il
  `Dockerfile` — controllalo esplicitamente, è la classe di bug già
  trovata due volte (M1: `gauge_check.py` mancante dalla `COPY`; qui in
  preparazione: l'endpoint HTTP non aggiornato per le nuove modalità).

## A fine lavoro

- Aggiorna la checklist "Stato" in `docs/logbook_fase3.md` e la riga di
  M3 nella tabella milestone di `docs/logbook.md`.
- Commenta l'esito su GitHub issue [#4](https://github.com/danielesalpietro/caliper-cad/issues/4).
- Commit e push sul branch assegnato a questa sessione. NON aprire PR
  senza che te lo chieda esplicitamente.
- Se l'esecuzione end-to-end reale non è stata possibile (niente
  Flowise), dillo chiaramente nel logbook e nel commento dell'issue —
  non presentarla come raggiunta se non lo è stata per davvero. Stesso
  stile di lavoro già usato finora: decisioni motivate, documentate,
  verificate per davvero, non assunte.
