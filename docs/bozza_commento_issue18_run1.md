<!-- Bozza commento per issue #18 — NON pubblicata, in attesa di ok dell'utente -->

## M6 run1 — bring-up automatico verificato, 5/9 TC-E2E eseguiti, executor ancora bloccato

Branch: [`claude/m6-rerun-run1`](https://github.com/danielesalpietro/caliper-cad/tree/claude/m6-rerun-run1) (parte da `develop`@`de8d4fb`, che include già tutto il run0). Logbook completo: [`docs/logbook_fase6.md`](https://github.com/danielesalpietro/caliper-cad/blob/claude/m6-rerun-run1/docs/logbook_fase6.md). Harvest finale **verde** (`retry_log.jsonl` popolato da generazioni reali per la prima volta nella milestone).

### Bootstrap automatico — verificato, con 2 bug reali trovati

- `flowise_bootstrap.py` è fallito al primo giro (`Invalid Password`): `start.sh` genera `FLOWISE_PASSWORD` con `openssl rand -base64 18`, che non garantisce mai un carattere speciale nell'output — Flowise lo rifiuta. Bypassato per questa sessione, **non fixato nel repo** (serve un ok esplicito, tocca `ops/runpod/start.sh`).
- Recupero manuale (bootstrap prima dell'import dei chatflow, ordine invertito rispetto al normale) ha lasciato la credential OpenAI non agganciata al primo giro — risolto rilanciando il bootstrap (idempotente) dopo l'import.
- **Nuovo bloccante mai visto nel run0**: Flowise 3.1.4 ha una protezione SSRF attiva di default che blocca `localhost` — necessario per l'architettura (Flowise→Ollama nello stesso container). Fix runtime applicato (`HTTP_SECURITY_CHECK="false"` su `[program:flowise]`), **da rendere permanente in `supervisord.conf`**.

### TC-E2E eseguiti

| TC | Esito |
|---|---|
| E2E-1 | Rosso→verde: `granite4:1b` inventava deterministicamente `tolerance_type` (4/4) nonostante il template lo vietasse esplicitamente — causa reale: lo schema del parser non offriva l'opzione vuota. Fix applicato al chatflow versionato, verificato 4/4 verde. |
| E2E-3 | Meccanica del retry loop corretta (2 tentativi, `unrecoverable_virtual`), ma il trigger è il SIGSEGV noto, non l'irrealizzabilità geometrica del pitch — non un test pulito finché l'executor non è fixato nella pipeline reale. |
| E2E-5 | PASS pulito — id Qdrant deterministici + offset persistito confermati con un riavvio reale di `stream-agent` via `supervisorctl`. |
| E2E-6 | PASS pulito — gate anti-bias verificato dal vivo (0 richieste a Flowise dopo 2 fallimenti virtuali + 1 fisico corroborante). |
| E2E-7 | Non eseguibile come specificato — l'handoff indica la leva sbagliata (`GAUGE_CHECK_CPU_LIMIT_SECONDS`, che è l'`RLIMIT_CPU` interno di `gauge_check.py`); il percorso "TIMEOUT strutturato" dipende da `GAUGE_CHECK_TIMEOUT_SECONDS`, hardcoded a 150s in `watcher.py`, non da env. |
| E2E-2 / E2E-4 / E2E-8 / E2E-9 | **Non tentati** — bloccati dal SIGSEGV/executor (vedi sotto). |

### SIGSEGV — causa confermata per `run_and_measure.py`, non ancora fixata nella pipeline reale

Diagnosi confermata: `run_and_measure.py` ha `RLIMIT_CPU=10s` **hardcoded** (non parametrico); con 128 CPU visibili (`nproc`) ma quota cgroup reale ~13.6 vCPU (`cfs_quota_us/cfs_period_us`), le librerie native dimensionano i pool di thread sul numero visibile, sommando >10s di CPU-time in una frazione di secondo. Con `taskset -c 0-11` (limitando l'affinità al reale) lo stadio **funziona** — prima volta in assoluto su questo tipo di pod.

**Ma il fix è stato validato solo in isolamento** (`verify_param_first.py` lanciato a mano con `taskset`), **mai collegato al servizio `verifier-executor`/`watcher.py` reale** che `generate_and_verify.py` chiama via HTTP — quel servizio gira sotto supervisord con il suo ambiente, senza `taskset`. È il blocco residuo principale per il prossimo run.

Scoperto anche un **secondo problema distinto**: con lo stesso `taskset`+override, `run_and_measure.py` passa ma il collaudo Go/No-Go successivo (`gauge_check.py`, chiamato da `verify_param_first.py`) crasha con SIGSEGV sotto le stesse condizioni — causa non ancora diagnosticata, probabilmente `CALIPER_AS_LIMIT_MB` insufficiente per il suo workload più pesante (sweep a 21 step).

Vedo che è già stato aperto `claude/executor-knobs-run1` — presumo indirizzi questi punti.

### Bench modelli L2.5 (addendum)

Motivato da E2E-1. 5 modelli su 15 casi, stesso template/schema del chatflow versionato:

| modello | inventati% | estratti_ok% | lat. media |
|---|---|---|---|
| granite4:1b (attuale) | **51.4** | 92.9 | 1.2s |
| granite4:3b | 5.7 | 95.2 | 0.98s |
| qwen3:8b | 2.9 | 100.0 | 18.94s |
| llama3.1:8b | 2.9 | 100.0 | 1.56s |
| gpt-4o-mini | **0.0** | 95.2 | 1.11s |

Solo `gpt-4o-mini` raggiunge `inventati=0`; tra i modelli locali `llama3.1:8b` è il migliore rapporto compliance/latenza. Scelta del modello lasciata all'utente — nessun cambio al chatflow live per questo motivo. Dettaglio completo (15×5 casi) in `runs/.../bench-l25/`.

### Riserve oneste

- `retry_log.jsonl` di default punta dentro il repo (`services/orchestrator/retry_log.jsonl`, gitignored), non a `/workspace/data/virtual_log/` come si aspetta `harvest.sh` — va sempre impostato esplicitamente, non solo qui.
- Il fix SSRF e la password Flowise sono modifiche a codice/config condiviso **non applicate al repo** in questo run — proposte qui, in attesa di ok.

Non spengo il pod finché non arriva conferma; harvest finale già verde e pushato in ogni caso.
