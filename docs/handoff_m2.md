# Handoff — M2: Controlli geometrici deterministici (TC1/TC2/TC3)

Prompt pronto per la sessione che implementa M2. Copiabile così com'è
come primo messaggio di una nuova sessione Claude Code.

Premessa su M1, per chi lo eredita di seconda mano: due sessioni parallele
hanno costruito M1 dallo stesso `handoff_m1.md`, poi riconciliate. È
stato revisionato criticamente e onestamente (non solo letto — rieseguito
indipendentemente) prima di scrivere questo handoff: tutti i test dichiarati
(unitari su geometria sintetica, PASS/FAIL/determinismo sui calibri reali)
sono stati **rilanciati e riprodotti con gli stessi numeri**. È stato
trovato e corretto un bug reale non banale: il `Dockerfile` di
`verifier-executor` non copiava `gauge_check.py` — se l'immagine fosse
stata costruita così com'era, ogni job di gauge-check sarebbe fallito
silenziosamente nel container reale. M1 è solido, non solo dichiarato tale.

---

Riprendi il progetto CALIPER (danielesalpietro/caliper-cad) — layer di
verifica deterministica per geometrie CAD generate da LLM.

## Da dove partire (importante, non ovvio)

Il lavoro di M1 **non è ancora mergiato in `develop`** — è sul branch
`claude/handoff-m1-docs-bm01i6` (HEAD `e2d6718` al momento di scrivere
questo handoff, nessuna PR aperta). Parti da lì, non da `develop`:
`git checkout -B <tuo-branch> origin/claude/handoff-m1-docs-bm01i6`. Se
nel frattempo è stato mergiato, riparti da `develop` — verifica con
`git log --oneline -5` che tu veda i file elencati sotto prima di
procedere.

## Ordine di lettura

1. `docs/logbook.md` — quadro generale, revisione critica (perché niente
   motore fisico/GPU)
