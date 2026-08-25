# Report M7 run0 — prima esecuzione su hardware dedicato privato

Data: 2026-08-25. Autore: sessione Claude Code, eseguita in SSH diretto
sulla workstation privata dell'utente (non RunPod, non un noleggio
vast.ai — quella macchina fisica ospita anche altri progetti/noleggi,
ma questa sessione ha lavorato solo dentro `~/caliper/repo`). Issue
[#19](https://github.com/danielesalpietro/caliper-cad/issues/19), PR
[#31](https://github.com/danielesalpietro/caliper-cad/pull/31)
(mergiata). Fonte primaria del dettaglio tecnico:
[`docs/logbook_fase7.md`](logbook_fase7.md) (per-TC, ogni bug con
rosso osservato e verde riprodotto) — questo documento aggiunge la
caratterizzazione hardware/software e l'analisi comparativa con M6
(RunPod) che il logbook non copre.

---

## 1. Esito in una riga

Prima volta che il progetto gira sulla topologia Docker completa (mai
esercitata prima: M1-M6 erano sandbox o processi nativi su pod) E
prima volta su hardware dedicato privato (non affittato/condiviso):
**6 bug reali trovati e risolti**, tutti sono giunture mai esercitate
prima (C9) — nessuno era visibile in M6. Criterio di accettazione di
M7 soddisfatto: suite TC-E2E-1..7 verde, 3/3 isolamento attivo
confermato, primo G-code della storia del progetto prodotto.

## 2. Ambiente — hardware e software di base

### Hardware

| Componente | Dettaglio |
|---|---|
| Modello | HP Z8 G4 (hostname `berlin-3eie`) |
| CPU | 2× Intel Xeon Gold 6244 @ 3.60GHz — **16 core fisici, 32 thread** (hyperthreading attivo), 2 nodi NUMA (0-7,16-23 / 8-15,24-31) |
| RAM | 235 GiB totali, 227 GiB disponibili all'avvio del run |
| GPU | 1× NVIDIA GeForce RTX 3090, 24576 MiB VRAM, driver 595.84, CUDA 13.2, PCIe gen1×16 a riposo (risparmio energetico, non un problema di link) |
| Disco (Docker data-root) | `/mnt/wdc-docker` — 466GB, 359GB liberi a inizio sessione |
| Rete | SSH diretto (non tunnel/proxy RunPod), porta 2222 |

Confronto diretto con l'ambiente M6 (pod RunPod, da
`docs/hardware_fingerprint_run1.md`): quel pod dichiarava **CPU
visibili** enormemente superiori alla **quota cgroup reale**
(mismatch strutturale dei pod condivisi, un rapporto ~9x). Qui non
c'è mismatch: `nproc`=32 e `cat /sys/fs/cgroup/cpu.max`=`max
100000` (nessun limite cgroup) coincidono con l'hardware fisico
reale — è proprio per questo che i bug di budget CPU trovati qui
(§5) sono di natura diversa da quelli di M6: non un cgroup che mente,
ma RLIMIT_CPU (che somma il tempo di TUTTI i thread) che si esaurisce
più in fretta perché ci sono davvero più core reali in parallelo.

### Software di base

| Componente | Versione |
|---|---|
| OS | Ubuntu 24.04.2 LTS |
| Kernel | 6.8.0-138-generic |
| Docker Engine | 29.7.2 (build a7dcaa6) |
| Docker Compose | v5.5.0 |
| NVIDIA Container Toolkit | 1.20.0 (runtime `nvidia` registrato via shim vast.ai, `/var/lib/vastai_kaalia/`) |

### Versioni dei componenti applicativi (dentro i container)

| Servizio | Versione | Fonte |
|---|---|---|
| CadQuery | 2.8.0 | pinnata, stessa di M1-M6 |
| OCP (wrapper OCCT) | 7.9.3.1.1 | risolta da pip, non pinnata esplicitamente |
| Python (executor/verifier/dashboard/stream-agent) | 3.11.16 | `python:3.11-slim` |
| Flowise | immagine `3.1.4` (il `package.json` interno riporta `3.1.2`, discrepanza nota del monorepo Flowise, non nostra) | pinnata, patchata localmente (§5.1) |
| Node.js (dentro Flowise) | v20.20.2 | dall'immagine Flowise |
| Ollama | 0.32.15 | `ollama/ollama:latest` al momento del pull |
| Qdrant | 1.19.0 (build `74f3e85b`) | `qdrant/qdrant:latest` al momento del pull |
| PrusaSlicer | 2.9.2+UNKNOWN | `billa05/prusacli:latest` |
| Modelli Ollama | `granite4:1b`, `granite-embedding:30m` | stessi di M6 |

Le immagini `:latest` (qdrant, ollama, open-webui, docker-socket-proxy)
non sono pinnate a uno sha esplicito in questo run — stesso stato
del compose ereditato da M6, non una regressione di M7, ma degno di
nota per la riproducibilità futura (vedi §7).

## 3. Dimensioni immagini e uso disco

| Immagine | Dimensione |
|---|---|
| `caliper-flowise:3.1.4-patched` | 9.11 GB |
| `caliper-verifier-executor` | 2.76 GB (CadQuery/OCP/VTK, coerente con la stima di `README.md` §8) |
| `caliper-stream-agent` | 399 MB |
| `caliper-dashboard` | 252 MB |
| `caliper-verifier` | 249 MB |
| **Totale immagini nostre** | **~12.8 GB** |
| `ollama/ollama` (base) | 8.43 GB |
| `flowiseai/flowise:3.1.4` (base, pre-patch) | 9.11 GB |
| `qdrant/qdrant` | 273 MB |
| `billa05/prusacli` | 1.54 GB |

Volumi persistenti (dati, non immagini) a fine sessione: `ollama_data`
3.33 GB (2 modelli), `qdrant_data` 1.04 GB, `open_webui_data` 278 MB,
`verifier_exec`/`flowise_data`/`stream_agent_state` sotto 1 MB (dati
di sessione minimi). Build cache Docker: 12.08 GB.

## 4. Timing osservato

| Fase | Durata |
|---|---|
| `docker compose build` (4 immagini, prima build assoluta, cache fredda) | ~2 min (dominato da `verifier-executor`: 111.9s solo per l'installazione di `cadquery==2.8.0`/OCP/VTK, poi 178s per l'export/unpack del layer) |
| Pull immagini pubbliche (ollama, flowise, qdrant, open-webui, docker-socket-proxy) | qualche minuto, dominato da `ollama/ollama` (~830MB) e `open-webui` |
| `ollama pull granite4:1b` + `granite-embedding:30m` | sotto i 2 minuti (rete della macchina, non misurato con precisione) |
| Bootstrap Flowise (account+API key+credential+import 3 chatflow) | pochi secondi, automatico (`flowise_bootstrap.py`) |
| E2E-2 completo (spec confermata → `/verify` → gauge-check GO → gauge-check NO-GO) via pipeline HTTP reale | pochi secondi (il collo di bottiglia è il gauge-check GO, ~37s wall — vedi §5.2) |
| Sessione completa (build → smoke test → isolamento → slicing → rimisura budget → suite TC-E2E-1..7) | ~2 ore |

## 5. I 6 bug — causa radice, fix, stato

Dettaglio completo con rosso/verde riprodotti in
[`docs/logbook_fase7.md`](logbook_fase7.md). Riepilogo:

| # | Bug | Causa radice | Fix | Stato |
|---|---|---|---|---|
| 1 | Flowise crasha a ogni boot Docker | `connect-sqlite3@0.9.17` nell'immagine ufficiale si aspetta un'istanza `sqlite3.Database` gia' aperta, Flowise gli passa una stringa (API cambiata upstream, bug non nostro) | `ops/docker/flowise/Dockerfile`, patch mirata + guard esplicita | ✅ committato |
| 2 | `prusaslicer` nel compose non slicava nulla | Entrypoint di default dell'immagine e' `/bin/bash`, mai verificato prima | `entrypoint: ["/app/prusa-slicer"]` | ✅ committato |
| 3 | `billa05/prusacli` non importa STEP | Manca `OCCTWrapper.so` nell'immagine (limite di terze parti) | Conversione STEP→STL con CadQuery prima dello slicing | ⚠️ workaround, non un fix all'immagine (fuori dal nostro controllo) |
| 4a | SIGSEGV su job CPU normale (`pitch=1.0`, mai successo su M6) | `CALIPER_AS_LIMIT_MB=2048` (il default "per docker-compose" nel codice, mai verificato in un container reale) insufficiente | `CALIPER_AS_LIMIT_MB=16384` (stesso valore di M6, qui pero' necessario anche per il caso BASE) | ✅ committato |
| 4b | SIGKILL su job CPU normale dopo il fix 4a | RLIMIT_CPU (somma tutti i thread) esaurito in ~2.4s con 32 core reali — mai successo su M6 sul caso base | `CALIPER_CPU_LIMIT_S=25` (era 10, worst-case misurato 13.71s × 1.5) | ✅ committato |
| 4c | Gauge-check GO killato al budget ereditato da M6 (140s) | Stesso meccanismo di 4b, sweep piu' pesante: worst-case reale 267.9s CPU (M6: 91.4s, quasi 3x) | `GAUGE_CHECK_CPU_LIMIT_SECONDS=405` (worst-case × 1.5) | ✅ committato |
| 5 | Chatflow L2.5 non raggiunge Ollama (`fetch failed`) | `baseUrl` del nodo ChatOllama era `http://localhost:11434/`, valido su RunPod (stesso pod), rotto tra container separati | `baseUrl` → `http://ollama:11434/`, nel chatflow versionato + istanza live (via PUT autenticato) | ✅ committato |
| 6 | Offset di `stream-agent` non persiste (`Read-only file system`) | Il sidecar `.offset` (fix C6, M5) veniva scritto dentro `/data/virtual_log`, montato `:ro` per scelta di sicurezza | Volume dedicato scrivibile `stream_agent_state`, path gia' overridabile via env (nessun cambio al codice) | ✅ committato |

Nessuno di questi 6 bug era osservabile in M6: Flowise girava nativo
(non Docker), non c'erano reti/mount container, e il pod RunPod aveva
un profilo di CPU/thread strutturalmente diverso (mismatch
cgroup/nproc, non un host bare-metal a 32 thread reali).

## 6. TC-E2E-1..7 nella topologia reale — confronto numerico con M6

| TC | M6 (RunPod, nativo) | M7 (Z8, Docker) | Nota |
|---|---|---|---|
| E2E-1 | 4/4 `tolerance_type` inventato prima del fix schema; dopo il fix, vuoto come atteso | Vuoto come atteso (schema gia' fixato in M6, ereditato) | Stesso comportamento "non indovinare" |
| E2E-2 | GO 0.305925mm³, NO-GO 20.158069mm³ | **GO 0.305925mm³, NO-GO 20.158069mm³ — identici** | Conferma che il collaudo geometrico e' indipendente dalla topologia/ambiente, come deve essere |
| E2E-2 wall-clock (gauge GO) | ~20.7s | ~37.1-37.5s | Piu' lento in wall-clock nonostante piu' core — plausibile: piu' contesa/overhead di scheduling tra 32 thread OS reali vs il profilo del pod, non approfondito oltre (fuori scope) |
| E2E-2 CPU totale (gauge GO) | 91.35s | 267.9s | ~2.9x — coerente con piu' parallelismo reale disponibile che il codice sfrutta (vedi §5, bug 4c) |
| E2E-8 rapporto CPU/wall | ~4.3-4.4x | ~7.1x | Piu' core reali disponibili → il codice ne usa di piu' in parallelo, aggregando piu' CPU-secondi per secondo di wall-clock |

## 7. Cosa resta esplicitamente aperto

- **`CALIPER_CPU_LIMIT_S=25`** e' calibrato sul caso base (`pitch=1.0`),
  non sul caso pesante di E2E-3 (`pitch=0.05`, worst-case CPU non
  misurato con un limite abbastanza alto da lasciarlo finire) — stessa
  situazione aperta gia' vista in M6 con `GAUGE_CHECK_CPU_LIMIT_SECONDS`
  prima di E2E-8. Decisione lasciata al supervisore/utente.
- **Immagini `:latest` non pinnate** (qdrant, ollama, open-webui,
  docker-socket-proxy): un pull futuro potrebbe tirare una versione
  diversa da quella qui misurata/validata, senza che nessun commit lo
  registri — stesso rischio gia' segnalato per Flowise prima di M5
  (ora pinnato), non ancora esteso agli altri.
- **`.github/workflows/publish-images.yml`** non pubblica ancora
  l'immagine Flowise patchata su GHCR (solo le 4 immagini di
  servizio applicative).
- **DELETE via Bearer API key sui chatflow Flowise** ha dato `403
  Forbidden` mentre il PUT via cookie di login e' passato — RBAC di
  Flowise 3.x non del tutto mappato, non approfondito.
- **Bench modello L2.5** (aperta da M6): non ripetuta qui, fuori scope
  M7.

## 8. Riferimenti

- Dettaglio tecnico completo (per-TC, rosso/verde): [`docs/logbook_fase7.md`](logbook_fase7.md)
- Harvest con manifest+sha256: `runs/20260825-133500-m7-run0/`
- Piano/criterio di accettazione: [`docs/piano_recupero.md`](piano_recupero.md) §M7
- Issue: [#19](https://github.com/danielesalpietro/caliper-cad/issues/19)
- PR mergiata: [#31](https://github.com/danielesalpietro/caliper-cad/pull/31)
- Confronto RunPod: [`docs/hardware_fingerprint_run1.md`](hardware_fingerprint_run1.md) (M6)
