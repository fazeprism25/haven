# The Retrieval Plan Abstraction — Design

Status: **Architecture only. No code changed.** Every claim about current code below was
checked against source on disk as of 2026-07-09, not against `CONTEXT_PLAN_OBJECT.md`'s
prior description of a `RetrievalPlan`/`RetrievalStep`/`GapDetector` — that design was
never built. This document supersedes that sketch with a smaller one grounded in what
Haven's read pipeline actually does today.

---

## 0. What already exists — the shape this must fit into

Verified directly against source:

- **The read pipeline runs exactly one retrieval pass per query** (or one per rewrite, when
  `QueryRewriter` is configured — `MemoryEngine.query_with_trace`,
  `obsidian/memory_engine/engine.py:687-701`). There is no per-category retrieval loop
  anywhere in the codebase. `CONTEXT_PLAN_OBJECT.md` §8/§9 designed one (`RetrievalStep` per
  `CategoryRequirement`, up to 5 calls for `CONTINUATION`) — it was never implemented. What
  got built instead, across Phases 1.5–4, is lighter:
  - **Phase 1.5** (`context_planner.py`): `ContextPlan` is computed once, before retrieval,
    and attached to `RetrievalTrace` purely for diagnostics.
  - **Phase 3** (`category_preference.py`): a single, bounded, additive score bonus —
    `CategoryPreferenceScorer` — nudges candidates whose category the plan requested,
    *after* the one retrieval call, between `DeterministicRanker` and `AcceptanceStage`.
    This is the only place `ContextPlan` currently changes pipeline output.
  - **Phase 2** (`coverage_analyzer.py`): `analyze_coverage()` compares the plan's
    requirements against what got accepted, read-only, into a `CoverageReport`.
  - **Phase 4** (`gap_recovery.py`): `decide_gap_recovery()` derives a `GapRecoveryDecision`
    (`should_retry`, `missing_categories`, `retry_budget`, `retry_reason`,
    `recovery_strategy`) from `ContextPlan` + `CoverageReport` alone. **Nothing consumes
    this decision.** `should_retry=True` today issues zero additional retrieval. This is
    the dead-end this document's abstraction exists to unblock — see §9.
- **Every stage passes an immutable, frozen-dataclass record to the next** — `ContextPlan`
  (`context_planner.py:329`), `CoverageReport` (`coverage_analyzer.py:255`),
  `GapRecoveryDecision` (`gap_recovery.py:200`), `RetrievalTrace`
  (`retrieval_models.py:1101`). Nothing is ever mutated in place.
- **`RetrievalTrace` already has a fixed pattern for absorbing a new diagnostic-only field**:
  `ContextPlanTrace` / `CoverageReportTrace` / `GapRecoveryTrace`
  (`retrieval_models.py:739-1093`) are each a plain-primitive projection of a richer object
  that lives in `obsidian.memory_engine` (`ContextPlan`, `CoverageReport`,
  `GapRecoveryDecision`), built by a private `_*_trace()` function in `engine.py`
  (`_context_plan_trace`, `_coverage_report_trace`, `_gap_recovery_trace`,
  `engine.py:331-409`). The dependency is strictly one-directional: `retrieval_models.py`
  (in `obsidian.ontology`) never imports the enum types those richer objects use, because
  `obsidian.memory_engine` already depends on `obsidian.ontology`, not the reverse. A fourth
  field follows this exact recipe.
- **`MemoryEngine` runs two independent copies of the retrieval prefix today** —
  `query_with_trace` (`engine.py:581-810`) and `_allocate` (`engine.py:816-880`), the latter's
  own docstring calling this a *deliberate* duplication so `query_working_context`/
  `query_structured` can never change `query`'s behavior. `CONTEXT_PLAN_OBJECT.md` §8 already
  flagged that a per-category loop becoming a third copy of this prefix would be real debt,
  and proposed factoring it into `_retrieve_and_rank()` first. That refactor was never done
  either — both copies still exist independently. Any design here has to either accept this
  duplication a while longer or be the reason it finally gets factored.
- **`QueryRewriter`** (`query_rewriter.py`) already produces *multiple queries* against the
  same `MemoryEngine.query_with_trace` call — up to two LLM-generated rewrites, each run
  through `HybridCandidateRetriever` independently, then merged by `_merge_candidates`
  (`engine.py:412-446`). This is a second, pre-existing "one query becomes several retrieval
  calls" mechanism that predates this design and is not being replaced by it — see §7 for how
  the two relate.
- **`RetrievalConfig`** (`retrieval_config.py`) and **`AcceptanceConfig`**
  (`acceptance_stage.py:89`) are the two existing tunable-parameter surfaces for a retrieval
  call: activation depth/decay, scoring weights, `minimum_candidate_score`, `max_results`
  (`RetrievalConfig`); abstention/gap-cut/relative-floor/hard-cap thresholds
  (`AcceptanceConfig`). Both are already frozen dataclasses, already constructor arguments to
  `MemoryEngine.__init__`, and already independent of `ContextPlan`. A `RetrievalPlan` does
  not replace either — it is the place a *specific retrieval step* can name an override to
  one, the same way `CategoryRequirement.priority_tier` doesn't replace
  `DeterministicRanker`'s scoring, it rides alongside it, currently unread.

