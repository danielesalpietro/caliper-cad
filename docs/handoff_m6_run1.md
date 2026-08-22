# Handoff M6 — run1 (re-run sul pod RunPod)

Per la sessione esecutrice nel pod. Autosufficiente: si legge questo, si
lavora. Il contesto completo è in `docs/report_run0.md` (analisi run0 e
gate), la suite TC-E2E resta quella di `docs/handoff_m6.md` (Passo 3).
Supervisione: issue #18 + push sul branch di sessione.

## Cosa è cambiato dal run0 (tutto GIÀ su develop, merge `de8d4fb`)

Il run0 è stato bring-up e diagnosi (~3h, zero TC-E2E): questo run parte
da un ambiente in cui tutto ciò che allora era manuale è automatico.

1. **L'immagine è corretta e boot-validata**: Flowise e `claude` CLI
   installati con verifica hard in build (il `|| true` che li mascherava
   è la causa radice del run0, rimossa); host key SSH in `/etc/ssh`;
   `supervisorctl` abilitato su `/run/supervisord.sock`.
2. **Flowise si configura da solo al boot** (`flowise_bootstrap.py`,
   validato dal vivo contro flowise@3.1.4 reale): account admin, API key
   `caliper-orchestrator` (permissions RBAC), credential OpenAI
   `CALIPER-CAD`, **import dei 3 chatflow versionati** (L2.5 + i due L2
   costruiti nel run0) e aggancio della credential ai nodi ChatOpenAI.
   Esito in `/workspace/logs/flowise_bootstrap.log`; le credenziali
   finiscono in `/workspace/.caliper_env` (volume, persiste).
   **Il Passo 1 di handoff_m6.md (costruzione chatflow) è GIÀ FATTO** —
   non ricostruire nulla in UI.
3. **Fix `call_flowise_l2`** (risposta `json` del parser strutturato) e
   **flag `--confirm`** sono in develop, con regressione in CI.
4. **Mitigazioni SIGSEGV negli esecutori** (vedi Passo 1 sotto):
   `VTK_SMP_MAX_THREADS=1` di default; `CALIPER_AS_LIMIT_MB` e
   `CALIPER_STACK_LIMIT_MB` sovrascrivibili via env.

## Passo 0 — verifica del boot (2 minuti, niente debug a pagamento)

Il log di avvio del pod (`start.sh`) deve mostrare: `[ok]` su
`OPENAI_API_KEY` e `GITHUB_TOKEN`; `flowise-bootstrap: OK — account, api
key, chatflow importati, credential agganciata`; sshd su (se `PUBLIC_KEY`
impostata); agent_bus avviato. Poi:

```bash
source /workspace/.caliper_env
curl -s -H "Authorization: Bearer $FLOWISE_API_KEY" \
  http://localhost:3000/api/v1/chatflows | python3 -c \
  "import json,sys; print([f['name'] for f in json.load(sys.stdin)])"
# attesi 3 nomi: L2.5 + i due 'CALIPER - L2 Generation ...'
bash ops/runpod/env_fingerprint.sh   # salva il fingerprint del run1
```

Se un bloccante è `[MANCA]` o il bootstrap è FALLITO: **non debuggare a
ore fatturate** — annota, correggi l'env dal pannello RunPod e riavvia
il pod (regola di costo, `docs/runbook_runpod.md`).

