# Hardware fingerprint — pod M6-run1

Raccolto durante il bench modelli L2.5 (`bench/bench_l25_models.py`),
2026-08-22 ~18:08 UTC. Necessario per reinterpretare i numeri del bench
(latenza, throughput) se l'hardware sottostante cambia — es. un pod
futuro su B200 renderebbe questi numeri non comparabili senza questo
riferimento.

## CPU

- **Modello**: AMD EPYC 7543 32-Core Processor — **2 socket fisici**
  (64 core fisici totali, 128 thread con SMT/hyperthreading attivo).
- **Visibili al container**: 128 (`nproc`), **quota cgroup reale
  ~13.6 vCPU-equivalenti** (`cfs_quota_us/cfs_period_us` =
  1360000/100000) — vedi `docs/logbook_fase6.md` Passo 1. Il pannello
  RunPod dichiara "16 vCPU": la quota reale (13.6) e' leggermente
  inferiore, non identica — differenza non spiegata, da tenere a mente
  (overhead di scheduling? arrotondamento del pannello?).
- **NUMA**: 2 nodi (node0: CPU 0-31,64-95; node1: CPU 32-63,96-127) —
  rilevante se in futuro si vincola l'affinita' con `taskset`: scegliere
  core dello STESSO nodo NUMA evita cross-node memory access.
- **Cache**: L1d/L1i 2MiB (64 istanze), L2 32MiB (64 istanze), L3
  **512MiB** (16 istanze, condivisa).
- **Frequenza**: max 3737.9 MHz, min 1500 MHz, boost abilitato.
- **Vulnerabilita' note**: nessuna "Vulnerable" — solo "Mitigation"/
  "Not affected" (mitigazioni Spectre/Meltdown attive, overhead atteso
  ma non quantificato qui).

## RAM

- **Visibile al container**: 503 GiB totali (`free -h`) — **quota
  cgroup reale: 61999996928 byte ≈ 57.7 GiB** (`memory.limit_in_bytes`,
  cgroup v1). Stesso pattern del CPU: visibile != reale, coerente coi
  62GB dichiarati dal pannello RunPod (questa volta il pannello
  combacia quasi esattamente con la quota cgroup, a differenza della
  CPU).

## GPU

- **Modello**: NVIDIA RTX A6000 (architettura Ampere), 1x.
- **VRAM**: 49140 MiB (~48GB) totali; durante il bench: 11302 MiB
  usati, 37369 MiB liberi (43% GPU util, 44% memory util con
  granite4:3b/qwen3:8b caricati via Ollama — **conferma che Ollama gira
  su GPU**, non CPU-only, rilevante per interpretare le latenze per
  modello riportate nel bench).
- **Driver**: 550.127.08, CUDA 12.4.
- **Clock**: SM 1935 MHz, memoria 7600 MHz (durante il bench).
- **Power**: limite 300W, consumo osservato durante il bench ~202W
  (P-state P2, non al massimo).
- **PCIe**: **Gen4 x16** (sia max che corrente — non degradato/
  limitato), bus `0000:A5:00.0`.

## Disco

- **Container overlay** (`/`, effimero): 60GB totali, 16MiB usati
  all'avvio di questa sessione.
- **Network Volume** (`/workspace`, persistente): `/dev/nvme1n1`,
  **60GB totali**, 10GB usati durante il bench (17%, 51GB liberi) —
  modelli Ollama scaricati finora: `granite4:1b` 3.3GB, `granite4:3b`
  2.1GB, `qwen3:8b` 5.2GB, `granite-embedding:30m` 62MB (~10.7GB
  totali). **Nota costo/capacita'**: con 4 modelli Ollama nella matrice
  del bench (`granite4:1b/3b`, `qwen3:8b`, `llama3.1:8b` ~4-5GB atteso)
  il totale resta ben dentro i 60GB del volume, ma **aggiungere modelli
  extra alla matrice in run futuri va controllato contro questo
  budget** (60GB totali, condivisi con repo/log/dataset/tutto il resto
  su `/workspace`).

## Nota per il confronto futuro (es. hardware diverso)

Questi numeri sono specifici di **questo** pod (RTX A6000 + 2x EPYC
7543, quota cgroup ~13.6 vCPU/~57.7GB RAM). Il bench `bench_l25_models.py`
non normalizza le latenze per l'hardware — se rieseguito su un pod con
GPU/CPU diversa (es. B200, molto piu' potente e con banda PCIe/HBM
radicalmente diversa), i tempi per-modello NON sono direttamente
comparabili con quelli di questo run senza questo fingerprint come
riferimento. Salvare sempre questo file (o equivalente) insieme a
qualunque bench di latenza/throughput.
