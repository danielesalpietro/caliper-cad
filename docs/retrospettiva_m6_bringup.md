# Retrospettiva — bring-up M6 su RunPod (fallimento di processo del supervisore)

Data: 2026-08-22. Autore: sessione di supervisione (issue #15/#18).
Registrata su richiesta esplicita dell'utente. Tono: onestà, non difesa —
lo stesso standard che la review (`docs/review_tecnica.md`) applica al
codice va applicato a chi ha condotto questo bring-up. **Ha condotto
male.**

## Cosa è andato storto

Il primo avvio reale del pod RunPod è stato un disastro di processo: si
è pagato tempo-GPU mentre si scoprivano, **uno alla volta e a runtime**,
problemi che dovevano essere noti prima di accendere qualunque cosa.
Nessuno era pronto per l'esecuzione. In sequenza:

1. **L'immagine `caliper-pod` non era mai stata avviata, nemmeno una
   volta.** Buildata e pubblicata su GHCR (verde in CI) — ma "build
   verde" ≠ "boot verde". La CI compilava l'immagine, non la eseguiva.
   Ogni difetto sotto è conseguenza diretta di questa scorciatoia.
2. **Variabili d'ambiente non passate al pod.** Dato per scontato che
   RunPod ereditasse Secrets/env: non lo fa — vanno dichiarate
   esplicitamente (nome=valore) nel template. `OPENAI_API_KEY`,
   `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `FLOWISE_*` tutte assenti al boot.
3. **`sshd` non partiva.** `start.sh` metteva le host key su
   `/workspace` (Network Volume MooseFS, che forza `0666`); sshd rifiuta
   una host key world-readable ed esce. Mai testato perché l'immagine
   non era mai stata avviata (punto 1).
4. **`claude` CLI "not found" nel pod.** Installata via `npm -g` in
   build, ma non nel PATH interattivo / install non verificata. Di nuovo:
   scoperto solo a runtime.
5. **Flowise non risultava tra i processi.** Non verificato al boot.
6. **Il supervisore non può raggiungere il pod.** Scoperto *dopo* aver
   proposto SSH come canale: l'egress della sandbox è TLS-only (banner
   SSH resettato) e le URL `*.proxy.runpod.net` sono bloccate da policy
   (403). Andava verificato **prima** di impostare un piano di controllo
   basato su SSH.
7. **Incidente di sicurezza indotto.** La gestione confusa ha portato
   l'utente a incollare una `OPENAI_API_KEY` in chat (ora da revocare) —
   una procedura corretta non avrebbe mai reso necessario esporre un
   segreto in conversazione.

## Causa radice

**Ho violato una regola che avevo scritto io stesso nel piano.**
`docs/piano_recupero.md` §3/M6 prescriveva una fase di preparazione:
*"testa in sandbox CPU-only tutto lo stack prima di accendere il pod …
il pod parte così da uno script già esercitato una volta, non da
tentativi live a ore fatturate."* Ho saltato quella fase: ho consegnato
un'immagine e istruzioni **basate su assunzioni**, non su un boot
osservato. Tutto il resto discende da qui.

Sotto, la stessa dinamica della criticità **C9** della review ("ogni
giuntura mai esercitata contiene un bug"), commessa dal supervisore che
quella review l'aveva scritta. Nessuna scusa.

## Azioni correttive (vincolanti per le prossime sessioni)

Alcune già applicate su `claude/review-tecnica` durante l'incidente
(marcate ✅), le altre da completare prima del prossimo bring-up.

1. ✅ **Gate "boot verde prima di affittare".** Il pod NON si affitta
   finché l'immagine non è stata avviata **end-to-end almeno una volta**
   in un ambiente non fatturato (job CI che fa `docker run` di
   `caliper-pod` su runner CPU, esegue `start.sh`, e asserisce: servizi
   su, `claude` presente, sshd su, report env). Vedi
   `.github/workflows/pod-boot-smoke.yml`. "Build verde" non basta.
2. ✅ **Self-test dell'immagine.** `start.sh` stampa al boot lo stato di
   ogni variabile attesa (`[ok]`/`[MANCA]`) e ha una modalità
   `--selftest` che esce non-zero se manca una variabile **bloccante**
   — un pod mal configurato fallisce subito e a voce alta, invece di
   restare acceso a pagamento in stato inutile.
3. ✅ **Auto-heal delle dipendenze scoperte rotte**: `claude` CLI
   reinstallata se assente + PATH npm globale; host key SSH in `/etc/ssh`
   (non sul volume MooseFS).
4. ✅ **Canale di controllo realistico documentato in anticipo.** Il
   supervisore non raggiunge il pod (TLS-only + 403 policy): il canale è
   il bus GitHub (`ops/runpod/agent_bus.sh`), che `start.sh` avvia
   automaticamente se `GITHUB_TOKEN` è presente — non un paste manuale
   reattivo.
5. ✅ **Checklist di setup del template esplicita** (nome=valore, con
   colonna "bloccante") in `ops/runpod/README.md`, con `OPENAI_API_KEY`
   e `ANTHROPIC_API_KEY` marcate bloccanti.
6. **Regola di processo, generale**: nessun handoff che dipende da
   infrastruttura viva viene consegnato "pronto" finché non è stato
   eseguito almeno una volta nell'ambiente più vicino disponibile. Un
   handoff basato su assunzioni è un handoff non finito. (Estende la
   regola #2 del piano — "verifica nell'ambiente reale più vicino".)
7. **Igiene dei segreti**: mai chiedere né indurre l'inserimento di
   chiavi in chat; solo env/Secret. Se un segreto finisce in
   conversazione → revoca immediata documentata.
8. **Disciplina di costo**: davanti a un blocco di configurazione su un
   pod acceso, la prima opzione offerta all'utente è
   `harvest.sh` + stop del pod (il volume persiste), non il debug live a
   ore fatturate.
9. ✅ **Runbook unico dei prerequisiti, dichiarato in anticipo.** Il
   difetto peggiore percepito dall'utente: una cascata di step manuali
   mai dichiarati, scoperti uno alla volta. Corretto con
   `docs/runbook_runpod.md` — Fase 0 (tutto a costo zero: chiavi, gate
   boot-smoke, parametri template) che deve essere verde PRIMA di
   accendere; Fase 1–3 (accensione, esecuzione, spegnimento) con la
   regola di costo. Nessuno step di setup va più scoperto a pod acceso.

## Esito della sessione esecutrice M6

> DA COMPLETARE quando arriva l'esito dell'altra sessione (in corso al
> momento di scrivere). Registrare: cosa è riuscita a eseguire davvero
> (quali TC-E2E), cosa no e perché, i numeri C8 se ottenuti, e se il pod
> è stato harvestato+spento correttamente. Onestà sull'esito, previsto
> come parziale/fallimentare — il valore di questa retrospettiva è che
> le azioni correttive sopra impediscano che si ripeta, non nascondere
> il risultato.
