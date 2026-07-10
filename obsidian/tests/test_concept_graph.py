"""Unit tests for obsidian.ontology.concept_graph.

Test groups:
* TestConstruction           — empty graph invariants.
* TestAddConcept             — add, idempotency, label collision.
* TestAddRelationship        — add, missing endpoints, idempotency.
* TestAddAttachment          — add, missing concept, idempotency.
* TestGetConcept             — hit and miss.
* TestHasConcept             — present and absent.
* TestAllConcepts            — empty, single, multiple sorted, read-only.
* TestParents                — direction, multiple, isolation.
* TestChildren               — direction, multiple, isolation.
* TestNeighbors              — both directions, dedup, self-exclusion.
* TestRelationships          — both directions, dedup.
* TestAttachmentsForConcept  — single, multiple, absent.
* TestConceptsForKO          — single, multiple, absent, multi-concept per KO.
* TestDeterminism            — insertion-order independence.
* TestEdgeCases              — isolated concepts, unknown UUIDs, graph with one edge.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.enums import OntologyRelationshipType
from obsidian.ontology.models import Attachment, Concept, Relationship


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def graph() -> ConceptGraph:
    return ConceptGraph()


@pytest.fixture
def haven() -> Concept:
    return Concept.from_label("Haven")


@pytest.fixture
def claude() -> Concept:
    return Concept.from_label("Claude")


@pytest.fixture
def qdrant() -> Concept:
    return Concept.from_label("Qdrant")


@pytest.fixture
def haven_uses_claude(haven: Concept, claude: Concept) -> Relationship:
    return Relationship.create(haven.id, claude.id, OntologyRelationshipType.USES)


@pytest.fixture
def haven_depends_on_qdrant(haven: Concept, qdrant: Concept) -> Relationship:
    return Relationship.create(
        haven.id, qdrant.id, OntologyRelationshipType.DEPENDS_ON
    )


@pytest.fixture
def ko_id() -> UUID:
    return uuid4()


@pytest.fixture
def attachment_haven(ko_id: UUID, haven: Concept) -> Attachment:
    return Attachment.create(ko_id, haven.id)


# ---------------------------------------------------------------------------
# TestConstruction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_instantiates(self) -> None:
        g = ConceptGraph()
        assert isinstance(g, ConceptGraph)

    def test_has_no_concepts(self, graph: ConceptGraph) -> None:
        absent = Concept.from_label("Ghost")
        assert not graph.has_concept(absent.id)

    def test_parents_unknown_concept_returns_empty(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        assert graph.parents(haven.id) == []

    def test_children_unknown_concept_returns_empty(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        assert graph.children(haven.id) == []

    def test_neighbors_unknown_concept_returns_empty(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        assert graph.neighbors(haven.id) == []

    def test_relationships_unknown_concept_returns_empty(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        assert graph.relationships(haven.id) == []

    def test_attachments_for_concept_unknown_returns_empty(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        assert graph.attachments_for_concept(haven.id) == []

    def test_concepts_for_ko_unknown_returns_empty(
        self, graph: ConceptGraph, ko_id: UUID
    ) -> None:
        assert graph.concepts_for_knowledge_object(ko_id) == []


# ---------------------------------------------------------------------------
# TestAddConcept
# ---------------------------------------------------------------------------


class TestAddConcept:
    def test_added_concept_is_present(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        assert graph.has_concept(haven.id)

    def test_get_concept_after_add(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        assert graph.get_concept(haven.id) is haven

    def test_add_multiple_concepts(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        assert graph.has_concept(haven.id)
        assert graph.has_concept(claude.id)

    def test_add_idempotent_no_error(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(haven)  # second call must not raise
        assert graph.has_concept(haven.id)

    def test_idempotent_preserves_first_object(
        self, graph: ConceptGraph
    ) -> None:
        # Two Concept objects with the same label produce the same UUID.
        c1 = Concept.from_label("Siddhartha", description="first")
        c2 = Concept.from_label("Siddhartha", description="second")
        graph.add_concept(c1)
        graph.add_concept(c2)
        # First inserted object is kept.
        assert graph.get_concept(c1.id).description == "first"

    def test_absent_concept_not_present(
        self, graph: ConceptGraph, haven: Concept, claude: Concept
    ) -> None:
        graph.add_concept(haven)
        assert not graph.has_concept(claude.id)


# ---------------------------------------------------------------------------
# TestAddRelationship
# ---------------------------------------------------------------------------


class TestAddRelationship:
    def test_add_relationship_succeeds(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_relationship(haven_uses_claude)  # must not raise

    def test_add_relationship_missing_source_raises(
        self,
        graph: ConceptGraph,
        claude: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        graph.add_concept(claude)
        with pytest.raises(KeyError, match="source Concept"):
            graph.add_relationship(haven_uses_claude)

    def test_add_relationship_missing_target_raises(
        self,
        graph: ConceptGraph,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        graph.add_concept(haven)
        with pytest.raises(KeyError, match="target Concept"):
            graph.add_relationship(haven_uses_claude)

    def test_add_relationship_both_missing_raises(
        self,
        graph: ConceptGraph,
        haven_uses_claude: Relationship,
    ) -> None:
        with pytest.raises(KeyError):
            graph.add_relationship(haven_uses_claude)

    def test_add_relationship_idempotent(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_relationship(haven_uses_claude)
        graph.add_relationship(haven_uses_claude)  # must not raise or duplicate
        assert len(graph.relationships(haven.id)) == 1

    def test_two_relationships_from_same_source(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        qdrant: Concept,
        haven_uses_claude: Relationship,
        haven_depends_on_qdrant: Relationship,
    ) -> None:
        for c in (haven, claude, qdrant):
            graph.add_concept(c)
        graph.add_relationship(haven_uses_claude)
        graph.add_relationship(haven_depends_on_qdrant)
        assert len(graph.relationships(haven.id)) == 2


# ---------------------------------------------------------------------------
# TestAddAttachment
# ---------------------------------------------------------------------------


class TestAddAttachment:
    def test_add_attachment_succeeds(
        self,
        graph: ConceptGraph,
        haven: Concept,
        attachment_haven: Attachment,
    ) -> None:
        graph.add_concept(haven)
        graph.add_attachment(attachment_haven)  # must not raise

    def test_add_attachment_missing_concept_raises(
        self,
        graph: ConceptGraph,
        attachment_haven: Attachment,
    ) -> None:
        with pytest.raises(KeyError, match="Concept"):
            graph.add_attachment(attachment_haven)

    def test_add_attachment_idempotent(
        self,
        graph: ConceptGraph,
        haven: Concept,
        attachment_haven: Attachment,
    ) -> None:
        graph.add_concept(haven)
        graph.add_attachment(attachment_haven)
        graph.add_attachment(attachment_haven)  # must not raise or duplicate
        assert len(graph.attachments_for_concept(haven.id)) == 1

    def test_multiple_attachments_same_concept(
        self,
        graph: ConceptGraph,
        haven: Concept,
    ) -> None:
        graph.add_concept(haven)
        a1 = Attachment.create(uuid4(), haven.id)
        a2 = Attachment.create(uuid4(), haven.id)
        graph.add_attachment(a1)
        graph.add_attachment(a2)
        assert len(graph.attachments_for_concept(haven.id)) == 2


# ---------------------------------------------------------------------------
# TestGetConcept
# ---------------------------------------------------------------------------


class TestGetConcept:
    def test_get_returns_concept(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        result = graph.get_concept(haven.id)
        assert result.label == "Haven"

    def test_get_missing_raises_key_error(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        with pytest.raises(KeyError):
            graph.get_concept(haven.id)

    def test_get_wrong_id_raises(
        self, graph: ConceptGraph, haven: Concept, claude: Concept
    ) -> None:
        graph.add_concept(haven)
        with pytest.raises(KeyError):
            graph.get_concept(claude.id)


# ---------------------------------------------------------------------------
# TestHasConcept
# ---------------------------------------------------------------------------


class TestHasConcept:
    def test_has_added_concept(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        assert graph.has_concept(haven.id) is True

    def test_has_absent_concept(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        assert graph.has_concept(haven.id) is False

    def test_has_arbitrary_uuid(self, graph: ConceptGraph) -> None:
        assert graph.has_concept(uuid4()) is False


# ---------------------------------------------------------------------------
# TestAllConcepts
# ---------------------------------------------------------------------------


class TestAllConcepts:
    def test_empty_graph_returns_empty_list(self, graph: ConceptGraph) -> None:
        assert graph.all_concepts() == []

    def test_returns_added_concept(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        assert graph.all_concepts() == [haven]

    def test_returns_all_added_concepts_sorted_by_uuid(
        self, graph: ConceptGraph, haven: Concept, claude: Concept, qdrant: Concept
    ) -> None:
        graph.add_concept(claude)
        graph.add_concept(haven)
        graph.add_concept(qdrant)

        expected = sorted([haven, claude, qdrant], key=lambda c: str(c.id))
        assert graph.all_concepts() == expected

    def test_does_not_mutate_graph(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        graph.all_concepts()
        assert graph.all_concepts() == [haven]


# ---------------------------------------------------------------------------
# TestParents
# ---------------------------------------------------------------------------


class TestParents:
    def test_no_parents_for_isolated_concept(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        assert graph.parents(haven.id) == []

    def test_single_parent(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_relationship(haven_uses_claude)
        # claude is the target; haven is the parent
        result = graph.parents(claude.id)
        assert len(result) == 1
        assert result[0].id == haven.id

    def test_source_concept_has_no_parents_via_own_edge(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_relationship(haven_uses_claude)
        assert graph.parents(haven.id) == []

    def test_multiple_parents(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        qdrant: Concept,
    ) -> None:
        for c in (haven, claude, qdrant):
            graph.add_concept(c)
        # Both haven and claude point to qdrant
        graph.add_relationship(
            Relationship.create(haven.id, qdrant.id, OntologyRelationshipType.USES)
        )
        graph.add_relationship(
            Relationship.create(claude.id, qdrant.id, OntologyRelationshipType.USES)
        )
        parents = graph.parents(qdrant.id)
        parent_ids = {p.id for p in parents}
        assert haven.id in parent_ids
        assert claude.id in parent_ids

    def test_parents_sorted(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        qdrant: Concept,
    ) -> None:
        for c in (haven, claude, qdrant):
            graph.add_concept(c)
        graph.add_relationship(
            Relationship.create(haven.id, qdrant.id, OntologyRelationshipType.USES)
        )
        graph.add_relationship(
            Relationship.create(claude.id, qdrant.id, OntologyRelationshipType.USES)
        )
        parents = graph.parents(qdrant.id)
        ids_str = [str(p.id) for p in parents]
        assert ids_str == sorted(ids_str)

    def test_parents_unknown_id_returns_empty(
        self, graph: ConceptGraph
    ) -> None:
        assert graph.parents(uuid4()) == []


# ---------------------------------------------------------------------------
# TestChildren
# ---------------------------------------------------------------------------


class TestChildren:
    def test_no_children_for_isolated_concept(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        assert graph.children(haven.id) == []

    def test_single_child(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_relationship(haven_uses_claude)
        result = graph.children(haven.id)
        assert len(result) == 1
        assert result[0].id == claude.id

    def test_target_has_no_children_via_incoming_edge(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_relationship(haven_uses_claude)
        assert graph.children(claude.id) == []

    def test_multiple_children(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        qdrant: Concept,
        haven_uses_claude: Relationship,
        haven_depends_on_qdrant: Relationship,
    ) -> None:
        for c in (haven, claude, qdrant):
            graph.add_concept(c)
        graph.add_relationship(haven_uses_claude)
        graph.add_relationship(haven_depends_on_qdrant)
        children = graph.children(haven.id)
        child_ids = {c.id for c in children}
        assert claude.id in child_ids
        assert qdrant.id in child_ids

    def test_children_sorted(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        qdrant: Concept,
        haven_uses_claude: Relationship,
        haven_depends_on_qdrant: Relationship,
    ) -> None:
        for c in (haven, claude, qdrant):
            graph.add_concept(c)
        graph.add_relationship(haven_uses_claude)
        graph.add_relationship(haven_depends_on_qdrant)
        children = graph.children(haven.id)
        ids_str = [str(c.id) for c in children]
        assert ids_str == sorted(ids_str)

    def test_children_unknown_id_returns_empty(
        self, graph: ConceptGraph
    ) -> None:
        assert graph.children(uuid4()) == []


# ---------------------------------------------------------------------------
# TestNeighbors
# ---------------------------------------------------------------------------


class TestNeighbors:
    def test_no_neighbors_for_isolated_concept(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        assert graph.neighbors(haven.id) == []

    def test_outgoing_edge_creates_neighbor(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_relationship(haven_uses_claude)
        assert claude in graph.neighbors(haven.id)

    def test_incoming_edge_creates_neighbor(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_relationship(haven_uses_claude)
        # claude is the target; haven is a neighbor FROM claude's perspective
        assert haven in graph.neighbors(claude.id)

    def test_neighbors_deduplicated_when_bidirectional(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        # Add two edges in opposite directions
        graph.add_relationship(
            Relationship.create(haven.id, claude.id, OntologyRelationshipType.USES)
        )
        graph.add_relationship(
            Relationship.create(claude.id, haven.id, OntologyRelationshipType.RELATED_TO)
        )
        # claude appears as both child and parent of haven, but only once
        neighbors = graph.neighbors(haven.id)
        assert neighbors.count(claude) == 1

    def test_neighbors_does_not_include_self(
        self, graph: ConceptGraph, haven: Concept, claude: Concept
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_relationship(
            Relationship.create(haven.id, claude.id, OntologyRelationshipType.USES)
        )
        assert haven not in graph.neighbors(haven.id)

    def test_neighbors_includes_both_directions(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        qdrant: Concept,
        haven_uses_claude: Relationship,
        haven_depends_on_qdrant: Relationship,
    ) -> None:
        for c in (haven, claude, qdrant):
            graph.add_concept(c)
        graph.add_relationship(haven_uses_claude)
        graph.add_relationship(haven_depends_on_qdrant)
        neighbors_haven = {n.id for n in graph.neighbors(haven.id)}
        assert claude.id in neighbors_haven
        assert qdrant.id in neighbors_haven

    def test_neighbors_sorted(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        qdrant: Concept,
        haven_uses_claude: Relationship,
        haven_depends_on_qdrant: Relationship,
    ) -> None:
        for c in (haven, claude, qdrant):
            graph.add_concept(c)
        graph.add_relationship(haven_uses_claude)
        graph.add_relationship(haven_depends_on_qdrant)
        neighbors = graph.neighbors(haven.id)
        ids_str = [str(n.id) for n in neighbors]
        assert ids_str == sorted(ids_str)

    def test_neighbors_unknown_id_returns_empty(
        self, graph: ConceptGraph
    ) -> None:
        assert graph.neighbors(uuid4()) == []


# ---------------------------------------------------------------------------
# TestRelationships
# ---------------------------------------------------------------------------


class TestRelationships:
    def test_no_relationships_for_isolated_concept(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        assert graph.relationships(haven.id) == []

    def test_outgoing_relationship_returned(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_relationship(haven_uses_claude)
        assert haven_uses_claude in graph.relationships(haven.id)

    def test_incoming_relationship_returned(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_relationship(haven_uses_claude)
        assert haven_uses_claude in graph.relationships(claude.id)

    def test_relationship_not_duplicated_for_shared_concept(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        # If concept is both source and target it should still appear only once.
        # (self-loops are prohibited by Relationship, so test via idempotency.)
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_relationship(haven_uses_claude)
        graph.add_relationship(haven_uses_claude)  # duplicate add
        assert graph.relationships(haven.id).count(haven_uses_claude) == 1

    def test_multiple_relationships_returned(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        qdrant: Concept,
        haven_uses_claude: Relationship,
        haven_depends_on_qdrant: Relationship,
    ) -> None:
        for c in (haven, claude, qdrant):
            graph.add_concept(c)
        graph.add_relationship(haven_uses_claude)
        graph.add_relationship(haven_depends_on_qdrant)
        rels = graph.relationships(haven.id)
        assert len(rels) == 2

    def test_relationships_sorted(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        qdrant: Concept,
        haven_uses_claude: Relationship,
        haven_depends_on_qdrant: Relationship,
    ) -> None:
        for c in (haven, claude, qdrant):
            graph.add_concept(c)
        graph.add_relationship(haven_uses_claude)
        graph.add_relationship(haven_depends_on_qdrant)
        rels = graph.relationships(haven.id)
        ids_str = [str(r.id) for r in rels]
        assert ids_str == sorted(ids_str)

    def test_relationships_unknown_id_returns_empty(
        self, graph: ConceptGraph
    ) -> None:
        assert graph.relationships(uuid4()) == []


# ---------------------------------------------------------------------------
# TestAttachmentsForConcept
# ---------------------------------------------------------------------------


class TestAttachmentsForConcept:
    def test_no_attachments_for_concept_without_any(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        assert graph.attachments_for_concept(haven.id) == []

    def test_single_attachment_returned(
        self,
        graph: ConceptGraph,
        haven: Concept,
        attachment_haven: Attachment,
    ) -> None:
        graph.add_concept(haven)
        graph.add_attachment(attachment_haven)
        result = graph.attachments_for_concept(haven.id)
        assert len(result) == 1
        assert result[0].id == attachment_haven.id

    def test_multiple_attachments_returned(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        a1 = Attachment.create(uuid4(), haven.id, relevance=0.8)
        a2 = Attachment.create(uuid4(), haven.id, relevance=0.6)
        graph.add_attachment(a1)
        graph.add_attachment(a2)
        result = graph.attachments_for_concept(haven.id)
        assert len(result) == 2

    def test_attachments_sorted(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        graph.add_concept(haven)
        attachments = [Attachment.create(uuid4(), haven.id) for _ in range(4)]
        for a in attachments:
            graph.add_attachment(a)
        result = graph.attachments_for_concept(haven.id)
        ids_str = [str(a.id) for a in result]
        assert ids_str == sorted(ids_str)

    def test_attachments_isolated_to_concept(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        attachment_haven: Attachment,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_attachment(attachment_haven)
        assert graph.attachments_for_concept(claude.id) == []

    def test_attachments_unknown_concept_returns_empty(
        self, graph: ConceptGraph
    ) -> None:
        assert graph.attachments_for_concept(uuid4()) == []


# ---------------------------------------------------------------------------
# TestConceptsForKO
# ---------------------------------------------------------------------------


class TestConceptsForKO:
    def test_no_concepts_for_unknown_ko(
        self, graph: ConceptGraph, ko_id: UUID
    ) -> None:
        assert graph.concepts_for_knowledge_object(ko_id) == []

    def test_single_concept_returned(
        self,
        graph: ConceptGraph,
        haven: Concept,
        ko_id: UUID,
    ) -> None:
        graph.add_concept(haven)
        graph.add_attachment(Attachment.create(ko_id, haven.id))
        result = graph.concepts_for_knowledge_object(ko_id)
        assert len(result) == 1
        assert result[0].id == haven.id

    def test_multiple_concepts_for_same_ko(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        ko_id: UUID,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_attachment(Attachment.create(ko_id, haven.id))
        graph.add_attachment(Attachment.create(ko_id, claude.id))
        result = graph.concepts_for_knowledge_object(ko_id)
        result_ids = {c.id for c in result}
        assert haven.id in result_ids
        assert claude.id in result_ids

    def test_concepts_sorted(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        qdrant: Concept,
        ko_id: UUID,
    ) -> None:
        for c in (haven, claude, qdrant):
            graph.add_concept(c)
            graph.add_attachment(Attachment.create(ko_id, c.id))
        result = graph.concepts_for_knowledge_object(ko_id)
        ids_str = [str(c.id) for c in result]
        assert ids_str == sorted(ids_str)

    def test_concepts_isolated_to_ko(
        self,
        graph: ConceptGraph,
        haven: Concept,
        ko_id: UUID,
    ) -> None:
        graph.add_concept(haven)
        graph.add_attachment(Attachment.create(ko_id, haven.id))
        other_ko = uuid4()
        assert graph.concepts_for_knowledge_object(other_ko) == []

    def test_different_kos_independent(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        ko_id: UUID,
    ) -> None:
        other_ko = uuid4()
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_attachment(Attachment.create(ko_id, haven.id))
        graph.add_attachment(Attachment.create(other_ko, claude.id))
        assert graph.concepts_for_knowledge_object(ko_id)[0].id == haven.id
        assert graph.concepts_for_knowledge_object(other_ko)[0].id == claude.id


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_children_order_independent_of_insertion(
        self,
        haven: Concept,
        claude: Concept,
        qdrant: Concept,
        haven_uses_claude: Relationship,
        haven_depends_on_qdrant: Relationship,
    ) -> None:
        g1 = ConceptGraph()
        g2 = ConceptGraph()
        for c in (haven, claude, qdrant):
            g1.add_concept(c)
        g1.add_relationship(haven_uses_claude)
        g1.add_relationship(haven_depends_on_qdrant)

        for c in (qdrant, claude, haven):  # reversed insertion order
            g2.add_concept(c)
        g2.add_relationship(haven_depends_on_qdrant)
        g2.add_relationship(haven_uses_claude)

        assert [c.id for c in g1.children(haven.id)] == [
            c.id for c in g2.children(haven.id)
        ]

    def test_relationships_order_independent(
        self,
        haven: Concept,
        claude: Concept,
        qdrant: Concept,
        haven_uses_claude: Relationship,
        haven_depends_on_qdrant: Relationship,
    ) -> None:
        g1 = ConceptGraph()
        g2 = ConceptGraph()
        for c in (haven, claude, qdrant):
            g1.add_concept(c)
            g2.add_concept(c)
        g1.add_relationship(haven_uses_claude)
        g1.add_relationship(haven_depends_on_qdrant)
        g2.add_relationship(haven_depends_on_qdrant)
        g2.add_relationship(haven_uses_claude)

        assert [r.id for r in g1.relationships(haven.id)] == [
            r.id for r in g2.relationships(haven.id)
        ]

    def test_attachments_order_independent(
        self, haven: Concept
    ) -> None:
        ko1, ko2 = uuid4(), uuid4()
        a1 = Attachment.create(ko1, haven.id)
        a2 = Attachment.create(ko2, haven.id)

        g1 = ConceptGraph()
        g1.add_concept(haven)
        g1.add_attachment(a1)
        g1.add_attachment(a2)

        g2 = ConceptGraph()
        g2.add_concept(haven)
        g2.add_attachment(a2)
        g2.add_attachment(a1)

        assert [a.id for a in g1.attachments_for_concept(haven.id)] == [
            a.id for a in g2.attachments_for_concept(haven.id)
        ]

    def test_concepts_for_ko_order_independent(
        self, haven: Concept, claude: Concept, qdrant: Concept
    ) -> None:
        ko = uuid4()
        g1 = ConceptGraph()
        g2 = ConceptGraph()
        for c in (haven, claude, qdrant):
            g1.add_concept(c)
            g1.add_attachment(Attachment.create(ko, c.id))
        for c in (qdrant, haven, claude):
            g2.add_concept(c)
            g2.add_attachment(Attachment.create(ko, c.id))

        assert [c.id for c in g1.concepts_for_knowledge_object(ko)] == [
            c.id for c in g2.concepts_for_knowledge_object(ko)
        ]


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_concept_with_no_edges_isolated(
        self, graph: ConceptGraph, haven: Concept, claude: Concept
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        assert graph.children(haven.id) == []
        assert graph.parents(haven.id) == []

    def test_single_edge_graph(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        graph.add_relationship(haven_uses_claude)
        assert len(graph.children(haven.id)) == 1
        assert len(graph.parents(claude.id)) == 1
        assert len(graph.children(claude.id)) == 0
        assert len(graph.parents(haven.id)) == 0

    def test_attachment_relevance_preserved(
        self, graph: ConceptGraph, haven: Concept
    ) -> None:
        ko = uuid4()
        graph.add_concept(haven)
        a = Attachment.create(ko, haven.id, relevance=0.42)
        graph.add_attachment(a)
        result = graph.attachments_for_concept(haven.id)
        assert result[0].relevance == pytest.approx(0.42)

    def test_relationship_confidence_preserved(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        rel = Relationship.create(
            haven.id, claude.id, OntologyRelationshipType.USES, confidence=0.75
        )
        graph.add_relationship(rel)
        result = graph.relationships(haven.id)
        assert result[0].confidence == pytest.approx(0.75)

    def test_concept_aliases_preserved_in_get(
        self, graph: ConceptGraph
    ) -> None:
        c = Concept.from_label("Claude", aliases=("Claude AI", "Anthropic Claude"))
        graph.add_concept(c)
        retrieved = graph.get_concept(c.id)
        assert "Claude AI" in retrieved.aliases

    def test_relationship_type_preserved(
        self,
        graph: ConceptGraph,
        haven: Concept,
        claude: Concept,
    ) -> None:
        graph.add_concept(haven)
        graph.add_concept(claude)
        rel = Relationship.create(
            haven.id, claude.id, OntologyRelationshipType.CREATED_BY
        )
        graph.add_relationship(rel)
        result = graph.relationships(haven.id)
        assert result[0].relationship_type == OntologyRelationshipType.CREATED_BY
