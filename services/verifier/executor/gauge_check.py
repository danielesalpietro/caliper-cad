"""
CALIPER — Livello 3, fase 3: calibro Go/No-Go virtuale (M1 + M2).

Estensione CPU-only di verifier-executor, non un nuovo container/motore
fisico — vedi docs/logbook.md (revisione critica, punto 2) e
docs/logbook_fase1.md per il perche'. Controlli booleani ESATTI su B-Rep
(CadQuery/OCC), non una simulazione dinamica: geometrie STEP note e
statiche, nessun codice generato da un LLM entra in questo script.

Invocato dal watcher come sottoprocesso SEPARATO da run_and_measure.py
(non lo stesso processo/subprocess di exec(code)), con un timeout
proprio e distinto (vedi GAUGE_CHECK_TIMEOUT_SECONDS in watcher.py e
"error": "gauge_check_timeout") — decisione presa in docs/handoff_m1.md
("Vincoli gia' decisi") e motivata in docs/logbook_fase2.md: non
concedere piu' tempo a un exec() non fidato solo perche' un'operazione
booleana OCC nota per essere pesante ne richiede di piu' su un solido
gia' passato il check di validita'/manifold.

Tre modalita' (M2 aggiunge "sweep" e "min_distance" a "static_interference"
gia' presente da M1 — vedi docs/logbook_fase2.md per la revisione TC1/TC2/TC3):

- "static_interference" (default, invariata da M1): un solo boolean
  intersect tra part e gauge, cosi' come sono nel file STEP.
- "sweep": interferenza booleana ripetuta in N step discreti lungo un
  percorso di inserimento/avvitamento (translazione lungo un asse, con
  rotazione sincronizzata al passo se e' un avvitamento elicoidale —
  TC1: pitch_mm assente/0, solo translazione; TC2: pitch_mm = passo
  reale della filettatura, stesso passo del preset "thread"). Un solo
  step con interferenza sopra soglia e' sufficiente per FAIL (si esce
  presto, non serve continuare a sprecare calcolo su un caso gia'
  fallito — coerente col motivo per cui questo timeout esiste). Ogni
  step scrive il checkpoint PRIMA di essere tentato (vedi sotto).
- "min_distance": distanza minima esatta (BRepExtrema_DistShapeShape)
  tra due facce del pezzo, identificate come le facce piu' vicine a due
  punti di misura dichiarati nella spec (TC3, snap-fit) — qui NON serve
  un secondo solido "calibro": e' un "calibro virtuale" tra due feature
  dello stesso pezzo, coerente con docs/logbook_fase2.md.

Checkpoint prima dell'operazione pesante: la diagnostica pre-flight
(conteggio facce/edge, validita' topologica BRepCheck_Analyzer) viene
scritta su disco PRIMA del boolean, non dopo — un SIGKILL su timeout non
lascia nulla da ispezionare a posteriori (vedi docs/logbook_fase2.md,
"Formato del log su TIMEOUT del gauge-check"). In modalita' "sweep" il
checkpoint viene riscritto prima di ogni step, cosi' il watcher legge
sempre l'ultimo step TENTATO (non completato) in caso di timeout a meta'
sweep.

[M3] part_source ("models", default, o "generated"): quale radice
usare per part_step_path — "models" per i pezzi di riferimento
noti/statici (MODELS_ROOT, invariato, script M1/M2 esistenti),
"generated" per un pezzo appena esportato da run_and_measure.py
(GENERATED_PARTS_ROOT, /exec/parts — vedi la sua docstring per il
perche' non puo' stare sotto MODELS_ROOT, montato read-only).
gauge_step_path resta sempre relativo a GAUGES_ROOT, invariato.

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
# [run0 RunPod] pool SMP di VTK dimensionato sui core visibili (256 su
# quel pod, quota reale ~27) — stesso motivo e stessa disciplina delle
# due righe sopra. Vedi run_and_measure.py per la nota completa.
os.environ.setdefault("VTK_SMP_MAX_THREADS", "1")

# Radici di mount read-only nel container verifier-executor (vedi
# docker-compose.yml). Sovrascrivibili via env per uso/test fuori
# Docker. I path nel job sono sempre relativi a queste radici, mai
# assoluti — vedi resolve_under_root().
MODELS_ROOT = os.environ.get("GAUGE_CHECK_MODELS_ROOT", "/models")
GAUGES_ROOT = os.environ.get("GAUGE_CHECK_GAUGES_ROOT", "/gauges")

# [M3] Radice SEPARATA per i pezzi appena generati da run_and_measure.py
# (vedi la sua docstring): scrivibile, sottocartella del volume
# verifier_exec gia' in uso, MAI confusa con MODELS_ROOT anche se in
# pratica potrebbe coincidere in alcuni deploy — la distinzione non e'
# solo di percorso ma di provenienza (Rischio #8/M4, stesso principio
# gia' applicato al firewall source: virtual|physical): MODELS_ROOT resta
# riservato a pezzi di riferimento noti/statici, versionati o comunque
# non prodotti dal loop di generazione corrente (criterio di
# accettazione di M1 — "l'AI non entra in questa milestone"); qui invece
# e' esplicitamente l'output della generazione sotto test. part_source
# nel job seleziona quale radice usare, default "models" per restare
# compatibile con tutti gli script M1/M2 esistenti che non lo passano.
GENERATED_PARTS_ROOT = os.environ.get("GAUGE_CHECK_GENERATED_PARTS_ROOT", "/exec/parts")
VALID_PART_SOURCES = ("models", "generated")

# Tolleranza numerica sul volume di intersezione per static_interference
# e per sweep lineare (TC1, geometrie non filettate): un boolean OCC su
# geometrie B-Rep non tangenti perfettamente puo' restituire un volume
# residuo non nullo per errore di rappresentazione in virgola mobile,
# non per interferenza reale. Non e' un parametro di progetto (non e'
# una tolleranza dimensionale del pezzo).
INTERFERENCE_VOLUME_EPSILON_MM3 = 1e-6

# Tolleranza numerica per sweep ELICOIDALE (TC2, filettature), separata
# e piu' larga della precedente — MISURATA empiricamente, non scelta a
# intuito (stessa disciplina gia' applicata al bug OpenBLAS/RLIMIT_AS in
# [v14], vedi docs/logbook.md). Causa nota: il calibro e il pezzo di
# controllo sono costruiti con generate_thread_gauge.build_thread_plug(),
# uno sweep elicoidale FINITO con estremita' tagliate a piatto (non
# smussate) — durante un avvitamento sincronizzato al passo, quando
# l'estremita' piatta del calibro si trova a meta' della corsa (non
# allineata con l'estremita' piatta, anch'essa piatta, del pezzo di
# controllo), il profilo li' non e' un'elica perfettamente periodica e
# lascia un residuo geometrico misurato fino a ~0.31mm3 anche per un
# calibro GO correttamente sottodimensionato (vedi verifica in
# docs/logbook_fase2.md, TC2). Una filettatura reale ha uno smusso di
# imbocco proprio per evitare questo effetto sulla prima spira — i
# calibri di questo progetto non ce l'hanno ancora (nota aperta, non
# taciuta). Soglia fissata a 0.5mm3: sopra il residuo massimo misurato
# per un calibro GO (0.31mm3, con margine), sotto il piu' piccolo valore
# di interferenza vera misurato per un calibro NO-GO lungo la stessa
# corsa (1.36mm3) — separazione di quasi 3x, non un valore di comodo.
HELICAL_SWEEP_VOLUME_EPSILON_MM3 = 0.5

# Tarato empiricamente durante il batch M2 (misurato, non a intuito —
# vedi docs/logbook_fase2.md, "Timeout e isolamento computazionale" e
# "Il numero non e' scelto a intuito"): il worst-case osservato e' lo
# sweep elicoidale completo di TC2 (calibro GO, 21 step, nessuna uscita
# anticipata perche' PASS su tutti) a ~65.5s di CPU-time (user+sys,
# esattamente cio' che RLIMIT_CPU conta — non il wall-clock, che con piu'
# core disponibili era solo ~23s: RLIMIT_CPU somma il tempo CPU di TUTTI
# i thread, e OCC usa multithreading interno per le operazioni booleane
# indipendentemente da OPENBLAS_NUM_THREADS/OMP_NUM_THREADS). Il
# placeholder precedente (30s, M1) era tarato solo su static_interference
# + bbox — insufficiente per un intero sweep elicoidale, causava
# SIGKILL prima ancora di un vero timeout diagnosticabile. Margine
# ~1.5x sul worst-case misurato: 65.5 * 1.5 ~ 98, arrotondato a 100.
# [run1, E2E-8/C8 — docs/logbook_fase6.md] Ricalibrato su hardware
# reale (RTX A6000 + EPYC 7543, affinita' 12 core): worst-case misurato
# 91.35s di CPU-time per lo sweep completo — il vecchio default 100
# (tarato in sandbox M2 su 65.5s) aveva margine quasi nullo li'.
# Stessa disciplina 1.5x: 91.35 * 1.5 ~ 137 -> 140.
GAUGE_CHECK_CPU_LIMIT_SECONDS = int(os.environ.get("GAUGE_CHECK_CPU_LIMIT_SECONDS", "140"))

VALID_MODES = ("static_interference", "sweep", "min_distance")


def set_limits():
    resource.setrlimit(resource.RLIMIT_CPU, (GAUGE_CHECK_CPU_LIMIT_SECONDS, GAUGE_CHECK_CPU_LIMIT_SECONDS))
    # Stessi limiti (e stesse env di override, [run0 RunPod]) di
    # run_and_measure.py — vedi la' per il razionale completo.
    as_bytes = int(os.environ.get("CALIPER_AS_LIMIT_MB", "2048")) * 1024**2
    resource.setrlimit(resource.RLIMIT_AS, (as_bytes, as_bytes))
    stack_mb = os.environ.get("CALIPER_STACK_LIMIT_MB", "")
    if stack_mb:
        stack_bytes = int(stack_mb) * 1024**2
        resource.setrlimit(resource.RLIMIT_STACK, (stack_bytes, stack_bytes))


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def resolve_under_root(root, relative_path):
    """Risolve relative_path sotto root, rifiutando ogni traversal fuori da esso.

    I gauge sono file versionati in git (config/gauges/), i "models"
    sono pezzi STEP noti montati read-only — nessuno dei due e' input
    arbitrario di un LLM, ma il confine di mount resta un confine di
    sicurezza: un path relativo con ".." non deve poter uscirne.
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


