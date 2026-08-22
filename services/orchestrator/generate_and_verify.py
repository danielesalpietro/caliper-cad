"""
CALIPER — orchestratore Livello 2 -> Livello 3 (genera, poi verifica)
------------------------------------------------------------------------
Script esterno, non un nodo Flowise (Rischio #9): niente Agent, niente
Custom Tool dentro il canvas — quei nodi hanno bug documentati
sull'interpolazione delle variabili (issue FlowiseAI/Flowise #4470,
#5150), stessa categoria dei bug gia' incontrati (ReActAgent,
ChatOllama). Questo script chiama l'API REST di Flowise per il Chatflow
L2 (generazione), poi il verifier (Livello 3) sul codice ottenuto.

Retry automatico (M2, vedi docs/logbook_fase2.md e
services/orchestrator/retry_policy.py): fino a MAX_RETRY_ATTEMPTS
tentativi, con uscita anticipata se lo stesso errore si ripete su 2
tentativi consecutivi. La Policy di retry in
docs/architettura-prototipo-mesh-llm.md richiede "variazione tra un
tentativo e l'altro, non semplice ripetizione" — qui realizzata in due
modi indipendenti: (1) un "retry_context" con un enunciato canned,
scritto da un umano (mai composto dal modello, mai numeri grezzi — vedi
retry_policy.py) iniettato nella spec inviata a L2; (2) temperatura
crescente via overrideConfig sulla chiamata Flowise (vedi
call_flowise_l2). **Riserva onesta:** (2) presuppone che il nodo
ChatOpenAI del Chatflow L2 sia configurato per accettare un override di
temperatura per-richiesta — non verificato in questa sessione (nessuna
istanza Flowise viva nel sandbox, stesso limite gia' incontrato per
Docker in M1) — se il nodo ignora overrideConfig, resta comunque attiva
la variazione (1). Idem per "il prompt legge SOLO directive_text" (vedi
docs/logbook_fase2.md): dipende dal template del Chatflow L2 lato
Flowise, non modificato in questa sessione (fuori scope, vedi
services/flowise/chatflows/).

Prima di chiamare L2, arricchisce la specifica con il preset della
feature (presets.json) — es. per "thread" aggiunge angolo del profilo
e norma di riferimento, che lo schema L2.5 da solo non contiene. Vedi
Rischio in docs/architettura-prototipo-mesh-llm.md: senza questo, L2
non ha la geometria minima per costruire un profilo reale e si ferma
(comportamento corretto del modello, non un bug — ma bloccante).

[M3, vedi docs/logbook_fase3.md] Due correzioni fatte insieme al
collegamento del gauge-check al loop reale, trovate PRIMA di scrivere
il collegamento (stessa disciplina di M1/M2 — un bug reale ogni volta
che si tocca l'integrazione):
1. call_verifier() non mandava mai 'spec' al verifier — il confronto
   dimensionale in run_and_measure.py (feature == "thread") non
   scattava MAI attraverso questo orchestratore, solo negli script di
   verifica manuali che lo chiamano direttamente. Corretto: ora invia
   la spec arricchita (senza retry_context, che serve solo a L2).
2. Il loop chiamava solo /verify — dopo un PASS, il collaudo Go/No-Go
   reale (M1/M2) non veniva mai eseguito sul pezzo generato, solo su
   pezzi statici negli script manuali. Corretto: se il preset della
   feature definisce gauge_check_mode (solo "thread" per ora), dopo un
   PASS di /verify il loop chiama anche /gauge-check sul pezzo appena
   esportato (vedi run_and_measure.py, generated_part_step_path) contro
   il calibro GO del preset. Il caso e' PASS solo se ENTRAMBI passano.

Uso:
    python generate_and_verify.py '{"feature": "thread", "nominal": "M6", ...}'

Variabili d'ambiente:
    FLOWISE_URL             default http://localhost:3000
    VERIFIER_URL            default http://localhost:8600
    FLOWISE_API_KEY         obbligatoria
    L2_CHATFLOW_NAME        default "CALIPER - L2 Generation (CadQuery)"
    L2_CHATFLOW_ID          se impostata, salta la ricerca per nome
    L2_STRATEGY             "free_code" (default), "sketch_first" (M3) o
                            "param_first" (M5, vedi sketch_compiler.py)
    L2_SKETCH_CHATFLOW_NAME default "CALIPER - L2 Generation (Sketch-First)"
    L2_SKETCH_CHATFLOW_ID   se impostata, salta la ricerca per nome (sketch_first
                            e param_first condividono lo stesso chatflow/nome:
                            entrambi chiedono a L2 di NON generare codice libero)
    L6_DATASET_DIR          [M4] directory del dataset Livello 6 (misura fisica reale,
                            vedi virtual_memory.py) usata per la regola anti-bias.
                            Assente/inesistente = nessuna corroborazione fisica
                            possibile = MAI esclusione (fail-open verso la
                            generazione, mai verso l'esclusione)
"""

