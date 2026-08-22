"""
CALIPER -- verifica il flag --confirm di generate_and_verify.py (M6,
Rischio #5: prima di M6 la spec normalizzata da L2.5 andava a L2 senza
conferma umana, mitigazione mai implementata -- vedi docs/handoff_m6.md
Passo 2).

Stessa classe di mock di verify_virtual_memory_loop_gate.py:
call_flowise_l2/resolve_chatflow_id sono mockate (nessuna rete reale),
builtins.input e' mockato per simulare la risposta umana.

Due scenari, stessa spec (nessuna memoria di fallimento, cosi' il gate
di M4 non interferisce):

A. --confirm presente, risposta "n": il loop deve terminare SENZA MAI
   chiamare call_flowise_l2 (rifiuto prima di qualunque chiamata di
   rete).
B. --confirm presente, risposta "y": il loop deve procedere
   normalmente e chiamare call_flowise_l2.

(Il caso senza --confirm, comportamento legacy invariato, e' coperto
implicitamente da tutti gli altri verify_*.py esistenti, che non
passano mai il flag e continuano a passare senza modifiche.)

Uso: python verify_confirm_flag.py
"""

import builtins
import json
import os
import sys
import tempfile


def main():
    sys.path.insert(0, os.path.dirname(__file__))

    with tempfile.TemporaryDirectory() as tmp:
        log_path = os.path.join(tmp, "retry_log.jsonl")
        dataset_dir = os.path.join(tmp, "l6")
        os.makedirs(dataset_dir, exist_ok=True)

        os.environ["FLOWISE_API_KEY"] = "fake-key-for-test"
        os.environ["RETRY_LOG_PATH"] = log_path
        os.environ["L6_DATASET_DIR"] = dataset_dir

        import generate_and_verify as gav

        spec = {"feature": "other", "nominal": "M6", "tolerance_type": "diametrale"}

        gav.resolve_chatflow_id = lambda strategy="free_code": "fake-chatflow-id"
        pass_result = {"status": "PASS", "checks": [{"name": "execution_and_geometry", "status": "PASS", "detail": None}]}
        gav.call_verifier = lambda code, spec=None: pass_result

        real_input = builtins.input
        ok = True

        # --- Scenario A: --confirm, risposta "n" -> nessuna chiamata a L2 ---
        calls_a = {"flowise": 0}
        gav.call_flowise_l2 = lambda chatflow_id, spec_json, temperature=None: calls_a.__setitem__(
            "flowise", calls_a["flowise"] + 1
        ) or "# should never be called"
        builtins.input = lambda prompt="": "n"
        sys.argv = ["generate_and_verify.py", json.dumps(spec), "--confirm"]
        rc_a = gav.main()
        print(f"Scenario A (--confirm, risposta n): return code={rc_a} (atteso 1), chiamate a L2={calls_a['flowise']} (attese 0)")
        ok_a = rc_a == 1 and calls_a["flowise"] == 0
        ok = ok and ok_a
        print("=== Scenario A (rifiuto, nessuna chiamata a L2):", "OK" if ok_a else "FALLITO", "===\n")

        # --- Scenario B: --confirm, risposta "y" -> generazione procede ---
        calls_b = {"flowise": 0}

        def fake_flowise_b(chatflow_id, spec_json, temperature=None):
            calls_b["flowise"] += 1
            return f"# fake code, attempt {calls_b['flowise']}"

        gav.call_flowise_l2 = fake_flowise_b
        builtins.input = lambda prompt="": "y"
        sys.argv = ["generate_and_verify.py", json.dumps(spec), "--confirm"]
        rc_b = gav.main()
        print(f"Scenario B (--confirm, risposta y): return code={rc_b} (atteso 0), chiamate a L2={calls_b['flowise']} (attesa >= 1)")
        ok_b = rc_b == 0 and calls_b["flowise"] >= 1
        ok = ok and ok_b
        print("=== Scenario B (conferma, generazione procede):", "OK" if ok_b else "FALLITO", "===\n")

        builtins.input = real_input

    print("=== Esito complessivo:", "TUTTI I CONTROLLI OK" if ok else "ALMENO UN CONTROLLO FALLITO", "===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
