# Logbook — M4: Chiusura del loop di retrieval, firewall simulato/fisico

Vedi [`logbook.md`](logbook.md) per il quadro generale. Dipende da M3 e
dal Livello 6/7 esistenti (dataset congelato, retrieval — vedi
`architettura-prototipo-mesh-llm.md`).

## Obiettivo (mantenuto, con una condizione aggiunta)

Proposta originale: automatizzare l'ingestion dei risultati del collaudo
virtuale (successi e fallimenti) nel vector store; milestone: l'agente
scarta a priori strategie di sketch che storicamente falliscono nel
collaudo virtuale.

## Il problema critico, non rimandabile

Il documento di architettura è esplicito: il Livello 3 è "veloce/automatico
ma simulato", il Livello 5 (misura fisica) è "l'unica fonte che cattura
variabili reali", e il Livello 6 è "deliberatamente a valle della
validazione fisica, non a monte" (evita di inquinare la base di conoscenza
con risultati solo simulati). Il Rischio #8 impone che i FAIL fisici siano
registrati con lo stesso rigore dei PASS — è una regola operativa, non una
nota facoltativa.

Il collaudo virtuale (M1–M3) produce risultati della **stessa natura
epistemica del Livello 3 esistente**: simulati, non fisici. Se M4 li
scrivesse nella stessa collezione del Livello 6 senza distinguerli, il
firewall che il progetto ha costruito apposta si romperebbe silenziosamente
— un risultato simulato verrebbe recuperato dal Livello 7 come se fosse
verità fisica.

**Decisione:** due collezioni separate, non una.

