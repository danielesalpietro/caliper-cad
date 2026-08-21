"""
CALIPER — schema dei vincoli di sketch 2D "sketch-first" (M3).

Vedi docs/logbook_fase3.md e l'issue GitHub #4. Motivazione (gia' nel
logbook, ripetuta qui perche' governa le scelte di questo modulo):
forzare L2 a restituire un insieme dichiarativo di vincoli (punti, linee,
archi, quote, tipo di vincolo) invece di codice CadQuery libero restringe
la superficie che l'LLM puo' inventare (profili, coordinate, chiamate API
inesistenti — vedi Rischio #1/#3/#5 e l'aneddoto reale su
texture_thread()/clearance= inventati), validabile a livello di SCHEMA
prima ancora di raggiungere il kernel geometrico (README §3.1: vincolare
l'output allo schema a livello di decoding, non solo dopo generazione).

Questo modulo e' PURO (nessuna dipendenza da cadquery/OCC): la
validazione strutturale/numerica deve poter girare anche senza il kernel
geometrico installato, stesso principio del controllo statico "fase 1" di
services/verifier/app.py (sintassi prima dell'esecuzione).

Ambito M3 (vedi docs/logbook_fase3.md — milestone ristretta al preset
"thread"): un solo "operation.type" supportato, "helical_thread_cut" — un
profilo a V (3 punti, 3 linee, stesso profilo NON troncato gia' usato in
config/gauges/generate_thread_gauge.py) sweepato elicoidalmente e
sottratto da un blocco ospite, per produrre un FORO filettato (coerente
con l'esempio L2.5 in architettura — non un tampone esterno, quello resta
il calibro, non il pezzo generato). Altri operation.type (extrude/revolve
semplice, per feature diverse da "thread") sono lasciati per M3+, non
inventati qui senza un caso d'uso reale (stessa disciplina di
presets.json: "defined: false" invece di fingere copertura).

Livello di validazione, in ordine (si ferma al primo che fallisce, stesso
principio del controllo statico a fasi del Livello 3):
1. Struttura/tipi (chiavi richieste presenti, tipi corretti).
2. Riferimenti (ogni line/arc/dimension punta a id esistenti).
3. Topologia (points/lines formano un'unica polilinea CHIUSA — ogni punto
   usato esattamente da 2 segmenti).
4. Consistenza numerica: le quote dichiarate (dimensions) devono
   corrispondere, entro tolleranza, a cio' che le coordinate GIA'
   codificano — un LLM che dichiara "angolo 60 gradi" in una dimension ma
   scrive coordinate che ne implicano uno diverso viene bloccato QUI,
   prima del kernel geometrico (stessa famiglia di bug gia' vista:
   un'affermazione plausibile ma falsa, vedi Rischio #3).
"""

import math

NUMERIC_TOLERANCE_MM = 1e-6
NUMERIC_TOLERANCE_DEG = 1e-3

SUPPORTED_OPERATION_TYPES = ("helical_thread_cut",)


