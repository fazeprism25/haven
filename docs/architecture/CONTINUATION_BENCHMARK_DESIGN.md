# Continuation Benchmark — Design

Status: **Phase 1 pilot implemented** (§10's Phases 1–3: adapter interface
extension, continuation runner + judge, one pilot category). See
"Phase 1 pilot status" at the end of this document for exactly what
shipped, what deliberately deviates from this design's numbers, and what
remains for Phases 4–5. The design itself (everything above that section)
is unchanged from the frozen version this pilot was built against.

**Ingestion update:** `CONTINUATION_BENCHMARK_AUDIT.md`'s Critical-1 (every
turn ingested as `MemoryType.FACT`, so `<ProjectState>` was always a
structurally empty shell on this benchmark) is now fixed — see
`docs/architecture/CONTINUATION_BENCHMARK_INGESTION_DESIGN.md` ("design
only" when Critical-1 was originally found, now **implemented**) for the
resolved design and its "Implementation status" section for what shipped.
`run_continuation_benchmarks.py`'s `--adapter haven` now ingests through
`HavenContinuationAdapter` (deterministic, `turn_type`-driven typed
ingestion, no LLM call) instead of plain `HavenAdapter`. §8's worked
example below — "whether `<Decisions>` correctly shows only the
blended-score decision... and not the rejected embedding-only one" — is now
genuinely observable for the first time; it was not before this fix, for
exactly the reason Critical-1 documents.
Grounded against `benchmarks/README.md`, `benchmarks/RUNNER_SPEC.md`,
`benchmarks/adapters/base.py`, `benchmarks/adapters/haven_adapter.py`,
representative dataset files (`decision_reconstruction/basic_001.json`,
`contradictions/basic_001.json`), and the three architecture evaluations
this design is meant to give a *behavioral* test for:
`PROJECT_STATE_EVALUATION.md`, `PROMPT_CONTINUATION_EVALUATION.md`,
`PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md`.

---

## 0. The one fact that shapes this whole design

`benchmarks/RUNNER_SPEC.md`'s existing execution flow is:

```
insert conversation (infer=False, verbatim) → adapter.search(query) →
join every result["memory"] string → LLM-judge the joined string against
answer_contains / must_not_contain
```

There is no generation step. The judge scores **the retrieved set itself**,
concatenated. This is fine — correct, even — for every existing category:
`decisions`, `supersession`, `contradictions`, `decision_reconstruction`,
etc. all ask "did the system surface the right fact(s)," and a joined list
of raw facts is a legitimate answer to that question.

It is not a legitimate way to answer this task's question. "Continue
implementing Haven" is not answered by a list of facts — it is answered by
an *action*, produced by reasoning over facts that disagree with each other
(a rejected approach and the decision that rejected it are both, individually,
real retrievable memories). A benchmark that joins Haven's retrieved
candidates into a string and judges the string is measuring the same thing
`decision_reconstruction` already measures, just with a longer conversation.
`PROMPT_CONTINUATION_EVALUATION.md` §9 already names the object this
benchmark needs to evaluate — not `ContextBuilder`'s flat join, but the
*rendered, structured prompt* (`Guidance` → `ProjectState` → `WorkingContext`)
that `query_structured()` already produces and that a downstream model would
actually read. Judging the retrieved set can't see the difference between a
system that hands a downstream model an oriented, tiered reconstruction and
one that hands it the same facts in a random pile — both retrieved the same
memories. Only judging what a downstream model *does* with what it was
handed can see that difference. That is the whole reason this benchmark
needs a second pipeline stage the existing runner doesn't have, not just a
new dataset directory.

Everything below follows from that.

---

## 1. Benchmark philosophy

**What this benchmark measures:** given a long, messy, realistic
project history — architecture debate, a rejected approach, an adopted one,
implementation, a debugging detour, resolved blockers, open blockers,
constraints stated once and never repeated, a roadmap — can a **brand-new AI
conversation**, given only what the memory system reconstructs (not the raw
history), produce a continuation response a returning engineer would
recognize as correct: the right next action, no re-litigating settled
questions, no violating a stated constraint, no treating a resolved blocker
as live.

**What it explicitly does not measure:** whether the right facts were
*retrievable* somewhere in the system. That is `decision_reconstruction`'s
job, and it already does it well. This benchmark starts from the assumption
that retrieval mostly works and asks the harder question sitting on top of
it: does *reconstruction* work — the synthesis, tiering, and staleness
resolution that turns a pile of retrieved facts into something a fresh model
can act on correctly, without re-deriving the whole conversation's judgment
calls from scratch.

**Design consequence:** the benchmark is judged on a **generated
continuation response**, not on the retrieved/reconstructed context
directly. The context is an intermediate artifact, inspectable for
diagnostics, but never the scored object. This is the single structural
choice that makes flat retrieval unable to score highly (elaborated in §10).

---

## 2. Answers to the ten questions

**1. What should one benchmark case contain?**
See §4 for the full schema. In short: a long multi-topic conversation with
per-turn role tags (not just `speaker`/`text`); a structured `ground_truth`
object describing project state *as of the query turn* (current objective,
active decisions, rejected/superseded decisions, active blockers, resolved
blockers, constraints, active tasks, completed tasks, open questions,
top-priority next action); one or more continuation queries; and a rubric
(`must_state` / `must_not_state` / `must_prioritize` / `forbidden_actions`)
derived mechanically from `ground_truth`, not authored independently of it —
so the rubric can never drift from the state it's supposed to check.

**2. How many conversation turns?**
Three tiers, all larger than every existing category except
`decision_reconstruction`'s upper end, because a continuation case must
contain enough *distinct kinds* of content (architecture, rejection,
implementation, debugging, blockers, constraints, roadmap) to be a real
project, not just a long thread about one axis:
- **short**: 40–60 turns (one milestone, single-thread)
- **medium**: 60–100 turns (one milestone, 2–3 interleaved concerns)
- **long**: 100–180 turns (2–3 milestones, staleness-heavy)
Tiers are a first-class dataset dimension (§4), not a difficulty label
bolted on after the fact — they let scaling degradation be measured
directly, which is the concrete, open risk `PROJECT_STATE_EVALUATION.md` §7
and `PROMPT_CONTINUATION_EVALUATION.md` §8 both flag (flat top-K crowding
out low-volume, high-value categories as a project grows).

**3. Should conversations branch?**
No, structurally — the runner ingests turns as one linear sequence
(`add_conversation`, `benchmarks/adapters/base.py:105-137`), and there is no
value in fighting that. But **content-level branching is required, not
optional**: a path attempted, abandoned, and explicitly superseded by a
different path is exactly the shape `supersession`/`contradictions` already
use, and it is the single most important content pattern this benchmark
needs (§10a). "Branching" here means "the conversation *talks about* two
paths and resolves to one," encoded linearly — never two divergent turn
sequences.

**4. Should there be multiple milestones?**
Yes, for the `long` tier and the dedicated `resume_after_milestone` category
(§6). A milestone boundary is a turn that bulk-resolves a cluster of
blockers/tasks ("Phase 1 shipped — the retrieval pipeline is done"). This is
the cleanest test of whether a system tracks *current phase* rather than
surfacing Phase-1-era open questions as if they're still open — precisely
the `identity`/`phase` gap `PROJECT_STATE_EVALUATION.md` §1(c)/§5 documents
as unimplemented in `ProjectState` today. The benchmark should measure this
gap, not require Haven to already have closed it.

**5. Should memories become stale?**
Yes, deliberately and heavily — this is the mechanism that makes the
benchmark measure reconstruction rather than recall (§10). Every case must
contain at least one of: a resolved blocker, a completed task, a superseded
decision, or a rejected approach, each still present verbatim in the
conversation and each lexically close to the continuation query. A system
that can't distinguish "true then" from "true now" fails this benchmark even
if it retrieves every relevant fact perfectly.

**6. How should success be judged?**
A three-stage pipeline, not a single LLM-judge call (§5):
**(A) Reconstruction** — the adapter builds whatever context object it would
actually hand a downstream model (Haven: `query_structured()`'s rendered
XML; mem0/baselines: the existing flat joined string). **(B) Continuation**
— a fixed, temperature-0 "continuation model" that has never seen the raw
conversation receives only that context plus the query, and produces a
response. **(C) Judging** — the LLM judge scores stage B's *response*
against the case's rubric, never the stage-A context directly. Stage A
output is retained for diagnostics (§8's optional retrieval-quality
sub-score) but is not the scored artifact.

**7. Which information must always be reconstructed?**
The hard-required rubric tier (§5's `must_not_state`/`forbidden_actions`,
weighted to dominate the score, §7): current (non-superseded) decision
status; active blockers; stated constraints, never violated by the proposed
next action; and — critically — **absence** of rejected approaches, resolved
blockers, or superseded decisions presented as current. These map directly
to the fields `PROJECT_STATE_EVALUATION.md` §2 already found to be
`MEMORY_DIRECT`/`DETERMINISTIC`, never-inferred, and safest to trust — this
benchmark is, among other things, a check that safety actually survives
being handed to a downstream model.

**8. Which information is optional?**
Full `implementation_state`/`code_areas` detail, the complete decision
history (as opposed to current status), debugging blow-by-blow, and any
`open_questions` beyond the top one or two — present in `ground_truth` for
completeness and available to the judge as partial credit, but never
required for a passing score. `identity`/`phase` are scored as **bonus**,
not required — `PROJECT_STATE_EVALUATION.md` §1(c) already establishes
these fields don't exist in Haven's `ProjectState` today; requiring them
would make every case fail for a reason that has nothing to do with
reconstruction quality and everything to do with a known, separately-scoped
gap.

**9. Which benchmark categories should exist?**
Eight, detailed in §6: `resume_coding`, `resume_debugging`,
`resume_research`, `resume_benchmarking`, `resume_architecture`,
`resume_documentation`, `resume_after_milestone`, `resume_stale`.

**10. How should the benchmark avoid rewarding simple retrieval?**
Five independent mechanisms, detailed in §10 — no single one is load-bearing
alone: (a) judging a generated response, never the retrieved context itself;
(b) a fixed context budget that punishes systems with no never-drop tiering;
(c) similarity-trap distractors (a rejected/superseded fact made lexically
*closer* to the query than the current answer); (d) a `must_prioritize` check
on the response's primary recommended action, not just fact presence; (e) a
hard-fail scoring tier for stating anything in `must_not_state` — a system
that dumps everything it retrieved gets penalized precisely because it
retrieved correctly but reconstructed nothing.

---

## 3. Why this needs a second pipeline, concretely

Reusing `run_benchmarks.py` unmodified is not an option, but reusing almost
everything *around* it is. Concretely, against `BaseAdapter`
(`benchmarks/adapters/base.py`):

- `from_config`, `delete_all`, `add`/`add_conversation` — reused exactly as
  they exist today. Ingestion is not what's different about this benchmark.
- `search()` — **not called** by the continuation runner. It returns a flat
  `{"results": [...]}` shape that has already discarded the one thing this
  benchmark needs: structure/tiering/orientation. Calling it and joining the
  results would silently degrade every adapter to the "flat list" condition
  this benchmark exists to distinguish from.
- **New, additive method**: `build_continuation_context(query: str) -> str`,
  with a default implementation on `BaseAdapter` that falls back to today's
  `search()` + join (so any adapter that doesn't override it still runs,
  just as the "flat retrieval" condition it should score poorly under —
  useful as a baseline, not a bug). `HavenAdapter` overrides it to call
  `MemoryEngine.query_structured(query)` — already-existing, unmodified,
  public API (`engine.py:1213`) — and returns the rendered XML verbatim,
  including `<Guidance>`, `<ProjectState>` (when the query classifies as
  `TaskMode.CONTINUATION`), and `<WorkingContext>` exactly as a real
  downstream conversation would receive it, opaque UUID titles and all. The
  benchmark must see Haven's real output, weaknesses included — it is not
  the benchmark's job to pre-clean what the architecture evaluations already
  documented as rough edges.

This is the only interface change this design proposes. Everything else —
dataset format, judge, scoring, runner shell — is new code alongside the
existing suite, not a modification to it.

---

## 4. Dataset format

Extends the existing required fields (`benchmark_id`, `category`,
`conversation`, `query`, `expected`) additively — a continuation case is a
superset, not a divergent shape.

```json
{
  "benchmark_id": "resume_coding_basic_001",
  "category": "resume_coding",
  "tier": "medium",
  "domain": "haven_retrieval_pipeline",

  "conversation": [
    {
      "turn_index": 0,
      "speaker": "user",
      "text": "We need a ranking stage before the slot allocator — right now candidates go straight from retrieval to allocation with no scoring.",
      "turn_type": "architecture_discussion"
    },
    {
      "turn_index": 1,
      "speaker": "user",
      "text": "I was thinking we rank by embedding similarity alone, simplest thing that could work.",
      "turn_type": "rejected_approach"
    },
    {
      "turn_index": 2,
      "speaker": "user",
      "text": "Embedding-only ranking won't separate a stale fact from a fresh one with similar wording though — we need recency and confirmation count in the score too.",
      "turn_type": "decision",
      "supersedes_turn": 1
    },
    {
      "turn_index": 3,
      "speaker": "user",
      "text": "My upstairs neighbor is having new flooring installed, it's been noisy all week.",
      "turn_type": "distractor"
    },
    {
      "turn_index": 4,
      "speaker": "user",
      "text": "Rule: never let a single low-confidence memory override a repeated, confirmed one, regardless of recency.",
      "turn_type": "constraint"
    },
    {
      "turn_index": 5,
      "speaker": "user",
      "text": "Blocked on the ranker until we settle whether score_breakdown needs to be exposed outside DeterministicRanker for debugging.",
      "turn_type": "blocker"
    },
    {
      "turn_index": 6,
      "speaker": "user",
      "text": "Decided: score_breakdown stays internal to DeterministicRanker, only final_score crosses the boundary. That unblocks the ranker work.",
      "turn_type": "decision",
      "resolves_turn": 5
    },
    { "...": "... 40-100 more turns spanning implementation, a debugging detour, more distractors, an open question, a roadmap item ..." }
  ],

  "ground_truth": {
    "current_objective": "Implement DeterministicRanker's scoring stage between retrieval and slot allocation.",
    "active_decisions": [
      "Rank by a blended score (recency + confirmation count + embedding similarity), not embedding similarity alone.",
      "score_breakdown stays internal to DeterministicRanker; only final_score is exposed externally."
    ],
    "superseded_or_rejected": [
      "Ranking by embedding similarity alone (rejected at turn 1, superseded by turn 2)."
    ],
    "constraints": [
      "Never let a single low-confidence memory override a repeated, confirmed one, regardless of recency."
    ],
    "active_blockers": [],
    "resolved_blockers": [
      "score_breakdown visibility question (blocked at turn 5, resolved at turn 6)."
    ],
    "active_tasks": [
      "Finish wiring DeterministicRanker's output into the slot allocator."
    ],
    "open_questions": [
      "Should recency decay be linear or exponential?"
    ],
    "top_priority_next_action": "Continue implementing DeterministicRanker's blended scoring function and wire it into the slot allocator."
  },

  "queries": [
    { "text": "Continue implementing Haven.", "task_mode_hint": "continuation" },
    { "text": "What should we work on next?", "task_mode_hint": "ambiguous" },
    { "text": "Where were we?", "task_mode_hint": "continuation" }
  ],

  "expected": {
    "must_state": [
      "the ranker uses a blended score (recency + confirmations + similarity), not similarity alone",
      "score_breakdown is internal-only"
    ],
    "must_not_state": [
      "ranking by embedding similarity alone, as a current or recommended approach",
      "the score_breakdown-visibility blocker, as still open"
    ],
    "forbidden_actions": [
      "recommending an approach that lets a single low-confidence memory override a confirmed one"
    ],
    "must_prioritize": [
      "wiring the ranker's output into the slot allocator should be the primary next action, not the open recency-decay question"
    ]
  }
}
```

**Field notes:**
- `turn_type` is authored metadata for dataset construction and rubric
  derivation — it is never given to the adapter under test. The adapter
  only ever sees `speaker`/`text`, exactly as today.
- `supersedes_turn` / `resolves_turn` make staleness relationships explicit
  and machine-checkable (a case-validation script can assert every
  `superseded_or_rejected`/`resolved_blockers` entry in `ground_truth`
  traces back to a real `supersedes_turn`/`resolves_turn` pointer — no
  hand-wavy staleness).
- `ground_truth` is the single source of truth; `expected`'s rubric fields
  are mechanically derivable from it (a build script, not hand-authored
  duplication) so the two can never silently diverge the way a hand-written
  rubric could.
- `queries` is a list, not a single string, specifically to test robustness
  across `ContextPlanner`'s five-way lexical `TaskMode` classification —
  `PROMPT_CONTINUATION_EVALUATION.md` §7 already demonstrated that "What
  should I do next?" — arguably the most orientation-seeking phrasing
  possible — currently falls through to `POINTED_QA` and gets no
  `ProjectState` at all. Each query in the list is scored independently
  against the *same* `ground_truth`/`expected`, which turns that finding
  into a reproducible, per-adapter, per-phrasing benchmark number instead of
  a one-off code-reading observation.
- `tier` (`short`/`medium`/`long`) is required, `domain` free-text for
  reporting/grouping, same convention `decision_reconstruction` already uses.

---

## 5. Judging methodology

```
                 ┌─────────────────────────────────────────────┐
Stage A          │  adapter.add_conversation(conversation)      │
(Reconstruction) │  adapter.build_continuation_context(query)   │  → context: str
                 └─────────────────────────────────────────────┘
                                     │
                                     ▼
                 ┌─────────────────────────────────────────────┐
Stage B          │  fixed continuation model, temperature=0     │
(Continuation)   │  system prompt: "You are resuming work on    │
                 │  this project. Below is what you know about  │
                 │  it. Nothing else." + context + query         │  → response: str
                 └─────────────────────────────────────────────┘
                                     │
                                     ▼
                 ┌─────────────────────────────────────────────┐
Stage C          │  LLM judge, temperature=0                    │
(Judging)        │  scores response against ground_truth/       │
                 │  expected → structured rubric result          │
                 └─────────────────────────────────────────────┘
```

**Stage B — why a separate continuation model, not the judge itself
generating and scoring in one pass:** conflating generation and judgment
in a single call lets the judge "help" the response along by already
knowing the rubric while generating it, which would make every adapter's
score converge toward the judge's own reconstruction ability rather than
the adapter's. Stage B must be a clean generation: same fixed model and
prompt template across every adapter and every case, its *only* variable
input is stage A's context, so any score difference between adapters is
attributable to what stage A handed it, not to prompt engineering per
adapter. Reuses the same model family as `benchmarks/judges/llm_judge.py`
already depends on (Qwen Cloud via `QWEN_API_KEY`) for infrastructure
consistency, at `temperature=0` for reproducibility, mirroring the existing
judge's own settings (`RUNNER_SPEC.md`'s noted caveat about live-LLM
variance near pass-rate boundaries applies here too, and should be reported
the same way — not treated as newly introduced by this benchmark).

