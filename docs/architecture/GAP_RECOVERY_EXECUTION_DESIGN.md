# Gap Recovery Execution — Design

Status: **Architecture only. No code changed.** This document specifies the smallest, safest
way to make `GapRecoveryDecision` (`obsidian/memory_engine/gap_recovery.py`, Phase 4) actually
*do* something, instead of only being attached to `RetrievalTrace.gap_recovery` for
diagnostics. It deliberately does **not** build `RetrievalPlan`/`RetrievalStep`
(`docs/architecture/CONTEXT_PLAN_OBJECT.md` §4/§8's N-per-category execution loop) — that
remains a separate, larger, not-yet-justified investment. Every claim about current code was
checked against source on disk as of 2026-07-09.

The flow this document designs:

```
Retrieve → Coverage → GapRecoveryDecision → (optional ONE retry) → Merge → Rank → Accept
```

---

## 0. What already exists

Confirmed directly against source:

- `MemoryEngine.query_with_trace` (`obsidian/memory_engine/engine.py:581-810`) already runs, in
  order: `ContextPlanner.plan` → per-query `HybridCandidateRetriever.retrieve_with_diagnostics`
  (once per entry in `queries`, which is `(raw_query,)` unless a `QueryRewriter` is configured)
  → `_merge_candidates` → `_active_candidates` → `DeterministicRanker.score_all` →
  `CategoryPreferenceScorer.score` → `AcceptanceStage.accept` → `DeterministicSlotAllocator.allocate`
  → `ContextBuilder.build` → `analyze_coverage` → `decide_gap_recovery`. Every one of these
  values (`context_plan`, `candidate_lists`, `candidates`, `ranked_all`, `decisions`,
  `accepted_candidates`, `allocated`, `now`, `coverage_report`, `gap_recovery_decision`) is a
  local variable already in scope in this one method, by the time `gap_recovery_decision` is
  computed at line 791.
- `_merge_candidates` (`engine.py:412-446`) already implements exactly the dedup-by-id,
  ontology-evidence-wins, sorted-by-id merge a retry needs — it is not specific to the
  `QueryRewriter` path, it is a generic `List[List[Candidate]] -> List[Candidate]` reducer that
  already gets called with a variable number of per-query candidate lists.
- `HybridCandidateRetriever.retrieve`/`retrieve_with_diagnostics` (`hybrid_candidate_retriever.py`)
  is a pure, deterministic function of the query *string* alone (plus the fixed
  `alias_index`/`concept_graph`/`memory_store`/`config` it was constructed with). Calling it
  twice with the *same* string returns byte-identical output — see its own "Determinism"
  section. This has a direct, load-bearing consequence for §5 below.
- `AcceptanceStage.accept` (`acceptance_stage.py`) takes the *full* unfiltered `ranked_all` list
  every time — its gap-cut, relative-floor, and abstention logic are only meaningful over one
  coherent, fully-populated ranking. There is no supported way to "add a few more accepted
  candidates" to an already-decided `AcceptanceDecision` list after the fact; the only correct
  way to change acceptance's answer is to re-run `accept()` over a differently-populated
  `ranked_all`.
- `CONTEXT_PLAN_OBJECT.md` §8 already anticipated this exact gap (its own "Status note (Phase
  3)" calls out that `RetrievalStep`/`GapDetector`/bounded-retry machinery is "still designs
  from scratch"), and §7 already settled the relevant precedent: the *plan* never mutates:
  only the *execution record* around it is allowed to grow, capped, non-iterative.

---

## 1. Q1 — Where is the cleanest insertion point for executing a retry?

**Inside `MemoryEngine.query_with_trace`, immediately after the existing
`gap_recovery_decision = decide_gap_recovery(context_plan, coverage_report)` line, before trace
assembly.** Nowhere else has every value a retry needs already computed and in scope:
`context_plan` (for the missing categories and confidence), `candidate_lists`/`candidates` (the
first pass's retrieval output, to merge against), `now` (for a deterministic second ranking
pass), and the same collaborator instances (`self._candidate_retriever`, `self._ranker`,
`self._category_preference_scorer`, `self._acceptance_stage`, `self._slot_allocator`,
`self._context_builder`) already constructed in `__init__`.

This is also the *only* method that currently reads `GapRecoveryDecision` at all — `query`,
`_allocate`, `query_working_context`, and `query_structured` never build one (per `_allocate`'s
own docstring: "Deliberately not planner-aware"). Executing the retry here, and nowhere else,
keeps that boundary intact: `query`/`query_working_context`/`query_structured` gain gap recovery
automatically (since `query` is defined as `query_with_trace(...)[0]`), while `_allocate` and its
two callers remain completely unaffected — no change to their code, their behavior, or their
"deliberately not planner-aware" contract.

---

## 2. Q2 — Can this be implemented without restructuring `MemoryEngine`?

**Yes.** No existing method signature needs to change:

- `HybridCandidateRetriever.retrieve_with_diagnostics(query: str)` is called again with a new
  query string — same call shape already used in the `for one_query in queries` loop.
- `DeterministicRanker.score_all(candidates, config, now=now)` is called again over the
  *merged* (original ∪ retry) candidate list — same call shape, same `now`.
- `CategoryPreferenceScorer.score(ranked_all, context_plan)`, `AcceptanceStage.accept(...)`,
  `DeterministicSlotAllocator.allocate(...)`, `ContextBuilder.build(...)` are each called again,
  unmodified, over the enlarged input.

The only new code is a conditional branch inside `query_with_trace` — `if
gap_recovery_decision.should_retry: ...` — that repeats a *subset* of steps `query_with_trace`
already performs, over a different input list. This is the same shape multi-query expansion
already uses (call `retrieve_with_diagnostics` more than once per request, merge, then run
ranking/acceptance/allocation exactly once over the union) — gap recovery is architecturally "one
more query, triggered by coverage instead of by `QueryRewriter`, running after acceptance instead
of before it."

**One local refactor is worth doing, but it is narrow.** Repeating
rank→category-preference→accept→allocate→build verbatim inside the `if should_retry` branch
would duplicate ~20 lines already in `query_with_trace`. Factor *only* that suffix into a private
helper, e.g. `MemoryEngine._rank_accept_allocate(candidates, context_plan, now) -> tuple[allocated,
decisions, ranked_all]`, called once for the first pass and once more inside the retry branch.
This is **not** the `_retrieve_and_rank` refactor `CONTEXT_PLAN_OBJECT.md` §8 describes (which
also folds in retrieval + merge + validity-filtering and is meant to be shared with `_allocate`)
— it is strictly smaller, touches only `query_with_trace`, and leaves `_allocate`,
`query_working_context`, and `query_structured` byte-identical. If even this is judged
unnecessary churn, the alternative is accepting the ~20-line duplication once, inside one
`if` branch, in one method — still zero restructuring of `MemoryEngine` itself.

---

## 3. Q3 — How should retry candidate merging work?

**Reuse `_merge_candidates` verbatim, at the `Candidate` level, before re-ranking.** Concretely:
append the retry pass's `List[Candidate]` to the existing `candidate_lists` (the same list of
per-query candidate lists `query_with_trace` already accumulates), call `_merge_candidates` again
over the extended list, then re-run `_active_candidates` → `score_all` → `CategoryPreferenceScorer`
→ `AcceptanceStage` → `DeterministicSlotAllocator` → `ContextBuilder` exactly as the first pass
did, on this new merged set.