class SketchValidationError(Exception):
    """Sollevata da validate_sketch_spec con la lista di errori trovati
    (non si ferma al primo in fase 1/2, per dare tutti gli errori
    strutturali in un colpo solo — utile se questo finisce in un
    retry_context in futuro, vedi docs/logbook_fase2.md)."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def _require(cond, errors, msg):
    if not cond:
        errors.append(msg)
    return cond


def _validate_structure(spec: dict, errors: list[str]) -> bool:
    if not _require(isinstance(spec, dict), errors, "la spec deve essere un oggetto JSON"):
        return False

    ok = True
    ok &= _require("feature" in spec and isinstance(spec["feature"], str), errors, "manca 'feature' (stringa)")
    ok &= _require("sketch" in spec and isinstance(spec["sketch"], dict), errors, "manca 'sketch' (oggetto)")
    ok &= _require("operation" in spec and isinstance(spec["operation"], dict), errors, "manca 'operation' (oggetto)")
    if not ok:
        return False

    sketch = spec["sketch"]
    for key in ("points", "lines", "arcs", "dimensions"):
        ok &= _require(key in sketch and isinstance(sketch[key], list), errors, f"sketch.{key} deve essere una lista")
    if not ok:
        return False

    for i, p in enumerate(sketch["points"]):
        ok &= _require(
            isinstance(p, dict) and {"id", "x", "y"} <= p.keys() and isinstance(p["id"], str),
            errors,
            f"sketch.points[{i}] deve avere 'id' (stringa), 'x', 'y' (numeri)",
        )
        if ok:
            ok &= _require(
                isinstance(p["x"], (int, float)) and isinstance(p["y"], (int, float)),
                errors,
                f"sketch.points[{i}] ('{p.get('id')}'): x/y devono essere numeri",
            )

    for i, ln in enumerate(sketch["lines"]):
        ok &= _require(
            isinstance(ln, dict) and {"id", "start", "end"} <= ln.keys(),
            errors,
            f"sketch.lines[{i}] deve avere 'id', 'start', 'end'",
        )

    for i, arc in enumerate(sketch["arcs"]):
        ok &= _require(
            isinstance(arc, dict) and {"id", "center", "radius", "start_angle_deg", "end_angle_deg"} <= arc.keys(),
            errors,
            f"sketch.arcs[{i}] deve avere 'id', 'center', 'radius', 'start_angle_deg', 'end_angle_deg'",
        )

    for i, dim in enumerate(sketch["dimensions"]):
        ok &= _require(
            isinstance(dim, dict) and {"type", "refs"} <= dim.keys(),
            errors,
            f"sketch.dimensions[{i}] deve avere 'type', 'refs'",
        )
        if ok and dim.get("type") not in ("distance", "angle", "radius"):
            errors.append(f"sketch.dimensions[{i}]: type deve essere 'distance', 'angle' o 'radius', non {dim.get('type')!r}")
            ok = False

    op = spec["operation"]
    ok &= _require(op.get("type") in SUPPORTED_OPERATION_TYPES, errors, f"operation.type deve essere uno di {SUPPORTED_OPERATION_TYPES}")
    ok &= _require(isinstance(op.get("host"), dict) and op["host"].get("type") == "block", errors, "operation.host deve essere {'type': 'block', 'size_mm': [x,y,z]}")
    if isinstance(op.get("host"), dict):
        size = op["host"].get("size_mm")
        ok &= _require(isinstance(size, list) and len(size) == 3 and all(isinstance(v, (int, float)) and v > 0 for v in size), errors, "operation.host.size_mm deve essere [x,y,z] con tre numeri positivi")
    for field in ("major_diameter_mm", "pitch_mm", "engagement_length_mm"):
        ok &= _require(
            isinstance(op.get(field), (int, float)) and op[field] > 0,
            errors,
            f"operation.{field} deve essere un numero positivo",
        )

    return ok


def _validate_references_and_topology(spec: dict, errors: list[str]) -> bool:
    sketch = spec["sketch"]
    point_ids = {p["id"] for p in sketch["points"]}
    ok = True

    if len(point_ids) != len(sketch["points"]):
        errors.append("sketch.points contiene id duplicati")
        ok = False

    line_ids = set()
    for ln in sketch["lines"]:
        if ln["id"] in line_ids:
            errors.append(f"sketch.lines: id duplicato {ln['id']!r}")
            ok = False
        line_ids.add(ln["id"])
        for ref_key in ("start", "end"):
            if ln[ref_key] not in point_ids:
                errors.append(f"sketch.lines[{ln['id']}].{ref_key} riferisce un punto inesistente: {ln[ref_key]!r}")
                ok = False

    for arc in sketch["arcs"]:
        if arc["center"] not in point_ids:
            errors.append(f"sketch.arcs[{arc['id']}].center riferisce un punto inesistente: {arc['center']!r}")
            ok = False

    entity_ids = point_ids | line_ids | {a["id"] for a in sketch["arcs"]}
    for dim in sketch["dimensions"]:
        for ref in dim["refs"]:
            if ref not in entity_ids:
                errors.append(f"sketch.dimensions: riferimento a entita' inesistente {ref!r}")
                ok = False

    if not ok:
        return False

    # Topologia: per il profilo a V (M3, operation.type="helical_thread_cut")
    # serve un'UNICA polilinea chiusa — ogni punto usato esattamente da 2
    # segmenti (linee o archi), nessun punto isolato, nessuna diramazione.
    # Un profilo che non chiude non e' sweepabile in un solido valido (lo
    # stesso .close() che generate_thread_gauge.py usa implicitamente).
    usage = {pid: 0 for pid in point_ids}
    for ln in sketch["lines"]:
        usage[ln["start"]] += 1
        usage[ln["end"]] += 1
    # Gli archi non sono usati dal profilo a V in M3 (solo linee), ma se
    # presenti contano comunque per la chiusura del contorno.
    for arc in sketch["arcs"]:
        usage[arc["center"]] = usage.get(arc["center"], 0)  # il centro non e' sul contorno, non conta

    non_closed = [pid for pid, count in usage.items() if count != 2]
    if non_closed:
        errors.append(
            f"il profilo dello sketch non e' una polilinea chiusa: punti con grado != 2: {non_closed} "
            "(ogni punto deve essere toccato da esattamente due segmenti)"
        )
        return False

    return True


def point_lookup(sketch: dict) -> dict:
    return {p["id"]: (float(p["x"]), float(p["y"])) for p in sketch["points"]}


def _validate_numeric_consistency(spec: dict, errors: list[str]) -> bool:
    sketch = spec["sketch"]
    points = point_lookup(sketch)
    lines_by_id = {ln["id"]: ln for ln in sketch["lines"]}
    ok = True

    for dim in sketch["dimensions"]:
        dtype = dim["type"]
        refs = dim["refs"]

        if dtype == "distance":
            if len(refs) != 2 or refs[0] not in points or refs[1] not in points:
                errors.append(f"dimension 'distance' richiede due id di punti validi in refs, trovato {refs!r}")
                ok = False
                continue
            (x1, y1), (x2, y2) = points[refs[0]], points[refs[1]]
            measured = math.hypot(x2 - x1, y2 - y1)
            declared = dim.get("value_mm")
            if not isinstance(declared, (int, float)):
                errors.append(f"dimension 'distance' su {refs}: manca value_mm numerico")
                ok = False
            elif abs(measured - declared) > NUMERIC_TOLERANCE_MM:
                errors.append(
                    f"dimension 'distance' su {refs}: dichiarata {declared}mm, "
                    f"ma le coordinate implicano {measured:.6f}mm (differenza > {NUMERIC_TOLERANCE_MM}mm) — "
                    "quota inconsistente con la geometria dichiarata"
                )
                ok = False

        elif dtype == "angle":
            if len(refs) != 2 or refs[0] not in lines_by_id or refs[1] not in lines_by_id:
                errors.append(f"dimension 'angle' richiede due id di linee valide in refs, trovato {refs!r}")
                ok = False
                continue
            angle = _angle_between_lines_deg(lines_by_id[refs[0]], lines_by_id[refs[1]], points)
            declared = dim.get("value_deg")
            if not isinstance(declared, (int, float)):
                errors.append(f"dimension 'angle' su {refs}: manca value_deg numerico")
                ok = False
            elif angle is None:
                errors.append(f"dimension 'angle' su {refs}: linee degeneri (lunghezza nulla), impossibile calcolare l'angolo")
                ok = False
            elif abs(angle - declared) > NUMERIC_TOLERANCE_DEG:
                errors.append(
                    f"dimension 'angle' su {refs}: dichiarato {declared} gradi, "
                    f"ma le coordinate implicano {angle:.6f} gradi (differenza > {NUMERIC_TOLERANCE_DEG} gradi) — "
                    "quota inconsistente con la geometria dichiarata"
                )
                ok = False

        elif dtype == "radius":
            arcs_by_id = {a["id"]: a for a in sketch["arcs"]}
            if len(refs) != 1 or refs[0] not in arcs_by_id:
                errors.append(f"dimension 'radius' richiede un id di arco valido in refs, trovato {refs!r}")
                ok = False
                continue
            declared = dim.get("value_mm")
            actual = arcs_by_id[refs[0]]["radius"]
            if not isinstance(declared, (int, float)):
                errors.append(f"dimension 'radius' su {refs}: manca value_mm numerico")
                ok = False
            elif abs(actual - declared) > NUMERIC_TOLERANCE_MM:
                errors.append(f"dimension 'radius' su {refs}: dichiarato {declared}mm, arco ha raggio {actual}mm")
                ok = False

    return ok


def _angle_between_lines_deg(line_a: dict, line_b: dict, points: dict) -> float | None:
    """Angolo INTERNO (0-180 gradi) tra due segmenti ADIACENTI, misurato
    al vertice condiviso — NON l'angolo grezzo tra le direzioni
    start->end delle due linee: per due linee in sequenza in un profilo
    (es. crest->root, root->crest — l'ordine naturale di un contorno
    tracciato in giro), una direzione punta VERSO il vertice condiviso e
    l'altra si allontana da esso, il che darebbe l'angolo SUPPLEMENTARE
    (180 - angolo vero) se calcolato ingenuamente sulle due direzioni
    cosi' come sono — bug trovato scrivendo il primo caso di prova a
    mano per il profilo a V (angolo atteso 60 gradi, calcolo ingenuo
    dava 120): qui si trova il vertice condiviso e si costruiscono
    entrambi i vettori USCENTI da esso, indipendentemente da come
    start/end sono assegnati nelle due linee."""
    ids_a = {line_a["start"], line_a["end"]}
    ids_b = {line_b["start"], line_b["end"]}
    shared = ids_a & ids_b
    if len(shared) != 1:
        return None  # linee non adiacenti (0 o 2 punti in comune) — angolo non definito qui
    vertex = next(iter(shared))
    other_a = line_a["end"] if line_a["start"] == vertex else line_a["start"]
    other_b = line_b["end"] if line_b["start"] == vertex else line_b["start"]

    vx, vy = points[vertex]
    ax, ay = points[other_a][0] - vx, points[other_a][1] - vy
    bx, by = points[other_b][0] - vx, points[other_b][1] - vy
    na, nb = math.hypot(ax, ay), math.hypot(bx, by)
    if na == 0 or nb == 0:
        return None
    cos_theta = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
    return math.degrees(math.acos(cos_theta))


CROSS_FIELD_TOLERANCE_MM = 1e-3


def _validate_operation_sketch_consistency(spec: dict, errors: list[str]) -> bool:
    """operation.* e sketch.* codificano in parte la STESSA informazione
    in due punti diversi della spec (il raggio di cresta del profilo e
    operation.major_diameter_mm, la profondita' dell'ospite e
    operation.engagement_length_mm) — devono concordare, stessa logica
    del controllo dimensionale sopra ma tra sezioni diverse della spec
    invece che dentro la sola sketch."""
    sketch = spec["sketch"]
    op = spec["operation"]
    points = point_lookup(sketch)
    ok = True

    max_x = max(x for x, _ in points.values())
    declared_major_radius = op["major_diameter_mm"] / 2.0
    if abs(max_x - declared_major_radius) > CROSS_FIELD_TOLERANCE_MM:
        errors.append(
            f"operation.major_diameter_mm implica un raggio di cresta {declared_major_radius}mm, "
            f"ma il punto piu' esterno dello sketch e' a {max_x}mm — sketch e operation inconsistenti"
        )
        ok = False

    size = op["host"]["size_mm"]
    if size[0] <= op["major_diameter_mm"] or size[1] <= op["major_diameter_mm"]:
        errors.append(
            f"operation.host.size_mm {size[:2]} deve essere maggiore del diametro maggiore "
            f"{op['major_diameter_mm']}mm su X e Y (il foro non ci starebbe dentro)"
        )
        ok = False
    # Deve essere UGUALE (non solo >=), non un dettaglio arbitrario: il
    # compilatore taglia la filettatura solo per engagement_length_mm
    # (stessa lunghezza del calibro, generate_thread_gauge.py) — un
    # blocco PIU' profondo lascerebbe materiale pieno NON filettato oltre
    # quella quota, e durante lo sweep del collaudo Go/No-Go (che
    # trasla il calibro fino a offset=engagement_length_mm, vedi
    # generate_and_verify.gauge_check_job_for_preset) il calibro ci
    # sbatterebbe contro appena superata la profondita' di impegno —
    # bug reale trovato scrivendo il primo caso di prova a mano per
    # questo compilatore (interferenza falsa, ~8.4mm3, al primo step di
    # sweep): un foro passante (through-hole), stessa scelta gia'
    # validata in verify_gauge_check_tc2.py (BLOCK_SIZE Z == LENGTH_MM),
    # non una costruzione indipendente. Un foro cieco piu' profondo del
    # filetto e' un caso reale ma richiederebbe un controcavo/gioco oltre
    # l'impegno che questo compilatore non modella ancora — fuori scope
    # M3, non un'assunzione silenziosa.
    if abs(size[2] - op["engagement_length_mm"]) > CROSS_FIELD_TOLERANCE_MM:
        errors.append(
            f"operation.host.size_mm[2]={size[2]}mm deve essere ESATTAMENTE uguale a "
            f"engagement_length_mm={op['engagement_length_mm']}mm in questa milestone "
            "(foro passante, nessun controcavo oltre l'impegno — vedi sketch_schema.py)"
        )
        ok = False

    return ok


def validate_sketch_spec(spec: dict) -> list[str]:
    """Valida una spec sketch-first. Ritorna la lista di errori (vuota se
    valida) — non solleva mai, il chiamante decide come reagire (in
    generate_and_verify.py, un errore di schema e' un FAIL immediato del
    tentativo, classificato RETRY_GENERIC — vedi retry_policy.py, mai un
    hint specifico perche' non e' un errore geometrico che
    classify_checkpoint sa interpretare)."""
    errors: list[str] = []
    if not _validate_structure(spec, errors):
        return errors
    if not _validate_references_and_topology(spec, errors):
        return errors
    _validate_numeric_consistency(spec, errors)
    _validate_operation_sketch_consistency(spec, errors)
    return errors


def assert_valid_sketch_spec(spec: dict) -> None:
    errors = validate_sketch_spec(spec)
    if errors:
        raise SketchValidationError(errors)
