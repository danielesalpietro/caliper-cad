# Piano di recupero post-review — M5–M8 (RunPod + RTX 3090)

Supervisione: la sessione che ha condotto la review (`docs/review_tecnica.md`,
issue #15). Questo documento è il piano operativo che attacca le criticità
C1–C11 emerse dalla review, con la sequenza, l'infrastruttura (RunPod fino al
24, RTX 3090 privata dal 24 in poi), i test case (input/output attesi,
verificabili) e il protocollo di recupero degli output prima dello
spegnimento dei pod.

Vincoli di partenza dichiarati dall'utente:

- la RTX 3090 privata **non è disponibile fino al 24** → tutto ciò che
  richiede un'istanza viva prima di quella data gira su **RunPod**;
- i costi non sono un problema → la configurazione è scelta per comodità e
  parità con l'hardware di destinazione, non per risparmio;
- i pod RunPod sono **effimeri**: vivono il tempo necessario — ogni output
  utile va recuperato **prima** dello spegnimento e conservato in una
  cartella specifica (vedi §5, protocollo di harvest);
- la supervisione guida le altre sessioni: ogni fase ha un handoff, una
  sessione esecutrice, e una verifica indipendente del supervisore prima
  del gate successivo.

---

## 1. Metodologia — "chiudi la giuntura"

La lezione centrale della review (C9): **ogni giuntura esercitata per la
prima volta ha rivelato un bug; i bug residui vivono nelle giunture mai
esercitate**. Il piano quindi non aggiunge funzionalità: chiude giunture,
nell'ordine dal costo di scoperta più basso al più alto. Cinque regole,
vincolanti per ogni sessione:

1. **Rosso prima di verde.** Ogni fix parte da un test che riproduce il
   difetto (fallisce sul codice attuale), poi il fix minimale, poi il test
   verde — e il test entra in `.github/workflows/regression.yml`, non resta
   eseguibile a mano. Nessun fix senza il suo test rosso documentato.
2. **Verifica nell'ambiente reale più vicino disponibile.** Sandbox per la
   logica, RunPod per le istanze vive, RTX 3090 per la topologia Docker.
   Un claim "verificato" dichiara sempre *in quale ambiente* — la parola
   "end-to-end" è riservata a esecuzioni con tutti i servizi vivi.
3. **I numeri ambiente-dipendenti si rimisurano nell'ambiente d'uso.**
   Budget CPU, timeout, epsilon: ogni ambiente nuovo (pod, container, 3090)
   produce una riga di misura nel logbook prima che il numero sia
   considerato valido lì (lezione C8).
4. **Nessun pod si spegne senza harvest verde.** Lo script di harvest (§5)
   verifica una checklist di artefatti attesi ed esce non-zero se manca
   qualcosa; due copie indipendenti (network volume + push su git) prima
   dello shutdown.
5. **Il supervisore riesegue, non rilegge.** Gate tra fasi: la sessione di
   supervisione riproduce indipendentemente i risultati dichiarati (stessa
   disciplina già usata per M1–M4) prima di autorizzare la fase successiva.
   Nessuna PR viene aperta o mergiata senza ok esplicito dell'utente.

## 2. Mappa criticità → fase

