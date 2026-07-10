"""Deterministic Coverage Analysis for Haven's read pipeline (Phase 2 only).

Phase 1 (:mod:`obsidian.memory_engine.context_planner`) produces a
:class:`~obsidian.memory_engine.context_planner.ContextPlan` describing which
named context categories a query needs. Phase 1.5
(:meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`) attaches
that plan to :class:`~obsidian.ontology.retrieval_models.RetrievalTrace` as
``context_plan``, purely for diagnostics.

This module, Phase 2, closes the loop between the two: it compares a
:class:`ContextPlan`'s requirements against the
:class:`~obsidian.ontology.retrieval_models.CandidateTrace` entries a
retrieval run actually accepted, and reports, per requested category, whether
enough was retrieved. It answers exactly one question -- "did retrieval
satisfy what the planner asked for?" -- and nothing else.

::

    ContextPlan.requirements   RetrievalTrace.candidates (accepted only)
              │                              │
              └──────────────┬───────────────┘
                              ▼
                       analyze_coverage
                              │
                              ▼
                       CoverageReport

Purely observational (this is Phase 2 of a multi-phase design)
----------------------------------------------------------------
* **No retrieval, ranking, or acceptance changes.** This module never calls
  :class:`~obsidian.memory_engine.hybrid_candidate_retriever.HybridCandidateRetriever`,
  :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`, or
  :class:`~obsidian.memory_engine.acceptance_stage.AcceptanceStage`, and never
  changes what any of them decide. It runs strictly after acceptance and slot
  allocation have already finished, over their already-produced output.
* **No candidate generation changes.** Nothing here proposes, discards, or
  re-scores a candidate; it only counts candidates that
  :class:`~obsidian.ontology.retrieval_models.CandidateTrace` already marked
  ``accepted``.
* **No WorkingContext or prompt changes.** This module never touches
  :class:`~obsidian.memory_engine.working_context_builder.WorkingContextBuilder`,
  :class:`~obsidian.memory_engine.structured_prompt_builder.StructuredPromptBuilder`,
  or :class:`~obsidian.memory_engine.context_builder.ContextBuilder`.
* **No planner changes.** :class:`~obsidian.memory_engine.context_planner.ContextPlan`
  and :class:`~obsidian.memory_engine.context_planner.ContextPlanner` are
  consumed read-only; this module never mutates or re-derives a plan.
* **No gap recovery, retries, or LLM calls.** :func:`analyze_coverage` is a
  pure, one-shot comparison. A future phase may use its output
  (:class:`CoverageReport`) to trigger a bounded retry of retrieval for an
  unmet ``REQUIRED`` category -- that "gap recovery" logic does not exist
  yet and is explicitly out of scope here; see
  ``docs/architecture/CONTEXT_PLAN_OBJECT.md`` §5 for the eventual design.

Why category resolution is still partial, not total
-----------------------------------------------------
Coverage counts an accepted candidate toward a category by mapping its
:class:`~obsidian.core.enums.MemoryType` to a
:class:`~obsidian.memory_engine.context_planner.ContextCategory` through a
fixed table (:data:`MEMORY_TYPE_CATEGORY`). Unlike
:func:`obsidian.ontology.retrieval_models.resolve_role`'s mapping to
``MemoryRole``, this table is deliberately **not total** in a different,
narrower way than it once was: ``CONSTRAINT``, ``BLOCKER``,
``OPEN_QUESTION``, ``IMPLEMENTATION_STATE``, and ``CODE_AREA`` all now have
an entry (``CONSTRAINT`` via the pre-existing ``MemoryType.RULE``; the other
four via the ``MemoryType`` members added alongside them -- see
``docs/architecture/PROJECT_STATE_KNOWLEDGE_MODEL.md``). What remains
un-mapped is only ``GOAL``, ``PROJECT``, ``PERSON``, ``EVENT``, ``SKILL``,
and ``PREFERENCE`` -- types with no corresponding ``ContextCategory`` at
all, not types this analyzer chose not to resolve. Separately,
:class:`~obsidian.ontology.retrieval_models.CandidateTrace` carries no
``metadata`` field a category override could live in (unlike
``KnowledgeObject``, which backs ``resolve_role``'s override path), so
:func:`resolve_category` has no override mechanism the way
:func:`resolve_role` does -- a candidate's category is decided by
``memory_type`` alone. Adding that override path is later-phase work.

``MEMORY_TYPE_CATEGORY``/:func:`resolve_category` are public (not
module-private) precisely so :mod:`~obsidian.memory_engine.category_preference`
(Phase 3 -- see that module's docstring) can resolve a candidate's category
the same way coverage does, from the same single table, rather than growing
a second, independently-maintained copy of this mapping.

Determinism
-----------
:func:`analyze_coverage` is a pure function of its two inputs: no clock read,
no randomness, no I/O, no LLM call, no retry. The same plan and the same
candidate traces always produce an equal :class:`CoverageReport`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, Optional, Tuple

from obsidian.core.enums import MemoryType
from obsidian.memory_engine.context_planner import ContextCategory, ContextPlan, Necessity
from obsidian.ontology.retrieval_models import CandidateTrace


# ---------------------------------------------------------------------------
# MemoryType -> ContextCategory resolution
# ---------------------------------------------------------------------------

#: Fixed, deliberately partial ``MemoryType`` -> ``ContextCategory`` mapping.
#: Only memory types with an honest category correspondence are listed; see
#: the module docstring's "Why category resolution is still partial, not
#: total" for why ``GOAL``, ``PROJECT``, ``PERSON``, ``EVENT``, ``SKILL``, and
#: ``PREFERENCE`` have no entry. Public so
#: :mod:`~obsidian.memory_engine.category_preference` can reuse it verbatim
#: (see the module docstring's closing note).
MEMORY_TYPE_CATEGORY: Dict[MemoryType, ContextCategory] = {
    MemoryType.DECISION: ContextCategory.DECISION,
    MemoryType.TASK: ContextCategory.TASK,
    MemoryType.RULE: ContextCategory.CONSTRAINT,
    MemoryType.FACT: ContextCategory.RESEARCH,
    MemoryType.BELIEF: ContextCategory.BELIEF,
    MemoryType.BLOCKER: ContextCategory.BLOCKER,
    MemoryType.IMPLEMENTATION_STATE: ContextCategory.IMPLEMENTATION_STATE,
    MemoryType.CODE_AREA: ContextCategory.CODE_AREA,
    MemoryType.OPEN_QUESTION: ContextCategory.OPEN_QUESTION,
}


def resolve_category(memory_type: MemoryType) -> Optional[ContextCategory]:
    """Return the :class:`ContextCategory` *memory_type* counts toward, if any.

    ``None`` when *memory_type* has no entry in :data:`MEMORY_TYPE_CATEGORY`
    -- such a candidate contributes to no category's ``retrieved_count``.
    """
    return MEMORY_TYPE_CATEGORY.get(memory_type)


# ---------------------------------------------------------------------------
# CoverageStatus
# ---------------------------------------------------------------------------


class CoverageStatus(str, Enum):
    """How well retrieval satisfied one :class:`CategoryCoverage` entry.

    Values
    ------
    FULL : str
        ``retrieved_count >= required_minimum``.
    PARTIAL : str
        ``0 < retrieved_count < required_minimum``.
    MISSING : str
        ``retrieved_count == 0`` (and ``required_minimum > 0``).
    """

    FULL = "full"
    PARTIAL = "partial"
    MISSING = "missing"


# ---------------------------------------------------------------------------
# CategoryCoverage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryCoverage:
    """Coverage outcome for one requested category.

    ``satisfied`` and ``status`` are derived properties, not stored fields --
    both are pure functions of ``retrieved_count`` and ``required_minimum``,
    so there is exactly one source of truth and no way for a hand-built
    instance to carry an inconsistent status.

    Parameters
    ----------
    category : ContextCategory
        The requested category this entry reports on.
    necessity : Necessity
        Whether the originating :class:`~obsidian.memory_engine.context_planner.CategoryRequirement`
        was ``REQUIRED`` or ``OPTIONAL``. An unmet ``OPTIONAL`` requirement is
        never a gap; see :attr:`CoverageReport.missing_required_categories`.
    required_minimum : int
        The requirement's ``min_count``. Must be ``>= 0``.
    retrieved_count : int
        Number of accepted candidates that resolved to ``category`` via
        :func:`_resolve_category`. Must be ``>= 0``.

    Raises
    ------
    ValueError
        If ``required_minimum`` or ``retrieved_count`` is negative.
    """

    category: ContextCategory
    necessity: Necessity
    required_minimum: int
    retrieved_count: int

    def __post_init__(self) -> None:
        if self.required_minimum < 0:
            raise ValueError(
                f"required_minimum must be >= 0; got {self.required_minimum}"
            )
        if self.retrieved_count < 0:
            raise ValueError(f"retrieved_count must be >= 0; got {self.retrieved_count}")

    @property
    def status(self) -> CoverageStatus:
        """Return this entry's :class:`CoverageStatus`, derived deterministically."""
        if self.retrieved_count >= self.required_minimum:
            return CoverageStatus.FULL
        if self.retrieved_count > 0:
            return CoverageStatus.PARTIAL
        return CoverageStatus.MISSING

    @property
    def satisfied(self) -> bool:
        """Return ``True`` iff ``status is CoverageStatus.FULL``."""
        return self.status is CoverageStatus.FULL

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "category": self.category.value,
            "necessity": self.necessity.value,
            "required_minimum": self.required_minimum,
            "retrieved_count": self.retrieved_count,
            "satisfied": self.satisfied,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CategoryCoverage":
        """Reconstruct a :class:`CategoryCoverage` from a serialised dictionary.

        ``satisfied`` and ``status`` keys, if present, are ignored -- they are
        re-derived from ``required_minimum``/``retrieved_count`` rather than
        trusted from the payload, so a hand-edited or stale dict cannot
        desynchronise them.
        """
        return cls(
            category=ContextCategory(data["category"]),
            necessity=Necessity(data["necessity"]),
            required_minimum=data["required_minimum"],
            retrieved_count=data["retrieved_count"],
        )


