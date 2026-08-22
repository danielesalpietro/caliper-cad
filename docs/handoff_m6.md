# Handoff — M6: Bring-up reale su RunPod (dentro il pod)

Prompt per la sessione Claude Code che gira **dentro il pod RunPod**
(`claude` nel web terminal del pod). Non è una sessione sandbox: hai
shell, GPU (RTX 3090 24GB), git e i servizi CALIPER in `localhost`.

**Tripwire:** sei nella directory `/workspace`. Se `ls /workspace` non
mostra `caliper-cad/`, `logs/`, `exec/`, `data/`, lo stack non è partito:
lancia `bash /opt/caliper/ops/runpod/start.sh` (idempotente) e aspetta
~60s prima di procedere. Il repo vivo è `/workspace/caliper-cad`
(branch `develop`, che contiene M5 mergiata + `ops/runpod/`).

## Ruolo di M6

Prima esecuzione **end-to-end viva** della storia del progetto:
L2.5 → conferma umana → L2 → `/verify` → `/gauge-check` (GO+NO-GO) →
retry loop → log virtuale → Livello 7 (Qdrant+Ollama). Fino a qui tutto
è stato verificato solo con fixture/mock (vedi `docs/review_tecnica.md`
C9): M6 esercita le giunture vive. Contesto completo:
`docs/piano_recupero.md` §3/M6 e i test case TC-E2E-1…9.

**Divergenza dichiarata (non un difetto):** il pod è un container senza
Docker-in-Docker — lo stack gira nativo (supervisord), non nella
topologia del compose. L'isolamento attivo di `verifier-executor`
(`network_mode: none`, socket proxy) è scope M7 sulla RTX 3090. Qui
verifichi il comportamento applicativo, non la topologia.

## Regole vincolanti (dal piano)

1. **Rosso→verde già fatto in M5**: qui non modifichi codice applicativo
   per far passare i test. Se un test E2E fallisce per un bug reale,
   documenta il rosso, fai il fix minimale, riesegui — e dichiaralo.
2. **Numeri per ambiente**: ogni budget (CPU/timeout) va **rimisurato
   qui** prima di essere considerato valido — questo pod ha **256 vCPU**,
   quindi `RLIMIT_CPU` del gauge-check (che somma il tempo di tutti i
   thread OCC) può bruciare il budget molto più in fretta della CI. È la
   ricalibrazione C8, obbligatoria (vedi TC-E2E-8).
3. **Harvest prima di spegnere**: nessun output si perde. `harvest.sh`
   dopo ogni TC-E2E completato, non solo alla fine (vedi sotto).
4. **Niente PR, niente merge**: commit+push sul branch della sessione,
   esito su issue #18. La verifica al gate la fa il supervisore.

## Passo 0 — smoke test e fingerprint

```bash
cd /workspace/caliper-cad && git log --oneline -1
ps aux | grep -E "ollama|qdrant|flowise|uvicorn|watcher" | grep -v grep
for p in 11434 6333 8600 8500 3000; do echo -n "porta $p: "; curl -s -o /dev/null -w "%{http_code}\n" --max-time 5 localhost:$p 2>/dev/null || echo giù; done
curl -s localhost:11434/api/tags | python3 -m json.tool | grep -E "name" || echo "modelli Ollama non ancora scaricati"
curl -s localhost:8600/health; echo
curl -s localhost:8500/health; echo
bash ops/runpod/env_fingerprint.sh | tee /workspace/caliper-runs/incoming/fingerprint-m6.json
```

Attesi: Ollama con `granite4:1b` + `granite-embedding:30m` (se mancano:
`ollama pull granite4:1b && ollama pull granite-embedding:30m`); verifier
e stream-agent `{"status":"ok",...}`; Flowise che risponde su :3000.

## Passo 1 — Flowise: chatflow L2.5 + L2, versionati

Flowise parte vuoto (DB su `/workspace/flowise`). Serve, una volta:

1. Apri Flowise (porta 3000 via RunPod *Connect*), registra l'account
   con `FLOWISE_USERNAME`/`FLOWISE_PASSWORD`, crea una **API key**.
2. Importa il chatflow L2.5 già versionato:
   `python services/flowise/import_chatflows.py` (con `FLOWISE_URL`,
   `FLOWISE_API_KEY` in env).
3. **Costruisci e versiona i chatflow L2** — oggi NON esistono nel repo
   (è il gap dichiarato in M3): un chatflow "L2 free-code" (Prompt
   Template → ChatOpenAI/GPT → output codice CadQuery) e, per la
   strategia `param_first` di M5, un "L2 param-first" (stesso schema, ma
   il template chiede **solo** i parametri numerici JSON —
   `major_diameter_mm`, `pitch_mm`, `engagement_length_mm`, `host_xy_mm`).
   Esportali e salvali in `services/flowise/chatflows/` (aggiorna
   `manifest.json`): diventano codice versionato, non config manuale.
   Nota: usa il nodo ChatOpenAI con `OPENAI_API_KEY` — evita ChatOllama
   per L2 (bug `fetch failed` documentato, rischio v10). Se vuoi un
   modello locale, servilo con vLLM (`ops/runpod/install_vllm.sh`) e
   puntaci ChatOpenAI via baseURL — ma per il bring-up base usa GPT.

