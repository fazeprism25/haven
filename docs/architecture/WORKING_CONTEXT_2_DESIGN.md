# Working Context 2.0 — Reconstruction, Not Retrieval

## Grounding: what exists today (verified against code, 2026-07-09)

Before proposing anything new, here is what the current pipeline actually does, so this
design extends it rather than duplicating or contradicting it:

- `MemoryEngine.query_with_trace` (`obsidian/memory_engine/engine.py:392`) runs a fixed,
  deterministic chain: `HybridCandidateRetriever` → validity filter → `DeterministicRanker`
  → `AcceptanceStage` → `DeterministicSlotAllocator` → `ContextBuilder`. `ContextBuilder`
  is a pure string renderer over whatever the allocator hands it — it has no concept of
  "what kind of context is needed."
- `WorkingContext` / `WorkingContextBuilder` (`obsidian/memory_engine/working_context_builder.py`,
  `obsidian/ontology/retrieval_models.py:1047`) already group ranked candidates into
  `RoleBucket`s keyed by `MemoryRole` (today: `DECISION, GOAL, TASK, BELIEF, RESEARCH,
  OPEN_QUESTION, REFERENCE`). This is the right shape but a narrow instance: it groups
  whatever retrieval happened to return; it never asks "did we get the right kinds of
  context for this query," and it never notices when a category is empty because nothing
  matched vs. empty because it genuinely doesn't exist.
- `AcceptanceStage` (`obsidian/memory_engine/acceptance_stage.py`) already has the right
  philosophy for this whole project: deterministic thresholds, abstention when nothing
  clears a bar, explicit rejection reasons per candidate rather than silent filtering.
  Working Context 2.0 should copy this philosophy for gap detection, not invent a new one.
- `QueryRewriter` (`obsidian/memory_engine/query_rewriter.py`) already establishes the
  pattern for *optional* LLM involvement in an otherwise deterministic pipeline: off by
  default, one bounded call, cached, fails open to zero effect on error. This is the
  template for where LLM judgment enters Working Context 2.0.
- `ManagerPipeline` (`obsidian/manager_ai/pipeline.py`) already classifies incoming
  conversation turns into `MemoryType`s at write time. It does not currently touch
  anything at read time — all reconstruction work happens fresh, per query, in
  `MemoryEngine.query_working_context`.
- Nothing in the repo today does intent classification, context-type planning, gap
  detection, or context validation. This part of the design is greenfield.

The core diagnosis: **Haven retrieves facts well but has no notion of "state."** A fact
is a sentence; state is "where things stand and why." Reconstruction requires assembling
state from facts, noticing when state is incomplete, and saying so rather than guessing.

---

## 1. How humans reconstruct context after a long absence

Breaking down the cognitive process into stages, because the pipeline in §4 mirrors this
directly:

1. **Orientation** — "What project is this? What was I doing?" A cheap, near-instant
   anchor: not recall of content, just recall of *which* frame to load.
2. **Gist recall** — A compressed summary surfaces first: current goal, general shape of
   where things stood. Not a full transcript — a headline.
3. **Timeline reconstruction** — A rough sequence of what happened recently: last few
   decisions, what changed, in approximate order. Humans don't recall exhaustively; they
   recall the *shape* of the recent past, weighted toward recency and salience.
4. **Selective deep-dive** — Only once oriented does a person decide *what kind of task*
   they're resuming (coding vs. writing vs. deciding), and only then do they pull in the
   specific detail that task needs (a file, a spec, a prior argument).
5. **Hypothesis formation** — A provisional plan forms: "I think the next step was X."
   Held loosely.
6. **Reality check** — The hypothesis gets checked against the actual artifact (the code,
   the doc) before acting. This is the step that catches stale memory — "I thought we
   hadn't done Y yet, but the file shows we did." Humans are good at admitting "I don't
   remember" here rather than confabulating.
7. **Commit and act** — Only after the check does the person proceed, and they update
   their mental state as they go.

The critical property this design must reproduce: **steps 1–3 are cheap and always run;
steps 4–6 are targeted and only pull in what the specific task needs; step 6 is a
falsifiable check, not a rubber stamp.** Most retrieval systems only ever do the
equivalent of step 3 (get relevant facts) and skip 1, 4, 6 entirely — which is exactly
why "continue implementing Haven" reads as amnesia even when the facts are in the
database.

---

## 2. Information categories in a reconstructed working context (state, not memories)

Each category is *state* — a claim about "where things stand now" — synthesized from one
or more memories, not a memory itself. Mapped against existing `MemoryRole`s (✅ = exists
today, 🆕 = new):

