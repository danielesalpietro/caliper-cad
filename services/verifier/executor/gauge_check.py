"""
CALIPER — Livello 3, fase 3: calibro Go/No-Go virtuale (M1).

Estensione CPU-only di verifier-executor, non un nuovo container/motore
fisico — vedi docs/logbook.md (revisione critica, punto 2) e
docs/logbook_fase1.md per il perche'. Controllo booleano ESATTO su B-Rep
(CadQuery/OCC), non una simulazione dinamica: due solidi STEP noti e
statici (un pezzo e un calibro), nessun codice generato da un LLM entra
in questo script (l'AI non entra in M1).

Invocato dal watcher come sottoprocesso SEPARATO da run_and_measure.py
(non lo stesso processo/subprocess di exec(code)), con un timeout
proprio e distinto (vedi GAUGE_CHECK_TIMEOUT_SECONDS in watcher.py e
"error": "gauge_check_timeout") — decisione presa in docs/handoff_m1.md
("Vincoli gia' decisi") e motivata in docs/logbook_fase2.md: non
concedere piu' tempo a un exec() non fidato solo perche' un'operazione
booleana OCC nota per essere pesante ne richiede di piu' su un solido
gia' passato il check di validita'/manifold.

SCOPE M1 (deliberatamente limitato): solo interferenza STATICA (nessuno
sweep lungo un percorso di inserimento/avvitamento — quello e' TC1/TC2,
M2, vedi docs/logbook_fase2.md). Il criterio di accettazione di M1 e'
"dato un pezzo STEP noto e un calibro STEP noto, PASS/FAIL con volume di
intersezione via il protocollo job/result esistente" — niente di piu'.

Checkpoint prima dell'operazione pesante: la diagnostica pre-flight
(conteggio facce/edge, validita' topologica BRepCheck_Analyzer) viene
scritta su disco PRIMA del boolean, non dopo — un SIGKILL su timeout non
lascia nulla da ispezionare a posteriori (vedi docs/logbook_fase2.md,
"Formato del log su TIMEOUT del gauge-check"). Qui e' un solo checkpoint
pre-flight (non N checkpoint per step intermedi): M1 non ha uno sweep da
discretizzare, quello e' scope di M2/TC1-TC2.

Uso: python gauge_check.py <job.json> <result.json> <checkpoint.json>
"""

import json
import os
import resource
import sys

# Stesso motivo di run_and_measure.py: OpenBLAS prealloca memoria per
# thread in base ai core visibili, non alla dimensione del problema.
# Va impostato prima di "import cadquery".
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

# Radici di mount read-only nel container verifier-executor (vedi
# docker-compose.yml). Sovrascrivibili via env per uso/test fuori
# Docker. I path nel job sono sempre relativi a queste radici, mai
# assoluti — vedi resolve_under_root().
MODELS_ROOT = os.environ.get("GAUGE_CHECK_MODELS_ROOT", "/models")
GAUGES_ROOT = os.environ.get("GAUGE_CHECK_GAUGES_ROOT", "/gauges")

# Tolleranza numerica sul volume di intersezione: un boolean OCC su
# geometrie B-Rep non tangenti perfettamente puo' restituire un volume
# residuo non nullo per errore di rappresentazione in virgola mobile,
# non per interferenza reale. Non e' un parametro di progetto (non e'
# una tolleranza dimensionale del pezzo).
INTERFERENCE_VOLUME_EPSILON_MM3 = 1e-6

# Placeholder, non tarato empiricamente — la calibrazione sul worst-case
# osservato durante il batch e' scope di M2 (docs/logbook_fase2.md,
# "Timeout e isolamento computazionale"). Il rapporto interno/esterno
# ricalca quello gia' in uso per run_and_measure.py (10s interno / 15s
# esterno in watcher.py): margine esterno maggiore di quello interno.
GAUGE_CHECK_CPU_LIMIT_SECONDS = 30


