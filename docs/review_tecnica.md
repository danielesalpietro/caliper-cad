# Review tecnica critica — CALIPER (architettura + M1–M4)

Sessione di sola lettura, come da `docs/handoff_review.md` e issue #15.
Letti per intero: `architettura-prototipo-mesh-llm.md`, `logbook.md`,
`logbook_fase1-4.md`, `handoff_m1-m4.md`, tutto il codice di
`services/` (verifier, executor, orchestrator, stream-agent), gli script
`verify_*.py`, `docker-compose.yml`, `.github/workflows/regression.yml`,
`config/gauges/`, `presets.json`, le issue #4/#9/#10/#15 e le PR
#6–#8, #11–#14. Nessuna modifica a codice o configurazione; questo
documento è l'unico output.

---

## 1. Executive summary

L'impianto concettuale è solido e la disciplina di processo è sopra la
media di qualunque progetto a questo stadio: decisioni motivate,
riserve dichiarate, numeri misurati, bug reali trovati a ogni revisione.
Ma la review ha trovato **tre difetti nuovi, non documentati, tutti su
giunture mai esercitate end-to-end** — il più grave dei quali
(criticità C1) rende il percorso reale di M3 **incapace di produrre un
PASS per la feature `thread` con qualunque strategia L2**: il controllo
dimensionale di `run_and_measure.py` misura il bounding box del pezzo,
ma il pezzo di M3 è un foro in un blocco ospite più largo del nominale
per costruzione. Questo conferma che il limite "verificato solo su
fixture/mock" non è una somma di limiti locali accettabili: è un rischio
strutturale, perché **ogni giuntura effettivamente esercitata finora ha
rivelato un bug, e le giunture non esercitate ne contengono altri**
(questa review ne è la prova: C1, C2, C3 sono esattamente di quella
classe). La priorità corretta ora non è una nuova milestone di
funzionalità, ma un bring-up reale dello stack e il bootstrap del
Livello 6 — il pezzo di Fase A che ha valore dichiarato massimo e che
nessuna delle quattro milestone ha toccato.

## 2. Punti di forza (meritati, non di cortesia)

- **La revisione critica della proposta motore-fisico/GPU è corretta e
  ben argomentata** (`logbook.md` §2). L'argomento chiave — il bug
  `[v14]` era dimensionale puro, invisibile a un collision check — è
  un'evidenza vera, non retorica; e la scelta dei booleani esatti OCC
  su B-Rep è coerente sia col principio di determinismo sia con la
  metodologia metrologica reale (calibri Go/No-Go). Non ho trovato
  argomenti per rovesciarla.
- **La postura di sandboxing è genuinamente conservativa e coerente**:
  `network_mode: none` su `verifier-executor`, protocollo job/result su
  volume invece di HTTP verso il container isolato, `docker-socket-proxy`
  con `POST=0`, `resolve_under_root()` contro il path traversal, limiti
  di risorse per job. Le motivazioni scritte nei commenti corrispondono
  al codice reale (verificato riga per riga). Un'eccezione di integrità
  resta aperta — vedi C7 — ma è un problema diverso dall'isolamento
  dell'host, che regge.
- **La disciplina "misurare, non assumere" è applicata davvero**, non
  solo dichiarata: timeout 100s derivato dai 65.5s CPU misurati di TC2,
  epsilon elicoidale 0.5mm³ tarato sul residuo misurato (0.31mm³) con
  la causa geometrica isolata (estremità piatte non smussate), il segno
  della rotazione sincronizzata determinato per confronto empirico, il
  bug OpenBLAS trovato per misura. Il design della misura di efficacia
  delle directive (`logbook_fase2.md`) riconosce esplicitamente il
  confondimento con la variazione obbligatoria — un livello di igiene
  statistica raro.
- **Il firewall simulato/fisico di M4 è progettato bene**: due
  collezioni mai fuse, `source` obbligatorio in lettura e scrittura,
  regola anti-bias fail-open verso la generazione, e la correzione
  `geometry_key` (senza `l2_strategy`) trovata scrivendo un test con
  fixture realistica è il segno di un test che lavora. I limiti che
  restano (C5) sono di calibrazione, non di impianto.
- **Il processo di handoff/verifica incrociata funziona**: ogni PR
  M1–M4 documenta una riesecuzione indipendente con numeri riprodotti,
  e ogni milestone ha trovato e corretto bug reali della precedente
  (Dockerfile `COPY`, endpoint `/gauge-check` fermo a M1, `spec` mai
  inoltrata, timeout HTTP stantio). La CI di regressione con step
  espliciti per script è la scelta giusta.