Branch di sessione: `claude/m6-rerun-run1` da `origin/develop`, creato
subito, push incrementale costante (nel run0 ha salvato il lavoro
dall'incidente SSH — è la regola che ha funzionato meglio).

## Passo 1 — SIGSEGV (priorità assoluta, timebox 45 minuti)

Nel run0 `run_and_measure.py` crashava (SIGSEGV/deadlock) sul codice
param-first: il pod mostra 256 vCPU ma la quota cgroup reale è ~27; i
pool nativi si dimensionano sui core visibili e sfondano
`RLIMIT_AS=2GB`. Sequenza, in ordine, UN cambiamento alla volta, ogni
tentativo annotato nel logbook (comando esatto + esito):

1. **Riproduci**: `python3 services/orchestrator/verify_param_first.py`
   senza alcun override. Se ora è VERDE, la sola `VTK_SMP_MAX_THREADS=1`
   (già nel codice) era la causa: fermati, annota, vai al Passo 2.
2. Se rosso: `CALIPER_STACK_LIMIT_MB=2` (stack pthread da 8MB→2MB) e
   riprova.
3. Se rosso: aggiungi `CALIPER_AS_LIMIT_MB=6144` e riprova. Se serve
   questo, scrivi nel logbook la giustificazione (indebolisce il
   sandboxing per-job — accettabile sul pod M6, divergenza dichiarata).
4. In parallelo (a costo zero): `nproc`, `nproc --all`,
   `cat /sys/fs/cgroup/cpu.max` — riporta i numeri; se il template
   RunPod permette un cpuset ristretto, segnalalo come fix strutturale.
5. **Scaduto il timebox senza verde**: NON insistere. Prosegui con i
   TC-E2E che non richiedono l'esecutore (E2E-1, 3, 5, 6, 7) e riporta
   il SIGSEGV come blocco residuo di E2E-2/E2E-8, con tutti i dati
   raccolti.

Vincolo: qualunque override usato qui va riportato ANCHE nel comando dei
TC successivi che passano dall'esecutore, e nel logbook con fingerprint
— i numeri dipendenti dall'ambiente si rimisurano in QUESTO ambiente.

## Passo 2 — suite TC-E2E

La tabella E2E-1…9 di `docs/handoff_m6.md` (Passo 3) è invariata e
resta la fonte: input, output attesi, note su E2E-8/C8. Delta run1:

- E2E-1 parte subito (chatflow già vivi e agganciati).
- `harvest.sh tc-eN` dopo OGNI test case, `--push` incluso — mai
  accumulare solo sul pod.
- La conferma umana (E2E, `--confirm`) è già implementata e in CI: va
  solo esercitata dal vivo, non costruita.
- Stavolta `retry_log.jsonl` DEVE esistere a fine run (nel run0 il suo
  buco era il motivo dell'harvest rosso): è la prova che almeno una
  generazione reale è arrivata in fondo.

## Fine lavoro (invariata + lezioni run0)

1. `docs/logbook_fase6.md` stile fasi precedenti (per-TC: input,
   comando, output reale, esito; numeri E2E-8 con fingerprint) e riga
   M6 in `docs/logbook.md`.
2. `bash ops/runpod/harvest.sh m6-final --push` → VERDE prima di
   spegnere. Harvest rosso = pod acceso finché non si capisce cosa
   manca (o si documenta il perché).
3. Testo del commento di chiusura per issue #18 preparato e passato al
   supervisore/utente per l'ok prima della pubblicazione.

## Regole vincolanti (ereditate, con le cicatrici del run0)

- **Mai** terminare il supervisord principale per ricaricare env: usa
  `supervisorctl -s unix:///run/supervisord.sock restart <programma>`
  (l'incidente del run0 — kill al PID 1 — ha buttato giù il container).
- Segreti solo via env/`/workspace/.caliper_env`, mai in chat, mai come
  argomenti di processo.
- Fallimento reale → fix minimale, rosso→verde documentato, dichiara.
  Cambi ad architettura o a codice condiviso: proposta al supervisore
  su issue #18, non decisione unilaterale.
- Davanti a un blocco di configurazione: harvest + stop, non debug live.

## Addendum — bench matrice modelli L2.5 (a fine suite, PRIMA dello stop)

Motivato dall'esito di E2E-1 (invenzione deterministica di
`tolerance_type` con granite4:1b) e dalla volontà dell'utente di
provare più modelli. Timebox 60 minuti, DOPO l'ultimo TC-E2E e prima
dell'harvest finale:

    BENCH_MODELS="granite4:1b,granite4:3b,qwen3:8b,llama3.1:8b" \
    OPENAI_API_KEY=... python3 bench/bench_l25_models.py

Lo script legge template e schema DAL chatflow versionato (nessuna
copia), scarica da solo i modelli Ollama mancanti, aggiunge gpt-4o-mini
come riferimento API se la chiave c'è, e scrive
`bench_l25_summary.md` + `bench_l25_cases.csv` in
`/workspace/caliper-runs/incoming/bench-l25/` — da includere
nell'harvest finale. Metrica decisiva: `inventati% = 0`. La SCELTA del
modello resta all'utente sui numeri; nessun cambio di chatflow in
questo run.

Nota diagnostica del supervisore su E2E-1: la descrizione del campo
`tolerance_type` nello schema del parser ("one of: diametrale, ...")
NON offre l'opzione vuota — istruzione in tensione col template
("leave it empty"). Fix candidato economico, da provare DOPO il bench
(così il bench fotografa il comportamento attuale): aggiungere
"or empty string if not specified in the prompt" alla descrizione,
ripetere E2E-1, e se rosso→verde ri-esportare il chatflow sul branch.
