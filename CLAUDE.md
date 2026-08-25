# CLAUDE.md — faro per ogni sessione CALIPER

Questo file viene caricato all'avvio di ogni sessione Claude. Esiste per
curare l'amnesia delle sessioni senza memoria storica: le direttive
chiave sono QUI, il dettaglio è nei documenti citati. **Regola d'oro:
prima di toccare un'area, leggi il documento che la governa (tabella
sotto). Non ricostruire da zero ciò che è già scritto: è già stato
pagato una volta, spesso in ore di GPU.**

## Cos'è CALIPER

Layer di verifica deterministica per CAD generato da LLM: prompt →
normalizzazione (L2.5, Ollama locale) → generazione codice CadQuery
(L2, ChatOpenAI) → esecuzione isolata → collaudo con calibri virtuali
GO/NO-GO (booleane OCC) → retry loop → memoria virtuale/fisica (L6/L7,
Qdrant). Architettura completa: `README.md` e
`docs/architettura-prototipo-mesh-llm.md`.

## Stato del progetto (aggiornare a ogni chiusura di milestone)

- **M1–M7 CHIUSE.** M6 (2026-08-22): suite TC-E2E 9/9 dal vivo su pod
  RunPod, primo end-to-end reale del progetto, SIGSEGV risolto. M7
  (2026-08-25): stessa suite (TC-E2E-1..7) riprodotta nella topologia
  Docker reale su RTX 3090 (workstation privata, non RunPod) — 6 bug
  reali trovati e risolti (tutti giunture mai esercitate in M6: crash
  Flowise su Docker, entrypoint/limite STEP di `billa05/prusacli`,
  budget CPU non trasferibili a un host a 32 core reali, baseUrl Ollama
  nel chatflow L2.5, offset di `stream-agent` su mount read-only), 3/3
  verifiche di isolamento attive, primo G-code della storia del
  progetto. Cronistoria ufficiale: `docs/logbook.md` (una riga per
  milestone).
- **Estensione M7 — "Prompt to Part" (2026-08-25, stesso giorno):**
  pagina statica interattiva servita dalla dashboard
  (`services/dashboard/static/prompt-to-part.html`) — prompt in
  linguaggio naturale → L2.5 (Ollama) → L2 (GPT) →
  `/verify`+gauge-check → viewer 3D (WebGL scritto da zero, nessuna
  libreria esterna) → 4 download (STEP, STL, G-code via nuovo servizio
  `slicer-watcher`, PDF stile disegno tecnico via `reportlab`).
  Due flag `.env` nuovi: `PUBLIC_ACCESS` (0.0.0.0/127.0.0.1 — Flowise e
  Open WebUI pubblici o solo interni; la dashboard NON è mai gated, per
  scelta esplicita, altrimenti un OFF da remoto toglierebbe l'accesso
  al pannello che dovrebbe rimetterlo ON) e `PROMPT_TO_PART_MODE`
  (RW/RO — in RO gli endpoint rispondono 403 anche lato server).
  **Nessun controllo di accesso su `/api/generate`** (chiama GPT a
  pagamento, pagina pubblica senza limite di frequenza) — scelta
  esplicita dell'utente per accelerare, tracciata in issue #35, non
  un'omissione.
- **Immagine pod di riferimento (build- e boot-validata)**:
  `ghcr.io/danielesalpietro/caliper-pod:git-fe90a0b`.
- **Prossima**: M8 (schema L6 + primo loop fisico) — scope in
  `docs/piano_recupero.md` §M8. La parte documentale (schema +
  bootstrap) non dipende da nessuna istanza; la parte fisica ha ora
  entrambi i prerequisiti (pezzo PASS-virtuale + primo G-code, da M7).
