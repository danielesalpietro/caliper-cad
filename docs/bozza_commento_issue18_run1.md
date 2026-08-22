<!-- Pubblicata su issue #18: https://github.com/danielesalpietro/caliper-cad/issues/18#issuecomment-5382000393 -->

## M6 run1 — SIGSEGV risolto, 9/9 TC-E2E eseguiti

Branch: [`claude/m6-rerun-run1`](https://github.com/danielesalpietro/caliper-cad/tree/claude/m6-rerun-run1) (parte da `develop`@`de8d4fb`, che include tutto il run0; poi mergiato `claude/executor-knobs-run1`). Logbook completo: [`docs/logbook_fase6.md`](https://github.com/danielesalpietro/caliper-cad/blob/claude/m6-rerun-run1/docs/logbook_fase6.md). Harvest finale **verde** (`retry_log.jsonl` popolato da generazioni reali).

### Bootstrap automatico — verificato, 2 bug reali trovati e documentati

- `flowise_bootstrap.py` fallito al primo giro (`Invalid Password`): `start.sh` genera `FLOWISE_PASSWORD` con `openssl rand -base64 18`, che non garantisce mai un carattere speciale — Flowise lo rifiuta. Bypassato per sessione, **non fixato nel repo** (tocca `ops/runpod/start.sh`, serve ok esplicito).
- Recupero manuale (bootstrap prima dell'import chatflow) ha lasciato la credential OpenAI non agganciata al primo giro — risolto rilanciando il bootstrap (idempotente) dopo l'import.
- **Nuovo bloccante**: Flowise 3.1.4 ha una protezione SSRF attiva di default che blocca `localhost` — necessaria per l'architettura (Flowise→Ollama nello stesso container). Fix runtime (`HTTP_SECURITY_CHECK="false"` su `[program:flowise]`), **da rendere permanente in `supervisord.conf`**.

### SIGSEGV — risolto e applicato al servizio reale

**Causa confermata**: il pod dichiara 128-256 CPU visibili (`nproc`) ma la quota cgroup reale è ~13-27 vCPU-equivalenti (a seconda del pod). Le librerie native (OpenBLAS, OCC/VTK) dimensionano i propri pool di thread sul numero *visibile*, non sulla quota reale — combinato con `RLIMIT_CPU=10s` (hardcoded, ora overridable via `CALIPER_CPU_LIMIT_S` grazie al merge di `claude/executor-knobs-run1`) e `RLIMIT_AS` stretto, il processo veniva ucciso (SIGSEGV/SIGKILL a seconda del punto).

**Fix applicato al servizio `verifier-executor` reale** (runtime, via `supervisorctl`, mai il supervisord principale — la regola che ha evitato l'incidente del run0):
```
command=taskset -c 0-11 /opt/venv/bin/python watcher.py
environment=...,CALIPER_STACK_LIMIT_MB="2",CALIPER_AS_LIMIT_MB="16384"
```
`AS_LIMIT_MB` a 16384 (non 6144, che sbloccava solo `run_and_measure.py` ma non il `gauge_check.py` successivo, workload più pesante). **Non ancora nel repo** — proposto qui, non applicato al codice/config committata.

### TC-E2E — 9/9 eseguiti

| TC | Esito |
|---|---|
| E2E-1 | Rosso→verde: `granite4:1b` inventava deterministicamente `tolerance_type` (4/4) — causa reale: lo schema del parser non offriva l'opzione vuota. Fix applicato al chatflow versionato (`services/flowise/chatflows/l25-*.json`), verificato 4/4 verde. |
| E2E-2 | **PASS completo attraverso la pipeline HTTP reale** — prima generazione L2→verify→gauge-check GO/NO-GO riuscita in tutto lo sforzo M6. GO residuo 0.305925mm³ (≤0.5mm³), NO-GO interferenza 20.158069mm³ (>1mm³), STEP su disco, record PASS in `retry_log.jsonl`. |
| E2E-3 | Rifatto dopo il fix: meccanica del retry loop corretta (2 tentativi, `unrecoverable_virtual`, `case_id` collegato) — la causa ora è un `RLIMIT_CPU` genuino (10s, non ancora ricalibrato per `run_and_measure.py` come invece fatto per `gauge_check.py` in E2E-8), non più il bug SIGSEGV. |
| E2E-4 | PASS — conferma live di C2: codice iniettato con foro liscio Ø7mm, GO e NO-GO entrambi senza interferenza (`interference_volume_mm3:0.0`) → verificato nel codice (`generate_and_verify.py` riga 698) che questo produce `outcome_error:"gauge_check_nogo_no_interference"` → FAIL finale. |
| E2E-5 | PASS pulito — id Qdrant deterministici + offset persistito confermati con un riavvio reale di `stream-agent` via `supervisorctl`. |
| E2E-6 | PASS pulito — gate anti-bias verificato dal vivo (0 richieste a Flowise dopo 2 fallimenti virtuali + 1 fisico corroborante). |
| E2E-7 | PASS dopo aver corretto la leva sbagliata indicata dall'handoff (serve `GAUGE_CHECK_TIMEOUT_SECONDS`, non `GAUGE_CHECK_CPU_LIMIT_SECONDS`) — TIMEOUT strutturato confermato, `preflight_diagnostics`+`last_checkpoint` (step 2/21) entrambi popolati. |
| E2E-8 (C8) | **Ricalibrato dal vivo**: sweep TC2 completo x3, misurato invocando `gauge_check.py` direttamente (non via HTTP — il client curl misura solo se stesso, non il lavoro reale). Worst-case CPU: **91.352s** (wall ~20.7s, rapporto ~4.4x — coerente con `taskset -c 0-11`). Nuovo budget proposto: **140s** (worst-case×1.5). Il default attuale (100s) era già a rischio (margine <10%). |
| E2E-9 (best-effort) | Ridotto a 3 larghezze (1.0/0.5/0.1mm, timebox 30min) su un difetto sintetico radiale in un foro noto Ø7mm. Rilevato a 1.0mm (2.02mm³) e 0.5mm (1.02mm³), **non rilevato a 0.1mm** (0.226mm³, sotto la soglia epsilon 0.5mm³). Risoluzione minima: tra 0.1mm e 0.5mm, coerente con lo spacing dei 21 step (~0.4mm). |

### Bench modelli L2.5 (addendum)

Motivato da E2E-1. 5 modelli su 15 casi, stesso template/schema del chatflow versionato (bug nel bench script trovato e corretto: leggeva il file versionato come se fosse la risposta API):

| modello | inventati% | estratti_ok% | lat. media |
|---|---|---|---|
| granite4:1b (attuale) | **51.4** | 92.9 | 1.2s |
| granite4:3b | 5.7 | 95.2 | 0.98s |
| qwen3:8b | 2.9 | 100.0 | 18.94s |
| llama3.1:8b | 2.9 | 100.0 | 1.56s |
| gpt-4o-mini | **0.0** | 95.2 | 1.11s |

Solo `gpt-4o-mini` raggiunge `inventati=0`; tra i modelli locali `llama3.1:8b` è il migliore rapporto compliance/latenza. Scelta del modello lasciata all'utente — nessun cambio al chatflow live per questo motivo. Fingerprint hardware completo (CPU/RAM/GPU/PCIe) in `docs/hardware_fingerprint_run1.md`, necessario per reinterpretare le latenze su hardware diverso.

### Riserve oneste / da propagare al repo (non fatto qui, serve ok)

1. `ops/runpod/start.sh` — generazione `FLOWISE_PASSWORD` non garantisce un carattere speciale.
2. `ops/runpod/supervisord.conf` — `HTTP_SECURITY_CHECK="false"` su Flowise, `taskset -c 0-11`+`CALIPER_STACK_LIMIT_MB`/`CALIPER_AS_LIMIT_MB` su `verifier-executor` (i core esatti — 0-11 — sono specifici di **questo** pod, da rimisurare se il pod cambia).
3. `GAUGE_CHECK_CPU_LIMIT_SECONDS` → 140s (E2E-8) — non ancora applicato al default.
4. `CALIPER_CPU_LIMIT_S` per `run_and_measure.py` — non ricalibrato in questo run (solo `gauge_check.py` lo è stato), E2E-3 lo tocca.
5. `retry_log.jsonl` di default punta dentro il repo (gitignored), non dove si aspetta `harvest.sh` — va sempre impostato esplicitamente.

Non spengo il pod finché non arriva conferma; harvest finale già verde e pushato.
