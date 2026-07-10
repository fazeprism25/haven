"""Unit tests for OntologyManager.

Coverage targets
----------------
* Empty / whitespace-only canonical_fact → no proposals.
* Single new concept → CREATE_CONCEPT + ATTACH_KNOWLEDGE_OBJECT.
* Single concept already in graph → ATTACH_KNOWLEDGE_OBJECT only.
* Single concept already in graph and already attached → no proposals.
* Two new concepts → CREATE_CONCEPT ×2 + CREATE_RELATIONSHIP + ATTACH ×2.
* Two existing unrelated concepts → CREATE_RELATIONSHIP + ATTACH ×2.
* Two existing already-related concepts → ATTACH ×2 only.
* Two existing concepts, one already attached → CREATE_RELATIONSHIP + ATTACH ×1.
* Three concepts → CREATE_CONCEPT ×3 + CREATE_RELATIONSHIP ×3 + ATTACH ×3.
* Proposal ordering: concepts first, relationships second, attachments last.
* Payload structure for each proposal type is well-formed.
* Confidence propagated to relationship payload; importance to attachment relevance.
* No graph mutation during propose().
* Determinism: identical inputs produce identical outputs.
* Stable relationship direction (smaller UUID is always source).
"""

from __future__ import annotations

from uuid import UUID

import pytest

from obsidian.manager_ai.models import KnowledgeObject
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.enums import OntologyRelationshipType, ProposalType
from obsidian.ontology.identity import concept_id as _concept_id, relationship_id as _relationship_id
from obsidian.ontology.models import Attachment, Concept, OntologyProposal, Relationship
from obsidian.ontology.ontology_manager import OntologyManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_knowledge(
    fact: str,
    confidence: float = 0.8,
    importance: float = 0.7,
) -> KnowledgeObject:
    return KnowledgeObject(
        canonical_fact=fact,
        confidence=confidence,
        importance=importance,
    )


def proposal_types(proposals: list[OntologyProposal]) -> list[str]:
    return [p.proposal_type.value for p in proposals]


def populate_graph_with_concept(graph: ConceptGraph, label: str) -> Concept:
    """Add a concept to the graph and return it."""
    c = Concept.from_label(label)
    graph.add_concept(c)
    return c


def count_graph_concepts(graph: ConceptGraph) -> int:
    """Count concepts stored in the graph (via has_concept probes)."""
    # We test a few expected UUIDs; for immutability tests we rely on
    # before/after comparison with specific labels.
    return sum(1 for _ in graph._concepts)  # noqa: SLF001 — test-only introspection


def count_graph_relationships(graph: ConceptGraph) -> int:
    return len(graph._relationships)  # noqa: SLF001


def count_graph_attachments(graph: ConceptGraph) -> int:
    return len(graph._attachments)  # noqa: SLF001


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def manager() -> OntologyManager:
    return OntologyManager()


@pytest.fixture()
def empty_graph() -> ConceptGraph:
    return ConceptGraph()


# ---------------------------------------------------------------------------
# Edge cases — no output
# ---------------------------------------------------------------------------


class TestNoConcepts:
    def test_empty_fact_returns_no_proposals(self, manager, empty_graph):
        ko = make_knowledge("")
        assert manager.propose(ko, empty_graph) == []

    def test_whitespace_fact_returns_no_proposals(self, manager, empty_graph):
        ko = make_knowledge("   \t\n  ")
        assert manager.propose(ko, empty_graph) == []

    def test_lowercase_only_fact_returns_no_proposals(self, manager, empty_graph):
        ko = make_knowledge("no concepts here at all")
        assert manager.propose(ko, empty_graph) == []


# ---------------------------------------------------------------------------
# Single concept — new
# ---------------------------------------------------------------------------


