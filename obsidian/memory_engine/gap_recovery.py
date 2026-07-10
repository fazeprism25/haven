"""Deterministic Gap Recovery Decision for Haven's read pipeline (Phase 4 only).

Phase 1 (:mod:`~obsidian.memory_engine.context_planner`) produces a
:class:`~obsidian.memory_engine.context_planner.ContextPlan` describing which
named context categories a query needs. Phase 2
(:mod:`~obsidian.memory_engine.coverage_analyzer`) compares that plan against
what retrieval actually accepted and reports a
:class:`~obsidian.memory_engine.coverage_analyzer.CoverageReport`. Both are
purely observational today -- see that module's docstring: "nothing reacts to
missing coverage. This is intentional."

This module, Phase 4, is the next architectural layer, and nothing more: it
answers exactly one question -- **"should Haven attempt another
retrieval?"** -- as a deterministic decision derived from a
:class:`~obsidian.memory_engine.context_planner.ContextPlan` and a
:class:`~obsidian.memory_engine.coverage_analyzer.CoverageReport`, and
nothing else. It does not retry retrieval, does not modify the plan, and does
not change what any prior stage decided.

::

    ContextPlan   CoverageReport
         │              │
         └──────┬───────┘
                ▼
        decide_gap_recovery
                ▼
        GapRecoveryDecision

Inputs, and only inputs
------------------------
:func:`decide_gap_recovery` reads exactly two objects -- a
:class:`~obsidian.memory_engine.context_planner.ContextPlan` and a
:class:`~obsidian.memory_engine.coverage_analyzer.CoverageReport` -- both
already-computed, immutable records. It has no access to, and imports
nothing from, :class:`~obsidian.memory_engine.hybrid_candidate_retriever.HybridCandidateRetriever`,
:class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`,
any LLM client, or :mod:`obsidian.ontology`. There is nothing this module
could call to retrieve, rank, or resolve a concept even if it wanted to.

Conservative by default
-------------------------
:attr:`GapRecoveryDecision.should_retry` is ``False`` unless a narrow,
obvious deterministic rule fires:

1. **No gap, no retry.** When
   :attr:`~obsidian.memory_engine.coverage_analyzer.CoverageReport.missing_required_categories`
   is empty -- including trivially for the ``TaskMode.POINTED_QA`` sentinel,
   whose ``CoverageReport`` always has empty ``entries`` -- there is nothing
   to recover from. :attr:`GapRecoveryDecision.retry_reason` is
   :attr:`RetryReason.NO_GAP`.
2. **A gap exists, but the plan itself is not trustworthy enough to act on.**
   :attr:`~obsidian.memory_engine.context_planner.ContextPlan.confidence`
   below :data:`MIN_PLAN_CONFIDENCE_FOR_RETRY` means the categories a retry
   would target might themselves be wrong -- retrying against a
   low-confidence plan risks retrieving more noise, not filling a real gap.
   :attr:`GapRecoveryDecision.retry_reason` is
   :attr:`RetryReason.LOW_PLAN_CONFIDENCE`; ``missing_categories`` is still
   populated (the gap is real and worth recording), only ``should_retry``
   stays ``False``.
3. **A gap exists and the plan is confident.** Only this case sets
   ``should_retry=True``, with :attr:`RetryReason.REQUIRED_CATEGORY_MISSING`
   and a fixed :data:`DEFAULT_RETRY_BUDGET`.

Every :class:`~obsidian.memory_engine.context_planner.ContextPlan` this
codebase's :class:`~obsidian.memory_engine.context_planner.ContextPlanner`
produces today has ``confidence=1.0`` (Phase 1 is deterministic-only), so
rule 2 above never fires against real traffic yet -- it exists so a future
LLM-fallback planning phase (which can legitimately produce a lower
``confidence``) has a rule to read without this module changing shape. This
mirrors :class:`~obsidian.memory_engine.context_planner.PlanningMethod`'s own
``LLM_FALLBACK`` value: reserved, not yet reachable, not a speculative
feature bolted on for its own sake.

Explicitly out of scope (this is Phase 4 of a multi-phase design)
-------------------------------------------------------------------
* **No retrieval retries.** This module never calls
  :class:`~obsidian.memory_engine.hybrid_candidate_retriever.HybridCandidateRetriever`
  or any other retrieval component, and issues no second retrieval pass for
  any category.
* **No planner or retrieval modifications.**
  :class:`~obsidian.memory_engine.context_planner.ContextPlan` and
  :class:`~obsidian.memory_engine.coverage_analyzer.CoverageReport` are
  consumed read-only; neither is mutated or re-derived.
* **No prompt or WorkingContext changes.** This module never touches
  :class:`~obsidian.memory_engine.working_context_builder.WorkingContextBuilder`,
  :class:`~obsidian.memory_engine.structured_prompt_builder.StructuredPromptBuilder`,
  or :class:`~obsidian.memory_engine.context_builder.ContextBuilder`.
* **No LLM calls.** :func:`decide_gap_recovery` is a pure function of two
  already-computed records; nothing here reasons about *why* a category is
  missing beyond the counts :class:`~obsidian.memory_engine.coverage_analyzer.CoverageReport`
  already carries.
* **No gap filling.** A ``GapRecoveryDecision`` is a recommendation, not an
  action. Nothing consumes it to change what
  :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
  returns -- see that method's own docstring for the same "observational
  only" guarantee this module's integration makes.

Determinism
-----------
:func:`decide_gap_recovery` is a pure function of its two inputs: no clock
read beyond :attr:`GapRecoveryDecision.created_at` (a diagnostic timestamp,
never read back), no randomness, no I/O, no retry of its own. The same plan
and the same coverage report always produce an equal
:class:`GapRecoveryDecision`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Tuple

from obsidian.memory_engine.context_planner import ContextCategory, ContextPlan
from obsidian.memory_engine.coverage_analyzer import CoverageReport

#: Below this classification confidence, a real gap is not trustworthy
#: enough to recommend a retry over -- the plan itself might have picked the
#: wrong categories, so retrying against them could retrieve more noise
#: rather than fill a genuine gap. A fixed module constant, not a
#: ``ContextPlan``/``RetrievalConfig`` field, mirroring
#: :data:`~obsidian.memory_engine.category_preference.CATEGORY_PREFERENCE_BONUS`'s
#: own "shape decision, not part of an existing tunable surface" reasoning.
MIN_PLAN_CONFIDENCE_FOR_RETRY: float = 0.5

#: Fixed, non-configurable retry budget recommended when a gap recovery
#: retry is warranted. ``ContextPlan`` carries no retry-budget field of its
#: own in this phase (see ``docs/architecture/CONTEXT_PLAN_OBJECT.md`` §2's
#: ``max_gap_retries``, not yet implemented on the real
#: :class:`~obsidian.memory_engine.context_planner.ContextPlan`), so this
#: module supplies its own fixed constant rather than reading one that does
#: not exist yet.
DEFAULT_RETRY_BUDGET: int = 1


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RetryReason(str, Enum):
    """Why a :class:`GapRecoveryDecision` did or did not recommend a retry.

    Values
    ------
    NO_GAP : str
        Every ``REQUIRED`` category in the originating
        :class:`~obsidian.memory_engine.coverage_analyzer.CoverageReport` was
        satisfied. Nothing to recover from.
    LOW_PLAN_CONFIDENCE : str
        A gap exists, but the originating
        :class:`~obsidian.memory_engine.context_planner.ContextPlan`'s own
        classification confidence is below
        :data:`MIN_PLAN_CONFIDENCE_FOR_RETRY` -- not trustworthy enough to
        retry against. Not reachable against any plan
        :class:`~obsidian.memory_engine.context_planner.ContextPlanner`
        produces today (always ``confidence=1.0``); reserved for a future
        LLM-fallback planning phase.
    REQUIRED_CATEGORY_MISSING : str
        A gap exists and the plan is confident enough to retry against. The
        only value that co-occurs with ``should_retry=True``.
    """

    NO_GAP = "no_gap"
    LOW_PLAN_CONFIDENCE = "low_plan_confidence"
    REQUIRED_CATEGORY_MISSING = "required_category_missing"


class RecoveryStrategy(str, Enum):
    """How a future retrieval pass would attempt to close a recorded gap.

    This phase never executes a strategy -- see the module docstring's
    "Explicitly out of scope" -- so only a single concrete value exists
    today, alongside the ``NONE`` sentinel. Additional strategies (e.g.
    widening ontology scope, a targeted sub-query per
    ``docs/architecture/WORKING_CONTEXT_2_DESIGN.md`` §4's "Additional
    Retrieval") are future-phase work; adding one is an additive enum change,
    not a change to this module's shape.

    Values
    ------
    NONE : str
        No retry recommended; there is no strategy to name.
    RETRY_MISSING_CATEGORIES : str
        Re-attempt retrieval for exactly the categories recorded in
        :attr:`GapRecoveryDecision.missing_categories`. The only strategy
        this phase's decision logic ever selects.
    """

    NONE = "none"
    RETRY_MISSING_CATEGORIES = "retry_missing_categories"


# ---------------------------------------------------------------------------
# GapRecoveryDecision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GapRecoveryDecision:
    """Whether Haven should attempt another retrieval pass, and why.

    A plain, immutable data structure, standalone in this phase -- like
    :class:`~obsidian.memory_engine.context_planner.ContextPlan` and
    :class:`~obsidian.memory_engine.coverage_analyzer.CoverageReport`,
    nothing in this codebase consumes it yet; it is only ever attached to a
    :class:`~obsidian.ontology.retrieval_models.RetrievalTrace` for
    diagnostics (see ``obsidian/docs/ARCHITECTURE.md``'s "Gap Recovery
    Decision" section).

    Parameters
    ----------
    should_retry : bool
        Whether another retrieval pass is recommended. ``True`` only when
        ``retry_reason`` is :attr:`RetryReason.REQUIRED_CATEGORY_MISSING`.
    missing_categories : tuple[ContextCategory, ...]
        The unsatisfied ``REQUIRED`` categories this decision is about,
        copied verbatim from
        :attr:`~obsidian.memory_engine.coverage_analyzer.CoverageReport.missing_required_categories`.
        Populated whenever a gap exists, even when ``should_retry`` is
        ``False`` (e.g. :attr:`RetryReason.LOW_PLAN_CONFIDENCE`) -- the gap
        is real and worth recording regardless of whether this decision acts
        on it. Empty exactly when ``retry_reason`` is
        :attr:`RetryReason.NO_GAP`.
    retry_budget : int
        Number of retry attempts recommended. ``0`` when ``should_retry`` is
        ``False``; :data:`DEFAULT_RETRY_BUDGET` otherwise.
    retry_reason : RetryReason
        Why this decision was reached. Default :attr:`RetryReason.NO_GAP`.
    confidence : float
        This decision's own confidence in ``[0.0, 1.0]``. Always ``1.0`` in
        this phase (deterministic rule evaluation only, no LLM judgment).
    recovery_strategy : RecoveryStrategy
        The strategy a future retry would use. :attr:`RecoveryStrategy.NONE`
        when ``should_retry`` is ``False``;
        :attr:`RecoveryStrategy.RETRY_MISSING_CATEGORIES` otherwise. Not
        executed by this module -- see the module docstring.
    created_at : datetime
        UTC timestamp when this decision was produced. Diagnostic only;
        never read back.

    Raises
    ------
    ValueError
        If ``confidence`` is outside ``[0.0, 1.0]``, if ``retry_budget`` is
        negative, or if ``should_retry``/``retry_reason``/
        ``recovery_strategy``/``missing_categories`` are inconsistent with
        each other (see the field descriptions above for the exact
        invariants enforced).
    """

    should_retry: bool
    missing_categories: Tuple[ContextCategory, ...] = ()
    retry_budget: int = 0
    retry_reason: RetryReason = RetryReason.NO_GAP
    confidence: float = 1.0
    recovery_strategy: RecoveryStrategy = RecoveryStrategy.NONE
    created_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "missing_categories", tuple(self.missing_categories)
        )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0]; got {self.confidence}")
        if self.retry_budget < 0:
            raise ValueError(f"retry_budget must be >= 0; got {self.retry_budget}")

        should_retry_reason = self.retry_reason is RetryReason.REQUIRED_CATEGORY_MISSING
        if self.should_retry != should_retry_reason:
            raise ValueError(
                "should_retry must be True if and only if retry_reason is "
                "REQUIRED_CATEGORY_MISSING; got "
                f"should_retry={self.should_retry}, retry_reason={self.retry_reason}"
            )

        should_retry_strategy = (
            self.recovery_strategy is RecoveryStrategy.RETRY_MISSING_CATEGORIES
        )
        if self.should_retry != should_retry_strategy:
            raise ValueError(
                "should_retry must be True if and only if recovery_strategy is "
                "RETRY_MISSING_CATEGORIES; got "
                f"should_retry={self.should_retry}, "
                f"recovery_strategy={self.recovery_strategy}"
            )

        if self.should_retry and self.retry_budget <= 0:
            raise ValueError("should_retry=True requires retry_budget > 0")
        if not self.should_retry and self.retry_budget != 0:
            raise ValueError("should_retry=False requires retry_budget == 0")

        if self.should_retry and not self.missing_categories:
            raise ValueError("should_retry=True requires a non-empty missing_categories")
        no_gap = self.retry_reason is RetryReason.NO_GAP
        if no_gap != (not self.missing_categories):
            raise ValueError(
                "retry_reason is NO_GAP if and only if missing_categories is empty"
            )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "should_retry": self.should_retry,
            "missing_categories": [c.value for c in self.missing_categories],
            "retry_budget": self.retry_budget,
            "retry_reason": self.retry_reason.value,
            "confidence": self.confidence,
            "recovery_strategy": self.recovery_strategy.value,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GapRecoveryDecision":
        """Reconstruct a :class:`GapRecoveryDecision` from a serialised dictionary."""
        return cls(
            should_retry=data["should_retry"],
            missing_categories=tuple(
                ContextCategory(c) for c in data.get("missing_categories", [])
            ),
            retry_budget=data.get("retry_budget", 0),
            retry_reason=RetryReason(data.get("retry_reason", RetryReason.NO_GAP.value)),
            confidence=data.get("confidence", 1.0),
            recovery_strategy=RecoveryStrategy(
                data.get("recovery_strategy", RecoveryStrategy.NONE.value)
            ),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if "created_at" in data
                else datetime.utcnow()
            ),
        )


# ---------------------------------------------------------------------------
# decide_gap_recovery
# ---------------------------------------------------------------------------


def decide_gap_recovery(
    plan: ContextPlan, coverage: CoverageReport
) -> GapRecoveryDecision:
    """Decide whether Haven should attempt another retrieval pass.

    Reads exactly two already-computed, immutable records --
    ``plan.confidence`` and ``coverage.missing_required_categories`` -- and
    nothing else. See the module docstring's "Conservative by default" for
    the three cases this evaluates, in order.

    Parameters
    ----------
    plan : ContextPlan
        The plan produced for this query, before retrieval began. Only
        ``confidence`` is read.
    coverage : CoverageReport
        The coverage report comparing *plan*'s requirements against what
        retrieval actually accepted. Only ``missing_required_categories`` is
        read.

    Returns
    -------
    GapRecoveryDecision
        ``should_retry=False`` with :attr:`RetryReason.NO_GAP` when
        *coverage* has no unsatisfied ``REQUIRED`` category (including
        trivially for the ``TaskMode.POINTED_QA`` sentinel).
        ``should_retry=False`` with :attr:`RetryReason.LOW_PLAN_CONFIDENCE`
        when a gap exists but *plan*'s ``confidence`` is below
        :data:`MIN_PLAN_CONFIDENCE_FOR_RETRY`. ``should_retry=True`` with
        :attr:`RetryReason.REQUIRED_CATEGORY_MISSING` otherwise.
    """
    missing = coverage.missing_required_categories

    if not missing:
        return GapRecoveryDecision(
            should_retry=False,
            missing_categories=(),
            retry_budget=0,
            retry_reason=RetryReason.NO_GAP,
            confidence=1.0,
            recovery_strategy=RecoveryStrategy.NONE,
        )

    if plan.confidence < MIN_PLAN_CONFIDENCE_FOR_RETRY:
        return GapRecoveryDecision(
            should_retry=False,
            missing_categories=missing,
            retry_budget=0,
            retry_reason=RetryReason.LOW_PLAN_CONFIDENCE,
            confidence=1.0,
            recovery_strategy=RecoveryStrategy.NONE,
        )

    return GapRecoveryDecision(
        should_retry=True,
        missing_categories=missing,
        retry_budget=DEFAULT_RETRY_BUDGET,
        retry_reason=RetryReason.REQUIRED_CATEGORY_MISSING,
        confidence=1.0,
        recovery_strategy=RecoveryStrategy.RETRY_MISSING_CATEGORIES,
    )
