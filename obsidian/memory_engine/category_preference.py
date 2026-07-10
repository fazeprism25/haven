"""Category-aware ranking adjustment for Haven's read pipeline (Phase 3).

Phase 1 (:mod:`~obsidian.memory_engine.context_planner`) produces a
:class:`~obsidian.memory_engine.context_planner.ContextPlan` describing which
named context categories a query needs. Phase 1.5 and Phase 2 (see
:mod:`~obsidian.memory_engine.engine` and
:mod:`~obsidian.memory_engine.coverage_analyzer`) wired that plan, and a
report of how well retrieval satisfied it, onto ``RetrievalTrace`` --
purely for diagnostics. Nothing before this module ever let a plan change
what retrieval actually returns.

This module, Phase 3, is the first behavior-changing use of the plan: it
takes the full, unfiltered list of
:class:`~obsidian.ontology.retrieval_models.RankedCandidate` objects
:class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`
already scored, and a :class:`~obsidian.memory_engine.context_planner.ContextPlan`,
and adds a small, fixed, deterministic score bonus to every candidate whose
resolved category the plan requested. Nothing is removed and nothing is
re-scored from scratch -- every candidate that was eligible before this
stage runs is still eligible after it.

::

    RankedCandidate[]        ContextPlan
    (from DeterministicRanker)     │
              │                    │
              └──────────┬─────────┘
                         ▼
              CategoryPreferenceScorer
                         │
                         ▼
            CategoryPreferenceScore[]
                         │
                         ▼ (.as_ranked_candidate() per entry)
                RankedCandidate[]  ──►  AcceptanceStage (unchanged)

Why a soft preference, not a hard filter
-------------------------------------------
A hard filter (drop every candidate whose category the plan didn't ask for)
would make an incomplete or over-narrow plan actively destructive: a
mis-classified query, or a query whose real answer happens to live in a
category the fixed lexical table didn't anticipate, would lose that answer
outright with no way to recover it downstream. Nothing about
:class:`~obsidian.memory_engine.context_planner.ContextPlanner`'s
classification is guaranteed correct -- it is a fixed lexical pattern table
(see that module's docstring), not a verified understanding of the query --
so treating its output as a hard requirement would let a classification
mistake silently delete evidence.

A bounded additive bonus instead only ever *reorders* candidates that were
already eligible: it can move a requested-category candidate ahead of an
unrequested one it was close to, and it can pull a borderline candidate
above the absolute floor or abstention threshold (see "Interaction with
AcceptanceStage" below) -- but it can never make an unrequested-category
candidate disappear, and a sufficiently stronger unrequested-category
candidate still wins (the bonus is small relative to the score range; see
:data:`CATEGORY_PREFERENCE_BONUS`). This is the same "keep both, let the
fuller context disambiguate" posture
:mod:`~obsidian.memory_engine.acceptance_stage` already uses for
same-magnitude keyword ties (its shared-supporting-concept exemption) and
:mod:`~obsidian.memory_engine.coverage_analyzer` uses for category
resolution (observe and report, never re-derive or discard) -- soft
influence is the pattern this pipeline already trusts, extended one stage
further.

Expected benefits
------------------
* Retrieval precision improves for the common case where the plan's
  classification is right: a coding/debugging query's constraints and
  implementation-state candidates get a deterministic nudge toward the top
  of the ranking and toward surviving acceptance, without requiring the
  keyword/ontology evidence alone to already put them there.
* Because the bonus is additive and small, it acts mostly as a tie-breaker
  among already-close candidates, which is exactly where category context
  is most useful -- when evidence-based scoring alone can't distinguish two
  similarly-relevant memories.

Failure modes
--------------
* **Wrong task-mode classification.** If
  :class:`~obsidian.memory_engine.context_planner.ContextPlanner` classifies
  a query into the wrong :class:`~obsidian.memory_engine.context_planner.TaskMode`,
  this stage will nudge the wrong categories. The bonus's boundedness limits
  the damage (a clearly-better candidate in the *right* category still
  wins), but a close race can still be tipped the wrong way. This is a
  planner-accuracy problem, not something this module can detect or correct
  -- see :mod:`~obsidian.memory_engine.context_planner` for that scope.
* **Category resolution is partial.** Exactly like
  :mod:`~obsidian.memory_engine.coverage_analyzer` (this module reuses its
  ``MEMORY_TYPE_CATEGORY`` table verbatim -- see "Design decisions" below),
  a candidate whose ``MemoryType`` has no ``ContextCategory`` entry
  (``GOAL``, ``PROJECT``, ``PERSON``, ``EVENT``, ``SKILL``, ``PREFERENCE``)
  can never receive a bonus, even if it is the single best answer to the
  query. It is neither helped nor hurt -- it is scored exactly as it would
  have been before this module existed -- but it can never win a close race
  against a requested-category candidate on category grounds alone.
* **Necessity is not yet weighted.** Every category in
  ``ContextPlan.requirements`` receives the same flat bonus regardless of
  whether its :class:`~obsidian.memory_engine.context_planner.Necessity` is
  ``REQUIRED`` or ``OPTIONAL``. In the current fixed requirement tables
  every entry happens to be ``REQUIRED`` (see
  ``context_planner._REQUIREMENTS_BY_MODE``), so this has no observable
  effect today; it would matter only once a future planner phase starts
  emitting ``OPTIONAL`` requirements, at which point tiering the bonus by
  necessity is the natural refinement -- not implemented now, since nothing
  in this codebase yet produces an ``OPTIONAL`` requirement to test it
  against.

Interaction with future gap recovery
---------------------------------------
A future ``GapDetector`` (``docs/architecture/CONTEXT_PLAN_OBJECT.md`` §5)
would trigger a bounded retry when :mod:`~obsidian.memory_engine.coverage_analyzer`
reports a ``REQUIRED`` category as unmet. This module changes *what gets
retrieved first time round* (by reordering, never by filtering), which
changes coverage's raw material -- a category that would have been MISSING
under evidence-based ranking alone may now be FULL because a
borderline-but-relevant candidate cleared acceptance thanks to its bonus.
That is the intended effect: this stage is a cheaper first line of defense
against exactly the gaps a retry would otherwise have to fix. It does not
replace gap recovery -- a category with *no* eligible candidate at all
still shows as MISSING no matter how large the bonus, since the bonus can
only reorder/rescue candidates that exist, never conjure one that wasn't
retrieved -- and it introduces no retry, no re-ranking loop, and no
dependency on ``CoverageReport`` itself (this module never reads a
``CoverageReport``; it runs upstream of one, on ``ContextPlan`` alone).

Explicitly out of scope
------------------------
* **No retrieval.** Candidates are consumed as already produced by
  :class:`~obsidian.memory_engine.hybrid_candidate_retriever.HybridCandidateRetriever`;
  nothing here fetches, re-fetches, or discards a candidate.
* **No re-scoring of the seven ranking components.** This module never
  recomputes ``activation``, ``attachment_relevance``, ``keyword_overlap``,
  ``importance``, ``confidence``, ``recency``, or ``confirmation_count`` --
  those remain exclusively
  :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s.
  It only adds one further, independent term on top of the composite score
  that ranker already produced.
* **No acceptance, allocation, or context-building changes.**
  :class:`~obsidian.memory_engine.acceptance_stage.AcceptanceStage`,
  :class:`~obsidian.memory_engine.deterministic_slot_allocator.DeterministicSlotAllocator`,
  and :class:`~obsidian.memory_engine.context_builder.ContextBuilder` are
  unmodified; they simply see a
  :class:`~obsidian.ontology.retrieval_models.RankedCandidate` list whose
  ``final_score`` values already include this stage's bonus.
* **No planner changes.**
  :class:`~obsidian.memory_engine.context_planner.ContextPlan` and
  :class:`~obsidian.memory_engine.context_planner.ContextPlanner` are
  consumed read-only; this module never mutates or re-derives a plan.
* **No hard filtering.** No candidate is ever removed, reclassified, or
  excluded from consideration by this module -- see "Why a soft preference,
  not a hard filter" above.

Interaction with AcceptanceStage
------------------------------------
:class:`~obsidian.memory_engine.acceptance_stage.AcceptanceStage` reads
``RankedCandidate.final_score`` for its absolute floor, abstention check,
score-gap cut, and relative-quality floor. Because this module's output
feeds ``AcceptanceStage`` in place of ``DeterministicRanker``'s raw output,
the bonus genuinely participates in every one of those decisions -- a
requested-category candidate just below ``minimum_candidate_score`` or
``abstention_score`` can clear it once its bonus is added, and the score-gap
cut and relative floor are computed against the bonus-adjusted scores, not
the raw ones. This is the intended mechanism for "planner influence must be
bounded, not absolute": the bonus is small enough that it changes outcomes
only for candidates that were already close to a threshold, never for ones
far below it.

Design decisions
-----------------
* **Category resolution reuses** :data:`~obsidian.memory_engine.coverage_analyzer.MEMORY_TYPE_CATEGORY`
  **and** :func:`~obsidian.memory_engine.coverage_analyzer.resolve_category`
  **verbatim**, rather than a second, independently-maintained table. Both
  this module and coverage analysis must agree on what category a
  ``MemoryType`` belongs to -- if they disagreed, a candidate could receive
  a preference bonus for a category coverage analysis simultaneously
  insists it did not contribute to, which would be a genuine, confusing
  inconsistency between two diagnostics that are supposed to describe the
  same run.
* **A single fixed, non-configurable bonus constant
  (**:data:`CATEGORY_PREFERENCE_BONUS`**), not a new** ``RetrievalConfig``
  **field.** Mirrors
  :data:`~obsidian.memory_engine.deterministic_ranker.RECENCY_SCALE_DAYS`:
  a hardcoded module constant for a shape decision that isn't part of the
  existing seven-weight scoring surface, rather than growing
  ``RetrievalConfig`` for a single new stage. Kept deliberately small
  (0.05 -- just above ``AcceptanceConfig.min_gap``'s default of 0.04) so it
  can tip a close race or a borderline threshold check without ever
  dominating a real evidence-based score difference.
* **A category matches at most once per candidate.**
  :func:`~obsidian.memory_engine.coverage_analyzer.resolve_category` is a
  function, not a multi-valued mapping -- a candidate's ``MemoryType``
  resolves to exactly one ``ContextCategory`` or none. There is therefore no
  way for a single candidate to accumulate more than one bonus, which is
  itself part of why "bounded" holds structurally, not just because the
  constant is small.
* **``requested_category`` is precomputed and carried on**
  :class:`CategoryPreferenceScore` **for explainability**, even though it is
  a pure function of ``memory_type``, mirroring
  :class:`~obsidian.ontology.retrieval_models.CandidateTrace`'s existing
  practice of carrying values a caller could technically recompute (e.g.
  ``score_breakdown``) so the Retrieval Inspector never has to re-derive a
  ranking decision to explain it.

Determinism
-----------
:meth:`CategoryPreferenceScorer.score` is a pure function of its two
inputs: no clock read, no randomness, no I/O, no LLM call. The bonus is a
fixed constant; category resolution is a fixed table lookup; the output is
re-sorted with the exact same ``(-final_score, str(knowledge_object.id))``
tie-break :class:`~obsidian.ontology.retrieval_models.RankedCandidate`
already defines (via :meth:`CategoryPreferenceScore.as_ranked_candidate`
feeding a plain ``sorted()`` call), so ties introduced by two candidates
receiving the same bonus are broken exactly as deterministically as ties
were broken before this module existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Set

from obsidian.memory_engine.context_planner import ContextCategory, ContextPlan
from obsidian.memory_engine.coverage_analyzer import resolve_category
from obsidian.ontology.retrieval_models import RankedCandidate

#: Fixed, non-configurable preference bonus (in the same [0.0, 1.0] units as
#: ``RankedCandidate.final_score``) added to a candidate whose resolved
#: category appears in the originating ``ContextPlan.requirements``. Not
#: part of ``RetrievalConfig`` -- see the module docstring's "Design
#: decisions". Deliberately small: just above
#: ``AcceptanceConfig.min_gap``'s default (0.04), so it can tip a close
#: race or a borderline acceptance threshold without overriding a real
#: evidence-based score difference.
CATEGORY_PREFERENCE_BONUS: float = 0.05


@dataclass(frozen=True)
class CategoryPreferenceScore:
    """One candidate's category-preference adjustment, fully explained.

    Parameters
    ----------
    ranked_candidate : RankedCandidate
        The original, unmodified :class:`~obsidian.ontology.retrieval_models.RankedCandidate`
        produced by :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`.
        ``ranked_candidate.final_score`` is this entry's ``base_score``.
    requested_category : ContextCategory, optional
        The :class:`~obsidian.memory_engine.context_planner.ContextCategory`
        this candidate's ``MemoryType`` resolved to, if any, and if that
        category appears in the plan's requirements. ``None`` when the
        candidate's memory type has no category mapping, or when its
        category was not requested -- either way, ``category_preference_bonus``
        is ``0.0`` in that case.
    category_preference_bonus : float
        The bonus actually applied. Either ``0.0`` or
        :data:`CATEGORY_PREFERENCE_BONUS`; never anything else.
    final_score : float
        ``base_score + category_preference_bonus``, clamped to ``[0.0, 1.0]``.

    Raises
    ------
    ValueError
        If ``final_score`` is outside ``[0.0, 1.0]``, or if
        ``category_preference_bonus`` is negative or exceeds
        :data:`CATEGORY_PREFERENCE_BONUS`.
    """

    ranked_candidate: RankedCandidate
    requested_category: Optional[ContextCategory]
    category_preference_bonus: float
    final_score: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.final_score <= 1.0:
            raise ValueError(
                f"final_score must be in [0.0, 1.0]; got {self.final_score}"
            )
        if not 0.0 <= self.category_preference_bonus <= CATEGORY_PREFERENCE_BONUS:
            raise ValueError(
                "category_preference_bonus must be in "
                f"[0.0, {CATEGORY_PREFERENCE_BONUS}]; got "
                f"{self.category_preference_bonus}"
            )

    @property
    def base_score(self) -> float:
        """The pre-bonus composite score, exactly as ``DeterministicRanker`` computed it."""
        return self.ranked_candidate.final_score

    def as_ranked_candidate(self) -> RankedCandidate:
        """Project to a :class:`RankedCandidate` carrying the adjusted ``final_score``.

        Reuses ``ranked_candidate.candidate`` and ``ranked_candidate.score_breakdown``
        verbatim -- only ``final_score`` differs from the wrapped
        ``ranked_candidate``. This is the value handed to
        :class:`~obsidian.memory_engine.acceptance_stage.AcceptanceStage`,
        which is unmodified and only ever sees ``RankedCandidate`` instances.
        """
        return RankedCandidate(
            candidate=self.ranked_candidate.candidate,
            final_score=self.final_score,
            score_breakdown=self.ranked_candidate.score_breakdown,
        )


class CategoryPreferenceScorer:
    """Applies a bounded, deterministic category-preference bonus to ranked candidates.

    Stateless and reusable across calls to :meth:`score` with different
    candidate lists or plans.

    Examples
    --------
    >>> scorer = CategoryPreferenceScorer()
    >>> scores = scorer.score(ranked_all, context_plan)  # doctest: +SKIP
    >>> scores[0].final_score >= scores[-1].final_score  # doctest: +SKIP
    True
    """

    def score(
        self,
        ranked_candidates: List[RankedCandidate],
        context_plan: ContextPlan,
    ) -> List[CategoryPreferenceScore]:
        """Adjust *ranked_candidates* by *context_plan*'s requested categories.

        Parameters
        ----------
        ranked_candidates : list[RankedCandidate]
            Every scored candidate for this query, typically the unfiltered
            output of
            :meth:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker.score_all`.
            Not mutated.
        context_plan : ContextPlan
            The plan produced for this query. ``context_plan.requirements``
            (regardless of ``necessity`` -- see the module docstring's
            "Design decisions") is read to determine which categories are
            requested; nothing else on the plan is read. An empty
            ``requirements`` tuple (the ``TaskMode.POINTED_QA`` sentinel)
            means every candidate gets a ``0.0`` bonus, i.e. this stage is a
            no-op.

        Returns
        -------
        list[CategoryPreferenceScore]
            One entry per input candidate, sorted by descending
            ``final_score`` with the same ``(-final_score, str(id))``
            tie-break :class:`~obsidian.ontology.retrieval_models.RankedCandidate`
            defines.
        """
        requested_categories = {
            requirement.category for requirement in context_plan.requirements
        }

        scores = [
            self._score_one(ranked_candidate, requested_categories)
            for ranked_candidate in ranked_candidates
        ]
        return sorted(
            scores,
            key=lambda s: (
                -s.final_score,
                str(s.ranked_candidate.candidate.knowledge_object.id),
            ),
        )

    @staticmethod
    def _score_one(
        ranked_candidate: RankedCandidate,
        requested_categories: Set[ContextCategory],
    ) -> CategoryPreferenceScore:
        """Compute the bonus and adjusted score for a single candidate."""
        memory_type = ranked_candidate.candidate.knowledge_object.memory_type
        category = resolve_category(memory_type)

        if category is not None and category in requested_categories:
            bonus = CATEGORY_PREFERENCE_BONUS
            matched_category = category
        else:
            bonus = 0.0
            matched_category = None

        final_score = min(1.0, ranked_candidate.final_score + bonus)

        return CategoryPreferenceScore(
            ranked_candidate=ranked_candidate,
            requested_category=matched_category,
            category_preference_bonus=bonus,
            final_score=final_score,
        )
