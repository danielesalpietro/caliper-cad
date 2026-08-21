"""
CALIPER -- generatore dei calibri a spina Go/No-Go per accoppiamento
albero-mozzo (TC1, Milestone M2 -- vedi docs/logbook_fase2.md).

Genera un calibro cilindrico liscio (nessuna filettatura) per verificare
un FORO passante in un mozzo -- accoppiamento a giochi (clearance fit),
non un press-fit (quello resta esplicitamente fuori scope, serve un
solver FEA, vedi docs/logbook_fase1.md, "Domanda aperta").

Convenzione GO/NO-GO, diametrale -- stessa di generate_thread_gauge.py:
  - GO   = spina al diametro MINIMO accettabile del foro
    (nominale - tolleranza). Deve poter attraversare qualunque foro non
    troppo stretto, in OGNI posizione lungo la corsa (non solo alle
    imboccature -- e' il motivo dello sweep, non solo del controllo
    statico, vedi docs/logbook_fase2.md TC1).
  - NO-GO = spina al diametro MASSIMO accettabile del foro
    (nominale + tolleranza). Non deve entrare in un foro entro
    tolleranza -- se entra senza interferire, il foro e' sovradimensionato.

Nominale/tolleranza qui sono un caso di esempio ILLUSTRATIVO (Ø8.0mm,
tolleranza diametrale 0.2mm) -- non e' legato a nessuna classe di
accoppiamento ISO specifica (es. H7/g6): il progetto non ha ancora un
preset "clearance_fit" con provenienza normativa definita, a differenza
di "thread" (ISO 68-1). Nota aperta, non taciuta -- vedi
services/orchestrator/presets.json.

Eseguito una tantum da un umano (o da CI su richiesta esplicita), MAI
dall'LLM in loop di generazione -- vedi docs/logbook_fase1.md e
config/gauges/README.md.

Uso:
    python generate_pin_gauge.py
"""

from pathlib import Path

import cadquery as cq

OUT_DIR = Path(__file__).parent


def build_pin(diameter_mm: float, length_mm: float):
    """Spina cilindrica liscia, stesso posizionamento (z in [0, length_mm],
    non centrata) dei calibri filettati -- coerenza di convenzione tra
    calibri, vedi generate_thread_gauge.py."""
    return cq.Workplane("XY").circle(diameter_mm / 2).extrude(length_mm)


def verify_and_export(solid, diameter_expected_mm: float, length_mm: float, out_path: Path):
    obj = solid.val()
    if not obj.isValid():
        raise RuntimeError(f"solido non valido: {out_path.name}")

    bb = obj.BoundingBox()
    tol = 1e-3
    if abs(bb.xlen - diameter_expected_mm) > tol or abs(bb.ylen - diameter_expected_mm) > tol:
        raise RuntimeError(
            f"diametro fuori tolleranza: atteso {diameter_expected_mm:.4f}, "
            f"ottenuto x={bb.xlen:.4f} y={bb.ylen:.4f}"
        )
    if abs(bb.zlen - length_mm) > tol:
        raise RuntimeError(f"lunghezza fuori tolleranza: attesa {length_mm}, ottenuta {bb.zlen:.4f}")

    import math

    vol_expected = math.pi * (diameter_expected_mm / 2) ** 2 * length_mm
    if abs(obj.Volume() - vol_expected) > 1e-3:
        raise RuntimeError(f"volume fuori range plausibile: {obj.Volume():.4f} mm3, atteso {vol_expected:.4f}")

    obj.exportStep(str(out_path))
    print(f"OK  {out_path.name}  bbox=({bb.xlen:.3f},{bb.ylen:.3f},{bb.zlen:.3f})  volume={obj.Volume():.3f}mm3")


if __name__ == "__main__":
    # Vedi docstring: Ø8.0mm nominale, tolleranza diametrale 0.2mm ->
    # range accettabile [7.8, 8.2]. Lunghezza spina 15mm, > della
    # profondita' del mozzo di controllo (12mm, vedi
    # verify_gauge_check_tc1.py) con margine per uno sweep che copra
    # l'intera corsa d'inserimento, stesso principio gia' applicato in
    # generate_thread_gauge.py (lunghezza del calibro come placeholder
    # esplicito, non un campo ancora presente nello schema L2.5).
    NOMINAL_DIAMETER_MM = 8.0
    TOLERANCE_MM = 0.2
    GO_DIAMETER_MM = NOMINAL_DIAMETER_MM - TOLERANCE_MM
    NOGO_DIAMETER_MM = NOMINAL_DIAMETER_MM + TOLERANCE_MM
    LENGTH_MM = 15.0

    go_solid = build_pin(GO_DIAMETER_MM, LENGTH_MM)
    verify_and_export(go_solid, GO_DIAMETER_MM, LENGTH_MM, OUT_DIR / "pin_D8_GO_clearance.step")

    nogo_solid = build_pin(NOGO_DIAMETER_MM, LENGTH_MM)
    verify_and_export(nogo_solid, NOGO_DIAMETER_MM, LENGTH_MM, OUT_DIR / "pin_D8_NOGO_clearance.step")