**Stage B's system prompt is deliberately minimal and adapter-agnostic** —
it must not coach the continuation model toward extracting structure from
messy input (that would let a bad stage-A context be rescued by a good
stage-B prompt, defeating the point). One fixed template, e.g.:

```
You are an AI assistant resuming work on an ongoing software project.
You have no memory of this project beyond what is provided below.
Do not assume anything not stated or clearly implied by the provided context.

<context>
{stage_a_context}
</context>

{query}
```

**Stage C — rubric-based judging, not `answer_contains` substring-style
semantic matching.** The existing judge (`judge_answer()`,
`RUNNER_SPEC.md`) is well-suited to fact-presence questions
(`answer_contains`/`must_not_contain`) but has no notion of *priority* or
*forbidden action* — both required here (§2, Q7/Q10). This benchmark needs
a new judge, `benchmarks/judges/continuation_judge.py`, with a richer
contract:

```json
{
  "must_state_score": 0.0-1.0,
  "must_not_state_violations": ["..."],
  "forbidden_action_violations": ["..."],
  "prioritization_correct": true/false,
  "coherence_score": 0.0-1.0,
  "reason": "...",
  "failure_type": "NONE|STALE_STATE_SURFACED|REJECTED_APPROACH_REVIVED|CONSTRAINT_VIOLATED|BLOCKER_IGNORED|MISPRIORITIZED|INCOMPLETE|JUDGE_ERROR"
}
```

