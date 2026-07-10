"""Deterministic slot allocation for Haven Ontology retrieval.

Implements the "Slot Allocator" stage described in
``docs/architecture/ONTOLOGY_SPEC.md``, immediately downstream of
:mod:`~obsidian.memory_engine.deterministic_ranker` ("Ranking"):

::

    RankedCandidate[]
        │
        ▼
    DeterministicSlotAllocator   (this module)
        │
        ▼
    RankedCandidate[]

This module has exactly one responsibility: select the highest-ranked
:class:`~obsidian.ontology.retrieval_models.RankedCandidate` objects that
fit within a configurable context budget
(``RetrievalConfig.max_results``).

Explicitly out of scope
------------------------
* **No ranking.** ``final_score`` and ``score_breakdown`` are consumed as
  given; this module never computes, recomputes, or adjusts a score. That
  is :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s
  responsibility.
* **No formatting.** No prompt assembly or string rendering — that is the
  future Context Builder stage's responsibility.
* **No retrieval.** Nothing is fetched from a
  :class:`~obsidian.memory_engine.memory_store.MemoryStore` or
  :class:`~obsidian.ontology.concept_graph.ConceptGraph`.
* **No mutation.** ``RankedCandidate`` instances are frozen dataclasses and
  are never modified, rebuilt, or copied — the returned list contains the
  exact same objects the caller passed in, merely selected and ordered.

Selection, not truncation
--------------------------
The task is to "select the highest ranked candidates", not to "take the
first N". The allocator therefore never trusts the caller's input order —
it re-derives the highest-ranked prefix itself using
:class:`~obsidian.ontology.retrieval_models.RankedCandidate`'s own total
order (descending ``final_score``, ties broken by ascending
``str(knowledge_object.id)``, via its rich comparison methods) and only
then takes the leading ``config.max_results`` entries. This makes
:meth:`DeterministicSlotAllocator.allocate` correct even if it is ever
called with an unsorted or partially-sorted list, without re-implementing
:class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s
ordering contract — ``sorted()`` is simply called again over the same,
already-tested comparison operators.

Design decisions
-----------------
* **The budget is ``RetrievalConfig.max_results``.** No separate
  token/character budget concept is introduced; ``RetrievalConfig`` only
  exposes a slot count, and the task scopes this module to that existing
  field.
* **Fewer candidates than the budget is not an error.** If
  ``len(ranked_candidates) <= config.max_results`` every candidate is
  returned, in ranked order.
* **Ties are resolved identically to the Ranker.** Reusing
  ``RankedCandidate``'s comparison operators (rather than re-deriving a
  tie-break rule here) guarantees the allocator's notion of "highest
  ranked" never drifts from the Ranker's.
"""

from __future__ import annotations

from typing import List

from obsidian.ontology.retrieval_config import RetrievalConfig
from obsidian.ontology.retrieval_models import RankedCandidate


class DeterministicSlotAllocator:
    """Selects the highest-ranked candidates within a configurable context budget.

    Stateless and reusable across calls to :meth:`allocate` with different
    ranked-candidate lists or configurations.

    Examples
    --------
    >>> allocator = DeterministicSlotAllocator()
    >>> allocated = allocator.allocate(ranked_candidates, RetrievalConfig())
    >>> len(allocated) <= RetrievalConfig().max_results
    True
    """

    def allocate(
        self,
        ranked_candidates: List[RankedCandidate],
        config: RetrievalConfig,
    ) -> List[RankedCandidate]:
        """Select the highest-ranked candidates within ``config.max_results``.

        Parameters
        ----------
        ranked_candidates : list[RankedCandidate]
            Candidates to allocate slots to, typically the output of
            :meth:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker.rank`.
            Not mutated; scores are never recomputed. Order is not
            trusted — the highest-ranked prefix is re-derived internally.
        config : RetrievalConfig
            Supplies ``max_results``, the context-budget slot count.

        Returns
        -------
        list[RankedCandidate]
            The leading ``config.max_results`` candidates (or all of them,
            if fewer than the budget), ordered by descending
            ``final_score`` with ties broken by ascending
            ``str(knowledge_object.id)``. Each entry is the identical
            ``RankedCandidate`` instance supplied by the caller.
        """
        return sorted(ranked_candidates)[: config.max_results]
