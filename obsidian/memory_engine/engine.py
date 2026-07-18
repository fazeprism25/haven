"""MemoryEngine — public retrieval API for Haven.

Implements Phase 8 ("MemoryEngine integration") of
``docs/architecture/ONTOLOGY_SPEC.md``: wires candidate retrieval into a
single deterministic entry point:

::

    raw query
        │
        ▼
    HybridCandidateRetriever    (obsidian.memory_engine.hybrid_candidate_retriever)
        │
        ▼
    Candidate[]                  (ontology-evidenced or zero-evidence — see below)
        │
        ▼
    DeterministicRanker         (obsidian.memory_engine.deterministic_ranker)
        │
        ▼
    AcceptanceStage             (obsidian.memory_engine.acceptance_stage)
        │
        ▼
    DeterministicSlotAllocator  (obsidian.memory_engine.deterministic_slot_allocator)
        │
        ▼
    ContextBuilder               (obsidian.memory_engine.context_builder)
        │
        ▼
    context string

When a :class:`~obsidian.memory_engine.query_rewriter.QueryRewriter` is
supplied (see "Optional multi-query expansion" below), an extra step runs
ahead of ``HybridCandidateRetriever`` and its output across every query is
merged before reaching ``DeterministicRanker``; nothing downstream of the
merge changes.

This module has exactly one responsibility: call
:class:`~obsidian.memory_engine.hybrid_candidate_retriever.HybridCandidateRetriever`
for candidate retrieval — once per query when multi-query expansion is
enabled — then hand the (possibly merged) output to the ranking,
allocation, and formatting stages, in order. It contributes no ranking,
retrieval, or formatting logic of its own, and no longer orchestrates the
resolve/spread/assemble sequence directly — that orchestration lives
exclusively in :class:`HybridCandidateRetriever` now, and duplicating it
here would mean two independently-maintained copies of the same seed
construction and stage-wiring logic.

No glue logic required by default
-----------------------------------
:meth:`HybridCandidateRetriever.retrieve` returns ``list[Candidate]`` — a
single, uniform type (see that module's docstring, "Design decisions").
Entries with ontology evidence carry one or more
:class:`~obsidian.ontology.retrieval_models.ActivatedConcept` in
``supporting_concepts``; keyword-only matches (no supporting Concept) are
zero-evidence ``Candidate`` instances (``supporting_concepts=()``,
``activation_score=0.0``, ``attachment_relevance=0.0`` —
``has_ontology_evidence`` is ``False``), not bare
:class:`~obsidian.manager_ai.models.KnowledgeObject` instances. There is
therefore no type mismatch to bridge between retrieval and ranking:
:class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`
already declares its contract as ``List[Candidate]``, and that is exactly
what :meth:`HybridCandidateRetriever.retrieve` produces. **When no**
:class:`~obsidian.memory_engine.query_rewriter.QueryRewriter` **is
configured** (the default — see below), :meth:`query` passes that single
call's output straight through unfiltered, exactly as before multi-query
expansion existed.

Optional multi-query expansion
--------------------------------
:meth:`__init__` accepts an optional ``query_rewriter``
(:class:`~obsidian.memory_engine.query_rewriter.QueryRewriter`). It
defaults to ``None``, the sensible default for a feature that makes an
outbound LLM call: rewriting must be opted into explicitly by passing a
constructed instance, never enabled implicitly.

* **Disabled or absent (``query_rewriter=None``, the default).**
  :meth:`query` is byte-for-byte identical to the implementation before
  this parameter existed: a single
  :meth:`HybridCandidateRetriever.retrieve` call on *raw_query*, passed
  straight through to ranking, allocation, and formatting. The branch in
  :meth:`query` for this case is that original method body, unchanged —
  no new code executes.
* **Enabled (a** :class:`~obsidian.memory_engine.query_rewriter.QueryRewriter`
  **instance was supplied).** :meth:`query` instead:

  1. calls :meth:`~obsidian.memory_engine.query_rewriter.QueryRewriter.rewrite`
     on *raw_query* to obtain a
     :class:`~obsidian.memory_engine.query_rewriter.RewriteResult`;
  2. calls :meth:`HybridCandidateRetriever.retrieve` once for
     ``rewrite_result.original`` (always exactly *raw_query* — see
     ``RewriteResult``'s own contract) and once more for each of
     ``rewrite_result.rewrites`` (zero, one, or two additional calls);
  3. merges every call's ``Candidate`` list into one, keyed by
     ``Candidate.knowledge_object.id`` (see :func:`_merge_candidates`);
  4. hands the merged, deterministically re-sorted list to the same
     ranking/allocation/formatting stages used in the disabled case.

  ``QueryRewriter`` already fails open on every internal error (missing
  API key, timeout, malformed JSON, other API errors — see its own module
  docstring), degrading to ``rewrites=()``. When that happens, step 2
  above performs exactly one retrieval call — the same one the disabled
  path would have made — so an enabled-but-failing rewriter degrades
  gracefully to single-query behavior rather than to no results.

Merge semantics (multi-query expansion only)
----------------------------------------------
:func:`_merge_candidates` deduplicates by ``Candidate.knowledge_object.id``
across the per-query ``Candidate`` lists, processed in a fixed order
(original query first, then each rewrite in the order ``RewriteResult``
produced them):

* The first candidate seen for a given id is kept.
* A later duplicate for the same id **replaces** the kept candidate only
  if the later one has ontology evidence (``Candidate.has_ontology_evidence``)
  and the kept one does not — i.e. an ontology-evidenced candidate is
  never displaced by a zero-evidence duplicate found via a different
  rewrite, but a zero-evidence match found via the original query *is*
  upgraded if a rewrite's query resolves the same ``KnowledgeObject``
  through the ontology path instead. Two ontology-evidenced duplicates
  keep whichever was found first (deterministic, since query order is
  fixed).
* The merged result is sorted by ascending ``str(knowledge_object.id)``,
  the same tie-break convention ``HybridCandidateRetriever`` itself uses,
  so the merge step introduces no order-dependence on which rewrite
  happened to find a duplicate.

This mirrors, one level up, the same "ontology evidence wins" merge policy
``HybridCandidateRetriever`` already applies *within* a single query when
its ontology and keyword paths both find the same ``KnowledgeObject`` (see
that module's "Merge semantics"); it does not duplicate that logic, since
it never inspects *how* a ``Candidate`` got its evidence, only whether
``Candidate.has_ontology_evidence`` is ``True``.

Unknown queries
----------------
If retrieval finds nothing at all — neither ontology-evidenced nor
keyword-only — :meth:`query` does not special-case an early return — it
runs the same three calls with an empty input at every stage.
``DeterministicRanker.rank`` on an empty list returns ``[]``, and so on
down to ``ContextBuilder.build([])``, which returns ``""`` by its own
documented contract. This keeps the "no results" path exercised by the
exact same code as every other query. A keyword-only match with a
composite score below ``RetrievalConfig.minimum_candidate_score`` is
dropped by :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`
exactly like any other low-scoring candidate — there is no separate
"keyword-only" cutoff. This holds whether or not multi-query expansion is
enabled: an empty merged list behaves exactly like an empty single-query
list.

Context Planner integration (Phase 1.5, diagnostics; Phase 3, retrieval-shaping)
---------------------------------------------------------------------------------
:meth:`query_with_trace` runs a
:class:`~obsidian.memory_engine.context_planner.ContextPlanner` once per
call, before rewriting or retrieval begins, and attaches the resulting
:class:`~obsidian.memory_engine.context_planner.ContextPlan` to the
returned :class:`~obsidian.ontology.retrieval_models.RetrievalTrace` as
``context_plan`` (see :func:`_context_plan_trace`). This is the only place
in the codebase that calls :class:`ContextPlanner`.

As of Phase 3, the plan is no longer purely observational:
:meth:`query_with_trace` also runs the plan and
:class:`DeterministicRanker`'s output through
:class:`~obsidian.memory_engine.category_preference.CategoryPreferenceScorer`
(see that module's docstring for the full design), which adds a small,
bounded, deterministic score bonus to any candidate whose category the plan
requested, *before* :class:`AcceptanceStage` runs. This means the plan now
genuinely participates in acceptance (absolute floor, abstention, score-gap
cut, relative floor), ranking order, and therefore in what
:class:`DeterministicSlotAllocator` allocates and
:class:`ContextBuilder`/:class:`WorkingContextBuilder`/:class:`StructuredPromptBuilder`
render — :meth:`query`'s returned context string can differ from what it
would be with an empty or different plan. It remains a *soft* influence: no
candidate is ever filtered out because of the plan, only reordered/nudged
across thresholds it was already close to (see
:mod:`~obsidian.memory_engine.category_preference`'s "Why a soft preference,
not a hard filter"). Nothing else in the pipeline reads the plan back:
rewriting, :class:`HybridCandidateRetriever`, and
:class:`DeterministicRanker` itself remain planner-agnostic, as does
:class:`WorkingContextBuilder`/:class:`StructuredPromptBuilder` beyond
seeing the already-adjusted allocation. A future phase may extend this
further into bounded gap-recovery retries; see
``docs/architecture/CONTEXT_PLAN_OBJECT.md`` §8 for that larger,
not-yet-implemented design.

Coverage Analysis integration (Phase 2, diagnostics only)
------------------------------------------------------------
:meth:`query_with_trace` also runs
:func:`~obsidian.memory_engine.coverage_analyzer.analyze_coverage` once per
call, after acceptance and slot allocation finish, comparing ``context_plan``'s
requirements against the candidates this run actually accepted. The result is
attached to the returned :class:`~obsidian.ontology.retrieval_models.RetrievalTrace`
as ``coverage`` (see :func:`_coverage_report_trace`). Like the Context Planner
integration above, nothing here or downstream reads the coverage report back:
it does not trigger a retry, does not change which candidates were accepted,
and does not influence :meth:`query`'s returned context string in any way. A
future phase may use it to trigger a bounded gap-recovery retry; see
``docs/architecture/CONTEXT_PLAN_OBJECT.md`` §5 for that larger,
not-yet-implemented design.

Gap Recovery Decision integration (Phase 4, diagnostics only)
------------------------------------------------------------------
:meth:`query_with_trace` also runs
:func:`~obsidian.memory_engine.gap_recovery.decide_gap_recovery` once per
call, immediately after coverage analysis finishes, deriving a
:class:`~obsidian.memory_engine.gap_recovery.GapRecoveryDecision` from
``context_plan`` and the just-computed ``coverage_report`` alone -- no
retriever, ranker, LLM, or ontology access. The result is attached to the
returned :class:`~obsidian.ontology.retrieval_models.RetrievalTrace` as
``gap_recovery`` (see :func:`_gap_recovery_trace`). Exactly like the Coverage
Analysis integration above, this is purely observational: nothing here or
downstream reads the decision back, no retrieval retry is issued even when
``should_retry`` is ``True``, and :meth:`query`'s returned context string is
unaffected. See :mod:`obsidian.memory_engine.gap_recovery`'s module
docstring for the full design and ``obsidian/docs/ARCHITECTURE.md``'s
"Gap Recovery Decision (Phase 4)" section for the phasing.

ProjectState integration (Phase A, observational only)
------------------------------------------------------------
:meth:`query_with_trace` also runs
:meth:`~obsidian.memory_engine.project_state.ProjectStateBuilder.build`
once per call, immediately after :class:`DeterministicSlotAllocator`
produces ``allocated`` -- the exact same list :class:`ContextBuilder` just
rendered into the returned context string. The result is attached to the
returned :class:`~obsidian.ontology.retrieval_models.RetrievalTrace` as
``project_state`` (see :func:`_project_state_trace`). This is purely
observational, exactly like the three integrations above: nothing here or
downstream reads the resulting
:class:`~obsidian.memory_engine.project_state.ProjectState` back to change
retrieval, ranking, acceptance, allocation, ``WorkingContext``, or any
rendered prompt, so :meth:`query`'s returned context string is unaffected.
Unlike the eventual (not-yet-built) incrementally-materialized
``ProjectState`` design, this Phase A build recomputes entirely from this
one query's ``allocated`` candidates every call -- no persistence, no
cross-query aggregation, no LLM. See
:mod:`obsidian.memory_engine.project_state`'s module docstring for the
full Phase A design and scope, and
``docs/architecture/PROJECT_STATE_DESIGN.md`` for the larger, multi-phase
design this implements only the first phase of.

Determinism
-----------
:class:`HybridCandidateRetriever` is independently deterministic given the
same inputs (see its own module docstring), as is every other collaborator
stage, including ``QueryRewriter`` (its own cache guarantees the same
``RewriteResult`` for the same query on a given instance — see that
module's "Determinism"). :meth:`query` adds no randomness. In the disabled
case it performs no unordered iteration and no filtering of its own — pure
pass-through orchestration. In the enabled case, the only addition is the
fixed-order, deterministic merge described above, which does not depend on
dict/set iteration order. The one wall-clock read in the pipeline is
:meth:`DeterministicRanker.rank`'s internal default for its ``now``
parameter (recency scoring); :meth:`query` does not read the clock itself
and does not expose a ``now`` override, since the task scopes the public
API to a single ``query(raw_query: str) -> str`` entry point.

Explicitly out of scope
------------------------
* **No ranking logic** — the seven scored components underlying
  ``final_score`` (``activation``, ``attachment_relevance``,
  ``keyword_overlap``, ``importance``, ``confidence``, ``recency``,
  ``confirmation_count``) and ``score_breakdown`` are computed exclusively
  by :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`.
  This module calls that ranker, then (Phase 3) calls
  :class:`~obsidian.memory_engine.category_preference.CategoryPreferenceScorer`
  to layer one further, independent, bounded adjustment on top of the
  ranker's composite score — it does not recompute or reweight any of the
  seven components itself, and neither does the category-preference stage
  (see that module's own scope statement).
* **No retrieval logic** — concept resolution, activation spreading,
  evidence collection, and keyword matching are exclusively
  :class:`~obsidian.memory_engine.hybrid_candidate_retriever.HybridCandidateRetriever`'s
  (and, transitively, its own collaborators'). The merge step this module
  adds when multi-query expansion is enabled only groups already-produced
  ``Candidate`` objects by id; it does not resolve concepts, spread
  activation, collect evidence, or match keywords itself.
* **No query-rewriting logic** — generating alternate phrasings, calling
  an LLM, and failing open on error are exclusively
  :class:`~obsidian.memory_engine.query_rewriter.QueryRewriter`'s. This
  module only decides *whether* to call it and how many retrieval calls to
  make from its output.
* **No formatting logic** — context string rendering is exclusively
  :class:`~obsidian.memory_engine.context_builder.ContextBuilder`'s.
* **No graph mutation** — only read-only ``ConceptGraph`` methods are
  reachable through the stages this module calls.
* **No vault mutation** — only read-only ``MemoryStore`` methods are
  reachable through the stages this module calls.
* **No hard filtering from ContextPlan** — :meth:`query_with_trace` calls
  :class:`~obsidian.memory_engine.context_planner.ContextPlanner` and, as of
  Phase 3, feeds its ``requirements`` into
  :class:`~obsidian.memory_engine.category_preference.CategoryPreferenceScorer`
  (see "Context Planner integration" above). That stage only ever adds a
  bounded score bonus; this module still never drops, reclassifies, or
  excludes a candidate because of what the plan says, and the plan itself
  is never mutated or re-derived here.
* **No CoverageReport consumption** — :meth:`query_with_trace` calls
  :func:`~obsidian.memory_engine.coverage_analyzer.analyze_coverage` and
  records its output on the trace (see "Coverage Analysis integration"
  above), but the resulting
  :class:`~obsidian.memory_engine.coverage_analyzer.CoverageReport` is never
  read back to retry retrieval, change acceptance, or alter allocation.
* **No gap-recovery retries** — :meth:`query_with_trace` calls
  :func:`~obsidian.memory_engine.gap_recovery.decide_gap_recovery` and
  records its output on the trace (see "Gap Recovery Decision integration"
  above), but the resulting
  :class:`~obsidian.memory_engine.gap_recovery.GapRecoveryDecision` is never
  read back to issue a second retrieval pass, change acceptance, or alter
  allocation, even when it recommends ``should_retry=True``.
* **No ProjectState consumption** — :meth:`query_with_trace` calls
  :meth:`~obsidian.memory_engine.project_state.ProjectStateBuilder.build`
  and records its output on the trace (see "ProjectState integration"
  above), but the resulting
  :class:`~obsidian.memory_engine.project_state.ProjectState` is never read
  back to influence retrieval, ranking, acceptance, allocation,
  ``WorkingContext``, or any rendered prompt -- it is built strictly after
  :class:`ContextBuilder` has already rendered ``context`` from the same
  ``allocated`` list, so it structurally cannot affect that string.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from uuid import UUID

from obsidian.memory_engine.acceptance_stage import AcceptanceConfig, AcceptanceStage
from obsidian.memory_engine.category_preference import CategoryPreferenceScorer
from obsidian.memory_engine.context_builder import ContextBuilder
from obsidian.memory_engine.context_planner import (
    ContextCategory,
    ContextPlan,
    ContextPlanner,
    TaskMode,
)
from obsidian.memory_engine.coverage_analyzer import (
    CoverageReport,
    analyze_coverage,
    resolve_category,
)
from obsidian.memory_engine.deterministic_ranker import DeterministicRanker
from obsidian.memory_engine.deterministic_slot_allocator import (
    DeterministicSlotAllocator,
)
from obsidian.memory_engine.gap_recovery import GapRecoveryDecision, decide_gap_recovery
from obsidian.memory_engine.hybrid_candidate_retriever import (
    HybridCandidateRetriever,
)
from obsidian.memory_engine.memory_store import MemoryStore
from obsidian.memory_engine.project_state import ProjectState, ProjectStateBuilder
from obsidian.memory_engine.query_rewriter import QueryRewriter
from obsidian.memory_engine.structured_prompt_builder import StructuredPromptBuilder
from obsidian.memory_engine.working_context_builder import (
    WorkingContextBuilder,
    primary_concept_id,
)
from obsidian.ontology.alias_index import AliasIndex
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.retrieval_config import RetrievalConfig
from obsidian.ontology.retrieval_models import (
    Candidate,
    CandidateTrace,
    CategoryCoverageTrace,
    ContextCategoryRequirementTrace,
    ContextKind,
    ContextPlanTrace,
    CoverageReportTrace,
    GapRecoveryTrace,
    ProjectStateFieldTrace,
    ProjectStateTrace,
    RankedCandidate,
    RetrievalPipelineStats,
    RetrievalTrace,
    StateRefTrace,
    WorkingContext,
)
from obsidian.ontology.retrieval_models import REJECTION_SLOT_BUDGET_EXCEEDED


def _context_plan_trace(context_plan: ContextPlan) -> ContextPlanTrace:
    """Project a :class:`ContextPlan` into its plain-primitive trace record.

    Converts each enum on *context_plan* to its ``.value`` string, since
    :class:`ContextPlanTrace` lives in ``obsidian.ontology`` and must not
    import the planner's enum types (see that module's "ContextPlanTrace"
    section for why). Purely a projection: no field is recomputed with
    different logic than what :meth:`ContextPlanner.plan` already decided.
    """
    return ContextPlanTrace(
        task_mode=context_plan.task_mode.value,
        planning_method=context_plan.planning_method.value,
        scope_concept_id=context_plan.scope_concept_id,
        confidence=context_plan.confidence,
        requirements=tuple(
            ContextCategoryRequirementTrace(
                category=r.category.value,
                necessity=r.necessity.value,
                min_count=r.min_count,
                max_count=r.max_count,
                priority_tier=r.priority_tier.value,
            )
            for r in context_plan.requirements
        ),
    )


def _coverage_report_trace(report: CoverageReport) -> CoverageReportTrace:
    """Project a :class:`CoverageReport` into its plain-primitive trace record.

    Converts each enum on *report*'s entries to its ``.value`` string, since
    :class:`CoverageReportTrace` lives in ``obsidian.ontology`` and must not
    import :mod:`obsidian.memory_engine.coverage_analyzer`'s or
    :mod:`obsidian.memory_engine.context_planner`'s enum types (see
    :class:`CoverageReportTrace`'s docstring for why). Purely a projection:
    no field is recomputed with different logic than what
    :func:`~obsidian.memory_engine.coverage_analyzer.analyze_coverage`
    already decided.
    """
    return CoverageReportTrace(
        entries=tuple(
            CategoryCoverageTrace(
                category=entry.category.value,
                necessity=entry.necessity.value,
                required_minimum=entry.required_minimum,
                retrieved_count=entry.retrieved_count,
                satisfied=entry.satisfied,
                status=entry.status.value,
            )
            for entry in report.entries
        ),
        overall_coverage_percentage=report.overall_coverage_percentage,
        missing_required_categories=tuple(
            c.value for c in report.missing_required_categories
        ),
        fully_satisfied=report.fully_satisfied,
    )


def _gap_recovery_trace(decision: GapRecoveryDecision) -> GapRecoveryTrace:
    """Project a :class:`GapRecoveryDecision` into its plain-primitive trace record.

    Converts each enum on *decision* to its ``.value`` string, since
    :class:`GapRecoveryTrace` lives in ``obsidian.ontology`` and must not
    import :mod:`obsidian.memory_engine.gap_recovery`'s or
    :mod:`obsidian.memory_engine.context_planner`'s enum types (see
    :class:`GapRecoveryTrace`'s docstring for why). Purely a projection: no
    field is recomputed with different logic than what
    :func:`~obsidian.memory_engine.gap_recovery.decide_gap_recovery` already
    decided.
    """
    return GapRecoveryTrace(
        should_retry=decision.should_retry,
        missing_categories=tuple(c.value for c in decision.missing_categories),
        retry_budget=decision.retry_budget,
        retry_reason=decision.retry_reason.value,
        confidence=decision.confidence,
        recovery_strategy=decision.recovery_strategy.value,
    )


def _state_ref_trace(ref) -> StateRefTrace:
    """Project a :class:`~obsidian.memory_engine.project_state.StateRef` verbatim."""
    return StateRefTrace(
        knowledge_object_id=ref.knowledge_object_id,
        canonical_fact=ref.canonical_fact,
        valid_from=ref.valid_from,
        confidence=ref.confidence,
        importance=ref.importance,
    )


def _project_state_trace(state: ProjectState) -> ProjectStateTrace:
    """Project a :class:`ProjectState` into its plain-primitive trace record.

    Every field is copied verbatim via :func:`_state_ref_trace` -- no field
    is recomputed with different logic than what
    :meth:`~obsidian.memory_engine.project_state.ProjectStateBuilder.build`
    already decided. See
    :mod:`~obsidian.memory_engine.project_state`'s module docstring for the
    full Phase A design this projects.
    """
    current_objective = None
    if state.current_objective is not None:
        current_objective = ProjectStateFieldTrace(
            value=_state_ref_trace(state.current_objective.value),
            derivation=state.current_objective.derivation.value,
            confidence=state.current_objective.confidence,
        )
    return ProjectStateTrace(
        current_objective=current_objective,
        decisions=tuple(_state_ref_trace(r) for r in state.decisions),
        superseded_decisions=tuple(
            _state_ref_trace(r) for r in state.superseded_decisions
        ),
        active_tasks=tuple(_state_ref_trace(r) for r in state.active_tasks),
        blockers=tuple(_state_ref_trace(r) for r in state.blockers),
        constraints=tuple(_state_ref_trace(r) for r in state.constraints),
        implementation_state=tuple(
            _state_ref_trace(r) for r in state.implementation_state
        ),
        code_areas=tuple(_state_ref_trace(r) for r in state.code_areas),
        open_questions=tuple(_state_ref_trace(r) for r in state.open_questions),
        gaps=state.gaps,
        confidence=state.confidence,
        generated_at=state.generated_at,
    )


def _merge_candidates(candidate_lists: List[List[Candidate]]) -> List[Candidate]:
    """Merge per-query ``Candidate`` lists, deduplicated by ``KnowledgeObject.id``.

    See the module docstring's "Merge semantics" for the full policy. A
    duplicate only replaces an already-kept candidate for the same id when
    the duplicate carries ontology evidence and the kept one does not —
    ontology evidence, once found for a given id, is never displaced by a
    later zero-evidence duplicate.

    Parameters
    ----------
    candidate_lists : list[list[Candidate]]
        One list per query that was retrieved for, in the order those
        queries were issued (original first, then each rewrite).

    Returns
    -------
    list[Candidate]
        One entry per unique ``KnowledgeObject.id`` across every list,
        sorted by ascending ``str(knowledge_object.id)`` for deterministic
        output regardless of which query found a duplicate.
    """
    merged: Dict[UUID, Candidate] = {}
    for candidates in candidate_lists:
        for candidate in candidates:
            ko_id = candidate.knowledge_object.id
            existing = merged.get(ko_id)
            if existing is None or (
                candidate.has_ontology_evidence and not existing.has_ontology_evidence
            ):
                merged[ko_id] = candidate

    return sorted(
        merged.values(), key=lambda candidate: str(candidate.knowledge_object.id)
    )


def _active_candidates(candidates: List[Candidate], now: datetime) -> List[Candidate]:
    """Drop candidates whose ``KnowledgeObject`` is no longer valid at *now*.

    A ``KnowledgeObject`` is *inactive* once its ``valid_until`` is set and has
    passed (``valid_until <= now``) -- the write path sets ``valid_until`` when
    a memory is archived or superseded (see
    :meth:`~obsidian.manager_ai.knowledge_updater.KnowledgeUpdater.supersede_decision`).
    An inactive memory is treated as if it were never a candidate at all: it is
    removed here, *before* ranking, so it never scores, never fills a context
    slot, and never appears in a :class:`~obsidian.ontology.retrieval_models.RetrievalTrace`.
    Memories with ``valid_until is None`` (the overwhelming majority) or a
    ``valid_until`` still in the future are kept unchanged.

    This is the retrieval pipeline's single, read-only expression of memory
    validity. It touches no vault file and mutates no
    :class:`~obsidian.memory_engine.memory_store.MemoryStore` state -- the
    archived note still exists on disk and is still loaded into the store; it is
    simply not *offered* as a retrieval candidate. Determinism is preserved
    because *now* is the exact reference instant the
    :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`
    already uses for recency (threaded in from the caller), not an independent
    clock read.
    """
    return [
        candidate
        for candidate in candidates
        if candidate.knowledge_object.valid_until is None
        or candidate.knowledge_object.valid_until > now
    ]


@dataclass
class _RetrievalPrefix:
    """Everything ``query``/``query_with_trace``/``query_structured`` share.

    Produced once per call by :meth:`MemoryEngine._run_retrieval`, which is
    now the single implementation of the rewrite → retrieve → merge →
    validity-filter → rank prefix. Before this dataclass existed, that
    prefix was implemented twice: inline in :meth:`MemoryEngine.query_with_trace`
    and, near-identically, in :meth:`MemoryEngine._allocate` (used by
    :meth:`MemoryEngine.query_working_context` and, transitively,
    :meth:`MemoryEngine.query_structured`). Two independently-maintained
    copies of the same seed construction and stage-wiring logic is exactly
    what :mod:`obsidian.memory_engine.engine`'s own module docstring already
    warns against for :class:`HybridCandidateRetriever`; this collapses that
    to one.

    Deliberately stops *before* :class:`~obsidian.memory_engine.category_preference.CategoryPreferenceScorer`:
    that stage is a Phase 3 addition specific to :meth:`MemoryEngine.query_with_trace`'s
    scoring/acceptance behaviour (see that method's own docstring), and
    :meth:`MemoryEngine._allocate` is deliberately **not** planner-aware --
    folding the category-preference bonus into this shared prefix would
    change :meth:`_allocate`'s (and therefore
    :meth:`query_working_context`'s and :meth:`query_structured`'s) ranking
    output, which is an explicit non-goal of this refactor ("Do NOT change
    ranking"). ``context_plan`` is carried here purely so callers *can*
    read ``task_mode``/``requirements`` without an extra
    :class:`~obsidian.memory_engine.context_planner.ContextPlanner` call --
    not because ranking already used it.

    Attributes
    ----------
    context_plan : ContextPlan
        This query's plan, from :class:`~obsidian.memory_engine.context_planner.ContextPlanner`.
    rewritten_queries : tuple[str, ...]
        Additional queries a configured
        :class:`~obsidian.memory_engine.query_rewriter.QueryRewriter`
        produced; ``()`` when no rewriter is configured.
    candidates : list[Candidate]
        Merged, validity-filtered candidates across every query issued.
    ranked_all : list[RankedCandidate]
        :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s
        raw output over *candidates* -- no category-preference bonus applied.
    ontology_total : int
        Summed ``ontology_candidate_count`` across every query issued.
    keyword_total : int
        Summed ``keyword_candidate_count`` across every query issued.
    matched_by_ontology : set[UUID]
        Union of every query's ``matched_by_ontology`` ids.
    matched_by_keyword : set[UUID]
        Union of every query's ``matched_by_keyword`` ids.
    now : datetime
        The single reference instant used for validity filtering and
        ranking's recency component this call.
    """

    context_plan: ContextPlan
    rewritten_queries: Tuple[str, ...]
    candidates: List[Candidate]
    ranked_all: List[RankedCandidate]
    ontology_total: int
    keyword_total: int
    matched_by_ontology: Set[UUID]
    matched_by_keyword: Set[UUID]
    now: datetime


class MemoryEngine:
    """Public retrieval API: raw query in, LLM-ready context string out.

    Holds the collaborator the retrieval pipeline needs to run —
    a :class:`HybridCandidateRetriever` built from an already-built
    :class:`AliasIndex`, an already-populated :class:`ConceptGraph`, an
    already-:meth:`~MemoryStore.load`-ed :class:`MemoryStore`, and a
    :class:`RetrievalConfig` — plus one instance of each of the ranking,
    allocation, and formatting stages, plus an optional
    :class:`~obsidian.memory_engine.query_rewriter.QueryRewriter`. Reusable
    across multiple calls to :meth:`query`.

    Parameters
    ----------
    alias_index : AliasIndex
        Built index used by :class:`HybridCandidateRetriever`'s ontology
        path to map query text to seed Concept ids.
    concept_graph : ConceptGraph
        Populated graph used for concept lookup, activation spreading,
        and evidence collection. Never mutated by this class.
    memory_store : MemoryStore
        Already-loaded store used to hydrate ``KnowledgeObject``
        instances. Never mutated by this class.
    config : RetrievalConfig, optional
        Governs activation spreading, ranking weights, and the context
        budget. Defaults to ``RetrievalConfig()`` when omitted.
    acceptance_config : AcceptanceConfig, optional
        Governs :class:`~obsidian.memory_engine.acceptance_stage.AcceptanceStage`'s
        abstention, gap-cut, relative-floor, and hard-cap thresholds.
        Defaults to ``AcceptanceConfig()`` when omitted.
    query_rewriter : QueryRewriter, optional
        When supplied, enables multi-query expansion (see the module
        docstring's "Optional multi-query expansion"). Defaults to
        ``None``, which disables it — :meth:`query` is then byte-for-byte
        identical to the implementation before this parameter existed.

    Examples
    --------
    >>> engine = MemoryEngine(alias_index, concept_graph, memory_store)
    >>> engine.query("What does Haven use?")  # doctest: +SKIP
    '[1] Haven uses Claude\\n    type: fact | ...'
    """

    def __init__(
        self,
        alias_index: AliasIndex,
        concept_graph: ConceptGraph,
        memory_store: MemoryStore,
        config: Optional[RetrievalConfig] = None,
        acceptance_config: Optional[AcceptanceConfig] = None,
        query_rewriter: Optional[QueryRewriter] = None,
    ) -> None:
        self._config = config if config is not None else RetrievalConfig()
        self._acceptance_config = (
            acceptance_config if acceptance_config is not None else AcceptanceConfig()
        )

        self._candidate_retriever = HybridCandidateRetriever(
            alias_index, concept_graph, memory_store, config=self._config
        )
        self._concept_graph = concept_graph
        self._memory_store = memory_store
        self._ranker = DeterministicRanker()
        self._category_preference_scorer = CategoryPreferenceScorer()
        self._acceptance_stage = AcceptanceStage()
        self._slot_allocator = DeterministicSlotAllocator()
        self._context_builder = ContextBuilder()
        self._working_context_builder = WorkingContextBuilder()
        self._structured_prompt_builder = StructuredPromptBuilder()
        self._context_planner = ContextPlanner()
        self._project_state_builder = ProjectStateBuilder()
        self._query_rewriter = query_rewriter

    # ------------------------------------------------------------------
    # Shared retrieval prefix
    # ------------------------------------------------------------------

    def _run_retrieval(self, raw_query: str) -> _RetrievalPrefix:
        """Run the one retrieval prefix shared by every public query method.

        This is the single implementation of rewrite → retrieve → merge →
        validity-filter → rank. :meth:`query_with_trace` and :meth:`_allocate`
        (used by :meth:`query_working_context` and, transitively,
        :meth:`query_structured`) both call this method instead of each
        independently re-implementing it -- see :class:`_RetrievalPrefix`'s
        docstring for why this used to be two copies and what changed.

        Also runs :class:`~obsidian.memory_engine.context_planner.ContextPlanner`
        once, before retrieval, exactly as :meth:`query_with_trace` already
        did on its own -- so a :class:`~obsidian.memory_engine.context_planner.ContextPlan`
        is now available to *every* caller of this method, including
        :meth:`query_structured`, without a second, independent planner
        call. The plan is carried on the returned :class:`_RetrievalPrefix`
        purely for callers to read; it is never fed into ranking here (see
        :class:`_RetrievalPrefix`'s docstring for why not).

        Parameters
        ----------
        raw_query : str
            The raw user query text.

        Returns
        -------
        _RetrievalPrefix
            Everything downstream stages (:class:`AcceptanceStage`,
            :class:`~obsidian.memory_engine.category_preference.CategoryPreferenceScorer`,
            :class:`ContextBuilder`, :class:`WorkingContextBuilder`,
            :class:`~obsidian.memory_engine.project_state.ProjectStateBuilder`,
            :class:`RetrievalTrace`) need, without recomputing retrieval.
        """
        now = datetime.utcnow()

        context_plan = self._context_planner.plan(raw_query)

        if self._query_rewriter is None:
            rewritten_queries: Tuple[str, ...] = ()
            queries: Tuple[str, ...] = (raw_query,)
        else:
            rewrite_result = self._query_rewriter.rewrite(raw_query)
            rewritten_queries = rewrite_result.rewrites
            queries = rewrite_result.queries

        candidate_lists: List[List[Candidate]] = []
        ontology_total = 0
        keyword_total = 0
        matched_by_ontology: Set[UUID] = set()
        matched_by_keyword: Set[UUID] = set()
        for one_query in queries:
            (
                query_candidates,
                provenance,
            ) = self._candidate_retriever.retrieve_with_diagnostics(one_query)
            candidate_lists.append(query_candidates)
            ontology_total += provenance.ontology_candidate_count
            keyword_total += provenance.keyword_candidate_count
            matched_by_ontology |= provenance.matched_by_ontology
            matched_by_keyword |= provenance.matched_by_keyword

        if self._query_rewriter is None:
            candidates = candidate_lists[0]
        else:
            candidates = _merge_candidates(candidate_lists)

        # Validity gate: an archived/superseded memory (valid_until <= now) is
        # not a retrieval candidate. See _active_candidates.
        candidates = _active_candidates(candidates, now)

        ranked_all = self._ranker.score_all(candidates, self._config, now=now)

        return _RetrievalPrefix(
            context_plan=context_plan,
            rewritten_queries=rewritten_queries,
            candidates=candidates,
            ranked_all=ranked_all,
            ontology_total=ontology_total,
            keyword_total=keyword_total,
            matched_by_ontology=matched_by_ontology,
            matched_by_keyword=matched_by_keyword,
            now=now,
        )

    def _accept_and_allocate(
        self,
        ranked_all: List[RankedCandidate],
        context_plan: Optional[ContextPlan] = None,
        now: Optional[datetime] = None,
    ) -> List[RankedCandidate]:
        """Run :class:`AcceptanceStage` + :class:`DeterministicSlotAllocator` over *ranked_all*.

        Shared by :meth:`_allocate` and :meth:`query_structured` so this
        second, smaller piece of pipeline logic also exists in exactly one
        place. :meth:`query_with_trace` deliberately keeps its own inline
        call to :class:`AcceptanceStage` rather than reusing this helper: it
        also needs the intermediate ``AcceptanceDecision`` list (to build
        each :class:`~obsidian.ontology.retrieval_models.CandidateTrace`),
        which this helper discards -- reusing it there would mean calling
        :class:`AcceptanceStage` twice for one request. This is also the one
        acceptance call site that runs topic-diversified (see
        :meth:`_accept_by_topic`) rather than global acceptance --
        :meth:`query_with_trace`'s own inline call is untouched and stays
        global, since a single pointed question has exactly one topic to
        answer and diversifying across topics would be meaningless there.

        Category-fallback retrieval (see
        ``docs/architecture/GENERIC_CONTINUATION_QUERY_ANALYSIS.md`` §4/§5
        fix #1)
        -------------------------------------------------------------------
        When *context_plan* classifies as
        :attr:`~obsidian.memory_engine.context_planner.TaskMode.CONTINUATION`
        and normal acceptance above accepts nothing at all -- the exact,
        structural failure mode that analysis documents for a
        vault-vocabulary-free query like "Continue." -- this method makes one
        additional, additive attempt: :meth:`_category_fallback_candidates`
        pulls a bounded number of top-ranked, still-valid memories per
        category the plan requires, independent of any lexical overlap with
        the query, and the exact same :class:`AcceptanceStage` +
        :class:`DeterministicSlotAllocator` this method already runs decide
        which of those survive. No new scoring or acceptance logic is
        introduced; a query that already retrieves normally is completely
        unaffected, since this branch only runs when the ordinary path
        produced zero accepted candidates.

        Parameters
        ----------
        ranked_all : list[RankedCandidate]
            Candidates to accept and allocate, in any order.
        context_plan : ContextPlan, optional
            This query's plan. ``None`` (the default) disables category
            fallback entirely -- existing callers that do not pass a plan
            see byte-identical behaviour to before this parameter existed.
        now : datetime, optional
            Reference time for fallback candidates' recency scoring and
            validity filtering, matching whatever reference instant
            *ranked_all* was itself produced with. Defaults to
            ``datetime.utcnow()`` when omitted.

        Returns
        -------
        list[RankedCandidate]
            The allocated candidates, as returned by
            :meth:`DeterministicSlotAllocator.allocate`.
        """
        accepted_candidates = self._accept_by_topic(ranked_all)

        if (
            not accepted_candidates
            and context_plan is not None
            and context_plan.task_mode is TaskMode.CONTINUATION
        ):
            fallback_ranked = self._category_fallback_candidates(
                context_plan, ranked_all, now
            )
            if fallback_ranked:
                accepted_candidates = self._accept_by_topic(fallback_ranked)

        return self._slot_allocator.allocate(accepted_candidates, self._config)

    def _accept_by_topic(
        self, ranked_all: List[RankedCandidate]
    ) -> List[RankedCandidate]:
        """Run :class:`AcceptanceStage` once per topic group, instead of once globally.

        Root cause this addresses
        --------------------------
        :class:`AcceptanceStage`'s relative-quality floor (stage 4) and hard
        cap (stage 5) are both anchored to a *single* top score across
        whatever candidate list they are handed (see that module's
        docstring). For a genuinely single-topic query that is exactly the
        right behaviour. But :meth:`query_working_context`/
        :meth:`query_structured` exist specifically to reconstruct a
        *multi-topic* working context (project state, architecture
        decisions, benchmarks, roadmap, blockers -- see
        ``docs/architecture/PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md``),
        and :class:`~obsidian.memory_engine.working_context_builder.WorkingContextBuilder`
        already groups the allocated candidates by topic (primary concept)
        for exactly that reason. Running one global acceptance pass
        *before* that grouping means whichever single topic happens to
        score highest overall (typically the most recent/most
        lexically-matched one) sets the top score every other topic's
        candidates get compared against -- a topic that is itself
        internally coherent and well-supported can still be floor-cut or
        cap-excluded wholesale, purely because a *different* topic scored
        higher on this particular query. This was measured directly: a
        broad "catch me up" query against a multi-topic vault returned
        candidates from only one or two topics, even when the query
        explicitly mentioned keywords for several others.

        Why this fix, not a bigger cap or a permissive floor
        ------------------------------------------------------
        Simply raising ``acceptance_max_k`` or loosening
        ``relative_floor_ratio`` (the same lever the dashboard's digest
        engine uses for its own different problem -- see
        ``obsidian/server/dashboard.py``'s ``_DIGEST_ACCEPTANCE_CONFIG``)
        would let low-relevance candidates through *within* whichever topic
        already dominates, without doing anything to surface the topics
        that are currently being crowded out entirely. The digest engine's
        problem is genuinely different: its query is the concatenation of
        every memory in the vault, so "let almost everything through" is
        the correct policy for that all-inclusive input. A real, specific
        "catch me up" query is not all-inclusive text -- it is one query
        that happens to span several coherent topics, so the right fix is
        to evaluate each topic on its own terms (its own top score, its own
        relative floor, its own cap), not to weaken the floor/cap globally.

        Mechanism
        ---------
        Groups *ranked_all* by :func:`~obsidian.memory_engine.working_context_builder.primary_concept_id`
        -- the exact same anchor-concept rule
        :class:`~obsidian.memory_engine.working_context_builder.WorkingContextBuilder`
        uses downstream to build :class:`~obsidian.ontology.retrieval_models.WorkingContext`
        objects, so a candidate is scored against the same peers it will
        later be *grouped* with, not an unrelated global pool. Every
        zero-evidence candidate (no supporting concepts) shares one
        ``None`` group, mirroring :class:`WorkingContextBuilder`'s own
        ``GENERAL`` bucket. :class:`AcceptanceStage` itself runs completely
        unmodified, once per group, with the exact same
        :class:`~obsidian.memory_engine.acceptance_stage.AcceptanceConfig`
        thresholds used everywhere else in the pipeline -- no new constant,
        no new threshold, no change to what "good enough" means within a
        topic. :class:`~obsidian.memory_engine.deterministic_slot_allocator.DeterministicSlotAllocator`
        (unchanged, downstream) still applies ``RetrievalConfig.max_results``
        as an overall ceiling across every group's survivors combined, so a
        vault with many small topics still cannot flood the final context.

        Scope
        -----
        Only :meth:`_accept_and_allocate` calls this -- i.e. only
        :meth:`_allocate` (:meth:`query_working_context`) and
        :meth:`query_structured`. :meth:`query`/:meth:`query_with_trace`
        keep their own inline, global :class:`AcceptanceStage` call
        untouched, so the Retrieval Inspector, benchmarks, and every other
        single-topic consumer of this engine see byte-identical behaviour
        to before this method existed.

        Parameters
        ----------
        ranked_all : list[RankedCandidate]
            Every scored candidate for this query, in any order.

        Returns
        -------
        list[RankedCandidate]
            The union of every topic group's accepted candidates. Order is
            not meaningful -- both :class:`DeterministicSlotAllocator` and
            :class:`WorkingContextBuilder` re-sort their input themselves.
        """
        groups: Dict[Optional[UUID], List[RankedCandidate]] = {}
        for ranked in ranked_all:
            groups.setdefault(primary_concept_id(ranked), []).append(ranked)

        accepted: List[RankedCandidate] = []
        for anchor in sorted(groups, key=lambda a: (a is None, str(a))):
            decisions = self._acceptance_stage.accept(
                groups[anchor], self._config, self._acceptance_config
            )
            accepted.extend(d.candidate for d in decisions if d.accepted)
        return accepted

    #: Bound on how many fallback candidates :meth:`_category_fallback_candidates`
    #: contributes per requested category, before those candidates are even
    #: handed to :class:`AcceptanceStage`. Keeps the fallback pool small and
    #: deliberately unrelated to any token-budget tuning knob -- see that
    #: method's docstring for why a fixed, small constant rather than a
    #: config field.
    _CATEGORY_FALLBACK_PER_CATEGORY_LIMIT = 5

    def _category_fallback_candidates(
        self,
        context_plan: ContextPlan,
        ranked_all: List[RankedCandidate],
        now: Optional[datetime],
    ) -> List[RankedCandidate]:
        """Rank a bounded, category-scoped fallback pool, independent of lexical overlap.

        Exists only to serve :meth:`_accept_and_allocate`'s category-fallback
        branch (see that method's docstring for when this runs and why).
        Root cause (``docs/architecture/GENERIC_CONTINUATION_QUERY_ANALYSIS.md``
        §2/§3): a query with no vault-specific vocabulary structurally cannot
        produce any candidate through either of
        :class:`~obsidian.memory_engine.hybrid_candidate_retriever.HybridCandidateRetriever`'s
        two paths, both of which require lexical/alias overlap by design.
        This method sidesteps that requirement entirely -- it builds
        candidates directly from :class:`~obsidian.memory_engine.memory_store.MemoryStore`,
        with no query-text matching of any kind.

        Deliberately reuses, rather than reimplements, every scoring/validity
        rule already in the pipeline:

        * A candidate only exists for a still-valid ``KnowledgeObject``
          (mirrors :func:`_active_candidates`'s ``valid_until`` check).
        * A candidate is only built for a ``KnowledgeObject`` whose
          ``memory_type`` resolves (via
          :func:`~obsidian.memory_engine.coverage_analyzer.resolve_category`,
          the same table :class:`~obsidian.memory_engine.coverage_analyzer.CoverageAnalyzer`
          and :class:`~obsidian.memory_engine.category_preference.CategoryPreferenceScorer`
          already share) to a category *context_plan* actually requires --
          never every category in the vault, and never a second,
          independently-maintained copy of that mapping.
        * Scoring is :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s
          existing weighted formula, unchanged. Every fallback candidate is
          built with ``supporting_concepts=()``, ``attachment_relevance=0.0``,
          ``activation_score=0.0``, ``keyword_overlap_score=0.0`` -- the
          honest representation of "no lexical or ontology evidence", not a
          fabricated one (see :class:`~obsidian.ontology.retrieval_models.Candidate`'s
          own docstring for this exact convention). Its ``final_score``
          therefore rests entirely on importance/confidence/recency/
          confirmation-count, which is precisely what makes it eligible to
          clear :class:`AcceptanceStage`'s abstention floor on its own
          merits -- no threshold anywhere is changed for this to work.
        * Per-category top-K (:data:`_CATEGORY_FALLBACK_PER_CATEGORY_LIMIT`)
          keeps the pool small and bounded *before* acceptance ever sees it,
          so a large vault cannot make this branch expensive or let one
          high-volume category crowd out the others within the fallback pool
          itself.

        A ``KnowledgeObject`` already present in *ranked_all* is excluded --
        this fallback only fills in what normal retrieval found nothing for,
        never duplicates it.

        Parameters
        ----------
        context_plan : ContextPlan
            The plan whose ``requirements`` define which categories this
            fallback is allowed to pull from.
        ranked_all : list[RankedCandidate]
            This query's normal (already lexically/ontology-driven) ranked
            candidates -- read only to exclude their ``KnowledgeObject`` ids
            from the fallback pool.
        now : datetime, optional
            Reference time for validity filtering and recency scoring.
            Defaults to ``datetime.utcnow()`` when omitted.

        Returns
        -------
        list[RankedCandidate]
            Every fallback candidate this method selected, scored and
            sorted exactly as :class:`DeterministicRanker.score_all` orders
            any other candidate list. Empty when *context_plan* requires no
            category, or when nothing in the store qualifies.
        """
        required_categories = {req.category for req in context_plan.requirements}
        if not required_categories:
            return []

        reference_time = now if now is not None else datetime.utcnow()
        already_seen = {rc.candidate.knowledge_object.id for rc in ranked_all}

        pool: List[Candidate] = []
        for ko in self._memory_store.all():
            if ko.id in already_seen:
                continue
            if ko.valid_until is not None and ko.valid_until <= reference_time:
                continue
            category = resolve_category(ko.memory_type)
            if category not in required_categories:
                continue
            pool.append(
                Candidate(
                    knowledge_object=ko,
                    supporting_concepts=(),
                    attachment_relevance=0.0,
                    activation_score=0.0,
                    keyword_overlap_score=0.0,
                )
            )

        if not pool:
            return []

        ranked_pool = self._ranker.score_all(pool, self._config, now=reference_time)

        per_category_counts: Dict[ContextCategory, int] = {}
        bounded: List[RankedCandidate] = []
        for ranked_candidate in ranked_pool:
            category = resolve_category(
                ranked_candidate.candidate.knowledge_object.memory_type
            )
            count = per_category_counts.get(category, 0)
            if count >= self._CATEGORY_FALLBACK_PER_CATEGORY_LIMIT:
                continue
            per_category_counts[category] = count + 1
            bounded.append(ranked_candidate)

        return sorted(bounded)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(self, raw_query: str) -> str:
        """Resolve *raw_query* into a deterministic LLM context string.

        Runs the full retrieval pipeline: hybrid candidate retrieval,
        ranking, slot allocation, and context building, in that order.
        When a :class:`~obsidian.memory_engine.query_rewriter.QueryRewriter`
        was supplied to :meth:`__init__`, candidate retrieval additionally
        runs once per rewrite and merges the results before ranking — see
        the module docstring's "Optional multi-query expansion" for the
        full behavior. With no rewriter configured (the default), this
        method behaves exactly as it did before that feature existed.

        Parameters
        ----------
        raw_query : str
            The raw user query text.

        Returns
        -------
        str
            The formatted context string produced by
            :class:`~obsidian.memory_engine.context_builder.ContextBuilder`.
            ``""`` if *raw_query* resolves to no candidate at all, or if no
            candidate survives ranking's ``minimum_candidate_score`` cutoff.
        """
        return self.query_with_trace(raw_query)[0]

    def query_with_trace(self, raw_query: str) -> Tuple[str, RetrievalTrace]:
        """Resolve *raw_query* exactly like :meth:`query`, plus a :class:`RetrievalTrace`.

        This is the canonical diagnostics entry point for Haven's
        Retrieval Inspector: it runs precisely the pipeline :meth:`query`
        runs — indeed :meth:`query` is defined as this method's first
        return value — so the returned context string is always
        byte-for-byte identical to what :meth:`query` alone would produce.
        The trace is assembled from values the pipeline already computes;
        nothing is recomputed with different logic, and no ranking or
        retrieval decision is made twice.

        As of Phase 1.5, this method also runs :class:`ContextPlanner` once,
        before retrieval begins, and attaches its output to the trace as
        :attr:`~obsidian.ontology.retrieval_models.RetrievalTrace.context_plan`.

        As of Phase 3, the resulting
        :class:`~obsidian.memory_engine.context_planner.ContextPlan` is no
        longer purely observational: after
        :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`
        scores every candidate, this method runs
        :class:`~obsidian.memory_engine.category_preference.CategoryPreferenceScorer`
        to add a small, bounded, deterministic score bonus to any candidate
        whose category the plan requested, *before* handing the (possibly
        adjusted) scores to :class:`AcceptanceStage`. This can change which
        candidates are accepted, their ranking order, and therefore the
        returned context string — see
        :mod:`~obsidian.memory_engine.category_preference`'s module
        docstring for the full design, including why this is a bounded,
        soft preference rather than a hard filter. Every
        :class:`~obsidian.ontology.retrieval_models.CandidateTrace` on the
        returned trace exposes ``base_score`` (the pre-bonus score),
        ``category_preference_bonus``, and ``final_score`` (post-bonus) so
        this influence is fully inspectable per candidate.

        As of Phase 2, this method also runs
        :func:`~obsidian.memory_engine.coverage_analyzer.analyze_coverage`
        once, after acceptance and slot allocation finish, and attaches its
        output to the trace as
        :attr:`~obsidian.ontology.retrieval_models.RetrievalTrace.coverage`.
        This remains purely observational: the resulting
        :class:`~obsidian.memory_engine.coverage_analyzer.CoverageReport` is
        never read back to retry retrieval or change any prior decision —
        Phase 3 changes retrieval's *raw material* (which candidates were
        accepted going into coverage analysis), not coverage analysis
        itself, which still runs strictly downstream and read-only.

        As of Phase 4, this method also runs
        :func:`~obsidian.memory_engine.gap_recovery.decide_gap_recovery`
        once, immediately after coverage analysis, deriving a
        :class:`~obsidian.memory_engine.gap_recovery.GapRecoveryDecision`
        from ``context_plan`` and the coverage report alone, and attaches it
        to the trace as
        :attr:`~obsidian.ontology.retrieval_models.RetrievalTrace.gap_recovery`.
        This is also purely observational: no retrieval retry is issued, even
        when the decision recommends one.

        As of Phase A, this method also runs
        :meth:`~obsidian.memory_engine.project_state.ProjectStateBuilder.build`
        once, immediately after slot allocation, deriving a
        :class:`~obsidian.memory_engine.project_state.ProjectState` from
        ``allocated`` alone -- the same candidates :class:`ContextBuilder`
        already rendered into ``context`` -- and attaches it to the trace as
        :attr:`~obsidian.ontology.retrieval_models.RetrievalTrace.project_state`.
        This is also purely observational: it is built strictly after
        ``context`` already exists, so it cannot influence retrieval,
        ranking, acceptance, allocation, ``WorkingContext``, or any rendered
        prompt.

        See ``obsidian/docs/ARCHITECTURE.md`` and
        :mod:`obsidian.memory_engine.context_planner` /
        :mod:`obsidian.memory_engine.category_preference` /
        :mod:`obsidian.memory_engine.coverage_analyzer` /
        :mod:`obsidian.memory_engine.gap_recovery` /
        :mod:`obsidian.memory_engine.project_state` for the full phasing.

        Parameters
        ----------
        raw_query : str
            The raw user query text.

        Returns
        -------
        tuple[str, RetrievalTrace]
            The same context string :meth:`query` returns, paired with a
            :class:`~obsidian.ontology.retrieval_models.RetrievalTrace`
            describing every candidate considered (accepted or rejected),
            pipeline-level counts/timing for this run, (Phase 1.5) the
            :class:`ContextPlan` produced for this query, (Phase 2) the
            :class:`~obsidian.memory_engine.coverage_analyzer.CoverageReport`
            comparing that plan's requirements against this run's accepted
            candidates, (Phase 4) the
            :class:`~obsidian.memory_engine.gap_recovery.GapRecoveryDecision`
            derived from that plan and coverage report, and (Phase A) the
            :class:`~obsidian.memory_engine.project_state.ProjectState`
            derived from this run's allocated candidates.
        """
        start = time.perf_counter()

        # Phase 1.5 / Step 1 (ProjectState x WorkingContext integration):
        # the rewrite/retrieve/merge/validity-filter/rank prefix, including
        # the Context Planner call, now lives in exactly one place -- see
        # _run_retrieval and _RetrievalPrefix's docstrings. Its output is
        # only ever attached to the trace below (see _context_plan_trace)
        # -- nothing here reads context_plan back to influence rewriting,
        # retrieval, ranking, acceptance, allocation, or context building,
        # so this addition cannot change the context string this method
        # returns.
        prefix = self._run_retrieval(raw_query)
        now = prefix.now
        context_plan = prefix.context_plan

        # Phase 3: nudge each candidate's score by a small, bounded amount
        # when its category was requested by context_plan -- see
        # obsidian.memory_engine.category_preference's module docstring for
        # the full design. This is the first stage in the pipeline where the
        # plan actually changes what gets accepted/ranked/rendered, rather
        # than being attached to the trace purely for diagnostics.
        preference_scores = self._category_preference_scorer.score(
            prefix.ranked_all, context_plan
        )
        preference_by_id = {
            score.ranked_candidate.candidate.knowledge_object.id: score
            for score in preference_scores
        }
        ranked_all = [score.as_ranked_candidate() for score in preference_scores]

        decisions = self._acceptance_stage.accept(
            ranked_all, self._config, self._acceptance_config
        )
        accepted_candidates = [d.candidate for d in decisions if d.accepted]
        allocated = self._slot_allocator.allocate(accepted_candidates, self._config)
        context = self._context_builder.build(allocated)

        # Phase A (ProjectState): derive a deterministic, read-only snapshot
        # of "what this run's accepted candidates say about where the
        # project stands" from the same allocated list ContextBuilder just
        # rendered. Purely observational -- its output is only ever attached
        # to the trace below (see _project_state_trace) and never read back
        # to influence retrieval, ranking, acceptance, allocation,
        # WorkingContext, or any rendered prompt. See
        # obsidian.memory_engine.project_state's module docstring for the
        # full Phase A design and scope.
        project_state = self._project_state_builder.build(allocated, now=now)

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        allocated_ids = {rc.candidate.knowledge_object.id for rc in allocated}

        candidate_traces = []
        for position, decision in enumerate(decisions, start=1):
            ranked_candidate = decision.candidate
            ko = ranked_candidate.candidate.knowledge_object
            preference_score = preference_by_id[ko.id]
            accepted = ko.id in allocated_ids
            if accepted:
                rejection_reason = None
                threshold_used = None
            elif decision.accepted:
                # Survived AcceptanceStage but didn't fit the slot budget.
                rejection_reason = REJECTION_SLOT_BUDGET_EXCEEDED
                threshold_used = float(self._config.max_results)
            else:
                rejection_reason = decision.rejection_reason
                threshold_used = decision.threshold_used
            candidate_traces.append(
                CandidateTrace(
                    knowledge_object_id=ko.id,
                    canonical_fact=ko.canonical_fact,
                    memory_type=ko.memory_type,
                    matched_by_keyword=ko.id in prefix.matched_by_keyword,
                    matched_by_ontology=ko.id in prefix.matched_by_ontology,
                    activation_score=ranked_candidate.candidate.activation_score,
                    attachment_relevance=ranked_candidate.candidate.attachment_relevance,
                    keyword_overlap_score=ranked_candidate.candidate.keyword_overlap_score,
                    importance=ko.importance,
                    confidence=ko.confidence,
                    final_score=ranked_candidate.final_score,
                    accepted=accepted,
                    rejection_reason=rejection_reason,
                    final_rank=position,
                    threshold_used=threshold_used,
                    score_gap=decision.score_gap,
                    relative_score=decision.relative_score,
                    abstained=decision.abstained,
                    score_breakdown=dict(ranked_candidate.score_breakdown),
                    base_score=preference_score.base_score,
                    category_preference_bonus=preference_score.category_preference_bonus,
                )
            )

        # Phase 2: compare the plan's requirements against what this run
        # actually accepted, purely for diagnostics. Its output is only ever
        # attached to the trace below (see _coverage_report_trace) -- nothing
        # here reads coverage_report back to influence retrieval, ranking,
        # acceptance, allocation, or context building, so this addition
        # cannot change the context string this method returns.
        coverage_report = analyze_coverage(context_plan, candidate_traces)

        # Phase 4: decide whether another retrieval pass would be warranted,
        # purely for diagnostics. Its output is only ever attached to the
        # trace below (see _gap_recovery_trace) -- nothing here reads
        # gap_recovery_decision back to retry retrieval or change any prior
        # decision, so this addition cannot change the context string this
        # method returns. See obsidian.memory_engine.gap_recovery's module
        # docstring for the full design.
        gap_recovery_decision = decide_gap_recovery(context_plan, coverage_report)

        trace = RetrievalTrace(
            query=raw_query,
            rewritten_queries=prefix.rewritten_queries,
            candidates=tuple(candidate_traces),
            pipeline_stats=RetrievalPipelineStats(
                total_ontology_candidates=prefix.ontology_total,
                total_keyword_candidates=prefix.keyword_total,
                total_merged_candidates=len(prefix.candidates),
                total_accepted_candidates=len(allocated),
                total_rejected_candidates=len(ranked_all) - len(allocated),
                final_context_size=len(context),
                retrieval_latency_ms=elapsed_ms,
            ),
            context_plan=_context_plan_trace(context_plan),
            coverage=_coverage_report_trace(coverage_report),
            gap_recovery=_gap_recovery_trace(gap_recovery_decision),
            project_state=_project_state_trace(project_state),
            rewriting_enabled=self._query_rewriter is not None,
        )
        return context, trace

    # ------------------------------------------------------------------
    # Additive public API (Working Context / Structured Prompt)
    # ------------------------------------------------------------------

    def _allocate(self, raw_query: str) -> List[RankedCandidate]:
        """Run retrieval through slot allocation, independent of rendering.

        Calls the shared :meth:`_run_retrieval` prefix (see that method and
        :class:`_RetrievalPrefix`'s docstrings) and then
        :meth:`_accept_and_allocate`, so :meth:`query_working_context` and
        :meth:`query_structured` share their retrieval, merge, validity-
        filter, and ranking logic with :meth:`query`/:meth:`query_with_trace`
        -- only acceptance/allocation policy differs (see "Deliberately not
        planner-aware" below), and even that final step now runs through the
        same :meth:`_accept_and_allocate` helper :meth:`query_structured`
        uses. Before this refactor, this method re-implemented the entire
        retrieval prefix inline, independently from :meth:`query_with_trace`'s
        copy -- two independently-maintained copies of the same seed
        construction and stage-wiring logic, which is exactly the kind of
        duplication ``docs/architecture/PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md``
        flagged as blocking ``ProjectState`` integration (there was no single
        place for :meth:`query_structured` to obtain both ``allocated`` and a
        :class:`~obsidian.memory_engine.context_planner.ContextPlan`).

        Deliberately **not** planner-aware for scoring (Phase 3): unlike
        :meth:`query_with_trace`, this method never runs
        :class:`~obsidian.memory_engine.category_preference.CategoryPreferenceScorer`
        between ranking and acceptance, so
        :meth:`query_working_context`/:meth:`query_structured` see
        :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s
        raw scores, unmodified for every candidate normal retrieval actually
        found. Extending Working Context/structured-prompt construction to be
        category-aware is out of this phase's scope; see
        :mod:`~obsidian.memory_engine.category_preference`'s module
        docstring for the scope this phase committed to.

        One narrow, additive exception: ``context_plan`` is now passed
        through to :meth:`_accept_and_allocate`, which reads it only to
        decide whether its category-fallback branch may run -- and that
        branch only ever fires when normal acceptance already produced zero
        candidates, so it cannot re-rank, re-score, or displace anything
        normal retrieval found. See :meth:`_accept_and_allocate`'s own
        docstring ("Category-fallback retrieval") for the full contract.

        Parameters
        ----------
        raw_query : str
            The raw user query text.

        Returns
        -------
        list[RankedCandidate]
            The same allocated candidates :meth:`query_with_trace` would
            pass to :class:`~obsidian.memory_engine.context_builder.ContextBuilder`,
            when neither run's :class:`~obsidian.memory_engine.context_planner.ContextPlan`
            requested any category (the sentinel case where the two methods'
            acceptance/allocation input is identical).
        """
        prefix = self._run_retrieval(raw_query)
        return self._accept_and_allocate(
            prefix.ranked_all, prefix.context_plan, prefix.now
        )

    def query_working_context(self, raw_query: str) -> List[WorkingContext]:
        """Resolve *raw_query* into deterministic :class:`WorkingContext` objects.

        Runs the same retrieval/ranking/acceptance/allocation pipeline as
        :meth:`query`, but renders the allocated candidates with
        :class:`~obsidian.memory_engine.working_context_builder.WorkingContextBuilder`
        instead of :class:`~obsidian.memory_engine.context_builder.ContextBuilder`.
        Purely additive: does not call, and cannot affect, :meth:`query`,
        :meth:`query_with_trace`, or :class:`ContextBuilder`.

        Every ``TOPIC``-kind context's title is then resolved from its
        ``anchor_concept_id`` to that concept's human-readable
        :attr:`~obsidian.ontology.models.Concept.label`, when the concept
        graph has one, via :meth:`_resolve_topic_titles` -- see that method's
        docstring for why this lives here rather than in
        :class:`WorkingContextBuilder` itself.

        Parameters
        ----------
        raw_query : str
            The raw user query text.

        Returns
        -------
        list[WorkingContext]
            One or more Working Contexts grouping the allocated candidates
            by role. Never empty — see
            :meth:`~obsidian.memory_engine.working_context_builder.WorkingContextBuilder.build`.
        """
        allocated = self._allocate(raw_query)
        contexts = self._working_context_builder.build(allocated)
        return self._resolve_topic_titles(contexts)

    def _resolve_topic_titles(
        self, contexts: List[WorkingContext]
    ) -> List[WorkingContext]:
        """Replace a ``TOPIC`` context's UUID title with its concept's label.

        :class:`~obsidian.memory_engine.working_context_builder.WorkingContextBuilder`
        has no access to :class:`ConceptGraph` by design (see its own module
        docstring) and titles a ``TOPIC`` context with ``str(anchor_concept_id)``
        for lack of anything better. This is a query-time, read-only
        ``ConceptGraph.get_concept`` lookup against the graph this engine
        already holds -- no retrieval, no second candidate/ranking pass, no
        new stage -- so both :meth:`query_working_context` and
        :meth:`query_structured` see a human-readable section title wherever
        the graph actually has one. A context whose anchor concept is absent
        from the graph (not expected in practice, since every anchor comes
        from a candidate's own ``supporting_concepts``) keeps its UUID title
        unchanged, exactly as before this method existed. ``GENERAL`` and
        ``PROJECT``-kind contexts are never touched -- ``GENERAL`` already has
        a fixed, human-readable title, and no code path constructs a
        ``PROJECT``-kind context yet.

        Parameters
        ----------
        contexts : list[WorkingContext]
            Contexts as built by :class:`WorkingContextBuilder`. Not mutated
            (``WorkingContext`` is frozen); a new list is returned.

        Returns
        -------
        list[WorkingContext]
            The same contexts, in the same order, with ``TOPIC`` titles
            resolved to a concept label wherever the graph has one.
        """
        resolved: List[WorkingContext] = []
        for context in contexts:
            if (
                context.kind is ContextKind.TOPIC
                and context.anchor_concept_id is not None
                and self._concept_graph.has_concept(context.anchor_concept_id)
            ):
                label = self._concept_graph.get_concept(context.anchor_concept_id).label
                context = replace(context, title=label)
            resolved.append(context)
        return resolved

    def query_structured(self, raw_query: str) -> str:
        """Resolve *raw_query* into Haven's XML-delimited structured prompt.

        Runs retrieval through allocation via the same shared
        :meth:`_run_retrieval` prefix and :meth:`_accept_and_allocate` helper
        :meth:`_allocate` uses (retrieval executes exactly once for this
        call), builds Working Contexts from the allocated candidates exactly
        as :meth:`query_working_context` would, then renders them with
        :class:`~obsidian.memory_engine.structured_prompt_builder.StructuredPromptBuilder`,
        using *raw_query* as the prompt's user request. Purely additive:
        does not call, and cannot affect, :meth:`query`,
        :meth:`query_with_trace`, or :class:`ContextBuilder`.

        ProjectState availability (Step 1 of
        ``docs/architecture/PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md``)
        ------------------------------------------------------------------
        When this query's :class:`~obsidian.memory_engine.context_planner.ContextPlan`
        classifies as :attr:`~obsidian.memory_engine.context_planner.TaskMode.CONTINUATION`,
        this method also builds a
        :class:`~obsidian.memory_engine.project_state.ProjectState` from the
        same ``allocated`` list (via
        :meth:`~obsidian.memory_engine.project_state.ProjectStateBuilder.build`,
        exactly as :meth:`query_with_trace` already does) and passes it to
        :meth:`StructuredPromptBuilder.render` as its optional
        ``project_state`` parameter, which renders it as a ``<ProjectState>``
        element. For every other task mode -- including
        :attr:`~obsidian.memory_engine.context_planner.TaskMode.POINTED_QA`,
        the common case -- ``project_state`` stays ``None`` and no
        ``<ProjectState>`` element is rendered at all; the prompt string is
        then byte-identical to before this integration existed.

        When ``allocated`` would otherwise be empty for a ``CONTINUATION``
        query, ``_accept_and_allocate``'s category-fallback branch (see that
        method's docstring) may have already filled it with bounded,
        non-lexical top-ranked memories per requested category before this
        method ever sees it -- so a generic, vault-vocabulary-free query like
        "Continue." can still produce a populated ``<ProjectState>`` and
        ``<WorkingContext>`` here, rather than the all-gaps, zero-candidate
        result described in
        ``docs/architecture/GENERIC_CONTINUATION_QUERY_ANALYSIS.md``.

        Parameters
        ----------
        raw_query : str
            The raw user query text. Used both to drive retrieval and as
            the rendered ``<UserRequest>`` text.

        Returns
        -------
        str
            The deterministic, XML-delimited structured prompt.
        """
        prefix = self._run_retrieval(raw_query)
        allocated = self._accept_and_allocate(
            prefix.ranked_all, prefix.context_plan, prefix.now
        )
        contexts = self._working_context_builder.build(allocated)
        contexts = self._resolve_topic_titles(contexts)

        project_state: Optional[ProjectState] = None
        if prefix.context_plan.task_mode is TaskMode.CONTINUATION:
            project_state = self._project_state_builder.build(allocated, now=prefix.now)

        return self._structured_prompt_builder.render(
            contexts, raw_query, project_state=project_state
        )
