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

- [x] **[v1]** Formato `config/gauges/` definito: una directory per
      coppia di calibri (`<feature>_<nominale>_<norma>/{GO,NOGO}.step`),
      registro di provenienza in `config/gauges/manifest.json`, montato
      read-only in `verifier-executor` su `/gauges` (vedi
      `docker-compose.yml`). Vedi `config/gauges/README.md`.
- [ ] **Primo calibro GO/NO-GO per `thread` (M6, ISO 68-1) — NON
      modellato.** Non e' un task che questa sessione puo' completare:
      il vincolo "niente file generati dall'IA" (vedi `docs/logbook.md`,
      punto 3, e `docs/handoff_m1.md`) e' esplicito e non aggirabile —
      serve CAD convenzionale da parte di chi ha accesso allo strumento.
      `config/gauges/thread_M6_ISO68-1/NOTE.md` e
      `config/gauges/manifest.json` (`calibration_status:
      "not_modeled"`) documentano lo stato in modo che non venga
      scambiato per fatto. **Bloccante per un collaudo end-to-end reale
      su M6**, non bloccante per il resto della checklist sotto (validata
      su geometrie sintetiche, vedi ultima voce).
- [x] **[v1]** `presets.json` esteso: preset `thread` con
      `gauge_go_step`/`gauge_nogo_step` (path relativi a
      `config/gauges/`).
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
      (`cmp`), esito reale OK (vedi log del commit di questa milestone).
      Verifica anche PASS/FAIL su geometria sintetica con volume di
      interferenza noto analiticamente (pin oversize in un anello,
      volume calcolato = volume misurato entro 0.01mm³).
- [ ] **Milestone raggiunta su pezzo/calibro statici — parzialmente.**
      Il meccanismo (protocollo, codice, timeout separato, checkpoint,
      determinismo) e' verificato end-to-end su geometrie sintetiche
      generate ad-hoc SOLO per il test (non versionate, non sono i
      calibri reali). **Non ancora verificato con Docker/il container
      `verifier-executor` reale** (nessun daemon Docker disponibile in
      questa sessione — da rieseguire in un ambiente con Docker prima di
      considerare il protocollo confermato end-to-end) **ne' con il
      calibro M6 reale** (non ancora modellato, vedi sopra). Prossimo
      passo naturale, fuori da questa sessione: modellare GO.step/
      NOGO.step per M6, poi ripetere `verify_gauge_check.py` (o un
      equivalente) attraverso lo stack Docker reale.
