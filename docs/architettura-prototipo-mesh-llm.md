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

**Domanda aperta, da chiarire con Fabrizio prima di scrivere il Livello 3:**
il metodo attuale ottiene dal cloud codice parametrico (es. OpenSCAD/
CadQuery) oppure STL/mesh diretta? Determina se la modalità parametrica del
L3 (vedi sotto) è disponibile fin da subito in Fase A. Se oggi il flusso
produce mesh diretta, il singolo cambiamento a più alto rendimento nella
Fase A è probabilmente chiedere al cloud codice CadQuery/OpenSCAD invece di
STL — sblocca la verifica esatta invece di quella ricostruita da mesh, che
per le filettature è la parte più difficile.

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
  Fase A: GPT/Gemini (invariato, metodo già validato da Fabrizio)
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
  Output: codice CadQuery/Python -> STL, oppure mesh diretta
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
  Slicer (parametri fissi e congelati, versionati)
  Stampa fisica
        |
        v
LIVELLO 5 — VALIDAZIONE FISICA (verità ultima)  [FASE A]
  Misura reale (calibro), test funzionale (si avvita?)
  Dato non sostituibile da alcun simulatore
        |
        v
LIVELLO 6 — DATASET CONGELATO (ground truth)  [FASE A]
  Ogni caso: {prompt, specifica strutturata L2.5, STL,
              parametri slicing, macchina, materiale/batch filamento,
              data, misura fisica, esito PASS/FAIL}
  Campi minimi realmente misurabili — niente temperatura/umidità
  promesse ma mai registrate nella pratica
  Versionato, immutabile una volta validato
  REGOLA OPERATIVA: da ora in poi, ogni FAIL fisico va registrato con
  la stessa cura di ogni PASS — non solo "se disponibili" (vedi nota
  sul bias di sopravvivenza più sotto)
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
4. **Variabili confondenti nel dataset congelato.** La misura fisica dipende
   da macchina, materiale/batch di filamento, non solo dai parametri di
   slicing. Un caso validato in PLA su una macchina non è ground truth
   trasferibile a PETG su un'altra — questi campi (più la data) vanno
   registrati esplicitamente nello schema del Livello 6, tenendo il set a
   ciò che verrà davvero misurato in pratica.
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
7. **[v2] Ambiguità codice-vs-mesh dal cloud, da chiarire con Fabrizio.** La
   modalità parametrica del L3 esiste solo se il cloud produce codice CAD,
   non STL diretta. Va verificato cosa produce oggi il metodo attuale prima
   di scrivere il Livello 3 — determina quale modalità sviluppare per prima.
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

## Componenti verificati (repository reali)

| Componente | Repository | Ruolo nell'architettura |
|---|---|---|
| CAD-Recode | `filaPro/cad-recode` | Livello 2 — generazione codice CAD parametrico |
| LLaMA-Mesh | `nv-tlabs/LLaMA-Mesh` | Livello 2 — generazione mesh diretta |
| stream-agent (Qdrant + Ollama) | adottato da NORTHSTREAM (`danielesalpietro/NORTHSTREAM`) | Livello 7 — grounding/RAG ibrido; **[v3] decisione presa**, da riadattare da stream Kafka a dataset statico (vedi Rischio #10) |
| Open WebUI | riusato da NORTHSTREAM (`danielesalpietro/NORTHSTREAM`) | Interfaccia web — chat per interrogare il Livello 7 |
| Landing/dashboard page | riadattata da NORTHSTREAM (`danielesalpietro/NORTHSTREAM`) | Interfaccia web — entry point statico verso lo stack Docker |
| Flowise | motore di esecuzione scelto — **[v3] decisione presa** | L1, L2.5, L2, L7-consultivo — orchestrazione dei nodi LLM-centrici, non dei livelli deterministici (L3-L6, vedi Rischio #9) |
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
      LLM-centrici (L1, L2.5, L2, L7-consultivo) — non ancora installato
      né configurato
- [x] **[v3]** Interfaccia web decisa: Open WebUI (chat) + landing/
      dashboard page, entrambe riadattate da NORTHSTREAM — non ancora
      integrate
- [ ] Schema di specifica strutturata (Livello 2.5) — **non ancora definito**
- [ ] Formato preset (feature ricorrenti pre-configurate) — **non ancora
      definito**, dipende dallo schema del L2.5
- [ ] Scaffold Docker (Flowise + stream-agent adattato + Qdrant + Open
      WebUI + landing page) — **non ancora scritto**
- [ ] Adattamento dello stream-agent da consumer Kafka a indexer del
      Livello 6 — **non ancora scritto** (vedi Rischio #10)
- [ ] Meccanismo di conferma umana della specifica L2.5 — **non ancora
      progettato** (obbligatorio se il L2.5 è automatizzato via LLM,
      vedi Rischio #5)
- [ ] Chiarito con Fabrizio: il cloud produce oggi codice CAD o STL/mesh
      diretta? — **domanda aperta, condiziona l'ordine di sviluppo del L3**
- [ ] Codice del verificatore geometrico (Livello 3) — **non ancora scritto**
- [ ] Dataset congelato (Livello 6), con campi macchina/materiale/data
      — **non ancora estratto/strutturato**
- [ ] Test di fattibilità del motore locale (Rischio #1) — **non eseguito**
- [ ] Integrazione end-to-end tra i componenti — **mai testata insieme**

## Prossimi passi proposti (Fase A, in parallelo)

0. **Chiarire con Fabrizio**: il metodo attuale ottiene dal cloud codice
   parametrico (OpenSCAD/CadQuery) o STL/mesh diretta? (vedi Rischio #7).
   Determina quale modalità del Livello 3 sviluppare per prima — è un
   blocco, non una nota a margine.
1. Definire lo schema JSON della specifica strutturata (Livello 2.5), con
   il meccanismo di conferma umana obbligatorio (vedi Rischio #5).
2. Prototipare il Livello 3 in isolamento (verifica mesh universale +
   verifica parametrica dove applicabile), testabile subito su STL
   qualsiasi, indipendente dai dati riservati di Fabrizio.
3. In parallelo: bootstrap retroattivo — documentare i casi storici già
   validati da Fabrizio, che diventano sia il primo contenuto del Livello 6
   sia i test case del Livello 3.
4. **[v3]** Scaffold Docker del motore di esecuzione e dell'interfaccia
   web decisi sopra: Flowise, stream-agent di NORTHSTREAM adattato al
   Livello 6 (Qdrant + Ollama), Open WebUI, landing/dashboard page. Passo
   infrastrutturale, indipendente dal contenuto dei passi 0-3 — può
   partire in parallelo, ma resta vuoto/non testabile finché L2.5 e L3
   (passi 1-2) non esistono da collegare.

La Fase B (motore locale, L7-integrato, fine-tuning) resta condizionata
all'esito del test di fattibilità del Rischio #1, e dipende dal
completamento della Fase A, non il contrario (vedi "Fasatura del progetto"
in apertura per il dettaglio della direzione). La Fase A è autosufficiente
e utile da sola anche se la Fase B non venisse mai avviata.