import json
import os
import sys
import urllib.error
import urllib.request

from retry_policy import MAX_RETRY_ATTEMPTS, RetryBudget, classify_checkpoint, directive_text_for
from sketch_compiler import compile_thread_params_to_code, compile_thread_sketch_to_code
from sketch_schema import SketchValidationError, validate_sketch_spec
from virtual_memory import should_exclude_strategy, spec_key as compute_spec_key

PRESETS_PATH = os.path.join(os.path.dirname(__file__), "presets.json")

FLOWISE_URL = os.getenv("FLOWISE_URL", "http://localhost:3000").rstrip("/")
VERIFIER_URL = os.getenv("VERIFIER_URL", "http://localhost:8600").rstrip("/")
FLOWISE_API_KEY = os.getenv("FLOWISE_API_KEY", "").strip()
L2_CHATFLOW_NAME = os.getenv("L2_CHATFLOW_NAME", "CALIPER - L2 Generation (CadQuery)")
L2_CHATFLOW_ID = os.getenv("L2_CHATFLOW_ID", "").strip()

# [M3] Strategia del nodo L2 — "free_code" (default, invariato: L2
# restituisce codice CadQuery libero) o "sketch_first" (L2 restituisce
# vincoli di sketch 2D come JSON, validati contro sketch_schema.py e
# compilati localmente da sketch_compiler.py, MAI eseguiti qui — vedi le
# loro docstring). Componibile, non una riscrittura di generate_and_verify.py
# (vedi docs/logbook_fase3.md): stesso loop, stesso protocollo di
# verifica/gauge-check, cambia solo COME si ottiene il codice da inviare
# a /verify. **Riserva onesta:** nessun chatflow Flowise
# "sketch-first" esiste in questa sessione (services/flowise/chatflows/
# ha solo la normalizzazione L2.5, non un L2 libero ne' sketch-first —
# quello vive solo dentro un'istanza Flowise configurata a mano, non
# versionata qui) — questa strategia e' verificata SOLO con
# call_flowise_l2 mockata (vedi verify_sketch_first_strategy.py), stessa
# classe di verifica di verify_gauge_check_loop_wiring.py: logica del
# loop, non generazione reale.
#
# [M5, C4/P3 — vedi docs/review_tecnica.md] "param_first": L2 restituisce
# SOLO i parametri fisici della filettatura (major_diameter_mm, pitch_mm,
# engagement_length_mm, host_xy_mm), validati e compilati in
# sketch_compiler.build_thread_sketch_spec_from_params()/
# compile_thread_params_to_code() — stessa trigonometria gia' usata da
# sketch-first, MAI una seconda via geometrica. Condivide lo stesso slot
# di chatflow di sketch_first (L2_SKETCH_CHATFLOW_NAME/_ID): entrambe
# chiedono a L2 di NON restituire codice libero, solo schemi diversi —
# nessun chatflow reale esiste per nessuna delle due in questa sessione
# (stessa riserva onesta di sopra), verificata SOLO con call_flowise_l2
# mockata.
L2_STRATEGY = os.getenv("L2_STRATEGY", "free_code").strip()
L2_SKETCH_CHATFLOW_NAME = os.getenv("L2_SKETCH_CHATFLOW_NAME", "CALIPER - L2 Generation (Sketch-First)")
L2_SKETCH_CHATFLOW_ID = os.getenv("L2_SKETCH_CHATFLOW_ID", "").strip()

# [M4] Vedi virtual_memory.py e docs/logbook_fase4.md.
L6_DATASET_DIR = os.getenv("L6_DATASET_DIR", "").strip()

SKETCH_FIRST_SUPPORTED_FEATURES = ("thread",)
PARAM_FIRST_SUPPORTED_FEATURES = ("thread",)

# Temperatura per tentativo (attempt 1-indexed) — variazione (2) descritta
# sopra. Valori indicativi, non calibrati su un batch reale (nessuna
# istanza Flowise viva per misurarne l'effetto in questa sessione).
RETRY_TEMPERATURES = [0.0, 0.3, 0.6]


