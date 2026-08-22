import json
import os

REPO = "/workspace/caliper-cad"
CHATFLOWS_DIR = os.path.join(REPO, "services/flowise/chatflows")

with open(os.path.join(CHATFLOWS_DIR, "l25-specification-normalization.json")) as f:
    l25 = json.load(f)

with open("/tmp/tmp_chatflow.json") as f:
    tmp_cf = json.load(f)
tmp_flow = json.loads(tmp_cf["flowData"])
chatopenai_template = next(n for n in tmp_flow["nodes"] if n["data"]["name"] == "chatOpenAI")

l25_nodes = {n["id"]: n for n in l25["nodes"]}
prompt_template_ref = l25_nodes["promptTemplate_0"]
llm_chain_ref = l25_nodes["llmChain_0"]
structured_parser_ref = l25_nodes["structuredOutputParser_0"]


def make_chatopenai(node_id, x, y, model_name="gpt-4o-mini", temperature=0):
    node = json.loads(json.dumps(chatopenai_template))
    node["id"] = node_id
    node["position"] = {"x": x, "y": y}
    node["positionAbsolute"] = {"x": x, "y": y}
    node["data"]["id"] = node_id
    node["data"]["inputs"]["modelName"] = model_name
    node["data"]["inputs"]["temperature"] = temperature
    for anchor in node["data"]["outputAnchors"]:
        old_prefix = "chatOpenAI_0-output-chatOpenAI"
        if anchor["id"].startswith(old_prefix):
            anchor["id"] = node_id + anchor["id"][len("chatOpenAI_0"):]
    for p in node["data"].get("inputParams", []):
        if "id" in p and p["id"].startswith("chatOpenAI_0-"):
            p["id"] = node_id + p["id"][len("chatOpenAI_0"):]
    return node


def make_prompt_template(node_id, x, y, template_text):
    node = json.loads(json.dumps(prompt_template_ref))
    node["id"] = node_id
    node["position"] = {"x": x, "y": y}
    node["positionAbsolute"] = {"x": x, "y": y}
    node["data"]["id"] = node_id
    node["data"]["inputs"]["template"] = template_text
    for p in node["data"].get("inputParams", []):
        p["id"] = node_id + p["id"][len("promptTemplate_0"):]
    for a in node["data"].get("outputAnchors", []):
        a["id"] = node_id + a["id"][len("promptTemplate_0"):]
    return node


def make_llm_chain(node_id, x, y, model_ref_id, prompt_ref_id, parser_ref_id=None):
    node = json.loads(json.dumps(llm_chain_ref))
    node["id"] = node_id
    node["position"] = {"x": x, "y": y}
    node["positionAbsolute"] = {"x": x, "y": y}
    node["data"]["id"] = node_id
    node["data"]["inputs"]["model"] = "{{%s.data.instance}}" % model_ref_id
    node["data"]["inputs"]["prompt"] = "{{%s.data.instance}}" % prompt_ref_id
    node["data"]["inputs"]["outputParser"] = ("{{%s.data.instance}}" % parser_ref_id) if parser_ref_id else ""
    for p in node["data"].get("inputParams", []):
        p["id"] = node_id + p["id"][len("llmChain_0"):]
    for a in node["data"].get("outputAnchors", []):
        if "id" in a:
            a["id"] = node_id + a["id"][len("llmChain_0"):]
        for opt in a.get("options", []):
            opt["id"] = node_id + opt["id"][len("llmChain_0"):]
    return node


def make_structured_parser(node_id, x, y, json_structure):
    node = json.loads(json.dumps(structured_parser_ref))
    node["id"] = node_id
    node["position"] = {"x": x, "y": y}
    node["positionAbsolute"] = {"x": x, "y": y}
    node["data"]["id"] = node_id
    node["data"]["inputs"]["jsonStructure"] = json.dumps(json_structure)
    for p in node["data"].get("inputParams", []):
        p["id"] = node_id + p["id"][len("structuredOutputParser_0"):]
    for a in node["data"].get("outputAnchors", []):
        a["id"] = node_id + a["id"][len("structuredOutputParser_0"):]
    return node


