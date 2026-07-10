"""Evidence collector for Haven Ontology retrieval.

Collects KnowledgeObjects directly attached to resolved Concepts.
No propagation, no traversal beyond direct attachments, no ranking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set, Tuple
from uuid import UUID

from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.models import Concept


@dataclass(frozen=True)
class ScoredCandidate:
    """A KnowledgeObject directly attached to a resolved Concept.

    Parameters
    ----------
    knowledge_object_id : UUID
        The UUID of the attached KnowledgeObject.
    concept_id : UUID
        The UUID of the Concept it is attached to.
    attachment_relevance : float
        The relevance score of the attachment (0.0–1.0).
    """

    knowledge_object_id: UUID
    concept_id: UUID
    attachment_relevance: float


class EvidenceCollector:
    """Collects :class:`ScoredCandidate` entries from direct graph attachments.

    Given a list of resolved :class:`~obsidian.ontology.models.Concept` objects
    and a :class:`~obsidian.ontology.concept_graph.ConceptGraph`, returns one
    :class:`ScoredCandidate` for each unique ``(knowledge_object_id, concept_id)``
    pair found in the graph's attachment index.

    No activation spreading, neighbour traversal, ranking, keyword fallback,
    LLM calls, or embedding lookups.
    """

    def collect(
        self,
        concepts: List[Concept],
        graph: ConceptGraph,
    ) -> List[ScoredCandidate]:
        """Return candidates for all KOs directly attached to *concepts*.

        Parameters
        ----------
        concepts : list[Concept]
            Resolved concepts whose direct attachments to collect.
            Duplicate entries are tolerated and handled transparently.
        graph : ConceptGraph
            Graph to query for attachments.

        Returns
        -------
        list[ScoredCandidate]
            One entry per unique ``(knowledge_object_id, concept_id)`` pair,
            sorted by ``(str(concept_id), str(knowledge_object_id))`` for
            deterministic ordering regardless of input order.
        """
        seen: Set[Tuple[UUID, UUID]] = set()
        candidates: List[ScoredCandidate] = []

        for concept in concepts:
            if not graph.has_concept(concept.id):
                continue
            for attachment in graph.attachments_for_concept(concept.id):
                key = (attachment.knowledge_object_id, attachment.concept_id)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    ScoredCandidate(
                        knowledge_object_id=attachment.knowledge_object_id,
                        concept_id=attachment.concept_id,
                        attachment_relevance=attachment.relevance,
                    )
                )

        candidates.sort(
            key=lambda c: (str(c.concept_id), str(c.knowledge_object_id))
        )
        return candidates