def max_entity_tolerance_mm(shape):
    """Tolleranza massima PER-ENTITA' (vertici/edge/facce) del B-Rep —
    operazione economica (nessun boolean), usata come diagnostica
    pre-flight: un valore anomalo qui e' spesso il segnale precoce di
    geometria quasi-degenere, vedi docs/logbook_fase2.md, "Formato del
    log su TIMEOUT del gauge-check", punto 2. Alimenta anche
    TOPOLOGY_TOLERANCE_ANOMALY nel contratto di retry L3->L2 (vedi
    services/orchestrator/retry_policy.py)."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    worst = 0.0
    for kind, cast, tol_fn in (
        (TopAbs_VERTEX, TopoDS.Vertex_s, BRep_Tool.Tolerance_s),
        (TopAbs_EDGE, TopoDS.Edge_s, BRep_Tool.Tolerance_s),
        (TopAbs_FACE, TopoDS.Face_s, BRep_Tool.Tolerance_s),
    ):
        m = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape.wrapped, kind, m)
        for i in range(1, m.Size() + 1):
            worst = max(worst, tol_fn(cast(m.FindKey(i))))
    return worst


def preflight_diagnostics(part, gauge=None):
    part_topo = unique_topology_counts(part)
    diag = {
        "part_face_count": part_topo["face_count"],
        "part_edge_count": part_topo["edge_count"],
        "part_topology_check": topology_check(part),
        "max_entity_tolerance_mm": round(max_entity_tolerance_mm(part), 6),
    }
    if gauge is not None:
        gauge_topo = unique_topology_counts(gauge)
        diag["gauge_face_count"] = gauge_topo["face_count"]
        diag["gauge_edge_count"] = gauge_topo["edge_count"]
        diag["gauge_topology_check"] = topology_check(gauge)
    return diag


def intersection_volume(a, b):
    common = a.intersect(b)
    return (common.Volume() if common.isValid() else 0.0), common


# ---------------------------------------------------------------------------
# static_interference (M1, invariata)
# ---------------------------------------------------------------------------


def run_static_interference(part, gauge, checkpoint_path):
    diagnostics = preflight_diagnostics(part, gauge)
    write_json(checkpoint_path, {"mode": "static_interference", "preflight_diagnostics": diagnostics})

    volume, _ = intersection_volume(part, gauge)
    status = "FAIL" if volume > INTERFERENCE_VOLUME_EPSILON_MM3 else "PASS"
    return {
        "status": status,
        "mode": "static_interference",
        "interference_volume_mm3": round(volume, 6),
        "preflight_diagnostics": diagnostics,
    }


# ---------------------------------------------------------------------------
# sweep (M2, TC1 lineare / TC2 elicoidale)
# ---------------------------------------------------------------------------


def run_sweep(part, gauge, sweep_spec, checkpoint_path):
    import cadquery as cq

    steps = int(sweep_spec.get("steps", 20))
    if steps < 2:
        raise ValueError(f"sweep.steps deve essere >= 2 (percorso con almeno un inizio e una fine): {steps!r}")
    start_offset = float(sweep_spec.get("start_offset_mm", 0.0))
    end_offset = float(sweep_spec.get("end_offset_mm", 0.0))
    pitch_mm = float(sweep_spec.get("pitch_mm", 0.0))
    helical = pitch_mm > 0
    epsilon = HELICAL_SWEEP_VOLUME_EPSILON_MM3 if helical else INTERFERENCE_VOLUME_EPSILON_MM3

    diagnostics = preflight_diagnostics(part, gauge)

    first_interference_step = None
    worst_volume = 0.0
    steps_completed = 0

    for i in range(steps):
        frac = i / (steps - 1)
        offset_mm = start_offset + (end_offset - start_offset) * frac
        angle_deg = (offset_mm / pitch_mm) * 360.0 if helical else 0.0

        # Checkpoint PRIMA di tentare questo step: se il processo viene
        # SIGKILLato durante il boolean di questo step, questo e' l'ultimo
        # dato disponibile a posteriori (vedi docstring del modulo e
        # docs/logbook_fase2.md, "Formato del log su TIMEOUT").
        write_json(
            checkpoint_path,
            {
                "mode": "sweep",
                "helical": helical,
                "preflight_diagnostics": diagnostics,
                "last_checkpoint": {
                    "step": i,
                    "total_steps": steps,
                    "offset_mm": round(offset_mm, 6),
                    "helix_position_deg": round(angle_deg, 3) if helical else None,
                },
            },
        )

        loc = cq.Location((0, 0, offset_mm), (0, 0, 1), angle_deg)
        moved_gauge = gauge.moved(loc)
        volume, _ = intersection_volume(part, moved_gauge)
        steps_completed = i + 1
        worst_volume = max(worst_volume, volume)

        if volume > epsilon:
            first_interference_step = i
            break  # un solo step con interferenza basta per FAIL — non serve continuare

    status = "FAIL" if first_interference_step is not None else "PASS"
    return {
        "status": status,
        "mode": "sweep",
        "interference_volume_mm3": round(worst_volume, 6),
        "preflight_diagnostics": diagnostics,
        "sweep": {
            "helical": helical,
            "pitch_mm": pitch_mm if helical else None,
            "start_offset_mm": start_offset,
            "end_offset_mm": end_offset,
            "steps_total": steps,
            "steps_completed": steps_completed,
            "first_interference_step": first_interference_step,
            "volume_epsilon_mm3": epsilon,
        },
    }


# ---------------------------------------------------------------------------
# min_distance (M2, TC3 — snap-fit, distanza minima tra facce del pezzo)
# ---------------------------------------------------------------------------


def _nearest_face_to_point(part, point_mm):
    """Trova la faccia di 'part' piu' vicina al punto dichiarato (mm).

    I "punti di misura" sono dichiarati nella spec L2.5 come coordinate
    approssimate (l'estensore dello schema non conosce l'indice interno
    delle facce del solido generato) — qui si risolve al volo la faccia
    B-Rep piu' vicina, stesso principio del "calibro virtuale posizionato
    in punti di misura noti" in docs/logbook_fase2.md (TC3).
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*point_mm)).Vertex()

    best_face = None
    best_distance = None
    for face in part.Faces():
        dss = BRepExtrema_DistShapeShape(vertex, face.wrapped)
        if not dss.IsDone():
            continue
        d = dss.Value()
        if best_distance is None or d < best_distance:
            best_distance = d
            best_face = face
    if best_face is None:
        raise ValueError(f"nessuna faccia trovata vicino al punto di misura {point_mm}")
    return best_face, best_distance