def make_edge(source_id, source_anchor_id, target_id, target_handle_suffix):
    return {
        "source": source_id,
        "sourceHandle": source_anchor_id,
        "target": target_id,
        "targetHandle": target_id + target_handle_suffix,
        "type": "buttonedge",
        "id": f"{source_id}-{source_anchor_id}-{target_id}-{target_id}{target_handle_suffix}",
    }


# ---------------------------------------------------------------------
# Chatflow A: "CALIPER - L2 Generation (CadQuery)" (free_code)
# ---------------------------------------------------------------------
FREE_CODE_TEMPLATE = (
    "Sei un generatore di codice CadQuery per CALIPER (Livello 2). Ricevi "
    "una specifica JSON arricchita (Livello 2.5) di una feature meccanica "
    "e devi restituire SOLO codice Python eseguibile con la libreria "
    "cadquery (modulo gia' disponibile come cq, puoi comunque scrivere "
    "'import cadquery as cq' se preferisci), che costruisce un solido e lo "
    "assegna alla variabile `result` -- obbligatorio: il verificatore "
    "esegue il codice ed esporta lo STEP a partire da `result`.\n\n"
    "Regole vincolanti:\n"
    "- Nessun testo extra, nessuna spiegazione, nessun blocco markdown: "
    "SOLO il codice Python, dalla prima riga.\n"
    "- Unita' sempre in millimetri.\n"
    "- Per una feature \"thread\" (filettatura): genera un blocco ospite "
    "rettangolare/quadrato (host) con un foro filettato la cui profondita' "
    "assiale e' spec['engagement_length_mm'] (se assente, usa un valore "
    "ragionevole coerente col diametro), profilo a V simmetrico "
    "all'angolo spec.get('thread_profile_angle_deg', 60) gradi, diametro "
    "maggiore ricavato da spec['nominal'] (es. \"M6\" -> 6.0mm). Il lato "
    "XY del blocco ospite deve essere STRETTAMENTE maggiore del diametro "
    "maggiore (il foro deve starci dentro).\n"
    "- Non includere mai show_object, exec, import os/sys, apertura file: "
    "codice non fidato eseguito isolato, deve solo costruire geometria.\n\n"
    "Specifica (Livello 2.5, JSON): {question}"
)

free_code_prompt = make_prompt_template("promptTemplate_0", -163, 308, FREE_CODE_TEMPLATE)
free_code_openai = make_chatopenai("chatOpenAI_0", 443, 117)
free_code_chain = make_llm_chain(
    "llmChain_0", 1454, 334,
    model_ref_id="chatOpenAI_0", prompt_ref_id="promptTemplate_0", parser_ref_id=None,
)

free_code_flow = {
    "nodes": [free_code_prompt, free_code_openai, free_code_chain],
    "edges": [
        make_edge(
            "chatOpenAI_0",
            next(a["id"] for a in free_code_openai["data"]["outputAnchors"]),
            "llmChain_0", "-input-model-BaseLanguageModel",
        ),
        make_edge(
            "promptTemplate_0",
            free_code_prompt["data"]["outputAnchors"][0]["id"],
            "llmChain_0", "-input-prompt-BasePromptTemplate",
        ),
    ],
}

# ---------------------------------------------------------------------
# Chatflow B: "CALIPER - L2 Generation (Sketch-First)" (param_first)
# ---------------------------------------------------------------------
PARAM_FIRST_TEMPLATE = (
    "Sei un estrattore di parametri fisici per una filettatura (CALIPER, "
    "Livello 2, strategia param-first). Ricevi una specifica JSON "
    "arricchita (Livello 2.5). Restituisci SOLO un oggetto JSON con "
    "questi 4 campi numerici in millimetri, nessun testo extra, nessun "
    "blocco markdown:\n\n"
    "- major_diameter_mm: diametro maggiore nominale della filettatura "
    "(es. spec['nominal']==\"M6\" -> 6.0)\n"
    "- pitch_mm: passo della filettatura (spec['pitch'] se presente)\n"
    "- engagement_length_mm: lunghezza di impegno / profondita' assiale "
    "del foro filettato\n"
    "- host_xy_mm: lato del blocco ospite quadrato in XY -- deve essere "
    "STRETTAMENTE maggiore di major_diameter_mm (il foro deve starci "
    "dentro)\n\n"
    "Non generare codice, non spiegare: SOLO l'oggetto JSON con questi "
    "4 campi.\n\n"
    "Specifica (Livello 2.5, JSON): {question}"
)

