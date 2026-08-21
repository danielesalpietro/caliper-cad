"""
CALIPER — verifica manuale di sketch_schema.py (M3).

Stesso stile di verify_retry_policy.py: eseguibile a mano, nessuna
dipendenza da cadquery (il modulo testato e' puro, vedi la sua
docstring). Copre i quattro livelli di validazione, in ordine, con casi
scritti a mano (non generati da un LLM — coerente con l'indicazione
dell'handoff di M3 di scrivere i casi di prova a mano per cio' che e'
verificabile senza un'istanza Flowise viva).

Uso: python verify_sketch_schema.py
"""

import copy
import math
import sys

from sketch_schema import validate_sketch_spec

# Profilo a V per M6/ISO 68-1 (stessi numeri di
# config/gauges/generate_thread_gauge.py: pitch=1.0, angolo=60,
# major=6.0mm -> r_major=3.0, H=pitch/(2*tan(30))=0.8660254037844387,
# r_minor=2.1339745962155613) — un FORO filettato (host block), non un
# tampone: l'operation "helical_thread_cut" sottrae il profilo dal blocco.
PITCH_MM = 1.0
ANGLE_DEG = 60.0
MAJOR_D_MM = 6.0
R_MAJOR = MAJOR_D_MM / 2.0
H = PITCH_MM / (2 * math.tan(math.radians(ANGLE_DEG / 2)))
R_MINOR = R_MAJOR - H


def valid_thread_spec():
    return {
        "feature": "thread",
        "sketch": {
            "points": [
                {"id": "p_crest_a", "x": R_MAJOR, "y": -PITCH_MM / 2},
                {"id": "p_crest_b", "x": R_MAJOR, "y": PITCH_MM / 2},
                {"id": "p_root", "x": R_MINOR, "y": 0.0},
            ],
            "lines": [
                {"id": "l_flank_in", "start": "p_crest_a", "end": "p_root"},
                {"id": "l_flank_out", "start": "p_root", "end": "p_crest_b"},
                {"id": "l_close", "start": "p_crest_b", "end": "p_crest_a"},
            ],
            "arcs": [],
            "dimensions": [
                {"type": "distance", "refs": ["p_crest_a", "p_crest_b"], "value_mm": PITCH_MM, "label": "pitch"},
                {"type": "angle", "refs": ["l_flank_in", "l_flank_out"], "value_deg": ANGLE_DEG, "label": "thread_profile_angle"},
            ],
        },
        "operation": {
            "type": "helical_thread_cut",
            "host": {"type": "block", "size_mm": [20.0, 20.0, 8.0]},
            "major_diameter_mm": MAJOR_D_MM,
            "pitch_mm": PITCH_MM,
            "engagement_length_mm": 8.0,
            "right_handed": True,
        },
    }


def main():
    ok = True

    print("--- 1. Spec valida (M6, ISO 68-1) ---")
    errors = validate_sketch_spec(valid_thread_spec())
    print("Errori:", errors)
    case_ok = errors == []
    print("Atteso: nessun errore:", "OK" if case_ok else "FALLITO")
    ok = ok and case_ok

    print("\n--- 2. Quota 'distance' inconsistente con le coordinate (pitch dichiarato 2.0 invece di 1.0) ---")
    spec = copy.deepcopy(valid_thread_spec())
    spec["sketch"]["dimensions"][0]["value_mm"] = 2.0
    errors = validate_sketch_spec(spec)
    print("Errori:", errors)
    case_ok = any("dimension 'distance'" in e for e in errors)
    print("Atteso: errore su dimension 'distance':", "OK" if case_ok else "FALLITO")
    ok = ok and case_ok

    print("\n--- 3. Quota 'angle' inconsistente (dichiarato 90 invece di 60 — bug del calcolo supplementare, vedi sketch_schema.py) ---")
    spec = copy.deepcopy(valid_thread_spec())
    spec["sketch"]["dimensions"][1]["value_deg"] = 90.0
    errors = validate_sketch_spec(spec)
    print("Errori:", errors)
    case_ok = any("dimension 'angle'" in e and "60.000000" in e for e in errors)
    print("Atteso: errore su dimension 'angle', implicati 60 gradi (non 120, il bug del calcolo ingenuo):", "OK" if case_ok else "FALLITO")
    ok = ok and case_ok

    print("\n--- 4. Profilo NON chiuso (rimossa l_close) ---")
    spec = copy.deepcopy(valid_thread_spec())
    spec["sketch"]["lines"] = spec["sketch"]["lines"][:2]
    errors = validate_sketch_spec(spec)
    print("Errori:", errors)
    case_ok = any("polilinea chiusa" in e for e in errors)
    print("Atteso: errore di topologia (non chiuso):", "OK" if case_ok else "FALLITO")
    ok = ok and case_ok

    print("\n--- 5. Riferimento a punto inesistente in una linea ---")
    spec = copy.deepcopy(valid_thread_spec())
    spec["sketch"]["lines"][0]["end"] = "p_non_esiste"
    errors = validate_sketch_spec(spec)
    print("Errori:", errors)
    case_ok = any("riferisce un punto inesistente" in e for e in errors)
    print("Atteso: errore di riferimento:", "OK" if case_ok else "FALLITO")
    ok = ok and case_ok

    print("\n--- 6. operation.major_diameter_mm inconsistente con la cresta dello sketch (7.0 invece di 6.0) ---")
    spec = copy.deepcopy(valid_thread_spec())
    spec["operation"]["major_diameter_mm"] = 7.0
    errors = validate_sketch_spec(spec)
    print("Errori:", errors)
    case_ok = any("sketch e operation inconsistenti" in e for e in errors)
    print("Atteso: errore di consistenza incrociata sketch/operation:", "OK" if case_ok else "FALLITO")
    ok = ok and case_ok

    print("\n--- 7. Blocco ospite troppo profondo... anzi troppo poco profondo per l'engagement dichiarato ---")
    spec = copy.deepcopy(valid_thread_spec())
    spec["operation"]["host"]["size_mm"][2] = 5.0  # < engagement_length_mm (8.0)
    errors = validate_sketch_spec(spec)
    print("Errori:", errors)
    case_ok = any("engagement_length_mm" in e for e in errors)
    print("Atteso: errore su profondita' insufficiente del blocco:", "OK" if case_ok else "FALLITO")
    ok = ok and case_ok

    print("\n--- 8. Struttura malformata (manca 'operation') ---")
    spec = copy.deepcopy(valid_thread_spec())
    del spec["operation"]
    errors = validate_sketch_spec(spec)
    print("Errori:", errors)
    case_ok = any("operation" in e for e in errors)
    print("Atteso: errore strutturale:", "OK" if case_ok else "FALLITO")
    ok = ok and case_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