| Category | Existing role | Notes |
|---|---|---|
| Project/system identity & purpose | 🆕 (`ContextKind` exists but is a grouping key, not a rendered "what is this" statement) | Anchor step |
| Architecture snapshot (components + responsibility) | 🆕 | Semi-static, doesn't need re-derivation every query |
| Implementation state (built / stubbed / in-progress) | 🆕 | Distinct from TASK — this is "done-ness," not "to-do" |
| Active goals, prioritized | ✅ `GOAL` | Needs prioritization, not just listing |
| Open blockers | 🆕 | Currently blockers get filed as `BELIEF` or `TASK` with no distinct status |
| Recent decisions + rationale + status (final/tentative/superseded) | ✅ `DECISION` (status partly via supersession chain in `KnowledgeUpdater`) | Status needs surfacing, not just the fact |
| Abandoned/rejected approaches + why | 🆕 | Distinct from supersession — "we tried X, dropped it" without a replacement fact necessarily existing |
| Durable constraints ("never do X") | 🆕 (currently folds into `BELIEF` via `MemoryType.RULE`) | Needs to be never-droppable (see §7) |
| Active tasks / in-progress checklist | ✅ `TASK` | |
| Relevant code areas for current focus | 🆕 | No existing concept→file mapping surfaced to context |
| Benchmark/test status | 🆕 | Exists in `benchmarks/results/` but never flows into working context |
| Recent discoveries/findings | ✅ `RESEARCH` (loose fit) | |
| Explicit do-not-do list | 🆕 | Distinct from "abandoned" — this is a standing prohibition, not a dropped idea |
| Open questions | ✅ `OPEN_QUESTION` | |
| Staleness/provenance per field | 🆕 | When was this true, how confident, source ids |

Six genuinely new categories: **blockers, rejected approaches, constraints,
implementation-state, code-areas, do-not-do**. These become new `MemoryRole` values (§5).

---

## 3. The Context Planner

**Its job: decide which of the categories in §2 are required for this query, and at what
scope — not retrieve anything.** Output is a plan consumed by retrieval, not an answer.

### Design: hybrid, deterministic-first, LLM as narrow fallback

This mirrors the project's existing bias (deterministic acceptance stage, deterministic
ranker, LLM only where `QueryRewriter` already uses it narrowly) — reuse that judgment
rather than introducing a new philosophy:

1. **Deterministic surface layer** (always runs, no LLM): cheap pattern/keyword
   classification of the query into a coarse `task_mode` — `CODING, DEBUGGING, PLANNING,
   WRITING, RESEARCH, BENCHMARKING, ARCHITECTURE, CONTINUATION`. `CONTINUATION` is its own
   mode, detected by patterns like "continue", "where were we", "pick up", or a query with
   no specific entities/nouns at all — this is what "Continue implementing Haven" hits.
2. **Ontology-driven layer**: resolve query entities against the existing `Concept` graph
   (`obsidian/ontology`) exactly as retrieval already does. Which concepts/subsystems are
   implicated determines *scope* (single concept vs. project-wide) and contributes to
   `task_mode` confidence (e.g., a query landing on a code-bearing concept nudges toward
   CODING/DEBUGGING).
3. **LLM fallback** (optional, only invoked when steps 1–2 disagree or produce low
   confidence — same `fails-open` contract as `QueryRewriter`): a single structured call
   classifying `task_mode`, `scope`, and which of the §2 categories are required. For
   `CONTINUATION`-mode queries this is nearly always triggered, since by definition scope
   is maximal and ambiguous — this is the one case where "get an LLM's judgment on what's
   needed" earns its cost.

Each `task_mode` maps to a fixed required-category set (deterministic table, editable
config — not learned):

| task_mode | required categories |
|---|---|
| CODING | implementation_state, code_areas, constraints, active_tasks |
| DEBUGGING | recent_discoveries, blockers, code_areas, rejected_approaches |
| PLANNING | goals, open_questions, constraints, decisions |
| WRITING | goals, decisions, constraints |
| RESEARCH | recent_discoveries, open_questions, decisions |
| BENCHMARKING | benchmark_status, recent_discoveries, blockers |
| ARCHITECTURE | architecture_snapshot, decisions, rejected_approaches, constraints |
| CONTINUATION | **all categories** — full reconstruction |

Output type: `ContextPlan { task_mode, scope: {concept_id | PROJECT_WIDE}, required_categories: [...], recency_window, needs_deep_code_context: bool }`.