PARAM_FIRST_JSON_STRUCTURE = [
    {"property": "major_diameter_mm", "type": "number", "description": "diametro maggiore nominale in mm", "actions": "", "id": 1},
    {"property": "pitch_mm", "type": "number", "description": "passo della filettatura in mm", "actions": "", "id": 2},
    {"property": "engagement_length_mm", "type": "number", "description": "lunghezza di impegno (profondita' assiale) in mm", "actions": "", "id": 3},
    {"property": "host_xy_mm", "type": "number", "description": "lato XY del blocco ospite in mm, deve essere > major_diameter_mm", "actions": "", "id": 4},
]

param_first_prompt = make_prompt_template("promptTemplate_0", -163, 308, PARAM_FIRST_TEMPLATE)
param_first_openai = make_chatopenai("chatOpenAI_0", 443, 117)
param_first_parser = make_structured_parser("structuredOutputParser_0", 971, 680, PARAM_FIRST_JSON_STRUCTURE)
param_first_chain = make_llm_chain(
    "llmChain_0", 1454, 334,
    model_ref_id="chatOpenAI_0", prompt_ref_id="promptTemplate_0",
    parser_ref_id="structuredOutputParser_0",
)

param_first_flow = {
    "nodes": [param_first_prompt, param_first_openai, param_first_parser, param_first_chain],
    "edges": [
        make_edge(
            "chatOpenAI_0",
            next(a["id"] for a in param_first_openai["data"]["outputAnchors"]),
            "llmChain_0", "-input-model-BaseLanguageModel",
        ),
        make_edge(
            "promptTemplate_0",
            param_first_prompt["data"]["outputAnchors"][0]["id"],
            "llmChain_0", "-input-prompt-BasePromptTemplate",
        ),
        make_edge(
            "structuredOutputParser_0",
            param_first_parser["data"]["outputAnchors"][0]["id"],
            "llmChain_0", "-input-outputParser-BaseLLMOutputParser",
        ),
    ],
}

os.makedirs(CHATFLOWS_DIR, exist_ok=True)
with open(os.path.join(CHATFLOWS_DIR, "l2-generation-cadquery.json"), "w") as f:
    json.dump(free_code_flow, f, indent=2)
    f.write("\n")
with open(os.path.join(CHATFLOWS_DIR, "l2-generation-sketch-first.json"), "w") as f:
    json.dump(param_first_flow, f, indent=2)
    f.write("\n")

manifest_path = os.path.join(CHATFLOWS_DIR, "manifest.json")
with open(manifest_path) as f:
    manifest = json.load(f)

manifest.append({
    "file": "l2-generation-cadquery.json",
    "name": "CALIPER - L2 Generation (CadQuery)",
    "description": "Livello 2, strategia free_code: dalla spec L2.5 arricchita genera codice CadQuery libero che costruisce la variabile `result` (Prompt Template -> ChatOpenAI/GPT -> LLM Chain, output testo).",
})
manifest.append({
    "file": "l2-generation-sketch-first.json",
    "name": "CALIPER - L2 Generation (Sketch-First)",
    "description": "Livello 2, condiviso da sketch_first e param_first (M5): oggi implementato solo per param_first -- dalla spec L2.5 estrae SOLO i parametri fisici della filettatura (major_diameter_mm, pitch_mm, engagement_length_mm, host_xy_mm) come JSON strutturato, compilati poi da sketch_compiler.compile_thread_params_to_code() (Prompt Template -> ChatOpenAI/GPT -> LLM Chain + Structured Output Parser).",
})

with open(manifest_path, "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")

print("File scritti e manifest aggiornato.")
