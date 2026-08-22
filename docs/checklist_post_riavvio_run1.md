# Checklist personale — verifica post riavvio pod (run1)

Basata su cosa e' andato storto/sorprendente in questa sessione.
Obiettivo: capire in 2 minuti cosa e' sopravvissuto al riavvio e cosa
va rifatto, senza riscoprirlo da zero.

## 1. Connessione e ambiente base
- [ ] SSH funziona con la stessa chiave? (`PUBLIC_KEY` nel template
      dovrebbe reinnestarla — se no, richiede intervento web terminal
      come nel run0)
- [ ] `OPENAI_API_KEY`/`GITHUB_TOKEN` presenti nell'ambiente REALE di
      boot (`/proc/<pid-supervisord>/environ`, non la propria shell —
      falso allarme gia' preso 2 volte in questa sessione)
- [ ] `nproc` vs `cat /sys/fs/cgroup/cpu.max` (o v1
      `cfs_quota_us/cfs_period_us`) — la quota reale e' cambiata?
      (era ~13.6 vCPU equivalenti su questo pod specifico, contro 128
      visibili — un pod diverso puo' avere numeri diversi)

## 2. Flowise — non dare per scontato "RUNNING" = "funzionante"
- [ ] `supervisorctl status flowise` = RUNNING **non basta** — leggere
      `/workspace/logs/flowise_bootstrap.log` per "bootstrap completato"
- [ ] Se il bootstrap e' fallito su "Invalid Password": e' il bug gia'
      noto di `start.sh` (openssl rand -base64 non garantisce un
      carattere speciale) — se non ancora fixato in `develop`, il
      workaround e' rilanciare `flowise_bootstrap.py` a mano con
      `FLOWISE_PASSWORD="${FLOWISE_PASSWORD}Aa1!"`
- [ ] 3 chatflow presenti via API (`GET /api/v1/chatflows`)
- [ ] La credential OpenAI sui 2 chatflow L2 e' quella di **questa**
      istanza (non un ID orfano da un pod precedente) — controllare
      `node["data"]["credential"]` nel flowData, deve corrispondere a
      un ID reale in questa istanza
- [ ] `HTTP_SECURITY_CHECK="false"` su `[program:flowise]` in
      `supervisord.conf` — **verificare se e' sopravvissuto**: era una
      mia modifica runtime al file su `/workspace` (volume,
      dovrebbe persistere), MA se `start.sh` fa un
      `git checkout -B develop origin/develop` al boot (come nel
      run0), sovrascrive l'edit locale non committato. Se assente:
      senza, E2E-1/ogni chiamata a L2.5 (ChatOllama->localhost) fallisce
      con "Access to this host is denied by policy" — riapplicare a
      mano o verificare se il supervisore l'ha reso permanente nel repo

## 3. Branch e stato repo
- [ ] `git branch --show-current` sul pod — se e' tornato a `develop`
      (start.sh l'ha risettato), richeckoutare il branch di sessione
      con `git fetch && git reset --hard origin/<branch>` (mai
      ripartire da un checkout locale vuoto se il branch esiste gia'
      su GitHub)
- [ ] Eventuali fix runtime a `ops/runpod/supervisord.conf` (SSRF,
      GAUGE_CHECK_CPU_LIMIT_SECONDS ecc.) sono su `/workspace`
      (persistente) ma **non committati** — sopravvivono solo se
      `start.sh` non li sovrascrive con un checkout pulito

## 4. Esecutore (SIGSEGV) — RISOLTO in questa sessione, ma solo a runtime
**Aggiornato a fine sessione**: il fix e' stato trovato, validato E
applicato al servizio reale (non piu' solo isolamento) — E2E-2/4/7/8/9
tutti PASS attraverso la pipeline HTTP vera. Ma:
- [ ] `CALIPER_CPU_LIMIT_S`/`CALIPER_STACK_LIMIT_MB`/`CALIPER_AS_LIMIT_MB`/
      `GAUGE_CHECK_TIMEOUT_SECONDS` sono overridabili via env nel CODICE
      (merge di `claude/executor-knobs-run1`, committato) — questa parte
      sopravvive a un riavvio, e' su git.
- [ ] **MA** l'applicazione runtime su `[program:verifier-executor]` in
      `supervisord.conf` (`command=taskset -c 0-11 ...` +
      `CALIPER_STACK_LIMIT_MB="2"` + `CALIPER_AS_LIMIT_MB="16384"`) e'
      SOLO su `/workspace` (non committata, stessa categoria del fix
      SSRF di Flowise) — verificare se sopravvissuta, altrimenti
      riapplicarla (vedi `docs/logbook_fase6.md`, sezione "Tentativo 5"
      per i valori esatti). `taskset -c 0-11` e' specifico DEI CORE DI
      QUESTO POD (nodo NUMA 0) — su un pod diverso vanno
      ricontrollati (`lscpu`, sezione NUMA) prima di riusare "0-11" a
      memoria.
- [ ] `GAUGE_CHECK_CPU_LIMIT_SECONDS` ricalibrato a 140s (E2E-8,
      worst-case 91.35s misurato) — solo proposto, non applicato come
      default nel codice. `CALIPER_CPU_LIMIT_S` per `run_and_measure.py`
      invece NON ricalibrato affatto (resta 10s, E2E-3 lo dimostra).

## 5. Percorsi/env da NON riassumere a memoria
- [ ] `RETRY_LOG_PATH` — il default del modulo Python punta DENTRO il
      repo (`services/orchestrator/retry_log.jsonl`, gitignored), non
      a `/workspace/data/virtual_log/retry_log.jsonl` come si aspetta
      `harvest.sh`. Impostarlo sempre esplicitamente per qualunque
      chiamata reale a `generate_and_verify.py`
- [ ] Estrarre segreti da `/workspace/.caliper_env` SEMPRE con
      `. /workspace/.caliper_env` (sourcing shell), mai con
      grep/cut/tr manuale sul file grezzo — il file usa apici singoli
      (`export VAR='...'`), un parsing ingenuo include gli apici nel
      valore (mi e' successo 2 volte)

## 6. Harvest
- [ ] `bash ops/runpod/harvest.sh <tag>` (senza `--push` prima, per
      vedere lo stato) — verde/rosso, e perche', PRIMA di decidere se
      va bene continuare o serve un fix