## Passo 2 — Conferma umana L2.5 (Rischio #5, forma minima)

Prima di M6 la spec normalizzata da L2.5 andava a L2 senza conferma —
mitigazione obbligatoria mai implementata. Aggiungi a
`generate_and_verify.py` un flag `--confirm`: stampa la spec arricchita
e chiede `y/n` prima di chiamare L2. È l'unica modifica al codice
applicativo ammessa in M6, ed è un *aggiunta* di sicurezza, non un fix
di test. Test rosso→verde + script di verifica, come da metodo.

## Passo 3 — suite TC-E2E (input → output atteso, verificabile)

Esegui in ordine; salva stdout di ognuno in
`/workspace/caliper-runs/incoming/tc-eN.log` e `harvest.sh tc-eN` dopo
ognuno. Dettaglio in `docs/piano_recupero.md` §3/M6.

| ID | Comando/Input | Output atteso |
|---|---|---|
| E2E-1 | prompt `"foro filettato M6, tolleranza 0.3mm, passo 1.0"` al chatflow L2.5 vivo | JSON `feature:thread, nominal:M6, pitch:1.0, tolerance:0.3`, `tolerance_type`/`measured_as` vuoti |
| E2E-2 | spec confermata → `L2_STRATEGY=param_first generate_and_verify.py` su GPT reale | exit 0; record `outcome:PASS` in `retry_log.jsonl` con `spec_key` completa; STEP in `/workspace/exec/parts`; GO residuo ≤0.5mm³, NO-GO interferenza >1mm³ |
| E2E-3 | spec irrealizzabile (`pitch:0.05`) | ≤3 tentativi, directive nei tentativi 2+, `unrecoverable_virtual`, record collegati per `case_id` |
| E2E-4 | codice iniettato con foro Ø7 (bypass L2, POST diretto a `/verify`+`/gauge-check`) | NO-GO senza interferenza → FAIL finale (conferma live di C2) |
| E2E-5 | indicizza fixture L6 + log virtuale; **riavvia stream-agent** (`kill`+riparte da supervisord); `POST /reindex` | conteggio punti nelle 2 collezioni Qdrant invariato dopo il restart (conferma live di C6/id deterministici) |
| E2E-6 | 2 FAIL virtuali + 1 FAIL fisico fixture per la stessa chiave | orchestratore esce **prima** di chiamare Flowise (0 richieste nei log) |
| E2E-7 | gauge job con `GAUGE_CHECK_CPU_LIMIT_SECONDS` basso | TIMEOUT **strutturato via HTTP** con `preflight_diagnostics`+`last_checkpoint` |
| E2E-8 | sweep TC2 completo ×3, thread OCC fissati | tempi CPU e wall registrati; nuovo budget = worst-case×1.5, scritto in `docs/logbook_fase6.md` con fingerprint |
| E2E-9 (best-effort) | difetti sintetici larghezza 1.0→0.1mm in un foro noto | larghezza minima rilevata dallo sweep a 21 step, documentata |

**E2E-8 è il cuore di C8**: misura `time` (user+sys vs real) del
gauge-check qui, con e senza `OMP_NUM_THREADS=1`/`OPENBLAS_NUM_THREADS=1`
già impostati; il pod ha 256 vCPU, quindi verifica se OCC ignora quei
limiti e satura comunque i core (era l'ipotesi della review). Proponi il
budget portabile: o fissare i thread OCC, o spostare l'enforcement sul
timeout wall-clock esterno del watcher. **Non** cambiare il default di
produzione senza il numero misurato.

## A fine lavoro

- `docs/logbook_fase6.md` (stile fasi 1–5): per ogni TC-E2E input,
  comando, output reale, esito; i numeri di E2E-8 con fingerprint.
- Aggiorna la riga M6 nella tabella milestone di `docs/logbook.md`.
- **harvest finale**: `bash ops/runpod/harvest.sh m6-final --push` — deve
  uscire verde (tutti gli artefatti obbligatori presenti) e pushare
  `runs/<data>-m6-final/` sul branch. I chatflow esportati devono essere
  in `services/flowise/chatflows/`.
- Commento autosufficiente su issue #18 (cosa fatto, esiti TC-E2E,
  numeri C8, riserve oneste). Commit+push sul branch della sessione.
- **NON spegnere il pod** finché l'harvest non è verde e pushato: è la
  regola #4 del piano, i pod sono effimeri.