- **Livello 6 (esistente):** solo risultati con misura fisica reale
  (macchina, materiale/batch, data — Rischio #4), invariato.
- **Log del collaudo virtuale (nuovo):** risultati M1–M3, marcati
  esplicitamente `source: virtual`, permanentemente distinguibili. Utile
  come segnale rapido (filtro/retry prima della stampa), mai come
  sostituto della validazione fisica.

Il Livello 7 (retrieval) può interrogare entrambe, ma il discriminatore
`source: virtual|physical` deve essere presente e non opzionale in ogni
record recuperato, non solo nello schema di scrittura.

## Rischio aggiuntivo: bias auto-rinforzante

Se il verificatore stesso ha un bug sistematico (già successo una volta,
v14: manifold valido ma dimensionalmente sbagliato del 200%), e il
retrieval scarta a priori le strategie che il collaudo virtuale segna come
FAIL, un bug del checker diventerebbe un pregiudizio permanente e
auto-confermato — mai corretto perché mai più testato.

**Mitigazione:** N fallimenti nel solo collaudo virtuale non bastano da
soli per escludere una strategia dal retrieval senza almeno un riscontro
nel Livello 5 (fisico) che lo corrobori — coerente con lo spirito del
Rischio #8 (il fisico resta l'arbitro ultimo), non solo per i PASS ma
anche per i pattern di FAIL usati per filtrare.

## Milestone (criterio di accettazione, con condizione)

L'agente consulta la memoria del collaudo virtuale prima di generare nuovo
codice, scartando strategie di sketch storicamente fallite — **a
condizione che** il campo `source` sia presente su ogni record e che la
mitigazione del bias auto-rinforzante sopra sia implementata, non solo
documentata.

## Punto di partenza già esistente (M2/M3, non ripartire da zero)

`services/orchestrator/retry_policy.py` (`RetryBudget.record_attempt()`)
scrive già, per ogni tentativo del loop L3→L2, un record JSONL
(`retry_log.jsonl`, ignorato da git — vedi `.gitignore`) con
`case_id`/`attempt`/`directive_used`/`outcome`/`outcome_error`/
`same_error_as_previous`/`source: "virtual"`/`timestamp`. **Questo è già
uno schema di log del collaudo virtuale con il discriminatore `source`
obbligatorio** — il primo item della checklist sotto è in parte già
soddisfatto nella forma, non nel contenuto (oggi registra solo l'esito
di `/verify` + `/gauge-check` per il loop di retry, non ancora un log
dedicato e interrogabile dal Livello 7). Valuta se estendere questo
formato invece di inventarne uno parallelo, o se le esigenze del
Livello 7 (retrieval semantico + filtro esatto) richiedono davvero una
struttura diversa — non assumerlo, verificalo.

## Stato

- [x] Schema del log del collaudo virtuale definito, separato dal Livello 6,
      con campo `source: virtual` obbligatorio — **estende** `retry_log.jsonl`/
      `RetryBudget` (retry_policy.py) invece di un formato parallelo, come
      valutato sopra: aggiunti `feature`/`spec_key` a ogni record (filtro
      esatto sui campi strutturati, coerente col resto del Livello 7),
      `source: "virtual"` era già obbligatorio dal M2
- [x] Ingestion automatica dei risultati M1–M3 (retry, e — quando la
      feature definisce `gauge_check_mode` — gauge-check/sweep, già
      collegati al loop da M3) nel log virtuale: ogni tentativo del loop
      `generate_and_verify.py` scrive un record via
      `RetryBudget.record_attempt()`, invariato dal M2 salvo i due campi
      aggiunti sopra
- [x] Livello 7 esteso (`services/stream-agent/app.py`) per interrogare
      **due collezioni Qdrant separate** (`caliper_l6_dataset` fisico,
      `caliper_virtual_log` nuovo — mai fuse) con discriminatore `source`
      esplicito in ogni risultato di `search_context()`, non solo nel
      testo incorporato. `docker-compose.yml` monta un secondo volume
      read-only (`VIRTUAL_LOG_PATH`) per il log virtuale nel container.
      **Riserva onesta (stessa di M1–M3):** nessuna istanza Ollama/Qdrant
      viva verificata in questa sessione (vedi "Cosa NON esiste ancora" in
      docs/handoff_m4.md) — codice scritto e sintatticamente verificato
      (`py_compile`), non eseguito contro un cluster reale
- [x] Regola "N fallimenti virtuali richiedono almeno 1 riscontro fisico"
      implementata in `services/orchestrator/virtual_memory.py`
      (`should_exclude_strategy()`), non solo scritta come policy — con
      una correzione reale trovata scrivendo il test end-to-end (vedi
      sotto): la corroborazione fisica confronta **solo la geometria**
      (`geometry_key`, senza la strategia L2), perché lo schema del
      Livello 6 non registra quale strategia ha prodotto il codice
      misurato — usare la chiave completa avrebbe reso la corroborazione
      irraggiungibile per costruzione
- [x] Verifica che l'agente scarti effettivamente una strategia nota come
      fallimentare, con caso di test reale (non solo teorico):
      `verify_virtual_memory_loop_gate.py` esercita `generate_and_verify.main()`
      per davvero (solo la rete è mockata, stesso stile di
      `verify_retry_policy.py`) con una fixture reale su disco
      (`retry_log.jsonl` + directory Livello 6) — la strategia con
      memoria corroborata viene scartata PRIMA di ogni chiamata a L2
      (0 chiamate registrate), una strategia diversa sulla stessa spec
      procede normalmente (gate non sovra-aggressivo). Aggiunto a
      `.github/workflows/regression.yml` insieme a `verify_virtual_memory.py`
      (6 scenari sulla regola anti-bias in isolamento)

**Limite dichiarato, non aggirato (stessa disciplina di M1–M3):** nessun
dataset Livello 6 reale esiste in questa sessione (vedi "Cosa NON esiste
ancora" in docs/handoff_m4.md) — la regola anti-bias e il Livello 7 sono
verificati con fixture scritte a mano che rispettano lo schema
documentato, non con dati fisici reali. Nessuna istanza Ollama/Qdrant/
Flowise viva nel sandbox: l'estensione del Livello 7 è scritta e
verificata sintatticamente, non eseguita end-to-end.
