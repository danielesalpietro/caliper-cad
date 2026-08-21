# Handoff — Technical review critica dell'intero progetto (sola lettura)

Prompt pronto per la sessione di review. Copiabile così com'è come primo
messaggio di una nuova sessione Claude Code.

**Nota di ruolo, diversa da ogni handoff precedente (M1–M4):** questa
sessione **non implementa nulla**. M1–M4 sono tutte mergiate in
`develop` — il progetto ha ora abbastanza codice reale, decisioni prese
e riserve dichiarate da meritare una revisione tecnica critica
indipendente, non un'altra milestone. Il vincolo è duro: **nessuna
modifica a codice applicativo o configurazione** (`services/`,
`config/`, `docker-compose.yml`, `.github/workflows/`,
`presets.json`...). L'unico output scritto ammesso è un documento di
review nuovo sotto `docs/`.

---

Riprendi il progetto CALIPER (danielesalpietro/caliper-cad) — layer di
verifica deterministica per geometrie CAD generate da LLM. Parti da
`develop` (tutte le milestone M1–M4 sono lì).

## Obiettivo

Una revisione tecnica critica **onesta**, non una conferma. Non ti sto
chiedendo di validare quello che è stato fatto — ti sto chiedendo di
metterlo in discussione con argomenti tecnici concreti, con la stessa
disciplina già applicata nel progetto stesso (vedi la revisione critica
della proposta originale motore-fisico/GPU in `docs/logbook.md`, accolta
solo dopo un confronto con prove, non per fiducia). Dove una decisione
— presa dall'utente, da una sessione di implementazione, o dal
supervisore che ha scritto questo handoff — ti sembra sbagliata,
fragile, o basata su un'assunzione non verificata, dillo esplicitamente
e argomenta perché. Dove vedi un'alternativa tecnica migliore,
proponila con una valutazione onesta di pro/contro — non una lista di
"si potrebbe anche fare X" senza analisi.

## Ordine di lettura

1. `docs/architettura-prototipo-mesh-llm.md` — l'architettura originale
   (Livelli 1–8, elenco Rischi, "Fasatura del progetto" Fase A/Fase B).
2. `docs/logbook.md` — il filone "Ciclo di Collaudo Virtuale" (M1–M4):
   provenienza, revisione critica della proposta originale, mappatura
   terminologica, tabella milestone, sezione "Processo di handoff e CI".
3. `docs/logbook_fase1.md` … `docs/logbook_fase4.md` — dettaglio per
   milestone: obiettivo rivisto, revisioni critiche, stato reale.
4. `docs/handoff_m1.md` … `docs/handoff_m4.md` — cosa è stato chiesto a
   ciascuna sessione di implementazione, e i vincoli dichiarati non
   rinegoziabili.
5. Codice reale, per intero (non solo i diff): `services/verifier/executor/gauge_check.py`,
   `run_and_measure.py`, `watcher.py`, `services/verifier/app.py`,
   `services/orchestrator/generate_and_verify.py`, `retry_policy.py`,
   `virtual_memory.py`, `sketch_schema.py`, `sketch_compiler.py`,
   `services/stream-agent/app.py`, `docker-compose.yml`,
   `.github/workflows/regression.yml`, `config/gauges/`.