`must_not_state_violations`/`forbidden_action_violations` being non-empty
is a **hard fail** regardless of `must_state_score` (§7's scoring — this is
the mechanism, not just a policy statement). The judge is given
`ground_truth` in full (not just `expected`) so it can reason about *why* a
statement is stale or forbidden, not just pattern-match against a fixed
string list — mirroring how the existing judge already does semantic, not
literal, matching.

---

## 6. Scoring

Per-query score (one query from a case's `queries` list, against the shared
`ground_truth`):

| Component | Weight | Notes |
|---|---|---|
| `must_state` coverage | 40% | Semantic presence of every required fact, judged, not substring-matched. |
| `must_not_state` / `forbidden_actions` | 35%, **hard-fail gated** | Any violation caps the whole per-query score at a fixed low ceiling (proposed: 0.2) regardless of how well `must_state` scored — a continuation that recommends a rejected approach is actively harmful, not merely incomplete, and must not be averaged away by otherwise-good recall. |
| `must_prioritize` | 15% | Does the response's *primary* recommended action match `ground_truth.top_priority_next_action`, not just mention it somewhere. |
| `coherence` | 10% | Judge's holistic read: does this read like a competent engineer's own resumption, not a fact list with a sentence template wrapped around it. Lowest weight — a mechanism for catching the "technically correct facts, unusable response" failure mode without letting prose quality dominate the score. |

