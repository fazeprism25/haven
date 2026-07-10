"""In-memory Concept Graph for the Haven Ontology.

Stores Concepts, Relationships, and Attachments with efficient lookup
methods.  All mutation methods are idempotent: re-adding an object whose
deterministic UUID is already present is a silent no-op.  All traversal
results are sorted by UUID string so that two graphs built from the same
set of mutations (in any insertion order) produce identical query results.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Set
from uuid import UUID

from obsidian.ontology.models import Attachment, Concept, Relationship


class ConceptGraph:
    """Deterministic in-memory graph of Concepts, Relationships, and Attachments.

    Relationships require both endpoint Concepts to be present before they
    can be added.  Attachments require their referenced Concept to be
    present; the KnowledgeObject is tracked by UUID only and does not need
    to be registered.

    All query methods that return multiple items are sorted by UUID string
    for deterministic ordering.
    """

    def __init__(self) -> None:
        self._concepts: Dict[UUID, Concept] = {}
        self._relationships: Dict[UUID, Relationship] = {}
        # concept UUID → set of relationship UUIDs where it is the source
        self._outgoing: Dict[UUID, Set[UUID]] = defaultdict(set)
        # concept UUID → set of relationship UUIDs where it is the target
        self._incoming: Dict[UUID, Set[UUID]] = defaultdict(set)
        self._attachments: Dict[UUID, Attachment] = {}
        # concept UUID → set of attachment UUIDs
        self._by_concept: Dict[UUID, Set[UUID]] = defaultdict(set)
        # knowledge-object UUID → set of attachment UUIDs
        self._by_ko: Dict[UUID, Set[UUID]] = defaultdict(set)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_concept(self, concept: Concept) -> None:
        """Add a Concept to the graph; no-op if already present.

        Parameters
        ----------
        concept : Concept
        """
        self._concepts.setdefault(concept.id, concept)

    def add_relationship(self, relationship: Relationship) -> None:
        """Add a Relationship between two existing Concepts; no-op if already present.

        Parameters
        ----------
        relationship : Relationship

        Raises
        ------
        KeyError
            If either endpoint Concept is not present in the graph.
        """
        if relationship.id in self._relationships:
            return
        if relationship.source_id not in self._concepts:
            raise KeyError(f"source Concept {relationship.source_id} not in graph")
        if relationship.target_id not in self._concepts:
            raise KeyError(f"target Concept {relationship.target_id} not in graph")
        self._relationships[relationship.id] = relationship
        self._outgoing[relationship.source_id].add(relationship.id)
        self._incoming[relationship.target_id].add(relationship.id)

    def add_attachment(self, attachment: Attachment) -> None:
        """Add an Attachment linking a KnowledgeObject to a Concept; no-op if already present.

        The referenced Concept must already be in the graph.  The
        KnowledgeObject is tracked by UUID only.

        Parameters
        ----------
        attachment : Attachment

        Raises
        ------
        KeyError
            If the referenced Concept is not present in the graph.
        """
        if attachment.id in self._attachments:
            return
        if attachment.concept_id not in self._concepts:
            raise KeyError(f"Concept {attachment.concept_id} not in graph")
        self._attachments[attachment.id] = attachment
        self._by_concept[attachment.concept_id].add(attachment.id)
        self._by_ko[attachment.knowledge_object_id].add(attachment.id)

    # ------------------------------------------------------------------
    # Concept queries
    # ------------------------------------------------------------------

    def get_concept(self, concept_id: UUID) -> Concept:
        """Return a Concept by UUID.

        Raises
        ------
        KeyError
            If no Concept with that UUID is stored.
        """
        return self._concepts[concept_id]

    def has_concept(self, concept_id: UUID) -> bool:
        """Return True if a Concept with that UUID is stored."""
        return concept_id in self._concepts

    def all_concepts(self) -> List[Concept]:
        """Return every Concept currently stored in the graph.

        Sorted by UUID string for determinism. Read-only; does not mutate
        the graph.
        """
        return sorted(self._concepts.values(), key=lambda c: str(c.id))

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def parents(self, concept_id: UUID) -> List[Concept]:
        """Return Concepts with an outgoing edge pointing TO concept_id.

        Sorted by UUID string for determinism.
        """
        source_ids = {
            self._relationships[rid].source_id
            for rid in self._incoming.get(concept_id, set())
        }
        return sorted(
            (self._concepts[sid] for sid in source_ids if sid in self._concepts),
            key=lambda c: str(c.id),
        )

    def children(self, concept_id: UUID) -> List[Concept]:
        """Return Concepts reachable from concept_id via outgoing edges.

        Sorted by UUID string for determinism.
        """
        target_ids = {
            self._relationships[rid].target_id
            for rid in self._outgoing.get(concept_id, set())
        }
        return sorted(
            (self._concepts[tid] for tid in target_ids if tid in self._concepts),
            key=lambda c: str(c.id),
        )

    def neighbors(self, concept_id: UUID) -> List[Concept]:
        """Return all Concepts adjacent to concept_id in either direction.

        Duplicates and self-references are removed.  Sorted by UUID string.
        """
        adjacent_ids: Set[UUID] = set()
        for rid in self._outgoing.get(concept_id, set()):
            adjacent_ids.add(self._relationships[rid].target_id)
        for rid in self._incoming.get(concept_id, set()):
            adjacent_ids.add(self._relationships[rid].source_id)
        adjacent_ids.discard(concept_id)
        return sorted(
            (self._concepts[cid] for cid in adjacent_ids if cid in self._concepts),
            key=lambda c: str(c.id),
        )

    # ------------------------------------------------------------------
    # Relationship queries
    # ------------------------------------------------------------------

    def relationships(self, concept_id: UUID) -> List[Relationship]:
        """Return all Relationships involving concept_id as source or target.

        Sorted by relationship UUID string for determinism.
        """
        rel_ids = self._outgoing.get(concept_id, set()) | self._incoming.get(
            concept_id, set()
        )
        return sorted(
            (self._relationships[rid] for rid in rel_ids),
            key=lambda r: str(r.id),
        )

    # ------------------------------------------------------------------
    # Attachment queries
    # ------------------------------------------------------------------

    def attachments_for_concept(self, concept_id: UUID) -> List[Attachment]:
        """Return all Attachments linked to a given Concept.

        Sorted by attachment UUID string for determinism.
        """
        return sorted(
            (
                self._attachments[aid]
                for aid in self._by_concept.get(concept_id, set())
            ),
            key=lambda a: str(a.id),
        )

    def concepts_for_knowledge_object(
        self, knowledge_object_id: UUID
    ) -> List[Concept]:
        """Return all Concepts linked to a given KnowledgeObject.

        Sorted by Concept UUID string for determinism.
        """
        concept_ids = {
            self._attachments[aid].concept_id
            for aid in self._by_ko.get(knowledge_object_id, set())
        }
        return sorted(
            (self._concepts[cid] for cid in concept_ids if cid in self._concepts),
            key=lambda c: str(c.id),
        )