---

## 1. What problem this actually solves

`GapRecoveryDecision.should_retry` has existed since Phase 4 and has never once caused a
second retrieval call. The reason isn't missing decision logic — `decide_gap_recovery()`
already correctly says *whether* and *why* to retry. What's missing is a description of
*how* a retry would run that `MemoryEngine` could execute without writing bespoke,
one-off retry code that special-cases "this is attempt 2."

That's the actual scope of `RetrievalPlan`: **a small, mechanics-aware object sitting between
`ContextPlan` (what's needed) and `MemoryEngine`'s retrieval call (what happens), so that
"run retrieval once" and "run retrieval twice, the second time narrower" are the same code
path executing a different number of steps** — not two different code paths. Nothing about
gap recovery's *decision* logic changes; this document is purely about giving that decision
somewhere to land.

This directly restates the user's framing: `ContextPlan` answers "what information do we
need," `RetrievalPlan` answers "how should this query be executed." They are kept separate
for the same reason `CONTEXT_PLAN_OBJECT.md` §1 goal 3 already gave: mixing "what" and "how"
into one object means reading the plan to answer "what does this query need" would require
also understanding acceptance thresholds and retrieval sources — a strictly worse object for
the Retrieval Inspector to render.

---

## 2. Where it lives

`obsidian/memory_engine/retrieval_plan.py` — a new module, following the codebase's existing
one-module-per-stage convention (`context_planner.py`, `coverage_analyzer.py`,
`gap_recovery.py`, `category_preference.py` are each exactly this shape: one or two frozen
dataclasses plus one pure function/class that produces them).

Not `obsidian/ontology/`: `RetrievalPlan` needs to reference `RetrievalConfig`
(`obsidian.ontology.retrieval_config`, fine — `memory_engine` already depends on `ontology`)
and, for step-level overrides, `AcceptanceConfig` (`obsidian.memory_engine.acceptance_stage`).
Putting `RetrievalPlan` in `obsidian.ontology` would require `ontology` to import
`memory_engine`'s `AcceptanceConfig`, inverting the dependency direction every existing trace
class in `retrieval_models.py` was carefully kept out of. `memory_engine` is the correct side
of that boundary — exactly where `ContextPlan` itself already lives.

Not inside `engine.py`: `CONTEXT_PLAN_OBJECT.md` §8 suggested `RetrievalPlan`/`RetrievalStep`
"live in `engine.py` itself... since they have no meaning independent of `MemoryEngine`'s
retrieval call." Keeping them in their own module instead, mirroring `ContextPlan`, is more
consistent with how every other frozen record in this pipeline is organized (one file per
concern, `engine.py` importing and orchestrating, never defining a data model of its own) —
`engine.py` today defines zero dataclasses; it should stay that way.

---

## 3. Proposed datamodel

```python
# obsidian/memory_engine/retrieval_plan.py

class RetrievalSource(str, Enum):
    """Which retrieval mechanism a RetrievalStep should run through.

    Values
    ------
    HYBRID : str
        HybridCandidateRetriever's existing keyword+ontology merge — the
        only value any RetrievalStep carries today, and the only one
        MemoryEngine can execute in this phase.
    KEYWORD : str
        Reserved. KeywordCandidateRetriever alone, no ontology path. No
        RetrievalStep produced by this codebase sets this today.
    ONTOLOGY : str
        Reserved. The ontology path alone (QueryResolver /
        ActivationSpreader / CandidateAssembler), no keyword path.
    SEMANTIC : str
        Reserved. Embedding-similarity retrieval. No embedder exists
        anywhere in this codebase yet; this value exists so a future
        embedding-backed retriever has a source to declare without a
        RetrievalStep schema change, mirroring
        PlanningMethod.LLM_FALLBACK's "reserved, not yet reachable" role.
    PROJECT_STATE : str
        Reserved. A retriever scoped to IMPLEMENTATION_STATE / CODE_AREA /
        BLOCKER KnowledgeObjects regardless of keyword/ontology match
        strength -- for "what's the current state of X" queries where
        recency and type matter more than textual overlap.
    ACTIVE_CONTEXT : str
        Reserved. A query-independent source: "whatever is currently
        pinned/active," bypassing keyword and ontology matching entirely.
    """
    HYBRID = "hybrid"
    KEYWORD = "keyword"
    ONTOLOGY = "ontology"
    SEMANTIC = "semantic"
    PROJECT_STATE = "project_state"
    ACTIVE_CONTEXT = "active_context"


class StepPurpose(str, Enum):
    """Why a RetrievalStep exists.

    Values
    ------
    PRIMARY : str
        Part of the plan's initial step set, decided before any
        retrieval runs. Every RetrievalPlan produced by this phase
        contains exactly one PRIMARY step and nothing else.
    GAP_RECOVERY : str
        Reserved. A step added after coverage analysis found a REQUIRED
        category unmet -- see §7. Not producible by anything in this
        phase; exists so RetrievalStep's shape does not change when gap
        recovery is eventually wired up.
    """
    PRIMARY = "primary"
    GAP_RECOVERY = "gap_recovery"


@dataclass(frozen=True)
class RetrievalStep:
    """One concrete instruction: run retrieval this way.

    A RetrievalStep never says *why* a query needs this category (that's
    ContextPlan.requirements' job) -- it says what MemoryEngine should
    literally do: which text to search, which mechanism to search it
    with, and which knobs to override, if any.

    Parameters
    ----------
    query : str
        The text to retrieve against. Equal to the originating
        ContextPlan.query for every PRIMARY step; a future GAP_RECOVERY
        step may carry a narrower synthesized sub-query instead.
    source : RetrievalSource
        Which retrieval mechanism executes this step. Default HYBRID --
        the only value MemoryEngine can execute today (see §6).
    scope_concept_id : UUID, optional
        Carried verbatim from ContextPlan.scope_concept_id. None means
        project-wide.
    max_results_override : int, optional
        Overrides RetrievalConfig.max_results for this step only, when
        set. None means "use the engine's configured default" -- the
        only value this phase ever produces.
    acceptance_config_override : AcceptanceConfig, optional
        Overrides the engine's configured AcceptanceConfig for this step
        only, when set. None means "use the engine's configured
        default" -- the only value this phase ever produces.
    purpose : StepPurpose
        Why this step exists. Default PRIMARY.
    target_category : ContextCategory, optional
        The category a GAP_RECOVERY step is attempting to satisfy.
        Must be None for a PRIMARY step and non-None for a GAP_RECOVERY
        step (enforced in __post_init__) -- there is no other way for
        this field to disagree with purpose.

    Raises
    ------
    ValueError
        If max_results_override is set and < 1, or if purpose and
        target_category are inconsistent with each other.
    """
    query: str
    source: RetrievalSource = RetrievalSource.HYBRID
    scope_concept_id: Optional[UUID] = None
    max_results_override: Optional[int] = None
    acceptance_config_override: Optional["AcceptanceConfig"] = None
    purpose: StepPurpose = StepPurpose.PRIMARY
    target_category: Optional["ContextCategory"] = None

    def __post_init__(self) -> None:
        if self.max_results_override is not None and self.max_results_override < 1:
            raise ValueError(
                f"max_results_override must be >= 1; got {self.max_results_override}"
            )
        is_gap_recovery = self.purpose is StepPurpose.GAP_RECOVERY
        if is_gap_recovery and self.target_category is None:
            raise ValueError("a GAP_RECOVERY step must set target_category")
        if not is_gap_recovery and self.target_category is not None:
            raise ValueError("a PRIMARY step must not set target_category")


@dataclass(frozen=True)
class RetrievalPlan:
    """How a query's retrieval should be executed, decided before it runs.

    Immutable, standalone in this phase -- like ContextPlan, nothing in
    this codebase consumes it yet beyond diagnostics (see §5). Not
    mutated or extended after construction; a future retry does not
    edit this object (see §7 for why, and where retry steps actually
    live instead).

    Parameters
    ----------
    query : str
        The originating ContextPlan.query, carried through verbatim for
        a self-contained record -- this is denormalized, not derived,
        so a RetrievalPlan can be read on its own without also holding
        the ContextPlan it came from.
    steps : tuple[RetrievalStep, ...]
        The steps to execute, in order. Exactly one PRIMARY step in
        every plan this phase produces; never empty.
    created_at : datetime
        UTC timestamp when this plan was produced. Diagnostic only.

    Raises
    ------
    ValueError
        If steps is empty.
    """
    query: str
    steps: Tuple[RetrievalStep, ...]
    created_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(self.steps))
        if not self.steps:
            raise ValueError("a RetrievalPlan must contain at least one step")

    @property
    def is_single_pass(self) -> bool:
        """True iff this plan is exactly today's one-PRIMARY-step shape."""
        return len(self.steps) == 1 and self.steps[0].purpose is StepPurpose.PRIMARY


def derive_retrieval_plan(context_plan: "ContextPlan") -> RetrievalPlan:
    """Translate a ContextPlan into a RetrievalPlan, deterministically.

    Phase 1 (this document's only implementable phase): always produces
    exactly one PRIMARY RetrievalStep, wrapping today's single
    HybridCandidateRetriever call verbatim --
    RetrievalStep(query=context_plan.query, source=HYBRID,
    scope_concept_id=context_plan.scope_concept_id). This holds
    regardless of task_mode or requirements -- category-aware behavior
    is CategoryPreferenceScorer's job (Phase 3, downstream of retrieval),
    not this function's, and this function reads context_plan.query and
    context_plan.scope_concept_id only. It never reads
    context_plan.requirements: expressing "N required categories" as N
    RetrievalSteps is explicitly out of scope for the reasons in §9.

    No clock read beyond RetrievalPlan.created_at, no randomness, no I/O,
    no LLM call, no exception path -- a pure, total function of its one
    input.
    """
    return RetrievalPlan(
        query=context_plan.query,
        steps=(
            RetrievalStep(
                query=context_plan.query,
                source=RetrievalSource.HYBRID,
                scope_concept_id=context_plan.scope_concept_id,
            ),
        ),
    )
```

**Answering the brief's datamodel questions directly:**

- *Should it describe steps, sources, scope, budgets, or something else?* All four, but only
  the first three are read by anything in this phase. `max_results_override` and
  `acceptance_config_override` exist as fields from day one (so a later phase adding a
  budget-aware retry doesn't need a schema migration) but stay `None` and unread until a
  phase that actually needs them — the same "carried, not yet consumed" pattern
  `CategoryRequirement.priority_tier` and `ContextPlan.max_gap_retries` already established.
- *How does it relate to `ContextPlan` without duplicating information?* `derive_retrieval_plan`
  reads exactly two fields off `ContextPlan` — `query`, `scope_concept_id` — and denormalizes
  `query` onto `RetrievalPlan` for standalone readability. It never reads or copies
  `requirements`; category vocabulary stays `ContextPlan`'s alone. This is the same
  "read only what you need, never re-derive" discipline `analyze_coverage()` already applies
  to `ContextPlan.requirements` and `decide_gap_recovery()` applies to `CoverageReport`.

---

## 4. Immutability

**Immutable — a frozen dataclass, exactly like `ContextPlan`, `CoverageReport`, and
`GapRecoveryDecision` already are.** `RetrievalPlan` is *not* the object that grows when a
retry happens.

This directly reuses `CONTEXT_PLAN_OBJECT.md` §7's already-settled reasoning (Approach A:
"immutable plan, evolving execution trace," rejecting a self-editing plan for the same
auditability reason `AcceptanceStage` produces one `AcceptanceDecision` per candidate rather
than a mutable filter) — generalized from "per-category retry" to "per-step retry." Concretely:

- `RetrievalPlan.steps` is fixed at construction and never appended to.
- A future gap-recovery retry does not produce a new `RetrievalPlan` with two steps, and does
  not mutate the existing one. It produces one additional `RetrievalStep`, executed and
  recorded directly into the *trace* (§5/§7) — the plan an inspector points at answers "what
  was decided before retrieval began," and that answer does not change just because retrieval
  later found a gap.

---

## 5. Should it become part of `RetrievalTrace` immediately? Yes — observational only.

Following the exact recipe `ContextPlanTrace`/`CoverageReportTrace`/`GapRecoveryTrace` already
established three times over (`retrieval_models.py:739-1093`):

```python
# obsidian/ontology/retrieval_models.py — additive only

@dataclass(frozen=True)
class RetrievalStepTrace:
    query: str
    source: str                       # RetrievalSource.value
    scope_concept_id: Optional[UUID]
    purpose: str                      # StepPurpose.value
    target_category: Optional[str]    # ContextCategory.value, or None

@dataclass(frozen=True)
class RetrievalPlanTrace:
    steps: Tuple[RetrievalStepTrace, ...] = ()
```

`RetrievalTrace` gains one new field: `retrieval_plan: Optional[RetrievalPlanTrace] = None`,
populated by a new `_retrieval_plan_trace()` projector in `engine.py`, called from
`query_with_trace` the same way `_context_plan_trace`/`_coverage_report_trace`/
`_gap_recovery_trace` already are. The one-directional dependency is preserved: these two new
`retrieval_models.py` classes hold only `str`/`UUID`/`Optional[str]` fields, never importing
`RetrievalSource`/`StepPurpose`/`ContextCategory` from `obsidian.memory_engine`.

Doing this immediately, not deferred to a later phase, is deliberate: in Phase 1 (§6), the
plan always has exactly one `PRIMARY` `HYBRID` step, so `RetrievalTrace.retrieval_plan` is
trivially, mechanically verifiable against `RetrievalTrace.pipeline_stats` and
`RetrievalTrace.candidates` on every single query in the existing test suite — "did this run
exactly the one step it said it would" is a free correctness check the moment the field
exists, and the exact mechanism the Retrieval Inspector will need once a second step (a real
retry) exists later.

---

## 6. Interaction with existing components — how this ships with zero behavior change

**Phase 1 (this document's recommended scope) never touches retrieval execution.**
`derive_retrieval_plan()` is called and its output attached to the trace; `MemoryEngine`
keeps calling `self._candidate_retriever.retrieve_with_diagnostics(one_query)` directly,
exactly as it does on `engine.py:687-701` today. Zero-behavior-change holds trivially: nothing
that produces the returned context string changes; the new call is pure, side-effect-free,
and its output is only ever attached to a trace field nothing reads back — the identical
guarantee every prior phase (1.5, 2, 4) already relies on.

```
raw query
    │
    ▼
ContextPlanner.plan(raw_query)   ── unchanged
    │
    ▼
ContextPlan
    │
    ├──────────────────────────────┐
    ▼                               ▼
derive_retrieval_plan(context_plan)   HybridCandidateRetriever.retrieve_with_diagnostics(raw_query)
    │ (NEW — attached to trace only)     │ (UNCHANGED — this is still what actually runs)
    ▼                                    ▼
RetrievalPlan  ──► RetrievalPlanTrace    Candidate[]
    (diagnostics only, not read back)        │
                                              ▼
                                    ... rest of pipeline, unchanged ...
```

**A later phase (explicitly out of this document's implementation scope, but worth naming so
the Phase-1 shape doesn't have to be revisited) would make `plan.steps[0]` the actual thing
executed** — replacing the direct `retrieve_with_diagnostics(raw_query)` call with something
like:

```python
def _execute_step(self, step: RetrievalStep, now: datetime) -> Tuple[List[Candidate], RetrievalProvenance]:
    if step.source is not RetrievalSource.HYBRID:
        raise NotImplementedError(f"RetrievalSource.{step.source.name} has no retriever yet")
    return self._candidate_retriever.retrieve_with_diagnostics(step.query)
```

Because Phase 1's `derive_retrieval_plan()` guarantees `plan.steps == (RetrievalStep(query=raw_query, source=HYBRID, scope_concept_id=context_plan.scope_concept_id),)`
for every input, `self._execute_step(plan.steps[0], now)` is provably identical to
`self._candidate_retriever.retrieve_with_diagnostics(raw_query)` — this swap changes *where*
code lives, not what it computes, the same "behavior-preserving refactor, verified by the
existing test suite staying green" standard `CONTEXT_PLAN_OBJECT.md` §8 already set for its
own (never-built) `_retrieve_and_rank()` factor. Raising `NotImplementedError` for any
non-`HYBRID` source rather than silently falling back to `HYBRID` is a deliberate choice: a
misconfigured or half-implemented `RetrievalStep` should fail loudly, matching
`AcceptanceStage`'s existing "no silent behavior change" posture, rather than quietly
retrieving something other than what the plan said.

**`_allocate` is explicitly not touched by this design.** It remains planner-agnostic today
(see its own docstring, `engine.py:828-839`) and stays that way here — `derive_retrieval_plan`
is only ever called from `query_with_trace`'s diagnostics path in Phase 1. Extending
`_allocate` to be plan-aware is a separate decision this document does not make.

---

## 7. How this enables a future bounded retry without special-casing `MemoryEngine`

This is the abstraction's actual payoff, and the reason it's worth building now even though
"implement retries" is explicitly out of scope for this task.

Today, `GapRecoveryDecision.should_retry=True` has nowhere to go. Without `RetrievalPlan`,
wiring it up would mean `MemoryEngine` growing a bespoke `if gap_recovery.should_retry: ...`
branch that calls `retrieve_with_diagnostics` a second time with hand-assembled arguments —
exactly the kind of one-off special case the project's "additive, explainable" philosophy
argues against, and exactly the risk `CONTEXT_PLAN_OBJECT.md` §8 flagged for the per-category
loop it designed (a second/third independently-maintained copy of the retrieval prefix).

With `RetrievalPlan` and `_execute_step` (§6) in place, a future retry phase's *entire*
implementation is: build one more `RetrievalStep`, execute it through the same helper every
other step already goes through, and merge its output using `_merge_candidates`
(`engine.py:412-446`, already deduplication-by-id and evidence-aware — no new merge logic
needed):

```python
# Illustrative only — not part of this document's implementation scope.
if gap_recovery_decision.should_retry:
    retry_step = RetrievalStep(
        query=context_plan.query,          # unchanged in this sketch; a smarter version
                                            # could synthesize a narrower sub-query per
                                            # gap_recovery_decision.missing_categories[0]
        source=RetrievalSource.HYBRID,
        scope_concept_id=context_plan.scope_concept_id,
        purpose=StepPurpose.GAP_RECOVERY,
        target_category=gap_recovery_decision.missing_categories[0],
    )
    retry_candidates, retry_provenance = self._execute_step(retry_step, now)
    candidates = _merge_candidates([candidates, retry_candidates])
    # ... re-run ranking/category-preference/acceptance over the merged set;
    # record retry_step on the trace as a second RetrievalStepTrace entry.
```

No new `MemoryEngine` method, no new merge algorithm, no new "is this a retry" boolean
threaded through unrelated code — `_execute_step` and `_merge_candidates` don't know or care
whether the step they're given is the plan's original step or a gap-recovery addition. The
retry step is recorded on the *trace* (as a second `RetrievalStepTrace`, appended to
`RetrievalTrace.retrieval_plan.steps` at execution time — note this is the trace's own tuple,
not `RetrievalPlan.steps` itself, preserving §4's immutability guarantee), not folded back
into the immutable `RetrievalPlan` that was decided before retrieval began. `plan.max_gap_retries`
(already present on the conceptual `ContextPlan` design, capped at 1 by
`gap_recovery.DEFAULT_RETRY_BUDGET`) bounds this to exactly one additional step, never a loop.

---

## 8. Execution examples

**Example A — today's common case (`POINTED_QA`), Phase 1 behavior.**
Query: `"what database do I use"`. `ContextPlan.requirements == ()`.
`derive_retrieval_plan` still produces one `PRIMARY` `HYBRID` step (it does not special-case
an empty-requirements plan — there's nothing to special-case, since it never reads
`requirements` at all). `RetrievalTrace.retrieval_plan.steps` has length 1. Nothing about the
returned context string changes from before this document's Phase 1 lands.

**Example B — `CODING_DEBUGGING` query, Phase 3 already active.**
Query: `"debug the ranking failure"`. `ContextPlan.requirements` includes `CONSTRAINT`,
`IMPLEMENTATION_STATE`, etc. `derive_retrieval_plan` *still* produces exactly one step — it
is not the layer that makes categories matter; `CategoryPreferenceScorer` (unchanged,
downstream of the one retrieval call) is. `RetrievalPlan` and category-awareness coexist
without either duplicating the other: the plan says "one hybrid pass over this scope," the
scorer says "and prefer these categories once results come back."

**Example C — a hypothetical future retry (illustrative, not built by this document).**
Same query as B. Suppose `CONSTRAINT` comes back `MISSING` in `CoverageReport`, and
`GapRecoveryDecision.should_retry=True`. A future phase appends one `GAP_RECOVERY` step
targeting `CONSTRAINT`, executes it through the unchanged `_execute_step`, merges its
candidates via the unchanged `_merge_candidates`, and records two `RetrievalStepTrace`
entries on the trace instead of one. Ranking, category preference, and acceptance re-run once
over the merged set — no change to any of those stages' own logic.

**Example D — a hypothetical future `PROJECT_STATE` source (illustrative, not built).**
A query classified as needing `IMPLEMENTATION_STATE`/`CODE_AREA` heavily could, in a future
phase, get a plan whose single step carries `source=PROJECT_STATE` instead of `HYBRID`.
`_execute_step` would dispatch to a not-yet-written `ProjectStateRetriever` instead of
`HybridCandidateRetriever`. Until that retriever exists, `_execute_step` raises
`NotImplementedError` rather than silently substituting `HYBRID` — see §6.

---

## 9. Trade-offs

- **Deliberately does not express `ContextPlan.requirements` as retrieval steps.** The
  original `CONTEXT_PLAN_OBJECT.md` design mapped one `RetrievalStep` per `CategoryRequirement`
  (up to 5 for `CONTINUATION`). This document rejects that mapping for Phase 1: it was never
  built in the two years since it was designed, the codebase instead shipped a working,
  cheaper alternative (`CategoryPreferenceScorer`) that gets most of the same benefit without
  N retrieval calls, and building the N-step version now would immediately require solving
  the `_retrieve_and_rank`/`_allocate` duplication problem (§0) that has sat unresolved this
  whole time. Trade-off accepted: this design is smaller and less capable than the original
  sketch, on purpose — it only earns back that capability if a future phase actually needs
  more than one step, which today only gap recovery does, and gap recovery needs at most one
  extra step, not five.
- **`max_results_override`/`acceptance_config_override` are speculative fields, unread until
  a phase that needs them.** This mirrors `PriorityTier`/`max_gap_retries`'s already-accepted
  pattern in this codebase, but it is still a real cost: a reviewer reading `RetrievalStep`
  today sees two fields nothing exercises. Accepted because the alternative — adding them
  later, when the first real consumer needs them — means every existing serialized
  `RetrievalStep` predating that change needs a schema-compatible default, which `Optional[...] = None`
  already guarantees either way; there's no cost saved by deferring the fields, only a
  presentation cost paid now instead of later.
- **Relationship to `QueryRewriter` is left unresolved, not reconciled.** `QueryRewriter`
  already turns one query into up to three retrieval calls (original + 2 rewrites),
  independently of `RetrievalPlan`. This document does not fold rewriting into
  `RetrievalPlan.steps` — doing so would mean `derive_retrieval_plan` needing to call
  `QueryRewriter` (an LLM-backed, fail-open component) to produce a plan, which conflicts with
  "deterministic first" and with `ContextPlanner`'s own explicit "no LLM calls" scoping.
  The result is two independent "one query becomes several retrieval calls" mechanisms
  existing side by side (rewriting expands the *query text*; `RetrievalPlan` will expand the
  *steps*, e.g. for retries or multiple sources). This is a real seam, not a design that
  cleanly subsumes the older mechanism — flagged here rather than silently left for someone
  to discover later.

---

## 10. Risks

- **Speculative-generality risk.** `RetrievalSource` names five reserved values
  (`KEYWORD`, `ONTOLOGY`, `SEMANTIC`, `PROJECT_STATE`, `ACTIVE_CONTEXT`) that nothing in this
  codebase implements. If none of them is ever built, this enum permanently carries dead
  weight. Mitigated the same way `PlanningMethod.LLM_FALLBACK` already is: each reserved value
  costs one enum line and is otherwise inert — cheap to carry, cheap to remove if a future
  audit decides one was never going to happen.
- **A second "plan" object risks confusing an inspector about which one to trust.** With both
  `ContextPlan` and `RetrievalPlan` on the trace, "why did retrieval do X" now has two places
  to look. Mitigated by strict separation of question: `ContextPlan` is read to answer "what
  did the planner think this query needed"; `RetrievalPlan` is read to answer "what did
  retrieval actually run" — as long as `RetrievalPlan` never grows a `requirements`-shaped
  field of its own (§3's datamodel deliberately has none), the two stay answerable
  independently rather than becoming two competing sources of truth for the same question.
- **This document's Phase 1 delivers no user-visible improvement by itself.** See §11 —
  named here as a risk because it means the value of building this now is entirely deferred to
  whether a later phase (gap recovery, a new source) actually gets built. If that later phase
  never lands, Phase 1 was pure, permanent plumbing cost with zero payoff. This is the central
  question the recommendation in §11 has to answer honestly.
- **`_execute_step`'s `NotImplementedError` for non-`HYBRID` sources is a live footgun once
  more sources exist.** If a future `ContextPlanner` extension starts emitting
  `RetrievalStep(source=PROJECT_STATE, ...)` before `_execute_step` has a real
  `ProjectStateRetriever` to dispatch to, every such query now hard-fails instead of merely
  degrading. This is intentional (§6) but must be paired, whenever a new source is introduced
  on the *planning* side, with the corresponding *execution* side landing in the same change —
  never split across two separate PRs.

---

## 11. Compatibility analysis

| Surface | Effect |
|---|---|
| `ContextPlan` | Untouched. `derive_retrieval_plan` reads it, never modifies its shape. |
| `MemoryEngine.query()` / `query_with_trace()` return value | Byte-identical in Phase 1 (§6) — the new call is diagnostics-only, attached to a trace field nothing reads back, exactly the standard Phases 1.5/2/4 already met. |
| `RetrievalTrace` | Gains one new `Optional[RetrievalPlanTrace] = None` field. Existing serialized traces missing this key deserialize with `retrieval_plan=None` via `from_dict`'s existing `data.get(...)` pattern — no migration needed, matching how `context_plan`/`coverage`/`gap_recovery` were each added without breaking older payloads. |
| `_allocate`, `query_working_context`, `query_structured` | Untouched — this design is not wired into that call path (§6). |
| `obsidian.ontology.retrieval_models` | Gains two new plain-primitive dataclasses (`RetrievalStepTrace`, `RetrievalPlanTrace`), following the exact `ContextPlanTrace`/`CoverageReportTrace`/`GapRecoveryTrace` precedent already three-for-three in this file. No existing class in this module changes shape. |
| Dependency direction | `obsidian/memory_engine/retrieval_plan.py` depends on `context_planner` (for `ContextPlan`), `acceptance_stage` (for the `AcceptanceConfig` override type), and `retrieval_config`/`retrieval_models` — the same direction every existing `memory_engine` module already depends in. `obsidian.ontology` gains no new dependency on `obsidian.memory_engine`. |
| Test suite | Every existing test in `obsidian/tests/` continues to exercise byte-identical `MemoryEngine` output; a new, additive `test_retrieval_plan.py` (mirroring `test_context_planner.py`, `test_coverage_analyzer.py`, `test_gap_recovery.py`) is the only new test surface this phase requires. |

---

## 12. Phased implementation roadmap

| Phase | Scope | Behavior change | Effort |
|---|---|---|---|
| **A — Introduce the object** | `RetrievalSource`, `StepPurpose`, `RetrievalStep`, `RetrievalPlan`, `derive_retrieval_plan()` in a new `retrieval_plan.py`, with `to_dict`/`from_dict` following the existing pattern. Unit-tested standalone against `ContextPlan` inputs — no `MemoryEngine` change at all. | None — the module isn't imported by anything yet. | Small (~0.5 day) — mechanical, same shape as every existing frozen-dataclass module. |
| **B — Attach to the trace, diagnostics only** | `RetrievalStepTrace`/`RetrievalPlanTrace` added to `retrieval_models.py`; `_retrieval_plan_trace()` added to `engine.py`; `query_with_trace` calls `derive_retrieval_plan(context_plan)` and attaches the projection to `RetrievalTrace.retrieval_plan`. Exactly the Phase-1.5/2/4 recipe. | None — output attached to a field nothing reads back (§6). | Small (~0.5 day). |
| **C — Execution unification (behavior-preserving refactor)** | Introduce `MemoryEngine._execute_step()`; replace the direct `retrieve_with_diagnostics(raw_query)` call inside `query_with_trace` with `_execute_step(plan.steps[0], now)`. Verified behavior-preserving by the existing test suite staying green, since Phase A/B already guarantee `plan.steps[0]` is exactly `(query=raw_query, source=HYBRID, scope_concept_id=context_plan.scope_concept_id)`. | None, by construction — this is the swap described and proven identical in §6. | Small–Medium (~1 day) — the risk is entirely in verification, not new logic. |
| **D — Multi-step loop shape** | `query_with_trace` iterates `for step in plan.steps: ...`, merging via the existing `_merge_candidates`, still executing exactly one step per query (Phase A never produces more than one). This phase exists only to make "add a second step" additive rather than a rewrite when Phase E needs it. | None — `plan.steps` still has length 1 for every input. | Small (~0.5 day). |
| **E — Bounded gap-recovery retry** *(explicitly out of this task's scope; named for completeness)* | Consume `GapRecoveryDecision.should_retry`: build and execute one `GAP_RECOVERY` `RetrievalStep` per §7, bounded by the existing `retry_budget`/`DEFAULT_RETRY_BUDGET`. First phase with an actual, user-visible behavior change (may improve recall for queries with a real coverage gap). | **Yes, intentionally** — this is the payoff phase. | Medium — mostly re-running ranking/acceptance/category-preference over a merged, larger candidate set; the retrieval-step mechanics themselves are already in place from A–D. |
| **F — New `RetrievalSource` values** *(future, unscheduled)* | Implement `ProjectStateRetriever`/`SemanticRetriever`/etc. behind `_execute_step`'s dispatch, one at a time, each pairing a planning-side change (`ContextPlanner` or a future planner emitting the new source) with the execution-side retriever in the same change (§10's mitigation). | Yes, once a new source lands. | Varies per source; `SEMANTIC` requires an embedder that doesn't exist anywhere in this codebase today and is the largest of these. |

Phases A–D are this document's recommended immediate scope: **~2.5 days total**, every step
individually zero-behavior-change and independently verifiable, matching exactly how Phases
1.5/2/3/4 were each shipped as small, provably-safe increments.

---

## 13. Recommendation

**Build Phases A–D now. Do not build Phase E (retries) yet — matching the explicit
constraint this task was given.**

The honest case against building this at all: Phases A–D produce zero user-visible value on
their own. `RetrievalPlan` with exactly one step, always equal to what already runs today, is
pure plumbing — an inspector gains one more (currently trivial) trace field, and that's the
entire observable effect. If the goal were "what single change most improves Haven's answers
right now," the higher-leverage move is Phase E directly: `GapRecoveryDecision.should_retry`
has been computed and silently discarded since Phase 4, and wiring it up would be the first
change since Phase 3 to plausibly improve recall on real queries.

But that comparison isn't the one this task poses. Given the explicit instruction that retries
are not being implemented yet, the choice is between (a) building this abstraction now, ahead
of its first consumer, or (b) waiting until gap recovery is actually greenlit and building
`RetrievalPlan` and the retry loop together at that point. **(a) is the better sequencing**,
for a reason specific to this codebase's own history: `CONTEXT_PLAN_OBJECT.md` already tried
option (b)'s shape once — it designed the per-category retrieval loop and the object to carry
it together, as one large speculative unit — and none of it got built, because "design the
plan object and the N-step execution loop simultaneously" is a much bigger unit of work than
either half alone. Splitting `RetrievalPlan` out now, scoped strictly to the always-one-step
case, is small (~2.5 days), fully verifiable against the existing test suite, and removes the
one real blocker (no place for a retry step to live, no unified execution path to run it
through) standing between `GapRecoveryDecision` and actually mattering — without committing to
the retry work itself, or to any of the speculative new sources, today.

If, instead, the team's real priority is "make Haven's retrieval measurably better soon"
rather than "prepare the ground for that," the higher-value alternative to this whole document
is **skip straight to a scoped Phase E** — a single, narrow "if a REQUIRED category is missing
and the plan is confident, run one more hybrid pass over just that category's likely
`MemoryType`s and merge it in" — accepting the small amount of bespoke wiring §7 argues
against, in exchange for shipping the actual improvement sooner. That is a legitimate
alternative and should be the question asked explicitly before starting Phase A: **is the
goal architectural readiness for a retry that's coming soon, or is it the retry itself?** This
document answers the former question well; it does not by itself deliver the latter.
