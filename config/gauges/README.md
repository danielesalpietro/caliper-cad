# `config/gauges/` — calibri di riferimento per il collaudo virtuale

Formato dei calibri Go/No-Go usati dal gauge-check del Livello 3, fase 3
(vedi [`docs/logbook.md`](../../docs/logbook.md) e
[`docs/logbook_fase1.md`](../../docs/logbook_fase1.md), M1). Pattern
analogo a [`config/prusaslicer/`](../prusaslicer/): configurazione
versionata nel repository, non uno stato nascosto altrove.

## Vincolo su come vengono prodotti

**Questi file STEP non sono l'output di un LLM in un loop di
generazione.** Un calibro generato dallo stesso tipo di processo che
dovrebbe validare non sarebbe più un riferimento indipendente —
invaliderebbe l'intero controllo (vedi `docs/logbook.md`, punto 3 della
revisione critica, e `docs/handoff_m1.md`).

Questo non significa "mai codice": la coppia GO/NO-GO per `thread`
(M6, ISO 68-1) è generata da
[`generate_thread_gauge.py`](generate_thread_gauge.py), uno script
**deterministico** basato su formule ISO note (profilo a V, angolo
60°), non su un prompt interpretato da un modello — eseguito una
tantum da un umano (o da CI su richiesta esplicita), mai dall'LLM nel
loop di generazione/verifica che il gauge stesso serve a controllare.
Ogni export si auto-verifica prima di essere scritto (validità del
solido, bounding box, volume plausibile) — vedi lo script per il
dettaglio e `manifest.json` per la provenienza di ogni singola coppia.

## Formato

File STEP a livello piatto, nome `<feature>_<GO|NOGO>_<nominale>_<norma>.step`:

```
config/gauges/
  README.md
  manifest.json
  generate_thread_gauge.py
  thread_M6_GO_ISO68-1.step
  thread_M6_NOGO_ISO68-1.step
```

- **GO** — deve poter accoppiarsi/percorrere il pezzo senza
  interferenza, se il pezzo è dentro tolleranza.
- **NOGO** — deve interferire, se il pezzo è dentro tolleranza (la sua
  funzione è rilevare un pezzo fuori tolleranza in eccesso).
- Entrambi in **STEP** (B-Rep), non STL — confronto booleano esatto,
  coerente col Rischio #3 dell'architettura ("verifica parametrica
  preferita alla verifica su mesh, dove possibile").

Il calibro `thread_M6_ISO68-1` è un **tampone filettato esterno**
(thread plug gauge), pensato per verificare un **foro** filettato
(coerente con l'esempio L2.5 in architettura, "foro filettato M6") —
non un anello. Se in futuro serve verificare una filettatura esterna
generata dalla pipeline, serve un calibro ad anello separato, non
questo stesso file riusato al contrario.

`manifest.json` è il registro: un'entrata per coppia, con
provenienza/stato di verifica — analogo a
`services/orchestrator/presets.json` per i preset di feature. Il preset
corrispondente in `presets.json` referenzia gli stessi path relativi nei
campi `gauge_go_step`/`gauge_nogo_step`.

## Come vengono letti

`verifier-executor` monta questa directory **read-only** su `/gauges`
(vedi `docker-compose.yml`) — nessuna nuova rotta HTTP, nessun
trasferimento di contenuto tramite il protocollo job/result: il job
porta solo il path relativo (es. `thread_M6_GO_ISO68-1.step`),
`gauge_check.py` lo risolve sotto `/gauges` (vedi
`resolve_under_root()` in quel file, che rifiuta ogni path che uscirebbe
dalla radice montata).

I pezzi da verificare (STEP noti/statici, non generati in questo stesso
job) seguono lo stesso pattern sotto `${DATA_DIR:-./data}/models`,
montato su `/models`.

## Stato di verifica

Verificati staticamente allo script (validità, bbox, volume, periodicità
dell'elica) e, tramite `gauge_check.py`, contro un pezzo di controllo
generico (foro liscio nominale) — vedi
`docs/logbook_fase1.md`, sezione "Verifica end-to-end sui calibri
reali". **Non ancora verificati contro un foro filettato reale prodotto
dalla pipeline L2** (fuori scope di M1, riguarda M3 — "pipeline
sketch-first → compilazione → collaudo").