2. `docs/logbook_fase2.md` — la milestone M2, oggetto di questo compito
   (include già: design TC1/TC2/TC3, formato del log su timeout,
   contratto di retry verso L2, budget di retry, design di misura
   dell'efficacia delle directive — non ripartire da zero, è già scritto)
3. GitHub issue [#3](https://github.com/danielesalpietro/caliper-cad/issues/3) (M2) — stessa discussione, coi commenti
4. `docs/logbook_fase1.md` — cosa M1 ha già consegnato (leggi soprattutto
   la sezione "Verifica end-to-end sui calibri reali" e lo stato finale)
5. `services/verifier/executor/gauge_check.py` — il modulo che estendi,
   non riscrivi. Oggi fa solo interferenza **statica** (nessuno sweep)
6. `services/verifier/executor/watcher.py` — routing per chiave
   (`"code"` / `"gauge_check"`), timeout indipendenti già implementati
7. `config/gauges/manifest.json` e `README.md` — unico calibro esistente:
   tampone filettato M6 (GO Ø5.7 / NO-GO Ø6.3), per verificare un **foro**
   filettato, non un anello (vedi nota sotto)

## Cosa M1 ha già consegnato (non ripartire da zero)

- `services/verifier/executor/gauge_check.py`: interferenza statica
  esatta via boolean CadQuery/OCC, diagnostica pre-flight scritta su
  checkpoint **prima** del boolean pesante, `source: "virtual"` su ogni
  record.
- Subprocess **già separato** da `exec(code)`, timeout **già
  indipendente** (`GAUGE_CHECK_CPU_LIMIT_SECONDS=30` interno,
  `GAUGE_CHECK_TIMEOUT_SECONDS=45` esterno in `watcher.py`) — placeholder
  non tarati, calibrarli sul worst-case reale è lavoro di M2, non
  reinventare il meccanismo.
- Endpoint `POST /gauge-check` in `services/verifier/app.py`, percorso
  indipendente da `/verify`, ma **passa comunque dal protocollo
  job/result su volume condiviso** — non è una violazione del vincolo
  "niente nuova rotta HTTP verso verifier-executor" (quel vincolo riguarda
  il container isolato, non il servizio `verifier` che già espone HTTP).
- `resolve_under_root()`: protezione da path traversal sui path relativi
  di pezzo/calibro — verificata manualmente durante la revisione di M1
  (rifiuta correttamente `../` e path assoluti), ma **non ha un test
  automatico nel repo** — se tocchi quella funzione, aggiungine uno.
- Calibro reale per `thread` M6: **tampone filettato esterno**, non
  anello — verifica un **foro** filettato (coerente con l'esempio L2.5
  in architettura). Se in TC2 ti serve verificare una filettatura
  esterna, serve un calibro diverso (anello), non questo riusato al
  contrario.

## Cosa NON è mai stato verificato (gap reali, non taciuti)

- **Mai eseguito dentro il container Docker reale.** Stesso limite di
  sandbox già incontrato in M1: `docker build` verso Docker Hub è
  bloccato da policy organizzativa (403 su
  `production.cloudfront.docker.com`, non aggirabile — vedi
  `/root/.ccr/README.md`). `pip install cadquery==2.8.0` funziona invece
  diretto via PyPI (non dietro il proxy con policy) — usa quello per
  sviluppare e validare, sapendo che è lo stesso codice ma non lo stesso
  ambiente containerizzato. Se in questa sessione hai accesso Docker
  reale (o a un builder CI), costruire e testare l'immagine per davvero
  sarebbe la prima verifica indipendente mai fatta — non obbligatorio per
  M2, ma il gap resta aperto finché qualcuno non lo fa.
- **Il percorso di TIMEOUT non è mai stato innescato per davvero** — solo
  letto/ispezionato nel codice (`watcher.py` cattura il checkpoint
  pre-flight su `subprocess.TimeoutExpired`). Costruire un caso che vada
  davvero in timeout (geometria quasi-degenere, o un limite CPU
  artificialmente basso per il test) è lavoro naturale di M2, non un
  prerequisito mancante.

## Compito: implementare M2

Deliverable, dalla checklist "Stato" di `docs/logbook_fase2.md` (voci
non ancora fatte):

1. **TC1** — calibri pin GO/NO-GO, controllo interferenza statica +
   sweep lungo l'asse di inserimento, verificato su geometria nota.
2. **TC2** — calibro filettato GO/NO-GO (tampone, per un foro — vedi
   sopra), sweep lungo il percorso elicoidale reale, verificato su
   geometria nota.
3. **TC3** — punti di misura dichiarati nello schema L2.5, controllo
   distanza minima (`BRepExtrema_DistShapeShape`), verificato su
   geometria nota.
4. **Timeout del gauge-check calibrato empiricamente** sul worst-case
   osservato durante il batch (non a intuito) — i placeholder 30s/45s di
   M1 restano finché non hai un numero misurato per sostituirli.
5. **Batch dei tre TC eseguito e documentato** con risultati numerici —
   stesso stile già in uso nei logbook (v12/v14 in architettura,
   `verify_gauge_check*.py` in M1): script eseguibile a mano, output
   incollato come prova diretta, non solo "esito OK".
6. **Budget di retry L3→L2** (3 tentativi + uscita anticipata) e **log
   collegato per tentativo** (`case_id`/`attempt`/`directive_used`/
   `outcome`) — design già scritto in `docs/logbook_fase2.md`, qui va
   implementato nell'orchestratore (`services/orchestrator/
   generate_and_verify.py`, che oggi non ha retry automatico).
7. **Modelli CAD di riferimento** per TC1-TC3 riusati anche per popolare
   il campo diagnostico del Livello 6 (collegamento col bootstrap
   retroattivo, non lavoro duplicato).

## Vincoli già decisi, non rinegoziabili senza confronto esplicito con l'utente

- Niente motore fisico/GPU — tutto CPU-only via boolean/sweep OCC esatti
  (vedi `docs/logbook.md`, punto 2 della revisione critica).
- Il gauge-check resta in un subprocess separato da `exec(code)` — **già
  costruito da M1**, estendilo, non duplicarlo.
- Log su timeout: solo dati noti prima del blocco (spec strutturata,
  diagnostica pre-flight) — mai una spiegazione causale post-hoc. Formato
  già definito in `docs/logbook_fase2.md`.
- Checkpoint del retry verso L2: classificazione deterministica in un
  enum fisso + una frase canned scritta da un umano — mai i numeri grezzi
  nel prompt del modello (rischio di "spiegazioni" plausibili ma
  inventate, stesso pattern di `texture_thread()`/`clearance=` già
  documentato). Design completo in `docs/logbook_fase2.md`.
- Misura dell'efficacia delle directive: non un conteggio grezzo — la
  Policy di retry impone comunque una variazione (temperatura/
  riformulazione) a ogni tentativo, quindi serve un confronto controllato
  per isolare l'effetto della frase, con soglia minima N≥20 prima di
  fidarsi di un tasso.

## A fine lavoro

- Aggiorna la checklist "Stato" in `docs/logbook_fase2.md`.
- Commenta l'esito su GitHub issue [#3](https://github.com/danielesalpietro/caliper-cad/issues/3).
- Se hai esteso `gauge_check.py` o `watcher.py`, aggiorna anche il
  `Dockerfile` di `verifier-executor` se aggiungi nuovi file — controllalo
  esplicitamente, è la classe di bug già trovata una volta in questa
  milestone.
- Commit e push sul branch assegnato a questa sessione. NON aprire PR
  senza che te lo chieda esplicitamente.
- Se una scelta implica un trade-off non coperto dai logbook, chiedi
  prima di procedere — stesso stile di lavoro già usato finora: decisioni
  motivate, documentate, verificate per davvero (rieseguite, non solo
  lette), non assunte.
