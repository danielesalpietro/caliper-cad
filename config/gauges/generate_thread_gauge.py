"""
CALIPER -- generatore dei calibri filettati Go/No-Go (Milestone M1).

Genera un tampone filettato esterno (thread plug gauge) per verificare un
foro filettato -- vedi l'esempio L2.5 in architettura ("foro filettato M6,
tolleranza 0.3mm, passo 1.0"): la feature generata dall'LLM e' un foro
(filettatura interna), quindi il calibro corretto e' un TAMPONE esterno,
non un anello.

Profilo a V simmetrico non troncato -- stessa approssimazione gia' usata
nel preset "thread" di services/orchestrator/presets.json (angolo 60
gradi, ISO 68-1), NON la geometria ISO reale con troncatura su
cresta/nocciolo: e' un calibro coerente con cio' che la pipeline verifica
oggi (Rischio: se in futuro si passa a un profilo troncato, questo script
e i calibri vanno rigenerati insieme, non solo uno dei due).

Convenzione GO/NO-GO, diametrale (coerente con
`default_tolerance_type: "diametrale"` nel preset):
  - GO   = tampone al diametro MINIMO accettabile (nominal - tolleranza).
    Deve entrare in qualunque foro non troppo stretto.
  - NO-GO = tampone al diametro MASSIMO accettabile (nominal + tolleranza).
    Non deve entrare (oltre poche spire) in un foro entro tolleranza --
    se entra, il foro e' sovradimensionato.

Eseguito una tantum da un umano (o da CI su richiesta esplicita), MAI
dall'LLM in loop di generazione -- vedi docs/logbook_fase1.md.

Uso:
    python generate_thread_gauge.py
"""

import math
from pathlib import Path

import cadquery as cq

OUT_DIR = Path(__file__).parent

# Sovrapposizione della cresta del profilo oltre il diametro nominale,
# necessaria per evitare la tangenza esatta tra l'utensile di taglio (la
# spirale) e il cilindro di base nel boolean cut -- senza questo l'OCC
# BRepAlgoAPI produce un solido non manifold (facce coincidenti al
# contorno), verificato empiricamente prima di fissare questo valore.
CUT_OVERLAP_MM = 0.05


def build_thread_plug(major_d_mm: float, pitch_mm: float, length_mm: float, angle_deg: float = 60.0):
    """Tampone filettato esterno, profilo a V pieno (non troncato)."""
    H = pitch_mm / (2 * math.tan(math.radians(angle_deg / 2)))
    r_major = major_d_mm / 2
    r_minor = r_major - H
    if r_minor <= 0:
        raise ValueError(
            f"root <= 0 per major={major_d_mm} pitch={pitch_mm} angle={angle_deg}: "
            "combinazione non valida per un profilo a V pieno"
        )

    path = cq.Wire.makeHelix(pitch=pitch_mm, height=length_mm, radius=r_major)
    profile = (
        cq.Workplane("XZ")
        .moveTo(r_major + CUT_OVERLAP_MM, -pitch_mm / 2)
        .lineTo(r_major + CUT_OVERLAP_MM, pitch_mm / 2)
        .lineTo(r_minor, 0)
        .close()
    )
    groove = profile.sweep(path, isFrenet=True)
    base = cq.Workplane("XY").circle(r_major).extrude(length_mm)
    solid = base.cut(groove)
    return solid, r_major, r_minor


def verify_and_export(solid, r_major_expected, length_mm, out_path: Path):
    obj = solid.val()
    if not obj.isValid():
        raise RuntimeError(f"solido non valido dopo il cut: {out_path.name}")

    bb = obj.BoundingBox()
    tol = 1e-3
    if abs(bb.xlen - r_major_expected * 2) > tol or abs(bb.ylen - r_major_expected * 2) > tol:
        raise RuntimeError(
            f"diametro fuori tolleranza: atteso {r_major_expected * 2:.4f}, "
            f"ottenuto x={bb.xlen:.4f} y={bb.ylen:.4f}"
        )
    if abs(bb.zlen - length_mm) > tol:
        raise RuntimeError(f"lunghezza fuori tolleranza: attesa {length_mm}, ottenuta {bb.zlen:.4f}")

    vol_full_cyl = math.pi * r_major_expected**2 * length_mm
    if not (0 < obj.Volume() < vol_full_cyl):
        raise RuntimeError(f"volume fuori range plausibile: {obj.Volume():.4f} mm3")

    obj.exportStep(str(out_path))
    print(f"OK  {out_path.name}  bbox=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})  volume={obj.Volume():.3f}mm3")


if __name__ == "__main__":
    # M6, passo 1.0, angolo 60 gradi -- vedi preset "thread" in
    # services/orchestrator/presets.json. Nominale 6.0mm, tolleranza
    # diametrale 0.3mm (default del preset) -> range accettabile [5.7, 6.3].
    PITCH_MM = 1.0
    ANGLE_DEG = 60.0
    GO_DIAMETER_MM = 5.7
    NOGO_DIAMETER_MM = 6.3

    # Lunghezza di impegno del calibro: NON ancora un campo dello schema
    # L2.5 (che oggi non porta una profondita'/lunghezza del foro) --
    # placeholder scelto come ~1.4x il diametro nominale (regola empirica
    # comune per l'impegno minimo di una filettatura stampata), non un
    # valore validato. Segnato come nota aperta in docs/logbook_fase1.md,
    # non un'assunzione silenziosa.
    LENGTH_MM = 8.0

    go_solid, go_r_major, _ = build_thread_plug(GO_DIAMETER_MM, PITCH_MM, LENGTH_MM, ANGLE_DEG)
    verify_and_export(go_solid, go_r_major, LENGTH_MM, OUT_DIR / "thread_M6_GO_ISO68-1.step")

    nogo_solid, nogo_r_major, _ = build_thread_plug(NOGO_DIAMETER_MM, PITCH_MM, LENGTH_MM, ANGLE_DEG)
    verify_and_export(nogo_solid, nogo_r_major, LENGTH_MM, OUT_DIR / "thread_M6_NOGO_ISO68-1.step")
