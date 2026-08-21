"""
CALIPER — verifica che il gate anti-bias di M4 scarti DAVVERO una
strategia nota come fallimentare dentro il loop reale (non solo la
funzione isolata di virtual_memory.py, gia' coperta da
verify_virtual_memory.py) — criterio di accettazione esplicito di M4
("Verifica con un caso di test reale: l'agente scarta effettivamente
una strategia nota come fallimentare — non solo in teoria", vedi
docs/logbook_fase4.md e issue #5).

Stessa classe di mock di verify_retry_policy.py::test_main_loop:
call_flowise_l2/call_verifier/resolve_chatflow_id sono mockate (nessuna
rete reale, nessuna istanza Flowise/verifier viva in questo sandbox —
vedi docs/handoff_m4.md), ma retry_log.jsonl e la directory Livello 6
sono FILE REALI su disco, letti dal codice reale di virtual_memory.py
attraverso generate_and_verify.main() — il percorso end-to-end del gate
e' esercitato per davvero, solo la rete e' sostituita.

Nota tecnica (stessa ragione per cui verify_retry_policy.py imposta
RETRY_LOG_PATH prima di QUALUNQUE import di retry_policy): RETRY_LOG_PATH
e' una costante di modulo letta una sola volta all'import — per questo
qui le variabili d'ambiente sono impostate PRIMA del primo import di
generate_and_verify/virtual_memory, e il modulo NON viene piu' ricaricato
tra gli scenari (un reload rileggerebbe generate_and_verify ma non
retry_policy/virtual_memory, gia' in sys.modules — stessa fixture per
entrambi gli scenari, per costruzione, evita il problema).

Due scenari, sulla STESSA fixture (un retry_log.jsonl con 2 FAIL
virtuali per la spec M6 + un FAIL fisico L6 che li corrobora):

A. Spec M6 (quella con memoria di fallimento corroborata): il loop deve
   terminare SENZA MAI chiamare call_flowise_l2 (nessuna generazione
   tentata) — non solo "fallire alla fine", proprio non deve nemmeno
   partire.
B. Stessa feature ma spec DIVERSA (nominal M8, nessuna storia di
   fallimento propria nella stessa fixture): il loop deve procedere
   normalmente e chiamare call_flowise_l2 — verifica che il gate non sia
   troppo aggressivo (non scarta tutto, solo la strategia con memoria).

Uso: python verify_virtual_memory_loop_gate.py
"""

import json
import os
import sys
import tempfile


def write_virtual_log(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_physical_case(dataset_dir, filename, feature, spec, esito):
    with open(os.path.join(dataset_dir, filename), "w", encoding="utf-8") as f:
        json.dump({"prompt": "test", "specifica_strutturata": {"feature": feature, **spec}, "esito": esito}, f)


def main():
    sys.path.insert(0, os.path.dirname(__file__))

    with tempfile.TemporaryDirectory() as tmp:
        log_path = os.path.join(tmp, "retry_log.jsonl")
        dataset_dir = os.path.join(tmp, "l6")
        os.makedirs(dataset_dir, exist_ok=True)

        # Fixture condivisa dai due scenari: solo M6 ha memoria di
        # fallimento corroborata, M8 no.
        spec_m6 = {"feature": "other", "nominal": "M6", "tolerance_type": "diametrale"}
        spec_m8 = {"feature": "other", "nominal": "M8", "tolerance_type": "diametrale"}

        os.environ["FLOWISE_API_KEY"] = "fake-key-for-test"
        os.environ["RETRY_LOG_PATH"] = log_path
        os.environ["L6_DATASET_DIR"] = dataset_dir

        # Import DOPO aver impostato le env var — vedi nota tecnica sopra.
        import generate_and_verify as gav
        from virtual_memory import MIN_VIRTUAL_FAILURES_FOR_EXCLUSION, spec_key

        key_m6 = spec_key("other", {**spec_m6, "l2_strategy": "free_code"})
        write_virtual_log(
            log_path,
            [
                {"case_id": f"prior-{n}", "attempt": 1, "spec_key": key_m6, "outcome": "FAIL", "source": "virtual"}
                for n in range(MIN_VIRTUAL_FAILURES_FOR_EXCLUSION)
            ],
        )
        write_physical_case(dataset_dir, "case1.json", "other", spec_m6, "FAIL")

        gav.resolve_chatflow_id = lambda strategy="free_code": "fake-chatflow-id"

        ok = True

        # --- Scenario A: M6, deve essere scartata prima di generare ---
        calls_a = {"flowise": 0}
        gav.call_flowise_l2 = lambda chatflow_id, spec_json, temperature=None: calls_a.__setitem__(
            "flowise", calls_a["flowise"] + 1
        ) or "# should never be called"
        sys.argv = ["generate_and_verify.py", json.dumps(spec_m6)]
        rc_a = gav.main()
        print(f"Scenario A (spec M6, memoria corroborata): return code={rc_a} (atteso 1), chiamate a L2={calls_a['flowise']} (attese 0)")
        ok_a = rc_a == 1 and calls_a["flowise"] == 0
        ok = ok and ok_a
        print("=== Scenario A (esclusione reale, nessuna chiamata a L2):", "OK" if ok_a else "FALLITO", "===\n")

        # --- Scenario B: M8, nessuna memoria propria, deve generare normalmente ---
        calls_b = {"flowise": 0}

        def fake_flowise_b(chatflow_id, spec_json, temperature=None):
            calls_b["flowise"] += 1
            return f"# fake code, attempt {calls_b['flowise']}"

        pass_result = {"status": "PASS", "checks": [{"name": "execution_and_geometry", "status": "PASS", "detail": None}]}
        gav.call_flowise_l2 = fake_flowise_b
        gav.call_verifier = lambda code, spec=None: pass_result
        sys.argv = ["generate_and_verify.py", json.dumps(spec_m8)]
        rc_b = gav.main()
        print(f"Scenario B (spec M8, nessuna memoria propria): return code={rc_b} (atteso 0), chiamate a L2={calls_b['flowise']} (attesa >= 1)")
        ok_b = rc_b == 0 and calls_b["flowise"] >= 1
        ok = ok and ok_b
        print("=== Scenario B (gate non sovra-aggressivo, generazione procede):", "OK" if ok_b else "FALLITO", "===\n")

    print("=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
