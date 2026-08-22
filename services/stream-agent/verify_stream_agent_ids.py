"""
CALIPER — verifica manuale della stabilita' degli id Qdrant e della
persistenza dell'offset del log virtuale (M5, C6 — vedi
docs/review_tecnica.md).

Vedi app.py: prima di M5, `abs(hash(key)) % (2**63)` usava hash(str) di
Python, RANDOMIZZATO per processo (PYTHONHASHSEED, ne' il Dockerfile ne'
docker-compose.yml lo fissano) — a ogni riavvio ogni caso gia'
indicizzato riceveva un id NUOVO, l'upsert non deduplicava mai e le
collezioni si riempivano di duplicati. Fix: deterministic_point_id()
usa uuid.uuid5 (deterministico dal contenuto, mai da hash()).

Nessuna istanza Qdrant/Ollama richiesta: i controlli sotto esercitano
solo funzioni pure e la persistenza su file, mai le chiamate di rete
(embed_text/qdrant.upsert non vengono mai invocate qui).

Due controlli:
1. Due sottoprocessi separati, con PYTHONHASHSEED DIVERSO esplicitamente
   impostato (0 e 1 — a riprova che il valore non e' quello di default
   condiviso per caso), calcolano deterministic_point_id() sulla STESSA
   chiave -> stesso risultato. PRIMA di M5, lo stesso confronto con
   hash() nativo dava risultati DIVERSI (dimostrato sotto).
2. L'offset del log virtuale, avanzato e persistito da
   index_virtual_log_once(), viene riletto correttamente da un secondo
   processo "riavviato" (nuovo import di app.py in un sottoprocesso
   pulito) — non riparte da zero.

Uso: python verify_stream_agent_ids.py
Richiede fastapi/qdrant-client/requests installati (vedi requirements.txt
— nessuna istanza viva necessaria, solo le librerie per importare app.py).
"""

import json
import os
import subprocess
import sys
import tempfile

APP_PATH = os.path.join(os.path.dirname(__file__), "app.py")

# Script minimale eseguito in un sottoprocesso pulito con PYTHONHASHSEED
# impostato PRIMA dell'avvio dell'interprete (l'unico modo in cui
# PYTHONHASHSEED ha effetto — non e' rileggibile/reimpostabile a runtime
# dallo stesso processo). DATASET_DIR/VIRTUAL_LOG_PATH puntati a
# directory inesistenti: il thread di indicizzazione in background
# (avviato all'import di app.py) fa cosi' subito ritorno senza toccare
# la rete, vedi app.py::index_dataset_once()/index_virtual_log_once().
ID_PROBE_SCRIPT = """
import sys
sys.path.insert(0, {app_dir!r})
import app
print(app.deterministic_point_id(sys.argv[1]))
"""


