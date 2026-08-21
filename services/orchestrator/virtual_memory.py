"""
CALIPER — memoria del collaudo virtuale, con corroborazione fisica (M4).

Vedi docs/logbook_fase4.md ("Il problema critico", "Rischio aggiuntivo:
bias auto-rinforzante") e GitHub issue #5. Questo modulo e' il punto
dove si applicano DAVVERO i due vincoli non rinegoziabili della
milestone, non solo dove sono documentati:

1. Il log del collaudo virtuale (retry_log.jsonl, source: "virtual",
   vedi retry_policy.py) e il dataset del Livello 6 (source fisico,
   misura reale) restano due sorgenti SEPARATE qui dentro: mai lette
   insieme in un'unica collezione, mai fuse in un unico record.
2. should_exclude_strategy() e' l'implementazione della regola
   anti-bias: N fallimenti nel SOLO collaudo virtuale non bastano mai,
   da soli, a escludere una strategia — serve almeno un FAIL fisico
   (Livello 5/6) sulla STESSA strategia a corroborarli. Un FAIL virtuale
   senza alcun riscontro fisico resta un segnale, non un'esclusione:
   se il verificatore ha un bug sistematico (gia' successo, v14), questa
   e' la difesa contro il pregiudizio permanente auto-confermato.

"Strategia" qui e' un concetto DETERMINISTICO, a soglie/campi fissi —
mai un LLM, mai una similarita' semantica: coerente con "filtro esatto
sui campi strutturati" gia' previsto per il Livello 7 in
docs/architettura-prototipo-mesh-llm.md (differenza voluta tra "M6
tol.0.3" e "M8 tol.0.3", che un embedding testuale confonderebbe).
"""

import json
import glob
import os

from retry_policy import CHECKER_VERSION, RETRY_LOG_PATH

# Deciso qui, coerente con EARLY_EXIT_CONSECUTIVE_REPEATS in
# retry_policy.py (stessa soglia gia' usata per "lo stesso errore due
# volte e' spreco di calcolo/segnale sufficiente per fermarsi") — non
# un nuovo numero indovinato.
MIN_VIRTUAL_FAILURES_FOR_EXCLUSION = 2

# Campi che compongono l'identita' di una "strategia" — deliberatamente
# un sottoinsieme fisso della spec (non l'intera spec: retry_context,
# timestamp, note libere ecc. non ne fanno parte). "l2_strategy" incluso
# perche' free_code e sketch_first sono percorsi di generazione diversi
# (vedi generate_and_verify.py) — un fallimento nell'uno non implica
# nulla sull'altro.
#
# [M5, C5 — vedi docs/review_tecnica.md] "tolerance"/"pitch" aggiunti:
# prima di M5 "M6 tol 0.05" e "M6 tol 0.5" (o M6xi passo 1.0 e M6xi passo
# 0.75) collassavano sulla stessa spec_key — in contraddizione con
# l'argomento fondante del filtro esatto del Livello 7 (differenza voluta
# tra due specifiche numericamente diverse, vedi
# docs/architettura-prototipo-mesh-llm.md). Entrambi campi top-level
# della spec L2.5 (vedi l'esempio "pitch": 1.0 in
# docs/architettura-prototipo-mesh-llm.md), non del preset.
SPEC_KEY_FIELDS = ("feature", "nominal", "tolerance_type", "thread_standard", "l2_strategy", "tolerance", "pitch")

# Sottoinsieme di SPEC_KEY_FIELDS usato SOLO per la corroborazione fisica
# (has_physical_failure) — senza "l2_strategy". Lo schema del Livello 6
# (docs/architettura-prototipo-mesh-llm.md) non registra QUALE strategia
# L2 ha prodotto il codice misurato fisicamente, solo la geometria/spec e
# l'esito: una misura fisica corrobora "questa geometria e' davvero
# fuori tolleranza", non "questa strategia L2 specifica lo e'". Usare la
# spec_key completa (con l2_strategy) qui renderebbe la corroborazione
# fisica IRRAGGIUNGIBILE per costruzione (nessun caso L6 avrebbe mai
# l2_strategy valorizzato) — bug reale trovato scrivendo
# verify_virtual_memory_loop_gate.py con una fixture L6 realistica
# (senza l2_strategy), non solo teorico.
GEOMETRY_KEY_FIELDS = tuple(f for f in SPEC_KEY_FIELDS if f != "l2_strategy")