- **Decisioni aperte**: modello L2.5 (re-bench con
  `bench/bench_l25_models.py` sullo schema post-fix, poi decide
  l'utente); barriera in `apply_preset` (campo utente vs inventato);
  ricalibrazione `CALIPER_CPU_LIMIT_S`; controllo accessi per
  `/api/generate` (issue #35); filettatura **esterna** ("vite", non
  solo il foro filettato di oggi) — scoping iniziato (vedi
  `_emit_thread_code` in `sketch_compiler.py`, il pezzo `_thread_pin`
  già esiste come geometria valida, va solo restituito invece di
  sottratto dall'host), nessun codice scritto: servirebbe anche un
  nuovo calibro ad **anello** (oggi solo tamponi, adatti a collaudare
  fori) prima di avere un Go/No-Go reale su una vite.

## Direttive non negoziabili (il metodo che ha funzionato)

1. **Niente "pronto" senza un'esecuzione osservata.** Un esito si
   dichiara solo con l'output reale davanti; un fix si dimostra
   rosso→verde (il rosso osservato PRIMA è la prova che il test morde).
2. **I numeri dipendenti dall'ambiente si rimisurano nell'ambiente.**
   Budget CPU, timeout, tolleranze: mai trasferiti per assunzione
   (vedi C8 e `docs/logbook_fase6.md`, E2E-8).
3. **Push incrementale sul branch di sessione, sempre.** Mai lavoro
   solo su una macchina remota (ha salvato il run0 e il run1). Mai
   commit diretti su `develop`; PR create su richiesta dell'utente, i
   merge li fa l'utente.
4. **Fallimento reale → fix minimale, documentato, dichiarato.** Cambi
   a codice condiviso o architettura: proposta al supervisore su issue
   GitHub, non decisione unilaterale.
5. **Segreti solo via env/Secrets** (`/workspace/.caliper_env` sul
   pod). Mai in chat, mai in argv, mai committati. Se uno finisce in
   chat → revoca immediata.
6. **Checklist pre-accensione prima di OGNI run a pagamento**
   (`docs/runbook_runpod.md` §0.4). "Build verde" ≠ "boot verde": il
   gate è `pod-boot-smoke` (parte da solo sui push di contenuto su
   develop; per modifiche solo-workflow, dispatch manuale).
7. **Regola di costo**: blocco di configurazione su macchina a
   pagamento → harvest + stop, non debug live. Harvest VERDE prima di
   spegnere; un pod volume muore col pod, git è l'unica persistenza.
8. **Mai kill del supervisord principale** (PID 1: giù il container —
   incidente run0). Riavvii mirati:
   `supervisorctl -s unix:///run/supervisord.sock restart <programma>`.
9. **Handoff autosufficienti, dichiarati in anticipo.** Nessuno step
   scoperto a macchina accesa. Ogni run produce un logbook per-TC
   (input, comando, output reale, esito) nello stile delle fasi 1–6.
10. **Onestà prima dell'ottimismo**: un rosso documentato vale più di
    un verde non provato; le riserve si scrivono, non si smussano.

## Mappa dei documenti — leggi PRIMA di toccare

| Se devi… | Leggi |
|---|---|
| Capire il metodo e il piano M5–M8 | `docs/piano_recupero.md` (metodologia "chiudi la giuntura", milestone, config RunPod) |
| Capire le criticità note del codice (C1–C11) | `docs/review_tecnica.md` — la review che ha rifondato il progetto |
| Accendere un pod RunPod | `docs/runbook_runpod.md` (Fase 0 a costo zero, checklist 0.4, regola di costo) + `ops/runpod/README.md` (env, problemi noti) |
| Capire cosa fa il boot del pod | `ops/runpod/start.sh` (affinity da cgroup, bootstrap Flowise automatico, selftest) e `ops/runpod/flowise_bootstrap.py` (validato dal vivo, sequenza nel docstring) |
| Lavorare su executor/limiti/SIGSEGV | `docs/logbook_fase6.md` (diagnosi completa) + knob: `CALIPER_AS_LIMIT_MB`, `CALIPER_STACK_LIMIT_MB`, `CALIPER_CPU_LIMIT_S`, `GAUGE_CHECK_CPU_LIMIT_SECONDS`, `GAUGE_CHECK_TIMEOUT_SECONDS` (default = produzione, override = pod) |
| Sapere cos'è successo nei run RunPod | `docs/report_run0.md` (analisi run0 + gate), `docs/logbook_runpod_run0.md`, `docs/logbook_fase6.md` (run1, 9/9), `docs/checklist_post_riavvio_run1.md` |
| Capire perché esistono certe regole | `docs/retrospettiva_m6_bringup.md` (il fallimento di processo che le ha generate) |
| Eseguire/riscrivere la suite E2E | `docs/handoff_m6.md` (tabella TC-E2E-1..9, la fonte) + `docs/handoff_m6_run1.md` (delta e regole run) |
| Toccare i chatflow Flowise | `services/flowise/chatflows/` (versionati, unica fonte) + `services/flowise/import_chatflows.py`; MAI costruire a mano in UI ciò che è versionato |
| Valutare/cambiare il modello L2.5 | `bench/bench_l25_models.py` (matrice riusabile; metrica decisiva inventati%=0) + risultati in `docs/logbook_fase6.md` §bench |
| Capire una milestone passata (M1–M5) | `docs/handoff_m<N>.md` + `docs/logbook_fase<N>.md` |
| Vedere lo storico decisioni run-time | issue GitHub **#18** (canale di supervisione M6+) e #15 (review) |

## Ruoli e canali

- **Sessione supervisore**: guida via issue #18 + branch; rivede i fix
  a codice condiviso; prepara handoff e gate. **Sessione esecutrice**
  (pod/3090): esegue l'handoff, push incrementale sul branch di
  sessione `claude/<milestone>-<tag>`, propone — non decide — sui cambi
  condivisi.
- CI: `regression.yml` (script di verifica), `publish-images.yml`
  (GHCR, tag `:git-<shortsha>`), `pod-boot-smoke.yml` (gate boot).
- I comandi/verify si scrivono come `verify_*.py` con mock dichiarati e
  vanno in `regression.yml`: un comportamento non coperto da un verify
  è un comportamento non garantito.

## Trappole note (pagate care — non ripagarle)

- RunPod: `nproc` MENTE (visibili ≫ quota cgroup, rapporto ~9x
  strutturale); env del template solo nome=valore; overlay effimero;
  pod volume ≠ network volume; porta SSH cambia a ogni riavvio;
  l'app mobile GitHub non può lanciare workflow_dispatch.
- Flowise 3.x: account/JWT (le env FLOWISE_USERNAME/PASSWORD 1.x sono
  inerti); SSRF blocca localhost/IP privati (`HTTP_SECURITY_CHECK`);
  password policy a 4 classi; con Structured Output Parser la
  prediction risponde in `data["json"]`, non `"text"`.
- GitHub Actions: il trigger `workflow_run` può non agganciarsi mai —
  boot-smoke usa un trigger `push` diretto per questo.
- Un modello piccolo obbedisce all'istruzione più vicina: le
  descrizioni dei campi negli schema devono offrire esplicitamente
  l'opzione "vuoto" (lezione E2E-1).
