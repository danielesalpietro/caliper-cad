"""
CALIPER — verifica manuale di virtual_memory.py (M4).

Stesso stile di verify_retry_policy.py: eseguibile a mano, output
incollato come prova diretta, nessuna rete/istanza esterna richiesta
(nessun Ollama/Qdrant/Flowise vivo in questo sandbox — vedi
docs/handoff_m4.md). Le fixture qui sotto sono REALI file su disco
(retry_log.jsonl + directory di casi Livello 6), non un mock della
logica sotto test — solo le dipendenze esterne (rete) sono fuori
scope, mai la logica che questo script verifica (stessa disciplina di
verify_retry_policy.py::test_main_loop).

Copre la regola anti-bias (criterio di accettazione M4, vedi
docs/logbook_fase4.md e issue #5):

1. spec_key(): stessa spec -> stessa chiave; feature/nominal/tolerance/
   strategia diversi -> chiave diversa (isolamento tra strategie).
2. should_exclude_strategy():
   A. Fallimenti virtuali sotto soglia -> mai esclusione.
   B. Fallimenti virtuali sopra soglia MA nessun dataset Livello 6
      disponibile -> mai esclusione (fail-open verso la generazione).
   C. Fallimenti virtuali sopra soglia, dataset presente ma SENZA un
      FAIL fisico sulla stessa strategia (solo PASS, o nessun caso
      corrispondente) -> mai esclusione (il cuore della regola
      anti-bias: un bug del verificatore, vedi [v14], non deve mai
      diventare pregiudizio permanente da solo).
   D. Fallimenti virtuali sopra soglia + almeno un FAIL fisico sulla
      stessa strategia -> esclusione.
   E. I fallimenti virtuali di una strategia DIVERSA non contano per la
      strategia sotto test (isolamento per spec_key, non solo per
      feature).

Uso: python verify_virtual_memory.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

from retry_policy import CHECKER_VERSION  # noqa: E402
from virtual_memory import (  # noqa: E402
    MIN_VIRTUAL_FAILURES_FOR_EXCLUSION,
    count_virtual_failures,
    should_exclude_strategy,
    spec_key,
)

THREAD_M6 = {"nominal": "M6", "tolerance_type": "diametrale", "thread_standard": "ISO 68-1", "l2_strategy": "free_code"}
THREAD_M8 = {"nominal": "M8", "tolerance_type": "diametrale", "thread_standard": "ISO 68-1", "l2_strategy": "free_code"}


def write_virtual_log(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_physical_case(dataset_dir, filename, feature, spec, esito):
    # Schema realistico del Livello 6 (docs/architettura-prototipo-mesh-llm.md):
    # NESSUN campo 'l2_strategy' — la misura fisica non registra quale
    # strategia L2 ha prodotto il codice, solo la geometria/spec e l'esito
    # (vedi GEOMETRY_KEY_FIELDS in virtual_memory.py). Rimosso qui
    # esplicitamente anche se presente nell'input, per non nascondere
    # accidentalmente il bug che ha motivato geometry_key().
    physical_spec = {k: v for k, v in spec.items() if k != "l2_strategy"}
    with open(os.path.join(dataset_dir, filename), "w", encoding="utf-8") as f:
        json.dump({"prompt": "test", "specifica_strutturata": {"feature": feature, **physical_spec}, "esito": esito}, f)


def virtual_fail_record(key, n, case_id=None, failure_class="geometric", checker_version=CHECKER_VERSION):
    # [M5, C5 — vedi docs/review_tecnica.md] failure_class/checker_version
    # aggiunti alla fixture esistente (dichiarato in docs/logbook_fase5.md,
    # come richiesto dall'handoff M5): senza questi campi, i record non
    # contano piu' per count_virtual_failures() (fail-open verso la
    # generazione, comportamento voluto per i record pre-M5 — ma qui la
    # fixture rappresenta un tentativo REALE di questa versione, quindi
    # deve portarli). case_id di default distinto per ogni "n" (comportamento
    # INVARIATO delle funzioni esistenti sotto, che simulano "n fallimenti
    # = n casi distinti" — vedi test_case_id_counting() per il caso nuovo
    # "stesso case_id, piu' tentativi").
    return {
        "case_id": case_id or f"case-{n}",
        "attempt": n,
        "spec_key": key,
        "outcome": "FAIL",
        "source": "virtual",
        "failure_class": failure_class,
        "checker_version": checker_version,
    }


def test_spec_key():
    ok = True
    k1 = spec_key("thread", THREAD_M6)
    k2 = spec_key("thread", dict(THREAD_M6))
    print("Stessa spec -> stessa spec_key:", k1 == k2)
    ok = ok and k1 == k2

    k3 = spec_key("thread", THREAD_M8)
    print("Nominal diverso (M6 vs M8) -> spec_key diversa:", k1 != k3)
    ok = ok and k1 != k3

    k4 = spec_key("thread", {**THREAD_M6, "l2_strategy": "sketch_first"})
    print("Strategia L2 diversa (free_code vs sketch_first) -> spec_key diversa:", k1 != k4)
    ok = ok and k1 != k4

    print("=== spec_key:", "OK" if ok else "FALLITO", "===\n")
    return ok


def test_below_threshold(tmp):
    log_path = os.path.join(tmp, "below_threshold.jsonl")
    key = spec_key("thread", THREAD_M6)
    write_virtual_log(log_path, [virtual_fail_record(key, 1)] * (MIN_VIRTUAL_FAILURES_FOR_EXCLUSION - 1))

    exclude, reason = should_exclude_strategy("thread", THREAD_M6, log_path=log_path, dataset_dir=None)
    print(f"Scenario A ({MIN_VIRTUAL_FAILURES_FOR_EXCLUSION - 1} FAIL virtuali, sotto soglia): exclude={exclude} — {reason}")
    ok = exclude is False
    print("=== Scenario A (sotto soglia):", "OK" if ok else "FALLITO", "===\n")
    return ok


def test_no_dataset(tmp):
    log_path = os.path.join(tmp, "no_dataset.jsonl")
    key = spec_key("thread", THREAD_M6)
    write_virtual_log(log_path, [virtual_fail_record(key, n) for n in range(MIN_VIRTUAL_FAILURES_FOR_EXCLUSION)])

    exclude, reason = should_exclude_strategy("thread", THREAD_M6, log_path=log_path, dataset_dir=None)
    print(f"Scenario B (sopra soglia, nessun dataset L6): exclude={exclude} — {reason}")
    ok = exclude is False

    exclude2, reason2 = should_exclude_strategy(
        "thread", THREAD_M6, log_path=log_path, dataset_dir=os.path.join(tmp, "does-not-exist")
    )
    print(f"Scenario B-bis (sopra soglia, dataset_dir inesistente): exclude={exclude2} — {reason2}")
    ok = ok and exclude2 is False

    print("=== Scenario B (nessuna corroborazione possibile, fail-open):", "OK" if ok else "FALLITO", "===\n")
    return ok


def test_dataset_without_corroborating_fail(tmp):
    log_path = os.path.join(tmp, "no_corrob.jsonl")
    dataset_dir = os.path.join(tmp, "l6_no_corrob")
    os.makedirs(dataset_dir, exist_ok=True)
    key = spec_key("thread", THREAD_M6)
    write_virtual_log(log_path, [virtual_fail_record(key, n) for n in range(MIN_VIRTUAL_FAILURES_FOR_EXCLUSION)])

    # Nessun caso fisico per M6 -> nessuna esclusione.
    exclude, reason = should_exclude_strategy("thread", THREAD_M6, log_path=log_path, dataset_dir=dataset_dir)
    print(f"Scenario C1 (dataset presente ma vuoto): exclude={exclude} — {reason}")
    ok = exclude is False

    # Un caso fisico PASS per M6 (contraddice il FAIL virtuale, non lo corrobora) -> nessuna esclusione.
    write_physical_case(dataset_dir, "case1.json", "thread", THREAD_M6, "PASS")
    exclude2, reason2 = should_exclude_strategy("thread", THREAD_M6, log_path=log_path, dataset_dir=dataset_dir)
    print(f"Scenario C2 (solo PASS fisico, nessun FAIL fisico): exclude={exclude2} — {reason2}")
    ok = ok and exclude2 is False

    print("=== Scenario C (regola anti-bias, nessun FAIL fisico corroborante):", "OK" if ok else "FALLITO", "===\n")
    return ok


def test_dataset_with_corroborating_fail(tmp):
    log_path = os.path.join(tmp, "corrob.jsonl")
    dataset_dir = os.path.join(tmp, "l6_corrob")
    os.makedirs(dataset_dir, exist_ok=True)
    key = spec_key("thread", THREAD_M6)
    write_virtual_log(log_path, [virtual_fail_record(key, n) for n in range(MIN_VIRTUAL_FAILURES_FOR_EXCLUSION)])
    write_physical_case(dataset_dir, "case1.json", "thread", THREAD_M6, "FAIL")

    exclude, reason = should_exclude_strategy("thread", THREAD_M6, log_path=log_path, dataset_dir=dataset_dir)
    print(f"Scenario D (FAIL virtuale sopra soglia + FAIL fisico corroborante): exclude={exclude} — {reason}")
    ok = exclude is True
    print("=== Scenario D (esclusione applicata):", "OK" if ok else "FALLITO", "===\n")
    return ok


def test_isolation_across_strategies(tmp):
    log_path = os.path.join(tmp, "isolation.jsonl")
    dataset_dir = os.path.join(tmp, "l6_isolation")
    os.makedirs(dataset_dir, exist_ok=True)
    key_m6 = spec_key("thread", THREAD_M6)
    key_m8 = spec_key("thread", THREAD_M8)

    # Tutti i fallimenti virtuali e il FAIL fisico appartengono a M8, non a M6.
    write_virtual_log(log_path, [virtual_fail_record(key_m8, n) for n in range(MIN_VIRTUAL_FAILURES_FOR_EXCLUSION)])
    write_physical_case(dataset_dir, "case1.json", "thread", THREAD_M8, "FAIL")

    exclude_m6, reason_m6 = should_exclude_strategy("thread", THREAD_M6, log_path=log_path, dataset_dir=dataset_dir)
    print(f"M6 (nessun fallimento proprio, solo M8 fallisce): exclude={exclude_m6} — {reason_m6}")
    ok = exclude_m6 is False

    exclude_m8, reason_m8 = should_exclude_strategy("thread", THREAD_M8, log_path=log_path, dataset_dir=dataset_dir)
    print(f"M8 (fallimenti propri, corroborati): exclude={exclude_m8} — {reason_m8}")
    ok = ok and exclude_m8 is True

    print("=== Scenario E (isolamento tra strategie diverse):", "OK" if ok else "FALLITO", "===\n")
    return ok


def test_case_id_counting(tmp):
    """[M5, C5, TC-M5-6] count_virtual_failures conta i CASI (case_id
    distinti), non i tentativi: 3 record FAIL dello STESSO case_id
    contano 1, non 3 — un solo run sfortunato (RetryBudget scrive un
    record per tentativo, fino a MAX_RETRY_ATTEMPTS) non deve da solo
    superare la soglia di esclusione."""
    log_path = os.path.join(tmp, "case_id_counting.jsonl")
    key = spec_key("thread", THREAD_M6)
    # Un solo case_id, 3 tentativi FAIL (stesso run) -> 1 caso, non 3.
    write_virtual_log(log_path, [virtual_fail_record(key, n, case_id="same-run") for n in range(1, 4)])
    count_same_run = count_virtual_failures(key, log_path=log_path)
    print(f"3 FAIL dello stesso case_id -> conteggio casi: {count_same_run} (atteso 1)")
    ok = count_same_run == 1

    # 2 case_id distinti -> 2 casi.
    log_path2 = os.path.join(tmp, "case_id_counting_distinct.jsonl")
    write_virtual_log(
        log_path2,
        [virtual_fail_record(key, 1, case_id="run-a"), virtual_fail_record(key, 1, case_id="run-b")],
    )
    count_distinct = count_virtual_failures(key, log_path=log_path2)
    print(f"2 case_id distinti -> conteggio casi: {count_distinct} (atteso 2)")
    ok = ok and count_distinct == 2

    print("=== Scenario F (conteggio per caso, non per tentativo):", "OK" if ok else "FALLITO", "===\n")
    return ok


def test_generation_failures_excluded(tmp):
    """[M5, C5, TC-M5-6] Record con failure_class="generation" (errore di
    generazione/JSON/schema, non un giudizio sulla geometria della
    strategia) non contano MAI per l'esclusione, indipendentemente da
    quanti ce ne sono."""
    log_path = os.path.join(tmp, "generation_excluded.jsonl")
    key = spec_key("thread", THREAD_M6)
    write_virtual_log(
        log_path,
        [virtual_fail_record(key, n, failure_class="generation") for n in range(1, 6)],  # 5 record, tutti "generation"
    )
    count = count_virtual_failures(key, log_path=log_path)
    print(f"5 record FAIL failure_class='generation' -> conteggio casi geometrici: {count} (atteso 0)")
    ok = count == 0

    exclude, reason = should_exclude_strategy("thread", THREAD_M6, log_path=log_path, dataset_dir=None)
    print(f"should_exclude_strategy con soli FAIL 'generation': exclude={exclude} — {reason}")
    ok = ok and exclude is False

    print("=== Scenario G (errori di generazione mai contati per l'esclusione):", "OK" if ok else "FALLITO", "===\n")
    return ok


def test_checker_version_scoping(tmp):
    """[M5, C5, TC-M5-6, P4] Record con checker_version DIVERSA dalla
    versione corrente non contano — un fix del checker (gauge_check.py/
    run_and_measure.py/measure_verdict.py, vedi retry_policy.py) azzera
    il pregiudizio accumulato dalla versione precedente invece di
    lasciarlo permanente (bug reale gia' successo una volta, [v14])."""
    log_path = os.path.join(tmp, "checker_version.jsonl")
    key = spec_key("thread", THREAD_M6)
    write_virtual_log(
        log_path,
        [virtual_fail_record(key, n, checker_version="old-buggy-version") for n in range(MIN_VIRTUAL_FAILURES_FOR_EXCLUSION)],
    )
    count_old = count_virtual_failures(key, log_path=log_path)  # default: versione CORRENTE
    print(f"{MIN_VIRTUAL_FAILURES_FOR_EXCLUSION} FAIL con checker_version vecchia -> conteggio con la versione corrente: {count_old} (atteso 0)")
    ok = count_old == 0

    count_matching_old = count_virtual_failures(key, log_path=log_path, checker_version="old-buggy-version")
    print(f"Stesso log, contato ESPLICITAMENTE con la vecchia versione: {count_matching_old} (atteso {MIN_VIRTUAL_FAILURES_FOR_EXCLUSION})")
    ok = ok and count_matching_old == MIN_VIRTUAL_FAILURES_FOR_EXCLUSION

    print("=== Scenario H (fix del checker azzera il pregiudizio):", "OK" if ok else "FALLITO", "===\n")
    return ok


def test_legacy_records_without_new_fields_excluded(tmp):
    """[M5, C5, TC-M5-6] Record scritti PRIMA di M5 (senza
    failure_class/checker_version, come li scriveva RetryBudget fino a
    M4) non devono rompere la lettura — semplicemente esclusi dal
    conteggio, fail-open verso la generazione (mai verso l'esclusione),
    comportamento conservativo invariato."""
    log_path = os.path.join(tmp, "legacy_records.jsonl")
    key = spec_key("thread", THREAD_M6)
    legacy_records = [
        {"case_id": f"legacy-{n}", "attempt": 1, "spec_key": key, "outcome": "FAIL", "source": "virtual"}
        for n in range(MIN_VIRTUAL_FAILURES_FOR_EXCLUSION + 3)
    ]
    write_virtual_log(log_path, legacy_records)
    count = count_virtual_failures(key, log_path=log_path)
    print(f"{len(legacy_records)} record legacy (pre-M5, senza failure_class/checker_version) -> conteggio: {count} (atteso 0)")
    ok = count == 0
    print("=== Scenario I (record legacy esclusi, nessun crash):", "OK" if ok else "FALLITO", "===\n")
    return ok


def main():
    ok = True
    ok = test_spec_key() and ok
    with tempfile.TemporaryDirectory() as tmp:
        ok = test_below_threshold(tmp) and ok
        ok = test_no_dataset(tmp) and ok
        ok = test_dataset_without_corroborating_fail(tmp) and ok
        ok = test_dataset_with_corroborating_fail(tmp) and ok
        ok = test_isolation_across_strategies(tmp) and ok
        ok = test_case_id_counting(tmp) and ok
        ok = test_generation_failures_excluded(tmp) and ok
        ok = test_checker_version_scoping(tmp) and ok
        ok = test_legacy_records_without_new_fields_excluded(tmp) and ok

    print("=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
