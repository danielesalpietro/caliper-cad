# `config/gauges/` — calibri di riferimento per il collaudo virtuale

Formato dei calibri Go/No-Go usati dal gauge-check del Livello 3, fase 3
(vedi [`docs/logbook.md`](../../docs/logbook.md) e
[`docs/logbook_fase1.md`](../../docs/logbook_fase1.md), M1). Pattern
analogo a [`config/prusaslicer/`](../prusaslicer/): configurazione
versionata nel repository, non uno stato nascosto altrove.

## Vincolo, non negoziabile senza confronto esplicito con l'utente

**Questi file STEP NON vengono generati dall'IA.** Vanno modellati con
CAD convenzionale, una volta, con precisione nota — stesso motivo per
cui un calibro fisico si costruisce una volta e si riusa, non si
ridisegna ogni volta che serve (vedi `docs/logbook.md`, punto 3 della
revisione critica, e `docs/handoff_m1.md`). Un calibro generato dallo
stesso tipo di processo che dovrebbe validare (un LLM) non sarebbe piu'
un riferimento indipendente — invaliderebbe l'intero controllo.

**Stato onesto a oggi (M1):** questa sessione ha costruito il formato,
il protocollo job/result e il codice del gauge-check
(`services/verifier/executor/gauge_check.py`), verificati su geometrie
sintetiche generate ad-hoc SOLO per testare il meccanismo (non salvate
qui — vedi commit di questa milestone per il dettaglio). **Nessun
calibro reale e' stato ancora modellato**: la coppia GO/NO-GO per M6
(ISO 68-1), primo calibro previsto dalla checklist di
`docs/logbook_fase1.md`, resta da fare con CAD convenzionale da parte di
chi ha accesso a uno strumento adatto — non e' un compito che questa
sessione puo' completare da sola senza fabbricare un file che
violerebbe il vincolo sopra. Vedi `manifest.json`,
`calibration_status: "not_modeled"`.

## Formato

Una directory per coppia di calibri, nome `<feature>_<nominale>_<norma>`
(slug, niente spazi):

```
config/gauges/
  README.md
  manifest.json
  thread_M6_ISO68-1/
    GO.step
    NOGO.step
```

- **GO.step** — deve poter accoppiarsi/percorrere il pezzo senza
  interferenza, se il pezzo e' dentro tolleranza.
- **NOGO.step** — deve interferire, se il pezzo e' dentro tolleranza
  (la sua funzione e' rilevare un pezzo fuori tolleranza in eccesso).
- Entrambi in **STEP** (B-Rep), non STL — confronto booleano esatto,
  coerente col Rischio #3 dell'architettura ("verifica parametrica
  preferita alla verifica su mesh, dove possibile").

`manifest.json` e' il registro: un'entrata per coppia, con
provenienza/stato di calibrazione — analogo a
`services/orchestrator/presets.json` per i preset di feature. Il preset
corrispondente in `presets.json` referenzia gli stessi path relativi nei
campi `gauge_go_step`/`gauge_nogo_step`.

## Come vengono letti

`verifier-executor` monta questa directory **read-only** su `/gauges`
(vedi `docker-compose.yml`) — nessuna nuova rotta HTTP, nessun
trasferimento di contenuto tramite il protocollo job/result: il job
porta solo il path relativo (es. `thread_M6_ISO68-1/GO.step`),
`gauge_check.py` lo risolve sotto `/gauges` (vedi
`resolve_under_root()` in quel file, che rifiuta ogni path che uscirebbe
dalla radice montata).

I pezzi da verificare (STEP noti/statici, non generati in questo stesso
job) seguono lo stesso pattern sotto `${DATA_DIR:-./data}/models`,
montato su `/models`.
