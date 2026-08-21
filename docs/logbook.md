# Logbook — Ciclo di Collaudo Virtuale

## Cos'è questo documento

Traccia l'avanzamento di un filone di lavoro avviato il 2026-08-21: dotare il
Livello 3 (verifica) di un vero collaudo **dimensionale e relazionale** tra
geometrie — non solo il controllo su bounding box già esistente (v14) — per
poter validare accoppiamenti (filettature, calzamenti, incastri) prima della
stampa fisica. È un log di esecuzione (cosa è stato fatto, quando, con che
esito), non un documento di architettura: per quello resta
[`architettura-prototipo-mesh-llm.md`](architettura-prototipo-mesh-llm.md),
di cui questo lavoro è un'**estensione**, non un sostituto.

Un logbook per fase: [`logbook_fase1.md`](logbook_fase1.md),
[`logbook_fase2.md`](logbook_fase2.md), [`logbook_fase3.md`](logbook_fase3.md),
[`logbook_fase4.md`](logbook_fase4.md).

## Provenienza

Proposta ricevuta il 2026-08-21: 4 layer (generazione 2D, compilazione,
collaudo virtuale via motore fisico headless con GPU, ground truth), 3 test
case (TC1 accoppiamento albero-mozzo, TC2 filettatura ISO, TC3 snap-fit),
roadmap in 4 fasi con milestone. Il testo integrale è nella cronologia della
conversazione che ha originato questo lavoro. Qui si documenta come è stata
**rivista criticamente** prima di diventare piano operativo — la revisione
non è un dettaglio a margine, è la parte che determina se il lavoro rispetta
i principi già stabiliti nel resto del progetto.

## Revisione critica (accolta prima di procedere)

