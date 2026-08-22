# Report run0 RunPod — analisi completa e preparazione al re-run

Data: 2026-08-22. Autore: sessione di supervisione (issue #15/#18).
Fonti primarie: `docs/logbook_runpod_run0.md` (branch
`claude/m6-runpod-bringup-run0`, chiusura `25eda9a`),
`docs/retrospettiva_m6_bringup.md`, harvest `run0-close` (pushato dal
pod), validazione live del bootstrap Flowise in sandbox (questo branch,
`7ec7265`).

Questo documento risponde a due domande: **cosa è successo davvero nel
run0** (con cause radice e stato dei fix, commit per commit) e **cosa
deve essere vero prima di accendere il prossimo pod** (gate, checklist,
priorità). È il documento operativo del re-run: il runbook
(`docs/runbook_runpod.md`) resta la procedura generale, qui c'è il
delta specifico post-run0.

---

## 1. Esito del run0 in una riga

~3h di GPU: **zero TC-E2E eseguiti**, ma 3 bug reali chiusi, i chatflow
L2 finalmente esistenti e testati dal vivo, e la scoperta che il vero
blocco (SIGSEGV, non budget CPU) è più serio di quanto C8 anticipasse.
Harvest finale ROSSO (manca `retry_log.jsonl` — coerente: nessuna
generazione completata), tutto il resto salvato su git.

## 2. Cosa è andato bene (e perché va ripetuto)

| Fatto | Perché conta |
|---|---|
| Push incrementale costante sul branch di sessione | Ha reso indolore l'incidente SSH di metà sessione: nulla è andato perso. Regola da mantenere sempre. |
| Metodo rosso→verde anche sul pod (fix `call_flowise_l2`, `--confirm`) | I fix sono dimostrati, non dichiarati: il rosso osservato prima del fix è la prova che il test morde. |
| Chatflow L2 costruiti programmaticamente e versionati (non a mano in UI) | Riproducibili su qualunque istanza Flowise via `import_chatflows.py`; chiudono un gap dichiarato fin da M3. |
| Diagnosi SIGSEGV portata fino alla causa plausibile (cgroup vs nproc) prima di fermarsi | Il prossimo run parte da piste concrete, non da un crash anonimo. |
| Recupero dall'incidente supervisord senza panico | La separazione overlay/volume era capita: si sapeva cosa era perso e cosa no. |
| Timebox rispettato a fine run (chiusura ordinata + harvest, non debug infinito) | È la disciplina di costo del runbook applicata davvero. |

## 3. Cosa è andato male — causa radice → fix → stato

| # | Problema (run0) | Causa radice | Fix | Stato |
|---|---|---|---|---|
| 1 | Flowise (e `claude`) assenti dal pod | `\|\| true` nel Dockerfile mascherava il fallimento di `npm install` (node-gyp senza `build-essential`, python 3.12 senza `distutils`) | `build-essential` + `NPM_CONFIG_PYTHON=python3.11` + install separate + verifica HARD `--version` di entrambe le CLI (`61e9b1b`); `PUPPETEER_SKIP_DOWNLOAD` (`ec36fbd`) | ✅ nell'immagine, gate boot-smoke lo verifica a ogni publish |
| 2 | sshd giù: host key su MooseFS (0666) | Host key generate su `/workspace` (Network Volume che forza permessi larghi) | `ssh-keygen -A` in `/etc/ssh` (overlay) in `start.sh` (`52fed61`) | ✅ nell'immagine |
| 3 | Env non presenti nel pod | RunPod NON eredita nulla: ogni variabile va dichiarata nome=valore nel template | `start.sh` stampa `[ok]`/`[MANCA]` e `--selftest` esce non-zero sulle bloccanti (`7d60396`); tabella env con colonna "bloccante" in `ops/runpod/README.md` (`40444b1`) | ✅ diagnostica al boot; l'inserimento nel template resta (per natura) manuale |
| 4 | Account/API key/credential Flowise creati a mano via UI | Flowise 3.x ha account+JWT, le env `FLOWISE_USERNAME/PASSWORD` di 1.x/2.x sono inerti | `ops/runpod/flowise_bootstrap.py` (`9486425`+`7ec7265`): register→login→API key (con permissions RBAC)→credential OpenAI→patch chatflow→`/workspace/.caliper_env`. **Validato dal vivo** in sandbox contro flowise@3.1.4 reale, DB pulito: 3 giri consecutivi rc=0, import dei 3 chatflow run0 con la API key generata, patch credential verificata via GET, idempotenza piena | ✅ automatizzato e validato; lanciato da `start.sh` a ogni boot |
| 5 | Kill del supervisord PID-1 → container riavviato, SSH perso | Nessun canale di controllo runtime dei processi (mancava la sezione supervisorctl) | Socket `[unix_http_server]`/`[supervisorctl]` su `/run/supervisord.sock` (`61e9b1b`): `supervisorctl restart flowise` senza toccare PID 1 | ✅ nell'immagine |
| 6 | `call_flowise_l2` restituiva stringa vuota per i chatflow con Structured Output Parser | Flowise 3.1.4 mette il risultato in `data["json"]`, non `data["text"]` — mai testato dal vivo prima (dichiarato onestamente nel codice) | Fix minimale sul branch run0 (`206ee22`): `text` prioritario (free_code invariato), altrimenti `json.dumps(data["json"])`. Rivisto e approvato dal supervisore (issue #18) | ✅ sul branch run0, entra in develop col merge del run0 |
| 7 | **SIGSEGV in `run_and_measure.py`** | Vedi §4 | Mitigazioni committate (`0d5dbac`), piano di verifica in §4 | ⚠️ mitigato, DA VALIDARE sul pod (priorità #1 del re-run) |
| 8 | Harvest ROSSO (`retry_log.jsonl` mancante) | Nessuna generazione L2 completata (conseguenza di #7) | Nessun fix necessario: l'harvest ha fatto il suo lavoro segnalando l'assenza | ✅ comportamento corretto |

## 4. SIGSEGV — priorità #1 del re-run

**Sintomo** (run0): il sottoprocesso `run_and_measure.py` muore in
SIGSEGV durante la compilazione del codice param-first (taglio
elicoidale OCC); `OMP_NUM_THREADS=1`/`OPENBLAS_NUM_THREADS=1` e
`taskset` non risolvono (crash diverso: allocazione TLS fallita, o
deadlock). CadQuery funziona per operazioni semplici (box → PASS).

**Diagnosi**: il pod dichiara 256 vCPU (`nproc`, `sched_getaffinity`)
ma la quota cgroup reale è ~27.2 vCPU-equivalenti. Le librerie native
dimensionano pool di thread e stack sui core *visibili*: 256 thread ×
8MB di stack di default = 2GB di address space prenotati solo di stack,
che da soli saturano `RLIMIT_AS=2GB` (hardcoded fino a ieri). OpenBLAS
e OMP erano già a 1, ma **VTK (dipendenza di OCP) ha un pool SMP
proprio** non governato da quelle variabili — il candidato principale
per il secondo pool. **Non è il "budget CPU stretto" di C8**: è un
crash strutturale che blocca qualunque `/verify` reale.

**Mitigazioni già committate** (`0d5dbac`, smoke con cadquery reale
verde, default di produzione invariati):

- `VTK_SMP_MAX_THREADS=1` (setdefault, prima dell'import di
  cadquery/OCP) in entrambi gli esecutori;
- `CALIPER_AS_LIMIT_MB` (default 2048): RLIMIT_AS parametrico via env;
- `CALIPER_STACK_LIMIT_MB` (opzionale): RLIMIT_STACK → stack di default
  dei pthread (glibc), per far stare 256 thread in poco address space.

**Piano di verifica sul pod (in quest'ordine, timebox 45 min)**:

1. Riprodurre il rosso: `verify_param_first.py` senza override → deve
   ancora crashare? Se coi soli setdefault (VTK) è già verde, fermarsi:
   causa confermata, fix minimo.
2. Altrimenti: `CALIPER_STACK_LIMIT_MB=2` → riprova.
3. Altrimenti: `CALIPER_AS_LIMIT_MB=6144` → riprova. Se serve questo,
   annotare la giustificazione nel logbook (indebolisce il sandboxing
   per-job: accettabile sul pod M6, che è comunque fuori dalla
   topologia isolata di produzione — divergenza già dichiarata).
4. In parallelo, a costo zero: verificare se il template RunPod può
   fissare un cpuset ristretto (nproc reale) — chiude il problema alla
   radice per tutte le librerie, anche quelle non ancora note.
5. Scaduto il timebox senza verde: proseguire con i TC-E2E che non
   richiedono l'esecutore (E2E-1, 3, 4, 5, 6, 7, 9 parziali) e
   riportare il SIGSEGV come blocco residuo di E2E-2/E2E-8.

## 5. Ciò che l'overlay effimero perdeva — stato dopo i fix

La checklist del logbook run0 elencava cosa rifare a mano a ogni
riavvio. Confronto con l'immagine attuale (`claude/review-tecnica`):

| Voce (checklist run0) | Ora | Come |
|---|---|---|
| `apt-get install build-essential` | ✅ automatico | Nell'immagine (`61e9b1b`) |
| `npm install -g flowise@3.1.4` (15 min sul pod!) | ✅ automatico | Nell'immagine con verifica HARD; zero minuti a pod acceso |
| Host key sshd fuori da MooseFS | ✅ automatico | `start.sh` → `/etc/ssh` |
| Chiave SSH in `authorized_keys` | ✅ automatico | `PUBLIC_KEY` nel template (opz.) — `start.sh` la reinietta a ogni boot |
| Account Flowise + API key via UI | ✅ automatico | `flowise_bootstrap.py` a ogni boot (validato dal vivo) |
| Credential OpenAI + aggancio ai chatflow L2 via UI | ✅ automatico | idem |
| Import dei chatflow versionati | ✅ automatico | `start.sh` → `import_chatflows.py` con la API key del bootstrap |
| Ripopolare `/root/.caliper_env` | ✅ ridotto a 2 segreti | Il bootstrap scrive `/workspace/.caliper_env` (VOLUME, persiste); dall'esterno servono solo `OPENAI_API_KEY` e `GITHUB_TOKEN` nel template (+`ANTHROPIC_API_KEY` solo se `claude` gira nel pod) |
| `git config credential.helper store` | ✅ non più necessario | `start.sh` clona con il token nella remote URL (`x-access-token:$GITHUB_TOKEN@`): i push funzionano senza helper |

Residuo manuale irriducibile: compilare le Environment Variables del
template RunPod (nome=valore) la prima volta. Tutto il resto è codice.

## 6. Gate del re-run (in ordine, nessuno saltabile)

1. **Merge PR #23** (`claude/review-tecnica` → `develop`): contiene
   Dockerfile corretto, bootstrap Flowise validato, mitigazioni
   SIGSEGV, runbook, retrospettiva completata e questo report.
   Decisione dell'utente.
2. **Merge del branch run0** (`claude/m6-runpod-bringup-run0`):
   contiene i chatflow L2, il fix `call_flowise_l2` (approvato),
   `--confirm`, il logbook run0. Se si preferisce, può essere mergiato
   in `develop` dopo il #23 (nessun conflitto atteso sui file
   applicativi: il run0 non tocca `ops/runpod/`).
3. **`publish-images.yml` verde** sul merge → immagine
   `ghcr.io/danielesalpietro/caliper-pod:git-<sha>`.
4. **`pod-boot-smoke.yml` verde** sulla stessa immagine → "boot verde",
   non solo "build verde". Questo è il via libera (runbook, Fase 0.2).
5. **Template RunPod aggiornato**: immagine `:git-<sha-boot-verde>`,
   env come da `ops/runpod/README.md` (bloccanti: `OPENAI_API_KEY`,
   `GITHUB_TOKEN`; `FLOWISE_PASSWORD` opzionale — se assente, il
   bootstrap la genera e la scrive in `/workspace/.caliper_env`;
   `PUBLIC_KEY` per SSH; `ANTHROPIC_API_KEY` solo se serve `claude`
   nel pod).
6. **Accensione** (qui inizia il costo): il log di boot deve mostrare
   tutti `[ok]`, `flowise-bootstrap ... bootstrap completato`, import
   chatflow, sshd su. Se un bloccante è `[MANCA]` → correggere l'env e
   riavviare, non debuggare a pagamento.

## 7. Sequenza del re-run (a pod acceso)

1. **SIGSEGV per primo** (§4, timebox 45 min) — sblocca o delimita
   tutto il resto.
2. Suite **TC-E2E-1…9** (handoff M6 / issue #18), `harvest.sh <tag>
   --push` dopo OGNI test case, non solo alla fine.
3. `docs/logbook_fase6.md` nel formato delle fasi precedenti (per-TC:
   input, comando, output reale, esito) + riga M6 in `docs/logbook.md`.
4. Chiusura: `harvest.sh m6-final --push` VERDE (con `retry_log.jsonl`
   stavolta) → stop del pod dal pannello. Il volume persiste.

Regole di condotta invariate (retrospettiva, azioni 6–8): push
incrementale sempre; mai kill del supervisord principale
(`supervisorctl` esiste apposta); segreti solo via env/Secret; davanti
a un blocco di configurazione la prima opzione è harvest+stop.

## 8. Cosa resta esplicitamente aperto

- **SIGSEGV**: mitigato con ipotesi solida ma NON ancora validato
  sull'ambiente che lo produce (il pod). Fino ad allora è un rischio
  aperto, non un fix.
- **Numeri C8** (budget CPU reale per sweep su quel pod): da misurare
  dopo il fix SIGSEGV — la ricalibrazione resta necessaria, il crash
  la precedeva.
- **`FLOWISE_USERNAME/PASSWORD` in `supervisord.conf`**: inerti su
  Flowise 3.x, lasciate finché un pod non conferma che nulla le legge
  (annotato nel .conf).
- **E2E-2/E2E-8** dipendono interamente dallo sblocco del punto 1.
- **Commento di stato su issue #18**: da pubblicare quando l'utente
  conferma (regola del run0: nessun commento pubblico senza ok).
