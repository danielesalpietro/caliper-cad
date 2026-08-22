#!/usr/bin/env python3
"""CALIPER — bench matrice modelli per L2.5 (normalizzazione spec).

Nato dal run1 (E2E-1: granite4:1b inventa deterministicamente
tolerance_type, vedi docs/logbook_fase6*.md): misura, sugli STESSI
template e schema del chatflow L2.5 versionato (letti dal JSON in
services/flowise/chatflows/ — nessuna copia che possa divergere), come
si comportano piu' modelli. Aggiungere un modello = aggiungerlo alla
lista BENCH_MODELS. La decisione di cambiare modello spetta all'utente,
sui numeri di questo report; la validazione finale del vincitore passa
comunque dal chatflow vivo (E2E-1 rosso->verde).

Metriche per modello, su ~15 prompt con atteso noto:
  - json_ok      : % risposte con JSON valido e completo dello schema
  - invented     : % campi che DOVEVANO restare vuoti e non lo sono
                   (la metrica che ha motivato il bench — soglia: 0%)
  - extract_ok   : % campi attesi non-vuoti estratti correttamente
  - latency_avg  : secondi medi per chiamata

Uso (sul pod, a fine suite TC-E2E — timebox 60 minuti):
  BENCH_MODELS="granite4:1b,granite4:3b,qwen3:8b,llama3.1:8b" \
  python3 bench/bench_l25_models.py
Env: OLLAMA_URL (default http://localhost:11434); OPENAI_API_KEY (se
presente aggiunge il candidato API di riferimento, BENCH_OPENAI_MODEL,
default gpt-4o-mini); BENCH_OUT (default
/workspace/caliper-runs/incoming/bench-l25). I modelli Ollama mancanti
vengono scaricati (pull) automaticamente.
"""
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHATFLOW = os.path.join(REPO_ROOT, "services/flowise/chatflows/l25-specification-normalization.json")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OUT_DIR = os.environ.get("BENCH_OUT", "/workspace/caliper-runs/incoming/bench-l25")
MODELS = [m.strip() for m in os.environ.get(
    "BENCH_MODELS", "granite4:1b,granite4:3b,qwen3:8b,llama3.1:8b").split(",") if m.strip()]
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("BENCH_OPENAI_MODEL", "gpt-4o-mini")

# Casi di test: expected = valore atteso per campo; "" = DEVE restare
# vuoto (un valore qui e' un'invenzione). I campi numerici sono
# confrontati come float. Derivati da E2E-1/E2E-3 e dal dataset M4.
CASES = [
    {"id": "e2e1", "prompt": "foro filettato M6, tolleranza 0.3mm, passo 1.0",
     "expected": {"feature": "thread", "nominal": "M6", "pitch": 1.0,
                  "tolerance": 0.3, "tolerance_type": "", "measured_as": ""}},
    {"id": "en-thread", "prompt": "threaded hole M8, pitch 1.25, tolerance 0.2 mm",
     "expected": {"feature": "thread", "nominal": "M8", "pitch": 1.25,
                  "tolerance": 0.2, "tolerance_type": "", "measured_as": ""}},
    {"id": "tol-type-given", "prompt": "filettatura M10 passo 1.5, tolleranza 0.4mm diametrale",
     "expected": {"feature": "thread", "nominal": "M10", "pitch": 1.5,
                  "tolerance": 0.4, "tolerance_type": "diametrale"}},
    {"id": "tol-type-lato", "prompt": "vite M5, passo 0.8, tolleranza 0.1 mm per lato",
     "expected": {"feature": "thread", "nominal": "M5", "pitch": 0.8,
                  "tolerance": 0.1, "tolerance_type": "per_lato"}},
    {"id": "no-tol", "prompt": "foro filettato M12 passo 1.75",
     "expected": {"feature": "thread", "nominal": "M12", "pitch": 1.75,
                  "tolerance": "", "tolerance_type": "", "measured_as": ""}},
    {"id": "no-pitch", "prompt": "threaded hole M6, tolerance 0.3mm",
     "expected": {"feature": "thread", "nominal": "M6", "pitch": "",
                  "tolerance": 0.3, "tolerance_type": "", "measured_as": ""}},
    {"id": "tiny-pitch", "prompt": "filettatura M6 passo 0.05, tolleranza 0.3",
     "expected": {"feature": "thread", "nominal": "M6", "pitch": 0.05,
                  "tolerance": 0.3, "tolerance_type": ""}},
    {"id": "plain-hole", "prompt": "foro passante da 8 mm",
     "expected": {"feature": "hole", "pitch": "", "tolerance_type": "",
                  "measured_as": ""}},
    {"id": "press-fit", "prompt": "accoppiamento press-fit albero 10mm",
     "expected": {"feature": "press_fit", "pitch": "", "tolerance_type": ""}},
    {"id": "it-canonical", "prompt": "foro filettato M4 con passo 0.7",
     "expected": {"feature": "thread", "nominal": "M4", "pitch": 0.7,
                  "tolerance_type": "", "measured_as": ""}},
    {"id": "vague", "prompt": "una filettatura metrica standard",
     "expected": {"feature": "thread", "nominal": "", "pitch": "",
                  "tolerance": "", "tolerance_type": "", "measured_as": ""}},
    {"id": "measured-as-given", "prompt": "M6 passo 1.0, tolleranza 0.2 misurata su nocciolo",
     "expected": {"feature": "thread", "nominal": "M6", "pitch": 1.0,
                  "tolerance": 0.2}},
    {"id": "boss", "prompt": "boss cilindrico diametro 12",
     "expected": {"feature": "boss", "pitch": "", "tolerance_type": "",
                  "measured_as": ""}},
    {"id": "en-vague-tol", "prompt": "M8 thread, tight tolerance",
     "expected": {"feature": "thread", "nominal": "M8", "pitch": "",
                  "tolerance": "", "tolerance_type": "", "measured_as": ""}},
    {"id": "snap-fit", "prompt": "aggancio snap-fit per coperchio",
     "expected": {"feature": "snap_fit", "nominal": "", "pitch": "",
                  "tolerance": "", "tolerance_type": "", "measured_as": ""}},
]


