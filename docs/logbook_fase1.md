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

- [x] Formato `config/gauges/` definito e primo calibro GO/NO-GO per
      `thread` (M6, ISO 68-1) modellato con CAD convenzionale —
      `config/gauges/generate_thread_gauge.py` genera
      `thread_M6_GO_ISO68-1.step` (Ø5.7mm) e `thread_M6_NOGO_ISO68-1.step`
      (Ø6.3mm), tampone filettato esterno per verificare un **foro**
      filettato (coerente con l'esempio L2.5, non un anello). Ogni export
      è auto-verificato (validità, bbox, volume nel range plausibile) —
      non generato e assunto corretto. Verifica aggiuntiva fatta a mano
      prima di committare: periodicità dell'elica confermata con test
      punto-per-punto dentro/fuori materiale a passo 1.0mm — stessa classe
      di controllo che avrebbe intercettato la classe di bug `[v14]`.
      **Nota aperta:** la lunghezza di impegno (8mm) è un placeholder,
      non ancora un campo dello schema L2.5 — vedi commento nello script
      e in `presets.json`.
      **Nota di tooling per sessioni future:** in questo sandbox `docker
      build` verso Docker Hub è bloccato da policy organizzativa (403 su
      `production.cloudfront.docker.com`, non aggirabile né da aggirare —
      vedi `/root/.ccr/README.md`); `pip install cadquery==2.8.0` invece
      funziona direttamente (PyPI non è dietro il proxy con policy). Non
      serve infrastruttura esterna (VM) per questo tipo di lavoro.
- [x] `presets.json` esteso con i campi gauge per il preset `thread`
      (`gauge_go_step`, `gauge_nogo_step`)
- [ ] Modulo di calcolo interferenza/distanza aggiunto a
      `services/verifier/executor/` (nuovo script o estensione di
      `run_and_measure.py`)
- [ ] Protocollo job/result esteso con blocco `gauge_check`
- [ ] Test di determinismo (stesso input → stesso output) verificato
- [ ] Milestone raggiunta su pezzo/calibro statici, non ancora su output AI