- **Le riserve oneste sono davvero oneste**: nessuna delle affermazioni
  "verificato" che ho controllato si è rivelata gonfiata. Il problema
  non è cosa viene dichiarato, è cosa non poteva essere visto senza
  un'esecuzione reale (vedi C9).

## 3. Criticità concrete

Ordinate per gravità. Le prime tre sono bug nuovi, non presenti in
nessun logbook o riserva dichiarata.

### C1 — Il controllo dimensionale contraddice la topologia del pezzo di M3: nessun PASS end-to-end è possibile per `thread` (bloccante)

`services/verifier/executor/run_and_measure.py:131-150`: per
`feature == "thread"`, `measured_diameter = max(bbox_x, bbox_y)` viene
confrontato col nominale (M6 → 6.0mm ± tolleranza). Questa semantica
assume che il pezzo **sia** il cilindro filettato (era vera per il caso
`[v14]`, un tampone esterno). Ma da M3 il pezzo generato è un **foro
filettato in un blocco ospite**, e lo schema **impone** che il blocco
sia più largo del diametro maggiore
(`sketch_schema.py:327-333`: `size_mm[0/1] > major_diameter_mm`; il
caso di prova usa 20×20mm). Quindi, attraverso l'orchestratore reale:

1. `generate_and_verify.py:450` inoltra la spec (correzione M3, punto 1);
2. `run_and_measure.py` misura `max(20, 20) = 20mm` contro 6.0 ± 0.3;
3. `dimensional_check` = FAIL, `execution` ribaltata a FAIL;
4. il gauge-check (`generate_and_verify.py:456-483`) non viene mai
   raggiunto, perché scatta solo dopo un PASS di `/verify`.

E la strategia `free_code` non sta meglio: se L2 genera il foro nel
blocco (ciò che la spec chiede), fallisce allo stesso modo; se genera
un tampone pieno (bbox 6mm, dimensionale PASS), il calibro GO
interseca il pieno e il gauge-check fallisce. **Le due verifiche
richiedono topologie incompatibili: nessuna geometria può passarle
entrambe.** Nessun test lo rileva perché ognuno bypassa la giuntura:
`verify_sketch_compiler_thread.py:131-143` esegue `exec(code)`
direttamente (mai `run_and_measure.py`); il mock di
`verify_gauge_check_loop_wiring.py:105` risponde con un bbox 6×6×8 —
cioè codifica l'assunzione vecchia (tampone) dentro il mock. Ironia
amara: `verify_run_and_measure_export.py:65-78` esercita esattamente
questo comportamento (blocco 10mm vs M6 → FAIL) e lo tratta come esito
atteso corretto.

È il finding più importante della review anche come *meta*-evidenza:
le due correzioni di M3 ("inoltra la spec" + "collega il gauge-check")
sono individualmente giuste e insieme attivano un fallimento garantito
— visibile solo componendo i pezzi, cioè esattamente ciò che nessun
sandbox ha mai potuto fare.

### C2 — Nel loop gira solo il calibro GO: metà banda di tolleranza mai controllata

`generate_and_verify.py:157-167` (`gauge_check_job_for_preset`) usa
solo `gauge_go_step`, con la giustificazione in docstring che "il NO-GO
valida il calibro stesso, non il pezzo generato". Metrologicamente è
rovesciata: nel collaudo Go/No-Go **entrambi** i tamponi provano il
pezzo — il GO deve impegnarsi (foro non sottodimensionato), il NO-GO
**non** deve impegnarsi (foro non sovradimensionato). Con il solo GO,
un foro Ø7mm "filettato" a vuoto passa lo sweep senza interferenza e
il loop lo dichiara PASS (il bbox del blocco non lo intercetta, e con
C1 corretto continuerebbe a non intercettarlo). Il codice del NO-GO
esiste ed è validato dagli script manuali (`verify_gauge_check_tc2.py`,
con semantica invertita: interferenza = esito corretto), ma non è mai
collegato al loop. Serve la chiamata NO-GO con inversione dell'esito
(`PASS` dello sweep NO-GO = FAIL del pezzo) — piccola, ma concettuale:
oggi il collaudo copre solo il lato inferiore della tolleranza.

### C3 — Il preset `snap_fit` manda in errore l'orchestratore per costruzione

`presets.json:27-40` dichiara `snap_fit` `defined: true` con
`gauge_check_mode: "min_distance"` e **senza** `gauge_go_step` (la
modalità non usa un calibro, correttamente). Ma
`generate_and_verify.py:172-173` solleva `ValueError` per **qualunque**
preset con `gauge_check_mode` privo di `gauge_go_step` → `main()` esce
con "Preset incoerente" prima ancora di generare. Inoltre nessun codice
costruisce i parametri `min_distance` del job dai `measurement_points`
del preset: la modalità `min_distance` è raggiungibile via HTTP ma non
dall'orchestratore. `snap_fit` è quindi oggi *peggio* che
`defined: false`: sembra supportato e fallisce a runtime. Fuori scope
M3 dichiarato, sì — ma allora il preset doveva restare senza
`gauge_check_mode` (come `clearance_fit`), che il loop gestisce
correttamente (scenario C di `verify_gauge_check_loop_wiring.py`).

