# Logbook — M2: Controlli geometrici deterministici, validati su geometrie note

Vedi [`logbook.md`](logbook.md) per il quadro generale. Dipende da M1
(protocollo calibri + modulo di interferenza) — vedi
[`logbook_fase1.md`](logbook_fase1.md).

## Obiettivo (rivisto rispetto alla proposta originale)

Proposta originale: script interni al motore fisico per collisioni
cinematiche complesse su TC1 e TC2, con forza/momento torcente applicati;
milestone: batch dei tre test case su geometrie note per validare la
precisione millimetrica del motore.

Adottato: stesso obiettivo di validazione, implementazione senza motore
fisico — per ciascun test case, il controllo diventa una query geometrica
esatta invece di una simulazione dinamica.

## TC1 — Accoppiamento albero-mozzo (clearance fit)

**Proposta originale:** spingere un perno nel foro con forza definita
lungo Z, rilevare compenetrazione dei collider prima di fine corsa.

**Rivisto:** interferenza statica **e** su sweep (perno spostato lungo Z
in step discreti, o volume spazzato calcolato analiticamente) contro un
calibro pin GO (diametro minimo) e uno NO-GO (diametro massimo). Un
clearance fit per definizione non deve avere interferenza in nessuna
posizione lungo la corsa — non serve applicare una forza: se serve forza
per inserirlo, non è più un clearance fit, è già un fallimento rilevabile
da interferenza statica. **Nota per il futuro:** un vero test di press-fit
(interferenza intenzionale, deformazione elastica, forza di inserimento)
è un problema diverso — richiede un solver dedicato (FEA), da tenere
esplicitamente fuori da questo test case finché non è il suo turno.

## TC2 — Filettatura metrica ISO

**Proposta originale:** simulazione di avvitamento con momento torcente
applicato; fallimento se la geometria si incastra o presenta sovrapposizioni
poligonali.

**Rivisto:** calibro ad anello GO/NO-GO filettato, sweep lungo il percorso
elicoidale reale (stesso passo/angolo del preset `thread` in
`presets.json`, ISO 68-1, 60°) — è la stessa identica metodologia di
collaudo filettature nella metrologia reale (calibri Go/No-Go a vite), non
un'invenzione. Il calibro GO deve poter percorrere l'intera elica senza
interferenza; il calibro NO-GO deve interferire. Elimina la sensibilità
a timestep/damping di un solver fisico iterativo, che su un'elica sottile
può dare sia falsi blocchi (jamming numerico) sia falsi pass (tunneling
attraverso una cresta sottile a step temporali larghi).

## TC3 — Giunto a scatto (snap-fit)

**Proposta originale:** array di raycast dalla base per misurare spessore
del dente di ritenzione e gap, confrontati con le quote teoriche.

**Rivisto:** stessa idea concettuale ("calibro virtuale" posizionato in
punti di misura noti), implementata con `BRepExtrema_DistShapeShape`
(distanza minima esatta tra facce/edge B-Rep) invece di raycast su mesh in
un motore fisico. I punti di misura (facce/edge di riferimento) vanno
dichiarati nella specifica L2.5 — è un'estensione dello schema, non solo
del codice del verificatore.

## Timeout e isolamento computazionale (sweep/boolean OCC)

Punto sollevato esplicitamente prima di scrivere codice: lo sweep
elicoidale per TC2 (e le interferenze statiche per TC1/TC3) sono
operazioni booleane OCC (`BOPAlgo`/`BRepAlgoAPI_Common`), un punto debole
noto su geometrie quasi-degeneri o quasi-tangenti — esattamente il tipo di
difetto che un'elica mal costruita da un LLM può produrre (vedi `[v14]`).
Possono diventare molto lente senza essere un loop infinito in senso
stretto.

**Stato attuale (verificato nel codice, non assunto):**
`services/verifier/executor/watcher.py` ha già un timeout wall-clock
esterno (`subprocess.run(..., timeout=15)`, SIGKILL incondizionato) e
`run_and_measure.py` ha già un `RLIMIT_CPU` auto-imposto (10s, SIGXCPU) —
due livelli, uno interno che si autolimita, uno esterno che non si fida
del primo. Corretto come pattern, ma **il budget è tarato per
`exec(code)` + bbox, condiviso con qualunque nuovo controllo aggiunto
nello stesso processo/subprocess.**

**Decisione:** il gauge-check (interferenza/sweep/distanza minima) va
lanciato come **subprocess separato**, con timeout proprio e
indipendente da quello di `exec(code)`:
- mantiene stretto il budget per codice non fidato (non concede più
  tempo a un exec() malevolo solo perché serve più tempo alla geometria);
- permette un budget diverso (probabilmente più ampio) per un'operazione
  OCC nota per essere più pesante ma eseguita su un solido già passato il
  check di validità/manifold;
- produce un `error` distinguibile (`gauge_check_timeout` vs
  `execution_timeout`) — diagnosticamente utile nel tempo per separare
  "codice generato male" da "geometria valida ma sweep pesante".

**Il numero non è scelto a intuito.** Va misurato empiricamente durante
il batch di M2 sulle geometrie di controllo (worst-case osservato ×
margine) — stesso metodo già usato per il bug OpenBLAS/RLIMIT_AS
(trovato per misura diretta, non per assunzione, vedi `[v14]`).

