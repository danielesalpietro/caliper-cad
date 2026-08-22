"""
CALIPER — compilatore vincoli-2D -> CadQuery (M3).

Vedi docs/logbook_fase3.md. Prende una spec sketch-first GIA' VALIDATA
(vedi sketch_schema.py — chiamare assert_valid_sketch_spec() prima di
usare questo modulo, non fidarsi di una spec non validata) e produce una
stringa di codice CadQuery — non esegue MAI cadquery direttamente, non
importa nemmeno il pacchetto: e' testo, generato deterministicamente
(nessun LLM in questo passaggio). Il codice prodotto passa poi dallo
STESSO confine di fiducia gia' in uso per il codice libero generato da
L2 (Rischio #9): eseguito solo dentro verifier-executor isolato, mai qui.

Ambito M3, un solo compilatore: compile_thread_sketch_to_code(), per
operation.type == "helical_thread_cut" (profilo a V sweepato
elicoidalmente, sottratto da un blocco ospite -> foro filettato, vedi
sketch_schema.py per il perche' di questo scope ristretto).

Riusa la stessa tecnica gia' validata in
config/gauges/generate_thread_gauge.py (build_thread_plug) e in
services/verifier/executor/verify_gauge_check_tc2.py
(build_nominal_thread_cavity: block.cut(build_thread_plug(...))) — non
una costruzione geometrica indipendente, per non rischiare la stessa
classe di incoerenza di fase/passo/verso gia' evitata li'. La differenza
e' che qui il profilo a V non e' hardcoded (3 .lineTo() fissi) ma
ricostruito dai punti/linee dichiarati nello sketch — la stessa forma,
ma percorsa dinamicamente seguendo la topologia dichiarata invece di tre
chiamate scritte a mano.

CUT_OVERLAP_MM: stessa costante e stesso motivo di generate_thread_gauge.py
(sovrapposizione dei punti di cresta oltre il raggio maggiore nominale,
altrimenti l'OCC boolean tra il cilindro di base e la scanalatura produce
un solido non manifold per tangenza esatta) — un dettaglio numerico di
stabilizzazione OCC, non un'informazione che lo sketch (o l'LLM che lo
genera) deve conoscere, per lo stesso principio gia' applicato ai
checkpoint di gauge_check.py in retry_policy.py: i numeri
d'implementazione restano fuori da cio' che il livello dichiarativo
vede.
"""

import math

from sketch_schema import CROSS_FIELD_TOLERANCE_MM, SketchValidationError, point_lookup, assert_valid_sketch_spec

CUT_OVERLAP_MM = 0.05


def _trace_closed_loop(sketch: dict) -> list[str]:
    """Ordina i punti dello sketch percorrendo le linee in sequenza,
    partendo da lines[0]. Precondizione (garantita da
    sketch_schema.validate_sketch_spec): ogni punto ha grado esattamente
    2 — un'unica polilinea chiusa, nessuna diramazione. Ritorna la
    sequenza di id punto (senza ripetere il primo alla fine)."""
    lines = sketch["lines"]
    if not lines:
        raise SketchValidationError(["sketch.lines e' vuoto: nessun profilo da tracciare"])

    adjacency: dict[str, list[str]] = {}
    for ln in lines:
        adjacency.setdefault(ln["start"], []).append(ln["end"])
        adjacency.setdefault(ln["end"], []).append(ln["start"])

    start = lines[0]["start"]
    ordered = [start]
    prev, current = None, start
    while True:
        candidates = [p for p in adjacency[current] if p != prev]
        # Se entrambi i vicini sono validi (caso non-degenere), evita di
        # tornare subito indietro sul primo passo scegliendo un vicino
        # diverso da 'prev'; su un ciclo semplice con grado 2 ovunque
        # questo basta a percorrere l'intero contorno una sola volta.
        nxt = candidates[0] if candidates else adjacency[current][0]
        if nxt == start:
            break
        ordered.append(nxt)
        prev, current = current, nxt
        if len(ordered) > len(adjacency):
            raise SketchValidationError(["il profilo non chiude entro il numero di punti dichiarati — topologia malformata"])
    return ordered