class TestSingleNewConcept:
    FACT = "Haven is a project"

    def test_two_proposals_returned(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        proposals = manager.propose(ko, empty_graph)
        # "Haven" is new → 1×CREATE_CONCEPT + 1×ATTACH_KNOWLEDGE_OBJECT
        assert len(proposals) == 2

    def test_proposal_types_in_order(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        types = proposal_types(manager.propose(ko, empty_graph))
        # CREATE_CONCEPT first, ATTACH second
        assert types == [
            ProposalType.CREATE_CONCEPT.value,
            ProposalType.ATTACH_KNOWLEDGE_OBJECT.value,
        ]

    def test_create_concept_payload(self, manager, empty_graph):
        ko = make_knowledge("Haven is a project")
        proposals = manager.propose(ko, empty_graph)
        create = proposals[0]
        assert create.proposal_type == ProposalType.CREATE_CONCEPT
        assert create.payload["label"] == "Haven"
        assert isinstance(create.payload["aliases"], list)
        assert isinstance(create.payload["description"], str)

    def test_attach_payload_structure(self, manager, empty_graph):
        ko = make_knowledge("Haven is a project")
        proposals = manager.propose(ko, empty_graph)
        attach = proposals[1]  # only one concept → index 1
        assert attach.proposal_type == ProposalType.ATTACH_KNOWLEDGE_OBJECT
        assert UUID(attach.payload["knowledge_object_id"]) == ko.id
        assert UUID(attach.payload["concept_id"]) == _concept_id("Haven")
        assert attach.payload["relevance"] == ko.importance

    def test_reason_is_non_empty(self, manager, empty_graph):
        ko = make_knowledge("Haven is a project")
        for p in manager.propose(ko, empty_graph):
            assert p.reason != ""


# ---------------------------------------------------------------------------
# Single concept — already in graph
# ---------------------------------------------------------------------------


class TestSingleExistingConcept:
    FACT = "Haven is a second-brain application"

    def test_only_attach_proposed(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        populate_graph_with_concept(empty_graph, "Haven")
        proposals = manager.propose(ko, empty_graph)
        types = proposal_types(proposals)
        assert ProposalType.CREATE_CONCEPT.value not in types
        assert ProposalType.ATTACH_KNOWLEDGE_OBJECT.value in types

    def test_one_attach_proposal(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        populate_graph_with_concept(empty_graph, "Haven")
        proposals = manager.propose(ko, empty_graph)
        assert len([p for p in proposals if p.proposal_type == ProposalType.ATTACH_KNOWLEDGE_OBJECT]) == 1


# ---------------------------------------------------------------------------
# Single concept — already attached
# ---------------------------------------------------------------------------


class TestSingleConceptAlreadyAttached:
    FACT = "Haven is a second-brain application"

    def test_no_proposals_when_already_attached(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        haven = populate_graph_with_concept(empty_graph, "Haven")
        attachment = Attachment.create(ko.id, haven.id, relevance=0.9)
        empty_graph.add_attachment(attachment)

        proposals = manager.propose(ko, empty_graph)
        assert proposals == []


# ---------------------------------------------------------------------------
# Two concepts — both new
# ---------------------------------------------------------------------------


class TestTwoNewConcepts:
    FACT = "Haven uses Claude for summarisation"

    def test_five_proposals_total(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        proposals = manager.propose(ko, empty_graph)
        # 2×CREATE_CONCEPT + 1×CREATE_RELATIONSHIP + 2×ATTACH
        assert len(proposals) == 5

    def test_ordering(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        proposals = manager.propose(ko, empty_graph)
        types = proposal_types(proposals)
        create_idx = [i for i, t in enumerate(types) if t == ProposalType.CREATE_CONCEPT.value]
        rel_idx = [i for i, t in enumerate(types) if t == ProposalType.CREATE_RELATIONSHIP.value]
        attach_idx = [i for i, t in enumerate(types) if t == ProposalType.ATTACH_KNOWLEDGE_OBJECT.value]

        assert len(create_idx) == 2
        assert len(rel_idx) == 1
        assert len(attach_idx) == 2
        assert max(create_idx) < min(rel_idx)
        assert max(rel_idx) < min(attach_idx)

    def test_relationship_payload(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        proposals = manager.propose(ko, empty_graph)
        rel = next(p for p in proposals if p.proposal_type == ProposalType.CREATE_RELATIONSHIP)
        assert rel.payload["relationship_type"] == OntologyRelationshipType.RELATED_TO.value
        assert rel.payload["confidence"] == ko.confidence
        assert UUID(rel.payload["source_id"]) != UUID(rel.payload["target_id"])


# ---------------------------------------------------------------------------
# Two concepts — both existing, unrelated
# ---------------------------------------------------------------------------


class TestTwoExistingUnrelatedConcepts:
    FACT = "Haven uses Claude for summarisation"

    def test_relationship_proposed_but_not_concepts(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        populate_graph_with_concept(empty_graph, "Haven")
        populate_graph_with_concept(empty_graph, "Claude")

        proposals = manager.propose(ko, empty_graph)
        types = proposal_types(proposals)

        assert ProposalType.CREATE_CONCEPT.value not in types
        assert ProposalType.CREATE_RELATIONSHIP.value in types
        assert types.count(ProposalType.ATTACH_KNOWLEDGE_OBJECT.value) == 2


# ---------------------------------------------------------------------------
# Two concepts — both existing, already related
# ---------------------------------------------------------------------------


class TestTwoExistingRelatedConcepts:
    FACT = "Haven uses Claude for summarisation"

    def test_only_attachments_proposed(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        haven = populate_graph_with_concept(empty_graph, "Haven")
        claude = populate_graph_with_concept(empty_graph, "Claude")

        # Pre-populate the RELATED_TO edge.
        haven_id = _concept_id("Haven")
        claude_id = _concept_id("Claude")
        if str(haven_id) <= str(claude_id):
            src, tgt = haven_id, claude_id
        else:
            src, tgt = claude_id, haven_id
        rel = Relationship.create(src, tgt, OntologyRelationshipType.RELATED_TO)
        empty_graph.add_relationship(rel)

        proposals = manager.propose(ko, empty_graph)
        types = proposal_types(proposals)

        assert ProposalType.CREATE_CONCEPT.value not in types
        assert ProposalType.CREATE_RELATIONSHIP.value not in types
        assert types.count(ProposalType.ATTACH_KNOWLEDGE_OBJECT.value) == 2


# ---------------------------------------------------------------------------
# Two existing concepts — one already attached
# ---------------------------------------------------------------------------


class TestPartiallyAttached:
    FACT = "Haven uses Claude for summarisation"

    def test_only_missing_attachment_proposed(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        haven = populate_graph_with_concept(empty_graph, "Haven")
        populate_graph_with_concept(empty_graph, "Claude")

        # Pre-attach KO to Haven only.
        att = Attachment.create(ko.id, haven.id)
        empty_graph.add_attachment(att)

        proposals = manager.propose(ko, empty_graph)
        types = proposal_types(proposals)

        attach_proposals = [p for p in proposals if p.proposal_type == ProposalType.ATTACH_KNOWLEDGE_OBJECT]
        assert len(attach_proposals) == 1
        assert UUID(attach_proposals[0].payload["concept_id"]) == _concept_id("Claude")


# ---------------------------------------------------------------------------
# Three concepts
# ---------------------------------------------------------------------------


class TestThreeConcepts:
    FACT = "Siddhartha built Haven using Claude"

    def test_counts(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        proposals = manager.propose(ko, empty_graph)
        types = proposal_types(proposals)
        # 3×CREATE_CONCEPT + 3×CREATE_RELATIONSHIP (pairs: 0-1, 0-2, 1-2) + 3×ATTACH
        assert types.count(ProposalType.CREATE_CONCEPT.value) == 3
        assert types.count(ProposalType.CREATE_RELATIONSHIP.value) == 3
        assert types.count(ProposalType.ATTACH_KNOWLEDGE_OBJECT.value) == 3

    def test_ordering_holds_for_three(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        proposals = manager.propose(ko, empty_graph)
        types = proposal_types(proposals)

        last_concept = max(i for i, t in enumerate(types) if t == ProposalType.CREATE_CONCEPT.value)
        first_rel = min(i for i, t in enumerate(types) if t == ProposalType.CREATE_RELATIONSHIP.value)
        last_rel = max(i for i, t in enumerate(types) if t == ProposalType.CREATE_RELATIONSHIP.value)
        first_attach = min(i for i, t in enumerate(types) if t == ProposalType.ATTACH_KNOWLEDGE_OBJECT.value)

        assert last_concept < first_rel
        assert last_rel < first_attach


# ---------------------------------------------------------------------------
# Payload correctness
# ---------------------------------------------------------------------------


class TestPayloadCorrectness:
    def test_relationship_confidence_matches_ko(self, manager, empty_graph):
        ko = make_knowledge("Haven uses Claude", confidence=0.9, importance=0.5)
        proposals = manager.propose(ko, empty_graph)
        rel = next(p for p in proposals if p.proposal_type == ProposalType.CREATE_RELATIONSHIP)
        assert rel.payload["confidence"] == 0.9

    def test_attachment_relevance_matches_importance(self, manager, empty_graph):
        ko = make_knowledge("Haven is a project", confidence=0.6, importance=0.4)
        proposals = manager.propose(ko, empty_graph)
        attach = next(p for p in proposals if p.proposal_type == ProposalType.ATTACH_KNOWLEDGE_OBJECT)
        assert attach.payload["relevance"] == 0.4

    def test_create_concept_label_preserved(self, manager, empty_graph):
        ko = make_knowledge("DTU is a university")
        proposals = manager.propose(ko, empty_graph)
        create = next(p for p in proposals if p.proposal_type == ProposalType.CREATE_CONCEPT)
        assert create.payload["label"] == "DTU"

    def test_attach_ko_id_matches(self, manager, empty_graph):
        ko = make_knowledge("Haven is a project")
        proposals = manager.propose(ko, empty_graph)
        attach = next(p for p in proposals if p.proposal_type == ProposalType.ATTACH_KNOWLEDGE_OBJECT)
        assert UUID(attach.payload["knowledge_object_id"]) == ko.id


# ---------------------------------------------------------------------------
# Relationship direction stability
# ---------------------------------------------------------------------------


class TestRelationshipDirection:
    def test_direction_is_stable_across_calls(self, manager, empty_graph):
        ko = make_knowledge("Haven uses Claude")
        p1 = manager.propose(ko, empty_graph)
        p2 = manager.propose(ko, empty_graph)

        rel1 = next(p for p in p1 if p.proposal_type == ProposalType.CREATE_RELATIONSHIP)
        rel2 = next(p for p in p2 if p.proposal_type == ProposalType.CREATE_RELATIONSHIP)

        assert rel1.payload["source_id"] == rel2.payload["source_id"]
        assert rel1.payload["target_id"] == rel2.payload["target_id"]

    def test_source_uuid_lexicographically_smaller(self, manager, empty_graph):
        ko = make_knowledge("Haven uses Claude")
        proposals = manager.propose(ko, empty_graph)
        rel = next(p for p in proposals if p.proposal_type == ProposalType.CREATE_RELATIONSHIP)
        assert rel.payload["source_id"] <= rel.payload["target_id"]


# ---------------------------------------------------------------------------
# Immutability — graph must not be mutated
# ---------------------------------------------------------------------------


class TestNoMutation:
    def test_graph_concepts_unchanged(self, manager, empty_graph):
        ko = make_knowledge("Haven uses Claude")
        before_concepts = count_graph_concepts(empty_graph)
        manager.propose(ko, empty_graph)
        assert count_graph_concepts(empty_graph) == before_concepts

    def test_graph_relationships_unchanged(self, manager):
        graph = ConceptGraph()
        populate_graph_with_concept(graph, "Haven")
        populate_graph_with_concept(graph, "Claude")
        ko = make_knowledge("Haven uses Claude")
        before = count_graph_relationships(graph)
        manager.propose(ko, graph)
        assert count_graph_relationships(graph) == before

    def test_graph_attachments_unchanged(self, manager):
        graph = ConceptGraph()
        populate_graph_with_concept(graph, "Haven")
        ko = make_knowledge("Haven is a project")
        before = count_graph_attachments(graph)
        manager.propose(ko, graph)
        assert count_graph_attachments(graph) == before


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    FACT = "Haven uses Claude and Qdrant"

    def test_identical_outputs_on_repeated_calls(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        result_a = [p.to_dict() for p in manager.propose(ko, empty_graph)]
        result_b = [p.to_dict() for p in manager.propose(ko, empty_graph)]
        assert result_a == result_b

    def test_same_labels_same_concept_ids(self, manager, empty_graph):
        ko1 = make_knowledge("Haven is a project")
        ko2 = make_knowledge("Haven is a project")
        p1 = manager.propose(ko1, empty_graph)
        p2 = manager.propose(ko2, empty_graph)

        ids_1 = {p.payload.get("label") for p in p1 if p.proposal_type == ProposalType.CREATE_CONCEPT}
        ids_2 = {p.payload.get("label") for p in p2 if p.proposal_type == ProposalType.CREATE_CONCEPT}
        assert ids_1 == ids_2


# ---------------------------------------------------------------------------
# Deduplication — ConceptDetector already deduplicates; manager must not
# re-introduce duplicates via its own logic.
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_no_duplicate_attach_proposals(self, manager, empty_graph):
        ko = make_knowledge("Haven is the core of Haven")
        proposals = manager.propose(ko, empty_graph)
        attach_concept_ids = [
            p.payload["concept_id"]
            for p in proposals
            if p.proposal_type == ProposalType.ATTACH_KNOWLEDGE_OBJECT
        ]
        assert len(attach_concept_ids) == len(set(attach_concept_ids))

    def test_no_duplicate_create_concept_proposals(self, manager, empty_graph):
        ko = make_knowledge("Haven is the core of Haven")
        proposals = manager.propose(ko, empty_graph)
        create_labels = [
            p.payload["label"]
            for p in proposals
            if p.proposal_type == ProposalType.CREATE_CONCEPT
        ]
        assert len(create_labels) == len(set(create_labels))


# ---------------------------------------------------------------------------
# Curated ENTITY_CAT -> CATEGORY IS_A bridge (Phase 2)
#
# See docs/architecture/ENTITY_CAT_INVESTIGATION.md Task 5 and
# obsidian/ontology/category_taxonomy.py for scope. A detected label
# present in the curated taxonomy gets a category Concept (created once)
# plus an IS_A relationship from instance to category, additive to the
# existing RELATED_TO co-occurrence linking above.
# ---------------------------------------------------------------------------


class TestCuratedInstanceGetsCategoryAndIsA:
    FACT = "I migrated everything to PostgreSQL."

    def test_category_concept_proposed(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        proposals = manager.propose(ko, empty_graph)
        create_labels = [
            p.payload["label"] for p in proposals if p.proposal_type == ProposalType.CREATE_CONCEPT
        ]
        assert "PostgreSQL" in create_labels
        assert "Database" in create_labels

    def test_is_a_relationship_proposed_instance_to_category(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        proposals = manager.propose(ko, empty_graph)
        is_a = [
            p
            for p in proposals
            if p.proposal_type == ProposalType.CREATE_RELATIONSHIP
            and p.payload["relationship_type"] == OntologyRelationshipType.IS_A.value
        ]
        assert len(is_a) == 1
        assert UUID(is_a[0].payload["source_id"]) == _concept_id("PostgreSQL")
        assert UUID(is_a[0].payload["target_id"]) == _concept_id("Database")

    def test_is_a_confidence_is_full_curated_confidence(self, manager, empty_graph):
        ko = make_knowledge(self.FACT, confidence=0.4)
        proposals = manager.propose(ko, empty_graph)
        is_a = next(
            p
            for p in proposals
            if p.proposal_type == ProposalType.CREATE_RELATIONSHIP
            and p.payload["relationship_type"] == OntologyRelationshipType.IS_A.value
        )
        # Curated, not extraction-confidence-dependent — unlike RELATED_TO,
        # which propagates knowledge.confidence.
        assert is_a.payload["confidence"] == 1.0

    def test_category_concept_ordered_before_is_a_relationship(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        proposals = manager.propose(ko, empty_graph)
        types = proposal_types(proposals)
        database_create_idx = next(
            i
            for i, p in enumerate(proposals)
            if p.proposal_type == ProposalType.CREATE_CONCEPT and p.payload["label"] == "Database"
        )
        is_a_idx = next(
            i
            for i, p in enumerate(proposals)
            if p.proposal_type == ProposalType.CREATE_RELATIONSHIP
            and p.payload["relationship_type"] == OntologyRelationshipType.IS_A.value
        )
        assert database_create_idx < is_a_idx

    def test_non_curated_label_gets_no_is_a(self, manager, empty_graph):
        ko = make_knowledge("I use MongoDB for my database.")
        proposals = manager.propose(ko, empty_graph)
        is_a = [
            p
            for p in proposals
            if p.proposal_type == ProposalType.CREATE_RELATIONSHIP
            and p.payload["relationship_type"] == OntologyRelationshipType.IS_A.value
        ]
        assert is_a == []


class TestCuratedCategorySharedAcrossInstances:
    # GPT and Qwen both curate to the "Model" category — the category
    # Concept must only be proposed once even though two instances in the
    # same KnowledgeObject reference it.
    FACT = "I replaced that plan with GPT for planning and Qwen for coding."

    def test_model_category_proposed_exactly_once(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        proposals = manager.propose(ko, empty_graph)
        model_creates = [
            p
            for p in proposals
            if p.proposal_type == ProposalType.CREATE_CONCEPT and p.payload["label"] == "Model"
        ]
        assert len(model_creates) == 1

    def test_two_is_a_relationships_to_the_same_category(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        proposals = manager.propose(ko, empty_graph)
        is_a = [
            p
            for p in proposals
            if p.proposal_type == ProposalType.CREATE_RELATIONSHIP
            and p.payload["relationship_type"] == OntologyRelationshipType.IS_A.value
        ]
        assert len(is_a) == 2
        sources = {UUID(p.payload["source_id"]) for p in is_a}
        targets = {UUID(p.payload["target_id"]) for p in is_a}
        assert sources == {_concept_id("GPT"), _concept_id("Qwen")}
        assert targets == {_concept_id("Model")}


class TestCuratedCategoryIdempotency:
    FACT = "I migrated everything to PostgreSQL."

    def test_category_and_is_a_not_reproposed_once_in_graph(self, manager, empty_graph):
        ko = make_knowledge(self.FACT)
        first = manager.propose(ko, empty_graph)

        # Apply the accepted proposals to a real graph, mirroring what
        # OntologyPipeline._apply_to_graph does, then propose again.
        postgres = Concept.from_label("PostgreSQL")
        database = Concept.from_label("Database")
        empty_graph.add_concept(postgres)
        empty_graph.add_concept(database)
        is_a_rel = Relationship.create(postgres.id, database.id, OntologyRelationshipType.IS_A, confidence=1.0)
        empty_graph.add_relationship(is_a_rel)
        att = Attachment.create(ko.id, postgres.id, relevance=ko.importance)
        empty_graph.add_attachment(att)

        second = manager.propose(ko, empty_graph)
        assert second == []

    def test_category_reused_for_second_curated_instance(self, manager, empty_graph):
        # Linear and Jira both curate to "Tracking Tool"; once the category
        # exists (from a prior knowledge object about Jira), a later
        # knowledge object about Linear must reuse it, not recreate it.
        ko1 = make_knowledge("We decided to use Jira for tracking.")
        proposals1 = manager.propose(ko1, empty_graph)
        for p in proposals1:
            if p.proposal_type == ProposalType.CREATE_CONCEPT:
                empty_graph.add_concept(Concept.from_label(p.payload["label"], tuple(p.payload["aliases"])))
        for p in proposals1:
            if p.proposal_type == ProposalType.CREATE_RELATIONSHIP:
                empty_graph.add_relationship(
                    Relationship.create(
                        UUID(p.payload["source_id"]),
                        UUID(p.payload["target_id"]),
                        OntologyRelationshipType(p.payload["relationship_type"]),
                        confidence=p.payload["confidence"],
                    )
                )
        for p in proposals1:
            if p.proposal_type == ProposalType.ATTACH_KNOWLEDGE_OBJECT:
                empty_graph.add_attachment(
                    Attachment.create(
                        UUID(p.payload["knowledge_object_id"]),
                        UUID(p.payload["concept_id"]),
                        relevance=p.payload["relevance"],
                    )
                )

        ko2 = make_knowledge("we moved everything to Linear because Jira got too slow for us.")
        proposals2 = manager.propose(ko2, empty_graph)
        tracking_tool_creates = [
            p
            for p in proposals2
            if p.proposal_type == ProposalType.CREATE_CONCEPT and p.payload["label"] == "Tracking Tool"
        ]
        assert tracking_tool_creates == []
        is_a2 = [
            p
            for p in proposals2
            if p.proposal_type == ProposalType.CREATE_RELATIONSHIP
            and p.payload["relationship_type"] == OntologyRelationshipType.IS_A.value
        ]
        assert len(is_a2) == 1
        assert UUID(is_a2[0].payload["source_id"]) == _concept_id("Linear")
        assert UUID(is_a2[0].payload["target_id"]) == _concept_id("Tracking Tool")


class TestCuratedCategoryValidatorRoundTrip:
    def test_all_curated_proposals_accepted_by_validator(self, manager, empty_graph):
        from obsidian.ontology.ontology_validator import OntologyValidator

        ko = make_knowledge("I replaced that plan with GPT for planning and Qwen for coding.")
        proposals = manager.propose(ko, empty_graph)
        results = OntologyValidator().validate(proposals, empty_graph)
        rejected = [r for r in results if not r.accepted]
        assert rejected == [], [r.rejection_reason for r in rejected]
