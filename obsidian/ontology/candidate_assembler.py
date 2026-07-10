"""Candidate assembler for Haven Ontology retrieval.

Implements the "Evidence Collection" stage described in
``docs/architecture/ONTOLOGY_SPEC.md``, immediately downstream of
:mod:`~obsidian.ontology.activation_spreader` ("Graph Expansion"):

::

    ActivatedConcepts
        │
        ▼
    Attachments            (obsidian.ontology.evidence_collector)
        │
        ▼
    MemoryStore hydration  (obsidian.memory_engine.memory_store)
        │
        ▼
    Candidate[]

This module has exactly one responsibility: turn the
``Dict[UUID, ActivatedConcept]`` produced by
:class:`~obsidian.ontology.activation_spreader.ActivationSpreader` into a
deterministically ordered list of
:class:`~obsidian.ontology.retrieval_models.Candidate` objects, one per
unique ``KnowledgeObject`` reachable through a direct
:class:`~obsidian.ontology.models.Attachment` from any activated Concept.

Responsibilities
----------------
* **Merge activation** — when a ``KnowledgeObject`` is attached to more than
  one activated Concept, combine their activation into the single
  ``Candidate.activation_score`` (see "Aggregation" below).
* **Hydrate KnowledgeObjects** — resolve each attachment's
  ``knowledge_object_id`` to a real
  :class:`~obsidian.manager_ai.models.KnowledgeObject` via
  :class:`~obsidian.memory_engine.memory_store.MemoryStore`.
* **Aggregate supporting concepts** — collect every
  :class:`~obsidian.ontology.retrieval_models.ActivatedConcept` that
  contributed an attachment to a given ``KnowledgeObject`` into that
  Candidate's ``supporting_concepts`` tuple.
* **Preserve deterministic ordering** — output order never depends on dict
  iteration order, insertion order, or the caller's input order.

Explicitly out of scope
------------------------
* **No ranking.** :class:`~obsidian.ontology.retrieval_models.Candidate` is
  produced, never :class:`~obsidian.ontology.retrieval_models.RankedCandidate`.
  No weighting of importance, confidence, or recency happens here.
* **No allocation.** No context budget, slot count, or ``max_results``
  truncation is applied — that is the responsibility of the future Memory
  Ranker / Slot Allocator stage.
* **No context building.** No prompt assembly or string formatting.
* **No graph traversal.** Attachments are looked up directly per Concept
  (an index lookup, not a relationship hop); this module never calls
  :meth:`~obsidian.ontology.concept_graph.ConceptGraph.relationships`,
  ``neighbors``, ``parents``, or ``children``. All propagation across
  relationship edges already happened upstream in
  :class:`~obsidian.ontology.activation_spreader.ActivationSpreader`.

Aggregation
-----------
For a ``KnowledgeObject`` supported by attachments from Concepts
``c_1, ..., c_n`` (each with its own :class:`ActivatedConcept` and
:class:`~obsidian.ontology.models.Attachment`):

* ``attachment_relevance`` is the *maximum* ``Attachment.relevance`` across
  those attachments — "the strength of the strongest direct attachment",
  per :class:`~obsidian.ontology.retrieval_models.Candidate`'s own
  docstring.
* ``activation_score`` is the *maximum* ``ActivatedConcept.activation_score``
  across the supporting concepts — the strongest evidence found for this
  candidate. Taking the maximum (rather than summing) is what keeps the
  result within the ``[0.0, 1.0]`` range ``Candidate`` requires without
  introducing a weighting scheme, which would be ranking.

Both aggregates are order-independent (``max`` over a set), so they do not
depend on attachment or concept iteration order.

Ordering
--------
Output ``Candidate`` objects are sorted by
``str(candidate.knowledge_object.id)`` ascending — the same tie-break
convention used by
:class:`~obsidian.ontology.retrieval_models.RankedCandidate` and
:meth:`~obsidian.memory_engine.memory_store.MemoryStore.all`. Each
``Candidate.supporting_concepts`` tuple is sorted by
``str(concept_id)`` ascending for the same reason.

Missing KnowledgeObjects
------------------------
An :class:`~obsidian.ontology.models.Attachment` referencing a
``knowledge_object_id`` that :class:`~obsidian.memory_engine.memory_store.MemoryStore`
does not have loaded means the memory it once pointed at is no longer in the
vault — the same "stale cross-reference" condition
:class:`~obsidian.ontology.concept_graph_loader.ConceptGraphLoader` already
tolerates for a missing Concept endpoint (a Relationship/Attachment half of a
multi-file write that never landed), except here the missing half is the
*vault file itself* (removed, never persisted, or restored from an older
backup after the attachment was already written). Since ``ConceptGraph`` has
no operation that deletes an Attachment when its Concept is created, this
class of staleness is expected to accumulate over a vault's lifetime and must
not take down retrieval for every other, still-valid Candidate reachable from
the same activated Concepts. Each such Attachment is skipped and logged via
``logging`` (module logger ``obsidian.ontology.candidate_assembler``) at
``WARNING`` with the offending id, mirroring
:class:`~obsidian.ontology.concept_graph_loader.ConceptGraphLoader`'s handling
of its own stale-reference case.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Mapping
from uuid import UUID

from obsidian.memory_engine.memory_store import MemoryStore
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.evidence_collector import EvidenceCollector, ScoredCandidate
from obsidian.ontology.models import Concept
from obsidian.ontology.retrieval_models import ActivatedConcept, Candidate

logger = logging.getLogger(__name__)


class CandidateAssembler:
    """Assembles :class:`Candidate` objects from activation-spreading output.

    Stateless and reusable across calls to :meth:`assemble` with different
    activated-concept tables, graphs, or memory stores.

    Examples
    --------
    >>> assembler = CandidateAssembler()
    >>> candidates = assembler.assemble(activated, graph, store)
    >>> candidates[0].knowledge_object.canonical_fact
    'Haven uses Claude'
    """

    def __init__(self) -> None:
        self._evidence_collector = EvidenceCollector()

    def assemble(
        self,
        activated_concepts: Mapping[UUID, ActivatedConcept],
        graph: ConceptGraph,
        store: MemoryStore,
    ) -> List[Candidate]:
        """Convert activated Concepts into hydrated retrieval Candidates.

        Parameters
        ----------
        activated_concepts : Mapping[UUID, ActivatedConcept]
            Every Concept reached by activation spreading, keyed by Concept
            id — the direct output of
            :meth:`~obsidian.ontology.activation_spreader.ActivationSpreader.spread`.
        graph : ConceptGraph
            Graph supplying the Concept objects and their direct
            Attachments. Never mutated, never traversed for relationships.
        store : MemoryStore
            Already-:meth:`~obsidian.memory_engine.memory_store.MemoryStore.load`-ed
            store used to hydrate ``KnowledgeObject`` instances.

        Returns
        -------
        list[Candidate]
            One Candidate per unique ``KnowledgeObject`` reachable through
            a direct Attachment from any activated Concept, sorted by
            ``str(knowledge_object.id)`` ascending. An Attachment whose
            ``knowledge_object_id`` is no longer in *store* contributes no
            Candidate (see "Missing KnowledgeObjects" in the module
            docstring) rather than aborting the whole call.
        """
        concepts: List[Concept] = [
            graph.get_concept(concept_id)
            for concept_id in sorted(activated_concepts, key=str)
            if graph.has_concept(concept_id)
        ]

        scored: List[ScoredCandidate] = self._evidence_collector.collect(
            concepts, graph
        )

        by_knowledge_object: Dict[UUID, List[ScoredCandidate]] = defaultdict(list)
        for entry in scored:
            by_knowledge_object[entry.knowledge_object_id].append(entry)

        candidates: List[Candidate] = []
        for ko_id in sorted(by_knowledge_object, key=str):
            entries = sorted(
                by_knowledge_object[ko_id], key=lambda e: str(e.concept_id)
            )

            if not store.has(ko_id):
                logger.warning(
                    "Skipping Attachment(s) for knowledge_object_id %s: no "
                    "longer present in the MemoryStore (memory removed from "
                    "the vault after the attachment was written)",
                    ko_id,
                )
                continue

            supporting_concepts = tuple(
                activated_concepts[entry.concept_id] for entry in entries
            )
            attachment_relevance = max(
                entry.attachment_relevance for entry in entries
            )
            activation_score = max(
                concept.activation_score for concept in supporting_concepts
            )

            candidates.append(
                Candidate(
                    knowledge_object=store.get(ko_id),
                    supporting_concepts=supporting_concepts,
                    attachment_relevance=attachment_relevance,
                    activation_score=activation_score,
                )
            )

        return candidates