def compile_thread_sketch_to_code(spec: dict) -> str:
    """spec deve avere operation.type == 'helical_thread_cut' e passare
    assert_valid_sketch_spec(). Ritorna codice CadQuery (stringa) che
    assegna 'result' — stesso contratto del codice libero generato da L2
    (vedi services/verifier/executor/run_and_measure.py:
    'result' non trovato dopo l'esecuzione -> FAIL)."""
    assert_valid_sketch_spec(spec)
    op = spec["operation"]
    if op["type"] != "helical_thread_cut":
        raise SketchValidationError([f"compile_thread_sketch_to_code non supporta operation.type={op['type']!r}"])
    if spec["sketch"]["arcs"]:
        raise SketchValidationError(["compile_thread_sketch_to_code non supporta archi nel profilo (M3, solo profilo a V con linee)"])

    sketch = spec["sketch"]
    points = point_lookup(sketch)
    ordered_ids = _trace_closed_loop(sketch)

    r_major = op["major_diameter_mm"] / 2.0
    pitch_mm = op["pitch_mm"]
    engagement_length_mm = op["engagement_length_mm"]
    host_x, host_y, host_z = op["host"]["size_mm"]
    if not op.get("right_handed", True):
        # Non implementato: nessun caso di prova per una filettatura
        # sinistrorsa in questa milestone (il preset "thread" e i calibri
        # M6 sono tutti destrorsi) — meglio un errore esplicito che una
        # geometria sinistrorsa mai verificata (stessa disciplina di
        # presets.json: 'defined: false' invece di fingere copertura).
        raise SketchValidationError(["right_handed=false non e' implementato in questa milestone (M3, nessun calibro/caso di prova sinistrorso)"])

    # I punti di cresta (raggio massimo, entro tolleranza) prendono
    # l'overlap di stabilizzazione OCC — vedi CUT_OVERLAP_MM sopra. Il
    # punto di radice (raggio minore) resta esatto.
    #
    # [M5, C4 — vedi docs/review_tecnica.md] La soglia qui era 1e-6mm,
    # piu' STRETTA di NUMERIC_TOLERANCE_MM (oggi 1e-3, vedi
    # sketch_schema.py): una spec con la cresta a "r_major - 5e-4" era
    # VALIDA per lo schema (entro tolleranza) ma questo confronto non
    # riconosceva il punto come cresta, saltando l'overlap di
    # stabilizzazione OCC — finestra di quasi-tangenza che
    # CUT_OVERLAP_MM esiste apposta per evitare. Allineata a
    # CROSS_FIELD_TOLERANCE_MM (stessa soglia di sketch_schema.py per la
    # consistenza sketch/operation, non un terzo numero indipendente):
    # qualunque punto che lo schema accetta come cresta riceve ora
    # l'overlap.
    profile_coords = []
    for pid in ordered_ids:
        x, y = points[pid]
        is_crest = abs(x - r_major) <= CROSS_FIELD_TOLERANCE_MM
        profile_coords.append((x + CUT_OVERLAP_MM if is_crest else x, y))

    move_x, move_y = profile_coords[0]
    profile_lines = "\n    ".join(f".lineTo({x!r}, {y!r})" for x, y in profile_coords[1:])

    code = _emit_thread_code(move_x, move_y, profile_lines, pitch_mm, engagement_length_mm, r_major, host_x, host_y, host_z)
    return code


def _emit_thread_code(move_x, move_y, profile_lines, pitch_mm, engagement_length_mm, r_major, host_x, host_y, host_z):
    code = f'''import cadquery as cq

# Profilo a V compilato da vincoli sketch-first (M3) — punti/linee
# dichiarati in sketch_compiler.compile_thread_sketch_to_code(), stessa
# forma di config/gauges/generate_thread_gauge.build_thread_plug() ma
# tracciata dinamicamente dalla topologia dello sketch invece che da tre
# .lineTo() fissi. CUT_OVERLAP_MM applicato ai punti di cresta per la
# stessa ragione di stabilizzazione OCC di generate_thread_gauge.py.
_profile = (
    cq.Workplane("XZ")
    .moveTo({move_x!r}, {move_y!r})
    {profile_lines}
    .close()
)
_path = cq.Wire.makeHelix(pitch={pitch_mm!r}, height={engagement_length_mm!r}, radius={r_major!r})
_groove = _profile.sweep(_path, isFrenet=True)
_thread_pin = cq.Workplane("XY").circle({r_major!r}).extrude({engagement_length_mm!r}).cut(_groove)

_host = (
    cq.Workplane("XY")
    .box({host_x!r}, {host_y!r}, {host_z!r}, centered=(True, True, False))
)
result = _host.cut(_thread_pin)
'''
    return code