| Criticità (da `review_tecnica.md`) | Fase | Come viene chiusa |
|---|---|---|
| C1 — dimensional check vs topologia foro (bloccante) | **M5** | Contratto dimensionale per-feature (proposta P2a): per `thread` il Go/No-Go è il controllo dimensionale; bbox ridotto a sanity check |
| C2 — solo calibro GO nel loop | **M5** | Chiamata NO-GO con semantica invertita nel loop |
| C3 — preset `snap_fit` rompe l'orchestratore | **M5** | Costruzione del job `min_distance` dai `measurement_points` del preset |
| C4 — sketch-first richiede precisione IEEE / sketch ridondante | **M5** | Strategia `param_first` (proposta P3) + fix finestra `is_crest` e tolleranze sketch |
| C5 — spec_key troppo larga, esclusione irrevocabile | **M5** (codice) + **M8** (schema L6) | `tolerance`/`pitch` in chiave, conteggio per caso, `checker_version` nei record, provenienza nello schema L6 |
| C6 — id Qdrant instabili tra riavvii | **M5** (fix) + **M6** (verifica live con restart) | id deterministici da contenuto, offset persistito |
| C7 — verdetto falsificabile dal codice non fidato | **M5** | Split esecuzione/misura (proposta P5) + test avversariale |
| C8 — budget CPU non portabile | **M5** (pinning thread) + **M6/M7** (rimisura) | Thread OCC fissati; budget primario wall-clock; rimisura per ambiente |
| C9 — sistema integro mai esistito | **M6** (nativo) + **M7** (Docker) | Prima esecuzione end-to-end viva; poi stessa suite nella topologia container reale |
| C10 — secondari (AnnAssign, CI branch, porte, cleanup, aliasing sweep) | **M5** (quick win) + **M6** (misura aliasing) | Vedi dettaglio fasi |
| C11 — priorità: L6 vuoto, conferma L2.5 mancante | **M8** (+ conferma L2.5 in M6) | Bootstrap Livello 6, schema formale, primo loop fisico |

Domande aperte della review coperte dal piano: #1 (risoluzione sweep → M6),
#2 (concordanza virtuale↔fisico → M8), #3 (conferma umana L2.5 → M6, forma
minima CLI), #6 (budget per ambiente → M6/M7). Restano fuori scope, da
ridiscutere dopo M8: #4 (profilo troncato), #5 (foro cieco/controcavo).

## 3. Fasi

### M5 — Fix pack post-review (sandbox, subito — nessuna infrastruttura richiesta)