This means the retry never produces a second, separately-accepted candidate set that has to be
reconciled with the first — there is exactly one acceptance decision per `KnowledgeObject.id`,
computed once, over the full post-retry candidate pool. That is what keeps `AcceptanceStage`'s
gap-cut and relative-floor logic (which assume one coherent ranking) correct; splicing
post-hoc "rescued" candidates into an already-decided accepted list would silently violate both.

## 4. Q4 — Should duplicate candidates be merged before ranking or after?

**Before.** Ranking, category-preference scoring, and acceptance are all only meaningful over one
consistent list of `RankedCandidate` — `AcceptanceStage`'s relative floor is `top_score *
relative_floor_ratio`, and `top_score` isn't stable if it's computed twice over two different
partial candidate sets and then reconciled. Merging `Candidate` lists first, then running
ranking/acceptance/allocation exactly once over the union, is also exactly what the existing
multi-query-rewrite path already does (`query_with_trace`'s `queries` loop merges before calling
`self._ranker.score_all` even once) — gap recovery reuses that same ordering rather than
inventing a second one.

---

## 5. Q5 — Should the retry search use the original query or derive a category-specific query?

**Derive a category-specific query. Reusing the identical `raw_query` is a no-op and must not be
the design.**

This follows directly from §0's determinism point: `HybridCandidateRetriever.retrieve` is a pure
function of the query string plus the (unchanged) `alias_index`/`concept_graph`/`memory_store`.
Calling it a second time with the exact same `raw_query` returns byte-identical `Candidate`s —
nothing new can appear in `candidate_lists`, `_merge_candidates` has nothing new to merge, and the
retry changes nothing. A retry is only capable of "recovering" a gap if it searches with
different text than the first pass already tried.

**Recommendation: a small, fixed `ContextCategory -> Tuple[str, ...]` keyword table**, following
the exact convention `_MODE_PATTERNS` (`context_planner.py`) and `MEMORY_TYPE_CATEGORY`
(`coverage_analyzer.py`) already establish (a hardcoded, editable, deterministic Python literal —
not learned, not LLM-authored):

```python
_CATEGORY_RETRY_KEYWORDS: Dict[ContextCategory, Tuple[str, ...]] = {
    ContextCategory.CONSTRAINT: ("constraint", "rule", "must not", "never"),
    ContextCategory.BLOCKER: ("blocker", "blocked", "stuck"),
    ContextCategory.OPEN_QUESTION: ("open question", "unresolved", "unclear"),
    ContextCategory.IMPLEMENTATION_STATE: ("implemented", "in progress", "status"),
    ContextCategory.CODE_AREA: ("file", "module", "code"),
    ContextCategory.DECISION: ("decided", "decision"),
    ContextCategory.TASK: ("task", "todo"),
    ContextCategory.RESEARCH: ("finding", "learned"),
    ContextCategory.BELIEF: ("belief", "opinion"),
}
```

The retry query is `raw_query + " " + " ".join(keywords for every category in
gap_recovery_decision.missing_categories)` — **one derived string covering every missing
category**, not one retry per category (see §7: the budget is one retry *total*, not one per
gap). This widens the keyword path's exact-token match (`KeywordCandidateRetriever`) toward
vocabulary the original phrasing didn't use, and, as a side effect, may resolve additional
ontology seeds via `QueryResolver`'s alias-index lookup if a category keyword happens to be an
alias. Both paths are exercised via the same, unmodified
`HybridCandidateRetriever.retrieve_with_diagnostics(retry_query)` call already used for every
other query in the pipeline — no new retrieval code.

An alternative — deriving the retry query from `context_plan.task_mode` or `scope_concept_id`
instead of the missing categories — was considered and rejected: `task_mode` is already fully
captured by which categories ended up `REQUIRED` (the retry only needs to know what's *missing*,
not *why* the plan wanted it), and `scope_concept_id` is `None` for every plan produced today
(Phase 1 never resolves one), so there is nothing to key off there yet.

---

## 6. Q6 — How can retries stay deterministic?

Every piece involved already is:

1. `_CATEGORY_RETRY_KEYWORDS` is a fixed table (no randomness, no clock, no I/O).
2. Building `retry_query` is a pure string join over `gap_recovery_decision.missing_categories`,
   which is itself already an ordered tuple (`CoverageReport.missing_required_categories`
   preserves plan declaration order — see `coverage_analyzer.py`'s `CoverageReport` docstring).
3. `HybridCandidateRetriever.retrieve_with_diagnostics` is independently deterministic given a
   fixed query string (module docstring, "Determinism").
4. `_merge_candidates` sorts by `str(knowledge_object.id)`, independent of dict/set iteration
   order or which pass found a duplicate first (module docstring, "Merge semantics").
5. `DeterministicRanker.score_all` must be called with the **same `now`** already computed once
   at the top of `query_with_trace` (`now = datetime.utcnow()`, line 664) — reusing that same
   local variable for the retry's re-ranking call (rather than reading the clock again) is what
   keeps recency scoring identical between what the retry *would* have computed for the first
   pass's candidates and what it actually computes when re-scoring the union. This is a one-line
   discipline (pass `now=now`, not `now=None`), not a new mechanism.
6. `AcceptanceStage.accept`, `DeterministicSlotAllocator.allocate`, `ContextBuilder.build` are
   already pure functions of their inputs.

The same `context_plan`, `coverage_report`, and first-pass `candidate_lists` always produce the
same `retry_query`, the same merged candidate set, and therefore the same final accepted/allocated
result. No LLM call anywhere in this path.

---

## 7. Q7 — How do we guarantee exactly one retry maximum?

Two independent guarantees, not one:

1. **`GapRecoveryDecision` itself is computed exactly once per `query_with_trace` call** (from
   the first pass's `coverage_report` alone) — there is only ever one decision to act on. Do
   **not** re-run `decide_gap_recovery` after the retry executes. The retry's own resulting
   coverage may be computed for diagnostics (see §8), but it must never feed back into a second
   `decide_gap_recovery` call — that would turn a bounded, one-shot design into an implicit loop.
2. **The retry executes at most one additional `HybridCandidateRetriever.retrieve_with_diagnostics`
   call, regardless of how many categories are in `missing_categories`.** §5 already folds every
   missing category into a single derived query string precisely so "one retry" means one retry —
   not one retrieval call per missing category (which is what the full `RetrievalStep`-per-category
   loop in `CONTEXT_PLAN_OBJECT.md` §8 would do, and which this document explicitly does not
   build). `GapRecoveryDecision.retry_budget` (fixed at `DEFAULT_RETRY_BUDGET = 1`,
   `gap_recovery.py:134`) is honored as "1 extra retrieval call for this query," not "1 call per
   category."

The guard in code is a single `if gap_recovery_decision.should_retry:` branch with no loop inside
it — structurally identical to `RetryReason`'s own enum already only ever producing
`should_retry=True` from one narrow rule (§3 of `gap_recovery.py`'s docstring).

---

## 8. Q8 — How should `RetrievalTrace` expose the first pass and second pass?

Two additive changes, both following the existing "plain-primitive projection, populated by
`query_with_trace`" convention `ContextPlanTrace`/`CoverageReportTrace`/`GapRecoveryTrace` already
use:

**a. Extend `GapRecoveryTrace`** (`obsidian/ontology/retrieval_models.py`) with:

```python
retry_executed: bool = False
retry_query: Optional[str] = None
candidates_added: int = 0          # new unique KnowledgeObject ids the retry contributed
categories_recovered: Tuple[str, ...] = ()   # originally-missing categories now satisfied
```

`retry_executed` is `False` whenever `should_retry` was `False` (nothing to execute) — but it is
also allowed to be `False` even when `should_retry` was `True`, if a future safety gate declines
to execute (see §9's failure modes). This is why it's a separate field from `should_retry` rather
than reusing it: `should_retry` is the *decision*, `retry_executed` is what *actually happened*,
mirroring `AcceptanceDecision.accepted` vs. `abstained` already being two separate booleans for
two separate questions.

`categories_recovered` requires computing a **second** `CoverageReport` after the retry's
candidates are folded in and re-accepted — comparing it against the first pass's
`missing_required_categories` is diagnostic-only arithmetic (`set(before) - set(after's
still-missing)`), never fed back into a second `decide_gap_recovery` call (§7).

**b. Extend `CandidateTrace`** with one new field, `found_by_retry: bool = False`, mirroring the
existing `matched_by_keyword`/`matched_by_ontology` booleans — `True` for any candidate whose
`KnowledgeObject.id` appeared in the retry pass's `Candidate` list but not the first pass's. This
lets the Retrieval Inspector answer "was this specific memory surfaced by the gap-recovery retry"
per-candidate, not just in aggregate.

Both additions are pure additive fields with defaults, so every existing serialized trace
(`from_dict`) continues to round-trip unchanged — the same pattern already used for `base_score`/
`category_preference_bonus` on `CandidateTrace` when Phase 3 landed.

---

## 9. Risks

- **Query augmentation can pull in ontology noise.** Appending category keywords (e.g.
  "constraint", "blocker") to the retry query could resolve unintended `QueryResolver` alias
  matches if a keyword happens to collide with an unrelated Concept's alias, seeding activation
  spreading somewhere irrelevant. Mitigation: keep `_CATEGORY_RETRY_KEYWORDS` deliberately generic,
  vocabulary-level terms (not project-specific nouns), and rely on `AcceptanceStage`'s existing
  relative-floor/gap-cut stages to filter out any resulting low-relevance candidates — exactly the
  same defense already in place against noisy keyword-path hits today.
- **A second full rank→accept→allocate pass doubles per-request latency on the (hopefully rare)
  queries that trigger it.** Bounded and acceptable: it only fires when `should_retry` is `True`,
  which requires both a `REQUIRED` category gap *and* `confidence >= 0.5` — see §10 for how rare
  that is expected to be against the current benchmark corpus.
- **`categories_recovered` can be zero even when `retry_executed` is `True`.** If the vault
  genuinely has no memory for the missing category, no query text will surface one — the retry
  correctly finds nothing, and the category legitimately remains `MISSING`. This is expected
  behavior, not a bug in the retry: `GapRecoveryDecision`'s own docstring already states a plan
  can never "conjure" a candidate that isn't there.
- **Code duplication if the `_rank_accept_allocate` extraction (§2) is skipped.** Judged low-risk
  (one `if` branch, one method) but worth naming: the alternative to the narrow local refactor is
  ~20 duplicated lines inside `query_with_trace` itself.
- **This still doesn't address `LOW_PLAN_CONFIDENCE`.** Every plan `ContextPlanner` produces today
  has `confidence=1.0` (Phase 1 is deterministic-only — see `context_planner.py`'s own docstring),
  so `RetryReason.LOW_PLAN_CONFIDENCE` never fires against real traffic. This design's retry
  execution is therefore reachable only via `RetryReason.REQUIRED_CATEGORY_MISSING` today, exactly
  as `decide_gap_recovery`'s own docstring already documents.

---

## 10. Expected benchmark impact

**`TaskMode.POINTED_QA` queries are entirely unaffected — `requirements=()` means
`CoverageReport.entries` is empty, `missing_required_categories` is always empty, and
`decide_gap_recovery` always returns `RetryReason.NO_GAP`.** Per `CONTEXT_PLANNER_DESIGN.md` §1's
own finding (cited in `CONTEXT_PLAN_OBJECT.md` §1 goal 2), most of the existing benchmark corpus
classifies as `POINTED_QA` today, since `ContextPlanner._classify` only leaves that sentinel for
queries matching no lexical pattern in `_MODE_PATTERNS`. Expect **zero measurable change** across
the bulk of `benchmarks/datasets/*` unless a meaningful fraction of queries contain
continue/debug/plan/research-flavored phrasing.

**Where a lift is plausible:** benchmark categories whose queries are likely to classify into
`CODING_DEBUGGING`, `STRUCTURING`, or `CONTINUATION` — the three task modes whose requirement
tables include `NEVER_DROP`-tiered categories (`CONSTRAINT`, `BLOCKER`) that
`docs/architecture/PROJECT_STATE_KNOWLEDGE_MODEL.md`'s newer `MemoryType`s back
(`RULE`/`BLOCKER`/`OPEN_QUESTION`/`IMPLEMENTATION_STATE`/`CODE_AREA`) — these are, by construction,
sparser in most vaults than `FACT`/`DECISION`. Looking at `benchmarks/datasets/`'s actual
categories, the closest proxies are `open_problems` (→ `OPEN_QUESTION`), `mistake_prevention` and
`decisions` (→ `CONSTRAINT`/`DECISION`), and `active_context` (→ `CONTINUATION`-shaped, multiple
categories at once). None of these benchmark categories map 1:1 onto `ContextCategory` — they were
designed to test different things (consolidation, temporal contradiction, multi-hop recall) — so
this is a plausibility argument, not a measured prediction.

**Realistic expectation: a small, narrow win, not a broad quality jump.** `overall_coverage_percentage`
should improve only on the minority of queries that (a) classify into a non-`POINTED_QA` mode,
*and* (b) already had `should_retry=True` under today's (purely diagnostic) `GapRecoveryDecision` —
i.e., queries that already show a real, confident, unsatisfied `REQUIRED` category gap in current
`RetrievalTrace.gap_recovery` output. Recommend measuring this directly before estimating further:
run the *existing* (already-shipped) `decide_gap_recovery` over the current benchmark corpus and
count how often `should_retry=True` fires today — that count is the retry's entire addressable
ceiling, since a retry can only ever act on cases that already reach that decision. Given `Phase
4`'s conservative-by-default design (`gap_recovery.py`'s own "Conservative by default" section),
expect that count to be a small percentage of total queries; call it single digits of percentage
points of overall benchmark accuracy/coverage, concentrated entirely in the coding/structuring/
continuation-shaped subset.

---

## 11. Implementation roadmap

| Step | Scope | Risk |
|---|---|---|
| 0. Measurement pass | Run the *existing*, already-shipped `decide_gap_recovery` over the benchmark corpus (no code change) and record how often `should_retry=True` fires, and for which categories. This sizes §10's ceiling with real numbers before building anything. | None — read-only |
| 1. `_CATEGORY_RETRY_KEYWORDS` table + `_build_retry_query` helper | New, small, pure function in `gap_recovery.py` or a new `gap_recovery_execution.py` module (following the one-module-per-stage convention). No behavior change until called. | Low |
| 2. `_rank_accept_allocate` extraction (optional, §2) | Behavior-preserving refactor of `query_with_trace`'s existing rank→category-preference→accept→allocate suffix into a private helper. Verify byte-identical output via existing tests before adding the retry branch. | Low–Medium — must be verified non-behavior-changing, same discipline `CONTEXT_PLAN_OBJECT.md` §8 already calls for on `_retrieve_and_rank` |
| 3. Retry branch in `query_with_trace` | `if gap_recovery_decision.should_retry:` → build retry query → one more `retrieve_with_diagnostics` call → merge with first-pass `candidate_lists` → re-run the (possibly-extracted) rank/accept/allocate suffix → rebuild `context`/`candidate_traces` from the new result. | Medium — this is the one behavior-changing step |
| 4. `GapRecoveryTrace`/`CandidateTrace` additive fields (§8) | `retry_executed`, `retry_query`, `candidates_added`, `categories_recovered`, `found_by_retry` | Low — purely additive, defaults preserve round-tripping |
| 5. Re-measure | Re-run the same benchmark pass from Step 0 and compare `overall_coverage_percentage`/accuracy deltas specifically on the subset that had `should_retry=True` before. | None — read-only |

---

## 12. Recommendation

**Build it, scoped exactly as this document describes — inside `query_with_trace`, one extra
retrieval call maximum, category-derived retry query, merge-before-rank, no `RetrievalPlan`.**
This closes the "nothing consumes `GapRecoveryDecision`" gap with the smallest change that can
possibly work: it adds no new collaborator class, no new pipeline stage object, no restructuring
of `MemoryEngine`, and reuses every existing stage (`HybridCandidateRetriever`,
`_merge_candidates`, `DeterministicRanker`, `CategoryPreferenceScorer`, `AcceptanceStage`,
`DeterministicSlotAllocator`, `ContextBuilder`) completely unmodified.

Do Step 0 (the measurement pass) **before** writing any of the execution code — it is zero-risk,
uses only what's already shipped, and will either confirm this is worth building now or reveal
that `should_retry=True` is rare enough in the current corpus that the win is not yet worth the
one behavior-changing step (Step 3). Do not build the full `RetrievalStep`-per-category loop
(`CONTEXT_PLAN_OBJECT.md` §4/§8) until this narrower version has been measured in production —
that remains the right "real investment," gated on this smaller step proving the mechanism works
at all.
