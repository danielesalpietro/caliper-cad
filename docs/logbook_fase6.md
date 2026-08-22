# Logbook — Fase 6 (M6): run1 — suite TC-E2E reale

Sessione esecutrice M6-run1, pod RunPod nuovo (immagine
`caliper-pod:git-de8d4fb`, tutto il lavoro del run0 già in `develop`).
Fonti vincolanti: `docs/handoff_m6_run1.md` (branch
`claude/handoff-m6-run1`), `docs/handoff_m6.md` (Passo 3, tabella
TC-E2E), `docs/report_run0.md` (contesto run0 e gate del re-run).
Supervisione: issue #18 + push su questo branch (`claude/m6-rerun-run1`).

Placeholder — popolato passo dopo passo, push incrementale dopo ogni
sezione completata.

## Pod

- IP/porta: `root@38.147.83.11:36196` (SSH).
- Dashboard: RTX A6000 x1, 16 vCPU dichiarati, 62GB RAM, template `8beo2j4pei`.
- **Nota preliminare**: `nproc`=128 visibili, ma cgroup reale
  `cfs_quota_us/cfs_period_us` = 1360000/100000 → **~13.6 vCPU
  equivalenti**, coerente coi 16 dichiarati dal pannello ma non con
  `nproc`. Stesso pattern del run0 (256 visibili vs ~27.2 reali,
  rapporto ~9.4x quasi identico) — sembra strutturale su RunPod, non
  un caso isolato. Rilevante per il Passo 1 (SIGSEGV).

## Passo 0 — verifica boot

(in corso)
