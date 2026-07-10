"""Deterministic Working Context assembly for Haven Ontology retrieval.

Implements the final, previously-missing stage between the Slot Allocator
and the Structured Prompt Builder:

::

    RankedCandidate[]
        │
        ▼
    WorkingContextBuilder        (this module)
        │
        ▼
    WorkingContext[]

This module has exactly one responsibility: group the already-selected,
already-ranked candidates into one or more
:class:`~obsidian.ontology.retrieval_models.WorkingContext` objects, using
the ontology evidence each candidate already carries
(:attr:`~obsidian.ontology.retrieval_models.Candidate.supporting_concepts`).
It never decides *which* candidates make the cut or *how good* they are —
only how the already-decided set is organised for presentation.

Explicitly out of scope
------------------------
* **No retrieval.** Nothing is fetched from a
  :class:`~obsidian.memory_engine.memory_store.MemoryStore` or
  :class:`~obsidian.ontology.concept_graph.ConceptGraph`; grouping uses only
  the ``supporting_concepts`` each :class:`~obsidian.ontology.retrieval_models.Candidate`
  already carries.
* **No ranking.** ``final_score`` and ``score_breakdown`` are consumed as
  given (via :class:`~obsidian.ontology.retrieval_models.RankedCandidate`'s
  own total order); this module never computes, recomputes, or adjusts a
  score. That is
  :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s
  responsibility.
* **No acceptance or slot allocation.** Which candidates are present, and
  how many, is decided upstream by
  :class:`~obsidian.memory_engine.acceptance_stage.AcceptanceStage` and
  :class:`~obsidian.memory_engine.deterministic_slot_allocator.DeterministicSlotAllocator`.
  This module never drops, adds, or re-scores a candidate.
* **No prompt generation.** No string rendering or XML assembly — that is
  :class:`~obsidian.memory_engine.structured_prompt_builder.StructuredPromptBuilder`'s
  responsibility.
* **No mutation.** ``RankedCandidate`` instances are frozen dataclasses and
  are never modified or rebuilt; a returned context's buckets reference the
  exact same objects the caller passed in.

Grouping rule
-------------
Each candidate is assigned to the group of its *primary concept* — the
:class:`~obsidian.ontology.retrieval_models.ActivatedConcept` in
``supporting_concepts`` with the highest ``activation_score`` (ties broken
by ascending ``str(concept_id)`` for determinism). One
:class:`~obsidian.ontology.retrieval_models.WorkingContext` is produced per
distinct primary concept found in the input, in ascending
``str(concept_id)`` order. Candidates with no ontology evidence at all
(``supporting_concepts == ()``) never anchor a context; they fall through
to a single shared ``GENERAL`` context instead.

If this process produces no groups whatsoever — the input is empty, or no
candidate has any ontology evidence — exactly one default ``GENERAL``
:class:`~obsidian.ontology.retrieval_models.WorkingContext` is returned
(possibly empty), rather than an empty list. This is what "no grouping
exists" degenerates to: a single catch-all context, never zero contexts.

Design decisions
-----------------
* **Order is never trusted, exactly as in the Slot Allocator.** Candidates
  are re-sorted via ``sorted()`` (``RankedCandidate``'s own total order)
  before bucketing, so output is identical regardless of input order.
* **Role buckets are total.** Every :class:`~obsidian.ontology.retrieval_models.MemoryRole`
  gets a bucket in every context, in the enum's declaration order, even
  when empty — this matches what
  :class:`~obsidian.memory_engine.structured_prompt_builder.StructuredPromptBuilder`
  expects (each role has a fixed XML tag that self-closes when empty).
* **State is a pure projection.** Each context's
  :class:`~obsidian.ontology.retrieval_models.WorkingContextState` is
  derived exclusively via
  :meth:`~obsidian.ontology.retrieval_models.WorkingContextState.from_buckets`
  over that same context's buckets — never hand-rolled here — so a memory
  referenced by ``state`` always also appears in one of ``buckets``.
"""

from __future__ import annotations

from typing import Dict, List, Optional
from uuid import UUID