**Case score** = mean of per-query scores across `queries` (a case with 3
phrasings and one bad response to "What should we work on next?" surfaces a
classification-routing weakness specifically, rather than being hidden by
averaging against two well-routed phrasings scoring near 1.0 — report both
the mean and the per-query-phrasing breakdown, never the mean alone).

**Category/suite score** = mean of case scores, reported alongside the
existing suite's `passed`/`answer_score` conventions for continuity, plus
one continuation-specific number the existing suite has no equivalent
for: **hard-fail rate** — the fraction of (case, query) pairs that hit the
`must_not_state`/`forbidden_actions` ceiling. This is arguably the single
most important number this benchmark produces, since it is a direct,
reproducible measurement of exactly the failure mode
`PROJECT_STATE_EVALUATION.md`/`PROMPT_CONTINUATION_EVALUATION.md` predict
from architecture reading alone (stale/rejected content surfacing as
current) rather than observing empirically.

**Diagnostic-only, not part of the headline score:** a `retrieval_coverage`
sub-score comparing stage-A's context against `ground_truth` directly (did
the *context* contain the needed facts at all, independent of what stage B
did with them). This is what lets `benchmarks/analysis/classify_failure.py`
-style tooling later distinguish "stage A failed to surface it" from "stage
A surfaced it fine, stage B/the reconstruction shape made it unusable" —
exactly the retrieval-vs-reconstruction distinction this whole benchmark
exists to draw, made inspectable rather than only inferable from the final
score.