# ---------------------------------------------------------------------------
# CoverageReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageReport:
    """The Coverage Analyzer's output: whether a plan's requirements were met.

    A plain, immutable data structure, standalone in this phase --
    :attr:`overall_coverage_percentage`, :attr:`missing_required_categories`,
    and :attr:`fully_satisfied` are all derived properties of ``entries``, the
    same single-source-of-truth approach as :class:`CategoryCoverage`.

    Parameters
    ----------
    entries : tuple[CategoryCoverage, ...]
        One entry per :class:`~obsidian.memory_engine.context_planner.CategoryRequirement`
        on the originating :class:`~obsidian.memory_engine.context_planner.ContextPlan`,
        in the plan's declaration order. Empty exactly when the plan's
        requirements are empty -- notably the ``TaskMode.POINTED_QA`` sentinel,
        which requests nothing, so there is nothing to report coverage for.
    """

    entries: Tuple[CategoryCoverage, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))

    @property
    def missing_required_categories(self) -> Tuple[ContextCategory, ...]:
        """Return the ``REQUIRED`` categories whose entry is not satisfied.

        In plan declaration order. Empty when every ``REQUIRED`` requirement
        was satisfied, including trivially when there were none.
        """
        return tuple(
            entry.category
            for entry in self.entries
            if entry.necessity is Necessity.REQUIRED and not entry.satisfied
        )

    @property
    def fully_satisfied(self) -> bool:
        """Return ``True`` iff :attr:`missing_required_categories` is empty."""
        return len(self.missing_required_categories) == 0

    @property
    def overall_coverage_percentage(self) -> float:
        """Return the percentage of ``REQUIRED`` entries that are satisfied.

        In ``[0.0, 100.0]``. ``OPTIONAL`` entries never affect this figure --
        see :class:`~obsidian.memory_engine.context_planner.Necessity`, "an
        unmet OPTIONAL requirement is never a gap on its own." ``100.0`` when
        there are no ``REQUIRED`` entries, including trivially for the
        ``TaskMode.POINTED_QA`` sentinel (empty ``entries``).
        """
        required = [e for e in self.entries if e.necessity is Necessity.REQUIRED]
        if not required:
            return 100.0
        satisfied_count = sum(1 for e in required if e.satisfied)
        return (satisfied_count / len(required)) * 100.0

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "entries": [e.to_dict() for e in self.entries],
            "overall_coverage_percentage": self.overall_coverage_percentage,
            "missing_required_categories": [
                c.value for c in self.missing_required_categories
            ],
            "fully_satisfied": self.fully_satisfied,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CoverageReport":
        """Reconstruct a :class:`CoverageReport` from a serialised dictionary.

        Only ``entries`` is read back; the other keys in ``to_dict()``'s
        output are re-derived rather than trusted, for the same reason
        :meth:`CategoryCoverage.from_dict` ignores its derived keys.
        """
        return cls(
            entries=tuple(
                CategoryCoverage.from_dict(e) for e in data.get("entries", [])
            )
        )


