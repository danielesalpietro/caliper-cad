# Logbook — M1: Scaffold di isolamento + calibri di riferimento

Vedi [`logbook.md`](logbook.md) per il quadro generale e la revisione
critica completa. Nota terminologica: questa è la prima milestone del
sotto-percorso "Ciclo di Collaudo Virtuale", innestato nella **Fase A**
esistente — non una nuova Fase C e non un sostituto della Fase B.

Prompt di handoff pronto per la sessione che implementa questa milestone:
[`handoff_m1.md`](handoff_m1.md).

## Obiettivo (rivisto rispetto alla proposta originale)

Proposta originale: nodo Docker per un motore 3D headless con VRAM
allocata, import dinamico di STL/STEP, milestone su un semplice raycast
lineare.

Adottato: **nessun nuovo container GPU**. Il collaudo virtuale è
un'estensione CPU-only di `verifier-executor` (già isolato,
`network_mode: none`, CadQuery/OCC presente) — vedi punto 2 della revisione
critica in `logbook.md` per il perché (evidenza diretta: il bug reale `[v14]`
non sarebbe stato colto da un collision check).

## Cosa serve costruire

1. **Formato dei calibri di riferimento.** File STEP versionati sotto
   `config/gauges/` (stesso pattern di `config/prusaslicer/`), non generati
   dall'IA. Estendere `services/orchestrator/presets.json` con i campi
   `gauge_go_step` / `gauge_nogo_step` (o `gauge_step` per un calibro
   singolo, es. TC1) per preset — oggi solo `thread` è `defined: true`, si
   parte da lì.
2. **Protocollo di invocazione.** Riusare il protocollo job/result su
   volume condiviso già in uso tra `verifier` e `verifier-executor`
   (`verifier_exec`), non una nuova rotta HTTP — coerente con Rischio #9
   (Flowise/HTTP non sono adatti a step deterministici con side-effect).
   Il job include il percorso della parte generata + il percorso del
   calibro; il result aggiunge un blocco `gauge_check` accanto a
   `dimensional_check` (già presente in `run_and_measure.py`).
3. **Import dinamico STEP (non solo STL).** La proposta originale parlava
   di STL — qui si preferisce STEP quando disponibile, per lo stesso
   motivo già stabilito nel Rischio #3 ("verifica parametrica preferita
   alla verifica su mesh, dove possibile"): un confronto B-Rep esatto è
   più affidabile di uno ricostruito da una mesh triangolata.

## Milestone (criterio di accettazione, rivisto)