### C4 — Sketch-first: lo schema richiede precisione da double IEEE, che un LLM non può emettere; e per `thread` lo sketch è ridondante

Tre problemi concatenati, tutti in `sketch_schema.py`/`sketch_compiler.py`:

1. **Tolleranze di consistenza irrealistiche per output LLM.**
   `NUMERIC_TOLERANCE_MM = 1e-6` e `NUMERIC_TOLERANCE_DEG = 1e-3`
   (`sketch_schema.py:46-47`). Il punto di radice del profilo M6 è
   `x = 3 − 0.5/tan(30°) = 2.1339745962155613`. Se l'LLM lo arrotonda a
   `2.134` (4 decimali), l'errore di 2.5e-5mm sposta l'angolo calcolato
   di ~1.5e-3 gradi → oltre soglia → FAIL di schema. Per passare, il
   modello deve emettere le coordinate a ~8+ decimali esatti, cioè fare
   trigonometria a precisione piena — esattamente il compito che
   sketch-first voleva togliergli. In pratica quasi ogni generazione
   LLM sintatticamente sensata verrà bocciata dallo schema, ricadrà su
   `RETRY_GENERIC` × 3 e finirà `unrecoverable_virtual`.
2. **Finestra di non-manifold tra le due tolleranze.**
   `sketch_compiler.py:114` applica `CUT_OVERLAP_MM` solo ai punti con
   `|x − r_major| ≤ 1e-6`, ma il cross-check schema accetta scostamenti
   fino a `CROSS_FIELD_TOLERANCE_MM = 1e-3` (`sketch_schema.py:303`).
   Una spec con la cresta a `2.9999` è valida per lo schema, ma il
   compilatore non riconosce il punto come cresta, non applica
   l'overlap, e il boolean OCC va in tangenza quasi esatta — la stessa
   classe di fallimento che `CUT_OVERLAP_MM` esiste per prevenire
   (docstring di `generate_thread_gauge.py:39-44`).
3. **Le coordinate sono la fonte di verità, le quote solo un checksum.**
   Il compilatore costruisce il profilo dalle coordinate
   (`profile_coords`), non dalle quote. Per `helical_thread_cut` il
   profilo a V è **interamente determinato** da `pitch_mm` e
   dall'angolo (già nel preset): lo sketch non aggiunge alcuna
   informazione rispetto a `operation.*` — aggiunge solo modi di
   sbagliare. Oggi "sketch-first" per `thread` è di fatto
   "parameter-first con passaggi in più e più fragili". Vedi proposta P3.

Nota di coerenza col progetto: è lo stesso pattern già riconosciuto in
issue #10 per il parser di sezione ("sposta la fragilità, non la
elimina") — applicato però qui alla milestone stessa, non a una
proposta esterna.

### C5 — Memoria del collaudo virtuale: chiave troppo larga, soglia presa in prestito, esclusione senza via d'uscita

Risposte dirette a due domande dell'handoff:

- **`spec_key` non include né `tolerance` né `pitch`**
  (`virtual_memory.py:46`: `feature, nominal, tolerance_type,
  thread_standard, l2_strategy`). "M6 tol 0.05" e "M6 tol 0.5" — o
  M6×1.0 e M6×0.75 — collassano sulla stessa strategia. È in
  contraddizione con l'argomento fondante del Livello 7 in architettura
  ("'M6 tol.0.3' e 'M8 tol.0.3' sono simili per un embedding ma
  geometricamente diversi"): il filtro esatto è stato costruito, ma con
  metà dei campi che quel ragionamento richiede. Fallimenti virtuali su
  una tolleranza impossibile possono così escludere una tolleranza
  facile.
- **`MIN_VIRTUAL_FAILURES_FOR_EXCLUSION = 2` non è un numero misurato**
  (`virtual_memory.py:38`) — è dichiaratamente preso "per coerenza" da
  `EARLY_EXIT_CONSECUTIVE_REPEATS`, che governa un fenomeno diverso
  (fermare un loop in corso vs pregiudicare i loop futuri). Effetto
  pratico: **una singola run fallita** scrive 2–3 record FAIL per la
  stessa `spec_key` (`count_virtual_failures` conta i tentativi, non i
  casi — `virtual_memory.py:103-113`), quindi la soglia è superata da
  un solo caso sfortunato. Peggio: i FAIL contati includono anche
  errori non geometrici (JSON malformato da L2, `generate_and_verify.py:423-439`),
  che non dicono nulla sulla strategia geometrica.
- **La corroborazione fisica via `geometry_key` è cross-strategy per
  necessità** (il Livello 6 non registra la strategia) — compromesso
  documentato e difendibile *oggi*, ma la conseguenza è che un FAIL
  fisico prodotto dalla strategia B (o da codice umano) corrobora
  l'esclusione della strategia A sulla stessa geometria. La correzione
  strutturale è a costo quasi zero: **aggiungere la provenienza
  (`l2_strategy`/`generator`) allo schema del Livello 6 adesso**, prima
  che esista un solo record, così la corroborazione potrà stringersi
  quando i dati arriveranno. C'è anche una fragilità di matching più
  sottile: `geometry_key` include `thread_standard`, che nella query
  arriva dall'arricchimento preset (`apply_preset`) — se i casi L6
  verranno registrati con la spec *non* arricchita, la corroborazione
  non matcherà mai (stessa classe del bug `l2_strategy` già trovato in
  M4, un livello più in profondità; la fixture di
  `verify_virtual_memory_loop_gate.py` non lo vede perché usa
  `feature: "other"`, senza preset).
- **L'esclusione è permanente e non revocabile**: `retry_log.jsonl` è
  append-only, non c'è scadenza, né versione del checker nei record.
  Se il bug era del verificatore (lo scenario che la regola anti-bias
  teme, `[v14]`), correggere il verificatore **non** riabilita le
  strategie escluse: il conteggio resta sopra soglia per sempre e la
  strategia non viene mai più tentata, quindi nessun nuovo segnale può
  contraddire la memoria. La mitigazione copre l'ingresso
  nell'esclusione, non l'uscita.

### C6 — Gli ID dei punti Qdrant NON sono stabili tra riavvii (risposta diretta alla domanda dell'handoff)

`services/stream-agent/app.py:168` e `:222` usano
`abs(hash(...)) % (2**63)` su stringhe. Dal Python 3.3 `hash(str)` è
randomizzato per processo (PYTHONHASHSEED), e né il Dockerfile né il
compose lo fissano: **a ogni riavvio del container ogni caso già
indicizzato riceve un id nuovo**. Combinato con lo stato di
deduplicazione tenuto solo in memoria (`_indexed_files`,
`_virtual_log_offset` — `app.py:88-89`), ogni riavvio ri-legge e
ri-indicizza tutto con id diversi: l'upsert non deduplica mai e le
collezioni si riempiono di duplicati (con costo di embedding ripetuto).
In più `abs()` raddoppia la probabilità di collisione tra chiavi
diverse. Fix banale: id deterministico da contenuto
(`uuid.uuid5(NAMESPACE, key)` o md5), offset persistito su disco. È il
caso da manuale del perché "codice scritto e `py_compile`-verificato"
(riserva onesta di M4) non copre questa classe di errore: serve un
processo che muoia e riparta.

### C7 — Il verdetto è scritto dallo stesso processo che esegue il codice non fidato: un PASS è falsificabile

`run_and_measure.py:77` esegue `exec(code)` **nello stesso processo**
che poi scrive `result.json`; il watcher accetta il file di risultato
se esiste (`watcher.py:56-68`). Il codice generato può quindi: leggere
`sys.argv[2]`, scrivere un result con `execution: "PASS"` e misure
plausibili, copiare come STEP "generato" un pezzo noto-buono letto da
`/models` (montato read-only ma leggibile) e chiamare `os._exit(0)`.
Tutti i controlli a valle (gauge-check compreso) collauderebbero il
pezzo copiato, non quello generato. Non è un problema di sicurezza
dell'host (il container regge) ma di **integrità del giudizio**, cioè
della proprietà che dà il nome al progetto. Il modello di minaccia non
è fantascienza: è il reward hacking di un LLM in un loop di retry che
viene letteralmente istruito dei motivi del fallimento precedente. La
correzione concettuale è separare i privilegi: il processo che esegue
`exec()` non deve essere quello che firma il verdetto (vedi P5).

### C8 — Il budget CPU del gauge-check non è trasferibile tra macchine: "misurato" sì, ma la grandezza misurata è sbagliata

Risposta alla domanda dell'handoff su 100s/400s: i numeri **sono**
misurati (65.5s CPU × 1.5 → 100; CI fallita a ~33s wall → 400), ma
`RLIMIT_CPU` somma il tempo CPU di **tutti i thread**, e OCC
parallelizza i booleani in base ai core visibili
(`gauge_check.py:120-134`, commento onesto sul punto). Quindi lo stesso
identico lavoro consuma budget diversi su macchine diverse — è già
successo: dev 65.5s CPU/23s wall, CI oltre 100s CPU/33s wall. Il
"limite di CPU" attuale misura *lavoro × parallelismo dell'ambiente*,
non lavoro. Conseguenza pratica: il valore di produzione (100s) tarato
sul sandbox di sviluppo può uccidere job legittimi nel container reale
(mai provato, vedi C9) o su hardware futuro, e ogni ambiente nuovo
richiederà una ricalibrazione a fallimento avvenuto (come in CI).
Alternative: fissare il numero di thread OCC per il processo di
gauge-check (rendendo CPU-time ≈ wall-time e il budget portabile), o
spostare l'enforcement sul solo timeout wall-clock esterno del watcher,
che esiste già ed è indipendente dal parallelismo.

### C9 — "Mai contro un'istanza viva" è un rischio strutturale, non una somma di limiti locali (risposta alla prima domanda dell'handoff)

Il tasso di base osservabile nel progetto stesso: **ogni giuntura
esercitata per la prima volta ha rivelato almeno un bug reale** —
Dockerfile `COPY` (M1), endpoint HTTP fermo a `static_interference`
(pre-M3), STEP mai esportato + mount `:ro` (M3), timeout HTTP stantio
(M3), `spec` mai inoltrata (M3), `geometry_key` irraggiungibile (M4).
Questa review, senza eseguire nulla, ne ha trovati altri tre della
stessa classe su giunture non ancora esercitate (C1, C2, C3) più due
che solo un'esecuzione reale mostrerebbe (C6, C8-produzione). Le
giunture ancora mai esercitate sono tante e tutte portanti: le immagini
Docker **mai costruite** (nemmeno una volta), i chatflow L2 **non
versionati** (quello sketch-first non esiste proprio — vive solo
nell'ipotesi di un'istanza configurata a mano), `overrideConfig` sulla
temperatura mai provato, Qdrant/Ollama mai raggiunti dal codice M4, i
mount di `billa05/prusacli` dichiaratamente dedotti e mai verificati.
L'induzione è diretta: aspettarsi che quelle giunture siano le prime
pulite della storia del progetto è l'assunzione non verificata più
grossa attualmente in piedi. Ogni milestone ha dichiarato onestamente
il proprio limite locale — ma nessun documento tira la somma: **il
sistema integro non è mai esistito**, e i criteri di accettazione
"end-to-end" di M3/M4 sono strutturalmente non soddisfacibili in
sandbox. Continuare ad accumulare milestone su questa base fa crescere
il costo del primo bring-up in modo superlineare (i bug si mascherano
a vicenda: C1 nasconde C2 dietro di sé).

### C10 — Limiti secondari, elencati per completezza

- **Aliasing dello sweep discreto**: 21 step su 8mm ≈ 0.4mm/step
  (`presets.json:11`); un difetto locale più stretto del passo di
  campionamento (tra due posizioni discrete) può sfuggire. Con passo
  1.0mm e campioni ogni 0.4mm la copertura angolare è ragionevole, ma
  non è mai stata misurata la sensibilità (es. con un difetto sintetico
  noto più stretto di uno step). L'epsilon elicoidale 0.5mm³ è inoltre
  tarato sul residuo dei *calibri attuali senza smusso*: rigenerare i
  calibri (nota aperta di M2) invalida la soglia — le due cose vanno
  versionate insieme.
- **Concorrenza del verifier**: il watcher processa i job in serie
  (`watcher.py:139-141`); due `/verify` concorrenti (FastAPI li serve
  in parallelo) mettono il secondo in coda mentre l'attesa HTTP è 30s
  (`app.py:43`) — con un gauge-check da 150s davanti, un `/verify`
  legittimo riceve un falso "nessuna risposta dall'executor".
- **Esposizione di rete**: il compose pubblica su tutte le interfacce
  dell'host Qdrant (6333), Ollama (11434) e il verifier (8600) senza
  autenticazione (`docker-compose.yml:55-57, 67-68, 126-127`). Il
  verifier accetta codice arbitrario da chiunque raggiunga la porta —
  l'esecuzione è sandboxata, ma su una LAN domestica è comunque una
  superficie gratuita: `127.0.0.1:` come prefisso di bind costa una
  riga a servizio.
- **`/exec/parts` senza pulizia** (già notato in PR #11, mai tracciato
  come issue) e `_indexed_files` senza bound (`app.py:88`).
- **CI solo su `develop`** (`regression.yml:20-24`): i branch delle
  sessioni non eseguono la regressione finché non aprono PR — le
  sessioni lavorano quindi senza la rete di sicurezza che la CI doveva
  dare proprio a loro.
- **`check_result_assigned` riconosce solo `ast.Assign`**
  (`app.py:105-117`): `result: cq.Workplane = ...` (AnnAssign) o un
  assegnamento con walrus vengono bocciati — un falso FAIL che brucia
  un tentativo di retry.
- **Determinismo verificato solo intra-macchina**: byte-per-byte su
  due run nella stessa sessione — mai tra macchine/numero di core
  diversi, con OCC multithread. Probabilmente regge (stessa versione
  pinnata), ma è un'assunzione, e il progetto ha una regola precisa su
  cosa farne.

### C11 — Priorità e coerenza degli schemi (risposte alle ultime domande dell'handoff)

- **M1–M4 vs Fase A/B**: la convivenza formale regge (M1–M4 sono
  dentro Fase A, nessuna dipendenza da Fase B). Ma sostanzialmente le
  quattro milestone hanno approfondito un solo livello (L3) mentre i
  deliverable che danno a Fase A il suo valore dichiarato ("protezione
  del metodo": dataset congelato + difesa dalla deriva del cloud) sono
  fermi da prima di M1: **Livello 6 mai popolato** (il bootstrap
  retroattivo richiede solo lavoro di documentazione con i casi storici
  già validati — nessuna dipendenza tecnica da M1–M4), **conferma umana
  L2.5 mai progettata** (mitigazione definita *obbligatoria* dal
  Rischio #5), L7-consultivo senza contenuto da consultare. M4 ha
  costruito l'infrastruttura di retrieval sopra un dataset vuoto: il
  gate anti-bias è oggi inerte per costruzione (nessun dato fisico →
  mai esclusione), cioè la milestone "chiusa" non può fare nulla finché
  il lavoro mai schedulato non viene fatto.
- **Sequenzialità M1→M4**: M1→M2 è una dipendenza vera; M3 dipende da
  M1/M2 solo per il wiring del gauge-check — schema e compilatore
  sketch-first erano paralleli; M4 dipende da M2 (formato del retry
  log) più che da M3. Ma la parallelizzazione persa più costosa non è
  tra le M: è che il bootstrap L6 e il meccanismo di conferma L2.5
  potevano correre in parallelo a *tutte e quattro* e non sono mai
  entrati in nessun piano.
- **Quanto è vicino il resto allo sketch-first (domanda M3)**:
  `clearance_fit` è vicino per il percorso `free_code` + gauge-check
  (basterebbe `gauge_check_mode: "sweep"` con `pitch_mm: 0` nel preset,
  la modalità lineare è già validata in TC1) ma è bloccato da C1 (il
  controllo dimensionale bbox ha la stessa incompatibilità col mozzo
  forato) e dal compilatore mono-operazione
  (`SUPPORTED_OPERATION_TYPES = ("helical_thread_cut",)`) — dove
  peraltro un `cylindrical_cut` sarebbe *più semplice* del thread.
  `snap_fit` è più lontano: C3, più il problema aperto (dichiarato nel
  preset) di far emettere a L2.5 punti di misura coerenti con la
  geometria generata. In sintesi: l'ostacolo reale non è lo schema, è
  che il contratto dimensionale di `/verify` non è mai stato
  generalizzato oltre il caso "tampone thread" di v14.

## 4. Proposte alternative (con pro/contro)

### P1 — Ridefinire la prossima milestone come "M5: bring-up reale", prima di qualunque nuova funzionalità

Contenuto: build reale delle immagini, `docker compose up` completo,
prima esecuzione L2.5→L2→/verify→/gauge-check con Flowise/Qdrant/Ollama
vivi, previa correzione di C1/C2/C3 (che altrimenti la bloccano al
primo colpo). Il criterio di accettazione di M3 (mai raggiunto, issue
#4 ancora aperta) diventa il criterio di M5.
**Pro**: scarica il rischio strutturale C9 al costo più basso possibile
(ogni milestone in più lo fa crescere); trasforma le riserve oneste
accumulate in verifiche; dà finalmente un numero reale a overrideConfig,
timeout in container, mount prusaslicer. **Contro**: richiede
l'ambiente dell'utente (GPU, Docker) — non delegabile a una sessione
sandbox; poco "nuovo valore" visibile; il fix di C1 richiede una
decisione di design non banale (vedi P2). **Costo**: giorni, non
settimane; in gran parte lavoro dell'utente + una sessione di supporto.

### P2 — Sostituire il controllo dimensionale bbox con un contratto per-feature

Il check bbox va bene solo per pezzi "pieni" il cui inviluppo è la
quota. Opzioni: (a) dichiarare nel preset *cosa* misurare (es. per
`thread`: nessun check bbox sul diametro — il Go/No-Go È il controllo
dimensionale del foro; tenere il bbox solo come sanity check ≥
nominale); (b) misurare davvero il foro (sezione via
`BRepAlgoAPI_Section`, già valutata in issue #10, o `BRepExtrema`
dall'asse). **Raccomandazione: (a)** — il progetto ha già costruito lo
strumento di misura giusto (i calibri); duplicarlo con una misura
ricostruita contraddice il Rischio #3 (parametrico/calibro > ricostruito
da geometria). **Pro**: elimina C1 senza nuova geometria; coerente col
principio "il preset dichiara i controlli". **Contro**: perde un check
indipendente dal calibro; se il calibro è sbagliato non c'è seconda
linea (mitigato dal fatto che i calibri sono auto-verificati alla
generazione e versionati).

### P3 — Per le feature con preset, "parameter-first" al posto di sketch-first

Dato C4: per `helical_thread_cut` lo sketch non porta informazione —
solo rischio. Alternativa: L2 emette **solo i parametri numerici**
(`major_diameter_mm`, `pitch_mm`, `engagement_length_mm`, host), il
compilatore deriva le coordinate del profilo con la trigonometria che
già conosce (`generate_thread_gauge.py` lo fa da M1). La validazione di
consistenza quota↔coordinate sparisce perché non c'è più nulla da
tenere consistente. **Pro**: elimina in blocco i tre problemi di C4;
superficie di errore LLM minima davvero (pochi numeri con range
fisici verificabili); riusa il codice esistente. **Contro**: vale solo
per feature con preset/operazione nota — per geometrie libere lo
sketch-first resta l'idea giusta *in prospettiva*; riduce L2 a "compila
un modulo", il che rende legittimo chiedersi se per le feature a preset
serva un LLM in generazione (risposta onesta: quasi no, e questo è un
punto a favore, non contro — il valore dell'LLM sta in L2.5 e nelle
feature non catalogate). Se si tiene sketch-first, il minimo è: quote
autoritative con coordinate *derivate* dal compilatore (constraint
solving semplice per il caso a V), o tolleranze di consistenza allargate
di 3-4 ordini di grandezza + `is_crest` agganciato alla stessa
tolleranza dello schema.

### P4 — Rendere la memoria virtuale revocabile e la chiave onesta

(1) Aggiungere `tolerance` e `pitch_mm` a `SPEC_KEY_FIELDS`; (2)
contare i **casi** (case_id distinti), non i tentativi, ed escludere
dagli aggregati gli errori non geometrici (JSON malformato ecc.); (3)
scrivere in ogni record una versione del checker (hash di
`gauge_check.py` o un numero manuale) e far contare solo i FAIL della
versione corrente; (4) aggiungere provenienza (`l2_strategy`) allo
schema L6 ora, a costo zero. **Pro**: chiude tutte le lame di C5 con
modifiche piccole e locali; (3) dà l'uscita dall'esclusione che oggi
manca. **Contro**: (1) rende la memoria più sparsa (serve più volume
prima che il gate morda — accettabile: meglio inerte che sbagliato);
(3) azzera la memoria a ogni fix del checker (è il comportamento
voluto: un giudice cambiato non eredita i pregiudizi del precedente).

### P5 — Separare l'esecuzione dal verdetto nel verifier-executor

Il subprocess che fa `exec(code)` esporta solo lo STEP (o un dump
neutro); la misura (validità, bbox, dimensionale) viene fatta da un
**secondo subprocess che non esegue mai codice non fidato**, lanciato
dal watcher sul file esportato; il result lo scrive solo quest'ultimo.
Un pezzo "copiato" da `/models` resterebbe possibile solo smontando
`/models` dal processo di exec (o filtrando: il codice non fidato non
ha bisogno di leggere i modelli di riferimento). **Pro**: il verdetto
diventa non falsificabile dal codice sotto giudizio — chiude C7
riportando l'integrità al livello del resto della postura; riusa il
protocollo file già esistente. **Contro**: un import STEP + misura in
più per job (~secondi); più parti mobili nel watcher. Dato che
l'intera ragione d'essere del progetto è non fidarsi dell'output LLM,
il costo è giustificato.

### P6 — Ridimensionare il ruolo di Flowise, o dichiararne uno sostenibile

Stato di fatto: il progetto ha già tolto a Flowise l'orchestrazione
(v13, bug di interpolazione), il collaudo (Rischio #9) e non ne
versiona i chatflow L2 (il chatflow sketch-first non esiste); restano
un prompt template e una chiamata modello, su un'immagine con tre bug
documentati (`ReActAgent`, `fetch failed` intermittente, interpolazione)
e `image: latest` non pinnata. L'alternativa è che
`generate_and_verify.py` chiami direttamente il provider (OpenAI/Ollama
API — ~30 righe, prompt versionato in git come i chatflow L2.5), tenendo
Flowise per ciò che rende bene: prototipazione interattiva di L2.5 e
L7-consultivo. **Pro**: elimina il componente più inaffidabile dal
percorso critico; il prompt L2 diventa finalmente versionato e
riproducibile; `overrideConfig` (mai verificato) smette di essere una
dipendenza. **Contro**: contraddice una decisione presa (v3/v9) e
rinuncia alla UI per iterare sul prompt di generazione; un componente
in più da mantenere nel codice proprio. Se si tiene Flowise: almeno
pinnare la versione e versionare *tutti* i chatflow, incluso L2 — oggi
il criterio di M3 dipende da configurazione manuale non riproducibile.

### P7 — Schedulare il bootstrap del Livello 6 come milestone propria, adesso

I casi storici di Fabrizio (prompt, STL, parametri slicing, misura al
calibro) sono l'unico dato fisico esistente al mondo per questo
progetto, e nessuna sessione può produrli: solo documentarli. Ogni
componente a valle (L7, gate anti-bias, campo diagnostico, test "il
verificatore boccia i casi noti-falliti") è inerte finché non esistono.
**Pro**: sblocca il valore dichiarato di Fase A; definisce lo schema L6
reale (oggi il codice accetta due convenzioni di nomi campo,
`specifica_strutturata`/`spec`, `esito`/`outcome` — segno che lo schema
non esiste); è parallelo a tutto il resto. **Contro**: lavoro
dell'utente, poco automatizzabile; espone al bias di sopravvivenza già
previsto (Rischio #8) se i casi FAIL storici non sono stati conservati
— da accettare e dichiarare, non da aggirare.

## 5. Domande aperte residue

Genuinamente aperte anche dopo questa review:

1. **Il collaudo Go/No-Go discreto ha la risoluzione che serve?** Non è
   mai stato misurato il più piccolo difetto che lo sweep a 21 step
   rileva (aliasing tra step, C10). Un esperimento da un pomeriggio:
   iniettare difetti sintetici di larghezza decrescente in un foro
   noto e trovare la soglia di rilevamento. Finché non c'è, "il calibro
   virtuale funziona" ha un asterisco quantitativo.
2. **Che rapporto c'è tra PASS virtuale e PASS fisico?** È la domanda
   fondante del progetto e nessun dato la tocca ancora: il primo lotto
   di stampe reali di pezzi PASS-virtuali (dopo P1+P7) è l'esperimento
   che decide se il Livello 3 esteso predice qualcosa. Se il tasso di
   concordanza è basso, tutta la sofisticazione M1–M4 va ricalibrata su
   ciò che la stampa reale effettivamente sbaglia (shrinkage, primo
   layer, sovraestrusione) — che nessun boolean B-Rep vede.
3. **Chi conferma la spec L2.5?** Il meccanismo di conferma umana
   (obbligatorio, Rischio #5) non è mai stato progettato: UI? CLI? Un
   campo `confirmed_by` nel job? Finché non esiste, ogni esecuzione
   end-to-end reale avrà un componente non deterministico non
   supervisionato a monte di tutto il resto.
4. **Il profilo a V non troncato basta davvero?** Tutto il collaudo
   (calibri e pezzi) usa la stessa approssimazione; la nota in
   `generate_thread_gauge.py` avverte che calibri e generatore vanno
   rigenerati insieme se si passa al profilo ISO reale — ma non è noto
   se l'approssimazione concordi con ciò che *lo slicer e la stampante*
   producono (il filetto stampato ha le creste arrotondate dal bead).
   Risposta possibile solo con il punto 2.
5. **La lunghezza di impegno forzata (`host_z == engagement`) regge il
   primo caso reale?** Un pezzo utile ha quasi sempre un foro cieco o
   un ospite più profondo; il vincolo attuale (corretto per il collaudo,
   `sketch_schema.py:350-356`) è incompatibile con la geometria dei
   pezzi veri. Il controcavo/gioco oltre l'impegno è dichiarato fuori
   scope, ma è il primo muro contro cui sbatterà qualunque caso d'uso
   non sintetico.
6. **Su quale hardware gira "la produzione"?** I budget (CPU 100s, 2GB,
   timeout 150s/200s/220s a catena) sono tutti tarati su ambienti che
   non sono il container reale mai costruito (C8/C9). La catena di
   timeout annidati (10/15, 100/150/200/220) è coerente oggi, ma è
   manutenuta a mano in quattro file diversi — quanto regge alla
   prossima ricalibrazione?

---

*Review condotta il 2026-08-21 su `develop` @ `782704b`. Nessun file di
codice o configurazione modificato.*