# ---------------------------------------------------------------------------
# analyze_coverage
# ---------------------------------------------------------------------------


def analyze_coverage(
    plan: ContextPlan, candidates: Iterable[CandidateTrace]
) -> CoverageReport:
    """Compare *plan*'s requirements against *candidates* actually accepted.

    For each :class:`~obsidian.memory_engine.context_planner.CategoryRequirement`
    on *plan*, counts how many *accepted* entries in *candidates* resolve to
    that requirement's category (via :func:`resolve_category`), and reports
    the result as one :class:`CategoryCoverage` entry. Rejected candidates
    (``CandidateTrace.accepted is False``) are ignored entirely -- coverage
    measures what retrieval actually returned, not what it merely considered.

    This function makes no retrieval, ranking, acceptance, or planning
    decision of its own; it is a read-only comparison of two already-produced
    artifacts. See the module docstring for the full scope and the
    determinism guarantee.

    Parameters
    ----------
    plan : ContextPlan
        The plan produced for this query, before retrieval began. Its
        ``requirements`` are read verbatim and never re-derived.
    candidates : Iterable[CandidateTrace]
        Every candidate considered during this retrieval run, accepted or
        not (e.g. :attr:`~obsidian.ontology.retrieval_models.RetrievalTrace.candidates`).
        Only entries with ``accepted is True`` are counted.

    Returns
    -------
    CoverageReport
        One entry per requirement in ``plan.requirements``, in that same
        order. Empty when ``plan.requirements`` is empty (the
        ``TaskMode.POINTED_QA`` sentinel).
    """
    retrieved_counts: Dict[ContextCategory, int] = {}
    for candidate in candidates:
        if not candidate.accepted:
            continue
        category = resolve_category(candidate.memory_type)
        if category is None:
            continue
        retrieved_counts[category] = retrieved_counts.get(category, 0) + 1

    entries = tuple(
        CategoryCoverage(
            category=requirement.category,
            necessity=requirement.necessity,
            required_minimum=requirement.min_count,
            retrieved_count=retrieved_counts.get(requirement.category, 0),
        )
        for requirement in plan.requirements
    )
    return CoverageReport(entries=entries)
