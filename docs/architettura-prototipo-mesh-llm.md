# Architettura teorica — Prototipo LLM-driven Mesh Generation con validazione fisica

> Stato: bozza di progettazione. Nessun codice ancora scritto salvo dove indicato.
> Ultimo aggiornamento: da versionare insieme al progetto reale.

## Contesto

Metodo di partenza (Fabrizio, autodidatta, gennaio–oggi): generazione di
geometrie tramite mesh di triangolazione (non analisi strutturale FEM),
ottenute con prompt semantico-descrittivo su LLM cloud (GPT, verifiche
incrociate con Gemini), STL processata via slicing e stampata in 3D.
Risultati validati: accoppiamenti filettati con tolleranze di tre decimi,
funzionanti dopo la stampa.

**Domanda aperta (RISOLTA) [v5]:** il metodo attuale produce oggi STL
diretta, non codice parametrico. Decisione presa: il Livello 2 (Fase A)
passa a un **doppio passaggio** — generazione di codice Python
(CadQuery/OpenSCAD), poi esecuzione locale di quel codice per produrre sia
STEP (B-Rep, quote esatte) sia STL (per lo slicer), dalla stessa sorgente
parametrica. Non è un cambiamento cosmetico: apre due canali di ispezione
indipendenti (B-Rep esatto vs. mesh tessellata) che permettono di isolare
se uno scostamento dimensionale nasce nella generazione del codice o nella
tessellazione/slicing a valle — impossibile da distinguere con la sola STL
diretta usata finora. Vedi Livello 2 nello schema e nota sul Livello 6 più
sotto.

Bisogno reale identificato: **stabilità e controllo del workflow**, non
potenza di calcolo — proteggere un metodo già funzionante da cambiamenti
imprevedibili lato modello cloud (aggiornamenti, filtri, comportamento meno
letterale), mantenendo autonomia senza diventare specialisti ML.

## Fasatura del progetto (revisione critica accolta)

L'architettura originaria trattava L2 (motore di generazione locale) come un
componente già affidabile. Non lo è: va isolato come rischio esplicito e
separato dalla parte che risolve davvero il bisogno dichiarato (stabilità
del workflow). Il progetto si divide quindi in due fasi **sequenziate**,
disaccoppiate in una sola direzione (vedi più sotto):

- **Fase A — Protezione del metodo** (L1, L2.5, L3, L4, L5, L6, L7-consultivo
  tardivo — disponibile solo dopo il bootstrap, vedi Livello 7 sotto). Il
  motore di generazione resta il cloud (GPT/Gemini), invariato. Si aggiungono
  normalizzazione della specifica, verifica automatica e dataset congelato.
  Rischio basso, valore immediato: se il modello cloud deriva, il
  verificatore lo intercetta e il dataset preserva ciò che funzionava.