def run_min_distance(part, min_distance_spec, checkpoint_path):
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    point_a = tuple(float(v) for v in min_distance_spec["point_a_mm"])
    point_b = tuple(float(v) for v in min_distance_spec["point_b_mm"])
    nominal_mm = float(min_distance_spec["nominal_mm"])
    tolerance_mm = float(min_distance_spec["tolerance_mm"])

    diagnostics = preflight_diagnostics(part)
    write_json(checkpoint_path, {"mode": "min_distance", "preflight_diagnostics": diagnostics})

    face_a, snap_distance_a = _nearest_face_to_point(part, point_a)
    face_b, snap_distance_b = _nearest_face_to_point(part, point_b)

    dss = BRepExtrema_DistShapeShape(face_a.wrapped, face_b.wrapped)
    measured_mm = dss.Value() if dss.IsDone() else None
    if measured_mm is None:
        raise RuntimeError("BRepExtrema_DistShapeShape non ha prodotto un risultato tra le due facce individuate")

    status = "PASS" if abs(measured_mm - nominal_mm) <= tolerance_mm else "FAIL"
    return {
        "status": status,
        "mode": "min_distance",
        "interference_volume_mm3": None,
        "preflight_diagnostics": diagnostics,
        "min_distance": {
            "point_a_mm": list(point_a),
            "point_b_mm": list(point_b),
            "face_snap_distance_a_mm": round(snap_distance_a, 6),
            "face_snap_distance_b_mm": round(snap_distance_b, 6),
            "measured_mm": round(measured_mm, 6),
            "nominal_mm": nominal_mm,
            "tolerance_mm": tolerance_mm,
        },
    }


