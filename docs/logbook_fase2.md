# Logbook — M2: Controlli geometrici deterministici, validati su geometrie note

Vedi [`logbook.md`](logbook.md) per il quadro generale. Dipende da M1
(protocollo calibri + modulo di interferenza) — vedi
[`logbook_fase1.md`](logbook_fase1.md), raggiunta e verificata
indipendentemente (branch `claude/handoff-m1-docs-bm01i6`, non ancora
mergiato in `develop`).

Prompt di handoff pronto per la sessione che implementa questa milestone:
[`handoff_m2.md`](handoff_m2.md).

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

**Rivisto:** calibro filettato GO/NO-GO, sweep lungo il percorso
elicoidale reale (stesso passo/angolo del preset `thread` in
`presets.json`, ISO 68-1, 60°) — è la stessa identica metodologia di
collaudo filettature nella metrologia reale, non un'invenzione. Il
calibro GO deve poter percorrere l'intera elica senza interferenza; il
calibro NO-GO deve interferire. Elimina la sensibilità a timestep/damping
di un solver fisico iterativo, che su un'elica sottile può dare sia falsi
blocchi (jamming numerico) sia falsi pass (tunneling attraverso una
cresta sottile a step temporali larghi).

**Correzione (post-M1):** qui sopra si parlava genericamente di "calibro
ad anello" — impreciso. L'esempio L2.5 già in architettura è "**foro**
filettato M6", cioè una filettatura interna: il calibro corretto è un
**tampone filettato esterno** (thread plug gauge), non un anello — è
quello effettivamente modellato in M1
(`config/gauges/thread_M6_GO_ISO68-1.step` /
`..._NOGO_ISO68-1.step`, Ø5.7/6.3mm). Un calibro ad anello servirebbe
solo per verificare una filettatura *esterna* generata dalla pipeline,
caso non ancora previsto da nessun preset — vedi
`config/gauges/README.md`.

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

### Come il checkpoint arriva al Livello 2 in retry, senza farlo "spiegare"