Why not pure LLM: cost/latency on every query, and it's the one thing in this pipeline
that most benefits from being auditable/debuggable the way `AcceptanceStage`'s decisions
already are (a wrong plan silently produces a wrong context with no trace). Why not pure
deterministic: `CONTINUATION`-mode queries are inherently open-ended in a way no fixed
rule table captures well — this is exactly the ambiguous 10% worth spending one LLM call on.

---

## 4. The complete pipeline

```
Question
  → Intent Understanding
  → Context Planning
  → Retrieval
  → Gap Detection
  → Additional Retrieval
  → Working Context Assembly
  → Validation
  → Final Context
```

**Intent Understanding** — classifies the query itself (not yet "what context is
needed," just "what is being asked"): is this a continuation query, a pointed factual
query, an instruction to act? Lightweight, reuses the same deterministic-first/LLM-fallback
contract as the Context Planner (could in practice be one combined call — kept as a
separate stage here because it answers a logically distinct question: *what is the user
asking* vs. *what state does answering it require*).

**Context Planning** — produces the `ContextPlan` (§3).

**Retrieval** — reuses `HybridCandidateRetriever` + `QueryRewriter` + `DeterministicRanker`
+ `AcceptanceStage` exactly as they exist, but driven per-category from the `ContextPlan`:
one retrieval pass per required category (not one global pass), each parameterized by that
category's typical `MemoryType`(s), with `scope` narrowing or widening the ontology
activation-spreading radius (`ActivationSpreader`) — project-wide scope spreads further,
single-concept scope stays tight. This is additive parameterization of existing
components, not new retrieval logic.

**Gap Detection** — for each required category in the plan, check: did retrieval produce
at least one `AcceptanceStage`-accepted candidate clearing an abstention-style confidence
floor? If not, that category is a **gap**, recorded explicitly (not silently dropped).
Deterministic, same shape as `AcceptanceStage`'s existing abstention check (§6).

**Additional Retrieval** — for each gap, one bounded follow-up pass: widen the ontology
radius, relax the acceptance thresholds for that pass only, or issue a `QueryRewriter`-style
targeted sub-query synthesized from the category itself (e.g., for a `blockers` gap:
"what is currently blocking [scope]"). Capped at one retry per gap — never an unbounded
loop, matching `AcceptanceStage`'s "only shrinks, never iterates" design.

**Working Context Assembly** — extended `WorkingContextBuilder` groups the (now
gap-checked) candidates into the full category set from §2, in `RoleBucket`-per-category
form, ordered by recency within category, with each bucket carrying its own
resolved-or-gap status.

**Validation** — three deterministic checks, not an LLM pass:
  - *Consistency*: no two accepted `DECISION`s on the same concept without one being
    marked superseded (`KnowledgeUpdater`'s existing supersession chain already carries
    this — validation just asserts it's respected in what got assembled).
  - *Staleness*: flag any category whose most-recent accepted item is older than a
    category-specific threshold (e.g., `blockers` older than 30 days gets flagged
    "possibly resolved, unconfirmed" rather than presented as current).
  - *Completeness*: every category in the `ContextPlan` is present in the final context —
    either with content or with an explicit `gap` marker. Never silently absent.

**Final Context** — rendered by an extended `StructuredPromptBuilder`, including an
explicit "Unknown / Not Found" section for surviving gaps. This is the single most
important behavioral change: the system says "I don't have information on X" instead of
proceeding as if X doesn't matter — the same trust-preserving move a human makes at their
own step 6 (§1).

---

## 5. The Working Context object

Extend, don't replace, the existing frozen-dataclass shape (`WorkingContext` /
`WorkingContextState` / `RoleBucket` / `MemoryRole`).

**New `MemoryRole` values**: `BLOCKER, CONSTRAINT, REJECTED_APPROACH, IMPLEMENTATION_STATE,
CODE_AREA, BENCHMARK_STATUS, DO_NOT_DO`. (`CONSTRAINT` should split cleanly out of
`MemoryType.RULE`, which today collapses into `MemoryRole.BELIEF` — rules are exactly the
"never do X" durable-constraint content that deserves its own never-dropped bucket rather
than being mixed in with general beliefs.)

**Object shape** (each field a `RoleBucket`-like structure, plus new cross-cutting metadata):

```
WorkingContext
├── identity            (project/system anchor — one-line "what this is")
├── architecture_snapshot (component → responsibility, semi-static)
├── implementation_state  (component → done/stubbed/in-progress)
├── goals                 (prioritized)
├── blockers               (with reason, staleness-flagged)
├── decisions              (with rationale + status: final/tentative/superseded)
├── rejected_approaches     (with reason — "why we didn't do it this way")
├── constraints             (never-drop — durable rules)
├── active_tasks            (checklist state)
├── code_areas              (file paths relevant to current focus)
├── benchmark_status        (last known pass/fail, per benchmark category)
├── recent_discoveries      (findings, investigation results)
├── open_questions
├── do_not_do
├── gaps                    (explicit list: category → why it's empty)
└── provenance               (per-field: source memory ids, confidence, as-of timestamp)
```

**Evolution — the key architectural change**: today, `WorkingContext` is fully recomputed
from scratch on every query. Working Context 2.0 should instead treat it as a
**materialized, incrementally-maintained view per project/concept**, updated at *write*
time. Extend `ManagerPipeline` with a hook (after `KnowledgeUpdater`) that updates the
relevant persistent `WorkingContextState` incrementally as new facts land — the same way
`CanonicalMatcher` already maintains canonical-fact state incrementally rather than
recomputing from the full history each time. Read-time reconstruction then becomes
**"load the materialized state, run Gap Detection to verify it's still complete, gap-fill
only what's missing"** — cheaper, and structurally identical to the human "recall state,
then reality-check" pattern from §1 step 6, rather than "rebuild everything, every time."
This is the single highest-leverage change for making a 400-turn conversation reconstruct
cheaply and reliably instead of getting slower and shakier the longer the project runs.

---

## 6. Detecting missing context

Three layers, cheapest first, matching `AcceptanceStage`'s abstention philosophy:

1. **Coverage check** (deterministic, always runs): for each `ContextPlan.required_categories`,
   is the corresponding bucket non-empty after Retrieval? Empty → gap.
2. **Confidence check** (deterministic, always runs): even a non-empty bucket is a gap if
   its top candidate doesn't clear a category-specific abstention floor — mirrors
   `AcceptanceStage`'s existing abstention-score gate exactly, applied per-category instead
   of per-query. Prevents "technically found something, but it's noise" from masquerading
   as coverage.
3. **Semantic self-check** (optional LLM, `CONTINUATION`-mode or low-plan-confidence
   queries only, same fails-open contract as `QueryRewriter`): "given this assembled
   context and this question, what's missing to answer confidently?" Catches gaps the
   deterministic checks structurally can't see — e.g., a category has content, but it's
   about the wrong sub-concept.

Requesting another pass: bounded to exactly one Additional Retrieval attempt per detected
gap (§4). If still empty after the retry, the gap is recorded as permanent for this query
and surfaces in the final context's "Unknown / Not Found" section — never retried again
within the same request, and never silently dropped.

---

## 7. Prioritization under a limited token budget

Reuse `DeterministicSlotAllocator`'s existing allocation philosophy, but with a fixed
tier order across categories rather than one flat scoring pass:

**Never removed** (always keep, truncate everything else first): `constraints`,
`do_not_do`, the single highest-priority active `goal`, the most recent unresolved
`blocker`. These are the categories where omission causes actively wrong behavior
(violating a stated rule) rather than merely incomplete behavior.

**Removed first**: `architecture_snapshot` (collapses to one line per component),
superseded `decisions`, resolved `active_tasks`, low-confidence `recent_discoveries`,
verbatim code excerpts in `code_areas` (collapse to file path only, drop excerpt).

**Middle tier, scored normally** (reuse existing importance/confidence/recency scoring
from `DeterministicRanker`): remaining `decisions`, `open_questions`, `rejected_approaches`.

**Degrade, don't delete**: when a category must shrink, collapse to a count-preserving
summary ("3 abandoned approaches, collapsed for space") rather than disappearing —
preserves the signal "there was more here" so the model doesn't mistake budget pressure
for absence of history, which would otherwise reintroduce the exact confabulation risk
Gap Detection (§6) is designed to prevent.

---

## 8. Integration with existing architecture

Reused as-is, no redesign:
- `HybridCandidateRetriever`, `QueryRewriter`, `DeterministicRanker`, `AcceptanceStage`,
  `DeterministicSlotAllocator` — retrieval mechanics unchanged, just invoked per-category
  and parameterized by `ContextPlan.scope`.
- `RoleBucket` / `MemoryRole` pattern — extended with 7 new enum values, not replaced.
- `KnowledgeUpdater`'s supersession chain — reused directly for decision-status and
  Validation's consistency check.
- `StructuredPromptBuilder` — extended to render new buckets and the "Unknown / Not
  Found" gaps section.
- `ManagerPipeline` — extended with one new post-`KnowledgeUpdater` hook for incremental
  `WorkingContextState` maintenance (§5); the five existing stages are untouched.
- `/retrieve_working_context` (`obsidian/server/main.py:1423`) — extended in place
  (new optional `reconstruction: bool` request flag) rather than adding a parallel endpoint.
- `benchmarks/adapters/haven_adapter.py` — extended to exercise the new endpoint mode.

New, additive-only components: `ContextPlanner`, `GapDetector`, the extended
`WorkingContext` dataclass and its new `MemoryRole`s, and the `ManagerPipeline` incremental
hook. Nothing existing is removed or restructured.

---

## 9. Benchmarking reconstruction, not retrieval

New categories under `benchmarks/datasets/`, measuring reconstruction fidelity against a
hand-authored ground-truth state summary via LLM-judge — distinct axis from the existing
retrieval-accuracy categories (contradictions, goals, identity, preferences, etc.):

- **`cold_open_continuation`** — the literal target scenario: synthetic long multi-month
  project, then "continue implementing X." Judge whether the reconstructed context lets
  the model correctly state current goal, blockers, and next step without further
  clarification.
- **`constraint_recall`** — does reconstruction surface constraints/do-not-do items
  unprompted, without the query mentioning them directly?
- **`gap_honesty`** — when ground truth genuinely has no answer for a category, does the
  system say "unknown" rather than confabulate? This is the metric that most directly
  measures whether Gap Detection works — inverse of hallucination rate.
- **`staleness_awareness`** — does reconstruction correctly flag decisions later
  superseded, rather than presenting stale state as current?
- **`budget_degradation`** — under an artificially tight token budget, are the
  never-drop categories (§7) still present and correctly prioritized?

Extend `benchmarks/analysis/classify_failure.py`'s failure taxonomy with reconstruction-specific
types: `GAP_MISSED` (should have flagged unknown, didn't), `STALE_PRESENTED_AS_CURRENT`,
`CONSTRAINT_DROPPED`, `PLAN_WRONG_CATEGORY` (Context Planner picked the wrong task_mode).
`HavenAdapter` needs no structural change — it already drives real
`MemoryEngine`/`VaultWriter` machinery, just needs to call the new reconstruction mode and
compare against a fuller judge rubric.

---

## 10. Implementation roadmap

**Phase 0 — before hackathon, low complexity, high impact.**
Extend `MemoryRole` with the 7 new categories; extend `WorkingContextBuilder` to bucket
into them using signals `ManagerPipeline`'s `Classifier` mostly already produces (blockers
and rejected-approaches may need a small classifier prompt addition); extend
`StructuredPromptBuilder` rendering. Mostly wiring existing data into new buckets — no new
retrieval logic.

**Phase 1 — before hackathon, medium complexity, high demo impact.**
Deterministic-only `ContextPlanner` (task_mode table + ontology-scope, no LLM fallback
yet). Makes context assembly visibly adaptive to query type — a strong, legible hackathon
demo moment on its own.

**Phase 2 — before hackathon if time allows, medium complexity, high impact.**
Deterministic `GapDetector` (coverage + confidence checks only) plus the one-shot
Additional Retrieval pass and the "Unknown / Not Found" rendering. This is the behavior
that most directly answers the brief — the system visibly admitting what it doesn't know
is the most convincing evidence of "genuine reconstruction" for a live demo.

**Phase 3 — after hackathon, higher complexity, high long-term payoff.**
Incremental `WorkingContextState` materialization via the `ManagerPipeline` write-time
hook, replacing full-recompute-per-query. Needed for this to scale gracefully to
hundreds-of-turns projects; not needed to demonstrate the concept at hackathon scale.

**Phase 4 — after hackathon, medium complexity, needs careful eval before enabling.**
LLM fallback in `ContextPlanner` for ambiguous/`CONTINUATION` queries, and the optional
semantic self-check layer in `GapDetector`. Latency/cost tradeoffs and prompt design need
their own evaluation pass before defaulting on.

**Phase 5 — after hackathon.**
New benchmark categories (§9) and `classify_failure.py` taxonomy extensions. Valuable but
not demo-critical; needed to make later phases measurable rather than vibes-based.

**Phase 6 — after hackathon, most complex, exploratory.**
Deeper Validation: semantic contradiction/staleness diffing across supersession chains
beyond the structural check in Phase 0–2's Validation stage. Highest complexity, builds
on `KnowledgeUpdater`'s existing data model rather than requiring new storage.