def _key_from_fields(feature: str, spec: dict, fields: tuple) -> str:
    parts = {"feature": feature}
    for field in fields:
        if field == "feature":
            continue
        parts[field] = spec.get(field)
    return json.dumps(parts, sort_keys=True, ensure_ascii=False)


def spec_key(feature: str, spec: dict) -> str:
    """Chiave esatta, stabile e ordinabile che identifica una strategia
    (feature + spec nominale/tolleranza + strategia L2) — usata per
    contare i fallimenti virtuali e per decidere COSA escludere. JSON con
    chiavi ordinate invece di una tupla/hash: leggibile direttamente nel
    log (utile per debug manuale, stesso stile di directive_text_for in
    retry_policy.py che preferisce testo ispezionabile a un enum opaco)."""
    return _key_from_fields(feature, spec, SPEC_KEY_FIELDS)


def geometry_key(feature: str, spec: dict) -> str:
    """Come spec_key(), ma senza 'l2_strategy' — vedi GEOMETRY_KEY_FIELDS.
    Usata SOLO per confrontare con il Livello 6 (corroborazione fisica),
    mai per contare i fallimenti virtuali (quelli restano specifici per
    strategia)."""
    return _key_from_fields(feature, spec, GEOMETRY_KEY_FIELDS)


def _iter_virtual_records(log_path: str):
    if not log_path or not os.path.isfile(log_path):
        return
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def count_virtual_failures(key: str, log_path: str = RETRY_LOG_PATH, checker_version: str | None = None) -> int:
    """Quanti CASI (case_id distinti, non tentativi) del collaudo
    virtuale hanno almeno un FAIL "geometric" per questa spec_key, su
    TUTTI i case_id (non solo il case corrente) — e' la memoria storica
    attraverso run diversi, non il budget di un singolo tentativo (quello
    resta RetryBudget.should_stop_early(), scopo diverso: fermare UN loop
    in corso, non informare i loop futuri).

    [M5, C5 — vedi docs/review_tecnica.md] Due correzioni rispetto a
    prima di M5:
    1. Conta i CASI, non i tentativi: RetryBudget scrive un record per
       OGNI tentativo dello stesso case_id (fino a MAX_RETRY_ATTEMPTS) —
       un solo run sfortunato poteva quindi superare da solo la soglia
       di esclusione, anche se rappresenta un unico episodio, non
       molteplici conferme indipendenti.
    2. Solo i record failure_class == "geometric" contano: un errore di
       generazione/JSON/schema (failure_class == "generation") non dice
       nulla sulla strategia geometrica — e solo i record della versione
       CORRENTE del checker (checker_version) contano, cosi' un fix del
       checker (es. [v14]) azzera il pregiudizio invece di lasciarlo
       permanente (P4). Record pre-M5 (senza questi campi) sono esclusi
       dal conteggio — fail-open verso la generazione, mai verso
       l'esclusione, comportamento conservativo invariato."""
    current_version = checker_version if checker_version is not None else CHECKER_VERSION
    failing_case_ids = set()
    for r in _iter_virtual_records(log_path):
        if r.get("source") != "virtual" or r.get("spec_key") != key or r.get("outcome") != "FAIL":
            continue
        if r.get("failure_class") != "geometric":
            continue
        if r.get("checker_version") != current_version:
            continue
        failing_case_ids.add(r.get("case_id"))
    return len(failing_case_ids)


