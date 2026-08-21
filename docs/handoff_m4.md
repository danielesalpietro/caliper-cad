# Handoff — M4: Chiusura del loop di retrieval, firewall simulato/fisico

Prompt pronto per la sessione che implementa M4. Copiabile così com'è
come primo messaggio di una nuova sessione Claude Code.

**Nota di processo (leggi prima del resto):** M1/M2/M3 sono ora
**mergiati in `develop`** (PR [#7](https://github.com/danielesalpietro/caliper-cad/pull/7)/[#8](https://github.com/danielesalpietro/caliper-cad/pull/8)/[#11](https://github.com/danielesalpietro/caliper-cad/pull/11)) — il problema che aveva bloccato la sessione
M3 (partita da `develop` prima del merge, senza trovare l'handoff — vedi
issue [#9](https://github.com/danielesalpietro/caliper-cad/issues/9))
non dovrebbe più presentarsi: **parti direttamente da `develop`**. Se per
qualche motivo non vedi questo file lì, controlla
`git log --oneline -5` prima di procedere — potresti essere su un
branch/checkout sbagliato, non serve ricostruire il contesto alla cieca.
C'è anche ora una CI (`.github/workflows/regression.yml`) che riesegue
automaticamente 12 script di verifica ad ogni push/PR — usala per
validare il tuo lavoro prima di aprire una PR, non solo l'esecuzione a
mano.

---

Riprendi il progetto CALIPER (danielesalpietro/caliper-cad) — layer di
verifica deterministica per geometrie CAD generate da LLM.

## Ordine di lettura

1. `docs/logbook.md` — quadro generale, inclusa la sezione "Processo di
   handoff e CI" in fondo
2. `docs/logbook_fase4.md` — la milestone M4, oggetto di questo compito
   (include già un punto di partenza concreto — non ripartire da zero,
   vedi sotto)
3. GitHub issue [#5](https://github.com/danielesalpietro/caliper-cad/issues/5) (M4)
4. `services/orchestrator/retry_policy.py` — **già scrive un log
   strutturato con `source: "virtual"`** per ogni tentativo di retry
   (`retry_log.jsonl`, vedi `RetryBudget.record_attempt()`) — non è lo
   schema finale del Livello 6/7, ma è la forma di partenza più vicina
   che esiste oggi. Valuta se estenderlo prima di inventare un formato
   parallelo.
5. `services/stream-agent/app.py` — il Livello 7 esistente (adattato da
   NORTHSTREAM). Legge JSON da `DATASET_DIR` (un file per caso, vedi
   `case_to_text()`), li incorpora con Ollama, li indicizza in Qdrant.
   **Oggi non ha alcun campo `source`** nel payload indicizzato — legge
   una sola directory (il Livello 6), non ha nozione di una seconda
   collezione "virtuale". Questo è il gap centrale che M4 deve chiudere,
   non un dettaglio implementativo.

## Cosa NON esiste ancora (verifica prima di assumere)

- Nessun formato di dataset del Livello 6 popolato con casi reali — il
  bootstrap retroattivo menzionato nell'architettura non è stato fatto
  in questa sessione di lavoro (M1-M3 si sono concentrate sul Livello 3).
- Nessuna istanza Ollama/Qdrant viva verificata in queste milestone
  (stesso limite di sandbox già incontrato per Docker e Flowise in
  M1-M3) — se non disponibile anche qui, dichiaralo esplicitamente,
  stessa disciplina già applicata (non simulare con un mock ciò che è
  l'oggetto stesso della milestone, vedi `docs/logbook_fase3.md` per il
  precedente su Flowise).

## Compito: implementare M4

Deliverable, dalla checklist "Stato" di `docs/logbook_fase4.md`:

1. **Schema del log del collaudo virtuale** — decidi se estendere
   `retry_log.jsonl`/`RetryBudget` (punto 4 sopra) o costruire un formato
   dedicato. In entrambi i casi: campo `source: "virtual"` **obbligatorio
   e non opzionale** su ogni record, mai fuso con lo schema del Livello 6
   (quello resta riservato a misure fisiche reali — macchina, materiale/
   batch, data, Rischio #4).
2. **Ingestion automatica** dei risultati M1-M3 (gauge-check, sweep, retry)
   nel log virtuale.
3. **Livello 7 esteso** (`services/stream-agent/app.py`) per interrogare
   sia il Livello 6 sia il log virtuale, con `source` presente e
   distinguibile in ogni risultato recuperato — non solo nello schema di
   scrittura, anche in quello di lettura/risposta.
4. **Regola anti-bias**, implementata non solo documentata: N fallimenti
   nel solo collaudo virtuale non bastano da soli per escludere una
   strategia dal retrieval senza almeno un riscontro fisico (Livello 5).
   Vedi "Rischio aggiuntivo: bias auto-rinforzante" in
   `docs/logbook_fase4.md` per il perché (bug reale già successo una
   volta, `[v14]`, non un rischio teorico).
5. **Verifica con un caso di test reale**: l'agente scarta effettivamente
   una strategia nota come fallimentare — non solo in teoria.

## Vincoli già decisi, non rinegoziabili senza confronto esplicito con l'utente

- Due collezioni separate, mai fuse (Livello 6 fisico vs log virtuale) —
  è il punto centrale di tutta questa milestone, vedi "Il problema
  critico, non rimandabile" in `docs/logbook_fase4.md`.
- `source: virtual|physical` obbligatorio su ogni record recuperato dal
  Livello 7, non solo su quelli scritti.
- Niente esclusione automatica di una strategia da soli fallimenti
  virtuali — serve corroborazione fisica.

## A fine lavoro

- Aggiorna la checklist "Stato" in `docs/logbook_fase4.md` e la riga di
  M4 nella tabella milestone di `docs/logbook.md`.
- **Aggiungi i tuoi nuovi script di verifica a
  `.github/workflows/regression.yml`** — non lasciarli solo eseguibili a
  mano, è il punto di questo file.
- Commenta l'esito su GitHub issue [#5](https://github.com/danielesalpietro/caliper-cad/issues/5).
- Commit e push sul branch assegnato a questa sessione. NON aprire PR
  senza che te lo chieda esplicitamente.
- Se una dipendenza esterna manca (Ollama/Qdrant vivi, dataset Livello 6
  popolato), dichiaralo esplicitamente come bloccante e scopa il lavoro
  a ciò che è verificabile senza — stesso stile già usato finora:
  decisioni motivate, documentate, verificate per davvero, non assunte.
