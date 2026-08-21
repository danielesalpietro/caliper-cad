"""
CALIPER — verifica manuale di check_result_assigned() con AnnAssign
(M5, C10 — vedi docs/review_tecnica.md).

`result: cq.Workplane = cq.Workplane("XY").box(1,1,1)` e' una assegnazione
con annotazione di tipo (ast.AnnAssign, PEP 526) — sintatticamente Python
valido e semanticamente identica a una ast.Assign ai fini di questo
controllo (presenza di una variabile chiamata 'result'). PRIMA di M5,
check_result_assigned() riconosceva solo ast.Assign: un LLM che annota il
tipo (comportamento plausibile, non raro in codice generato) veniva
bocciato con un falso FAIL — bruciando un tentativo di retry per codice
altrimenti valido.

Tre casi:
1. AnnAssign con valore ("result: cq.Workplane = ...") -> PASS (oggi FAIL,
   dimostralo).
2. Assign semplice ("result = ...") -> PASS, invariato (nessuna
   regressione).
3. AnnAssign SENZA valore ("result: cq.Workplane" da solo, una pura
   dichiarazione di tipo senza assegnazione — 'result' non esiste ancora
   a runtime) -> FAIL, correttamente: non e' un'assegnazione vera.

Uso: python verify_result_variable_annassign.py
Richiede fastapi/pydantic installati (import di app.py) — nessuna
istanza viva necessaria, la funzione sotto test e' pura (solo ast).
"""

import ast
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from app import check_result_assigned  # noqa: E402

CODE_ANNASSIGN_WITH_VALUE = """
import cadquery as cq
result: cq.Workplane = cq.Workplane("XY").box(1, 1, 1)
"""

CODE_PLAIN_ASSIGN = """
import cadquery as cq
result = cq.Workplane("XY").box(1, 1, 1)
"""

CODE_ANNASSIGN_WITHOUT_VALUE = """
import cadquery as cq
result: cq.Workplane
"""


def check(code):
    tree = ast.parse(code)
    return check_result_assigned(tree)


def main():
    ok = True

    print("--- 1. AnnAssign CON valore ('result: cq.Workplane = ...') ---")
    r1 = check(CODE_ANNASSIGN_WITH_VALUE)
    print(r1)
    case1_ok = r1["status"] == "PASS"
    print("Atteso: PASS (oggi FAIL, dimostralo):", "OK" if case1_ok else "FALLITO")
    ok = ok and case1_ok

    print("\n--- 2. Assign semplice ('result = ...') — nessuna regressione ---")
    r2 = check(CODE_PLAIN_ASSIGN)
    print(r2)
    case2_ok = r2["status"] == "PASS"
    print("Atteso: PASS (invariato):", "OK" if case2_ok else "FALLITO")
    ok = ok and case2_ok

    print("\n--- 3. AnnAssign SENZA valore ('result: cq.Workplane' da solo) — non e' un'assegnazione vera ---")
    r3 = check(CODE_ANNASSIGN_WITHOUT_VALUE)
    print(r3)
    case3_ok = r3["status"] == "FAIL"
    print("Atteso: FAIL ('result' non esiste a runtime senza un valore):", "OK" if case3_ok else "FALLITO")
    ok = ok and case3_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