---

## 7. Categories

Eight categories, each stressing a different resumption shape. All reuse
the shared schema, runner, and judge — only `conversation` content and
`ground_truth` shape differ, matching how the existing suite's 11 categories
already share one pipeline.

1. **`resume_coding`** — mid-implementation of a specific component.
   Ground truth centers on `active_tasks`/`current_objective`; the trap is
   an earlier, rejected implementation approach lexically similar to the
   adopted one (see the worked example in §4).
2. **`resume_debugging`** — mid-investigation of a bug. Several
   already-tried, already-ruled-out hypotheses appear as real turns; the
   continuation must not re-propose a ruled-out fix and must correctly
   state the *current* leading hypothesis, not the first one mentioned.
3. **`resume_research`** — a comparison/tradeoff conversation (structurally
   close to `decision_reconstruction`'s existing shape, e.g. the DB-choice
   example in `decision_reconstruction/basic_001.json`) followed by "what
   should we do next" — the continuation must not re-litigate an
   already-decided tradeoff as if it were still open.
4. **`resume_benchmarking`** — meta, deliberately: benchmark design/
   methodology work paused mid-way (decisions about metrics, rejected
   scoring approaches, open questions about dataset size) — exercises the
   same shape this very design document's own creation would produce, and
   is the category most likely to catch a system re-suggesting an approach
   this document itself already rejected.
