"""
CALIPER — contratto di retry Livello 3 -> Livello 2 (M2).

Design gia' deciso in docs/logbook_fase2.md ("Come il checkpoint arriva
al Livello 2 in retry, senza farlo 'spiegare'", "Limite massimo di
tentativi (L3 -> L2)", "Misurare l'efficacia delle directive") — questo
modulo lo implementa, non lo ridiscute. Usato da generate_and_verify.py.

Due pezzi separati, per costruzione:

1. classify_checkpoint(): funzione A SOGLIE deterministica (mai un LLM)
   che riduce il checkpoint di un gauge_check TIMEOUT (o l'assenza di
   uno, per un FAIL "semplice" senza sweep — es. dimensionale/sintassi)
   a un enum fisso. Ad ogni enum corrisponde UN SOLO enunciato canned,
   scritto da un umano qui sotto (DIRECTIVE_TEXTS) — mai composto dal
   modello, mai i numeri grezzi nel prompt (quelli restano per L7, filtro
   esatto su parametri strutturati).

2. RetryBudget: budget massimo di tentativi (3, deciso in
   docs/logbook_fase2.md) con uscita anticipata se lo stesso
   directive/errore si ripete su 2 tentativi consecutivi nonostante la
   variazione — e log strutturato per tentativo (case_id/attempt/
   directive_used/outcome), prerequisito di qualunque misura futura
   dell'efficacia delle directive (Fase 1 di quella misura, non la Fase 2
   di confronto controllato — quella resta lavoro successivo, serve
   volume).

[M4, vedi docs/logbook_fase4.md] RetryBudget accetta ora 'feature' e
'spec_key' (opzionali, per compatibilita' con gli usi esistenti che non
li passano) e li scrive su OGNI record. Questo e' lo schema del "log del
collaudo virtuale" richiesto da M4 — estensione di questo formato
esistente invece di un formato parallelo (vedi "Punto di partenza gia'
esistente" in docs/logbook_fase4.md): 'source: virtual' era gia'
obbligatorio dal M2, qui si aggiunge il filtro esatto sui campi
strutturati che serve al Livello 7 (virtual_memory.py) per riconoscere
quando due tentativi appartengono alla STESSA strategia (stessa feature +
stessa spec nominale/tolleranza + stessa strategia L2), non solo allo
stesso case_id di un singolo run.

Riserva onesta (gia' in docs/logbook_fase2.md, ripetuta qui perche'
governa il comportamento del codice): l'enum e i suoi enunciati sono
un'ipotesi non validata ("timeout in fase iniziale = profilo troppo
complesso" e' plausibile, non dimostrato) — RETRY_LOG_PATH esiste apposta
per tracciarli insieme all'esito del retry successivo, non per assunzione.
"""

import hashlib
import json
import os
import time
import uuid

# Deciso in docs/logbook_fase2.md, "Limite massimo di tentativi (L3 -> L2)".
MAX_RETRY_ATTEMPTS = 3
EARLY_EXIT_CONSECUTIVE_REPEATS = 2

RETRY_LOG_PATH = os.environ.get(
    "RETRY_LOG_PATH", os.path.join(os.path.dirname(__file__), "retry_log.jsonl")
)

# [M5, C5 — vedi docs/review_tecnica.md, P4] Gli script che decidono un
# verdetto GEOMETRICO (non "generation") — se uno di questi ha un bug
# sistematico (gia' successo una volta, [v14]), un fix qui deve azzerare
# il pregiudizio che quel bug ha accumulato nel log virtuale, non
# lasciarlo permanente. checker_version (sotto) e' un hash del loro
# contenuto: virtual_memory.should_exclude_strategy() conta solo i FAIL
# della versione CORRENTE, cosi' correggere uno di questi file rende
# automaticamente non piu' contati i FAIL prodotti dalla versione
# precedente (bacata).
_CHECKER_FILES = (
    os.path.join(os.path.dirname(__file__), "..", "verifier", "executor", "gauge_check.py"),
    os.path.join(os.path.dirname(__file__), "..", "verifier", "executor", "run_and_measure.py"),
    os.path.join(os.path.dirname(__file__), "..", "verifier", "executor", "measure_verdict.py"),
)