def http_json(url, body=None, headers=None, timeout=300):
    req = urllib.request.Request(url, method="POST" if body is not None else "GET")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=timeout) as r:
        return json.loads(r.read().decode())


def load_template_and_fields():
    cf = json.load(open(CHATFLOW))
    fd = json.loads(cf["flowData"]) if isinstance(cf.get("flowData"), str) else cf.get("flowData", cf)
    template, fields = None, []
    for n in fd.get("nodes", []):
        name = n.get("data", {}).get("name")
        if name == "promptTemplate":
            template = n["data"]["inputs"]["template"]
        if name == "structuredOutputParser":
            for f in json.loads(n["data"]["inputs"]["jsonStructure"]):
                if f.get("property"):
                    fields.append((f["property"], f.get("type", "string"),
                                   f.get("description", "")))
    if not template or not fields:
        sys.exit("template o schema non trovati nel chatflow — file cambiato?")
    return template, fields


def build_prompt(template, fields, user_prompt):
    schema_desc = "\n".join(
        f'- "{p}" ({t}): {d}' for p, t, d in fields)
    return (template.replace("{prompt}", user_prompt)
            + "\n\nRespond ONLY with a JSON object with exactly these keys "
              "(use \"\" for unknown string fields, null for unknown numbers):\n"
            + schema_desc)


def call_ollama(model, prompt):
    body = {"model": model, "stream": False, "format": "json",
            "options": {"temperature": 0},
            "messages": [{"role": "user", "content": prompt}]}
    r = http_json(f"{OLLAMA}/api/chat", body)
    return r["message"]["content"]


def ensure_ollama_model(model):
    tags = http_json(f"{OLLAMA}/api/tags")
    have = {m["name"] for m in tags.get("models", [])}
    if model in have or f"{model}:latest" in have:
        return True
    print(f"  pull {model} (puo' richiedere minuti)...", flush=True)
    try:
        http_json(f"{OLLAMA}/api/pull", {"model": model, "stream": False},
                  timeout=1800)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"  pull FALLITO per {model}: {e} — modello saltato", flush=True)
        return False