5. **`resume_architecture`** — system-design discussion with an explicitly
   rejected design and an adopted one (mirrors this repo's own
   `PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md` §3.2's "option (a) now,
   option (b) later" pattern) — the continuation must not propose the
   rejected design as new.
6. **`resume_documentation`** — a docs/write-up in progress with decisions
   already made about scope, voice, and structure; the continuation must
   continue consistent with those decisions, not restate or contradict them.
7. **`resume_after_milestone`** — `long`-tier only, 2–3 milestone
   boundaries. Ground truth requires correctly identifying *current* phase
   and treating prior-milestone tasks/blockers as resolved, not open. The
   dedicated test for the `identity`/`phase` gap named in
   `PROJECT_STATE_EVALUATION.md` §1(c)/§5 — expected to score worst of all
   eight categories against Haven's current, phase-blind `ProjectState`, and
   that expected low score is itself the useful signal, not a bug in the
   benchmark.
8. **`resume_stale`** — `long`-tier, staleness density maximized: multiple
   resolved blockers, multiple superseded decisions, at least one rejected
   approach, deliberately outnumbering the still-current facts. The
   stress-test category — if a system passes every other category but fails
   `resume_stale`, that's a specific, actionable finding (staleness
   resolution breaks under volume), not a vague "benchmark is hard."

---

## 8. Worked example — end to end

Using the case sketched in §4 (`resume_coding_basic_001`):

1. **Ingest.** All ~50 turns loaded verbatim via `add_conversation`, exactly
   as today — including turn 1 (the rejected embedding-only approach) and
   turn 5 (the now-resolved blocker).
2. **Reconstruct (Stage A).** `HavenAdapter.build_continuation_context("Continue implementing Haven.")`
   calls `query_structured()`. Per `PROMPT_CONTINUATION_EVALUATION.md` §7,
   this phrasing lexically matches `TaskMode.CONTINUATION`, so the returned
   XML includes a real `<ProjectState>` block. Whether that block's
   `<Decisions>` correctly shows only the blended-score decision (turn 2)
   and not the rejected embedding-only one (turn 1) as current — and
   whether `<Blockers>` correctly omits the resolved score_breakdown
   blocker (turn 5, resolved turn 6) — is exactly what Haven's real,
   unmodified `ProjectStateBuilder` determines. The benchmark does not
   simulate this; it observes it.
3. **Continue (Stage B).** The fixed continuation model receives only that
   XML plus "Continue implementing Haven." and produces a response, with no
   access to the 50 raw turns.
4. **Judge (Stage C).** The judge checks the response against `expected`:
   does it state the blended-score approach as current (`must_state`); does
   it avoid recommending embedding-only ranking or citing the
   score_breakdown question as still blocking (`must_not_state`); is
   wiring the ranker into the slot allocator — not the open recency-decay
   question — presented as the primary next step (`must_prioritize`).
5. **Compare adapters.** Run the same case through `mem0`, `return_all`,
   `recency`, `bm25`, `embedding`, and Haven's ablations
   (`haven_no_ontology`, etc.) via their fallback
   `build_continuation_context` (flat retrieval → join). The prediction
   this benchmark exists to test: baselines that retrieve both turn 1 and
   turn 2 (both are real, relevant, lexically similar memories — a
   similarity-based retriever has no reason to suppress turn 1) hand the
   continuation model a context where both appear with no signal about
   which is current, and the continuation model — reasoning over an
   undifferentiated pile — has a real, measurable chance of reviving the
   rejected approach. Haven's structured `<ProjectState>`, when the
   `Decisions`/`SupersededDecisions` split works as `PROJECT_STATE_EVALUATION.md`
   §2 describes it (`DETERMINISTIC`, a pure projection of
   `DecisionMetadata.status`), should not have this failure mode for this
   specific case shape — an empirical claim this benchmark is built to
   confirm or refute, not assume.

---

## 9. How this avoids rewarding flat retrieval

Restating §2 Q10 with the mechanism each point relies on:

| Mechanism | What it defeats |
|---|---|
| **(a) Score the generated response, never the retrieved context.** | Any adapter whose only skill is "surface the right raw facts" gets no credit for that alone — it must also *not* hand the continuation model something that causes a wrong response, which raw retrieval has no way to guarantee. |
| **(b) Fixed context budget on stage A's input to stage B.** | An adapter with no never-drop/tiering logic (every trivial baseline, `README.md`'s `return_all`/`recency`/`bm25`/`embedding`) will truncate under the same budget pressure `DeterministicSlotAllocator` already imposes on Haven — except without Haven's tiering, truncation is as likely to drop a `constraint` as a distractor. |
| **(c) Similarity-trap distractors.** | Every case places at least one rejected/superseded fact lexically *closer* to the query than the current answer (§4's turn 1 vs. turn 2) — inverts `README.md`'s existing `recency` "acid test" logic (there, recency alone should already win; here, recency *and* status resolution both need to work, or the trap succeeds). |
| **(d) `must_prioritize` on the primary recommended action.** | A flat dump gives a continuation model no signal about which retrieved fact matters most *right now* — its response's ordering will track recency-of-mention or verbosity, not project priority. Checking the *primary* recommendation, not "is it mentioned anywhere," is what makes this catch mis-prioritization instead of rewarding a response that lists everything. |
| **(e) Hard-fail ceiling on `must_not_state`/`forbidden_actions`.** | Directly punishes the dump-everything strategy: a system that hands the continuation model every retrieved fact, current and stale alike, makes it *more* likely (not less) that a stale fact leaks into the response, because nothing in an undifferentiated dump tells the continuation model to suppress it. |

No single mechanism above is sufficient alone — a sufficiently lucky flat
retriever could pass (b) without (c), or dodge (c) without a `long`-tier
case's volume triggering (b). They are meant to compound, the same way the
existing suite's baselines/ablations/distractor-sweep already compound to
make a Haven pass rate *interpretable* rather than just reported
(`README.md`'s own framing). The distractor sweep infrastructure
(`run_distractor.py`) should be run against this category directly once
implemented — a pass-rate-vs-distractor-count curve is the most direct
empirical version of the claim this whole design makes.

---

## 10. Implementation roadmap

Sequenced so each phase is independently reviewable and nothing downstream
is built on an unvalidated assumption from an earlier phase.

**Phase 0 — this document.** Frozen for review before any dataset,
runner, or judge code is written. No implementation in this phase.

**Phase 1 — adapter interface extension.**
Add `build_continuation_context(query: str) -> str` to `BaseAdapter`
(`benchmarks/adapters/base.py`), non-abstract with a default fallback to
today's `search()` + join — additive, byte-identical behavior for every
existing category's runner path, since `run_benchmarks.py` never calls the
new method. Override it in `HavenAdapter` to call `query_structured()`
(already exists, unmodified). Override it in the mem0/baseline adapters
only if their existing `search()` output isn't already the right shape (for
most, the fallback is correct as-is — a flat retriever's "continuation
context" *is* its flat retrieval, honestly).

**Phase 2 — continuation runner + judge.**
New `benchmarks/runners/run_continuation_benchmarks.py` implementing
stages A/B/C (§5). New `benchmarks/judges/continuation_judge.py` with the
rubric contract in §5, reusing the existing `QWEN_API_KEY`/temperature=0
infrastructure convention. New result schema extending
`RUNNER_SPEC.md`'s "Output Schema" additively (per-query breakdown, hard-fail
rate, per the metrics in §6) — a separate `results_continuation_<adapter>.json`
file, mirroring how `run_distractor.py` already writes to a filename outside
the standard `results*.json` glob specifically so schemas never collide.

**Phase 3 — one pilot category, small.**
Author `resume_coding` only, ~10 cases across all three tiers, by hand,
including the mechanical `ground_truth` → `expected` rubric derivation
script (§4). Run it against Haven and at least one trivial baseline
(`recency` or `embedding`) before authoring anything else — this is the
checkpoint that validates the whole pipeline (does stage B actually produce
distinguishable responses across adapters; does the judge's hard-fail gating
behave as intended; is the fixed context budget in §10(b) set to a value
that actually produces truncation pressure) before investing in the
remaining ~150-190 cases.

**Phase 4 — remaining categories.**
Author the other seven categories (§7) once Phase 3's pipeline is validated,
~15–25 cases each depending on category (fewer for `resume_after_milestone`/
`resume_stale`, which are `long`-tier and expensive to author well; more for
`resume_coding`/`resume_debugging`, which are cheaper to produce variety
in) — target ~150–200 total cases, comparable in scale to the existing
suite's `supersession` (55) + `decision_reconstruction` (26) + `refinements`
(30) combined.

**Phase 5 — comparison infrastructure.**
Wire the new category into the existing baseline/ablation/distractor-sweep
machinery (`README.md`'s "Baselines, ablations, and robustness") — same
adapters, same `classify_failure.py`-style reporting, plus the new
hard-fail-rate metric (§6) as a first-class column. Produce a
`results/final_report_continuation.md` in the same spirit as the existing
`results/final_report.md`, once real numbers exist to report.

**Explicitly out of scope for this roadmap:** any change to
`ProjectState`/`WorkingContext`/`StructuredPromptBuilder` themselves. This
benchmark is a measurement instrument for the architecture as it already
exists (weaknesses included, per §3's "opaque UUID titles and all"), not a
vehicle for shipping the fixes those evaluations already recommended. Once
real numbers exist (Phase 5), they should inform which of
`PROJECT_STATE_EVALUATION.md` §9's ranked recommendations to prioritize next
— but that prioritization is future work this document deliberately does
not do.

---

## 11. Phase 1 pilot status

Covers §10's Phases 1–3 together (adapter interface extension, runner +
judge, one pilot category) — implemented as a single additive pilot rather
than three separate reviewable drops, per the scope given for this pass.
Phases 4 and 5 are **not** implemented. `ProjectState`/`WorkingContext`/
`StructuredPromptBuilder` were not touched, per this document's own
explicit exclusion above.

**Files added/changed:**
- `benchmarks/adapters/base.py` — added `build_continuation_context(query) -> str`,
  non-abstract, default falls back to `search()` + the existing flat join.
  Purely additive: `run_benchmarks.py` never calls it, so every existing
  category's results are byte-identical to before this method existed
  (verified — the full existing `benchmarks/tests/` suite, 195 tests, still
  passes unchanged).
- `benchmarks/adapters/haven_adapter.py` — `HavenAdapter.build_continuation_context`
  override calling `MemoryEngine.query_structured()`, exactly as §3
  specifies. `benchmarks/adapters/ablations.py` (subclasses `HavenAdapter`)
  and `benchmarks/adapters/baselines.py` (subclasses `BaseAdapter` directly)
  needed no changes at all — they inherit the real override or the flat
  fallback respectively, for free.
- `benchmarks/judges/continuation_judge.py` — new. Implements §5's Stage C
  contract (`must_state_score`, `must_not_state_violations`,
  `forbidden_action_violations`, `prioritization_correct`,
  `coherence_score`, `failure_type`). Reuses `llm_judge.py`'s Qwen Cloud
  client/model-resolution helpers rather than duplicating them. §6's
  weighting formula (40/35/15/10, hard-fail ceiling at 0.2) is applied
  deterministically in Python (`_weighted_score`), not left to the LLM to
  compute, so it can't drift between calls.
- `benchmarks/runners/run_continuation_benchmarks.py` — new. Implements
  Stage A (`adapter.build_continuation_context`), Stage B (a fixed,
  temperature-0 Qwen call using §5's exact system prompt template), and
  Stage C (the new judge). A fully separate module from
  `run_benchmarks.py` — no shared execution path, no import of one by the
  other except reusing `run_benchmarks.get_adapter_cls` for adapter-name
  resolution. Writes `benchmarks/results/results_continuation_<adapter>.json`.
- `benchmarks/datasets_continuation/resume_coding/*.json` — 10 pilot cases
  (see below).
- Tests: `benchmarks/tests/test_base_adapter.py` and `test_haven_adapter.py`
  gained new test classes for `build_continuation_context`;
  `benchmarks/tests/test_continuation_judge.py` and
  `test_run_continuation_benchmarks.py` are new.

**Deviations from this document's numbers (deliberate, pilot-scope):**
- **Turn counts are compressed.** §2 Q2 specifies 40–180 turns per tier;
  the 10 pilot cases run 25–28 turns each (∼10 signal turns —
  architecture discussion, one rejected-then-superseded approach, a
  constraint, a blocker and its resolution, implementation turns, an open
  question, a next task — interleaved with mundane distractor turns from a
  shared pool). This validates the full pipeline shape (staleness traps,
  mechanical rubric derivation, hard-fail gating) at a fraction of the
  authoring cost; scaling to the design's real turn-count ranges is
  Phase 4 work, not redone here.
- **Two queries per case, not three.** Every case's `queries` list is
  `["Continue implementing the project.", "What should we work on
  next?"]` — the two phrasings this task's own instructions named — rather
  than §4's three-phrasing example. A third phrasing (e.g. "Where were
  we?") is easy to add per-case later without touching any pipeline code.
- **Dataset root is `benchmarks/datasets_continuation/`, not
  `benchmarks/datasets/resume_coding/`.** Not specified either way by this
  design. Chosen so this schema (`queries`/`ground_truth` instead of a
  single `query` string) can never be discovered by
  `run_benchmarks.discover_dataset_dirs()` at all — belt-and-suspenders on
  top of the fact that the existing loader would skip a continuation case
  anyway (missing the required `query` field) rather than crash on it.
  Mirrors `run_distractor.py`'s own reasoning for keeping its output
  filename outside the `results*.json` glob.
- **`retrieval_coverage` diagnostic sub-score (§6, explicitly optional/
  diagnostic-only) is not implemented.** Stage A's context is retained
  per-query in the result JSON for manual inspection, but no automated
  ground-truth-vs-context comparison exists yet.
- **Not run against baselines/ablations/`run_distractor.py` yet** — that
  wiring is Phase 5. `haven` is the runner's default adapter (raw
  `mem0.Memory` has no `build_continuation_context` at all, since it
  predates `BaseAdapter`; the runner falls back to a flat `search()`+join
  for any adapter missing the method, so it would still run, just always
  under the "flat retrieval" condition).

**Compatibility:** `benchmarks/runners/run_benchmarks.py` is byte-for-byte
unmodified. No existing dataset category, adapter behavior, or result
schema changed.

**Next (Phase 4/5, not started):** author the remaining seven categories
(§7) at real scale; wire baselines/ablations/distractor-sweep comparison;
implement `retrieval_coverage` diagnostics; produce
`results/final_report_continuation.md` once real pass-rate numbers exist.

**Post-pilot update (ingestion fix):** this section describes the pilot
exactly as it originally shipped, including Critical-1's now-resolved
defect (every turn stored as `MemoryType.FACT`). That defect is fixed —
see the "Ingestion update" note at the top of this document and
`docs/architecture/CONTINUATION_BENCHMARK_INGESTION_DESIGN.md`'s
"Implementation status" section. One number above needs re-reading in that
light: with typed ingestion in place, `<ProjectState>` is no longer
*structurally* empty, but the two query phrasings this pilot's 10 cases
ship with mostly still don't populate it in practice, for a retrieval-
seeding reason unrelated to ingestion (see the ingestion design doc's
finding 2). Phase 4 authoring should pick query phrasings with real
keyword overlap with each case's own vocabulary, not just phrasings that
classify as `TaskMode.CONTINUATION`.