### Formato del log su TIMEOUT del gauge-check

**Correzione di percorso:** un TIMEOUT (o un FAIL) del gauge-check non
raggiunge mai il Livello 4 (slicing) — quello vede solo PASS. Rientra
nella "Policy di retry (Livello 3 → Livello 2)" già definita
nell'architettura (budget massimo di tentativi, variazione tra un
tentativo e l'altro, fallback dichiarato al superamento del budget), e
finisce nel log virtuale separato di M4 (`source: virtual`), mai nel
Livello 6.

**Vincolo tecnico, non solo di stile:** un `SIGKILL` non lascia nulla da
ispezionare a posteriori — nessun handler, nessuno stack trace del punto
in cui l'algoritmo OCC era bloccato. Qualsiasi contesto allegato al log
deve quindi venire da ciò che si sapeva **prima** di lanciare
l'operazione, mai dall'osservazione dell'hang:

1. spec strutturata in ingresso (pitch, nominale, tolleranza, feature,
   calibro usato) — è l'input del job, non qualcosa recuperato post-mortem;
2. diagnostica pre-flight economica calcolata **prima** del boolean
   pesante (conteggio facce/edge, `BRepCheck_Analyzer`, tolleranza massima
   per-entità del B-Rep — un valore anomalo qui è spesso il segnale
   precoce di geometria quasi-degenere);
3. checkpoint di avanzamento, se lo sweep elicoidale è discretizzato in N
   step invece di un'unica chiamata booleana opaca (coerente con "step
   discreti" già proposto sopra per TC1/TC2) — ogni step scrive su file
   prima di essere tentato, così il watcher può leggere l'ultimo
   checkpoint anche se il processo muore senza preavviso.

**Esplicitamente escluso:** nessuna spiegazione causale generata da un
LLM sul perché si è bloccato — reintrodurrebbe l'incertezza LLM-as-judge
che il Livello 3 esiste per eliminare. Il log resta dati strutturati, non
prosa — coerente con la nota già in architettura sul Livello 7 ("senza
filtro esatto sui parametri strutturati, il retrieval confonderebbe pezzi
geometricamente diversi ma testualmente simili").

Formato indicativo, coerente con `dimensional_check` già presente in
`run_and_measure.py`:

```json
{
  "execution": "FAIL",
  "error": "gauge_check_timeout",
  "gauge_check": {
    "status": "TIMEOUT",
    "gauge_used": "thread_M6_GO_ISO68-1.step",
    "timeout_seconds": 42,
    "input_spec": {
      "feature": "thread", "nominal": "M6", "pitch": 1.0,
      "tolerance": 0.3, "profile_angle_deg": 60,
      "tolerance_type": "diametrale"
    },
    "preflight_diagnostics": {
      "face_count": 812, "edge_count": 2440,
      "topology_check": "ok",
      "max_entity_tolerance_mm": 0.0134
    },
    "last_checkpoint": {"step": 14, "total_steps": 40, "helix_position_deg": 126.0},
    "source": "virtual"
  }
}
```

`source: virtual` non è opzionale — è il discriminatore imposto dalla
decisione già presa in M4 (vedi
[`logbook_fase4.md`](logbook_fase4.md)): questi record vanno nel log
virtuale separato, e un pattern di soli TIMEOUT virtuali non basta da
solo a escludere una strategia dal retrieval senza un riscontro fisico
(Livello 5) — un bug del checker (già successo, `[v14]`) non deve
diventare un pregiudizio permanente e auto-confermato.

## Milestone (criterio di accettazione, mantenuto)

Esecuzione batch automatica dei tre test case su geometrie **note,
disegnate convenzionalmente, non generate da un LLM** — stesso principio
già usato per il Livello 3 esistente ("verificato sui due output reali del
Chatflow L2", v12). Serve prima dimostrare che il verificatore giudica
correttamente casi di controllo, prima di fidarsi che giudichi bene output
AI.

**Aggiunta:** i modelli CAD di riferimento per TC1–TC3 sono lo stesso
lavoro già segnato come TODO aperto per il Livello 6 ("Modelli CAD di
riferimento... non ancora fatto", nell'architettura) — costruirli una
volta e riusarli per entrambi gli scopi, non duplicare il lavoro.

## Stato

- [ ] Gauge-check separato dall'esecuzione del codice in un subprocess
      indipendente, con timeout proprio (`gauge_check_timeout` distinto
      da `execution_timeout`)
- [ ] Timeout del gauge-check calibrato empiricamente sul worst-case
      osservato durante il batch, non stimato a priori
- [ ] TC1: calibri pin GO/NO-GO modellati, controllo interferenza
      statico+sweep implementato e verificato su geometria nota
- [ ] TC2: calibro ad anello GO/NO-GO filettato modellato, sweep elicoidale
      implementato e verificato su geometria nota
- [ ] TC3: punti di misura dichiarati nello schema L2.5, controllo distanza
      minima implementato e verificato su geometria nota
- [ ] Batch dei tre TC eseguito e documentato con risultati numerici
- [ ] Modelli CAD di riferimento riusati per popolare il campo diagnostico
      del Livello 6 (collegamento esplicito col bootstrap retroattivo)
