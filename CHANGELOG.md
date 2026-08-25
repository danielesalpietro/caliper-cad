# Changelog

Formato ispirato a [Keep a Changelog](https://keepachangelog.com/),
adattato al ritmo per-milestone di questo progetto: ogni voce
corrisponde a una milestone del piano (`docs/logbook.md` §Milestone e
stato è la fonte primaria, con dettaglio per-TC; questo file è
l'indice cronologico). Non ci sono ancora release/tag versionati — le
date sono quelle di merge in `develop`.

## [M7] — 2026-08-25

Topologia Docker reale su RTX 3090 — prima volta su hardware dedicato
privato (non RunPod, non condiviso). PR
[#31](https://github.com/danielesalpietro/caliper-cad/pull/31), issue
[#19](https://github.com/danielesalpietro/caliper-cad/issues/19).

### Added
- Prima build Docker della storia del progetto (`docker compose build`
  — verifier, verifier-executor, stream-agent, dashboard).
- `ops/docker/flowise/Dockerfile`: build locale patchata sopra
  `flowiseai/flowise:3.1.4` (l'immagine ufficiale crasha su ogni boot
  Docker per un bug upstream in `connect-sqlite3`).
- Primo G-code della storia del progetto (slicing reale con
  `billa05/prusacli`, dopo aver corretto l'entrypoint e aggirato un
  limite di import STEP dell'immagine).
- Volume dedicato `stream_agent_state` per l'offset di `stream-agent`
  (C6), separato dal mount read-only del log virtuale.
- `docs/logbook_fase7.md`, `docs/report_m7_run0.md` (analisi + fingerprint
  hardware/software del run), `runs/20260825-133500-m7-run0/` (harvest).

### Fixed
- Flowise 3.1.4 in Docker: crash sistematico al boot (`connect-sqlite3`
  API mismatch).
- `docker-compose.yml`, servizio `prusaslicer`: entrypoint di default
  errato (bash invece del binario).
- Chatflow L2.5 versionato: `baseUrl` del nodo Ollama puntava a
  `localhost` (valido su RunPod, rotto tra container Compose separati).
- Budget CPU/memoria di `verifier-executor` (ereditati da M6, non
  trasferibili a un host a 32 core reali): `CALIPER_AS_LIMIT_MB`
  confermato necessario a 16384, `CALIPER_CPU_LIMIT_S` 10→25,
  `GAUGE_CHECK_CPU_LIMIT_SECONDS` 140→405 — tutti rimisurati dal vivo.

### Verified
- 3/3 verifiche di isolamento attive (non solo dichiarate): rete
  bloccata da `verifier-executor`, POST al socket-proxy rifiutata
  (403), path traversal su `/gauge-check` rifiutato via HTTP.
- Suite TC-E2E-1..7 riprodotta nella topologia reale — tutta verde;
  E2E-2 con numeri identici a M6 (GO 0.305925mm³, NO-GO 20.158069mm³).

## [M6] — 2026-08-22

Bring-up reale su RunPod, prima esecuzione end-to-end viva del
progetto. PR [#25](https://github.com/danielesalpietro/caliper-cad/pull/25),
[#26](https://github.com/danielesalpietro/caliper-cad/pull/26), issue
[#18](https://github.com/danielesalpietro/caliper-cad/issues/18).

### Added
- Immagine `caliper-pod` (GHCR) e stack completo su RunPod: Qdrant,
  Ollama, Flowise, servizi Python, tutti nativi (niente
  Docker-in-Docker sui pod).
- Bootstrap Flowise automatico (`ops/runpod/flowise_bootstrap.py`):
  account, API key, credential OpenAI, aggancio ai chatflow L2 — zero
  passaggi manuali in UI.
- 3 chatflow versionati in `services/flowise/chatflows/` (L2.5, L2
  free-code, L2 param/sketch-first).
- `docs/logbook_fase6.md`, bench di 5 modelli L2.5
  (`bench/bench_l25_models.py`).

### Fixed
- **SIGSEGV nell'esecutore**: pool di thread nativi (OpenBLAS/OCC/VTK)
  dimensionati sui core *visibili* del pod (128–256), non sulla quota
  cgroup reale (~13–27) — affinity calcolata da `/sys/fs/cgroup/cpu.max`
  + `CALIPER_STACK_LIMIT_MB`/`CALIPER_AS_LIMIT_MB` parametrici.
- Password Flowise generata da `openssl rand -base64` non garantiva
  sempre un carattere speciale — bootstrap ora riprova con suffisso a
  4 classi garantite.
- Policy SSRF di Flowise 3.1.4 bloccava `localhost`/IP privati
  (necessario per Flowise→Ollama nello stesso pod) — `HTTP_SECURITY_CHECK=false`.
- Schema `tolerance_type` del chatflow L2.5: il modello inventava
  deterministicamente un valore quando il campo doveva restare vuoto
  (mancava l'opzione esplicita "vuoto" nello schema).

### Verified
- 9/9 TC-E2E eseguiti dal vivo (run0: 0/9 + diagnosi; run1: 9/9).
- Primo PASS end-to-end reale della storia del progetto (E2E-2): L2.5
  → L2 (GPT) → `/verify` → `/gauge-check` GO/NO-GO.
- C2 (NO-GO nel loop), C6 (id Qdrant deterministici + offset
  persistito a un riavvio reale), C8 (budget CPU ricalibrato: 91.35s
  worst-case → 140s).

## [M5] — 2026-08-22

Fix pack post-review: sblocca l'end-to-end (C1–C8, C10). PR
[#22](https://github.com/danielesalpietro/caliper-cad/pull/22), issue
[#17](https://github.com/danielesalpietro/caliper-cad/issues/17).

### Fixed
- C1: contratto dimensionale per-feature (per `thread`, il Go/No-Go
  gauge check è il collaudo, non il confronto bbox-vs-nominale).
- C2: calibro NO-GO agganciato nel loop reale (prima solo GO).
- C3: preset `snap_fit` non crasha più l'orchestratore (job
  `min_distance` costruito dai `measurement_points`).
- C4: strategia `param_first` (L2 emette solo parametri numerici, il
  compilatore deriva le coordinate).
- C5: `tolerance`/`pitch` nella chiave della memoria virtuale, conteggio
  per caso non per tentativo, `checker_version` nei record.
- C6: id Qdrant deterministici da contenuto (`uuid5`), offset
  persistito.
- C7: split esecuzione/misura — il processo che esegue codice non
  fidato non scrive mai il verdetto.
- C10: quick win vari (`AnnAssign`, trigger CI su `claude/**`, cleanup
  `/exec/parts`, bind `127.0.0.1` sulle porte del compose, Flowise
  pinnato a una versione esplicita).

### Added
- 8 nuovi script `verify_*.py` (TC-M5-1…8) in
  `.github/workflows/regression.yml`.

## [M4] — 2026-08-21

Chiusura del loop di retrieval, firewall simulato/fisico esplicito. PR
[#14](https://github.com/danielesalpietro/caliper-cad/pull/14), issue
[#5](https://github.com/danielesalpietro/caliper-cad/issues/5).

### Added
- `retry_log.jsonl` esteso con `feature`/`spec_key`.
- Gate anti-bias (`virtual_memory.py`): esclusione di una strategia
  richiede corroborazione fisica, mai solo fallimenti virtuali; fail
  open verso la generazione, mai verso l'esclusione.
- Due collezioni Qdrant separate (fisica/virtuale) con `source`
  esplicito su ogni record recuperato.

## [M3] — 2026-08-21

Pipeline sketch-first → compilazione → collaudo (preset `thread`). PR
[#11](https://github.com/danielesalpietro/caliper-cad/pull/11), issue
[#4](https://github.com/danielesalpietro/caliper-cad/issues/4).

### Added
- Schema JSON per vincoli sketch 2D (`sketch_schema.py`), compilatore
  verso CadQuery (`sketch_compiler.py`), wiring nel retry loop.

### Fixed (durante la costruzione, non nel codice preesistente)
- STEP mai esportato da `run_and_measure.py`.
- Timeout HTTP del gauge-check non ricalibrato.
- `spec` mai inoltrata a `/verify`.

## [M2] — 2026-08-21

Controlli geometrici deterministici, validati su geometrie note. PR
[#8](https://github.com/danielesalpietro/caliper-cad/pull/8), issue
[#3](https://github.com/danielesalpietro/caliper-cad/issues/3).

### Added
- TC1/TC2/TC3 verificati indipendentemente.
- Retry L3→L2.

### Fixed
- Timeout ricalibrato su misura reale (65.5s CPU worst-case, non un
  numero assunto).

## [M1] — 2026-08-21

Scaffold di isolamento + calibri di riferimento. PR
[#7](https://github.com/danielesalpietro/caliper-cad/pull/7), issue
[#2](https://github.com/danielesalpietro/caliper-cad/issues/2).

### Added
- Calibro Go/No-Go virtuale (`gauge_check.py`): interferenza booleana
  esatta, sweep lungo un percorso di inserimento/avvitamento, distanza
  minima esatta — contro calibri di riferimento versionati
  (`config/gauges/`).
- Protocollo di isolamento esecuzione/verdetto (base di quello poi
  irrevocabile in M5/C7).

---

*Per il dettaglio tecnico completo di ogni milestone (input/output
attesi, rosso osservato prima di ogni fix, verifica indipendente) vedi
`docs/logbook.md` e i rispettivi `docs/logbook_fase<N>.md`.*