def _iter_physical_cases(dataset_dir: str):
    if not dataset_dir or not os.path.isdir(dataset_dir):
        return
    for path in sorted(glob.glob(os.path.join(dataset_dir, "*.json"))):
        try:
            with open(path, "r", encoding="utf-8") as f:
                yield json.load(f)
        except (OSError, json.JSONDecodeError):
            continue


def has_physical_failure(feature: str, spec: dict, dataset_dir: str) -> bool:
    """Vero se il dataset del Livello 6 (misura fisica reale, MAI il log
    virtuale — vedi docstring del modulo) contiene almeno un caso FAIL
    per la stessa GEOMETRIA (geometry_key, non spec_key — vedi
    GEOMETRY_KEY_FIELDS: il Livello 6 non registra la strategia L2).
    Il Livello 6 non ha oggi un campo 'source' (e' per costruzione
    sempre fisico, vedi architettura), quindi qui NON lo si cerca:
    leggere da questa directory e' gia' di per se' la garanzia di
    provenienza fisica, la separazione delle directory/collezioni e' il
    firewall, non un campo in piu' da controllare."""
    key = geometry_key(feature, spec)
    for case in _iter_physical_cases(dataset_dir):
        case_spec = case.get("specifica_strutturata") or case.get("spec") or {}
        case_feature = case_spec.get("feature") or case.get("feature")
        case_key = geometry_key(case_feature, case_spec)
        esito = case.get("esito") or case.get("outcome")
        if case_key == key and esito == "FAIL":
            return True
    return False


def should_exclude_strategy(
    feature: str,
    spec: dict,
    log_path: str = RETRY_LOG_PATH,
    dataset_dir: str | None = None,
    min_virtual_failures: int = MIN_VIRTUAL_FAILURES_FOR_EXCLUSION,
    checker_version: str | None = None,
) -> tuple[bool, str]:
    """Decide se scartare una strategia PRIMA di generare (M4, criterio
    di accettazione della milestone). Ritorna (escludi, motivo) — il
    motivo e' sempre popolato, anche quando escludi e' False, per audit
    (perche' NON e' stata esclusa e' informazione tanto quanto il perche'
    lo e' stata).

    dataset_dir assente/vuoto/non esistente (Livello 6 non ancora
    popolato, vedi docs/handoff_m4.md "Cosa NON esiste ancora") non e'
    un errore: significa semplicemente "nessuna corroborazione fisica
    possibile", quindi MAI esclusione — fail-open verso la generazione,
    mai fail-open verso l'esclusione. Coerente con la regola anti-bias:
    l'assenza di dati fisici non puo' MAI abilitare un'esclusione.

    checker_version: default None -> CHECKER_VERSION corrente (vedi
    retry_policy.py); parametrizzabile solo per i test (M5, C5)."""
    key = spec_key(feature, spec)
    virtual_failures = count_virtual_failures(key, log_path, checker_version=checker_version)

    if virtual_failures < min_virtual_failures:
        return False, (
            f"{virtual_failures} fallimento/i virtuale/i per questa strategia "
            f"(< soglia {min_virtual_failures}) — nessuna esclusione."
        )

    if not dataset_dir or not os.path.isdir(dataset_dir):
        return False, (
            f"{virtual_failures} fallimenti virtuali >= soglia, ma nessun dataset Livello 6 "
            "disponibile per corroborazione fisica — regola anti-bias: MAI esclusione senza "
            "riscontro fisico (vedi docs/logbook_fase4.md)."
        )

    if not has_physical_failure(feature, spec, dataset_dir):
        return False, (
            f"{virtual_failures} fallimenti virtuali >= soglia, ma NESSUN fallimento fisico "
            "(Livello 6) corrobora questa strategia — regola anti-bias: nessuna esclusione. "
            "Se il verificatore ha un bug sistematico (es. [v14]), un fallimento virtuale da "
            "solo non deve mai diventare pregiudizio permanente."
        )

    return True, (
        f"{virtual_failures} fallimenti virtuali >= soglia {min_virtual_failures}, corroborati "
        "da almeno un fallimento fisico (Livello 6) sulla stessa strategia — esclusione applicata."
    )
