"""
CALIPER — verifica manuale del cleanup di GENERATED_PARTS_DIR (M5, C10 —
vedi docs/review_tecnica.md).

Gap dichiarato in PR #11, mai chiuso: run_and_measure.py esporta ogni
pezzo generato sotto /exec/parts (GENERATED_PARTS_DIR) e nulla lo ripulisce
mai — crescita illimitata su un processo (watcher.py) a lunga vita.
watcher.cleanup_generated_parts() rimuove ora i file piu' vecchi di
GENERATED_PARTS_RETENTION_SECONDS.

Due casi (funzione pura, nessun processo/subprocess necessario — mtime
manipolato direttamente con os.utime):
1. Un file piu' vecchio della ritenzione viene rimosso.
2. Un file piu' recente della ritenzione NON viene rimosso (nessuna
   cancellazione prematura di un pezzo ancora potenzialmente in uso).

Uso: python verify_generated_parts_cleanup.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))

import watcher  # noqa: E402


def main():
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        watcher.GENERATED_PARTS_DIR = tmp
        retention = 100.0
        watcher.GENERATED_PARTS_RETENTION_SECONDS = retention
        now = 1_000_000.0

        old_path = os.path.join(tmp, "old.step")
        recent_path = os.path.join(tmp, "recent.step")
        for path in (old_path, recent_path):
            with open(path, "w", encoding="utf-8") as f:
                f.write("x")
        os.utime(old_path, (now - retention - 10, now - retention - 10))  # oltre la ritenzione
        os.utime(recent_path, (now - retention + 10, now - retention + 10))  # entro la ritenzione

        watcher.cleanup_generated_parts(now=now)

        old_removed = not os.path.exists(old_path)
        recent_kept = os.path.exists(recent_path)
        print(f"File vecchio (oltre la ritenzione) rimosso: {old_removed} (atteso True)")
        print(f"File recente (entro la ritenzione) mantenuto: {recent_kept} (atteso True)")
        ok = old_removed and recent_kept

        print("\n--- Cartella inesistente: nessuna eccezione ---")
        watcher.GENERATED_PARTS_DIR = os.path.join(tmp, "does-not-exist")
        try:
            watcher.cleanup_generated_parts(now=now)
            no_crash = True
        except Exception as e:  # noqa: BLE001
            no_crash = False
            print(f"Eccezione inattesa: {e}")
        print("Atteso: nessuna eccezione:", "OK" if no_crash else "FALLITO")
        ok = ok and no_crash

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