def set_limits():
    resource.setrlimit(resource.RLIMIT_CPU, (GAUGE_CHECK_CPU_LIMIT_SECONDS, GAUGE_CHECK_CPU_LIMIT_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))  # 2GB, stesso limite di run_and_measure.py


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def resolve_under_root(root, relative_path):
    """Risolve relative_path sotto root, rifiutando ogni traversal fuori da esso.

    I gauge sono file versionati in git (config/gauges/), i "models"
    sono pezzi STEP noti montati read-only — nessuno dei due e' input
    arbitrario di un LLM in M1, ma il confine di mount resta un confine
    di sicurezza: un path relativo con ".." non deve poter uscirne.
    """
    if os.path.isabs(relative_path):
        raise ValueError(f"path assoluto non ammesso: {relative_path!r}")
    root_real = os.path.realpath(root)
    candidate_real = os.path.realpath(os.path.join(root, relative_path))
    if candidate_real != root_real and not candidate_real.startswith(root_real + os.sep):
        raise ValueError(f"path fuori dalla radice consentita ({root}): {relative_path!r}")
    return candidate_real


def unique_topology_counts(shape):
    """Conteggio di facce/edge UNICI (non per-occorrenza) via TopTools_IndexedMapOfShape.

    TopExp_Explorer da solo conta occorrenze orientate (un edge condiviso
    da due facce viene visto due volte) — fuorviante come segnale di
    complessita'. Qui si usa la mappa indicizzata per un conteggio reale.
    """
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    def count(kind):
        m = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape.wrapped, kind, m)
        return m.Size()

    return {"face_count": count(TopAbs_FACE), "edge_count": count(TopAbs_EDGE)}


def topology_check(shape):
    from OCP.BRepCheck import BRepCheck_Analyzer

    return "ok" if BRepCheck_Analyzer(shape.wrapped).IsValid() else "invalid"


def preflight_diagnostics(part, gauge):
    part_topo = unique_topology_counts(part)
    gauge_topo = unique_topology_counts(gauge)
    return {
        "part_face_count": part_topo["face_count"],
        "part_edge_count": part_topo["edge_count"],
        "part_topology_check": topology_check(part),
        "gauge_face_count": gauge_topo["face_count"],
        "gauge_edge_count": gauge_topo["edge_count"],
        "gauge_topology_check": topology_check(gauge),
    }


def main():
    job_path, result_path, checkpoint_path = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)
    gc = job["gauge_check"]
    part_rel = gc["part_step_path"]
    gauge_rel = gc["gauge_step_path"]

    result = {
        "execution": "FAIL",
        "error": None,
        "measurements": None,
        "dimensional_check": None,
        "gauge_check": {
            "status": "FAIL",
            "mode": "static_interference",
            "part_step_path": part_rel,
            "gauge_step_path": gauge_rel,
            "interference_volume_mm3": None,
            "preflight_diagnostics": None,
            "source": "virtual",
        },
    }

    import cadquery as cq  # import qui, dopo set_limits() e dopo le env OpenBLAS/OMP

    try:
        part_path = resolve_under_root(MODELS_ROOT, part_rel)
        gauge_path = resolve_under_root(GAUGES_ROOT, gauge_rel)
        part = cq.importers.importStep(part_path).val()
        gauge = cq.importers.importStep(gauge_path).val()
    except Exception as e:
        result["error"] = f"import STEP fallito: {type(e).__name__}: {e}"
        write_json(result_path, result)
        return

    # Checkpoint PRIMA del boolean pesante: se il processo viene
    # SIGKILLato per timeout, il watcher legge questo file — e' l'unico
    # dato disponibile a posteriori (vedi docstring del modulo).
    diagnostics = preflight_diagnostics(part, gauge)
    write_json(
        checkpoint_path,
        {"preflight_diagnostics": diagnostics, "part_step_path": part_rel, "gauge_step_path": gauge_rel},
    )

    try:
        common = part.intersect(gauge)
        volume = common.Volume() if common.isValid() else 0.0
    except Exception as e:
        result["error"] = f"boolean intersection fallita: {type(e).__name__}: {e}"
        result["gauge_check"]["preflight_diagnostics"] = diagnostics
        write_json(result_path, result)
        return

    status = "FAIL" if volume > INTERFERENCE_VOLUME_EPSILON_MM3 else "PASS"
    result["execution"] = status
    result["gauge_check"]["status"] = status
    result["gauge_check"]["interference_volume_mm3"] = round(volume, 6)
    result["gauge_check"]["preflight_diagnostics"] = diagnostics
    write_json(result_path, result)

    # Successo: il checkpoint pre-flight non serve piu' (non c'e' stato
    # un timeout da diagnosticare a posteriori).
    if os.path.exists(checkpoint_path):
        os.remove(checkpoint_path)


if __name__ == "__main__":
    set_limits()
    main()
