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

The full architecture is described in [`docs/architecture.md`](docs/architecture.md).
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

Three ways to execute this pipeline were considered: (a) a custom
workflow/graph engine, (b) reusing an existing automation platform such as
n8n via a custom node package, (c) a lightweight declarative runner
(YAML/JSON pipelines, no graph UI). The project starts with (c) — lowest
cost, testable immediately — while designing node boundaries clean enough
to become an n8n node package later, without promising a graph UI that
does not exist yet.

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
collected yet. See [`docs/architecture.md`](docs/architecture.md) for the
current design and its own explicit list of unresolved dependencies.

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
