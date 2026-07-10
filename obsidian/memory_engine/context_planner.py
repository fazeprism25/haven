"""Deterministic Context Planner for Haven's read pipeline (Phase 1 only).

Implements the planning-only slice of ``docs/architecture/CONTEXT_PLAN_OBJECT.md``
and ``docs/architecture/CONTEXT_PLANNER_DESIGN.md``:

::

    raw query (+ optional concept scope)
        │
        ▼
    ContextPlanner              (this module)
        │
        ▼
    ContextPlan

This module has exactly one responsibility: decide *which named context
categories* (:class:`ContextCategory`) a query needs, and at what minimum/
maximum counts, before any retrieval happens. It never decides *how good* a
piece of context is, never fetches one, and never renders one.

Mechanics-agnostic, by design
------------------------------
:class:`ContextPlan` says what is needed; it never says how retrieval should
get there. It carries no
:class:`~obsidian.memory_engine.acceptance_stage.AcceptanceConfig`, no
activation-spreading depth, no
:class:`~obsidian.ontology.retrieval_config.RetrievalConfig` field — none of
retrieval's own tuning surface. Translating a plan into concrete retrieval
steps (a future ``RetrievalPlan``) is a separate, later concern
(``docs/architecture/CONTEXT_PLAN_OBJECT.md`` §4/§9) and is explicitly out of
scope here.

Explicitly out of scope (this is Phase 1 of a multi-phase design; see the
architecture docs' phasing for the rest)
-------------------------------------------------------------------------
* **No retrieval.** This module never calls
  :class:`~obsidian.memory_engine.hybrid_candidate_retriever.HybridCandidateRetriever`
  or any other retrieval component, and never touches ``Candidate`` /
  ``RankedCandidate``.
* **No ranking or acceptance.** Nothing here scores, filters, or accepts a
  memory. That remains
  :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s
  and :class:`~obsidian.memory_engine.acceptance_stage.AcceptanceStage`'s job,
  unchanged.
* **No gap detection or retries.** A future ``GapDetector`` compares
  retrieval's output against a plan's requirements and may issue one bounded
  retry; this module produces the plan only and never re-evaluates or amends
  it after the fact.
* **No LLM calls.** :attr:`ContextPlan.planning_method` is always
  :attr:`PlanningMethod.DETERMINISTIC` in this phase. The field exists so a
  future LLM-fallback path (mirroring
  :class:`~obsidian.memory_engine.query_rewriter.QueryRewriter`'s fail-open
  contract) can be added later without changing :class:`ContextPlan`'s shape.
* **No behavior-affecting wiring into `MemoryEngine`, `WorkingContextBuilder`,
  or the prompt builders.** As of Phase 1.5,
  :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace` calls
  :class:`ContextPlanner` once per request, before retrieval begins, and
  attaches the resulting :class:`ContextPlan` to the returned
  :class:`~obsidian.ontology.retrieval_models.RetrievalTrace` as
  ``context_plan`` — see that method's module docstring, "Context Planner
  integration (Phase 1.5, diagnostics only)". This is the only integration
  point: the plan is exposed for diagnostics and is not read back by
  ``WorkingContextBuilder``, any prompt builder, or any retrieval/ranking/
  acceptance/allocation decision, so existing behaviour remains
  byte-identical to before this module was wired in. A future phase may
  change that; see ``docs/architecture/CONTEXT_PLAN_OBJECT.md`` §8.

Why `ContextCategory` is its own enum, not `MemoryRole`
---------------------------------------------------------
``docs/architecture/CONTEXT_PLAN_OBJECT.md`` §4 designs a category
requirement to reuse
:class:`~obsidian.ontology.retrieval_models.MemoryRole` verbatim — but that
assumes ``WORKING_CONTEXT_2_DESIGN.md``'s Phase 0 (extending `MemoryRole`
with `CONSTRAINT`, `BLOCKER`, `IMPLEMENTATION_STATE`, `CODE_AREA`, etc.) has
already landed. It has not, on purpose: this task's scope is Phase 1 only,
and `MemoryRole` is left untouched, because extending it would silently
change
:class:`~obsidian.memory_engine.working_context_builder.WorkingContextBuilder`'s
output (it iterates ``for role in MemoryRole`` to build a *total* set of
buckets in every :class:`~obsidian.ontology.retrieval_models.WorkingContext`
— new enum members would mean new, always-present empty buckets everywhere,
which is not byte-identical behaviour). :class:`ContextCategory` is
therefore a deliberately separate, planning-only vocabulary for this phase,
covering exactly the categories the four task-mode rules below use.
Reconciling it with `MemoryRole` is later-phase work, flagged here rather
than done silently.

Determinism
-----------
:meth:`ContextPlanner.plan` is a pure function of its two inputs: no clock
read beyond :attr:`ContextPlan.created_at` (a diagnostic timestamp, never
read back by classification), no randomness, no I/O, no LLM call. The same
query, with the same scope, always classifies to the same
:class:`TaskMode` and the same ``requirements`` tuple.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, Optional, Tuple
from uuid import UUID


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TaskMode(str, Enum):
    """The five task shapes a query classifies into.

    Values
    ------
    POINTED_QA : str
        A single-fact lookup. No reconstruction needed; the sentinel case
        (``requirements == ()``) that tells the caller "use today's
        retrieval unchanged."
    CODING_DEBUGGING : str
        Resuming or fixing code: implementation state, code areas,
        constraints, and open questions matter most.
    STRUCTURING : str
        Planning, designing, or writing: decisions, research, and
        constraints matter; merges the prior design docs' separate
        planning/design/architecture/writing modes (near-total category
        overlap between them).
    RESEARCH : str
        Investigating or learning about the user's own project: research
        findings, beliefs, and decisions matter.
    CONTINUATION : str
        Maximal, ambiguous scope ("continue implementing X", "where were
        we") — every category this planner knows about is required.
    """

    POINTED_QA = "pointed_qa"
    CODING_DEBUGGING = "coding_debugging"
    STRUCTURING = "structuring"
    RESEARCH = "research"
    CONTINUATION = "continuation"


class PlanningMethod(str, Enum):
    """How a :class:`ContextPlan`'s ``task_mode`` was decided.

    Values
    ------
    DETERMINISTIC : str
        Decided by the fixed lexical pattern table (the only method this
        phase implements).
    LLM_FALLBACK : str
        Reserved for a future phase; not produced by this module.
    """

    DETERMINISTIC = "deterministic"
    LLM_FALLBACK = "llm_fallback"


class Necessity(str, Enum):
    """Whether a :class:`CategoryRequirement` must be satisfied or is merely welcome.

    Values
    ------
    REQUIRED : str
        Retrieval must attempt to fill this category; an unmet REQUIRED
        requirement is a gap (future ``GapDetector`` concern, not decided
        here).
    OPTIONAL : str
        Nice to have; never a gap on its own.
    """

    REQUIRED = "required"
    OPTIONAL = "optional"


class PriorityTier(str, Enum):
    """Ordinal survival priority under a limited token budget.

    Deliberately an ordinal tier, not a numeric weight — see
    ``docs/architecture/CONTEXT_PLAN_OBJECT.md`` §3 for why a continuous
    weight was rejected as a second, uncalibrated scoring system layered on
    top of :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s
    existing one. No allocator reads this field yet; it is carried on
    :class:`CategoryRequirement` now so a future budget-aware allocator has
    something to read without a schema change.

    Values
    ------
    NEVER_DROP : str
        Kept even when a token budget must shrink everything else first.
    NORMAL : str
        Scored and trimmed normally.
    DROP_FIRST : str
        Collapsed or removed before any ``NORMAL`` category.
    """

    NEVER_DROP = "never_drop"
    NORMAL = "normal"
    DROP_FIRST = "drop_first"


class ContextCategory(str, Enum):
    """A named kind of context state a query may require.

    Distinct from :class:`~obsidian.ontology.retrieval_models.MemoryRole`
    (see the module docstring for why); covers exactly the categories used
    by the four non-trivial :class:`TaskMode` requirement tables in this
    module.

    Values
    ------
    DECISION : str
        A decision that was made.
    TASK : str
        A concrete pending action.
    CONSTRAINT : str
        A durable, never-do-X rule.
    BLOCKER : str
        Something currently preventing progress.
    RESEARCH : str
        A finding or piece of factual knowledge gathered while working.
    OPEN_QUESTION : str
        An explicitly unresolved question.
    IMPLEMENTATION_STATE : str
        What is built, stubbed, or in-progress.
    CODE_AREA : str
        A file or component relevant to the current focus.
    BELIEF : str
        A held opinion or principle.
    """

    DECISION = "decision"
    TASK = "task"
    CONSTRAINT = "constraint"
    BLOCKER = "blocker"
    RESEARCH = "research"
    OPEN_QUESTION = "open_question"
    IMPLEMENTATION_STATE = "implementation_state"
    CODE_AREA = "code_area"
    BELIEF = "belief"


# ---------------------------------------------------------------------------
# CategoryRequirement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CategoryRequirement:
    """One line of a :class:`ContextPlan`'s "what's needed" table.

    Expresses statements like "need at least 2 Decisions" or "no more than
    2 Constraints" without saying anything about how retrieval would
    satisfy them.

    Parameters
    ----------
    category : ContextCategory
        The category this requirement is about.
    necessity : Necessity
        Whether this category is required or merely optional.
    min_count : int
        Minimum number of accepted items needed to satisfy this
        requirement. Must be ``>= 0``. Default ``1``.
    max_count : int, optional
        Maximum number of items this category should contribute; ``None``
        means no plan-level cap (retrieval's own caps, e.g.
        :attr:`~obsidian.memory_engine.acceptance_stage.AcceptanceConfig.acceptance_max_k`,
        still apply independently). If set, must be ``>= min_count``.
    priority_tier : PriorityTier
        Survival priority under a token budget. Default
        :attr:`PriorityTier.NORMAL`.

    Raises
    ------
    ValueError
        If ``min_count`` is negative, or ``max_count`` is set and smaller
        than ``min_count``.
    """

    category: ContextCategory
    necessity: Necessity
    min_count: int = 1
    max_count: Optional[int] = None
    priority_tier: PriorityTier = PriorityTier.NORMAL

    def __post_init__(self) -> None:
        if self.min_count < 0:
            raise ValueError(f"min_count must be >= 0; got {self.min_count}")
        if self.max_count is not None and self.max_count < self.min_count:
            raise ValueError(
                "max_count must be >= min_count; got "
                f"max_count={self.max_count}, min_count={self.min_count}"
            )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "category": self.category.value,
            "necessity": self.necessity.value,
            "min_count": self.min_count,
            "max_count": self.max_count,
            "priority_tier": self.priority_tier.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CategoryRequirement":
        """Reconstruct a :class:`CategoryRequirement` from a serialised dictionary."""
        return cls(
            category=ContextCategory(data["category"]),
            necessity=Necessity(data["necessity"]),
            min_count=data.get("min_count", 1),
            max_count=data.get("max_count"),
            priority_tier=PriorityTier(
                data.get("priority_tier", PriorityTier.NORMAL.value)
            ),
        )


# ---------------------------------------------------------------------------
# ContextPlan
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextPlan:
    """The Context Planner's output: what a query needs, decided before retrieval.

    A plain, immutable data structure — never mutated after construction
    (see ``docs/architecture/CONTEXT_PLAN_OBJECT.md`` §7 for why an
    immutable plan plus a separately-growing execution record was chosen
    over a self-editing plan). Nothing in this codebase consumes this
    object yet; it is a standalone artifact in this phase.

    Parameters
    ----------
    query : str
        The raw user query this plan was produced for. May be empty.
    task_mode : TaskMode
        The classified task shape.
    requirements : tuple[CategoryRequirement, ...]
        What this query needs, in table-declaration order. Empty exactly
        when ``task_mode is TaskMode.POINTED_QA`` — the sentinel meaning
        "no plan needed, use today's retrieval unchanged."
    scope_concept_id : UUID, optional
        A pre-resolved concept id, carried through verbatim if the caller
        supplied one. ``None`` means project-wide / unresolved scope. This
        planner performs no concept resolution of its own.
    confidence : float
        This plan's own classification confidence, in ``[0.0, 1.0]``.
        Always ``1.0`` in this phase (deterministic classification only).
    planning_method : PlanningMethod
        How ``task_mode`` was decided. Always
        :attr:`PlanningMethod.DETERMINISTIC` in this phase.
    created_at : datetime
        UTC timestamp when this plan was produced. Diagnostic only; never
        read by classification.

    Raises
    ------
    ValueError
        If ``confidence`` is outside ``[0.0, 1.0]``.
    """

    query: str
    task_mode: TaskMode
    requirements: Tuple[CategoryRequirement, ...] = ()
    scope_concept_id: Optional[UUID] = None
    confidence: float = 1.0
    planning_method: PlanningMethod = PlanningMethod.DETERMINISTIC
    created_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirements", tuple(self.requirements))
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0]; got {self.confidence}")

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "query": self.query,
            "task_mode": self.task_mode.value,
            "requirements": [r.to_dict() for r in self.requirements],
            "scope_concept_id": (
                str(self.scope_concept_id) if self.scope_concept_id is not None else None
            ),
            "confidence": self.confidence,
            "planning_method": self.planning_method.value,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ContextPlan":
        """Reconstruct a :class:`ContextPlan` from a serialised dictionary."""
        scope = data.get("scope_concept_id")
        return cls(
            query=data["query"],
            task_mode=TaskMode(data["task_mode"]),
            requirements=tuple(
                CategoryRequirement.from_dict(r) for r in data.get("requirements", [])
            ),
            scope_concept_id=UUID(scope) if scope is not None else None,
            confidence=data.get("confidence", 1.0),
            planning_method=PlanningMethod(
                data.get("planning_method", PlanningMethod.DETERMINISTIC.value)
            ),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if "created_at" in data
                else datetime.utcnow()
            ),
        )


# ---------------------------------------------------------------------------
# Fixed, per-task-mode requirement tables
# ---------------------------------------------------------------------------
#
# Deterministic, editable config -- not learned, not LLM-authored. See
# docs/architecture/CONTEXT_PLANNER_DESIGN.md §2 for the category rationale
# behind each mode's set. CONSTRAINT and BLOCKER are tiered NEVER_DROP
# wherever they appear, per docs/architecture/WORKING_CONTEXT_2_DESIGN.md §7
# ("constraints ... the most recent unresolved blocker" are never removed
# first); every other category in these tables is NORMAL tier.


def _req(
    category: ContextCategory, *, tier: PriorityTier = PriorityTier.NORMAL
) -> CategoryRequirement:
    return CategoryRequirement(
        category=category, necessity=Necessity.REQUIRED, min_count=1, priority_tier=tier
    )


_REQUIREMENTS_BY_MODE: Dict[TaskMode, Tuple[CategoryRequirement, ...]] = {
    TaskMode.POINTED_QA: (),
    TaskMode.CONTINUATION: (
        _req(ContextCategory.DECISION),
        _req(ContextCategory.TASK),
        _req(ContextCategory.CONSTRAINT, tier=PriorityTier.NEVER_DROP),
        _req(ContextCategory.BLOCKER, tier=PriorityTier.NEVER_DROP),
        _req(ContextCategory.RESEARCH),
        _req(ContextCategory.OPEN_QUESTION),
    ),
    TaskMode.CODING_DEBUGGING: (
        _req(ContextCategory.DECISION),
        _req(ContextCategory.CONSTRAINT, tier=PriorityTier.NEVER_DROP),
        _req(ContextCategory.IMPLEMENTATION_STATE),
        _req(ContextCategory.CODE_AREA),
        _req(ContextCategory.OPEN_QUESTION),
    ),
    TaskMode.STRUCTURING: (
        _req(ContextCategory.DECISION),
        _req(ContextCategory.RESEARCH),
        _req(ContextCategory.CONSTRAINT, tier=PriorityTier.NEVER_DROP),
    ),
    TaskMode.RESEARCH: (
        _req(ContextCategory.RESEARCH),
        _req(ContextCategory.BELIEF),
        _req(ContextCategory.DECISION),
    ),
}


# ---------------------------------------------------------------------------
# Lexical classification table
# ---------------------------------------------------------------------------
#
# Order matters: earlier modes are checked first, first match wins.
# CONTINUATION is checked before CODING_DEBUGGING so "continue implementing
# Haven" classifies as CONTINUATION rather than matching CODING_DEBUGGING's
# "implement" pattern -- see
# docs/architecture/WORKING_CONTEXT_2_DESIGN.md §3 on CONTINUATION needing
# to win that disambiguation.

_MODE_PATTERNS: Tuple[Tuple[TaskMode, Tuple[str, ...]], ...] = (
    (
        TaskMode.CONTINUATION,
        (
            "continue",
            "where were we",
            "pick up where",
            "picking up where",
            "pick this back up",
            "pick back up",
            "resume",
            "keep going",
            "what was i doing",
            "what were we doing",
            "what should i do next",
            "what should we do next",
            "what should i work on next",
            "what should we work on next",
            "what's next",
            "whats next",
        ),
    ),
    (
        TaskMode.CODING_DEBUGGING,
        (
            "debug",
            "bug",
            "error",
            "exception",
            "stack trace",
            "not working",
            "isn't working",
            "aren't working",
            "doesn't work",
            "fix",
            "implement",
            "why is this failing",
        ),
    ),
    (
        TaskMode.STRUCTURING,
        (
            "plan",
            "design",
            "architecture",
            "roadmap",
            "outline",
            "structure",
        ),
    ),
    (
        TaskMode.RESEARCH,
        (
            "research",
            "investigate",
            "look into",
            "find out",
            "explore",
        ),
    ),
)


# ---------------------------------------------------------------------------
# ContextPlanner
# ---------------------------------------------------------------------------


class ContextPlanner:
    """Deterministic, mechanics-agnostic Context Planner (Phase 1).

    Stateless and reusable across calls to :meth:`plan` with different
    queries. Consumes only the raw query text and, optionally, a
    pre-resolved concept id — never memory content, never a retrieval
    config, never an LLM. See the module docstring for full scope.

    Examples
    --------
    >>> planner = ContextPlanner()
    >>> plan = planner.plan("what database do I use")
    >>> plan.task_mode
    <TaskMode.POINTED_QA: 'pointed_qa'>
    >>> plan.requirements
    ()
    """

    def plan(
        self, raw_query: str, scope_concept_id: Optional[UUID] = None
    ) -> ContextPlan:
        """Classify *raw_query* into a :class:`ContextPlan`, deterministically.

        Parameters
        ----------
        raw_query : str
            The raw user query text. Never retrieved against, never
            tokenised beyond a cheap ``.strip().lower()`` substring match.
        scope_concept_id : UUID, optional
            A pre-resolved concept id, if the caller already has one (e.g.
            from an earlier retrieval pass). Carried through verbatim onto
            the returned plan's ``scope_concept_id``; this method performs
            no concept resolution of its own.

        Returns
        -------
        ContextPlan
            ``task_mode=TaskMode.POINTED_QA`` and ``requirements=()`` for
            any query that matches no mode pattern (including an empty
            query) — the sentinel meaning "no plan needed, use today's
            retrieval unchanged" (see
            ``docs/architecture/CONTEXT_PLAN_OBJECT.md`` §2).
        """
        task_mode = self._classify(raw_query)
        return ContextPlan(
            query=raw_query,
            task_mode=task_mode,
            requirements=_REQUIREMENTS_BY_MODE[task_mode],
            scope_concept_id=scope_concept_id,
            confidence=1.0,
            planning_method=PlanningMethod.DETERMINISTIC,
        )

    @staticmethod
    def _classify(raw_query: str) -> TaskMode:
        """Return the first matching :class:`TaskMode`, or ``POINTED_QA``.

        Pure lexical substring matching against :data:`_MODE_PATTERNS`, in
        table order (first match wins). An empty or whitespace-only query
        also classifies as ``POINTED_QA`` — there is nothing to plan for.
        """
        normalized = raw_query.strip().lower()
        if not normalized:
            return TaskMode.POINTED_QA
        for mode, patterns in _MODE_PATTERNS:
            if any(pattern in normalized for pattern in patterns):
                return mode
        return TaskMode.POINTED_QA
