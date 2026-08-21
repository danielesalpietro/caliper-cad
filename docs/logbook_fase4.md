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

## Stato

- [ ] Schema del log del collaudo virtuale definito, separato dal Livello 6,
      con campo `source: virtual` obbligatorio
- [ ] Ingestion automatica dei risultati M1–M3 nel log virtuale
- [ ] Livello 7 esteso per interrogare entrambe le collezioni con
      discriminatore esplicito nei risultati recuperati
- [ ] Regola "N fallimenti virtuali richiedono almeno 1 riscontro fisico"
      implementata, non solo scritta come policy
- [ ] Verifica che l'agente scarti effettivamente una strategia nota come
      fallimentare, con caso di test reale (non solo teorico)