def main():
    job_path, result_path, checkpoint_path = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(job_path, "r", encoding="utf-8") as f:
        job = json.load(f)
    gc = job["gauge_check"]
    mode = gc.get("mode", "static_interference")
    part_rel = gc["part_step_path"]
    gauge_rel = gc.get("gauge_step_path")
    part_source = gc.get("part_source", "models")

    result = {
        "execution": "FAIL",
        "error": None,
        "measurements": None,
        "dimensional_check": None,
        "gauge_check": {
            "status": "FAIL",
            "mode": mode,
            "part_step_path": part_rel,
            "part_source": part_source,
            "gauge_step_path": gauge_rel,
            "interference_volume_mm3": None,
            "preflight_diagnostics": None,
            "source": "virtual",
        },
    }

    if mode not in VALID_MODES:
        result["error"] = f"mode non valido: {mode!r} (attesi: {VALID_MODES})"
        write_json(result_path, result)
        return

    if part_source not in VALID_PART_SOURCES:
        result["error"] = f"part_source non valido: {part_source!r} (attesi: {VALID_PART_SOURCES})"
        write_json(result_path, result)
        return

    import cadquery as cq  # import qui, dopo set_limits() e dopo le env OpenBLAS/OMP

    try:
        part_root = MODELS_ROOT if part_source == "models" else GENERATED_PARTS_ROOT
        part_path = resolve_under_root(part_root, part_rel)
        part = cq.importers.importStep(part_path).val()
        gauge = None
        if gauge_rel is not None:
            gauge_path = resolve_under_root(GAUGES_ROOT, gauge_rel)
            gauge = cq.importers.importStep(gauge_path).val()
    except Exception as e:
        result["error"] = f"import STEP fallito: {type(e).__name__}: {e}"
        write_json(result_path, result)
        return

    try:
        if mode == "static_interference":
            if gauge is None:
                raise ValueError("static_interference richiede gauge_step_path")
            outcome = run_static_interference(part, gauge, checkpoint_path)
        elif mode == "sweep":
            if gauge is None:
                raise ValueError("sweep richiede gauge_step_path")
            outcome = run_sweep(part, gauge, gc.get("sweep", {}), checkpoint_path)
        elif mode == "min_distance":
            outcome = run_min_distance(part, gc.get("min_distance", {}), checkpoint_path)
    except Exception as e:
        result["error"] = f"{mode} fallito: {type(e).__name__}: {e}"
        # diagnostica pre-flight, se gia' scritta nel checkpoint, resta li'
        # per l'ispezione — non duplicata qui (vedi checkpoint_path).
        write_json(result_path, result)
        return

    result["execution"] = outcome["status"]
    result["gauge_check"].update(outcome)
    write_json(result_path, result)

    # Successo: il checkpoint pre-flight/per-step non serve piu' (non c'e'
    # stato un timeout da diagnosticare a posteriori).
    if os.path.exists(checkpoint_path):
        os.remove(checkpoint_path)


if __name__ == "__main__":
    set_limits()
    main()