1. **Collisione di nomenclatura.** Il progetto usa già "Fase A / Fase B"
   (rischio, non sequenza: A = generazione cloud + verifica, autosufficiente;
   B = autonomia locale, condizionata a un test di fattibilità — vedi
   "Fasatura del progetto" nell'architettura) e "Livello 1–8" per gli stadi
   della pipeline. La proposta originale introduceva "Layer 1–4" e "Fase
   1–4" con contenuti diversi. Per non sovrapporre due schemi di numerazione
   incompatibili nello stesso repository, qui si usa **M1–M4** (milestone),
   esplicitamente innestate nella **Fase A esistente**: il collaudo virtuale
   non richiede il motore di generazione locale, resta coerente con la
   regola già stabilita ("Fase B dipende da Fase A, non il contrario").
2. **Il motore fisico/GPU per il collaudo virtuale è sovradimensionato e in
   tensione diretta con i principi del progetto.** Prova empirica già
   disponibile nel changelog dell'architettura (`[v14]`): il bug reale
   trovato su una filettatura era un solido **manifold, non
   self-intersecting, tecnicamente valido** — ma con bounding box 2.0mm
   invece di 6.0mm nominali (profilo elica in coordinate locali non
   allineate alla posizione reale nello spazio). Un motore fisico che fa
   collision detection/raycast **non avrebbe colto questo errore**: non
   c'è un secondo corpo che collide, è un errore dimensionale puro, già
   intercettato dal controllo bbox esistente in `run_and_measure.py`. In
   più, un solver fisico iterativo (timestep, damping, restituzione)
   introduce la stessa non-determinismo che l'intera architettura esiste
   per eliminare (vedi README §3 e Rischio #3: "il giudice è
   deterministico... non eredita l'incertezza che vorrebbe rimuovere"). E
   un GPU passthrough su un container che esegue codice non fidato allarga
   la superficie d'attacco proprio dove il progetto ha già scelto la
   postura più conservativa (Rischio #9: `network_mode: none` su
   `verifier-executor`, `docker-socket-proxy` con `POST=0`, nessun
   controllo di scrittura su Docker/host).
   **Decisione presa:** niente motore fisico né GPU dedicata. Il collaudo
   virtuale è implementato come estensione di `verifier-executor`
   (CPU-only, isolato, CadQuery/OCC già presente) con controlli booleani
   **esatti**: interferenza statica, interferenza su percorso di
   inserimento/avvitamento ("sweep"), distanza minima esatta
   (`BRepExtrema_DistShapeShape`). Concettualmente sono **calibri Go/No-Go
   virtuali** — la stessa metodologia di collaudo filettature standard
   (ISO), coerente col nome del progetto. Deterministico, stesso protocollo
   job/result su volume condiviso già in uso, zero nuova infrastruttura
   GPU.
3. **Manca un pezzo strutturale: serve una seconda geometria.** TC1/TC2/TC3
   richiedono tutti un riferimento (calibro, perno, foro) contro cui
   testare — oggi la pipeline genera una sola feature per volta (L2.5/
   `presets.json` sono a singola feature). Soluzione adottata: calibri di
   riferimento **versionati come file STEP** (uno per preset, es.
   `config/gauges/`), **non generati dall'IA** — analogo fisico diretto: un
   calibro va prodotto una volta con precisione nota, non ridisegnato ogni
   volta che serve.
4. **Rischio epistemico per M4 (chiusura del loop).** Il Livello 3 è
   descritto nell'architettura come "veloce/automatico ma simulato"; il
   Livello 5 (misura fisica) resta "l'unica fonte che cattura variabili
   reali" (vedi Rischio #8: i FAIL fisici vanno registrati con lo stesso
   rigore dei PASS). Se il loop di retrieval ingerisse i risultati del
   collaudo virtuale nello stesso dataset congelato del Livello 6 senza un
   campo esplicito `source: virtual|physical`, romperebbe il firewall che
   il progetto ha costruito apposta per non scambiare un risultato simulato
   per verità fisica. Trattati come collezioni separate, mai fuse.

## Mappatura terminologica (proposta → adottato)

| Proposta originale | Nome adottato qui | Relazione con l'architettura esistente |
|---|---|---|
| Layer 1 — Generazione 2D | Modalità "sketch-first" del Livello 2 | Estende L2, non lo sostituisce; riduce la superficie di errore dell'LLM (Rischio #1/#3/#5) |
| Layer 2 — Compilazione (kernel geometrico) | Già Livello 3, fase 1 | `services/verifier/executor/run_and_measure.py` esegue già CadQuery isolato — nessun nuovo componente |
| Layer 3 — Collaudo virtuale (motore fisico GPU) | **Livello 3, fase 3 — calibri Go/No-Go virtuali** | Estensione CPU-only di `verifier-executor`, non un nuovo motore/container GPU |
| Layer 4 — Ground truth & storage | Già Livello 6 (dataset congelato) + Livello 7 (retrieval) | Automatizza un passaggio oggi manuale, con firewall simulato/fisico esplicito |

## Milestone e stato

| # | Nome | Issue | Logbook | Stato |
|---|---|---|---|---|
| M1 | Scaffold di isolamento + calibri di riferimento | [#2](https://github.com/danielesalpietro/caliper-cad/issues/2) | [logbook_fase1.md](logbook_fase1.md) | 🟢 mergiata in `develop` ([#7](https://github.com/danielesalpietro/caliper-cad/pull/7)) — protocollo/codice + primo calibro reale M6, verificati end-to-end (PASS/FAIL, volume, determinismo). Non ancora eseguito dentro il container `verifier-executor` reale (nessun Docker in sandbox, stesso codice validato fuori) |
| M2 | Controlli geometrici deterministici, validati su geometrie note | [#3](https://github.com/danielesalpietro/caliper-cad/issues/3) | [logbook_fase2.md](logbook_fase2.md) | 🟢 mergiata in `develop` ([#8](https://github.com/danielesalpietro/caliper-cad/pull/8)) — TC1/TC2/TC3 verificati indipendentemente, timeout ricalibrato su misura reale (65.5s CPU worst-case), retry L3→L2 implementato |
| M3 | Pipeline sketch-first → compilazione → collaudo (ambito: preset "thread") | [#4](https://github.com/danielesalpietro/caliper-cad/issues/4) | [logbook_fase3.md](logbook_fase3.md) | 🟡 mergiata in `develop` ([#11](https://github.com/danielesalpietro/caliper-cad/pull/11)), criterio di accettazione parzialmente raggiunto — schema/compilatore/wiring del gauge-check nel loop costruiti e verificati con casi scritti a mano (3 bug reali trovati e corretti: STEP mai esportato da `run_and_measure.py`, timeout HTTP del gauge-check non ricalibrato, `spec` mai inoltrata a `/verify`); l'esecuzione end-to-end reale con una generazione L2 vera **non è stata possibile** (nessuna istanza Flowise nel sandbox) e non è stata simulata con un mock della generazione |
| M4 | Chiusura del loop di retrieval, firewall simulato/fisico | [#5](https://github.com/danielesalpietro/caliper-cad/issues/5) (chiusa) | [logbook_fase4.md](logbook_fase4.md) | 🟢 mergiata in `develop` ([#14](https://github.com/danielesalpietro/caliper-cad/pull/14)) — retry_log.jsonl esteso con `feature`/`spec_key`, `virtual_memory.py` con regola anti-bias verificata come gate reale nel loop (0 chiamate a L2 per una strategia esclusa), Livello 7 esteso a due collezioni Qdrant separate con `source` esplicito; nessun dataset Livello 6 reale né istanza Ollama/Qdrant viva in questa sessione, stesso limite di sandbox di M1–M3 |
| M5 | Fix pack post-review: sbloccare l'end-to-end (C1–C8, C10) | [#17](https://github.com/danielesalpietro/caliper-cad/issues/17) | [logbook_fase5.md](logbook_fase5.md) | 🟡 non ancora mergiata (issue [#15](https://github.com/danielesalpietro/caliper-cad/issues/15)/[review_tecnica.md](review_tecnica.md) — C1, C2, C3, C4, C5, C6, C7, C10 chiusi in sandbox: contratto dimensionale per-feature (P2a), calibro NO-GO nel loop, `snap_fit`/min_distance senza crash, strategia `param_first` (P3) + tolleranze sketch-first allargate, memoria virtuale con `tolerance`/`pitch` in chiave + conteggio per caso + `checker_version` revocabile, id Qdrant deterministici + offset persistito, split esecuzione/verdetto (P5) contro un verdetto contraffatto, quick win (AnnAssign, CI su `claude/**`, cleanup `/exec/parts`, bind `127.0.0.1`, Flowise pinnato); 8 test nuovi (TC-M5-1…8) con output rosso pre-fix documentato in logbook_fase5.md, 14 script esistenti senza regressioni; C8 (budget CPU non portabile) non affrontato, resta per M6/M7; verifica indipendente del supervisore prima del gate M6 ancora da fare |

Le milestone sono sequenziali per dipendenza tecnica (M2 richiede M1, M3
richiede M2, M4 richiede M3 e il Livello 6 esistente, M5 richiede la
review tecnica di M1–M4) — non per calendario: nessuna scadenza è
fissata qui, coerentemente con lo stato "concept / pre-implementazione"
del progetto. Dopo M5 il piano di recupero (`docs/piano_recupero.md`)
prosegue con M6 (bring-up reale su RunPod), M7 (topologia Docker sulla
RTX 3090) e M8 (bootstrap Livello 6 + primo loop fisico).

## Processo di handoff e CI (a valle di M1-M3)

Due correzioni di processo dopo la chiusura di M1-M3, entrambe rilevanti
per chi implementa M4:

- **Handoff.** Una sessione partita da `develop` prima che M1/M2 fossero
  mergiati non ha trovato `docs/handoff_m3.md` (viveva solo su un branch
  non mergiato) — ha dovuto ricostruire il contesto alla cieca. Causa e
  correzione in dettaglio: issue
  [#9](https://github.com/danielesalpietro/caliper-cad/issues/9). Con
  M1-M3 ora mergiati in `develop`, questo problema specifico è risolto
  per M4 in poi — ma la regola resta: ogni handoff futuro va anche come
  commento autosufficiente sull'issue della milestone, non solo come file
  su un branch.
- **CI.** 12 script di verifica eseguibili a mano si sono accumulati tra
  M1/M2/M3 (nessuna suite automatica nel progetto, vedi
  `architettura-prototipo-mesh-llm.md`), rieseguiti manualmente ad ogni
  revisione invece di restare verdi in automatico. Aggiunto
  [`.github/workflows/regression.yml`](../.github/workflows/regression.yml):
  li esegue tutti su ogni push/PR verso `develop`. Chi implementa M4 deve
  aggiungere lì i propri nuovi script di verifica, non lasciarli solo
  eseguibili a mano.
- **README.md e index.html.** Erano rimasti indietro rispetto allo stato
  reale — `index.html` fermo a prima di M1 (tabella di stato con
  "Verification code: not yet", falso da quando esiste
  `gauge_check.py`), `README.md` con lo status banner in cima ancora
  "no verification code written" mentre la tabella dei servizi più sotto
  già descriveva il gauge-check di M1 — due fonti di verità in
  contraddizione nello stesso file. **Regola per ogni milestone da qui in
  avanti:** quando lo stato di `docs/logbook.md` cambia (una milestone
  raggiunge il criterio di accettazione, o viene mergiata), aggiornare
  nella stessa PR anche lo status banner e la sezione Status di
  `README.md` e la tabella di stato di `index.html` — non lasciarli
  disallineati fino alla prossima volta che qualcuno se ne accorge.

## Riferimenti

- Architettura: [`architettura-prototipo-mesh-llm.md`](architettura-prototipo-mesh-llm.md)
- Rischi citati: #1 (motore locale), #3 (verifica parametrica preferita a
  mesh), #8 (rigore FAIL fisici), #9 (Flowise non è un motore per step
  deterministici/side-effect fisici — vale anche per il collaudo virtuale)
- Codice esistente riusato: `services/verifier/executor/run_and_measure.py`,
  `services/orchestrator/presets.json`, `docker-compose.yml` (rete
  `caliper-ai`, volume `verifier_exec`)
