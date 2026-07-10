"""Deterministic ProjectState reconstruction for Haven's read pipeline (Phase A only).

Implements Phase A of ``docs/architecture/PROJECT_STATE_DESIGN.md``: a
:class:`ProjectState` object answering "what does this run's accepted
retrieval output say about where the project currently stands" —
:class:`StateRef`-backed buckets keyed by the same category vocabulary
:mod:`~obsidian.memory_engine.coverage_analyzer` already uses, grouped from
the exact candidates a query's pipeline already decided to keep.

::

    RankedCandidate[]           (DeterministicSlotAllocator's output --
         │                       the same "allocated" list ContextBuilder
         │                       and WorkingContextBuilder already render)
         ▼
    ProjectStateBuilder          (this module)
         │
         ▼
    ProjectState

Phase A only — read this before extending
-------------------------------------------
``docs/architecture/PROJECT_STATE_DESIGN.md`` §10 lays out five phases
(A: foundation, B: incremental materialization, C: WorkingContext wiring,
D: inferred fields, E: hardening). **This module implements Phase A, and
Phase A only.** Concretely, that means:

* **Recomputed from one query's retrieval results, not full-vault
  aggregation.** The design document's own Phase A description (§10)
  proposes a "full-vault aggregation function" as an intermediate step
  before incremental materialization. This implementation is narrower still:
  :meth:`ProjectStateBuilder.build` takes exactly the ``allocated`` list a
  single :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
  call already produced -- the same candidates
  :class:`~obsidian.memory_engine.context_builder.ContextBuilder` and
  :class:`~obsidian.memory_engine.working_context_builder.WorkingContextBuilder`
  render for that call. There is no vault scan, no store query, and no
  cross-query aggregation. A :class:`ProjectState` therefore describes "what
  this run surfaced," not "everything the project has ever contained" --
  see "What ``gaps`` does and does not mean" below.
* **No persistence.** No JSON sidecar, no ``ProjectStateStore``, no write
  path changes. :class:`ProjectState` is never written to disk and never
  read back from disk; it exists only as a return value.
* **No incremental materialization.** No ``ManagerPipeline`` hook, no
  ``version``/``last_incorporated_event_id`` watermark -- those are
  meaningless without persistence (Phase B) and are intentionally absent
  from this dataclass rather than included as always-``None``/always-``1``
  dead fields.
* **No inference.** Every field below is either :attr:`FieldDerivation.DETERMINISTIC`
  (pure bookkeeping) or :attr:`FieldDerivation.MEMORY_DIRECT` (a verbatim
  pointer to an accepted ``KnowledgeObject``). The design document's
  ``identity``, ``phase``, and the ambiguous-tie-break half of
  ``current_objective`` are all :attr:`FieldDerivation.INFERRED` fields
  (design §3, §10 Phase D) and are **not implemented here** --
  :class:`FieldDerivation` still declares the ``INFERRED`` member so a
  future phase can add such a field without a breaking enum change, but
  nothing in this module ever produces it. ``current_objective`` in this
  module is always the deterministic default the design document itself
  names as the *unambiguous* case: the single highest-ranked accepted
  ``GOAL``-typed candidate, with no LLM tie-break when several compete (see
  :meth:`ProjectStateBuilder._build_current_objective`).
* **No retrieval/ranking/acceptance/WorkingContext/prompt influence.**
  :class:`ProjectStateBuilder` only ever reads an already-finished
  ``allocated`` list; it returns a new object and mutates nothing. See
  ``docs/architecture/ARCHITECTURE.md``'s "Project State (Phase A)" section
  for the same guarantee stated at the integration point
  (:meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`).
* **``rejected_approaches``/``do_not_do``/``recent_discoveries``/``identity``/
  ``phase`` are omitted entirely**, not included as permanently-empty
  fields. The design document itself flags each as either write-side-gapped
  (no ``MemoryType``/category produces them yet -- ``rejected_approaches``,
  ``do_not_do``) or ``INFERRED``-only (``identity``, ``phase``) -- see §3,
  §8. ``recent_discoveries`` (``RESEARCH`` category) has a real data source
  but is out of this phase's explicit field list; a later phase can add it
  without a breaking change since :class:`ProjectState` is a plain,
  additively-extensible dataclass.

What ``gaps`` does and does not mean
---------------------------------------
Because a :class:`ProjectState` is built from one query's already-accepted
candidates, an empty category here means "this run's retrieval did not
surface anything in this category" -- it is not, and cannot be, a claim that
the project has no such fact anywhere in the vault. This is a strictly
weaker (but honest) signal than the design document's eventual full-vault
or incrementally-materialized ``gaps`` (§8's ``STATE_DRIFT`` failure mode
does not apply here, since there is nothing to drift from -- there is no
persisted prior state at all). Treat :attr:`ProjectState.gaps` as "absent
from *this* reconstruction," matching exactly what
:class:`~obsidian.memory_engine.coverage_analyzer.CoverageReport` already
means by "missing" for the same run.

Determinism
-----------
:meth:`ProjectStateBuilder.build` is a pure function of its inputs: no
clock read beyond the caller-supplied *now* (defaulting to
``datetime.utcnow()`` only when omitted, exactly like
:meth:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker.score_all`'s
own ``now`` parameter), no randomness, no I/O, no LLM call. The same
``allocated`` list, passed twice, always produces an equal
:class:`ProjectState` (modulo ``generated_at`` when *now* is left to
default -- pass an explicit *now* for byte-identical comparisons in tests,
the same convention :mod:`obsidian.tests.test_engine`'s ``_FrozenDatetime``
already establishes for the rest of the pipeline).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Dict, Generic, List, Optional, Tuple, TypeVar
from uuid import UUID

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import DecisionStatus, KnowledgeObject, get_decision_metadata
from obsidian.memory_engine.context_planner import ContextCategory
from obsidian.memory_engine.coverage_analyzer import resolve_category
from obsidian.ontology.retrieval_models import RankedCandidate

T = TypeVar("T")


# ---------------------------------------------------------------------------
# FieldDerivation
# ---------------------------------------------------------------------------


class FieldDerivation(str, Enum):
    """How a :class:`ProjectStateField`'s value was produced.

    Values
    ------
    DETERMINISTIC : str
        A pure projection of already-stored fields, no judgment involved
        (e.g. a count, a bookkeeping timestamp).
    MEMORY_DIRECT : str
        A verbatim pointer to an accepted ``KnowledgeObject`` -- the value
        *is* a real memory, not a synthesis of one.
    INFERRED : str
        Bounded LLM synthesis, fails open on error (see
        ``docs/architecture/PROJECT_STATE_DESIGN.md`` §3). Declared here for
        forward compatibility with a future phase; **this module never
        produces a field carrying this value** -- see this module's
        docstring, "No inference."
    """

    DETERMINISTIC = "deterministic"
    MEMORY_DIRECT = "memory_direct"
    INFERRED = "inferred"


# ---------------------------------------------------------------------------
# StateRef
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StateRef:
    """A lightweight, ``ProjectState``-local pointer to an accepted memory.

    Deliberately not a :class:`~obsidian.ontology.retrieval_models.RankedCandidate`
    or :class:`~obsidian.ontology.retrieval_models.Candidate` -- per
    ``docs/architecture/PROJECT_STATE_DESIGN.md`` §2, ``ProjectState`` must
    never embed a transient ranking score or an in-memory-only retrieval
    object; it carries only the ``KnowledgeObject`` fields needed to
    identify and display the memory it points to.

    Parameters
    ----------
    knowledge_object_id : UUID
        Identity of the underlying ``KnowledgeObject``.
    canonical_fact : str
        The fact text, copied verbatim at build time.
    valid_from : datetime
        The ``KnowledgeObject``'s own ``valid_from``.
    confidence : float
        The ``KnowledgeObject``'s own ``confidence`` (0.0 - 1.0).
    importance : float
        The ``KnowledgeObject``'s own ``importance`` (0.0 - 1.0).

    Raises
    ------
    ValueError
        If ``confidence`` or ``importance`` are outside ``[0.0, 1.0]``.
    """

    knowledge_object_id: UUID
    canonical_fact: str
    valid_from: datetime
    confidence: float
    importance: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0]; got {self.confidence}")
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError(f"importance must be in [0.0, 1.0]; got {self.importance}")

    @classmethod
    def from_knowledge_object(cls, ko: KnowledgeObject) -> "StateRef":
        """Project *ko* into a :class:`StateRef`, verbatim, no judgment."""
        return cls(
            knowledge_object_id=ko.id,
            canonical_fact=ko.canonical_fact,
            valid_from=ko.valid_from,
            confidence=ko.confidence,
            importance=ko.importance,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "knowledge_object_id": str(self.knowledge_object_id),
            "canonical_fact": self.canonical_fact,
            "valid_from": self.valid_from.isoformat(),
            "confidence": self.confidence,
            "importance": self.importance,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StateRef":
        """Reconstruct a :class:`StateRef` from a serialised dictionary."""
        return cls(
            knowledge_object_id=UUID(data["knowledge_object_id"]),
            canonical_fact=data["canonical_fact"],
            valid_from=datetime.fromisoformat(data["valid_from"]),
            confidence=data["confidence"],
            importance=data["importance"],
        )


# ---------------------------------------------------------------------------
# ProjectStateField
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectStateField(Generic[T]):
    """A single-value ``ProjectState`` field, tagged with its own provenance.

    Only used for :attr:`ProjectState.current_objective` in this phase --
    every other ``ProjectState`` field is a plain ``Tuple[StateRef, ...]``,
    exactly as ``docs/architecture/PROJECT_STATE_DESIGN.md`` §2's own
    ``ProjectState`` code sample defines it (multi-member memory-direct
    fields carry their own provenance per-``StateRef``, so wrapping the
    whole tuple a second time would be redundant).

    Parameters
    ----------
    value : T
        The field's value.
    derivation : FieldDerivation
        How *value* was produced. Always :attr:`FieldDerivation.MEMORY_DIRECT`
        in this phase (see the module docstring).
    source_ids : tuple[UUID, ...]
        ``KnowledgeObject`` ids backing *value*; empty if none.
    confidence : float
        0.0 - 1.0; always ``1.0`` in this phase, since nothing here is
        :attr:`FieldDerivation.INFERRED`.
    last_updated : datetime
        When this field was computed -- equal to the owning
        :class:`ProjectState`'s ``generated_at`` in this phase, since there
        is no persisted prior value to compare against.

    Raises
    ------
    ValueError
        If ``confidence`` is outside ``[0.0, 1.0]``.
    """

    value: T
    derivation: FieldDerivation
    source_ids: Tuple[UUID, ...]
    confidence: float
    last_updated: datetime

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0]; got {self.confidence}")
        object.__setattr__(self, "source_ids", tuple(self.source_ids))


# ---------------------------------------------------------------------------
# ProjectState
# ---------------------------------------------------------------------------

#: The category fields :attr:`ProjectState.gaps` reports over, in
#: declaration order. Kept as plain strings (not
#: :class:`~obsidian.memory_engine.context_planner.ContextCategory`) since
#: ``current_objective`` has no ``ContextCategory`` entry at all (``GOAL``
#: is one of the un-mapped types -- see
#: :mod:`~obsidian.memory_engine.coverage_analyzer`'s "Why category
#: resolution is still partial, not total") and a single vocabulary covering
#: every ``ProjectState`` field, category-backed or not, is simpler than two.
PROJECT_STATE_FIELD_NAMES: Tuple[str, ...] = (
    "current_objective",
    "decisions",
    "active_tasks",
    "blockers",
    "constraints",
    "implementation_state",
    "code_areas",
    "open_questions",
)


@dataclass(frozen=True)
class ProjectState:
    """A deterministic snapshot of "what this run's retrieval found," by category.

    See the module docstring for exactly how this differs from
    ``docs/architecture/PROJECT_STATE_DESIGN.md``'s full, eventual
    ``ProjectState`` (no persistence, no incremental update, no inference,
    single-query scope rather than full-vault).

    Parameters
    ----------
    current_objective : ProjectStateField[StateRef], optional
        The single highest-ranked accepted ``GOAL``-typed candidate, if any.
        ``None`` when no ``GOAL``-typed candidate was accepted this run.
    decisions : tuple[StateRef, ...]
        Accepted ``DECISION``-typed candidates whose
        ``DecisionMetadata.status`` (when present) is not ``SUPERSEDED`` --
        includes decisions with no ``DecisionMetadata`` attached at all
        (every decision written before Decision Memory existed), matching
        :class:`~obsidian.memory_engine.context_builder.ContextBuilder`'s
        own "no ``DecisionMetadata`` renders like any other decision"
        convention.
    superseded_decisions : tuple[StateRef, ...]
        Accepted ``DECISION``-typed candidates whose ``DecisionMetadata.status``
        is ``SUPERSEDED``. Typically empty in practice: a superseded
        decision's ``KnowledgeObject.valid_until`` is normally set at the
        same time, which means
        :func:`~obsidian.memory_engine.engine._active_candidates` already
        drops it before it can reach acceptance -- this field exists for the
        (documented, deterministic) case where status and validity diverge,
        not as a field expected to be routinely populated by this phase.
    active_tasks : tuple[StateRef, ...]
        Accepted ``TASK``-typed candidates.
    blockers : tuple[StateRef, ...]
        Accepted ``BLOCKER``-typed candidates.
    constraints : tuple[StateRef, ...]
        Accepted ``RULE``-typed candidates (``RULE`` resolves to
        ``ContextCategory.CONSTRAINT`` -- see
        :mod:`~obsidian.memory_engine.coverage_analyzer`).
    implementation_state : tuple[StateRef, ...]
        Accepted ``IMPLEMENTATION_STATE``-typed candidates.
    code_areas : tuple[StateRef, ...]
        Accepted ``CODE_AREA``-typed candidates.
    open_questions : tuple[StateRef, ...]
        Accepted ``OPEN_QUESTION``-typed candidates.
    gaps : tuple[str, ...]
        Names from :data:`PROJECT_STATE_FIELD_NAMES` whose field above is
        empty, in declaration order. See the module docstring's "What
        ``gaps`` does and does not mean."
    confidence : float
        ``len(PROJECT_STATE_FIELD_NAMES) - len(gaps)) / len(PROJECT_STATE_FIELD_NAMES)``,
        i.e. the fraction of the 8 tracked fields that are non-empty. A
        deterministic *completeness* signal over this run's own fields --
        not a per-fact confidence (see each ``StateRef.confidence`` for
        that) and not a claim about the rest of the vault.
    generated_at : datetime
        When this ``ProjectState`` was computed. Never persisted, never
        compared against a prior value -- purely a diagnostic timestamp,
        the same role ``RetrievalTrace.created_at`` already plays.

    Raises
    ------
    ValueError
        If ``confidence`` is outside ``[0.0, 1.0]``.
    """

    current_objective: Optional[ProjectStateField[StateRef]]
    decisions: Tuple[StateRef, ...]
    superseded_decisions: Tuple[StateRef, ...]
    active_tasks: Tuple[StateRef, ...]
    blockers: Tuple[StateRef, ...]
    constraints: Tuple[StateRef, ...]
    implementation_state: Tuple[StateRef, ...]
    code_areas: Tuple[StateRef, ...]
    open_questions: Tuple[StateRef, ...]
    gaps: Tuple[str, ...]
    confidence: float
    generated_at: datetime

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0]; got {self.confidence}")
        object.__setattr__(self, "decisions", tuple(self.decisions))
        object.__setattr__(self, "superseded_decisions", tuple(self.superseded_decisions))
        object.__setattr__(self, "active_tasks", tuple(self.active_tasks))
        object.__setattr__(self, "blockers", tuple(self.blockers))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "implementation_state", tuple(self.implementation_state))
        object.__setattr__(self, "code_areas", tuple(self.code_areas))
        object.__setattr__(self, "open_questions", tuple(self.open_questions))
        object.__setattr__(self, "gaps", tuple(self.gaps))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "current_objective": (
                {
                    "value": self.current_objective.value.to_dict(),
                    "derivation": self.current_objective.derivation.value,
                    "source_ids": [str(i) for i in self.current_objective.source_ids],
                    "confidence": self.current_objective.confidence,
                    "last_updated": self.current_objective.last_updated.isoformat(),
                }
                if self.current_objective is not None
                else None
            ),
            "decisions": [r.to_dict() for r in self.decisions],
            "superseded_decisions": [r.to_dict() for r in self.superseded_decisions],
            "active_tasks": [r.to_dict() for r in self.active_tasks],
            "blockers": [r.to_dict() for r in self.blockers],
            "constraints": [r.to_dict() for r in self.constraints],
            "implementation_state": [r.to_dict() for r in self.implementation_state],
            "code_areas": [r.to_dict() for r in self.code_areas],
            "open_questions": [r.to_dict() for r in self.open_questions],
            "gaps": list(self.gaps),
            "confidence": self.confidence,
            "generated_at": self.generated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectState":
        """Reconstruct a :class:`ProjectState` from a serialised dictionary."""
        current_objective_data = data.get("current_objective")
        current_objective = None
        if current_objective_data is not None:
            current_objective = ProjectStateField(
                value=StateRef.from_dict(current_objective_data["value"]),
                derivation=FieldDerivation(current_objective_data["derivation"]),
                source_ids=tuple(
                    UUID(i) for i in current_objective_data.get("source_ids", [])
                ),
                confidence=current_objective_data["confidence"],
                last_updated=datetime.fromisoformat(
                    current_objective_data["last_updated"]
                ),
            )
        return cls(
            current_objective=current_objective,
            decisions=tuple(StateRef.from_dict(r) for r in data.get("decisions", [])),
            superseded_decisions=tuple(
                StateRef.from_dict(r) for r in data.get("superseded_decisions", [])
            ),
            active_tasks=tuple(StateRef.from_dict(r) for r in data.get("active_tasks", [])),
            blockers=tuple(StateRef.from_dict(r) for r in data.get("blockers", [])),
            constraints=tuple(StateRef.from_dict(r) for r in data.get("constraints", [])),
            implementation_state=tuple(
                StateRef.from_dict(r) for r in data.get("implementation_state", [])
            ),
            code_areas=tuple(StateRef.from_dict(r) for r in data.get("code_areas", [])),
            open_questions=tuple(
                StateRef.from_dict(r) for r in data.get("open_questions", [])
            ),
            gaps=tuple(data.get("gaps", [])),
            confidence=data["confidence"],
            generated_at=datetime.fromisoformat(data["generated_at"]),
        )


# ---------------------------------------------------------------------------
# ProjectStateBuilder
# ---------------------------------------------------------------------------

#: ``ContextCategory`` -> ``ProjectState`` field name, for every category
#: this builder buckets directly (i.e. every category except
#: ``ContextCategory.DECISION``, which needs the extra decision/superseded
#: split, and the categories with no ``ProjectState`` field at all --
#: ``RESEARCH``, ``BELIEF`` -- which are simply not tracked in this phase).
#: Reuses :func:`~obsidian.memory_engine.coverage_analyzer.resolve_category`
#: verbatim for ``MemoryType`` -> ``ContextCategory`` resolution, the same
#: single source of truth
#: :mod:`~obsidian.memory_engine.category_preference` already reuses, rather
#: than growing a third, independently-maintained copy.
_CATEGORY_TO_FIELD: Dict[ContextCategory, str] = {
    ContextCategory.TASK: "active_tasks",
    ContextCategory.BLOCKER: "blockers",
    ContextCategory.CONSTRAINT: "constraints",
    ContextCategory.IMPLEMENTATION_STATE: "implementation_state",
    ContextCategory.CODE_AREA: "code_areas",
    ContextCategory.OPEN_QUESTION: "open_questions",
}


class ProjectStateBuilder:
    """Derives a :class:`ProjectState` from one query's accepted candidates.

    Stateless and reusable across calls to :meth:`build` with different
    candidate lists. See the module docstring for the full Phase A scope.

    Examples
    --------
    >>> builder = ProjectStateBuilder()
    >>> state = builder.build(allocated)  # doctest: +SKIP
    >>> state.confidence  # doctest: +SKIP
    0.25
    """

    def build(
        self, allocated: List[RankedCandidate], now: Optional[datetime] = None
    ) -> ProjectState:
        """Build a :class:`ProjectState` from *allocated*, deterministically.

        Parameters
        ----------
        allocated : list[RankedCandidate]
            The already-accepted, already-slot-allocated candidates for one
            query -- typically
            :meth:`~obsidian.memory_engine.deterministic_slot_allocator.DeterministicSlotAllocator.allocate`'s
            own return value, the same list
            :class:`~obsidian.memory_engine.context_builder.ContextBuilder`
            and
            :class:`~obsidian.memory_engine.working_context_builder.WorkingContextBuilder`
            render. Not mutated; order is not trusted (re-sorted internally,
            the same discipline
            :class:`~obsidian.memory_engine.working_context_builder.WorkingContextBuilder`
            uses).
        now : datetime, optional
            Reference instant for ``generated_at`` and every field's
            ``last_updated``. Defaults to ``datetime.utcnow()`` when
            omitted; pass an explicit value for byte-identical comparisons
            in tests.

        Returns
        -------
        ProjectState
            Never ``None`` -- an *allocated* list with no matching
            candidate for any tracked category yields a
            :class:`ProjectState` whose every field is empty and whose
            ``gaps`` is the full :data:`PROJECT_STATE_FIELD_NAMES` tuple.
        """
        if now is None:
            now = datetime.utcnow()

        ordered = sorted(allocated)

        by_field: Dict[str, List[StateRef]] = {
            name: [] for name in _CATEGORY_TO_FIELD.values()
        }
        goal_refs: List[StateRef] = []
        decisions: List[StateRef] = []
        superseded_decisions: List[StateRef] = []

        for ranked in ordered:
            ko = ranked.candidate.knowledge_object
            ref = StateRef.from_knowledge_object(ko)

            if ko.memory_type is MemoryType.GOAL:
                goal_refs.append(ref)
                continue

            if ko.memory_type is MemoryType.DECISION:
                metadata = get_decision_metadata(ko)
                if metadata is not None and metadata.status is DecisionStatus.SUPERSEDED:
                    superseded_decisions.append(ref)
                else:
                    decisions.append(ref)
                continue

            category = resolve_category(ko.memory_type)
            field_name = _CATEGORY_TO_FIELD.get(category) if category is not None else None
            if field_name is not None:
                by_field[field_name].append(ref)

        current_objective = self._build_current_objective(goal_refs, now)

        active_tasks = tuple(by_field["active_tasks"])
        blockers = tuple(by_field["blockers"])
        constraints = tuple(by_field["constraints"])
        implementation_state = tuple(by_field["implementation_state"])
        code_areas = tuple(by_field["code_areas"])
        open_questions = tuple(by_field["open_questions"])
        decisions_t = tuple(decisions)

        populated = {
            "current_objective": current_objective is not None,
            "decisions": bool(decisions_t),
            "active_tasks": bool(active_tasks),
            "blockers": bool(blockers),
            "constraints": bool(constraints),
            "implementation_state": bool(implementation_state),
            "code_areas": bool(code_areas),
            "open_questions": bool(open_questions),
        }
        gaps = tuple(name for name in PROJECT_STATE_FIELD_NAMES if not populated[name])
        confidence = (len(PROJECT_STATE_FIELD_NAMES) - len(gaps)) / len(
            PROJECT_STATE_FIELD_NAMES
        )

        return ProjectState(
            current_objective=current_objective,
            decisions=decisions_t,
            superseded_decisions=tuple(superseded_decisions),
            active_tasks=active_tasks,
            blockers=blockers,
            constraints=constraints,
            implementation_state=implementation_state,
            code_areas=code_areas,
            open_questions=open_questions,
            gaps=gaps,
            confidence=confidence,
            generated_at=now,
        )

    @staticmethod
    def _build_current_objective(
        goal_refs: List[StateRef], now: datetime
    ) -> Optional[ProjectStateField[StateRef]]:
        """Return the top-ranked goal as a field, or ``None`` if there is none.

        *goal_refs* is already in descending-rank order (built from the
        already-sorted ``allocated`` list), so "top-ranked" is simply the
        first element -- no tie-break judgment is made even when several
        goals were accepted; see the module docstring's "No inference" for
        why this phase never synthesizes a choice among competing goals.
        """
        if not goal_refs:
            return None
        top = goal_refs[0]
        return ProjectStateField(
            value=top,
            derivation=FieldDerivation.MEMORY_DIRECT,
            source_ids=(top.knowledge_object_id,),
            confidence=1.0,
            last_updated=now,
        )