Dato un pezzo STEP noto e un calibro STEP noto (entrambi statici, non
generati da un LLM — l'AI non entra in questa milestone), il sistema
restituisce un JSON PASS/FAIL con volume di intersezione, tramite lo stesso
protocollo job/result esistente.

**Aggiunta rispetto alla proposta originale:** il criterio di accettazione
include un test di **determinismo esplicito** — stesso input eseguito due
volte produce output identico byte per byte. Non è un dettaglio: è la
proprietà che l'intera architettura esiste per garantire (vedi Rischio #3),
va verificata esplicitamente qui, non data per scontata perché "è solo
matematica su B-Rep".

## Domanda aperta da portare avanti

I controlli relazionali statici/kinematici (interferenza, sweep, distanza
minima) coprono davvero il 100% dei casi TC1–TC3, o esiste un caso che
richiede simulazione fisica reale? Se in futuro serve una misura di
deformazione elastica (es. forza reale per sganciare uno snap-fit), la
risposta corretta è un solver FEA dedicato (es. CalculiX), non un motore
fisico/GPU per rigid body — un motore fisico da videogioco non modella
comunque la deformazione elastica del materiale. Non decidere questa
domanda per assunzione: verificarla quando (e se) un test case la richiede
davvero.

## Stato

Due sessioni hanno lavorato in parallelo su rami diversi a partire dallo
stesso handoff (`handoff_m1.md`) e sono state riconciliate qui: una ha
costruito il protocollo/codice (formato `config/gauges/`, gauge-check,
routing watcher, endpoint HTTP), l'altra ha modellato il primo calibro
reale. Voce per voce, stato consolidato:

- [x] **[v1]** Formato `config/gauges/` definito: file STEP a livello
      piatto sotto `config/gauges/` (naming
      `<feature>_<GO|NOGO>_<nominale>_<norma>.step`), registro di
      provenienza in `config/gauges/manifest.json`, montato read-only in
      `verifier-executor` su `/gauges` (vedi `docker-compose.yml`). Vedi
      `config/gauges/README.md`.
- [x] **[v2]** Primo calibro GO/NO-GO per `thread` (M6, ISO 68-1)
      **modellato**: `config/gauges/generate_thread_gauge.py` genera
      `thread_M6_GO_ISO68-1.step` (Ø5.7mm) e `thread_M6_NOGO_ISO68-1.step`
      (Ø6.3mm), tampone filettato esterno per verificare un **foro**
      filettato (coerente con l'esempio L2.5, non un anello). Non un
      file fabbricato dall'IA nel senso vietato dal vincolo (vedi
      `docs/logbook.md`, punto 3): script deterministico basato su
      formule ISO note, eseguito una tantum da un umano, mai dall'LLM in
      loop di generazione — vedi docstring dello script e
      `config/gauges/README.md` per la distinzione. Ogni export
      auto-verificato (validità, bbox, volume nel range plausibile) —
      non generato e assunto corretto. Verifica aggiuntiva a mano prima
      di committare: periodicità dell'elica confermata con test
      punto-per-punto dentro/fuori materiale a passo 1.0mm — stessa
      classe di controllo che avrebbe intercettato la classe di bug
      `[v14]`. **Nota aperta:** la lunghezza di impegno (8mm) è un
      placeholder, non ancora un campo dello schema L2.5 — vedi commento
      nello script e in `presets.json`.
      **Nota di tooling:** in questo sandbox `docker build` verso Docker
      Hub è bloccato da policy organizzativa (403, non aggirabile — vedi
      `/root/.ccr/README.md`); `pip install cadquery==2.8.0` funziona
      invece direttamente (PyPI non è dietro il proxy con policy). Non
      serve infrastruttura esterna (VM) per generare i calibri né per
      validare il gauge-check fuori da Docker — vedi voce sotto.
- [x] **[v1]** `presets.json` esteso: preset `thread` con
      `gauge_go_step`/`gauge_nogo_step` (path relativi a
      `config/gauges/`, coerenti col mount `/gauges` in
      `verifier-executor`).
- [x] **[v1]** Modulo di calcolo interferenza aggiunto:
      `services/verifier/executor/gauge_check.py` — nuovo script
      separato da `run_and_measure.py` (mai lo stesso processo/timeout,
      vedi vincolo in `docs/handoff_m1.md`), boolean intersection esatta
      via CadQuery/OCC (`Shape.intersect().Volume()`), diagnostica
      pre-flight (facce/edge unici via `TopTools_IndexedMapOfShape`,
      `BRepCheck_Analyzer`) scritta su un file di checkpoint PRIMA del
      boolean pesante (vedi `docs/logbook_fase2.md`, "Formato del log su
      TIMEOUT"). Scope M1 deliberatamente limitato a interferenza
      statica — nessuno sweep (quello e' TC1/TC2, M2).
- [x] **[v1]** Protocollo job/result esteso: `watcher.py` instrada per
      chiave (`"code"` → `run_and_measure.py`, invariato; `"gauge_check"`
      → `gauge_check.py`, nuovo), con timeout esterno indipendente
      (`GAUGE_CHECK_TIMEOUT_SECONDS`, placeholder 45s — non ancora
      tarato empiricamente, scope M2) e `error: "gauge_check_timeout"`
      distinto da quello di `exec(code)`. Nuovo endpoint
      `POST /gauge-check` in `services/verifier/app.py`
      (`GaugeCheckRequest{part_step_path, gauge_step_path}`), percorso
      indipendente da `/verify`. Blocco `gauge_check` nel result JSON,
      accanto a `dimensional_check` (mai fuso: `source: "virtual"`
      esplicito su ogni record, coerente col firewall simulato/fisico di
      M4).
- [x] **[v1]** Test di determinismo — **verificato empiricamente**, non
      assunto: `services/verifier/executor/verify_gauge_check.py`
      esegue lo stesso job due volte e confronta l'output byte per byte
      (`cmp`), esito reale OK. Verifica anche PASS/FAIL su geometria
      sintetica con volume di interferenza noto analiticamente (pin
      oversize in un anello, volume calcolato = volume misurato entro
      0.01mm³).
- [x] **[v2]** Milestone raggiunta su pezzo/calibro statici, **con i
      calibri reali M6** (non più solo su geometria sintetica ad-hoc):
      vedi la sezione "Verifica end-to-end sui calibri reali" più sotto
      per il dettaglio e l'esito.

## Verifica end-to-end sui calibri reali

Chiusura del gap lasciato aperto dalla prima riconciliazione (verificato
solo su geometria sintetica usa-e-getta): con `thread_M6_GO_ISO68-1.step`
e `thread_M6_NOGO_ISO68-1.step` ora versionati, `gauge_check.py` è stato
eseguito **realmente** contro di essi tramite il protocollo job/result
(`services/verifier/executor/verify_gauge_check_real_gauges.py`), non
solo tramite un import diretto.

Pezzo di controllo: un blocco con un foro liscio passante Ø6.0mm
(nominale) — non un foro filettato reale (costruirne uno fedele è scope
di M3, non di M1), ma sufficiente a validare la semantica GO/NO-GO in
modo verificabile:

- **Calibro GO (inviluppo Ø5.7mm)** contro il foro Ø6.0mm → nessuna
  interferenza (il tampone è più piccolo del foro) → **PASS, volume
  0.0mm³**. Confermato.
- **Calibro NO-GO (inviluppo Ø6.3mm)** contro lo stesso foro → **FAIL,
  volume misurato 3.69mm³**. Confermato che viene rilevata
  interferenza — ma **il volume esatto non è stato derivato in forma
  chiusa** come nel caso sintetico (pin pieno): il tampone NO-GO è
  filettato, solo le creste dell'elica raggiungono il diametro di
  inviluppo, il resto del profilo a V sta sotto — un calcolo analitico
  esatto richiederebbe integrare l'intersezione del profilo lungo
  l'elica. Verificato invece che il volume misurato sia positivo (non
  solo lo status booleano) e sotto il limite superiore ottenuto
  assumendo un cilindro pieno equivalente (23.19mm³) — nessun profilo a
  V può interferire più di un cilindro pieno alla stessa dimensione.
  **Onestà sul livello di confidenza:** il PASS/FAIL è verificato con
  certezza, il volume numerico è preso come dato misurato, non come
  predizione confermata — a differenza del caso sintetico dove il
  confronto era esatto entro 0.01mm³.
- **Determinismo riconfermato sui file reali**: l'intero script
  (import STEP reale incluso) eseguito due volte produce output
  identico byte per byte (`diff` pulito).

Non ancora fatto, esplicitamente fuori scope M1: verifica contro un vero
foro filettato generato dalla pipeline (serve lo sweep elicoidale di
TC2/M2, non l'interferenza statica di M1) e verifica dentro il container
Docker reale (`verifier-executor`) — la sandbox di questa sessione non
ha un daemon Docker (`docker build` verso Docker Hub bloccato da policy
organizzativa, 403), ma `pip install cadquery==2.8.0` funziona
direttamente: lo stesso codice che gira nel container (`gauge_check.py`,
invariato) è stato eseguito ed è quello verificato qui, solo non dentro
l'immagine `verifier-executor` costruita da Docker.