def call_openai(model, prompt):
    body = {"model": model, "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}]}
    r = http_json("https://api.openai.com/v1/chat/completions", body,
                  headers={"Authorization": f"Bearer {OPENAI_KEY}"})
    return r["choices"][0]["message"]["content"]


def is_empty(v):
    return v is None or v == "" or v == 0 and isinstance(v, bool)


def score_case(expected, raw, field_names):
    try:
        got = json.loads(raw)
        assert isinstance(got, dict)
    except (json.JSONDecodeError, AssertionError):
        return {"json_ok": False, "invented": 0, "must_empty": 0,
                "extract_ok": 0, "extract_tot": 0, "raw": raw[:200]}
    invented = must_empty = extract_ok = extract_tot = 0
    for field, want in expected.items():
        have = got.get(field)
        if want == "":
            must_empty += 1
            if not is_empty(have):
                invented += 1
        else:
            extract_tot += 1
            if isinstance(want, float) or isinstance(want, int):
                try:
                    if have is not None and abs(float(have) - float(want)) < 1e-9:
                        extract_ok += 1
                except (TypeError, ValueError):
                    pass
            elif isinstance(have, str) and have.strip().lower() == str(want).lower():
                extract_ok += 1
    missing = [f for f in field_names if f not in got]
    return {"json_ok": not missing, "invented": invented,
            "must_empty": must_empty, "extract_ok": extract_ok,
            "extract_tot": extract_tot, "raw": json.dumps(got)[:300]}


def main():
    template, fields = load_template_and_fields()
    field_names = [p for p, _, _ in fields]
    os.makedirs(OUT_DIR, exist_ok=True)
    candidates = [("ollama", m) for m in MODELS]
    if OPENAI_KEY:
        candidates.append(("openai", OPENAI_MODEL))
    else:
        print("OPENAI_API_KEY assente — candidato API di riferimento saltato")

    rows, summary = [], []
    for backend, model in candidates:
        print(f"\n== {model} ({backend}) ==", flush=True)
        if backend == "ollama" and not ensure_ollama_model(model):
            continue
        tot = {"json_ok": 0, "invented": 0, "must_empty": 0,
               "extract_ok": 0, "extract_tot": 0, "lat": []}
        for case in CASES:
            prompt = build_prompt(template, fields, case["prompt"])
            t0 = time.time()
            try:
                raw = (call_ollama if backend == "ollama" else call_openai)(model, prompt)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
                raw = f"ERRORE: {e}"
            lat = time.time() - t0
            s = score_case(case["expected"], raw, field_names)
            tot["json_ok"] += s["json_ok"]
            tot["invented"] += s["invented"]
            tot["must_empty"] += s["must_empty"]
            tot["extract_ok"] += s["extract_ok"]
            tot["extract_tot"] += s["extract_tot"]
            tot["lat"].append(lat)
            rows.append({"model": model, "case": case["id"], "latency_s": round(lat, 2),
                         "json_ok": s["json_ok"], "invented": s["invented"],
                         "extract_ok": f'{s["extract_ok"]}/{s["extract_tot"]}',
                         "raw": s["raw"]})
            print(f"  {case['id']}: json={'ok' if s['json_ok'] else 'NO'} "
                  f"inventati={s['invented']} estratti={s['extract_ok']}/{s['extract_tot']} "
                  f"{lat:.1f}s", flush=True)
        n = len(CASES)
        summary.append({
            "model": model, "backend": backend,
            "json_ok_pct": round(100 * tot["json_ok"] / n, 1),
            "invented_pct": round(100 * tot["invented"] / max(tot["must_empty"], 1), 1),
            "extract_ok_pct": round(100 * tot["extract_ok"] / max(tot["extract_tot"], 1), 1),
            "latency_avg_s": round(sum(tot["lat"]) / n, 2),
        })

    with open(os.path.join(OUT_DIR, "bench_l25_cases.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    with open(os.path.join(OUT_DIR, "bench_l25_summary.md"), "w") as f:
        f.write("# Bench L2.5 — matrice modelli\n\n"
                "| modello | backend | json_ok% | **inventati%** | estratti_ok% | lat media (s) |\n"
                "|---|---|---|---|---|---|\n")
        for s in summary:
            f.write(f"| {s['model']} | {s['backend']} | {s['json_ok_pct']} | "
                    f"**{s['invented_pct']}** | {s['extract_ok_pct']} | {s['latency_avg_s']} |\n")
        f.write("\nSoglia di candidatura: inventati=0, json_ok=100, "
                "estratti_ok >= baseline granite4:1b.\n")
    print("\n=== RIEPILOGO ===")
    for s in summary:
        print(s)
    print(f"\nreport: {OUT_DIR}/bench_l25_summary.md (+ cases.csv) — includere nell'harvest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