Sblocca l'end-to-end prima di pagare un solo minuto di pod: inutile
accendere RunPod finché C1 garantisce il FAIL. Tutto è verificabile in
sandbox con `pip install cadquery==2.8.0` (stessa via di M1–M4). Handoff
completo in `docs/handoff_m5.md`; issue dedicata su GitHub. Decisioni di
design già prese dal supervisore (motivate nella review, non rinegoziabili
senza confronto con l'utente):

- **C1 → proposta P2a**: il preset dichiara il contratto dimensionale.
  Per `thread`: niente confronto bbox-vs-nominale (il collaudo Go/No-Go È
  la misura del foro); bbox resta come sanity check (bbox_z ≈
  `engagement_length_mm`, bbox_x/y ≥ diametro maggiore). Il caso storico
  `[v14]` (pezzo 2mm nel posto sbagliato) resta intercettato: un solido
  pieno o assente dove dovrebbe esserci il foro fa interferire il GO
  (FAIL), un foro sovradimensionato non fa interferire il NO-GO (FAIL, con
  C2 chiuso). La coppia GO+NO-GO copre entrambi i lati della banda.
- **C4 → proposta P3**: nuova strategia `param_first` come default per le
  feature con preset — L2 emette solo i parametri numerici
  (`major_diameter_mm`, `pitch_mm`, `engagement_length_mm`, host), il
  compilatore deriva le coordinate con la trigonometria che già possiede.
  `sketch_first` resta disponibile, con tolleranze allargate
  (1e-3 mm / 0.1°) e `is_crest` agganciato alla stessa tolleranza del
  cross-check (chiude la finestra di non-manifold).
- **C7 → proposta P5**: il subprocess che esegue `exec(code)` esporta solo
  lo STEP e non scrive mai il verdetto; un secondo subprocess fidato (che
  non esegue codice non fidato) importa lo STEP, misura e scrive il
  result. `/models` non è più leggibile dal processo di exec.

Test case M5 (ognuno = script `verify_*.py` nuovo in CI; input e output
attesi esatti nell'handoff):

| ID | Input | Output atteso (verificabile) |
|---|---|---|
| TC-M5-1 (C1) | Codice compilato del foro M6 in blocco 20×20×8 + spec `thread M6 tol 0.3` via job `run_and_measure` | `execution: PASS` (oggi: FAIL dimensionale) e, sul pezzo esportato, GO sweep PASS (residuo ≤ 0.5mm³) |
| TC-M5-2 (C2) | Foro liscio Ø7.0 in blocco (fuori tolleranza +) attraverso il loop mockato in rete | Verdetto finale FAIL con errore `gauge_check_nogo_no_interference`; oggi: PASS silenzioso |
| TC-M5-3 (C3) | Spec `snap_fit` attraverso il loop | Nessun `ValueError`; job `/gauge-check` con `mode: min_distance` e i punti del preset (assert sui contenuti del job); oggi: crash "Preset incoerente" |
| TC-M5-4 (C6) | Stessa fixture indicizzata da due processi con `PYTHONHASHSEED` diversi | Id dei punti identici tra i due processi; offset del log riletto da file dopo "riavvio" simulato |
| TC-M5-5 (C7) | Codice avversariale che scrive un result PASS contraffatto e chiama `os._exit(0)` | Verdetto finale FAIL (il result contraffatto non è accettato); oggi: PASS contraffatto accettato |
| TC-M5-6 (C5) | Log con 3 tentativi FAIL dello stesso `case_id`; log con 2 casi distinti; record con errore non geometrico | Esclusione conta i **casi**, non i tentativi; errori non geometrici esclusi; `checker_version` presente in ogni record nuovo |
| TC-M5-7 (C4) | Parametri thread M6 via `param_first` | STEP con gli stessi numeri del caso a mano (GO residuo ≈ 0.3059mm³, NO-GO ≈ 20.158mm³); spec sketch con coordinate a 4 decimali ora valida |
| TC-M5-8 (C10) | `result: cq.Workplane = ...` (AnnAssign) a `/verify` | Check statico `result_variable` PASS; oggi: FAIL |

Più: trigger CI esteso ai branch `claude/**`, cleanup di `/exec/parts`
(ritenzione configurabile), bind `127.0.0.1:` sulle porte del compose
(verificato con `docker compose config` in M5, esercitato in M7).

Criterio di accettazione M5: gli 8 test nuovi verdi in CI + i 14 esistenti
senza regressioni + verifica indipendente del supervisore (riesecuzione).

### M6 — Bring-up reale su RunPod (dopo il merge di M5)

Prima esecuzione **end-to-end viva** della storia del progetto:
L2.5 (Ollama/granite) → conferma umana → L2 (GPT via Flowise) → `/verify` →
`/gauge-check` → retry loop → log virtuale → Livello 7 (Qdrant+Ollama).

**Vincolo tecnico dichiarato onestamente:** i pod RunPod sono container —
**niente Docker-in-Docker**. Su RunPod lo stack gira quindi in modalità
**nativa** (processi nello stesso pod: binario Qdrant, Ollama, Flowise via
npm, servizi Python in venv, lanciati da uno script di avvio versionato).
Questo esercita tutte le giunture *applicative* vive (Flowise↔Ollama,
Qdrant, generazione L2 reale, riavvii, timeout reali) ma **non** la
topologia container (immagini, mount, `network_mode: none`, socket proxy) —
quella resta a M7 sulla 3090. La divisione è esplicita, non un ripiego
taciuto.

Contenuto:

1. **Preparazione — [rev. 2, decisa con l'utente]: immagine propria su
   GHCR invece del bootstrap a runtime.** `ops/runpod/Dockerfile` builda
   l'immagine monolitica `caliper-pod` (stack completo a versioni
   pinnate coerenti col progetto: CadQuery 2.8.0, Flowise 3.1.4, Ollama
   0.32.15, Qdrant v1.19.0, Python 3.11, Claude Code CLI);
   `.github/workflows/publish-images.yml` la pubblica su GHCR **insieme
   alle 4 immagini di servizio del compose** — che così vengono buildate
   in CI per la prima volta nella storia del progetto (chiude subito la
   parte "immagini mai costruite" di C9, e la classe di bug "COPY
   dimenticata" di M1 diventa un fallimento di CI). `start.sh`,
   `supervisord.conf`, `harvest.sh`, `env_fingerprint.sh`,
   `install_vllm.sh` sono versionati in `ops/runpod/`. Il compose resta
   la topologia di M7: `docker-compose.ghcr.yml` è l'override che in M7
   fa `pull` delle immagini GHCR invece di ricostruirle.
   **vLLM (preferenza dichiarata dall'utente):** a parità di funzione si
   preferisce vLLM — ruolo assegnato: serving dei modelli locali per il
   test Rischio #1 (M6-extra, endpoint OpenAI-compatible su :8700 via
   `install_vllm.sh`, venv sul volume) e via di fuga designata per L2.5
   se il bug ChatOllama di Flowise (v10) si ripresenta live. Il bring-up
   M6 resta su Ollama perché il sistema sotto test è quello scritto
   (stream-agent e chatflow parlano l'API nativa Ollama) — sostituirlo
   prima della prima esecuzione viva cambierebbe il sistema sotto test.
2. **Esecuzione sul pod** (sessione Claude Code *dentro il pod*, vedi §4):
   bring-up, versioning dei chatflow L2 (finalmente esportati in
   `services/flowise/chatflows/` — oggi il chatflow L2 free-code vive solo
   in un'istanza manuale e quello param/sketch-first non esiste), verifica
   `overrideConfig` temperatura, esecuzione della suite TC-E2E, rimisura
   dei budget (C8) con thread OCC fissati, test di riavvio per C6,
   misura dell'aliasing dello sweep (domanda aperta #1).
3. **Conferma umana L2.5 (forma minima):** prompt CLI nell'orchestratore
   (`--confirm`: mostra la spec normalizzata, chiede y/n prima di
   generare) — chiude la mitigazione obbligatoria del Rischio #5 nella
   forma più piccola che sia reale, senza UI.

Test case M6 (input/output attesi; esiti registrati nel run folder e nel
logbook di fase):

| ID | Input | Output atteso (verificabile) |
|---|---|---|
| TC-E2E-1 | Prompt: `"foro filettato M6, tolleranza 0.3mm, passo 1.0"` al chatflow L2.5 vivo | JSON con `feature: "thread"`, `nominal: "M6"`, `pitch: 1.0`, `tolerance: 0.3`, `tolerance_type`/`measured_as` vuoti (comportamento "non indovinare" già visto in v9) |
| TC-E2E-2 | Spec confermata → loop completo con `param_first` su GPT reale | Exit 0; record `outcome: PASS` in `retry_log.jsonl` con `spec_key` completa; STEP esistente; GO residuo ≤ 0.5mm³, NO-GO interferenza > 1mm³ |
| TC-E2E-3 | Spec sabotata (es. `pitch: 0.05`, irrealizzabile) | Loop ≤ 3 tentativi, directive presente nei tentativi 2+, uscita `unrecoverable_virtual`, record collegati per `case_id` |
| TC-E2E-4 | Codice iniettato con foro Ø7 (bypass L2) | NO-GO senza interferenza → FAIL finale (conferma live di TC-M5-2) |
| TC-E2E-5 | Fixture L6 + log virtuale indicizzati; **restart del processo stream-agent**; `/reindex` | Conteggio punti nelle due collezioni Qdrant invariato dopo il restart (conferma live di C6); `/chat` risponde con contesto etichettato `[physical]`/`[virtual]` |
| TC-E2E-6 | 2 casi FAIL virtuali + 1 FAIL fisico fixture per la stessa chiave | Orchestratore esce **prima** di chiamare Flowise (0 richieste nei log Flowise) |
| TC-E2E-7 | Gauge job con budget CPU artificialmente basso | Risultato TIMEOUT **strutturato via HTTP** con `preflight_diagnostics` + `last_checkpoint` (il percorso non è mai stato innescato end-to-end) |
| TC-E2E-8 | Sweep TC2 completo, thread OCC fissati, 3 ripetizioni | Tempi CPU e wall registrati; nuovo budget = worst-case × 1.5, scritto in `docs/` con fingerprint dell'ambiente |
| TC-E2E-9 | Difetti sintetici di larghezza decrescente (1.0 → 0.1mm) in un foro noto | Larghezza minima di difetto rilevata dallo sweep a 21 step, documentata (risposta alla domanda aperta #1) |

**Extra opzionale M6 (abilitato dal GPU affittato, costi non un problema):**
primo test di fattibilità del Rischio #1 — stessi prompt della suite
sottoposti a un modello locale candidato (es. un coder 7–14B via Ollama,
24GB VRAM bastano) e confrontati contro il Livello 3. È il test che
l'architettura richiede da sempre prima della Fase B; il pod lo rende
gratuito in termini di setup. Non blocca il criterio di accettazione di M6.

Criterio di accettazione M6: TC-E2E-1..8 con esito documentato (TC-E2E-9 e
l'extra sono best-effort), chatflow L2 versionati nel repo, harvest verde,
numeri nel logbook di fase.

### M7 — Topologia Docker reale su RTX 3090 (dal 24)

La giuntura che RunPod non può esercitare: le immagini **mai costruite**
e la topologia del compose.

1. `docker build` di tutte le immagini (verifier, executor, stream-agent,
   dashboard) — prima build della storia del progetto.
2. `docker compose up` completo; smoke test di ogni servizio.
3. **Riesecuzione della stessa suite TC-E2E-1..7 dentro la topologia
   reale** — stessi input, stessi output attesi di M6: ogni divergenza
   M6→M7 è per definizione un bug di topologia (mount, rete, path), la
   classe che la review ha previsto.
4. Verifiche di isolamento *attive*, non dichiarate: dall'interno di
   `verifier-executor` un tentativo di rete deve fallire
   (`network_mode: none`); una POST al socket proxy deve essere rifiutata
   (`POST=0`); path traversal su `/gauge-check` rifiutato via HTTP.
5. Mount reali di `billa05/prusacli` verificati; primo slicing L4 di un
   pezzo PASS (`caliper-pla.ini`) → primo G-code della storia del progetto.
6. Rimisura budget (C8) nell'ambiente container (regola #3): i numeri di
   M6 non si trasferiscono per assunzione.

Criterio di accettazione M7: suite TC-E2E verde in topologia container +
le 3 verifiche di isolamento attive documentate + primo G-code prodotto.

### M8 — Bootstrap Livello 6 + primo loop fisico

Il lavoro a valore massimo di Fase A, mai schedulato in M1–M4 (C11):

1. **Schema L6 formale** (chiude l'ambiguità `specifica_strutturata`/`spec`,
   `esito`/`outcome`): campi obbligatori — spec arricchita (con
   `thread_standard`, per il matching di `geometry_key`), provenienza
   (`l2_strategy`/`generator`, da C5/P4), macchina, materiale/batch, data,
   misura, esito, `checker_version` — più validatore e script di verifica.
2. **Bootstrap retroattivo**: i casi storici già validati da Fabrizio
   documentati nello schema (lavoro dell'utente, guidato da una sessione);
   FAIL storici inclusi se esistono, bias di sopravvivenza dichiarato se no
   (Rischio #8).
3. **Primo lotto fisico del loop**: stampa dei pezzi PASS-virtuali di
   M6/M7, misura al calibro, registrazione nello schema L6 →
   **prima misura di concordanza virtuale↔fisico** (domanda aperta #2, la
   domanda fondante del progetto).
4. Con dati L6 reali: il gate anti-bias di M4 smette di essere inerte —
   primo test del percorso di esclusione con corroborazione fisica vera.

Criterio di accettazione M8: schema L6 versionato con validatore; ≥ N casi
storici registrati (N deciso con l'utente); ≥ 1 lotto stampato-misurato-
registrato; tabella di concordanza virtuale↔fisico nel logbook.

## 4. RunPod — template e configurazione scelta

Configurazione raccomandata (decisa; i costi non sono un vincolo):

| Voce | Scelta | Motivazione |
|---|---|---|
| Cloud | **Secure Cloud** | Affidabilità/persistenza migliore del Community per sessioni lunghe |
| GPU | **1× RTX 4090 24GB** | Parità di classe con la 3090 di destinazione (24GB); ampio per granite4:1b + embedding; lascia spazio all'extra Rischio #1 (modelli 7–14B) |
| Datacenter | **EU con supporto Network Volume** (es. `EU-RO-1` — verificare in creazione che il DC scelto offra sia 4090 sia network volume: il volume è vincolato al datacenter) | Latenza e residenza dati |
| Container disk | **60 GB** | CadQuery/OCP ~3GB, modelli Ollama, node_modules Flowise, margine |
| **Network Volume** | **`caliper-artifacts`, 100 GB, montato su `/workspace`** | È la "cartella specifica" persistente: sopravvive allo spegnimento del pod |
| Immagine template | **`ghcr.io/danielesalpietro/caliper-pod:git-<sha>`** — immagine nostra, buildata e pubblicata su GHCR da `.github/workflows/publish-images.yml` (vedi `ops/runpod/README.md`) | **[rev. 2, decisa con l'utente]** Sostituisce l'idea iniziale "immagine generica + bootstrap a runtime": versioni pinnate coerenti col progetto (CadQuery 2.8.0, Flowise 3.1.4, Ollama 0.32.15, Qdrant v1.19.0), e la build in CI chiude subito il pezzo di C9 "immagini mai costruite" |
| Porte HTTP esposte | 3000 (Flowise), 3010, 6333 (Qdrant), 8000, 8500 (stream-agent), 8600 (verifier), 8700 (vLLM), 11434 (Ollama) + SSH | Accesso via proxy RunPod per ispezione umana |
| Start command | *(vuoto — l'immagine ha già `CMD ops/runpod/start.sh`)* | `start.sh` prepara `/workspace`, aggiorna il repo, avvia supervisord; i modelli Ollama persistono sul volume |
| Secrets (RunPod Secrets, mai nel repo) | `OPENAI_API_KEY`, `FLOWISE_PASSWORD`, `FLOWISE_API_KEY`, `ANTHROPIC_API_KEY` (o token `claude setup-token`), `GITHUB_TOKEN` (push harvest) | Il template li inietta come env; il bootstrap li consuma |

**Modello di esecuzione delle sessioni su pod:** il bootstrap installa
anche **Claude Code CLI nel pod**. La sessione M6 gira *dentro* il pod
(web terminal o SSH → `claude`, incollando l'handoff): ha accesso diretto
a GPU, servizi e filesystem, senza dipendere da tunnel dalla sandbox — che
non può raggiungere il pod. Il supervisore (questa sessione) segue via
issue GitHub e artefatti pushati.

Cosa deve fare l'utente (unico prerequisito umano di M6, ~15 minuti):
creare il Network Volume `caliper-artifacts` (100GB) nel DC scelto, creare
il template con i parametri della tabella, caricare i Secrets, avviare il
pod quando la sessione di preparazione dichiara `ops/runpod/` pronto.
Stima costi: ~0,7–0,9 $/h di pod + ~7 $/mese di volume — una sessione M6
completa costa nell'ordine di 5–15 $.

## 5. Protocollo di harvest — nessun output perso

La "cartella specifica" richiesta è doppia, per ridondanza:

1. **Sul Network Volume (persistente oltre il pod):**
   `/workspace/caliper-runs/<YYYYMMDD-HHMM>-<tag>/` — tutto, inclusi i
   binari (STEP generati, G-code, export completi).
2. **Nel repository (permanente e leggibile dalle sessioni future):**
   directory `runs/<YYYYMMDD>-<tag>/` committata sul branch della sessione
   — solo gli artefatti testuali/JSON curati.

`ops/runpod/harvest.sh <tag>` (scritto e testato in sandbox nella
preparazione di M6) raccoglie e verifica la checklist:

- `retry_log.jsonl` completo del run;
- i result JSON di `/exec/results` (copie) e i log dei servizi;
- gli STEP dei pezzi PASS (volume; sha256 nel manifest committato);
- **export dei chatflow Flowise** → `services/flowise/chatflows/` (diventano
  codice versionato, non restano nel pod);
- `env_fingerprint.json` (nproc, RAM, GPU/driver, versioni pacchetti,
  commit del repo) — obbligatorio per la regola #3 (numeri per ambiente);
- trascrizioni degli esiti TC-E2E (stdout salvato, non ricordato);
- `MANIFEST.json` con elenco file + sha256 + esito checklist.

Regole: harvest **esce non-zero se un artefatto atteso manca** → il pod non
si spegne con harvest rosso; l'harvest gira anche a metà sessione dopo ogni
TC-E2E completato (non solo alla fine — un pod può morire da solo); il push
su git è la seconda copia: **spegnere solo dopo push confermato**.

## 6. Supervisione e sequenza operativa

```
ora     M5 (sandbox) ──► verifica supervisore ──► merge (ok utente)
                                                     │
utente  crea volume+template RunPod (§4)             │
                                                     ▼
        M6-prep (sandbox: ops/runpod/, test CPU-only) ──► pod acceso
                                                     │
        M6 (sessione DENTRO il pod) ──► harvest ──► verifica supervisore
                                                     │
dal 24  M7 (RTX 3090, Docker reale) ─────────────────┤
        M8 (L6 + fisico, con l'utente) ◄─────────────┘
```

- Ogni fase ha: issue GitHub dedicata, handoff autosufficiente come
  commento sull'issue (regola post-#9), sessione esecutrice con
  `source_revision` esplicito, verifica indipendente del supervisore al
  gate.
- M5 parte **subito** (nessuna dipendenza). M6-prep parte al merge di M5.
  M7 e M8 sono parallelizzabili tra loro dal 24 (M8.1–M8.2, schema e
  bootstrap documentale, possono anzi partire prima: non dipendono da
  nessuna istanza).
- Le sessioni **non aprono PR e non mergiano**: pushano il branch,
  commentano l'issue, il supervisore verifica, l'utente decide il merge.

## 7. Rischi di questo piano (dichiarati)

- **RunPod ≠ topologia Docker**: il limite è strutturale (pod = container,
  niente DinD) ed è il motivo per cui M7 esiste. Se il 24 slittasse, M7
  slitta con la 3090 — nessun workaround che finga di validare la
  topologia senza la topologia.
- **Il bootstrap nativo può divergere dal compose** (versioni, path): il
  rischio è mitigato pinnando le stesse versioni dei Dockerfile/compose
  (`cadquery==2.8.0`, immagini Flowise/Qdrant pinnate — il pinning di
  Flowise, oggi `latest`, va fatto in M5) e dichiarando ogni divergenza nel
  fingerprint.
- **La generazione L2 reale può semplicemente fallire spesso** (è la prima
  volta): il piano lo tratta come *dato*, non come fallimento del piano —
  i tassi per strategia (`free_code` vs `param_first`) finiscono nel log
  virtuale e sono il primo contenuto empirico su cui calibrare le
  directive (design di misura già pronto da M2).
- **M8 dipende da lavoro umano non delegabile** (casi storici, stampe,
  calibro): il piano lo isola in una fase propria invece di nasconderlo
  dentro una milestone tecnica.

---

*Piano scritto il 2026-08-21 dalla sessione di supervisione (review issue
#15), su `claude/review-tecnica`. Handoff M5: `docs/handoff_m5.md`.*
