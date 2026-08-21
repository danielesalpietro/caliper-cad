# Handoff — M1: Scaffold di isolamento + calibri di riferimento

Prompt pronto per la sessione che implementa M1. Copiabile così com'è
come primo messaggio di una nuova sessione Claude Code.

---

Riprendi il progetto CALIPER (danielesalpietro/caliper-cad) — layer di
verifica deterministica per geometrie CAD generate da LLM.

Leggi in ordine, prima di scrivere codice:

1. `docs/logbook.md` — quadro generale del "Ciclo di Collaudo Virtuale" e
   la revisione critica già fatta (perché niente motore fisico/GPU)
2. `docs/logbook_fase1.md` — la milestone M1, oggetto di questo compito
3. GitHub issue [#2](https://github.com/danielesalpietro/caliper-cad/issues/2) (M1) — stesso contenuto, per i commenti eventuali
4. `docs/architettura-prototipo-mesh-llm.md` — architettura completa
   (Livelli 1-8, Rischi numerati, Policy di retry)
5. `services/verifier/executor/run_and_measure.py` e `watcher.py` — il
   verifier-executor esistente, isolato (`network_mode: none`), che
   estenderai
6. `services/orchestrator/presets.json` — solo il preset `thread` è
   `defined: true` oggi

Stato del repo: PR [#6](https://github.com/danielesalpietro/caliper-cad/pull/6) (branch `claude/ripresa-progetto-hbvy5k` → `develop`)
contiene tutti i logbook e non è ancora mergiata. Se è già stata
mergiata quando leggi questo, riparti da `develop`; se non lo è ancora,
verifica comunque che i file sopra siano presenti nel branch da cui
parti (contengono le decisioni vincolanti sotto).

## Compito: implementare M1 — Scaffold di isolamento + calibri di riferimento

Deliverable, dalla checklist "Stato" di `docs/logbook_fase1.md`:

1. Formato `config/gauges/` per calibri STEP versionati (pattern analogo a
   `config/prusaslicer/`) — NON generati dall'IA, modellati con CAD
   convenzionale. Primo calibro: coppia GO/NO-GO filettata per M6, ISO
   68-1 (unico preset `thread` già `defined: true`).
2. Estendi `services/orchestrator/presets.json` col preset `thread`:
   campi `gauge_go_step` / `gauge_nogo_step`.
3. Modulo di calcolo interferenza/distanza in
   `services/verifier/executor/` — nuovo script o estensione di
   `run_and_measure.py`, via CadQuery/OCC (boolean intersection,
   `BRepExtrema_DistShapeShape`). Riusa il protocollo job/result sul
   volume condiviso `verifier_exec` già esistente tra `verifier` e
   `verifier-executor` — NIENTE nuova rotta HTTP (vedi Rischio #9).
4. Nuovo blocco `gauge_check` nel result JSON, accanto a
   `dimensional_check` già presente.
5. Test di determinismo esplicito: stesso input eseguito due volte →
   output identico byte per byte. È parte del criterio di accettazione,
   non opzionale.

## Vincoli già decisi, non rinegoziabili senza confronto esplicito con l'utente

- **Niente container GPU/motore fisico** — CPU-only, estensione di
  `verifier-executor`. Motivazione con evidenza reale in `docs/logbook.md`
  (bug `[v14]`: solido manifold ma dimensionalmente sbagliato, un
  collision check non l'avrebbe colto).
- Il gauge-check va isolato in un **subprocess separato** da `exec(code)`,
  con timeout proprio e distinto (`gauge_check_timeout` vs
  `execution_timeout`) — decisione presa in issue #3 (M2), ma la forma del
  protocollo va già predisposta qui in M1. Timeout tarato per misura
  empirica durante il batch, non a intuito.
- Log su timeout: solo dati noti prima del blocco (spec strutturata,
  diagnostica pre-flight economica) — mai una spiegazione causale
  post-hoc (un `SIGKILL` non lascia nulla da ispezionare). Formato JSON
  indicativo già in `docs/logbook_fase2.md`.
- Rimane aperta la domanda su se servirà mai un solver FEA per
  deformazione elastica (fuori scope qui) — non deciderla per
  assunzione.

## A fine lavoro

- Aggiorna la checklist "Stato" in `docs/logbook_fase1.md` (e la sezione
  pertinente di `logbook_fase2.md` se tocchi il protocollo del
  gauge-check).
- Commenta l'esito su GitHub issue [#2](https://github.com/danielesalpietro/caliper-cad/issues/2).
- Commit e push sul branch assegnato a questa sessione. NON aprire PR
  senza che te lo chieda esplicitamente, NON mergiare la #6.
- Se una scelta implica un trade-off architetturale non coperto dai
  logbook (es. libreria per `BRepExtrema`, formato esatto di
  `config/gauges/`), chiedi prima di procedere — è lo stesso stile di
  lavoro già usato finora su questo progetto: decisioni motivate,
  documentate, non assunte.