def flowise_get(path: str):
    req = urllib.request.Request(f"{FLOWISE_URL}{path}", method="GET")
    req.add_header("Authorization", f"Bearer {FLOWISE_API_KEY}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_presets() -> dict:
    with open(PRESETS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_preset(presets: dict, feature: str) -> dict | None:
    preset = presets.get(feature)
    if not preset or not preset.get("defined"):
        return None
    return preset


def apply_preset(spec: dict, presets: dict) -> dict:
    preset = get_preset(presets, spec.get("feature", ""))
    if preset is None:
        return spec

    enriched = dict(spec)
    if "standard" in preset:
        enriched.setdefault("thread_standard", preset["standard"])
    if "profile_angle_deg" in preset:
        enriched.setdefault("thread_profile_angle_deg", preset["profile_angle_deg"])
    if not enriched.get("tolerance_type") and "default_tolerance_type" in preset:
        enriched["tolerance_type"] = preset["default_tolerance_type"]
    # [M5, C1 — vedi docs/review_tecnica.md] il preset dichiara il
    # contratto dimensionale (run_and_measure.py lo legge dalla spec, non
    # dal preset direttamente — questo e' l'unico canale di arricchimento,
    # vedi Blocco A in docs/handoff_m5.md): per "thread",
    # dimensional_check="gauge" disattiva il bbox-vs-nominale legacy.
    if "dimensional_check" in preset:
        enriched.setdefault("dimensional_check", preset["dimensional_check"])
    if "engagement_length_mm" in preset:
        enriched.setdefault("engagement_length_mm", preset["engagement_length_mm"])
    return enriched


def gauge_check_job_for_preset(preset: dict) -> dict | None:
    """Costruisce i parametri per /gauge-check (calibro GO) dal preset
    della feature (M3, vedi docstring del modulo) — None se il preset non
    definisce un collaudo Go/No-Go (gauge_check_mode assente, es.
    clearance_fit non ancora collegato qui, fuori scope M3 che si limita
    a "thread").

    Il loop verifica che il pezzo generato PASSI il GO (deve poter essere
    impegnato). [M5, C2 — vedi docs/review_tecnica.md] Il NO-GO (deve
    invece interferire, per coprire il lato superiore della banda di
    tolleranza) e' ora chiamato anche lui nel loop, vedi
    gauge_nogo_job_for_preset() sotto e main(): prima di M5 il NO-GO
    restava compito dei soli script di verifica manuali
    (verify_gauge_check_tc*.py), lasciando un foro sovradimensionato
    inosservato dal loop reale.
    """
    mode = preset.get("gauge_check_mode")
    if not mode:
        return None
    if mode == "min_distance":
        # [M5, C3 — vedi docs/review_tecnica.md] min_distance (es.
        # snap_fit) non usa un calibro fisico: nessun gauge_go_step da
        # pretendere qui, il job si costruisce altrove (vedi
        # min_distance_job_for_preset()).
        return None
    if "gauge_go_step" not in preset:
        raise ValueError("preset con gauge_check_mode ma senza gauge_go_step — schema del preset incoerente")

    job = {"mode": mode, "gauge_step_path": preset["gauge_go_step"]}
    if mode == "sweep":
        for required in ("sweep_steps", "engagement_length_mm"):
            if required not in preset:
                raise ValueError(f"preset con gauge_check_mode='sweep' ma senza '{required}'")
        job["sweep"] = {
            "steps": preset["sweep_steps"],
            "start_offset_mm": 0.0,
            "end_offset_mm": preset["engagement_length_mm"],
            "pitch_mm": preset.get("pitch_mm", 0.0),
        }
    return job


def gauge_nogo_job_for_preset(preset: dict) -> dict | None:
    """[M5, C2 — vedi docs/review_tecnica.md] Costruisce i parametri per
    /gauge-check del calibro NO-GO — None se il preset non definisce un
    NO-GO (nessun gauge_nogo_step, es. min_distance/snap_fit, che non usa
    calibri fisici). Stessa struttura di gauge_check_job_for_preset(), un
    solo campo diverso (gauge_nogo_step al posto di gauge_go_step) — la
    semantica invertita (interferenza attesa = pezzo OK) si applica a
    valle, in main(), non qui: questa funzione costruisce solo il job."""
    mode = preset.get("gauge_check_mode")
    if not mode or mode == "min_distance" or "gauge_nogo_step" not in preset:
        return None

    job = {"mode": mode, "gauge_step_path": preset["gauge_nogo_step"]}
    if mode == "sweep":
        for required in ("sweep_steps", "engagement_length_mm"):
            if required not in preset:
                raise ValueError(f"preset con gauge_check_mode='sweep' ma senza '{required}'")
        job["sweep"] = {
            "steps": preset["sweep_steps"],
            "start_offset_mm": 0.0,
            "end_offset_mm": preset["engagement_length_mm"],
            "pitch_mm": preset.get("pitch_mm", 0.0),
        }
    return job


def min_distance_job_for_preset(preset: dict) -> dict | None:
    """[M5, C3 — vedi docs/review_tecnica.md] Costruisce i parametri per
    /gauge-check (modalita' min_distance, es. snap_fit) dai
    measurement_points del preset — None se il preset non definisce
    misure di questo tipo. Prima di questo fix, generate_and_verify.py
    pretendeva SEMPRE gauge_go_step per ogni gauge_check_mode
    (gauge_check_job_for_preset), mandando in ValueError qualunque spec
    'snap_fit' — min_distance non usa un calibro fisico, il 'calibro
    virtuale' e' tra due punti dello STESSO pezzo (vedi presets.json,
    measurement_points, e gauge_check.py::run_min_distance)."""
    if preset.get("gauge_check_mode") != "min_distance":
        return None
    points = preset.get("measurement_points") or {}
    if not points:
        raise ValueError("preset con gauge_check_mode='min_distance' ma senza 'measurement_points'")
    # M5 collega un solo punto di misura per caso (coerente con lo scope
    # di M3/M5 — un solo preset 'snap_fit' con un solo measurement_point
    # oggi, 'retention_gap'): se in futuro un preset ne dichiara piu' di
    # uno, questo e' il punto da estendere (fuori scope qui, nessuna
    # assunzione silenziosa — un secondo punto verrebbe ignorato senza
    # errore altrimenti).
    name, spec_point = next(iter(points.items()))
    for required in ("point_a_mm", "point_b_mm", "nominal_mm", "tolerance_mm"):
        if required not in spec_point:
            raise ValueError(f"measurement_point '{name}' senza '{required}' — preset incoerente")
    return {
        "mode": "min_distance",
        "min_distance": {
            "point_a_mm": spec_point["point_a_mm"],
            "point_b_mm": spec_point["point_b_mm"],
            "nominal_mm": spec_point["nominal_mm"],
            "tolerance_mm": spec_point["tolerance_mm"],
        },
    }


def resolve_chatflow_id(strategy: str = "free_code") -> str:
    """[M3] strategy seleziona QUALE chatflow risolvere — "sketch_first"
    e (M5) "param_first" usano un chatflow DIVERSO (L2_SKETCH_CHATFLOW_NAME/_ID,
    condiviso da entrambe — vedi la nota su L2_STRATEGY), perche' devono
    restituire vincoli/parametri JSON invece di codice libero: un
    prompt/template diverso, non la stessa chain con un'istruzione in
    piu' (vedi services/flowise/chatflows/ — nessuno dei chatflow L2 e'
    versionato li', solo la normalizzazione L2.5; tutti vivono in
    un'istanza Flowise configurata a mano, fuori scope versionarli qui)."""
    non_free_code = strategy in ("sketch_first", "param_first")
    chatflow_id = L2_SKETCH_CHATFLOW_ID if non_free_code else L2_CHATFLOW_ID
    chatflow_name = L2_SKETCH_CHATFLOW_NAME if non_free_code else L2_CHATFLOW_NAME
    if chatflow_id:
        return chatflow_id
    chatflows = flowise_get("/api/v1/chatflows")
    for c in chatflows:
        if c["name"] == chatflow_name:
            return c["id"]
    raise RuntimeError(
        f"Nessun chatflow chiamato '{chatflow_name}' trovato. "
        f"Imposta L2_CHATFLOW_ID/L2_SKETCH_CHATFLOW_ID esplicitamente, o controlla il nome."
    )


def call_flowise_l2(chatflow_id: str, spec_json: str, temperature: float | None = None) -> str:
    body = {"question": spec_json}
    if temperature is not None:
        # Variazione (2), vedi docstring del modulo — riserva onesta: non
        # verificato che il nodo ChatOpenAI del Chatflow L2 legga questo
        # override (nessuna istanza Flowise viva in questa sessione).
        body["overrideConfig"] = {"temperature": temperature}
    req = urllib.request.Request(
        f"{FLOWISE_URL}/api/v1/prediction/{chatflow_id}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {FLOWISE_API_KEY}")
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    # [M6, verificato dal vivo contro Flowise 3.1.4] quando il
    # chatflow usa uno Structured Output Parser (param_first/
    # sketch_first), la prediction API restituisce il risultato in
    # "json", non in "text" (che manca del tutto). "text" resta
    # prioritario per free_code (LLM Chain senza output parser).
    if "text" in data:
        return data["text"]
    if "json" in data:
        return json.dumps(data["json"])
    return ""


def generate_code_for_attempt(strategy: str, chatflow_id: str, spec_json: str, temperature: float | None, feature: str):
    """Ottiene il codice CadQuery da inviare a /verify per questo
    tentativo — punto di estensione tra le strategie L2 (M3/M5, vedi
    docstring del modulo). Ritorna (code, error): esattamente uno dei due
    e' non-None. Un errore qui (JSON malformato, spec che non valida lo
    schema, feature non supportata dal compilatore) e' un FAIL di questo
    tentativo SENZA nemmeno chiamare /verify — non c'e' codice da
    verificare, non lo stesso genere di errore che classify_checkpoint sa
    interpretare, quindi ricade su RETRY_GENERIC (mai un hint inventato,
    vedi retry_policy.py)."""
    if strategy == "free_code":
        return call_flowise_l2(chatflow_id, spec_json, temperature=temperature), None

    if strategy == "sketch_first":
        if feature not in SKETCH_FIRST_SUPPORTED_FEATURES:
            return None, f"sketch_first non supporta la feature {feature!r} in questa milestone (solo {SKETCH_FIRST_SUPPORTED_FEATURES})"

        raw = call_flowise_l2(chatflow_id, spec_json, temperature=temperature)
        try:
            sketch_spec = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, f"L2 (sketch-first) non ha restituito JSON valido: {e}"

        errors = validate_sketch_spec(sketch_spec)
        if errors:
            return None, "spec sketch-first non valida: " + "; ".join(errors)

        try:
            code = compile_thread_sketch_to_code(sketch_spec)
        except SketchValidationError as e:
            return None, f"compilazione sketch->CadQuery fallita: {e}"
        return code, None

    if strategy == "param_first":
        # [M5, C4/P3 — vedi docs/review_tecnica.md] L2 emette SOLO i
        # parametri fisici della filettatura, il compilatore costruisce
        # internamente la spec sketch canonica (stessa trigonometria di
        # sketch_first, vedi sketch_compiler.py) — nessuna seconda via
        # geometrica.
        if feature not in PARAM_FIRST_SUPPORTED_FEATURES:
            return None, f"param_first non supporta la feature {feature!r} in questa milestone (solo {PARAM_FIRST_SUPPORTED_FEATURES})"

        raw = call_flowise_l2(chatflow_id, spec_json, temperature=temperature)
        try:
            params = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, f"L2 (param-first) non ha restituito JSON valido: {e}"
        if not isinstance(params, dict):
            return None, "L2 (param-first) non ha restituito un oggetto JSON di parametri"

        # thread_profile_angle_deg viene dal preset (arricchito in
        # apply_preset() nella spec di RICHIESTA, spec_json) — non e'
        # qualcosa che L2 emette in param_first, e' un dato di
        # riferimento del profilo standard, stesso principio per cui
        # sketch-first non lo chiede all'LLM (vedi presets.json).
        try:
            request_spec = json.loads(spec_json)
        except json.JSONDecodeError:
            request_spec = {}
        profile_angle_deg = request_spec.get("thread_profile_angle_deg", 60.0)

        try:
            code = compile_thread_params_to_code(params, profile_angle_deg=profile_angle_deg)
        except SketchValidationError as e:
            return None, f"parametri param-first non validi: {e}"
        return code, None

    return None, f"L2_STRATEGY sconosciuta: {strategy!r}"


def call_verifier(code: str, spec: dict | None = None) -> dict:
    # [M3] 'spec' ora inoltrata davvero — vedi punto 1 nella docstring del
    # modulo: senza questo il confronto dimensionale in run_and_measure.py
    # non scattava mai attraverso questo orchestratore.
    body = {"code": code}
    if spec is not None:
        body["spec"] = spec
    req = urllib.request.Request(
        f"{VERIFIER_URL}/verify",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def call_gauge_check(part_step_path: str, gauge_job: dict) -> dict:
    """Chiama /gauge-check (M1/M2) sul pezzo appena esportato da
    run_and_measure.py (M3, vedi docstring del modulo). part_source e'
    sempre "generated" qui: questo loop collauda solo pezzi appena
    generati, mai i modelli statici sotto /models (quelli restano
    dominio degli script di verifica manuali, vedi gauge_check.py)."""
    body = {"part_step_path": part_step_path, "part_source": "generated", **gauge_job}
    req = urllib.request.Request(
        f"{VERIFIER_URL}/gauge-check",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    # Deve superare GAUGE_CHECK_HTTP_TIMEOUT_SECONDS lato verifier (app.py,
    # oggi 200s) con margine — stesso principio del bug trovato li'.
    with urllib.request.urlopen(req, timeout=220) as resp:
        return json.loads(resp.read().decode("utf-8"))


def failure_error_string(result: dict) -> str | None:
    """Estrae una stringa d'errore stabile da un risultato /verify FAIL,
    per il confronto 'same_error_as_previous' di RetryBudget."""
    exec_check = next((c for c in result.get("checks", []) if c["name"] == "execution_and_geometry"), None)
    if exec_check and exec_check.get("detail"):
        return exec_check["detail"]
    failed = [c for c in result.get("checks", []) if c["status"] == "FAIL"]
    if failed:
        return failed[0]["name"]
    return "verify_fail_unknown"


def should_stop_retrying(budget: RetryBudget, attempt: int, outcome_error: str | None) -> bool:
    """Vero se il loop deve fermarsi dopo aver registrato un FAIL a
    questo tentativo (uscita anticipata o budget esaurito) — fattorizzato
    perche' un FAIL puo' arrivare da tre punti diversi del loop ora (era
    uno solo prima di M3): errore di generazione/compilazione (nessun
    codice da verificare), FAIL di /verify, FAIL/TIMEOUT di
    /gauge-check. Stessa RetryBudget, stessa policy, un solo posto che
    stampa e decide."""
    if budget.should_stop_early():
        print(f"\n=== Uscita anticipata: stesso errore/directive ripetuto {2} volte consecutive ({outcome_error}) ===")
        return True
    if attempt == MAX_RETRY_ATTEMPTS:
        print(f"\n=== Budget di retry esaurito ({MAX_RETRY_ATTEMPTS} tentativi) ===")
        return True
    return False


def gauge_check_failure_error_string(gc_response: dict) -> str:
    """Equivalente di failure_error_string() ma per un FAIL/TIMEOUT di
    /gauge-check — stesso scopo (confronto 'same_error_as_previous')."""
    gc = gc_response.get("gauge_check") or {}
    status = gc.get("status", "UNKNOWN")
    mode = gc.get("mode", "unknown_mode")
    return f"gauge_check_{status.lower()}_{mode}"


def main():
    if len(sys.argv) < 2:
        print("Uso: python generate_and_verify.py '<spec JSON L2.5>'")
        return 1
    if not FLOWISE_API_KEY:
        print("FLOWISE_API_KEY non impostata.")
        return 1

    spec = json.loads(sys.argv[1])
    presets = load_presets()
    enriched_spec = apply_preset(spec, presets)
    if enriched_spec != spec:
        print("-> Preset applicato:")
        print(json.dumps(enriched_spec, indent=2, ensure_ascii=False))

    preset = get_preset(presets, enriched_spec.get("feature", ""))
    nogo_job = None
    try:
        gauge_job = gauge_check_job_for_preset(preset) if preset else None
        if gauge_job is not None:
            # [M5, C2] solo le modalita' a calibro (es. "sweep") hanno un
            # NO-GO — min_distance non ci arriva mai qui (gauge_job resta
            # None per quel mode, vedi sotto).
            nogo_job = gauge_nogo_job_for_preset(preset)
        elif preset:
            # [M5, C3] mode assente qui puo' significare "nessun collaudo
            # per questa feature" (es. clearance_fit) O "min_distance"
            # (es. snap_fit, che non usa gauge_step_path) — distinti solo
            # da min_distance_job_for_preset(), None per il primo caso.
            gauge_job = min_distance_job_for_preset(preset)
    except ValueError as e:
        print(f"Preset incoerente per il gauge-check: {e}")
        return 1
    if gauge_job is not None and gauge_job.get("mode") == "min_distance":
        print(f"-> Collaudo min_distance attivo per questa feature ({gauge_job['min_distance']})")
    elif gauge_job is not None:
        nogo_note = f", nogo={nogo_job['gauge_step_path']}" if nogo_job is not None else " (nessun NO-GO nel preset)"
        print(f"-> Collaudo Go/No-Go attivo per questa feature (mode={gauge_job['mode']}, gauge={gauge_job['gauge_step_path']}{nogo_note})")
    else:
        print("-> Nessun collaudo Go/No-Go per questa feature (preset senza gauge_check_mode) — solo /verify.")

    print(f"-> Strategia L2: {L2_STRATEGY}")

    # [M4, vedi docs/logbook_fase4.md e virtual_memory.py] Consultare la
    # memoria del collaudo virtuale PRIMA di generare — sia prima di
    # spendere qualunque chiamata di rete (nessuna chiamata a Flowise,
    # nemmeno resolve_chatflow_id, se la strategia e' esclusa). La
    # spec_key include la strategia L2 stessa: free_code e sketch_first
    # sono percorsi distinti, un'esclusione sull'uno non tocca l'altro.
    feature = enriched_spec.get("feature", "")
    key = compute_spec_key(feature, {**enriched_spec, "l2_strategy": L2_STRATEGY})
    exclude, reason = should_exclude_strategy(feature, {**enriched_spec, "l2_strategy": L2_STRATEGY}, dataset_dir=L6_DATASET_DIR or None)
    print(f"-> Memoria del collaudo virtuale (spec_key={key}): {reason}")
    if exclude:
        print("\n=== Strategia scartata dalla memoria del collaudo virtuale — generazione NON avviata. ===")
        return 1

    try:
        chatflow_id = resolve_chatflow_id(L2_STRATEGY)
    except (RuntimeError, urllib.error.HTTPError) as e:
        print(f"Impossibile risolvere il Chatflow L2: {e}")
        return 1
    print(f"-> Chatflow L2: {chatflow_id}")

    budget = RetryBudget(feature=feature, spec_key=key)
    print(f"-> case_id: {budget.case_id}")

    directive, directive_text = None, None
    previous_error = None

    for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
        attempt_spec = dict(enriched_spec)
        if attempt > 1:
            # Vedi docstring del modulo: 'retry_context' e' la variazione
            # (1), sempre indipendente dalla temperatura crescente (2) —
            # entrambe attive ad ogni retry, mai una sostituisce l'altra.
            attempt_spec["retry_context"] = {
                "attempt": attempt,
                "previous_error": previous_error,
                "directive": directive,
                "directive_text": directive_text,
            }
        spec_json = json.dumps(attempt_spec, ensure_ascii=False)
        temperature = RETRY_TEMPERATURES[min(attempt - 1, len(RETRY_TEMPERATURES) - 1)]

        print(f"\n=== Tentativo {attempt}/{MAX_RETRY_ATTEMPTS} (temperature={temperature}) ===")
        if attempt > 1:
            print(f"-> directive: {directive} — {directive_text}")

        print(f"-> Genero il codice (Livello 2, strategia={L2_STRATEGY})...")
        try:
            code, generation_error = generate_code_for_attempt(
                L2_STRATEGY, chatflow_id, spec_json, temperature, enriched_spec.get("feature", "")
            )
        except urllib.error.HTTPError as e:
            print(f"Generazione fallita: HTTP {e.code} - {e.read().decode('utf-8', 'ignore')}")
            return 1

        if code is None:
            # [M3] Errore di generazione/validazione/compilazione PRIMA
            # ancora di raggiungere /verify (JSON malformato, spec
            # sketch-first che non passa lo schema, feature non
            # supportata dal compilatore) — vedi generate_code_for_attempt().
            # Nessun codice da verificare: FAIL di questo tentativo senza
            # chiamare /verify, classificato RETRY_GENERIC (non e' un
            # errore geometrico che classify_checkpoint sa interpretare).
            print(f"\n!!! Generazione del codice fallita: {generation_error}")
            directive = "RETRY_GENERIC"
            directive_text = directive_text_for(directive)
            # [M5, C5 — vedi docs/review_tecnica.md] failure_class="generation":
            # nessuna geometria e' mai stata prodotta/misurata qui (JSON
            # malformato, schema non valido, compilazione fallita) — non
            # deve mai contare per l'esclusione di una strategia
            # (should_exclude_strategy conta solo i FAIL "geometric",
            # vedi virtual_memory.py).
            budget.record_attempt(
                attempt, directive_used=directive, outcome="FAIL", outcome_error=generation_error, failure_class="generation"
            )
            previous_error = generation_error

            if should_stop_retrying(budget, attempt, generation_error):
                break
            continue

        print("\n--- Codice generato ---")
        print(code)

        print("\n-> Verifico (Livello 3)...")
        # [M3] 'spec' inoltrata ora (vedi punto 1 nella docstring del
        # modulo) — attempt_spec, non enriched_spec: retry_context non fa
        # danno al confronto dimensionale (ignora chiavi che non conosce),
        # ma e' comunque piu' corretto passare esattamente cio' che questo
        # tentativo ha usato.
        result = call_verifier(code, spec=attempt_spec)
        print("\n--- Esito verifica ---")
        print(json.dumps(result, indent=2, ensure_ascii=False))

        gauge_result = None  # popolato solo se /verify PASS e gauge_job e' definito

        if result["status"] == "PASS":
            if gauge_job is None:
                budget.record_attempt(attempt, directive_used=directive, outcome="PASS", outcome_error=None)
                print(f"\n=== PASS al tentativo {attempt}/{MAX_RETRY_ATTEMPTS} (solo /verify, nessun gauge-check per questa feature) ===")
                return 0

            part_step_path = result.get("generated_part_step_path")
            if not part_step_path:
                # Non dovrebbe accadere se /verify ha detto PASS (vedi
                # run_and_measure.py: esporta sempre su PASS) — se succede
                # e' un bug altrove, non un caso da inghiottire in
                # silenzio: trattato come FAIL di questo tentativo.
                outcome_error = "missing_generated_part_step_path"
                directive = "RETRY_GENERIC"
                directive_text = directive_text_for(directive)
                # [M5, C5] Bug di protocollo, non un giudizio sulla
                # geometria della strategia — "generation", stesso motivo
                # del branch sopra.
                budget.record_attempt(
                    attempt, directive_used=directive, outcome="FAIL", outcome_error=outcome_error, failure_class="generation"
                )
                previous_error = outcome_error
                print("\n!!! /verify ha detto PASS ma non ha restituito generated_part_step_path — impossibile eseguire il gauge-check.")
            else:
                gauge_label = "calibro GO" if nogo_job is not None else gauge_job["mode"]
                print(f"\n-> Collaudo Go/No-Go (gauge-check, {gauge_label})...")
                gauge_result = call_gauge_check(part_step_path, gauge_job)
                print("\n--- Esito gauge-check ---")
                print(json.dumps(gauge_result, indent=2, ensure_ascii=False))

                if gauge_result["status"] != "PASS":
                    outcome_error = gauge_check_failure_error_string(gauge_result)
                    directive = classify_checkpoint(gauge_result.get("gauge_check"))
                    directive_text = directive_text_for(directive)
                    # [M5, C5] Un gauge-check reale e' stato eseguito su un
                    # pezzo davvero esportato: FAIL "geometric", conta per
                    # l'esclusione della strategia.
                    budget.record_attempt(
                        attempt, directive_used=directive, outcome="FAIL", outcome_error=outcome_error, failure_class="geometric"
                    )
                    previous_error = outcome_error
                elif nogo_job is None:
                    budget.record_attempt(attempt, directive_used=directive, outcome="PASS", outcome_error=None)
                    print(f"\n=== PASS al tentativo {attempt}/{MAX_RETRY_ATTEMPTS} (/verify + gauge-check) ===")
                    return 0
                else:
                    # [M5, C2 — vedi docs/review_tecnica.md] Il GO da solo
                    # copre solo il lato INFERIORE della banda di
                    # tolleranza (foro non sottodimensionato) — prima di
                    # M5 il NO-GO non veniva mai chiamato qui, un foro
                    # sovradimensionato passava lo sweep GO senza
                    # interferenza e il loop lo dichiarava PASS. Semantica
                    # INVERTITA rispetto al GO: qui l'interferenza e' il
                    # risultato CORRETTO (il foro non deve accettare il
                    # calibro NO-GO, troppo grande) — "status" == "PASS"
                    # da gauge_check.py significa "nessuna interferenza
                    # rilevata", che per un NO-GO e' il caso NEGATIVO.
                    print("\n-> Collaudo Go/No-Go (gauge-check, calibro NO-GO — deve interferire)...")
                    nogo_result = call_gauge_check(part_step_path, nogo_job)
                    print("\n--- Esito gauge-check (NO-GO) ---")
                    print(json.dumps(nogo_result, indent=2, ensure_ascii=False))

                    if nogo_result["status"] == "PASS":
                        # Nessuna interferenza sul NO-GO: il foro e' troppo
                        # grande, mai stato rilevato dal solo GO — errore
                        # stabile dedicato (non generato dal codice del
                        # NO-GO, che qui ha "status"="PASS" con la propria
                        # semantica: nessun'altra stringa lo distinguerebbe
                        # da un vero PASS).
                        outcome_error = "gauge_check_nogo_no_interference"
                        directive = classify_checkpoint(nogo_result.get("gauge_check"))
                        directive_text = directive_text_for(directive)
                        # [M5, C5] geometric: il NO-GO e' stato eseguito
                        # davvero sul pezzo esportato, il foro risulta
                        # sovradimensionato.
                        budget.record_attempt(
                            attempt, directive_used=directive, outcome="FAIL", outcome_error=outcome_error, failure_class="geometric"
                        )
                        previous_error = outcome_error
                    elif nogo_result["status"] == "FAIL":
                        # Interferenza rilevata: esattamente cio' che un
                        # NO-GO deve fare — PASS solo ora, con ENTRAMBI i
                        # calibri verificati.
                        budget.record_attempt(attempt, directive_used=directive, outcome="PASS", outcome_error=None)
                        print(f"\n=== PASS al tentativo {attempt}/{MAX_RETRY_ATTEMPTS} (/verify + GO + NO-GO) ===")
                        return 0
                    else:
                        # TIMEOUT: nessuna interferenza CONFERMATA entro il
                        # tempo massimo — inconclusivo, mai un PASS senza
                        # conferma (stessa disciplina fail-safe gia'
                        # applicata altrove, es. run_and_measure.py
                        # sull'esportazione STEP fallita).
                        outcome_error = gauge_check_failure_error_string(nogo_result)
                        directive = classify_checkpoint(nogo_result.get("gauge_check"))
                        directive_text = directive_text_for(directive)
                        # [M5, C5] geometric: anche un TIMEOUT qui avviene
                        # su un gauge-check reale in corso, non su un
                        # errore di generazione/protocollo.
                        budget.record_attempt(
                            attempt, directive_used=directive, outcome="FAIL", outcome_error=outcome_error, failure_class="geometric"
                        )
                        previous_error = outcome_error
        else:
            # FAIL di /verify (sintassi/esecuzione/bbox/dimensionale):
            # nessun gauge_check e' mai stato eseguito in questo caso (il
            # pezzo non e' nemmeno stato validato) — classify_checkpoint
            # ricade correttamente su RETRY_GENERIC, non silenziosamente
            # (vedi retry_policy.py). [M5, C5] failure_class="geometric":
            # /verify ha davvero eseguito/misurato il codice generato
            # (anche un FAIL di sintassi passa da run_and_measure.py) —
            # e' un giudizio reale sulla geometria/codice di questa
            # strategia, non un errore di protocollo a monte.
            outcome_error = failure_error_string(result)
            directive = classify_checkpoint(None)
            directive_text = directive_text_for(directive)
            budget.record_attempt(
                attempt, directive_used=directive, outcome="FAIL", outcome_error=outcome_error, failure_class="geometric"
            )
            previous_error = outcome_error

        if should_stop_retrying(budget, attempt, outcome_error):
            break

    print(f"\n=== final_status: unrecoverable_virtual (case_id={budget.case_id}, source=virtual) ===")
    print("-> Richiede intervento umano (fallback gia' previsto per la Fase A).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
