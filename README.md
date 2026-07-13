# CALIPER

### A verification layer for LLM-generated CAD geometry

*Working name — see [Naming](#naming). Not yet checked for namespace conflicts.*

**Status:** concept / pre-implementation. Architecture defined, no
verification code written, no dataset collected.

---

## Abstract

Large language models can now generate parametric CAD code and triangulated
mesh geometry directly from natural-language descriptions, including
dimensioned features such as threaded fits toleranced to tenths of a
millimeter. What is missing is not generation capability but an
independent, deterministic layer that verifies a generated geometry against
its stated specification *before* that geometry is trusted — for slicing,
for manufacturing, or for reuse. CALIPER is a proposed architecture for that
verification layer: a structured specification format, a geometric and
parametric checker, and a ground-truth dataset built from physically
measured outcomes — designed to sit between any generation model, cloud or
local, and any downstream manufacturing step. The pipeline is structured
as composable nodes with feature-specific presets rather than a monolithic
script. This repository currently contains the architecture design and a
set of open questions, not a working implementation.

## 1. Motivation

Precision mechanical design — threaded fits, press fits, clearances
specified to tenths of a millimeter — has traditionally required either a
trained CAD operator or a dedicated CAM/CAE toolchain. A separate and
largely undocumented practice has emerged around general-purpose LLMs:
prompting them with detailed geometric and dimensional descriptions to
generate mesh or parametric geometry, then slicing and 3D printing it
directly, with tolerances tight enough for functional mechanical fits.

This works, some of the time, on some models. It is not yet a method with a
name, a benchmark, or a way to know in advance whether a given prompt will
succeed. There is currently no widely available tool that:

- translates a natural-language dimensional specification into a
  structured, unambiguous form (tolerance type, nominal value, measurement
  convention);
- checks a generated geometry against that structured specification
  *before* it is sliced;
- records the physically measured outcome as ground truth, separate from
  and strictly downstream of any simulated or purely geometric check;
- uses that ground truth to inform future generations, rather than
  starting from an unconditioned prompt every time.

CALIPER is an attempt to define that missing layer, independent of any one
generation model.

## 2. Limitations of cloud LLMs for this task, today

Cloud-hosted general-purpose LLMs are, at present, the only widely
available models capable of the geometric reasoning this task requires.
Open, locally-run models specialized for CAD generation exist, but are
trained largely on simple primitive-and-extrusion datasets, and their
ability to handle threaded, tightly-toleranced features has not been
established (see [Open questions](#5-open-questions)). Relying on cloud
models for a precision-dependent workflow, however, introduces failure
modes that sit outside the control of the person using them:

- **Silent model drift.** A provider can update a model's weights or
  behavior at any time. A prompt that reliably produced correct geometry
  previously may silently produce something subtly different — same
  prompt, different output — with no version pin available to the end
  user.
- **Increasing conversational abstraction.** As models are tuned toward
  general-purpose, safety-conscious conversational use, literal,
  highly specific numerical instructions appear more likely to be
  paraphrased, rounded, or "helpfully" reinterpreted — the opposite of
  what a tolerance-critical task requires.
- **No first-class geometric verification.** A cloud LLM can be asked to
  double-check its own output, but it is not a deterministic geometric
  kernel. Asking a language model to grade its own dimensional accuracy is
  not equivalent to measuring it.
- **No persistent, addressable memory of what worked.** Each session
  effectively starts over. Nothing prevents a previously successful
  specification from being regenerated worse next time, and nothing
  surfaces "this exact feature was already solved" before generating from
  scratch.

None of this is a defect specific to one vendor. It is a structural
property of using a general-purpose, frequently-updated, non-deterministic
model as the sole authority in a task that has a physically correct
answer.

## 3. Approach (summary)

The full architecture is described in
[`docs/architettura-prototipo-mesh-llm.md`](docs/architettura-prototipo-mesh-llm.md)
(see also the visual [architecture diagram](docs/architecture.html)).
In summary, the design separates concerns into a sequenced pipeline:

1. **Specification normalization** — natural language to structured,
   human-confirmed JSON (nominal value, tolerance type, feature type).
   Where automated, output should be constrained to the schema at the
   decoding level (e.g. JSON Schema enforcement) — this guarantees
   syntactic validity, not semantic correctness, so human confirmation
   remains necessary as an independent check, not a redundant one.
2. **Generation** — cloud or local LLM, swappable, not architecturally
   privileged. For local-generation feasibility testing, temperature is
   fixed at 0 for reproducibility between runs — this does not by itself
   guarantee dimensional accuracy, which remains the verifier's job.
3. **Verification** — deterministic, non-LLM: parametric check against CAD
   code where available; geometric integrity check (manifold, watertight)
   always; dimensional check against the mesh as fallback. This is
   stronger than the common "LLM-as-judge" pattern, where a second model
   grades the first — still probabilistic, even if independently
   configured. Here the judge is a deterministic script or a physical
   measurement, so it does not inherit the uncertainty it is meant to
   remove.
4. **Slicing and printing** — parameters fixed and versioned.
5. **Physical validation** — the sole source of ground truth; not
   substitutable by simulation.
6. **Frozen dataset** — every case recorded only after physical
   measurement, including machine, material/batch, and outcome —
   successes and failures alike.
7. **Retrieval** — new prompts checked against similar validated cases
   before generation, hybrid exact-filter plus semantic search.
8. **Fine-tuning** (future, conditional) — only once the dataset is large
   enough to provide real training signal.

A local-generation phase is explicitly downstream of and dependent on the
verification and dataset layers, not a parallel or prerequisite track: the
verifier has to exist before there is anything meaningful to test a local
model against.

## 4. Execution model: nodes and presets

Each stage above is, in practice, a node: typed input, typed output,
independently testable, replaceable without touching the others — cloud
and local generation, for instance, are two implementations of the same
node. A **preset** is a ready-made configuration for a recurring
mechanical feature class — e.g. "ISO metric thread," "press fit," "snap
fit" — that pre-fills the specification schema and default tolerance
convention for that feature, reducing how much free-form interpretation
step 1 has to do.

**Execution engine:** [Flowise](https://flowiseai.com/) (LangChain-based,
drag-and-drop, native Qdrant node) orchestrates the LLM-centric nodes —
specification normalization, generation, consultive retrieval. It is not a
fit for deterministic or physical-side-effect steps (verification, slicing,
physical measurement, dataset write); those stay external scripts, invoked
as Custom Tool/HTTP calls, never rewritten inside a flow node. Web
interface: [NORTHSTREAM](https://github.com/danielesalpietro/NORTHSTREAM)'s
Open WebUI (chat, to query the grounded dataset) and its landing/dashboard
page, reused in place of a custom UI. See [§8](#8-local-stack-docker) for
the concrete container setup.

## 5. What this is not

- Not a new CAD generation model. It is intended to sit in front of
  existing ones — cloud or open-source — and to be indifferent to which
  one is used.
- Not a claim that any specific small open-weight model can currently
  replace large cloud models for this task. That is an open, untested
  question.
- Not a production tool. At this stage it is an architecture and a set of
  falsifiable open questions.

## 6. Open questions

These are unresolved by design, not omissions:

- Does a specification-to-geometry pipeline actually require a large
  general-purpose LLM, or is the geometric reasoning narrow enough for a
  small, task-specific model once enough ground-truth data exists?
- Can a deterministic verifier catch failure modes that matter physically
  (e.g. interference on a helical thread) from mesh alone, without access
  to parametric or B-rep data?
- How much physically-measured data is "enough" before retrieval-based
  grounding meaningfully changes generation quality — tens of cases,
  hundreds, more?
- Is tolerance-specification ambiguity (diametral vs. per-side vs.
  measured-at-root) better resolved by a stricter input format, by an
  interactive confirmation step, or by both together?
- Does this approach generalize beyond threaded mechanical fits — sliding
  fits, snap fits, non-cylindrical tolerances — or does each feature class
  require its own verification logic?

No answers are asserted here. Contributions that answer, partially answer,
or sharpen any of these questions are the most useful kind of contribution
this project can receive right now.

## 7. Status

Architecture defined. No verification code written yet. No dataset
collected yet. See
[`docs/architettura-prototipo-mesh-llm.md`](docs/architettura-prototipo-mesh-llm.md)
for the current design and its own explicit list of unresolved dependencies.

## 8. Local stack (Docker)

A working scaffold exists as [`docker-compose.yml`](docker-compose.yml),
covering the execution engine, retrieval, and slicing portions of the
pipeline. Run end-to-end at least once (GPU detected correctly, first
Flowise chatflow built and tested — see
[`docs/architettura-prototipo-mesh-llm.md`](docs/architettura-prototipo-mesh-llm.md)
for the full log). One known intermittent issue: the Flowise↔Ollama
connection occasionally fails with `fetch failed`, likely an upstream
bundling bug in `flowiseai/flowise:latest` — not yet isolated, see the
architecture doc's risk list. Retrying usually works.

| Service | Role | Image | Size |
|---|---|---|---|
| `flowise` | execution engine — input, specification normalization, generation, consultive retrieval (Livelli 1, 2.5, 2, 7) | `flowiseai/flowise` | ~950 MB |
| `ollama` | local model runtime for the retrieval agent (embedding + chat) | `ollama/ollama` | ~4 GB + models |
| `qdrant` | vector store for the retrieval agent | `qdrant/qdrant` | ~75 MB |
| `stream-agent` | retrieval/grounding agent (Livello 7) — adapted from [NORTHSTREAM](https://github.com/danielesalpietro/NORTHSTREAM): indexes the frozen dataset from disk instead of consuming a Kafka stream | built locally | ~300 MB |
| `verifier` | deterministic verifier (Livello 3) — not a Flowise node (Rischio #9), callable as an HTTP tool. Static checks (Python syntax, `import cadquery`, `result` assignment) always; delegates execution + dimensional measurement to `verifier-executor` via a shared volume | built locally | ~150 MB |
| `verifier-executor` | isolated execution of LLM-generated CadQuery code (Livello 3, phase 2) — `network_mode: none`, communicates with `verifier` only through the `verifier_exec` volume (job/result files), per-job CPU/memory limits via `resource.setrlimit`. Runs the code, measures the bounding box, compares against the spec's `nominal`±`tolerance` where a preset defines the check | built locally | ~2.8 GB (CadQuery + OCP + VTK) |
| `open-webui` | chat interface to query the grounded dataset | `ghcr.io/open-webui/open-webui` | ~1.4 GB |
| `dashboard` | entry point to the stack — per-service status (server-side health checks) and read-only logs, ordered by pipeline level; no start/stop/restart by design (see Networking below) | built locally | ~150 MB |
| `docker-socket-proxy` | the only container that touches `/var/run/docker.sock`, scoped to `CONTAINERS=1`, `LOGS=1`, `POST=0` — list/inspect/logs only, every write action (start/stop/restart/exec/create/delete) rejected regardless of caller | `tecnativa/docker-socket-proxy` | ~15 MB |
| `prusaslicer` | slicing (Livello 4) — CLI-only, invoked on demand, not a running service; see [prusaslicer-cli-docker](https://github.com/danielesalpietro/prusaslicer-cli-docker) | `billa05/prusacli` | 366 MB |

**Networking.** Services that genuinely need to reach each other
(`flowise`, `stream-agent`, `open-webui`, `ollama`, `qdrant`, `verifier`)
share one network. Services that don't are isolated: `prusaslicer` runs
with `network_mode: none` — a file-in, file-out batch job, not a server,
has no legitimate reason to reach the network in either direction.
`verifier-executor` also runs with `network_mode: none`, for a different
reason: it executes LLM-generated code, which is untrusted by definition
— it talks to `verifier` only through a shared volume (job/result files),
never over HTTP, and never gets a socket to the Docker host.
`docker-socket-proxy` publishes no port to the host at all — reachable
only from `dashboard`, over the internal network. `dashboard` itself is
the one deliberate exception to "isolate what doesn't need to talk to
anything": it's multi-homed (both networks), because unlike the old
static landing page it needs to reach every other service for
server-side status checks (client-side `fetch()` would hit CORS on
services that don't set the header, and would also mean the browser
reaching containers directly) and to reach `docker-socket-proxy` for
logs. It **cannot** start, stop, or restart anything — that capability
was deliberately left out; see the risk entry in the architecture doc.

**Capacity.** ~13 GB compressed image pull (~18–21 GB once
extracted/running), dominated by `ollama`, `open-webui`, and
`verifier-executor` (CadQuery/OCP/VTK, ~2.8 GB). RAM: roughly 5–8 GB peak
across all containers plus the two local models loaded (`granite4:1b`,
`granite-embedding:30m`); add ~1–2 GB transiently when `verifier-executor`
runs a job (capped at 2 GB per job via `RLIMIT_AS`). VRAM: ~2.5–3.5 GB for
those same models — modest relative to a current mid/high-range GPU. None
of this is required to read or evaluate the architecture; it only matters
once running the stack locally.

Configuration template: [`.env.example`](.env.example). Slicing profile:
[`config/prusaslicer/caliper-pla.ini`](config/prusaslicer/caliper-pla.ini).

## 9. Installation

### Prerequisites

- Docker Desktop (or Docker Engine + Compose v2). On Windows, WSL2 backend.
- NVIDIA Container Toolkit + a CUDA-capable GPU, if you want the retrieval
  agent's local models on GPU (optional — Ollama falls back to CPU,
  slower).
- ~20 GB free disk space (see [Capacity](#8-local-stack-docker) above).

### Steps

1. Clone and copy the environment template:

   ```sh
   git clone https://github.com/danielesalpietro/caliper-cad.git
   cd caliper-cad
   cp .env.example .env
   ```

2. Start the stack:

   ```sh
   docker compose up -d
   ```

   First run pulls ~10 GB of images. `prusaslicer` stays out — it's
   profile-gated and invoked on demand only (see §8, and the "not
   verified" note on its mount paths below).

3. Pull the local models into Ollama — not bundled in the image, and not
   auto-pulled on first request:

   ```sh
   docker compose exec ollama ollama pull granite4:1b
   docker compose exec ollama ollama pull granite-embedding:30m
   ```

4. Open Flowise (`http://localhost:3000`) and register an account — this
   is Flowise's own local user store, first-run only, unrelated to
   anything else in the stack. Then go to **Settings → API Keys** and
   create a key. It cannot exist before this step, so it can't be
   pre-baked into the image or `.env.example`.

5. Put that key in your local `.env` as `FLOWISE_API_KEY`, then import the
   chatflows versioned in [`services/flowise/chatflows/`](services/flowise/chatflows):

   ```sh
   docker compose up flowise-init
   ```

   Idempotent — safe to re-run. Skips chatflows that already exist by
   name, so it's also how you pick up newly added ones later.

6. Verify:

   | URL | What you should see |
   |---|---|
   | `http://localhost:8000` | dashboard — status + read-only logs for every service, ordered by pipeline level |
   | `http://localhost:3000` | Flowise — "CALIPER - L2.5 Specification Normalization" in the chatflow list |
   | `http://localhost:3010` | Open WebUI — empty until the Livello 6 dataset has real cases |
   | `http://localhost:6333/dashboard` | Qdrant |

### What's not automated yet

- No case in the Livello 6 dataset yet — the retrieval agent (Open
  WebUI / stream-agent) has nothing to ground against until the
  bootstrap described in
  [`docs/architettura-prototipo-mesh-llm.md`](docs/architettura-prototipo-mesh-llm.md)
  happens.
- Only Livello 2.5 (specification normalization) has a chatflow so far —
  Livello 2 (generation) doesn't yet.
- `prusaslicer`'s mount paths (`/models`, `/gcode`, `/config`) are
  deduced from the upstream repo's description, not inspected directly —
  confirm before running `docker compose run prusaslicer` for real.

## Naming

"CALIPER" is a working name, chosen to describe what the project
guarantees — a measured result — rather than what it generates. It has not
yet been checked for conflicts against unrelated existing projects; the
final repository name may differ.

## License

TBD.

## Contributing

Not yet open for code contributions — there is no code. Discussion on the
open questions above, or on the architecture document, is welcome via
issues.
