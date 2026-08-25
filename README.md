# CALIPER

### A verification layer for LLM-generated CAD geometry

*Working name — see [Naming](#naming). Not yet checked for namespace conflicts.*

**Status:** early prototype, but end-to-end verified twice over — once
live on RunPod, once in the real Docker topology on dedicated private
hardware. Architecture defined; the deterministic verification layer
(Livello 3 — Go/No-Go gauge checks, dimensional checks, a sketch-first
generation path) is implemented and independently verified for the ISO
metric thread feature class (milestones M1–M3, see
[`docs/logbook.md`](docs/logbook.md)). The retrieval loop (Livello 7)
distinguishes physical from simulated evidence end-to-end (M4, `source:
virtual|physical` on every retrieved record, anti-bias gate wired into
the generation loop). A full critical technical review of M1–M4
([`docs/review_tecnica.md`](docs/review_tecnica.md), issue
[#15](https://github.com/danielesalpietro/caliper-cad/issues/15)) found
three undocumented blocking defects on never-exercised seams; the
resulting recovery plan
([`docs/piano_recupero.md`](docs/piano_recupero.md), milestones M5–M8)
has closed M5 through M7: the M5 fix pack (issue
[#17](https://github.com/danielesalpietro/caliper-cad/issues/17)), the
first live end-to-end run on RunPod with a real generation model — L2.5
→ L2 → verify → gauge-check GO/NO-GO, 9/9 test cases (M6, issue
[#18](https://github.com/danielesalpietro/caliper-cad/issues/18)) — and
the first real Docker Compose topology on dedicated private hardware
(RTX 3090): first-ever `docker build`/`docker compose up`, active
network isolation confirmed (not just declared), and the first G-code
the project has ever produced (M7, issue
[#19](https://github.com/danielesalpietro/caliper-cad/issues/19), report
in [`docs/report_m7_run0.md`](docs/report_m7_run0.md)). No ground-truth
dataset collected yet, no physical print-and-measure loop closed yet
(scope of M8, next).

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
script. This repository contains the architecture design, a set of open
questions, and — as of milestones M1–M3 — a working, independently
verified deterministic verification layer for one feature class (ISO
metric threads, plus scaffolding for two more); the generation
orchestration, ground-truth dataset, and retrieval stages remain largely
undeveloped. See [`docs/logbook.md`](docs/logbook.md) for the current
state in detail.

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

Architecture defined. See
[`docs/architettura-prototipo-mesh-llm.md`](docs/architettura-prototipo-mesh-llm.md)
for the full design and its own explicit list of unresolved dependencies.

A separate, more recent thread of work — the "Ciclo di Collaudo
Virtuale" (virtual gauging cycle), tracked in
[`docs/logbook.md`](docs/logbook.md) — has since built and independently
verified real code for Livello 3 (verification), scoped to the ISO
metric thread feature class:

- **M1–M2 (merged, done):** a CPU-only, deterministic Go/No-Go gauge
  checker (`services/verifier/executor/gauge_check.py`) — exact B-Rep
  boolean interference, swept interference along an insertion/screwing
  path, exact minimum-distance checks — against versioned reference
  gauges (`config/gauges/`), plus a retry policy for the generation loop
  (`services/orchestrator/retry_policy.py`).
- **M3 (merged, partially done):** a "sketch-first" generation path — a
  validated JSON schema for 2D sketch constraints
  (`services/orchestrator/sketch_schema.py`) compiled to CadQuery
  (`sketch_compiler.py`) — wired into the retry loop end-to-end and
  verified against hand-written test cases. **Not yet done:** a real run
  with a live generation model (no Flowise instance available in the
  sandboxes this was built in) — see open issue
  [#4](https://github.com/danielesalpietro/caliper-cad/issues/4).
- **M4 (implemented, independently verified):** closes the retrieval
  loop — `retry_log.jsonl` (`services/orchestrator/retry_policy.py`)
  extended with `feature`/`spec_key` instead of a parallel format; an
  anti-bias rule (`services/orchestrator/virtual_memory.py`) that
  refuses to exclude a generation strategy from N virtual (simulated)
  failures alone — a physical FAIL (Livello 6) on the same geometry is
  required, and its absence fails open toward generation, never toward
  exclusion — wired as a real gate in
  `services/orchestrator/generate_and_verify.py` before any L2 call, not
  just documented; the Livello 7 agent (`services/stream-agent/app.py`)
  now queries two separate Qdrant collections (physical/virtual, never
  merged) with `source` explicit on every retrieved record, not only in
  the embedded text. **Not yet done:** no live Ollama/Qdrant instance
  and no real Livello 6 dataset in the sandboxes this was built in —
  verified against on-disk fixtures and `py_compile`, not run end-to-end
  — see open issue [#5](https://github.com/danielesalpietro/caliper-cad/issues/5).

- **Review tecnica + piano di recupero (post-M4):** a read-only critical
  review of the whole project
  ([`docs/review_tecnica.md`](docs/review_tecnica.md), issue
  [#15](https://github.com/danielesalpietro/caliper-cad/issues/15))
  found, among others, three blocking defects on seams never exercised
  end-to-end — the dimensional bbox check contradicting M3's host-block
  topology (C1), only the GO gauge wired into the loop (C2), the
  `snap_fit` preset crashing the orchestrator (C3) — and a forgeable
  verdict (C7). The recovery plan
  ([`docs/piano_recupero.md`](docs/piano_recupero.md)) tracks milestones
  M5–M8 (issues
  [#17](https://github.com/danielesalpietro/caliper-cad/issues/17)–[#20](https://github.com/danielesalpietro/caliper-cad/issues/20)).
- **M5 (merged):** the fix pack closing C1–C7 and C10 quick wins,
  including a per-feature dimensional contract, the NO-GO gauge in the
  loop, a trusted measurement process (`measure_verdict.py`) that makes
  the verdict unforgeable by generated code, a `param_first` L2
  strategy, deterministic Qdrant point ids, and a revocable
  virtual-memory exclusion keyed on
  `tolerance`/`pitch`/`checker_version`. Details and red-before-green
  evidence in [`docs/logbook_fase5.md`](docs/logbook_fase5.md).
- **Images on GHCR:** all four compose service images plus the
  monolithic RunPod pod image (`ops/runpod/`, pinned versions) are built
  and published by
  [`.github/workflows/publish-images.yml`](.github/workflows/publish-images.yml)
  on every relevant push — the first image builds in the project's
  history.
- **M6 (merged):** the first live end-to-end run of the whole pipeline
  — L2.5 (Ollama) → L2 (GPT) → `/verify` → `/gauge-check` GO/NO-GO →
  retry loop → virtual memory (Qdrant) — on a RunPod pod (native
  processes, no Docker-in-Docker available there). 9/9 test cases run;
  a SIGSEGV in the executor was root-caused (native thread pools sized
  on *visible* CPU count, not the real cgroup quota) and fixed. Details
  in [`docs/logbook_fase6.md`](docs/logbook_fase6.md).
- **M7 (merged):** the same test suite reproduced in the real Docker
  Compose topology, for the first time ever, on dedicated private
  hardware (RTX 3090) rather than a rented pod. First `docker
  build`/`docker compose up` in the project's history; three isolation
  guarantees confirmed *active* (a network call from inside
  `verifier-executor` genuinely fails, a write to the read-only Docker
  socket proxy is rejected, a path-traversal attempt on `/gauge-check`
  is rejected over HTTP) rather than only declared; the first G-code the
  project has ever produced. Six real bugs found and fixed, all on
  seams M6 never exercised (Flowise crashing on every Docker boot, a
  wrong slicer entrypoint, CPU/memory budgets that don't transfer
  between environments, an Ollama URL hardcoded to `localhost`). Full
  analysis, including a hardware/software fingerprint of the run
  environment, in [`docs/report_m7_run0.md`](docs/report_m7_run0.md);
  per-test detail in [`docs/logbook_fase7.md`](docs/logbook_fase7.md).

The verification scripts accumulated across M1–M5 (14 through M4, 21
after M5) run automatically on every push/PR via
[`.github/workflows/regression.yml`](.github/workflows/regression.yml).

No ground-truth dataset (Livello 6) has been collected yet and no
physical print-and-measure loop has been closed (scope of M8, next), and
no local-generation feasibility test (Rischio #1) has been run
(optional extra of M6, served via vLLM).

## 8. Local stack (Docker)

A working scaffold exists as [`docker-compose.yml`](docker-compose.yml),
covering the execution engine, retrieval, and slicing portions of the
pipeline — run end-to-end for real in M7 (see
[`docs/report_m7_run0.md`](docs/report_m7_run0.md)), GPU detected
correctly, all Flowise chatflows built and tested against the live
compose topology. The Flowise image is built locally
(`ops/docker/flowise/`) rather than pulled directly: the upstream
`flowiseai/flowise:3.1.4` image crashes on every boot with a default
SQLite session store (a `connect-sqlite3` API mismatch, see the M7
logbook) — patched, not worked around. A persistent
`fetch failed`/Flowise↔Ollama connection issue some setups hit turned
out, in M7, to be a versioned chatflow pointing its Ollama node at
`localhost` (valid when Flowise ran natively on the same RunPod pod in
M6, not valid across separate Compose containers) — fixed in the
versioned chatflow.

| Service | Role | Image | Size |
|---|---|---|---|
| `flowise` | execution engine — input, specification normalization, generation, consultive retrieval (Livelli 1, 2.5, 2, 7) | `flowiseai/flowise` | ~950 MB |
| `ollama` | local model runtime for the retrieval agent (embedding + chat) | `ollama/ollama` | ~4 GB + models |
| `qdrant` | vector store for the retrieval agent | `qdrant/qdrant` | ~75 MB |
| `stream-agent` | retrieval/grounding agent (Livello 7) — adapted from [NORTHSTREAM](https://github.com/danielesalpietro/NORTHSTREAM): indexes the frozen dataset from disk instead of consuming a Kafka stream. **[M4]** Also indexes the virtual gauging log (`retry_log.jsonl`) into a second, separate Qdrant collection — physical and virtual results are never merged, and every retrieved record carries an explicit `source: physical\|virtual` | built locally | ~300 MB |
| `verifier` | deterministic verifier (Livello 3) — not a Flowise node (Rischio #9), callable as an HTTP tool. Static checks (Python syntax, `import cadquery`, `result` assignment) always; delegates execution + dimensional measurement to `verifier-executor` via a shared volume | built locally | ~150 MB |
| `verifier-executor` | isolated execution of LLM-generated CadQuery code (Livello 3, phase 2) — `network_mode: none`, communicates with `verifier` only through the `verifier_exec` volume (job/result files), per-job CPU/memory limits via `resource.setrlimit`. Runs the code, measures the bounding box, compares against the spec's `nominal`±`tolerance` where a preset defines the check, and (**[M3]**) exports the validated solid as STEP to a writable subfolder of the same volume (`/exec/parts`) so a gauge check can run against it. **[M1–M2]** Also runs the virtual Go/No-Go gauge check (`gauge_check.py`) as a separate subprocess with its own empirically-calibrated timeout — static B-Rep interference, interference swept along an insertion/screwing path, or exact minimum-distance — comparing a part (known-static or just-generated) against a known gauge STEP; two extra mounts, `${DATA_DIR:-./data}/models` (read-only) and `config/gauges/` (read-only, see [`config/gauges/README.md`](config/gauges/README.md)); no new HTTP route | built locally | ~2.8 GB (CadQuery + OCP + VTK) |
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
Gauge format for the virtual collaudo (M1): [`config/gauges/`](config/gauges/README.md).

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

Open for code contributions on the current milestone (M4, closing the
retrieval loop — see [`docs/logbook.md`](docs/logbook.md) and issue
[#5](https://github.com/danielesalpietro/caliper-cad/issues/5)) and on
the open questions above. Before opening a PR, read
[`docs/logbook.md`](docs/logbook.md)'s "Processo di handoff e CI"
section — every change should keep the 12+ verification scripts in
[`.github/workflows/regression.yml`](.github/workflows/regression.yml)
green, and add new ones for new behavior rather than leaving them only
manually runnable.