Il rischio: se i numeri grezzi del checkpoint (step, face count, tolleranza)
finiscono nel prompt di retry così come sono, il modello li interpreta
liberamente — stesso pattern già visto di spiegazioni plausibili ma
inventate (`texture_thread()`/`clearance=`, Rischio #3). Per evitarlo, il
checkpoint non passa mai al prompt come dato grezzo: viene prima ridotto a
un **enum fisso**, da una funzione a soglie deterministica (codice, non
LLM):

```
step/total_steps < 0.33            → SWEEP_TIMEOUT_EARLY
step/total_steps >= 0.33           → SWEEP_TIMEOUT_LATE
max_entity_tolerance_mm > soglia   → TOPOLOGY_TOLERANCE_ANOMALY
nessuna soglia superata            → RETRY_GENERIC (nessun hint specifico)
```

A ciascun codice corrisponde **un solo enunciato canned, scritto da un
umano** — mai composto dal modello, mai i numeri grezzi:

```json
"retry_context": {
  "attempt": 2,
  "previous_error": "gauge_check_timeout",
  "directive": "SWEEP_TIMEOUT_EARLY",
  "directive_text": "Il tentativo precedente ha superato il tempo massimo nella prima parte del percorso elicoidale. Riduci la complessita' del profilo (numero di segmenti) o il numero di giri modellati."
}
```

Solo `directive_text` entra nel prompt di L2, come parte della "strategia
di variazione" già richiesta dalla Policy di retry esistente. I numeri
grezzi restano fuori dal prompt e vanno invece a L7, dove servono da
filtro esatto su parametri strutturati (coerente con la nota già in
architettura sul retrieval ibrido), non da testo su cui far ragionare il
modello.

**Se nessuna soglia scatta con sicurezza → `RETRY_GENERIC`**, nessun hint
tecnico specifico, si torna alla sola variazione prompt/temperatura già
prevista — meglio nessun hint che uno sbagliato, stessa disciplina già
usata in `presets.json` (`defined: false` invece di inventare).

**Riserva onesta:** l'enum e i suoi enunciati sono un'ipotesi non
validata ("timeout in fase iniziale = profilo troppo complesso" è
plausibile, non dimostrato). Vanno tracciati insieme all'esito del retry
successivo (PASS/FAIL) nel log virtuale, per validarli o correggerli con
misura reale — stesso principio già applicato al bug OpenBLAS e al bug
dimensionale `[v14]`, non per assunzione.

**Nota di implementazione:** l'orchestratore (`generate_and_verify.py`)
non ha ancora un retry automatico (v13: "Nessun retry automatico ancora").
Questo contratto va costruito insieme a quel meccanismo, non prima —
vedi anche [`logbook_fase3.md`](logbook_fase3.md) per la direzione a
lungo termine: con lo schema sketch-first di M3, la stessa
classificazione potrà clampare direttamente un campo numerico dei
vincoli invece di passare per un'istruzione testuale.

### Limite massimo di tentativi (L3 → L2)

La Policy di retry esistente in architettura lo cita solo come esempio
("budget massimo di tentativi per singolo caso, es. 3–5"), mai fissato
né implementato. **Deciso qui: 3 tentativi**, con un'uscita anticipata —
se lo stesso `directive`/errore si ripete su 2 tentativi consecutivi
nonostante la variazione (hint + temperatura/riformulazione già
obbligatoria), si esce dal loop prima di consumare il terzo: continuare
con la stessa classificazione che ha già fallito una volta è spreco di
calcolo, non persistenza utile.

Al superamento del budget (o all'uscita anticipata), fallback già
previsto per la Fase A: intervento umano. Il caso finisce nel log
virtuale con `final_status: unrecoverable_virtual`, sempre
`source: virtual` — non entra nel Livello 6 a meno che non venga poi
davvero verificato fisicamente.

### Misurare l'efficacia delle directive — un problema di confondimento, non solo di conteggio

Non basta contare "quante volte SWEEP_TIMEOUT_EARLY è seguito da un
PASS": la Policy di retry esistente impone *comunque* una variazione
(riformulazione/temperatura) a ogni tentativo, indipendentemente dalla
directive. Se ogni retry cambia sia la temperatura sia la frase-hint, un
retry riuscito potrebbe essere dovuto solo al cambio di temperatura, non
alla frase — misurare un tasso grezzo senza controllo sarebbe
correlazione spacciata per causalità.

**Fase 1 — infrastruttura (subito, indipendente dal design statistico):**
ogni tentativo di retry logga un record collegato al precedente:

```json
{
  "case_id": "job-8f3a21",
  "attempt": 2,
  "directive_used": "SWEEP_TIMEOUT_EARLY",
  "outcome": "FAIL",
  "outcome_error": "gauge_check_timeout",
  "same_error_as_previous": true,
  "source": "virtual"
}
```

Senza `case_id` a collegare i tentativi non si può calcolare nulla —
questo va costruito comunque, prima ancora di sapere quale analisi
farci sopra.

**Fase 2 — confronto controllato, quando c'è volume:** per una frazione
dei casi nello stesso bucket di classificazione, alternare "solo
variazione generica" vs "variazione generica + directive specifica", e
confrontare i tassi di PASS tra i due gruppi. Solo così la frase-hint è
isolata come variabile, non confusa con l'effetto della variazione già
obbligatoria.

**Soglia minima prima di fidarsi del numero:** almeno N≥20 casi per
directive prima di considerare il tasso significativo, con intervallo di
Wilson invece di percentuale grezza — su pochi campioni una percentuale
grezza è rumore travestito da segnale. Stessa disciplina già applicata
nel progetto: misurare prima di decidere, non assumere (bug OpenBLAS, bug
dimensionale `[v14]`).

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

- [x] Gauge-check separato dall'esecuzione del codice in un subprocess
      indipendente, con timeout proprio (`gauge_check_timeout` distinto
      da `execution_timeout`) — **anticipato in M1**:
      `services/verifier/executor/gauge_check.py` + routing per chiave
      in `watcher.py`, per ora solo interferenza statica (nessuno
      sweep). Verificato indipendentemente, incluso il bug del
      Dockerfile (`gauge_check.py` mancante dalla `COPY`) trovato e
      corretto durante la revisione di M1.
- [x] Timeout del gauge-check calibrato empiricamente sul worst-case
      osservato durante il batch, non stimato a priori — worst-case
      misurato: sweep elicoidale completo di TC2 (calibro GO, 21 step,
      nessuna uscita anticipata) a ~65.5s di CPU-time (user+sys, non
      wall-clock — vedi nota su multithreading OCC in `gauge_check.py`).
      Limite interno alzato da 30s (placeholder M1) a **100s**, esterno
      da 45s a **150s** (stesso rapporto ~1.5x già in uso). Placeholder
      precedente causava SIGKILL prima di un vero timeout diagnosticabile
      — vedi sezione "Batch M2" sotto per i numeri completi.
- [x] Budget massimo di retry L3→L2 fissato a 3 tentativi + uscita
      anticipata su ripetizione dello stesso errore, implementato
      nell'orchestratore — `services/orchestrator/retry_policy.py`
      (`RetryBudget`, `classify_checkpoint`) + loop di retry in
      `generate_and_verify.py`. Verificato con `verify_retry_policy.py`
      (mock di Flowise/verifier, nessuna istanza viva disponibile in
      questo sandbox): tre scenari (recupero al 2° tentativo, budget
      esaurito su 3 errori diversi, uscita anticipata su 2 ripetizioni
      consecutive) tutti confermati. **Riserva onesta:** la temperatura
      crescente per tentativo passa via `overrideConfig` nella chiamata
      Flowise — non verificato che il nodo ChatOpenAI del Chatflow L2 lo
      accetti (nessuna istanza Flowise viva). Il loop chiama solo
      `/verify` (non `/gauge-check`), quindi `classify_checkpoint` ricade
      sempre su `RETRY_GENERIC` finché un lavoro futuro (M3) non integra
      il gauge-check nello stesso loop di generazione.
- [x] Log strutturato `case_id`/`attempt`/`directive_used`/`outcome` per
      collegare i tentativi — `RetryBudget.record_attempt()` scrive un
      record JSONL per tentativo (`retry_policy.py`, `RETRY_LOG_PATH`),
      prerequisito di qualunque misura di efficacia delle directive.
- [ ] Design di confronto controllato (con/senza directive specifica, a
      parità di variazione generica) definito prima di trarre conclusioni
      sui tassi di successo per directive — non fatto in M2: richiede
      volume di casi reali che non esiste ancora (nessuna pipeline L2
      viva in questo sandbox), resta lavoro futuro esplicito.
- [x] TC1: calibri pin GO/NO-GO modellati (`config/gauges/pin_D8_GO/NOGO_clearance.step`,
      via `generate_pin_gauge.py`), controllo interferenza statico+sweep
      implementato e verificato su geometria nota — vedi "Batch M2" sotto.
- [x] TC2: calibro filettato GO/NO-GO modellato (riuso dei tamponi M6 di
      M1), sweep elicoidale implementato e verificato su geometria nota
      (foro filettato nominale sintetico) — vedi "Batch M2" sotto.
- [x] TC3: punti di misura dichiarati nello schema L2.5 (preset
      `snap_fit` in `presets.json`, campo `measurement_points`), controllo
      distanza minima (`BRepExtrema_DistShapeShape`, modalità
      `min_distance` di `gauge_check.py`) implementato e verificato su
      geometria nota — vedi "Batch M2" sotto. Non ancora collegato alla
      normalizzazione L2.5 reale in Flowise (fuori scope, nota nel preset).
- [x] Batch dei tre TC eseguito e documentato con risultati numerici —
      vedi sezione "Batch M2 — esecuzione e risultati" sotto.
- [ ] Modelli CAD di riferimento riusati per popolare il campo diagnostico
      del Livello 6 (collegamento esplicito col bootstrap retroattivo) —
      non fatto in M2: i pezzi di controllo usati qui sono generati al
      volo dagli script `verify_gauge_check_tc*.py` (stesso stile di M1),
      non ancora versionati come modelli CAD di riferimento riusabili dal
      Livello 6. Resta lavoro futuro esplicito.

## Batch M2 — esecuzione e risultati

Eseguito a mano (nessuna suite di test automatica nel progetto, stesso
stile di M1 — v12/v14 in architettura, `verify_gauge_check*.py`), fuori
Docker: `pip install cadquery==2.8.0` in un venv locale (stesso limite di
sandbox già incontrato in M1 — `docker build` verso Docker Hub bloccato
da policy organizzativa, vedi `/root/.ccr/README.md`). Sei script,
eseguiti in sequenza, tutti OK:

```
$ python services/verifier/executor/verify_gauge_check.py                # M1, sintetico
=== Esito complessivo: TUTTI I CONTROLLI OK ===

$ python services/verifier/executor/verify_gauge_check_real_gauges.py    # M1, calibri reali M6
=== Esito complessivo: TUTTI I CONTROLLI OK ===

$ python services/verifier/executor/verify_gauge_check_tc1.py            # M2, TC1
--- GO sweep ---   status=PASS  steps_completed=16/16  first_interference_step=null
--- NOGO sweep ---  status=FAIL  steps_completed=2/16   first_interference_step=1  volume=2.545mm3
Determinismo sweep: OK
=== Esito complessivo: TUTTI I CONTROLLI OK ===

$ python services/verifier/executor/verify_gauge_check_tc2.py            # M2, TC2
--- Calibro GO (sweep elicoidale, 21 step) ---   status=PASS  volume_max=0.305928mm3 (< epsilon 0.5mm3)
--- Calibro NO-GO (sweep elicoidale, 21 step) --- status=FAIL  first_interference_step=0  volume=20.158363mm3
Determinismo sweep elicoidale: OK
=== Esito complessivo: TUTTI I CONTROLLI OK ===

$ python services/verifier/executor/verify_gauge_check_tc3.py            # M2, TC3
--- Gap nominale (0.3mm) ---            status=PASS  measured_mm=0.3
--- Gap fuori tolleranza (0.1mm) ---     status=FAIL  measured_mm=0.1  (snap alla faccia reale, non al punto dichiarato)
Determinismo: OK
=== Esito complessivo: TUTTI I CONTROLLI OK ===

$ python services/orchestrator/verify_retry_policy.py                    # M2, contratto di retry (mock)
classify_checkpoint: 6/6 casi limite corretti
RetryBudget: uscita anticipata + log su file OK
main() con retry: scenario FAIL→PASS OK, budget esaurito OK, uscita anticipata OK
=== Esito complessivo: TUTTI I CONTROLLI OK ===
```

Output completo (694 righe, incollato senza tagli) allegato alla
consegna di questa milestone.

**Nota onesta su TC2 (calibrata sull'epsilon di sweep elicoidale):** il
calibro GO, correttamente sottodimensionato, mostra comunque un residuo
di interferenza fino a ~0.31mm³ vicino a metà della corsa (non agli
estremi) — causa identificata e verificata empiricamente: le estremità
piatte (non smussate) dello sweep elicoidale finito di
`generate_thread_gauge.build_thread_plug()`, usata sia per i calibri sia
per il pezzo di controllo. Non è un errore di fase (verificato: il segno
della rotazione sincronizzata al passo è stato determinato empiricamente
confrontando le due possibilità — quella sbagliata produce interferenza
enorme anche a piena registrazione, ~17mm³ contro ~0.3mm³). `HELICAL_SWEEP_VOLUME_EPSILON_MM3
= 0.5` in `gauge_check.py` è tarato su questo residuo misurato (margine
~1.6x sopra il rumore del GO, ~2.7x sotto il più piccolo valore di
interferenza vera del NO-GO) — non un valore di comodo. **Limite
onestamente aperto:** un vero calibro filettato ha uno smusso di imbocco
proprio per evitare questo effetto sulla prima spira — i calibri di
questo progetto non ce l'hanno ancora; se in futuro serve un epsilon più
stretto, la soluzione corretta è modellare lo smusso, non restringere la
soglia sopra dati rumorosi.