6. Issue aperte: [#4](https://github.com/danielesalpietro/caliper-cad/issues/4)
   (esecuzione end-to-end reale mai fatta), [#9](https://github.com/danielesalpietro/caliper-cad/issues/9)
   (processo di handoff), [#10](https://github.com/danielesalpietro/caliper-cad/issues/10)
   (proposta di analisi in sezione, non implementata) — e la cronologia
   delle PR mergiate (#7, #8, #11, #12, #13, #14) per vedere cosa è
   stato deciso e perché, non solo lo stato finale.

## Cosa mettere sotto esame — spunti concreti, non un elenco esaustivo

Non fermarti a questi, ma non ignorarli:

- **Tutta la verifica di M1–M4 è stata fatta contro fixture/mock, mai
  contro un'istanza viva** (nessun Flowise, Ollama o Qdrant reale in
  nessun sandbox usato finora — vedi le "riserve oneste" ripetute in
  ogni `logbook_faseX.md`). È un limite locale accettabile in ogni
  singola milestone, o è un rischio strutturale sull'intero impianto di
  verifica che nessuna milestone da sola rende visibile?
- **Soglie calibrate empiricamente**: `GAUGE_CHECK_CPU_LIMIT_SECONDS`
  (100s produzione / 400s CI), il worst-case 65.5s di TC2,
  `MIN_VIRTUAL_FAILURES_FOR_EXCLUSION = 2` in `virtual_memory.py`
  (giustificata "per coerenza" con `EARLY_EXIT_CONSECUTIVE_REPEATS`, non
  con dati propri). Sono numeri misurati o numeri presi in prestito per
  analogia?
- **Generazione degli ID punto Qdrant** in `services/stream-agent/app.py`
  (`abs(hash(...)) % (2**63)`, sia per i casi fisici che per il log
  virtuale) — verifica se è garantita la stabilità tra riavvii del
  processo, non darlo per scontato.
- **Le due collezioni Qdrant separate (fisico/virtuale)** — il design
  del firewall regge, ma verifica se `virtual_memory.py::geometry_key()`
  (che esclude `l2_strategy` per corroborare fisicamente) introduce un
  rischio diverso: corroborazione troppo larga tra strategie L2 diverse
  sulla stessa geometria.
- **Lo schema M1–M4 (milestone tecniche) convive con Fase A/Fase B
  (rischio) definito nell'architettura originale** — i due schemi sono
  ancora coerenti tra loro con quello che è stato effettivamente
  costruito, o uno dei due ha bisogno di essere rivisto?
- **Sequenzialità M1→M4** dichiarata "per dipendenza tecnica" — verifica
  se è davvero l'unico ordine possibile o se alcune parti erano
  parallelizzabili, e se le priorità relative (es. dataset Livello 6 mai
  popolato, mai affrontato in nessuna milestone) sono ancora quelle
  giuste oggi.
- **Ambito ristretto a "thread" per sketch-first (M3)** — quanto è
  vicino il resto (clearance_fit, snap_fit) a poter riusare lo stesso
  schema/compilatore, e cosa lo impedisce davvero?

## Vincoli

- **Nessuna modifica a codice o configurazione.** Solo lettura di tutto
  il repository. L'unico file nuovo consentito è il documento di review.
- Onestà anche scomoda: se una decisione del supervisore (chi scrive
  questo handoff) è sbagliata, dillo con lo stesso rigore con cui
  diresti che è sbagliata una decisione di chiunque altro.
- Le proposte alternative vanno valutate, non solo elencate: cosa si
  guadagna, cosa si perde, cosa richiederebbe per essere implementata.

## Deliverable

Un documento `docs/review_tecnica.md` con almeno:

1. **Executive summary** — 5-10 righe, il giudizio complessivo.
2. **Punti di forza** — onestamente, non per cortesia: cosa regge bene
   e perché.
3. **Criticità concrete** — con riferimento file:riga dove applicabile,
   non affermazioni generiche.
4. **Proposte alternative** — dove ha senso, con pro/contro espliciti.
5. **Domande aperte residue** — quelle che restano genuinamente aperte
   anche dopo la review, non solo quelle già note.

## A fine lavoro

- Commit **solo** di `docs/review_tecnica.md` (nessun altro file).
- Commenta l'esito per intero su GitHub issue
  [#15](https://github.com/danielesalpietro/caliper-cad/issues/15)
  (commento autosufficiente, stessa regola già in vigore da dopo
  l'issue #9 — non solo un file su un branch).
- Push sul branch assegnato a questa sessione. **Non aprire PR** senza
  che te lo chieda esplicitamente.