def compute_id_in_fresh_process(key: str, hash_seed: str, app_dir: str, dataset_dir: str, log_path: str) -> str:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hash_seed
    env["DATASET_DIR"] = dataset_dir
    env["VIRTUAL_LOG_PATH"] = log_path
    result = subprocess.run(
        [sys.executable, "-c", ID_PROBE_SCRIPT.format(app_dir=app_dir), key],
        env=env, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def compute_hash_in_fresh_process(key: str, hash_seed: str) -> int:
    """Stesso confronto ma con hash() nativo (PRE-fix) — per dimostrare
    che il problema esiste davvero con la primitiva vecchia, non solo
    in teoria."""
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hash_seed
    result = subprocess.run(
        [sys.executable, "-c", f"print(abs(hash({key!r})) % (2**63))"],
        env=env, capture_output=True, text=True, check=True,
    )
    return int(result.stdout.strip())


def main():
    ok = True
    app_dir = os.path.dirname(APP_PATH)

    with tempfile.TemporaryDirectory() as tmp:
        dataset_dir = os.path.join(tmp, "no-dataset")
        log_path = os.path.join(tmp, "no-log", "retry_log.jsonl")

        print("--- 1a. hash() nativo (PRE-fix): PYTHONHASHSEED diverso -> id diversi (dimostra il bug) ---")
        key = "case-42:attempt-1"
        hash_seed0 = compute_hash_in_fresh_process(key, "0")
        hash_seed1 = compute_hash_in_fresh_process(key, "1")
        print(f"hash() con PYTHONHASHSEED=0: {hash_seed0}, PYTHONHASHSEED=1: {hash_seed1}")
        hash_differs = hash_seed0 != hash_seed1
        print("Atteso (comportamento PRE-fix): id DIVERSI tra i due processi:", "OK" if hash_differs else "FALLITO (inatteso: identici)")
        ok = ok and hash_differs

        print("\n--- 1b. deterministic_point_id() (POST-fix): PYTHONHASHSEED diverso -> id IDENTICI ---")
        id_seed0 = compute_id_in_fresh_process(key, "0", app_dir, dataset_dir, log_path)
        id_seed1 = compute_id_in_fresh_process(key, "1", app_dir, dataset_dir, log_path)
        print(f"deterministic_point_id() con PYTHONHASHSEED=0: {id_seed0}, PYTHONHASHSEED=1: {id_seed1}")
        ids_match = id_seed0 == id_seed1 and id_seed0 != ""
        print("Atteso: id IDENTICI tra i due processi (deterministico dal contenuto):", "OK" if ids_match else "FALLITO")
        ok = ok and ids_match

        print("\n--- 2. Offset del log virtuale persistito su file, riletto da un 'riavvio' ---")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        records = [
            {"case_id": f"case-{n}", "attempt": 1, "spec_key": "k", "outcome": "FAIL", "source": "virtual",
             "failure_class": "geometric", "checker_version": "test"}
            for n in range(3)
        ]
        with open(log_path, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        env = dict(os.environ)
        env["DATASET_DIR"] = dataset_dir
        env["VIRTUAL_LOG_PATH"] = log_path
        # "Processo 1": legge tutte le righe presenti, avanza e persiste
        # l'offset. index_virtual_log_once() tenta anche embed_text()
        # per ogni riga nuova (nessun Ollama vivo qui) — fallisce e
        # stampa un errore per record (catturato internamente, non fa
        # crashare il processo), rumore atteso e ignorato: l'offset e'
        # stampato con un prefisso univoco per non confonderlo con
        # quell'output.
        run1 = subprocess.run(
            [sys.executable, "-c", f"""
import sys
sys.path.insert(0, {app_dir!r})
import app
app.index_virtual_log_once()
print("OFFSET=" + str(app._virtual_log_offset))
"""],
            env=env, capture_output=True, text=True, check=True,
        )
        offset_after_run1 = int(next(line for line in run1.stdout.splitlines() if line.startswith("OFFSET=")).split("=")[1])
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"case_id": "case-new", "attempt": 1, "spec_key": "k", "outcome": "FAIL",
                                 "source": "virtual", "failure_class": "geometric", "checker_version": "test"}) + "\n")

        # "Processo 2" (nuovo interprete = simula un riavvio): deve
        # ripartire dall'offset persistito, non da zero — l'import di
        # app.py rilegge _load_virtual_log_offset() dal file.
        run2 = subprocess.run(
            [sys.executable, "-c", f"""
import sys
sys.path.insert(0, {app_dir!r})
import app
print("OFFSET=" + str(app._virtual_log_offset))
"""],
            env=env, capture_output=True, text=True, check=True,
        )
        loaded_offset = int(next(line for line in run2.stdout.splitlines() if line.startswith("OFFSET=")).split("=")[1])
        print(f"Offset dopo il primo processo: {offset_after_run1}; offset riletto da un nuovo processo ('riavvio'): {loaded_offset}")
        offset_ok = loaded_offset == offset_after_run1 and loaded_offset > 0
        print("Atteso: l'offset persistito e' quello riletto, non zero (nessuna rilettura da capo):", "OK" if offset_ok else "FALLITO")
        ok = ok and offset_ok

    print("\n=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
