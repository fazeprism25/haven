# The Context Plan Object — Design

Status: **Architecture only. No code changed.** This document answers one question left
open by `CONTEXT_PLANNER_DESIGN.md`: that document decided *how* a Context Planner should
decide what's needed (deterministic-first, 5-mode table, LLM fallback on ambiguity) and
*when* retrieval should re-check itself (plan once, verify coverage, one bounded retry).
This document specifies *what object* carries that decision through the pipeline — its
fields, its relationship to `RetrievalPlan`/`CoverageReport`/`WorkingContext`, and exactly
which existing modules it touches. Every claim about current code was checked against
source on disk (not against the prior two docs' descriptions) as of 2026-07-09.

This document assumes, but does not re-derive, the 5-mode clustering
(`POINTED_QA, CODING_DEBUGGING, STRUCTURING, RESEARCH, CONTINUATION`) and the
plan-once-retrieve-verify-retry decision from `CONTEXT_PLANNER_DESIGN.md` §1–§5, and the
category vocabulary (`blockers, rejected_approaches, constraints, implementation_state,
code_areas, benchmark_status, do_not_do`, plus the existing `MemoryRole` values) from
`WORKING_CONTEXT_2_DESIGN.md` §2/§5.

---

## 0. What already exists — the shape this must fit into

Verified directly against source, since the object being designed here has to slot between
these pieces without breaking any of them:

- `MemoryEngine.query_with_trace` (`obsidian/memory_engine/engine.py:392`) and
  `MemoryEngine._allocate` (`engine.py:523`) each run their own copy of
  rewrite → retrieve → merge → validity-filter → rank → accept → allocate. `_allocate`'s own
  docstring calls this a **deliberate** duplication so `query_working_context`/
  `query_structured` (`engine.py:576`, `engine.py:601`) can never change `query`'s behavior.
  A Context Plan that drives *N* per-category retrieval passes cannot become a third or
  fourth copy of this prefix without the duplication becoming real debt — this is addressed
  directly in §7.
- Every stage in the read pipeline passes an immutable, frozen-dataclass record to the next:
  `Candidate` → `RankedCandidate` (`obsidian/ontology/retrieval_models.py:126,256`) →
  `AcceptanceDecision` (`obsidian/memory_engine/acceptance_stage.py:151`) → allocated
  `RankedCandidate` list → `WorkingContext`/`RoleBucket`
  (`retrieval_models.py:1047,902`). Nothing is ever mutated in place; a later stage
  either reuses an earlier object verbatim or wraps it in a new frozen record. The Context
  Plan object must follow this convention, not introduce the first mutable object in the
  pipeline.
- `AcceptanceStage.accept` (`acceptance_stage.py`) already returns **one decision per
  candidate, accepted or not**, carrying the exact threshold/gap/relative-score that
  produced the verdict (`AcceptanceDecision`, `acceptance_stage.py:151-203`) — this is the
  precedent for how Gap Detection should report itself: a decision object per thing
  evaluated, never a silently filtered list.
- `DeterministicSlotAllocator.allocate` (`deterministic_slot_allocator.py:89-115`) is a
  pure `sorted(...)[:config.max_results]` cutoff — no token budget, no per-category notion.
  Confirmed by its own docstring: "No separate token/character budget concept is
  introduced." Any per-category count limit the Context Plan wants to express (`§3`) has
  nothing to hook into today; it is new logic, not a parameterization of something that
  exists.
- `WorkingContextBuilder.build` (`obsidian/memory_engine/working_context_builder.py:120`)
  already does two-level grouping — by primary concept (highest-`activation_score`
  supporting concept, ties broken by `str(concept_id)`), then by `MemoryRole`
  (`_MEMORY_TYPE_ROLE`, `retrieval_models.py:858`) inside each. Every `MemoryRole` gets a
  bucket in every context, even empty (`working_context_builder.py:72-76` design note) —
  this "total, always-present buckets" property is exactly what gap markers need to attach
  to; no restructuring required, just one additive field.
- `RetrievalConfig.max_depth` (`obsidian/ontology/retrieval_config.py:98`) is the real name
  of the activation-spreading hop-count knob (`CONTEXT_PLANNER_DESIGN.md` §3 point 1 already
  flagged the prior doc's "radius" as the wrong name for this).
- `QueryRewriter` (`obsidian/memory_engine/query_rewriter.py`) is the house pattern for
  "optional LLM step with a deterministic-first fallback": cached by normalized query text,
  one bounded call, fails open to zero effect on any error. The Context Plan's own
  `planning_method` field (§2) exists so this same fail-open path is auditable after the
  fact, exactly the way `RetrievalTrace.rewritten_queries` being empty already tells a
  debugger "the rewriter ran and produced nothing" versus "the rewriter never ran."

---

## 1. Design goals, restated as constraints on the object

Everything below the field list in §2 is in service of these four constraints, taken
directly from the two prior docs and from the pipeline's existing conventions:

1. **Auditable at rest.** Like `AcceptanceDecision` and `RetrievalTrace`, a `ContextPlan`
   must be inspectable independent of what retrieval later does with it — a wrong plan
   should be diagnosable by reading the plan object alone, before touching retrieval logs.
2. **A strict floor, never a regression.** `CONTEXT_PLANNER_DESIGN.md` §1's "requires
   nothing" finding means the common case (`POINTED_QA`) must degrade to *exactly* today's
   single-pass `_allocate` behavior, at zero marginal retrieval cost. The object's shape
   must make "no plan was really needed" a first-class, cheap-to-detect state, not a plan
   with one trivial requirement that still forces a per-category loop.
3. **Mechanics-agnostic where it's read, mechanics-aware where it's executed.** A plan
   should say *what* is needed (roles, counts, priority) without knowing *how* retrieval
   gets there (which `AcceptanceConfig`, which `ActivationSpreader` depth). Mixing the two
   makes the plan harder to inspect and couples planning to retrieval internals it
   shouldn't need to know about. This is why §2's `ContextPlan` and §4's `RetrievalPlan`
   are two different objects, not one.
4. **Bounded, not iterative.** Per `CONTEXT_PLANNER_DESIGN.md` §5's Option B recommendation,
   exactly one retry per gap, never a loop. The object must make "this is the retry" a
   visible, capped fact (§6), not an implicit re-invocation of the same code path.

---

## 2. `ContextPlan` — fields (answers brief Q1)

```python
class TaskMode(str, Enum):
    POINTED_QA = "pointed_qa"            # no plan needed — today's single-pass behavior
    CODING_DEBUGGING = "coding_debugging"
    STRUCTURING = "structuring"           # planning + design + architecture + writing, merged
    RESEARCH = "research"
    CONTINUATION = "continuation"         # maximal scope, all categories

class PlanningMethod(str, Enum):
    DETERMINISTIC = "deterministic"
    LLM_FALLBACK = "llm_fallback"

@dataclass(frozen=True)
class PlanScope:
    """None concept_id means project-wide; mirrors RetrievalConfig.max_depth's real name."""
    concept_id: Optional[UUID] = None
    max_depth_override: Optional[int] = None

    @property
    def is_project_wide(self) -> bool:
        return self.concept_id is None

@dataclass(frozen=True)
class ContextPlan:
    query: str
    task_mode: TaskMode
    scope: PlanScope
    requirements: Tuple["CategoryRequirement", ...]
    confidence: float                # 0.0-1.0, this plan's own classification confidence
    planning_method: PlanningMethod
    max_gap_retries: int = 1         # exposed, not hidden — same reasoning as AcceptanceConfig's fields
    created_at: datetime = field(default_factory=datetime.utcnow)
```

**`requirements: ()` is the `POINTED_QA` sentinel, not a plan with one trivial entry.** An
empty tuple means "no category planning applies — run the existing single-pass retrieval
exactly as today, unmodified." This is what makes goal 2 in §1 concrete: the majority of
queries (the existing benchmark corpus, per `CONTEXT_PLANNER_DESIGN.md` §1) get a
`ContextPlan(task_mode=POINTED_QA, requirements=())`, and `MemoryEngine` can check
`if not plan.requirements` and fall straight through to the existing `_allocate` call —
zero new retrieval passes, zero new code on the hot path.

Fields deliberately **not** on `ContextPlan`, and why: no `AcceptanceConfig`, no
`ActivationSpreader` depth beyond the scope override, no `MemoryType` bias table. Those are
retrieval mechanics — they belong on `RetrievalPlan` (§4), derived *from* this object, so
that reading a `ContextPlan` never requires understanding acceptance thresholds to answer
"what did the planner think this query needed."

---

## 3. Weighting and priority (answers brief Q2)

**No continuous weight field. Priority is a three-value ordinal tier, not a tunable
number.** This mirrors `WORKING_CONTEXT_2_DESIGN.md` §7's tiering
(never-removed / scored-normally / removed-first) directly, and deliberately does *not*
introduce a new scoring dimension the way `DeterministicRanker`'s `score_breakdown`
already does at the candidate level — a category-level float weight would be a second,
uncalibrated scoring system sitting on top of one that already exists and is tuned. Ordinal
tiers say "Project always outranks Research" as a structural fact about the tier assignment,
not a magnitude comparison that needs calibrating:

```python
class PriorityTier(str, Enum):
    NEVER_DROP = "never_drop"   # constraints, do_not_do, top active goal, newest open blocker
    NORMAL = "normal"           # scored normally by the existing DeterministicRanker output
    DROP_FIRST = "drop_first"   # architecture_snapshot, superseded decisions, resolved tasks
```

`priority_tier` lives on `CategoryRequirement` (§4), one per required role, not on
`ContextPlan` as a single global setting — different task modes weight the same role
differently (a `blocker` is `NEVER_DROP` under `CODING_DEBUGGING` but merely `NORMAL`
under `RESEARCH`, where it's not even a required category). *Within* a tier, ordering is
untouched: `DeterministicRanker`'s existing `final_score` still decides ordering among
members of the same role/tier, exactly as it does today — the tier only decides which
categories survive a token-budget cut first (`WORKING_CONTEXT_2_DESIGN.md` §7's job, unchanged
by this document, just given a field to read from instead of a hardcoded category list).

**Answering "should Current Project always outrank Recent Research" directly:** yes, but
expressed as `CONTINUATION`'s fixed requirement table assigning `identity`/`goals`
`NEVER_DROP` or high-priority `NORMAL` and `recent_discoveries` `DROP_FIRST` — a property of
the per-mode table (a config decision, editable, not learned), not a property that needs a
weight to express.

---

## 4. Specifying retrieval requirements (answers brief Q3)

```python
class RequirementNecessity(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"

@dataclass(frozen=True)
class CategoryRequirement:
    role: MemoryRole                       # reuses the existing enum verbatim — no parallel vocabulary
    necessity: RequirementNecessity
    min_count: int = 1
    max_count: Optional[int] = None        # None = no cap beyond AcceptanceConfig.acceptance_max_k
    priority_tier: PriorityTier = PriorityTier.NORMAL

    def __post_init__(self) -> None:
        if self.min_count < 0:
            raise ValueError("min_count must be >= 0")
        if self.max_count is not None and self.max_count < self.min_count:
            raise ValueError("max_count must be >= min_count")
```

This directly expresses the brief's example ("1 Project, 2 Decisions, 1 Goal, 3 Tasks, no
more than 2 Constraints") as one `CategoryRequirement` per line: `role=MemoryRole.DECISION,
necessity=REQUIRED, min_count=2`; `role=MemoryRole.CONSTRAINT, necessity=OPTIONAL,
max_count=2`, etc. **`role` reuses `MemoryRole` verbatim, including the 7 new values
`WORKING_CONTEXT_2_DESIGN.md` §5 proposes** (`BLOCKER, CONSTRAINT, REJECTED_APPROACH,
IMPLEMENTATION_STATE, CODE_AREA, BENCHMARK_STATUS, DO_NOT_DO`) — the Context Plan
introduces no new category vocabulary of its own; "category" in the brief and "`MemoryRole`"
in the codebase are the same thing, once those 7 values land.

The fixed per-`task_mode` requirement tables (`CODING_DEBUGGING`, `STRUCTURING`, `RESEARCH`,
`CONTINUATION`; `POINTED_QA` maps to `requirements=()`) are editable config, exactly the
posture `CONTEXT_PLANNER_DESIGN.md` §2/§4 already recommends — a Python literal the
`ContextPlanner` looks up by `task_mode`, not a learned or LLM-authored table.

---

## 5. How Gap Detection consumes this object (answers brief Q4)

**`GapDetector` does no scoring of its own — it only compares `CoverageEntry` counts
against `CategoryRequirement` bounds.** This is the same division of labor
`AcceptanceDecision` already models: the *judgment* of "is this candidate good enough"
lives entirely in `AcceptanceStage`; `GapDetector`'s only job is arithmetic over
`AcceptanceStage`'s already-computed output:

```python
def detect(plan: ContextPlan, entries: Tuple["CoverageEntry", ...]) -> "CoverageReport":
    # For each entry: satisfied = (
    #     len(entry.accepted) >= entry.requirement.min_count
    #     and (entry.requirement.max_count is None or len(entry.accepted) <= entry.requirement.max_count)
    #     and entry.top_confidence is not None
    #     and entry.top_confidence >= <category-specific abstention floor already used by AcceptanceStage>
    # )
    ...
```

No candidate is re-ranked, re-scored, or re-filtered here — `entry.accepted` is exactly the
`RankedCandidate` list `AcceptanceStage.accept` already produced for that category's
`RetrievalStep` (§6), and `entry.top_confidence` is that same list's top `final_score`,
read, not recomputed. This avoids the duplicate-logic risk the brief calls out directly:
"is this good enough" stays a single answer, owned by `AcceptanceStage`; `GapDetector`
only asks "did the plan's stated minimum get met." `AcceptanceConfig`'s existing
`abstention_score` (`acceptance_stage.py:99-103`) is what supplies the
"category-specific abstention floor" `WORKING_CONTEXT_2_DESIGN.md` §6 calls for — each
`RetrievalStep` (§6) carries its own `AcceptanceConfig` instance, so the floor is
already category-specific by construction, not by `GapDetector` inventing a second
threshold system.

---

## 6. How the Working Context Builder consumes this object (answers brief Q5)

`WorkingContextBuilder.build` gains one new optional parameter, defaulting to `None` so
every existing caller (`MemoryEngine.query_working_context`, `query_structured`) is
byte-for-byte unaffected:

```python
def build(
    self,
    ranked_candidates: List[RankedCandidate],
    coverage: Optional["CoverageReport"] = None,
    merge_scope: bool = False,
) -> List[WorkingContext]:
```

- `coverage=None` (today's every call site): identical behavior, identical output, no gap
  markers, no merged scope. This is what makes the change additive rather than a rewrite.
- `coverage` present: after building buckets exactly as today, look up each bucket's role in
  `coverage.entries`; if that entry is unsatisfied, set the bucket's new
  `gap_reason: Optional[str]` field (additive, default `None` on `RoleBucket`). The
  "every role gets a bucket even when empty" property already in place
  (`working_context_builder.py:72-76`) means there is always a bucket to attach the marker
  to — no restructuring of the grouping logic itself.
- `merge_scope=True` (set by the caller when `plan.scope.is_project_wide`, i.e.
  `CONTINUATION` mode): skip the per-primary-concept split and fold every candidate into one
  context of `kind=ContextKind.PROJECT` (activating the currently-dead `PROJECT` variant,
  `CONTEXT_PLANNER_DESIGN.md` §3 point 3) instead of the existing per-concept `TOPIC` split.
  This directly answers `WORKING_CONTEXT_2_DESIGN.md` §3 point 1's finding: the planner's
  actual job re: scope isn't inventing new grouping, it's *overriding* the grouping that
  already happens on every query, only when `CONTINUATION` calls for one holistic
  reconstruction instead of N per-concept contexts.
- `WorkingContext` gains one new additive field, `gaps: Tuple[MemoryRole, ...] = ()`, a pure
  projection over its own buckets' `gap_reason`s — the same "derive, never hand-roll" rule
  `WorkingContextState.from_buckets` already follows for `status`/`current_goal`.

---

## 7. Immutable or evolving? (answers brief Q6)

**`ContextPlan` itself is immutable — a frozen dataclass, built once, never mutated,
exactly like `RankedCandidate`, `AcceptanceDecision`, and `RetrievalTrace` already are.**
What is allowed to grow is the *execution record* built around it, not the plan.

**Approach A — immutable plan, evolving execution trace (recommended).** The plan is
computed once by `ContextPlanner.plan()` and never touched again. Retrieval executes it as
a `RetrievalPlan` (§8) of `RetrievalStep`s; if `GapDetector` finds an unsatisfied *required*
`CategoryRequirement`, exactly one additional `RetrievalStep` is appended for that
category — marked `is_retry=True`, with a relaxed `AcceptanceConfig` or a synthesized
targeted sub-query — and `GapDetector` re-evaluates only that entry. The *plan*
(`ContextPlan.requirements[i]`, its `min_count`/`max_count`) never changes; only the number
of `RetrievalStep`s executed against it grows, capped at `plan.max_gap_retries`. This is
structurally identical to how `RetrievalTrace` already accumulates multiple
`rewritten_queries` from one `QueryRewriter.rewrite()` call without anything being mutated
in place — a fixed, small, one-shot expansion, not a loop.

**Approach B — the plan itself is mutable, re-derived mid-retrieval** (e.g. the planner is
re-invoked with partial results and allowed to add or drop requirements). Rejected, for the
same reason `CONTEXT_PLANNER_DESIGN.md` §5 already rejected unbounded iterative retrieval
(its "Option C"): no termination guarantee without an explicit budget, and it destroys the
one property that makes this whole design worth building — an inspector can point at *the*
plan and ask "why this," and get one fixed answer. A plan that silently reshapes itself
mid-run turns that into "why this, as of whichever revision happened to be current when you
looked" — the same auditability loss `AcceptanceStage`'s design already explicitly avoids
by producing one `AcceptanceDecision` per candidate rather than a mutable running filter.

**Recommendation: A.** It costs nothing beyond what `CONTEXT_PLANNER_DESIGN.md` §5 already
committed to (bounded, one-retry-per-gap retrieval), and it keeps `ContextPlan` at the same
trust level as every other frozen record already in this pipeline.

---

## 8. Integration with existing architecture, minimal-change (answers brief Q7)

> **Status note (Phase 1.5).** A much smaller, diagnostics-only slice of this
> section has landed ahead of the rest: `MemoryEngine.query_with_trace` calls
> `ContextPlanner` once per request and attaches the resulting `ContextPlan`
> to `RetrievalTrace.context_plan` (plain-primitive fields: task mode,
> planning method, scope, confidence, category requirements). It does not
> use `MemoryRole` (`ContextCategory` stays its own enum, per this module's
> own docstring), does not add `gap_reason`/`gaps`, does not add
> `query_context_plan`, and does not touch `_allocate` or the
> `_retrieve_and_rank` refactor below — every item in this section is still
> unimplemented future work. See `obsidian/docs/ARCHITECTURE.md`'s "Context
> Planner (Phase 1.5 — observational only)" section for what actually
> exists today.
>
> **Status note (Phase 3).** A first, deliberately narrow slice of "make the
> plan actually drive retrieval" has also landed, well short of the full
> per-category `RetrievalStep` execution loop this section designs:
> `CategoryPreferenceScorer` (`obsidian/memory_engine/category_preference.py`)
> adds a small, bounded score bonus to any candidate whose category
> `context_plan.requirements` names, applied once per query between
> `DeterministicRanker` and `AcceptanceStage` — a soft re-ranking nudge, not
> the `RetrievalStep`-per-category loop, `GapDetector`, or bounded-retry
> machinery this section still designs from scratch. It does not add
> `RetrievalPlan`/`RetrievalStep`, does not run `_retrieve_and_rank` more
> than once per query, and does not touch `_allocate`. See
> `obsidian/docs/ARCHITECTURE.md`'s "Category-Aware Retrieval (Phase 3 —
> behavior-changing)" section for what actually exists today.

Reused as-is, no redesign:
- `MemoryRole`, `resolve_role()`, `RoleBucket`'s "total buckets" property
  (`retrieval_models.py:775-936`) — `CategoryRequirement.role` is this enum, not a new one.
- `AcceptanceStage`/`AcceptanceConfig`/`AcceptanceDecision` — called once per
  `RetrievalStep` with a per-step `AcceptanceConfig` instance; **no signature change**,
  contrary to what a first read of `CONTEXT_PLANNER_DESIGN.md` §3 point 4 might suggest —
  calling `accept()` N times with N independently-scoped candidate lists is N ordinary
  calls to the existing method, not a new contract. The actual design decision this
  surfaces (not a code change) is *whether* different `RetrievalStep`s get different
  `AcceptanceConfig.abstention_score` values — `RetrievalStep.acceptance_config` (§8) makes
  that decision explicit and inspectable rather than implicit.
- `WorkingContextBuilder`, `RetrievalConfig.max_depth`, `RetrievalTrace` — reused verbatim;
  `CoverageEntry` embeds a `RetrievalTrace` per category pass (§8) instead of inventing a
  parallel diagnostics shape.
- `QueryRewriter`'s fail-open/cache contract — the `PlanningMethod.LLM_FALLBACK` path
  reuses its client/cache/timeout scaffolding directly rather than rebuilding it, per
  `CONTEXT_PLANNER_DESIGN.md` §4/§9.

New, additive-only:
- A new module, `obsidian/memory_engine/context_planner.py`, holding `TaskMode`,
  `PlanningMethod`, `RequirementNecessity`, `PriorityTier`, `PlanScope`,
  `CategoryRequirement`, `ContextPlan`, and the `ContextPlanner` class — following the
  existing one-module-per-stage convention (`acceptance_stage.py`, `query_rewriter.py`,
  `working_context_builder.py` are each exactly this shape). Depends on
  `retrieval_models.MemoryRole`; nothing in `retrieval_models.py` depends back on it.
- A new module, `obsidian/memory_engine/gap_detector.py`, holding `CoverageEntry`,
  `CoverageReport`, and `GapDetector`.
- `RetrievalPlan`/`RetrievalStep` (§8's mechanics-aware translation layer) live in
  `engine.py` itself, next to the `_allocate` prefix they parameterize, since they have no
  meaning independent of `MemoryEngine`'s retrieval call.
- **`RoleBucket.gap_reason: Optional[str] = None`** and **`WorkingContext.gaps: Tuple[MemoryRole, ...] = ()`** —
  two additive fields, default values chosen so every existing serialized `WorkingContext`
  round-trips through `to_dict`/`from_dict` unchanged (an omitted key on read defaults to
  `None`/`()`, matching the pattern `RankedCandidate.score_breakdown` etc. already use for
  optional data).
- `MemoryEngine` gains one new method, `query_context_plan(raw_query) -> Tuple[List[WorkingContext], ContextPlan, CoverageReport]`,
  following exactly the `query_working_context`/`query_structured` precedent: **purely
  additive, cannot affect `query`, `query_with_trace`, or `ContextBuilder`.**
- **The `_allocate`-duplication decision, resolved explicitly rather than deferred:**
  `CONTEXT_PLANNER_DESIGN.md` §3 point 5 flagged that a per-category retrieval loop
  becoming a third independent copy of the rewrite→retrieve→merge→validity-filter→rank
  prefix is a real risk. This document resolves it: factor that prefix (the code shared by
  `query_with_trace` lines 420-453 and `_allocate` lines 548-567) into one private helper,
  `MemoryEngine._retrieve_and_rank(query: str, config: RetrievalConfig) -> List[RankedCandidate]`,
  that all three call sites — `query_with_trace`, `_allocate`, and the new per-category loop
  inside `query_context_plan` — call. This is a behavior-preserving refactor (same inputs,
  same outputs, verified by the existing test suite staying green), done once, before
  `query_context_plan`'s per-category loop is built, rather than after a fourth copy
  already exists.
- `server/main.py`'s `/retrieve_working_context` endpoint gains the `reconstruction: bool`
  flag `WORKING_CONTEXT_2_DESIGN.md` §8 already proposed, switching to
  `query_context_plan` and including `plan`/`coverage` in the JSON response for the
  dashboard's Retrieval Inspector.

Nothing existing is removed or restructured; the one true refactor (`_retrieve_and_rank`)
changes *where* code lives, not what it does.

---

## 9. The final datamodel and its relationships (answers brief Q8)

```
Question (raw_query: str)
    │
    ▼
ContextPlanner.plan(raw_query)
    │  reads: cheap pattern/keyword table, concept-resolution readout (already free —
    │  retrieval computes it anyway), optional LLM fallback on disagreement/CONTINUATION
    ▼
ContextPlan                                    ◄── immutable, never mutated (§7)
    │  { query, task_mode, scope, requirements: (CategoryRequirement, ...),
    │    confidence, planning_method, max_gap_retries, created_at }
    │
    ▼
RetrievalPlan.from_context_plan(plan)          ◄── pure derivation, mechanics-aware (§1 goal 3)
    │  one RetrievalStep per CategoryRequirement (or exactly one pass-through
    │  RetrievalStep when requirements == (), i.e. POINTED_QA)
    ▼
RetrievalPlan { plan, steps: (RetrievalStep, ...) }
    │  RetrievalStep { requirement, sub_query, acceptance_config, scope, is_retry }
    │
    ▼
MemoryEngine._retrieve_and_rank(step.sub_query, ...)   ◄── existing prefix, factored out (§8)
    + AcceptanceStage.accept(..., step.acceptance_config)   ◄── existing, called once per step
    │
    ▼
CoverageEntry { requirement, accepted: (RankedCandidate, ...), top_confidence,
                satisfied, gap_reason, retried, trace: RetrievalTrace }
    │  one per RetrievalStep executed
    │
    ▼
GapDetector.detect(plan, entries)              ◄── arithmetic only, no re-scoring (§5)
    │  for each unsatisfied REQUIRED entry not yet retried: append one retry
    │  RetrievalStep (bounded by plan.max_gap_retries), re-run only that entry
    ▼
CoverageReport { plan, entries: (CoverageEntry, ...) }
    │  .gaps property: roles where satisfied == False
    │
    ▼
WorkingContextBuilder.build(allocated_candidates, coverage=report, merge_scope=plan.scope.is_project_wide)
    │  existing per-concept/per-role grouping, now annotated with gap markers (§6)
    ▼
WorkingContext[] { ..., buckets: (RoleBucket { role, members, gap_reason }, ...),
                   gaps: (MemoryRole, ...) }
    │
    ▼
StructuredPromptBuilder.render(contexts, raw_query)     ◄── existing renderer, extended
                                                             with an "Unknown / Not Found"
                                                             section reading WorkingContext.gaps
```

Every arrow is a pure function over the previous immutable object plus, at the retrieval
steps, the existing engine internals — nothing is fed back upstream, matching §7's
recommendation.

---

## 10. Implementation effort and hackathon phasing (answers brief Q9)

This refines `CONTEXT_PLANNER_DESIGN.md`'s own Phase 1/2 sizing with the datamodel
specifics from this document; it does not re-estimate the broader per-category-retrieval
cost already sized there (§6 there is still the authoritative cost table for that work).

**Before hackathon — the object itself, deterministic-only, demoable on its own.**

| Piece | Effort | Notes |
|---|---|---|
| `TaskMode`, `PlanningMethod`, `RequirementNecessity`, `PriorityTier`, `PlanScope`, `CategoryRequirement`, `ContextPlan` dataclasses + `to_dict`/`from_dict` | Small (~0.5–1 day) | Mechanical, follows the exact pattern every other frozen dataclass in `retrieval_models.py` already uses |
| Deterministic `ContextPlanner.plan()` — 5-mode table + concept-resolution readout, `POINTED_QA` default, no LLM | Small (~1 day) | Zero dependency on any retrieval change; testable purely against query strings + a stubbed concept resolver |
| `RetrievalPlan.from_context_plan()` pure derivation | Small (~0.5 day) | Mechanical mapping from `CategoryRequirement` to `RetrievalStep`; no execution logic yet |
| Factor `_retrieve_and_rank()` out of `query_with_trace`/`_allocate` | Medium (~1 day) | Must be verified behavior-preserving — existing tests must stay byte-identical; this is the one piece that touches proven code |
| Log/display the computed `ContextPlan` (no gap detection, no per-category execution yet) | Small (~0.5 day) | The demo moment: an inspectable, mode-adaptive plan object, shown the same way `AcceptanceDecision`/`RetrievalTrace` already are |

**Total: ~3.5–4 days.** This alone delivers the artifact the brief asks for
("what exactly should the planner produce") as a real, inspectable object, without
requiring any of the higher-risk retrieval-loop work below.

**After hackathon — making the plan actually drive retrieval and rendering.**

| Piece | Effort | Notes |
|---|---|---|
| Per-category `RetrievalStep` execution loop (up to 5 calls to `_retrieve_and_rank` for `CONTINUATION`) | Medium–High | The real architecture cost `CONTEXT_PLANNER_DESIGN.md` §6 already sized; this document doesn't change that estimate |
| `GapDetector` + `CoverageEntry`/`CoverageReport` | Low–Medium, once the loop above exists | Pure comparison logic (§5); cannot be built or measured standalone |
| Bounded retry wiring (`max_gap_retries`, `is_retry` steps) | Low | Mechanical given the loop and `GapDetector` above |
| `RoleBucket.gap_reason` / `WorkingContext.gaps` fields + `WorkingContextBuilder` wiring (`coverage`, `merge_scope` params) | Medium | Touches a well-tested existing module; the `None`/`False`-default path must stay byte-identical for every existing caller |
| `StructuredPromptBuilder` "Unknown / Not Found" section | Low–Medium | Pure rendering addition, reads `WorkingContext.gaps` |
| `server/main.py` `reconstruction` flag + response shape | Low | Additive endpoint parameter |
| `PlanningMethod.LLM_FALLBACK` path | Medium | Reuses `QueryRewriter` scaffolding; needs its own eval pass before defaulting on, per `CONTEXT_PLANNER_DESIGN.md` Phase 4 |

**Recommendation:** build the "before hackathon" row now — it is small, low-risk, has no
dependency on the harder retrieval-loop work, and is the concrete answer to "what does the
planner produce." Treat everything in the second table as the real investment, gated on
the `_retrieve_and_rank` factor already being in place so it doesn't get built against a
prefix that's about to be refactored out from under it.