def compute_checker_version() -> str:
    """Hash corto (12 esadecimali, sha256 troncato — leggibile nel log,
    stesso stile di spec_key in virtual_memory.py) del contenuto
    concatenato di _CHECKER_FILES. Un file mancante (es. ambiente di
    test senza l'albero completo) e' incluso nell'hash come tale
    (percorso invece di contenuto), invece di sollevare — coerente con
    "mai un'eccezione che risale per un problema di ambiente", stessa
    disciplina gia' vista altrove nel progetto."""
    h = hashlib.sha256()
    for path in _CHECKER_FILES:
        try:
            with open(path, "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(f"missing:{path}".encode("utf-8"))
    return h.hexdigest()[:12]


CHECKER_VERSION = compute_checker_version()

# Soglia per TOPOLOGY_TOLERANCE_ANOMALY — NON ancora calibrata su casi
# reali (nessun batch con questo errore osservato finora): valore
# indicativo, va rivista con misura reale non appena il primo caso reale
# arriva nel log (stessa disciplina di "misurare, non assumere" gia'
# applicata al timeout del gauge-check in gauge_check.py). Finche' non
# scatta con sicurezza, classify_checkpoint ricade su RETRY_GENERIC —
# "meglio nessun hint che uno sbagliato" (docs/logbook_fase2.md).
TOPOLOGY_TOLERANCE_THRESHOLD_MM = 0.05

# Un solo enunciato canned per enum, scritto da un umano — vedi
# docstring del modulo. RETRY_GENERIC non ha testo: nessun hint tecnico
# specifico, si torna alla sola variazione prompt/temperatura gia'
# prevista dalla Policy di retry esistente.
DIRECTIVE_TEXTS = {
    "SWEEP_TIMEOUT_EARLY": (
        "Il tentativo precedente ha superato il tempo massimo nella prima parte "
        "del percorso (sweep/avvitamento). Riduci la complessita' del profilo "
        "(numero di segmenti) o il numero di giri/step modellati."
    ),
    "SWEEP_TIMEOUT_LATE": (
        "Il tentativo precedente ha superato il tempo massimo nella parte avanzata "
        "del percorso (sweep/avvitamento). La geometria di base sembra corretta ma "
        "l'operazione e' troppo pesante sull'intera corsa: semplifica il profilo o "
        "riduci la lunghezza/il numero di giri modellati."
    ),
    "TOPOLOGY_TOLERANCE_ANOMALY": (
        "Il tentativo precedente ha prodotto una geometria con tolleranza "
        "topologica anomala (bordi o facce quasi degeneri). Ricostruisci la "
        "feature evitando superfici quasi tangenti o segmenti di lunghezza "
        "quasi nulla."
    ),
    "RETRY_GENERIC": None,
}


def classify_checkpoint(gauge_check_result: dict | None) -> str:
    """Riduce un risultato di gauge_check (TIMEOUT o FAIL, con o senza
    checkpoint di sweep) a uno dei quattro enum fissi. Vedi
    docs/logbook_fase2.md per la tabella completa delle soglie.

    gauge_check_result puo' essere None (es. un FAIL "semplice" del
    Livello 3 — sintassi/dimensionale — senza alcun gauge-check
    coinvolto): in quel caso non c'e' alcun checkpoint da classificare,
    si ricade su RETRY_GENERIC.
    """
    if not gauge_check_result:
        return "RETRY_GENERIC"

    diag = gauge_check_result.get("preflight_diagnostics") or {}
    max_tol = diag.get("max_entity_tolerance_mm")
    if isinstance(max_tol, (int, float)) and max_tol > TOPOLOGY_TOLERANCE_THRESHOLD_MM:
        return "TOPOLOGY_TOLERANCE_ANOMALY"

    checkpoint = gauge_check_result.get("last_checkpoint")
    if checkpoint and checkpoint.get("total_steps"):
        ratio = checkpoint["step"] / checkpoint["total_steps"]
        return "SWEEP_TIMEOUT_EARLY" if ratio < 0.33 else "SWEEP_TIMEOUT_LATE"

    return "RETRY_GENERIC"


def directive_text_for(directive: str) -> str | None:
    return DIRECTIVE_TEXTS.get(directive)


class RetryBudget:
    """Budget di tentativi per un singolo caso (case_id), con log
    strutturato per tentativo — vedi docstring del modulo."""

    def __init__(
        self,
        case_id: str | None = None,
        max_attempts: int = MAX_RETRY_ATTEMPTS,
        feature: str | None = None,
        spec_key: str | None = None,
    ):
        self.case_id = case_id or str(uuid.uuid4())
        self.max_attempts = max_attempts
        self.feature = feature
        self.spec_key = spec_key
        self.history: list[dict] = []

    def record_attempt(
        self,
        attempt: int,
        directive_used: str | None,
        outcome: str,
        outcome_error: str | None,
        failure_class: str | None = None,
    ) -> dict:
        same_as_previous = False
        if self.history:
            prev = self.history[-1]
            same_as_previous = prev["directive_used"] == directive_used and prev["outcome_error"] == outcome_error

        record = {
            "case_id": self.case_id,
            "feature": self.feature,
            "spec_key": self.spec_key,
            "attempt": attempt,
            "directive_used": directive_used,
            "outcome": outcome,
            "outcome_error": outcome_error,
            "same_error_as_previous": same_as_previous,
            "source": "virtual",
            "timestamp": time.time(),
            # [M5, C5 — vedi docs/review_tecnica.md] failure_class:
            # "geometric"|"generation"|None (None per outcome="PASS", non
            # ha senso li'), scritto esplicitamente dal chiamante
            # (generate_and_verify.py) invece di essere dedotto da
            # outcome_error con un parser di stringhe — solo i record
            # "geometric" contano per should_exclude_strategy()
            # (virtual_memory.py). checker_version: hash del checker al
            # momento di QUESTO tentativo — solo i record della versione
            # CORRENTE contano per l'esclusione, un fix del checker azzera
            # il pregiudizio (P4).
            "failure_class": failure_class,
            "checker_version": CHECKER_VERSION,
        }
        self.history.append(record)
        self._append_log(record)
        return record

    def should_stop_early(self) -> bool:
        """Uscita anticipata se lo stesso directive/errore si ripete su
        EARLY_EXIT_CONSECUTIVE_REPEATS tentativi CONSECUTIVI (deciso in
        docs/logbook_fase2.md: "continuare con la stessa classificazione
        che ha gia' fallito una volta e' spreco di calcolo")."""
        if len(self.history) < EARLY_EXIT_CONSECUTIVE_REPEATS:
            return False
        tail = self.history[-EARLY_EXIT_CONSECUTIVE_REPEATS:]
        first = tail[0]
        return all(
            r["directive_used"] == first["directive_used"] and r["outcome_error"] == first["outcome_error"]
            for r in tail
        )

    def budget_exhausted(self) -> bool:
        return len(self.history) >= self.max_attempts

    def _append_log(self, record: dict):
        with open(RETRY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