# ---------------------------------------------------------------------------
# param_first (M5, C4/P3 — vedi docs/review_tecnica.md)
# ---------------------------------------------------------------------------
#
# Per le feature con preset, sketch-first (sopra) chiede all'LLM di
# emettere coordinate 2D che codificano una trigonometria che il
# compilatore gia' conosce (lo stesso profilo a V e' interamente
# determinato da pitch_mm e dall'angolo del preset) — "sposta la
# fragilita', non la elimina" (stesso pattern gia' riconosciuto in
# issue #10, qui applicato alla milestone sketch-first stessa). Qui L2
# emette SOLO i 4 parametri fisici della filettatura
# (major_diameter_mm, pitch_mm, engagement_length_mm, host_xy_mm): il
# compilatore costruisce la spec sketch canonica con la STESSA
# trigonometria gia' validata in
# config/gauges/generate_thread_gauge.build_thread_plug()
# (H = pitch/(2*tan(angolo/2)), r_major = D/2, r_minor = r_major - H) e
# riusa compile_thread_sketch_to_code() — nessuna seconda via
# geometrica, un solo posto che sa costruire il profilo a V.


def build_thread_sketch_spec_from_params(params: dict, profile_angle_deg: float = 60.0) -> dict:
    """Costruisce la spec sketch-first canonica (stesso schema di
    hand_written_thread_spec() in verify_sketch_compiler_thread.py) dai
    soli parametri fisici della strategia 'param_first'. Solleva
    SketchValidationError (stessa classe di assert_valid_sketch_spec) se
    i parametri non descrivono una filettatura fisicamente valida —
    generate_and_verify.py la tratta identicamente a un errore di
    validazione sketch-first (FAIL di generazione, RETRY_GENERIC, vedi
    generate_code_for_attempt())."""
    required = ("major_diameter_mm", "pitch_mm", "engagement_length_mm", "host_xy_mm")
    for name in required:
        value = params.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            raise SketchValidationError([f"{name} deve essere un numero positivo, trovato {value!r}"])

    major_diameter_mm = params["major_diameter_mm"]
    pitch_mm = params["pitch_mm"]
    engagement_length_mm = params["engagement_length_mm"]
    host_xy_mm = params["host_xy_mm"]

    if host_xy_mm <= major_diameter_mm:
        raise SketchValidationError(
            [f"host_xy_mm={host_xy_mm} deve essere maggiore del diametro maggiore {major_diameter_mm} (il foro non ci starebbe dentro)"]
        )

    r_major = major_diameter_mm / 2.0
    h = pitch_mm / (2 * math.tan(math.radians(profile_angle_deg / 2)))
    r_minor = r_major - h
    if r_minor <= 0:
        raise SketchValidationError(
            [
                f"major_diameter_mm={major_diameter_mm}, pitch_mm={pitch_mm}, angolo={profile_angle_deg}: "
                f"raggio di radice <= 0 ({r_minor:.6f}mm) — profilo a V non valido (stesso vincolo di "
                "config/gauges/generate_thread_gauge.build_thread_plug())"
            ]
        )

    return {
        "feature": "thread",
        "sketch": {
            "points": [
                {"id": "p_crest_a", "x": r_major, "y": -pitch_mm / 2},
                {"id": "p_crest_b", "x": r_major, "y": pitch_mm / 2},
                {"id": "p_root", "x": r_minor, "y": 0.0},
            ],
            "lines": [
                {"id": "l_flank_in", "start": "p_crest_a", "end": "p_root"},
                {"id": "l_flank_out", "start": "p_root", "end": "p_crest_b"},
                {"id": "l_close", "start": "p_crest_b", "end": "p_crest_a"},
            ],
            "arcs": [],
            "dimensions": [
                {"type": "distance", "refs": ["p_crest_a", "p_crest_b"], "value_mm": pitch_mm, "label": "pitch"},
                {"type": "angle", "refs": ["l_flank_in", "l_flank_out"], "value_deg": profile_angle_deg, "label": "thread_profile_angle"},
            ],
        },
        "operation": {
            "type": "helical_thread_cut",
            "host": {"type": "block", "size_mm": [host_xy_mm, host_xy_mm, engagement_length_mm]},
            "major_diameter_mm": major_diameter_mm,
            "pitch_mm": pitch_mm,
            "engagement_length_mm": engagement_length_mm,
            "right_handed": True,
        },
    }


def compile_thread_params_to_code(params: dict, profile_angle_deg: float = 60.0) -> str:
    """param_first -> CadQuery, passando dalla stessa spec sketch
    canonica e dallo stesso compilatore di sketch-first (vedi docstring
    sopra) — mai una via geometrica indipendente."""
    spec = build_thread_sketch_spec_from_params(params, profile_angle_deg)
    return compile_thread_sketch_to_code(spec)