- **Fase B — Autonomia locale** (L2 locale, L7-integrato nel loop, L8).
  Condizionata a un test di fattibilità esplicito (vedi Rischio #1 sotto).

**Direzione della dipendenza (correzione):** la Fase A è autosufficiente e
non dipende dalla Fase B. La Fase B **dipende dalla Fase A**, non il
contrario: il test di fattibilità del Rischio #1 usa il Livello 3 (Fase A)
come metro di giudizio per il motore locale, e il Livello 7 in modalità
integrata (Fase B) indicizza il contenuto del Livello 6 (Fase A). Senza
Fase A completata, la Fase B non ha né criterio di verifica né dataset da
usare. Non si parte con il motore locale prima di avere il verificatore.

## Modello di esecuzione: nodi componibili e preset

L'architettura descritta sopra è, di fatto, un **workflow a stadi**: ogni
Livello ha input tipizzato, output tipizzato, ed è concettualmente
sostituibile — non un blocco di codice monolitico. Vale la pena renderlo
esplicito, perché cambia come viene implementato, non cosa fa.

**Nodi.** Ogni Livello (L1, L2.5, L2, L3, L4, L5, L6, L7, L8) è trattato
come un nodo indipendente: riceve un input definito, produce un output
definito, è testabile in isolamento, è sostituibile senza toccare gli
altri nodi. Questo è già implicito nella Fasatura A/B (es. L2 cloud e L2
locale sono due implementazioni dello stesso nodo), ma va reso esplicito
come principio di implementazione.

**Preset.** Un preset è una configurazione pronta per una classe di feature
meccanica ricorrente (es. "Filettatura metrica ISO", "Accoppiamento a
pressione", "Incastro a scatto"). Ogni preset pre-compila: lo schema di
specifica del L2.5 per quella feature, il tipo di tolleranza di default
(diametrale/per lato/su nocciolo/su cresta), e quali controlli del L3
applicare. Questo riduce direttamente il Rischio #5 (ambiguità di
normalizzazione): per le feature note, gran parte del L2.5 non richiede più
interpretazione libera da parte di un LLM, perché il preset la vincola in
anticipo.

**Motore di esecuzione — decisione esplicita (revisionata, v3).** Tre
opzioni erano originariamente sul tavolo:

1. Costruire un motore di workflow proprio (grafo, esecuzione, UI) —
   scartata: infrastruttura enorme, indipendente dal vero differenziatore
   del progetto (verifica deterministica + dataset fisico), rischio di
   disperdere lo sforzo prima ancora di validare il Rischio #1.
2. Riusare n8n, scrivendo i nodi di questo progetto come custom node
   package installabile in un'istanza n8n esistente.
3. Un runner leggero, pipeline dichiarativa (YAML/JSON) eseguita da script,
   senza UI a grafo.

**Scelta aggiornata: Flowise, scoped ai nodi LLM-centrici.** Flowise
(LangChain-based, grafo drag-and-drop, nodo Qdrant nativo) orchestra i
livelli conversazionali/LLM della pipeline — L1 (input), L2.5
(normalizzazione), L2 (generazione), L7-consultivo (retrieval) — dove il
concetto di "chatflow/agent flow" è un fit naturale. Non è però un motore
per step deterministici o con side-effect fisici: L3 (verifica geometrica),
L4 (slicing), L5 (misura fisica), L6 (scrittura dataset) restano script
esterni, esposti a Flowise come Custom Tool / chiamata HTTP, non riscritti
al suo interno — altrimenti si perderebbe la garanzia di determinismo che
il Livello 3 richiede (vedi Rischio #9). Questo rende superflua, per ora,
la scelta tra le opzioni 2 e 3 originarie: Flowise copre la parte
orchestrata via UI che n8n avrebbe coperto, restando scoped alla porzione
LLM invece che a tutta la pipeline.

## Interfaccia web — decisione esplicita

Due componenti distinti, entrambi ripresi da NORTHSTREAM
(`danielesalpietro/NORTHSTREAM`) invece che dalla web UI Gradio di
RecursiveMAS (scartata per questo scopo):

- **Open WebUI** — interfaccia di chat, già usata in NORTHSTREAM per
  parlare con Ollama e con lo stream-agent tramite un endpoint
  OpenAI-compatible. Qui diventa il modo di interrogare in linguaggio
  naturale il Livello 7 (dataset congelato + retrieval), non di eseguire
  la pipeline: l'esecuzione resta compito di Flowise.
- **Landing / dashboard page** — le pagine statiche di overview di
  NORTHSTREAM (`index.html` + `dashboard.html`), riadattate come punto
  d'ingresso allo stack Docker di CALIPER: link a Flowise, a Open WebUI,
  allo stato dei servizi. Nessuna logica applicativa propria.


```
LIVELLO 1 — INPUT / INTERFACCIA
  Prompt semantico-descrittivo (linguaggio naturale)
  es. "foro filettato M6, tolleranza 0.3mm, passo 1.0"
        |
        v
LIVELLO 2.5 — NORMALIZZAZIONE SPECIFICA  [FASE A]
  Prompt naturale -> specifica strutturata (JSON)
  es. {"feature": "thread", "nominal": "M6", "pitch": 1.0,
       "tolerance": 0.3, "tolerance_type": "diametrale|per_lato|
       su_nocciolo|su_cresta", "measured_as": "..."}
  Necessario perché "tolleranza 0.3mm" da sola è ambigua e rende
  il PASS/FAIL del Livello 3 mal definito
  ATTENZIONE: se questa traduzione è fatta da un LLM, introduce un
  componente non deterministico A MONTE del verificatore — dettagli e
  mitigazione obbligatoria: vedi Rischio #5 più sotto
  MECCANISMO TECNICO (in aggiunta alla mitigazione, non in sua sostituzione):
  decoding vincolato allo schema JSON (constrained decoding / JSON Schema
  enforcement) — vincola la sintassi (l'output non può che essere JSON
  valido secondo lo schema), non la semantica (può comunque essere
  sintatticamente valido con la tolleranza sbagliata). Per questo la
  conferma umana del Rischio #5 resta necessaria: sono due difese
  indipendenti, non alternative — la seconda copre ciò che la prima non può
        |
        v
LIVELLO 2 — GENERAZIONE GEOMETRICA (motore)  [A: cloud / B: locale]
  Fase A: GPT/Gemini, DOPPIO PASSAGGIO [v5, deciso, sostituisce STL diretta]:
    1. generazione di codice Python (CadQuery/OpenSCAD)
    2. esecuzione locale del codice -> STEP (B-Rep, quote esatte)
                                     -> STL (per lo slicer)
    Stessa sorgente parametrica, due canali di ispezione indipendenti: uno
    scostamento dimensionale è isolabile come nato nel codice generato
    (visibile su STEP) o nella tessellazione/slicing a valle (visibile solo
    confrontando STEP vs STL) — impossibile da distinguere con la sola STL
    diretta usata finora
  Candidato aggiuntivo [v4], NON validato: Zoo Text-to-CAD (KittyCAD API)
    -> genera B-Rep/STEP nativamente, non tessellazione; termine di
       paragone o alternativa al doppio passaggio via codice, richiede un
       proprio test di fattibilità su feature filettate prima di essere
       equiparato a GPT/Gemini (vedi tabella componenti)
  Fase B (condizionata, vedi Rischio #1):
    Candidati: CAD-Recode / cadrille (Qwen2-1.5B/2B) — via preferita,
               produce codice CadQuery/Python parametrico
               oppure LLaMA-Mesh (mesh diretta) — via secondaria
    Parametro operativo per il test di fattibilità: temperature=0.
    Garantisce RIPRODUCIBILITÀ (stesso prompt -> stesso output), non
    ACCURATEZZA (un output riproducibile può comunque essere
    geometricamente sbagliato in modo consistente). Serve a rendere
    il test del Rischio #1 confrontabile tra run, non a risolvere la
    precisione dimensionale — quella resta compito del Livello 3
  Output: codice Python -> STEP + STL (Fase A), oppure mesh diretta
          (via LLaMA-Mesh in Fase B, se scelta)
        |
        v
LIVELLO 3 — VERIFICATORE GEOMETRICO, DOPPIA MODALITÀ  [FASE A]
  Script deterministico, NON un LLM — scelta più forte del pattern
  generico "LLM-as-judge" (un secondo modello che valuta il primo,
  ancora probabilistico anche se indipendente): qui il giudice non è
  un modello linguistico ma uno script deterministico o una misura
  fisica, quindi non eredita l'incertezza che vorrebbe eliminare
   a) Verifica parametrica (primaria, se disponibile codice CAD):
      confronto esatto sui parametri del codice/B-rep contro la
      specifica strutturata del L2.5, prima della tessellazione
   b) Verifica mesh (sempre eseguita, universale):
      - controllo manifold / watertight (integrità per lo slicer)
      - se non c'è codice parametrico (es. via LLaMA-Mesh): misura
        dimensionale ricostruita dalla mesh come fallback
  Output: PASS / FAIL con delta numerico, contro la specifica L2.5
        |
   PASS |   FAIL -> retry L2 (vedi policy di retry sotto)
        v
LIVELLO 4 — SLICING E STAMPA  [FASE A]
  [v6, deciso] PrusaSlicer, modalità CLI headless, containerizzato —
  pattern di riferimento: Billa05/prusaslicer-cli-docker (CLI-only,
  nessuna GUI). Comando: prusa-slicer-console --export-gcode
  --load profilo.ini modello.stl -o output.gcode
  Profilo come file .ini versionato nel repository — "parametri fissi e
  congelati" del Livello 4 non è più uno stato nascosto in una GUI, è un
  file tracciato in git
  Default iniziali (PLA, negoziabili, NON ancora la versione finale):
   - layer height: 0.2mm
   - perimetri: 3
   - temperatura ugello/piatto: DA DEFINIRE — dipende dal materiale/marca
     filamento effettivo, domanda ancora aperta e collegata al campo
     materiale/batch del Livello 6 (vedi Rischio #4)
  Stampa fisica
        |
        v
LIVELLO 5 — VALIDAZIONE FISICA (verità ultima)  [FASE A]
  Misura reale (calibro), test funzionale (si avvita?)
  Dato non sostituibile da alcun simulatore
        |
        v
LIVELLO 6 — DATASET CONGELATO (ground truth)  [FASE A]
  Ogni caso: {prompt, specifica strutturata L2.5, codice Python generato,
              STEP, STL, parametri slicing, macchina, materiale/batch
              filamento, data, misura fisica, esito PASS/FAIL,
              modello_di_riferimento (opzionale)}
  Campi minimi realmente misurabili — niente temperatura/umidità
  promesse ma mai registrate nella pratica
  Versionato, immutabile una volta validato
  REGOLA OPERATIVA: da ora in poi, ogni FAIL fisico va registrato con
  la stessa cura di ogni PASS — non solo "se disponibili" (vedi nota
  sul bias di sopravvivenza più sotto)
  CAMPO DIAGNOSTICO [v5, deciso]: dove disponibile, ogni caso è
  confrontato con un modello CAD costruito in modo convenzionale
  (disegnato a mano, non generato da LLM) per la stessa feature. Il
  confronto non è binario come il PASS/FAIL fisico: quantifica QUALE
  parametro del codice generato si discosta dal riferimento e di quanto
  (es. passo filettatura 0.98mm generato vs 1.00mm di riferimento).
  Trasforma il dataset da registro di esiti a materiale diagnostico
  utilizzabile per capire perché un caso fallisce, non solo che fallisce
  — rilevante sia per il L3 (quali soglie impostare) sia per un
  eventuale L8 (segnale di errore più ricco del solo PASS/FAIL)
        |
        v
LIVELLO 7 — GROUNDING / RAG IBRIDO, IN DUE PARTI
  Adattamento dello stream-agent di NORTHSTREAM (danielesalpietro/
  NORTHSTREAM): FastAPI + Qdrant + Ollama, retrieval ibrido:
   - filtro esatto sui campi strutturati (feature, nominal, tolerance...)
   - similarità semantica solo sul resto del prompt
  Necessario perché "M6 tol.0.3" e "M8 tol.0.3" sono simili per un
  embedding testuale puro ma geometricamente diversi
  ADATTAMENTO RICHIESTO (non è riuso as-is, vedi Rischio #10): la
  sorgente consuma uno stream Kafka in continuo; qui il consumer loop va
  sostituito con un indexer batch/incrementale sul Livello 6 (dataset
  statico, non eventi CDC)

  L7-CONSULTIVO [FASE A, disponibile solo dopo bootstrap L6]
    Lookup "un caso quasi identico esiste già, con questi parametri"
    prima di generare. Utile anche col motore cloud — è di per sé una
    difesa contro le derive del cloud, cioè il bisogno dichiarato.
    Richiede che il Livello 6 abbia già contenuto: non disponibile
    dal giorno 1, solo dopo che il bootstrap retroattivo lo popola.

  L7-INTEGRATO [FASE B]
    Integrazione del lookup nel loop di generazione automatica locale
    -> il modello locale si àncora a precedenti reali, non genera a vuoto
        |
        v
LIVELLO 8 (futuro, opzionale) — FINE-TUNING  [FASE B]
  Solo quando il dataset congelato è abbastanza grande
  LoRA/QLoRA su Qwen2.5-Coder o simile, con questi dati come
  ground truth supervisionata
```

## Policy di retry (Livello 3 → Livello 2)

Il loop di correzione non può essere illimitato:

- **Budget massimo di tentativi** per singolo caso (es. 3–5), oltre il quale
  si esce dal loop automaticamente.
- **Strategia di variazione** tra un tentativo e l'altro (riformulazione del
  prompt, variazione di temperatura), non semplice ripetizione — un modello
  che fallisce una geometria tende a rifallirla identica.
- **Fallback esplicito e dichiarato** al superamento del budget, distinto
  per fase (in Fase A il motore è già il cloud, "escalation al cloud" non
  ha senso):
  - Fase A: fallback a intervento umano.
  - Fase B: escalation dal motore locale al cloud, oppure intervento umano.
  Nessun loop deve poter girare a vuoto silenziosamente.

## Note di design

- **Livelli 3 e 5 sono due arbitri di verità distinti, non intercambiabili.**
  Il Livello 3 è veloce/automatico ma simulato (geometria/parametri). Il
  Livello 5 è lento/manuale ma è l'unica fonte che cattura variabili reali
  (shrinkage del materiale, tolleranze macchina) che nessuna mesh o codice
  parametrico può prevedere da sola.
- **Il Livello 6 è deliberatamente a valle della validazione fisica**, non a
  monte. Un dato entra nel dataset di verità solo dopo essere stato misurato
  fisicamente, mai prima — evita di inquinare la base di conoscenza con
  output solo simulati e mai verificati.
- **Il Livello 7 (RAG) è ibrido per costruzione**, non solo semantico: senza
  filtro esatto sui parametri strutturati, il retrieval confonderebbe pezzi
  geometricamente diversi ma testualmente simili.
- **Il fine-tuning (Livello 8) è intenzionalmente ultimo e opzionale**:
  prematuro finché il dataset non è abbastanza grande da dare segnale di
  addestramento reale; il RAG da solo può bastare a lungo prima di arrivarci.

## Rischi e revisioni

1. **[Rischio #1, massima priorità] Il motore di generazione locale (L2,
   Fase B) è l'anello debole dell'intera architettura.** CAD-Recode e
   cadrille sono modelli 1.5–2B addestrati su dataset sintetici stile
   DeepCAD (primitive, estrusioni, fori semplici): filettature con passo e
   tolleranza specifici quasi certamente escono dal loro dominio di
   training. LLaMA-Mesh genera mesh low-poly di tipo artistico, inadatte a
   tolleranze di 0.3mm. Il metodo di Fabrizio funziona con GPT/Gemini,
   modelli enormemente più capaci — sostituirli con un modello locale
   piccolo è un'assunzione non verificata, non un dettaglio implementativo.
   **Prima di investire nella Fase B, va eseguito un test di fattibilità
   isolato**: stessi prompt già validati da Fabrizio in cloud, sottoposti al
   motore locale candidato, e confrontati contro il Livello 3.
   **[v4] Conferma esterna indipendente:** il paper Text-to-CadQuery (arXiv
   2505.06507, dataset di ~170k coppie testo→CadQuery, exact match top-1
   69.3% contro 58.8% del baseline, Chamfer Distance −48.6%) osserva
   miglioramenti consistenti scalando la dimensione del modello — lo stesso
   motivo per cui non si può assumere che un modello locale da 1.5–2B
   eguagli GPT/Gemini senza test. Non sostituisce il test di fattibilità
   sopra, ma ne rinforza la premessa con un dato esterno.
2. **Tensione tra bisogno dichiarato e soluzione originaria.** Il bisogno
   reale è la stabilità del workflow contro derive del modello cloud, non
   necessariamente l'autonomia da esso. La Fase A (L3+L6, cloud invariato)
   risolve già gran parte di questo bisogno da sola, a basso rischio e
   costo. La Fase B (L2 locale) persegue un obiettivo diverso — autonomia —
   più costoso e rischioso, e va tenuta esplicitamente disaccoppiata: un suo
   fallimento non deve compromettere la Fase A.
3. **Verifica parametrica preferita alla verifica su mesh, dove possibile —
   istanza del principio generale "l'IA scrive il programma, il computer
   esegue" (program synthesis).** Se il L2 produce codice CadQuery,
   diametri e passi sono parametri espliciti nel codice/B-rep: verificarli
   lì è esatto, verificarli ricostruendoli da una mesh triangolata è
   approssimato. È lo stesso principio per cui, nei casi di ragionamento
   matematico/logico, si fa scrivere all'IA il codice che calcola il
   risultato invece di chiederle il risultato direttamente: il calcolo
   viene delegato a un motore deterministico, l'IA resta un traduttore
   linguaggio-naturale → codice, non il decisore finale. La verifica di
   integrità mesh (manifold/watertight) resta comunque necessaria sempre,
   indipendentemente dalla via, perché serve allo slicer — non è quindi un
   fallback solo per la via LLaMA-Mesh, ma un controllo universale separato
   da quello dimensionale.
   **[v4] Aneddoto reale, non ipotetico:** un esempio di codice
   OpenSCAD/BOSL2 circolato per questo progetto usava una funzione
   `texture_thread()` per fori filettati, con un parametro `clearance=`.
   Nessuna delle due esiste in BOSL2 — la funzione reale è
   `screw_hole("M6x1", ...)`, e il parametro di vestibilità si chiama
   `tolerance=`, non `clearance=`. Nome plausibile ma inventato, su un
   tema tecnico specifico: è esattamente il tipo di errore silenzioso
   contro cui la verifica parametrica di questo Livello è progettata, e
   un promemoria che vale anche per il codice CadQuery/OpenSCAD generato
   dal Livello 2 stesso, non solo per esempi esterni.
4. **Variabili confondenti nel dataset congelato.** La misura fisica dipende
   da macchina, materiale/batch di filamento, non solo dai parametri di
   slicing. Un caso validato in PLA su una macchina non è ground truth
   trasferibile a PETG su un'altra — questi campi (più la data) vanno
   registrati esplicitamente nello schema del Livello 6, tenendo il set a
   ciò che verrà davvero misurato in pratica.
   **[v6]** Per lo stesso motivo, la temperatura ugello/piatto nel profilo
   PrusaSlicer del Livello 4 resta esplicitamente **non definita** finché
   materiale e marca del filamento non sono decisi — fissarla prima
   creerebbe un parametro congelato ma arbitrario, invece che derivato dal
   materiale realmente usato.
5. **[v2] Il Livello 2.5 è a sua volta un punto di fallimento silenzioso se
   automatizzato.** Tradurre linguaggio naturale in specifica strutturata è
   un compito interpretativo: se lo fa un LLM senza supervisione, un errore
   di normalizzazione produce un L3 che verifica correttamente contro una
   specifica sbagliata — PASS formalmente corretto, sostanzialmente falso,
   invisibile fino alla stampa. Mitigazione obbligatoria: conferma umana
   esplicita della specifica strutturata prima della generazione.
6. **[v2] Direzione della dipendenza tra fasi, corretta.** La Fase A è
   autosufficiente. La Fase B dipende dalla Fase A (usa L3 come metro di
   giudizio, usa L6 come contenuto per L7) — non il contrario. Non si parte
   con il motore locale prima di avere il verificatore.
7. **[v2, RISOLTO in v5] Ambiguità codice-vs-mesh dal cloud.** La modalità
   parametrica del L3 esiste solo se il cloud produce codice CAD, non STL
   diretta. Chiarito: il metodo attuale produce oggi STL diretta.
   Decisione presa (vedi Contesto e Livello 2): passaggio a doppia uscita
   Python → STEP + STL, che sblocca la verifica parametrica esatta invece
   di quella ricostruita da mesh.
8. **[v2] Bias di sopravvivenza nel bootstrap retroattivo.** Se Fabrizio ha
   conservato solo i casi funzionanti, il dataset iniziale sarà tutto PASS:
   inutile per testare che il verificatore sappia bocciare, e privo di
   segnale negativo per un eventuale fine-tuning futuro. Da qui in avanti i
   FAIL fisici vanno registrati con lo stesso rigore dei PASS, come regola
   operativa e non come nota facoltativa.
9. **[v3] Flowise non è un motore per step deterministici o con
   side-effect fisici.** Orchestra bene i nodi LLM-centrici (L1, L2.5, L2,
   L7-consultivo), ma L3 (verifica geometrica), L4 (slicing), L5 (misura
   fisica) e L6 (scrittura dataset) devono restare script esterni,
   richiamati da Flowise come Custom Tool/chiamata HTTP — non riscritti
   dentro un nodo Flowise. Riscriverli lì reintrodurrebbe la stessa
   incertezza che il Livello 3 esiste per eliminare.
10. **[v3] Lo stream-agent di NORTHSTREAM non è riusabile "as-is".** È
    scritto per consumare un flusso Kafka continuo (eventi CDC), non un
    dataset statico. Adottarlo per il Livello 7 richiede di sostituire il
    consumer loop con un indexer batch/incrementale sul Livello 6 —
    un adattamento del componente, non solo una configurazione diversa.
11. **[v10] Affidabilità intermittente di Flowise↔Ollama in
    `flowiseai/flowise:latest`.** Le chiamate del nodo ChatOllama falliscono
    a volte con `TypeError: fetch failed` dentro il pacchetto bundlato
    `@langchain/ollama/node_modules/ollama/dist/browser.cjs` — stesso
    pattern del bug ReActAgent (Rischio già osservato nello Stato attuale
    [v8]), probabile problema di bundling dell'immagine, non di rete o di
    configurazione (connettività diretta e `fetch` nativo di Node
    funzionano sempre; un riavvio del container non risolve). Non ancora
    isolato a una causa precisa: un primo test era riuscito con la stessa
    identica configurazione. Da monitorare prima di costruire flow più
    complessi su Flowise+Ollama — se persiste, valutare di pinnare una
    versione diversa di `flowiseai/flowise` o testare in parallelo con un
    provider cloud (GPT/Gemini) per isolare se il problema è specifico di
    Ollama.

## Bootstrap retroattivo del dataset (parallelo al primo prototipo)

Il Livello 3 non ha senso da testare a vuoto: i casi storici già validati da
Fabrizio (prompt, STL, parametri di slicing, misura al calibro) vanno
documentati in parallelo alla scrittura del verificatore. Servono a due
scopi contemporaneamente: sono il primo contenuto reale del Livello 6, e
sono i test case con cui verificare che il Livello 3 dia i risultati giusti
(PASS sui casi noti-funzionanti, FAIL sui casi noti-falliti, se disponibili).

## Analogia di riferimento (Mike OSS Legal)

| Mike OSS (legal) | Equivalente ingegneristico |
|---|---|
| CourtListener (verità giuridica) | Script di misura geometrica + dati fisici verificati (calibro) |
| Citazione di un caso | Riferimento a un prompt→STL→misura già validato |
| RAG su casi passati | RAG (pattern NORTHSTREAM) sul dataset congelato |
| Il modello non decide da solo se è vero | Il modello non decide da solo se la tolleranza è rispettata — lo decide la misura |

Differenza chiave: nel legal la verifica resta parzialmente interpretativa
(un umano legge comunque la sentenza citata); in questo dominio la
verifica può essere resa deterministica e automatica — uno script misura
la mesh generata e rifiuta l'output se fuori tolleranza, prima ancora dello
slicing.

**[v4] Convalida esterna indipendente.** Leo AI (`getleo.ai`) — prodotto
commerciale chiuso, nessun repository pubblico, **non un componente da
integrare** — arriva a una conclusione strutturalmente simile per una
strada indipendente: posiziona il proprio valore non nella generazione ma
nella validazione (accesso certificato SOC-2 a standard ingegneristici,
integrazione PDM/PLM, ricerca componenti geometry-aware su B-rep). È più
vicino ai nostri Livelli 3+7 che al Livello 2. Non cambia l'architettura,
ma è una seconda conferma indipendente — dopo l'analogia Mike OSS — che
"la generazione richiede una validazione separata e deterministica" non è
un'assunzione arbitraria di questo progetto.

## Componenti verificati (repository reali)

| Componente | Repository | Ruolo nell'architettura |
|---|---|---|
| CAD-Recode | `filaPro/cad-recode` | Livello 2 — generazione codice CAD parametrico |
| LLaMA-Mesh | `nv-tlabs/LLaMA-Mesh` | Livello 2 — generazione mesh diretta |
| Zoo Text-to-CAD (KittyCAD) | `KittyCAD/kittycad.py` (API client; servizio cloud proprietario) | Livello 2 — **[v4] candidato Fase A aggiuntivo**, non un sostituto validato di GPT/Gemini. Genera B-Rep/STEP nativamente (non mesh), a differenza dei modelli text-to-3D generici — rilevante perché rende irrilevante la domanda aperta "codice o mesh?" per questo candidato. **Non testato** su feature filettate tolleranziate: richiede un proprio test di fattibilità, distinto da quello del Rischio #1 (che riguarda i candidati locali, non quelli cloud) |
| Text-to-CadQuery (dataset) | `Text-to-CadQuery/Text-to-CadQuery` | Livello 8 — **[v4] candidato dataset per fine-tuning**: ~170k coppie testo→CadQuery (estensione di Text2CAD), top-1 exact match 69.3% (da 58.8%), Chamfer Distance −48.6%. Formato nativamente CadQuery, coerente col Rischio #3. **Non sostituisce il Livello 6**: non è dataset fisicamente validato, resta un candidato per il pre-training/base model, non per il ground truth |
| stream-agent (Qdrant + Ollama) | adottato da NORTHSTREAM (`danielesalpietro/NORTHSTREAM`) | Livello 7 — grounding/RAG ibrido; **[v3] decisione presa**, da riadattare da stream Kafka a dataset statico (vedi Rischio #10) |
| Open WebUI | riusato da NORTHSTREAM (`danielesalpietro/NORTHSTREAM`) | Interfaccia web — chat per interrogare il Livello 7 |
| Landing/dashboard page | riadattata da NORTHSTREAM (`danielesalpietro/NORTHSTREAM`) | Interfaccia web — entry point statico verso lo stack Docker |
| Flowise | motore di esecuzione scelto — **[v3] decisione presa** | L1, L2.5, L2, L7-consultivo — orchestrazione dei nodi LLM-centrici, non dei livelli deterministici (L3-L6, vedi Rischio #9) |
| PrusaSlicer CLI | pattern di riferimento: `Billa05/prusaslicer-cli-docker` | Livello 4 — **[v6] decisione presa**: CLI headless containerizzata, profilo `.ini` versionato nel repository. Parametri di default (layer height, perimetri) fissati; temperatura ugello/piatto esplicitamente in sospeso, dipende dal materiale (vedi Rischio #4) |
| RecursiveMAS | `RecursiveMAS/RecursiveMAS` | Ipotesi d'uso esplicita (non impegnativa): se in futuro il Livello 2 evolvesse in un processo multi-step (un agente genera, uno verifica, uno corregge), potrebbe orchestrare quel loop invece di scriverlo ad-hoc. Nessun ruolo nell'architettura attuale — voce speculativa, non un componente pianificato. **[v3]** La sua web UI (Gradio/HOUSE) è stata valutata e scartata a favore di Open WebUI per l'interfaccia di CALIPER |

## Stato attuale (cosa esiste davvero)

- [x] Metodo manuale funzionante (Fabrizio) — non ancora documentato in
      forma strutturata/dataset
- [x] Candidati per Livello 2 (Fase B) identificati e verificati come reali,
      ma **non testati sul caso d'uso specifico** (vedi Rischio #1)
- [x] Componente di grounding (Livello 7) identificato in modo concreto:
      stream-agent di NORTHSTREAM (Qdrant + Ollama), da riadattare da
      stream Kafka a dataset statico (vedi Rischio #10)
- [x] Concetto del verificatore (Livello 3) definito, ora a doppia modalità
- [x] **[v3]** Motore di esecuzione deciso: Flowise, scoped ai nodi
      LLM-centrici (L1, L2.5, L2, L7-consultivo)
- [x] **[v3]** Interfaccia web decisa: Open WebUI (chat) + landing/
      dashboard page, entrambe riadattate da NORTHSTREAM
- [x] **[v5]** Risolto: il metodo produceva STL diretta — deciso il
      passaggio a doppio output Python -> STEP + STL (vedi Livello 2 e
      Rischio #7)
- [x] **[v6]** Slicer del Livello 4 deciso: PrusaSlicer CLI headless,
      containerizzato (pattern `Billa05/prusaslicer-cli-docker`)
- [x] **[v7]** Scaffold Docker scritto: `docker-compose.yml` con qdrant,
      ollama (riserva GPU), flowise, stream-agent (build locale), open-webui,
      landing-page, prusaslicer (profilo `tools`, on-demand). Reti isolate
      dove non serve comunicazione (`caliper-public` per la landing page,
      `network_mode: none` per prusaslicer), condivisa dove serve
      (`caliper-ai` per qdrant/ollama/flowise/stream-agent/open-webui).
      Validato con `docker compose config`.
- [x] **[v8]** Primo `docker compose up` reale eseguito con successo:
      GPU rilevata correttamente da Ollama (RTX 5080, CUDA 12.0, 15.9GiB —
      conferma la stima di capacity), qdrant/stream-agent/landing-page/
      open-webui su e raggiungibili, Flowise usato end-to-end (account
      registrato, login). Bug non bloccante rilevato e documentato:
      `flowiseai/flowise:latest` fallisce a caricare i nodi
      `ReActAgentChat`/`ReActAgentLLM` per un mismatch di versione in
      `@langchain/core` (bug upstream, non della nostra config) — non
      servono per L1/L2.5/L2/L7-consultivo, nessuna azione necessaria.
      Corretto uno spreco reale: Open WebUI scaricava un secondo stack di
      embedding da HuggingFace per il proprio RAG interno (non usato, il
      grounding vero è stream-agent+Qdrant) — ora punta a Ollama via
      `RAG_EMBEDDING_ENGINE=ollama`, riusando `granite-embedding:30m`
- [x] **[v7]** Adattamento dello stream-agent da consumer Kafka a indexer
      del Livello 6 — **scritto**, prima versione: legge periodicamente
      `DATASET_DIR` da disco e indicizza in Qdrant. Retrieval solo
      semantico per ora — il filtro esatto sui campi strutturati resta
      **TODO**, dipende dallo schema L2.5 (vedi Rischio #10)
- [x] **[v7]** Profilo PrusaSlicer scritto: `config/prusaslicer/caliper-pla.ini`
      (layer height 0.2mm, 3 perimetri) — temperature ugello/piatto
      esplicitamente assenti dal file, in attesa della decisione sul
      materiale (vedi Rischio #4)
- [x] **[v7]** `.env.example` generato — supporta OpenAI/GPT (anche via
      endpoint locale tipo LM Studio), Gemini, Claude, Ollama
- [x] **[v9]** Primo Chatflow Flowise costruito e testato: "CALIPER - L2.5
      Specification Normalization" (Prompt Template → Ollama/granite4:1b →
      LLM Chain → Structured Output Parser). Schema JSON prima versione:
      `feature` (string), `nominal` (string), `pitch` (number), `tolerance`
      (number), `tolerance_type` (string), `measured_as` (string). Test
      reale su "foro filettato M6, tolleranza 0.3mm, passo 1.0" →
      `tolerance_type` e `measured_as` correttamente lasciati vuoti
      (comportamento "non indovinare" del Rischio #5 verificato in pratica,
      non solo teorizzato).
      **[v10] Corretto**: `feature` estraeva il termine letterale dal
      prompt ("filettato") invece di normalizzarlo al vocabolario canonico
      ("thread") — verificato che `granite4:1b` supporta l'italiano
      nativamente (12 lingue), quindi non era un limite del modello ma
      un'istruzione mancante nel prompt. Aggiunto vincolo esplicito
      (enum: thread/press_fit/snap_fit/hole/boss/other + istruzione di
      normalizzazione linguistica) al Prompt Template, salvato.
      **Non riverificato end-to-end**: dopo la modifica, le chiamate al
      Chatflow falliscono in modo intermittente con `TypeError: fetch
      failed` all'interno di `@langchain/ollama/node_modules/ollama/dist/
      browser.cjs` (stesso pattern di bundling difettoso già osservato sul
      bug ReActAgent — probabile problema upstream nell'immagine
      `flowiseai/flowise:latest`, non della nostra configurazione).
      Escluso: connettività di rete Flowise→Ollama (`wget` funziona),
      `fetch` nativo di Node da dentro il container (funziona, sia
      streaming sia no), riavvio del container Flowise (non risolve). Il
      primo test (prima della modifica) era riuscito con la stessa identica
      configurazione — la modifica al prompt non è la causa più probabile.
      Da tenere presente come rischio di affidabilità di Flowise+Ollama in
      questa versione dell'immagine, non ancora una causa isolata.
- [x] **[v11]** Provisioning dei chatflow versionato: il Chatflow L2.5
      esportato dalla UI e salvato in
      `services/flowise/chatflows/l25-specification-normalization.json`
      (+ `manifest.json` per gli import futuri), con uno script idempotente
      (`services/flowise/import_chatflows.py`) e un servizio dedicato nel
      compose (`flowise-init`) che lo importa via API usando una API key
      generata dall'utente — non un token di sessione, per non gestire
      credenziali applicative all'interno del provisioning stesso.
      Documentato in [`README.md`](../README.md), sezione 9 (Installation).
      Nota di processo: l'estrazione automatica del chatflow via browser
      non è riuscita in modo affidabile (interazioni UI intermittenti) —
      esportato manualmente dall'utente dalla UI di Flowise invece.
- [ ] Formato preset (feature ricorrenti pre-configurate) — **non ancora
      definito**, dipende dallo schema del L2.5
- [ ] **[v7]** Path di mount attesi dall'immagine `billa05/prusacli` —
      **non verificati direttamente** (solo dedotti dalla descrizione del
      repository), da confermare prima del primo uso reale
- [ ] Meccanismo di conferma umana della specifica L2.5 — **non ancora
      progettato** (obbligatorio se il L2.5 è automatizzato via LLM,
      vedi Rischio #5)
- [ ] **[v5]** Doppio passaggio del Livello 2 (Python -> STEP + STL) —
      **non ancora implementato**, decisione presa ma codice non scritto
- [ ] Codice del verificatore geometrico (Livello 3) — **non ancora scritto**
- [ ] Dataset congelato (Livello 6), con campi macchina/materiale/data e
      confronto con modello CAD di riferimento — **non ancora
      estratto/strutturato**
- [ ] **[v5]** Modelli CAD di riferimento (disegnati in modo convenzionale)
      per i casi già validati fisicamente — **non ancora fatto**,
      necessario per il campo diagnostico del Livello 6
- [ ] Test di fattibilità del motore locale (Rischio #1) — **non eseguito**
- [ ] Integrazione end-to-end tra i componenti — **mai testata insieme**

## Prossimi passi proposti (Fase A, in parallelo)

0. ~~Chiarire con Fabrizio: il cloud produce codice o STL diretta?~~
   **[v5] RISOLTO** — vedi Contesto e Rischio #7: produce STL diretta,
   deciso il passaggio a doppio output Python → STEP + STL.
1. Definire lo schema JSON della specifica strutturata (Livello 2.5), con
   il meccanismo di conferma umana obbligatorio (vedi Rischio #5).
2. Prototipare il Livello 3 in isolamento (verifica mesh universale +
   verifica parametrica dove applicabile), testabile subito su STL
   qualsiasi, indipendente dai dati riservati di Fabrizio.
3. **[v5]** Implementare il doppio passaggio del Livello 2 (Python →
   STEP + STL) e verificare che i due output derivino coerentemente dalla
   stessa sorgente parametrica prima di integrarlo nel resto della
   pipeline.
4. In parallelo: bootstrap retroattivo — documentare i casi storici già
   validati da Fabrizio, che diventano sia il primo contenuto del Livello 6
   sia i test case del Livello 3. **[v5]** Dove possibile, accompagnarli
   con un modello CAD di riferimento costruito in modo convenzionale per
   lo stesso pezzo, per popolare fin da subito il campo diagnostico del
   Livello 6.
5. ~~Scaffold Docker del motore di esecuzione e dell'interfaccia web~~
   **[v7] FATTO** — `docker-compose.yml`, `services/stream-agent/`
   (adattamento scritto), `services/landing/dashboard.html`,
   `config/prusaslicer/caliper-pla.ini`, `.env.example`. Validato solo
   con `docker compose config` (sintassi/risoluzione variabili) — **non
   ancora eseguito per davvero**: resta vuoto/non testabile end-to-end
   finché L2.5 e L3 (passi 1-2) non esistono da collegare.
6. ~~Scrivere il profilo `.ini` iniziale di PrusaSlicer~~ **[v6/v7]
   FATTO** — vedi `config/prusaslicer/caliper-pla.ini`. Temperatura
   ugello/piatto resta esplicitamente da definire finché la domanda su
   materiale/marca filamento non è risolta (vedi Rischio #4) — non
   bloccante per lo scaffold, bloccante per il primo caso reale.
7. **[v7]** Verificare i path di mount reali attesi dall'immagine
   `billa05/prusacli` (non ispezionata direttamente, solo dedotta dalla
   descrizione del repository) prima di eseguire il servizio
   `prusaslicer` per la prima volta.
8. **[v7]** Primo avvio reale dello stack (`docker compose up`, GPU
   NVIDIA richiesta per `ollama` — verificata disponibile: RTX 5080
   16GB) e verifica che i modelli Granite si scarichino e rispondano.

La Fase B (motore locale, L7-integrato, fine-tuning) resta condizionata
all'esito del test di fattibilità del Rischio #1, e dipende dal
completamento della Fase A, non il contrario (vedi "Fasatura del progetto"
in apertura per il dettaglio della direzione). La Fase A è autosufficiente
e utile da sola anche se la Fase B non venisse mai avviata.