from obsidian.ontology.retrieval_models import (
    ActivatedConcept,
    ContextKind,
    MemoryRole,
    RankedCandidate,
    RoleBucket,
    WorkingContext,
    WorkingContextState,
    resolve_role,
)

#: Key used for the shared catch-all context (no anchor concept).
_GENERAL_KEY = "ctx:general"
_GENERAL_TITLE = "General"


class WorkingContextBuilder:
    """Groups ranked candidates into one or more Working Contexts.

    Stateless and reusable across calls to :meth:`build` with different
    candidate lists.

    Examples
    --------
    >>> builder = WorkingContextBuilder()
    >>> contexts = builder.build(allocated)  # doctest: +SKIP
    >>> len(contexts) >= 1  # doctest: +SKIP
    True
    """

    def build(self, ranked_candidates: List[RankedCandidate]) -> List[WorkingContext]:
        """Group *ranked_candidates* into deterministic Working Contexts.

        Parameters
        ----------
        ranked_candidates : list[RankedCandidate]
            Candidates to assemble, typically the output of
            :meth:`~obsidian.memory_engine.deterministic_slot_allocator.DeterministicSlotAllocator.allocate`.
            Not mutated; scores and membership are never recomputed. Order
            is not trusted — candidates are re-sorted internally.

        Returns
        -------
        list[WorkingContext]
            One context per distinct primary concept found in the input
            (ascending ``str(concept_id)`` order), followed by a single
            shared ``GENERAL`` context for any candidates with no ontology
            evidence. If no candidate has any ontology evidence — including
            when *ranked_candidates* is empty — the result is exactly one
            ``GENERAL`` context.
        """
        ordered = sorted(ranked_candidates)

        grouped: Dict[UUID, List[RankedCandidate]] = {}
        general: List[RankedCandidate] = []
        for ranked in ordered:
            anchor = self._primary_concept_id(ranked)
            if anchor is None:
                general.append(ranked)
            else:
                grouped.setdefault(anchor, []).append(ranked)

        contexts = [
            self._build_context(
                key=f"ctx:{anchor}",
                title=str(anchor),
                kind=ContextKind.TOPIC,
                members=members,
                anchor_concept_id=anchor,
            )
            for anchor, members in sorted(grouped.items(), key=lambda item: str(item[0]))
        ]

        if general or not contexts:
            contexts.append(
                self._build_context(
                    key=_GENERAL_KEY,
                    title=_GENERAL_TITLE,
                    kind=ContextKind.GENERAL,
                    members=general,
                    anchor_concept_id=None,
                )
            )

        return contexts

    @staticmethod
    def _primary_concept_id(ranked: RankedCandidate) -> Optional[UUID]:
        """Return the highest-activation supporting concept id, if any.

        Ties broken by ascending ``str(concept_id)`` so the choice never
        depends on ``supporting_concepts`` insertion order.
        """
        supporting_concepts = ranked.candidate.supporting_concepts
        if not supporting_concepts:
            return None

        def key(activated: ActivatedConcept) -> tuple:
            return (-activated.activation_score, str(activated.concept_id))

        return min(supporting_concepts, key=key).concept_id

    @staticmethod
    def _build_context(
        *,
        key: str,
        title: str,
        kind: ContextKind,
        members: List[RankedCandidate],
        anchor_concept_id: Optional[UUID],
    ) -> WorkingContext:
        by_role: Dict[MemoryRole, List[RankedCandidate]] = {role: [] for role in MemoryRole}
        for ranked in members:
            role = resolve_role(ranked.candidate.knowledge_object)
            by_role[role].append(ranked)

        buckets = tuple(
            RoleBucket(role=role, members=tuple(by_role[role])) for role in MemoryRole
        )

        member_concept_ids = tuple(
            sorted(
                {
                    activated.concept_id
                    for ranked in members
                    for activated in ranked.candidate.supporting_concepts
                },
                key=str,
            )
        )

        return WorkingContext(
            key=key,
            title=title,
            kind=kind,
            state=WorkingContextState.from_buckets(list(buckets)),
            buckets=buckets,
            anchor_concept_id=anchor_concept_id,
            member_concept_ids=member_concept_ids,
        )
