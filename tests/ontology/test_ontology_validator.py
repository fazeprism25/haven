"""Unit tests for OntologyValidator.

Coverage targets
----------------
Structural
  * Empty proposal list → empty results.
  * Every input proposal appears exactly once in output (same order).
  * Accepted ↔ rejection_reason invariant (non-empty iff rejected).

CREATE_CONCEPT
  * Valid new concept → accepted.
  * Already in graph → rejected (duplicate).
  * Already accepted in this batch → rejected (intra-batch duplicate).
  * Missing 'label' key → rejected.
  * Empty label string → rejected.
  * Whitespace-only label → rejected.
  * Non-str label → rejected.
  * Missing 'aliases' key → rejected.
  * Non-list aliases → rejected.
  * Non-str element inside aliases → rejected.
  * Duplicate aliases → rejected.
  * Missing 'description' key → rejected.
  * Non-str description → rejected.

CREATE_RELATIONSHIP
  * Valid relationship, both endpoints in graph → accepted.
  * Source in accepted batch, target in graph → accepted.
  * Both endpoints in accepted batch → accepted.
  * Source absent → rejected.
  * Target absent → rejected.
  * Already in graph → rejected.
  * Already in batch → rejected.
  * Self-loop (source_id == target_id) → rejected.
  * Invalid source UUID string → rejected.
  * Invalid target UUID string → rejected.
  * Non-str source_id → rejected.
  * Invalid relationship_type value → rejected.
  * Missing 'confidence' key → rejected.
  * confidence < 0 → rejected.
  * confidence > 1 → rejected.
  * Missing 'source_id' → rejected.
  * Missing 'target_id' → rejected.
  * Missing 'relationship_type' → rejected.
  * Non-numeric confidence → rejected.

ATTACH_KNOWLEDGE_OBJECT
  * Valid attachment, concept in graph → accepted.
  * Concept in accepted batch → accepted.
  * Concept absent from graph and batch → rejected.
  * Already in graph → rejected.
  * Already in batch → rejected.
  * Invalid knowledge_object_id UUID → rejected.
  * Invalid concept_id UUID → rejected.
  * Non-str knowledge_object_id → rejected.
  * relevance < 0 → rejected.
  * relevance > 1 → rejected.
  * Non-numeric relevance → rejected.
  * Missing 'knowledge_object_id' → rejected.
  * Missing 'concept_id' → rejected.
  * Missing 'relevance' → rejected.

Order-dependency (batch sequencing)
  * Relationship before its CREATE_CONCEPT → endpoint missing → rejected.
  * Relationship after its CREATE_CONCEPT (same batch) → accepted.
  * Attach before CREATE_CONCEPT → concept missing → rejected.
  * Attach after CREATE_CONCEPT (same batch) → accepted.
  * Rejected CREATE_CONCEPT does NOT make concept available for later proposals.

Determinism
  * Identical inputs always produce identical outputs.

No mutation
  * Graph unchanged after validate().
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.enums import OntologyRelationshipType, ProposalType
from obsidian.ontology.identity import (
    attachment_id as _aid,
    concept_id as _cid,
    relationship_id as _rid,
)
from obsidian.ontology.models import Attachment, Concept, OntologyProposal, Relationship
from obsidian.ontology.ontology_validator import OntologyValidator, ValidationResult


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def concept_proposal(label: str, aliases=None, description="") -> OntologyProposal:
    return OntologyProposal(
        proposal_type=ProposalType.CREATE_CONCEPT,
        payload={
            "label": label,
            "aliases": aliases if aliases is not None else [],
            "description": description,
        },
        reason=f"test concept {label}",
    )


def relationship_proposal(
    source_id: UUID,
    target_id: UUID,
    rel_type: OntologyRelationshipType = OntologyRelationshipType.RELATED_TO,
    confidence: float = 0.8,
) -> OntologyProposal:
    return OntologyProposal(
        proposal_type=ProposalType.CREATE_RELATIONSHIP,
        payload={
            "source_id": str(source_id),
            "target_id": str(target_id),
            "relationship_type": rel_type.value,
            "confidence": confidence,
        },
        reason="test relationship",
    )


def attach_proposal(ko_id: UUID, concept_id: UUID, relevance: float = 0.9) -> OntologyProposal:
    return OntologyProposal(
        proposal_type=ProposalType.ATTACH_KNOWLEDGE_OBJECT,
        payload={
            "knowledge_object_id": str(ko_id),
            "concept_id": str(concept_id),
            "relevance": relevance,
        },
        reason="test attachment",
    )


def raw_proposal(proposal_type: ProposalType, payload: dict) -> OntologyProposal:
    return OntologyProposal(proposal_type=proposal_type, payload=payload, reason="raw")


def add_concept(graph: ConceptGraph, label: str) -> Concept:
    c = Concept.from_label(label)
    graph.add_concept(c)
    return c


def add_relationship(
    graph: ConceptGraph,
    label_a: str,
    label_b: str,
    rel_type: OntologyRelationshipType = OntologyRelationshipType.RELATED_TO,
) -> Relationship:
    src = _cid(label_a)
    tgt = _cid(label_b)
    if str(src) > str(tgt):
        src, tgt = tgt, src
    r = Relationship.create(src, tgt, rel_type)
    graph.add_relationship(r)
    return r


def add_attachment(graph: ConceptGraph, ko_id: UUID, label: str) -> Attachment:
    cid = _cid(label)
    a = Attachment.create(ko_id, cid)
    graph.add_attachment(a)
    return a


def graph_concept_count(graph: ConceptGraph) -> int:
    return len(graph._concepts)  # noqa: SLF001


def graph_relationship_count(graph: ConceptGraph) -> int:
    return len(graph._relationships)  # noqa: SLF001


def graph_attachment_count(graph: ConceptGraph) -> int:
    return len(graph._attachments)  # noqa: SLF001


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def validator() -> OntologyValidator:
    return OntologyValidator()


@pytest.fixture()
def empty_graph() -> ConceptGraph:
    return ConceptGraph()


@pytest.fixture()
def graph_with_haven() -> ConceptGraph:
    g = ConceptGraph()
    add_concept(g, "Haven")
    return g


@pytest.fixture()
def graph_with_haven_and_claude() -> ConceptGraph:
    g = ConceptGraph()
    add_concept(g, "Haven")
    add_concept(g, "Claude")
    return g


# ---------------------------------------------------------------------------
# Structural tests
# ---------------------------------------------------------------------------


class TestStructural:
    def test_empty_proposals_returns_empty(self, validator, empty_graph):
        assert validator.validate([], empty_graph) == []

    def test_result_count_matches_input_count(self, validator, empty_graph):
        proposals = [concept_proposal("Alpha"), concept_proposal("Beta")]
        results = validator.validate(proposals, empty_graph)
        assert len(results) == len(proposals)

    def test_result_order_matches_input_order(self, validator, empty_graph):
        proposals = [concept_proposal("Alpha"), concept_proposal("Beta")]
        results = validator.validate(proposals, empty_graph)
        for p, r in zip(proposals, results):
            assert r.proposal is p

    def test_accepted_has_empty_rejection_reason(self, validator, empty_graph):
        results = validator.validate([concept_proposal("Haven")], empty_graph)
        assert results[0].accepted is True
        assert results[0].rejection_reason == ""

    def test_rejected_has_non_empty_rejection_reason(self, validator, empty_graph):
        p = raw_proposal(
            ProposalType.CREATE_CONCEPT,
            {"label": "", "aliases": [], "description": ""},
        )
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False
        assert result.rejection_reason != ""


# ---------------------------------------------------------------------------
# CREATE_CONCEPT — happy path
# ---------------------------------------------------------------------------


class TestCreateConceptAccepted:
    def test_new_concept_accepted(self, validator, empty_graph):
        result = validator.validate([concept_proposal("Haven")], empty_graph)[0]
        assert result.accepted is True

    def test_concept_with_aliases_accepted(self, validator, empty_graph):
        p = concept_proposal("Claude", aliases=["Claude AI", "Anthropic Claude"])
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is True

    def test_concept_with_description_accepted(self, validator, empty_graph):
        p = concept_proposal("Haven", description="Personal second-brain")
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is True


# ---------------------------------------------------------------------------
# CREATE_CONCEPT — rejection cases
# ---------------------------------------------------------------------------


class TestCreateConceptRejected:
    def test_already_in_graph(self, validator, graph_with_haven):
        result = validator.validate([concept_proposal("Haven")], graph_with_haven)[0]
        assert result.accepted is False
        assert "already exists" in result.rejection_reason

    def test_duplicate_within_batch(self, validator, empty_graph):
        proposals = [concept_proposal("Haven"), concept_proposal("Haven")]
        results = validator.validate(proposals, empty_graph)
        assert results[0].accepted is True
        assert results[1].accepted is False
        assert "duplicated within this batch" in results[1].rejection_reason

    def test_missing_label_key(self, validator, empty_graph):
        p = raw_proposal(ProposalType.CREATE_CONCEPT, {"aliases": [], "description": ""})
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False
        assert "label" in result.rejection_reason

    def test_empty_label(self, validator, empty_graph):
        p = raw_proposal(ProposalType.CREATE_CONCEPT, {"label": "", "aliases": [], "description": ""})
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False

    def test_whitespace_label(self, validator, empty_graph):
        p = raw_proposal(ProposalType.CREATE_CONCEPT, {"label": "   ", "aliases": [], "description": ""})
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False

    def test_non_str_label(self, validator, empty_graph):
        p = raw_proposal(ProposalType.CREATE_CONCEPT, {"label": 42, "aliases": [], "description": ""})
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False

    def test_missing_aliases_key(self, validator, empty_graph):
        p = raw_proposal(ProposalType.CREATE_CONCEPT, {"label": "Haven", "description": ""})
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False
        assert "aliases" in result.rejection_reason

    def test_aliases_not_list(self, validator, empty_graph):
        p = raw_proposal(ProposalType.CREATE_CONCEPT, {"label": "Haven", "aliases": "bad", "description": ""})
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False

    def test_alias_element_not_str(self, validator, empty_graph):
        p = raw_proposal(ProposalType.CREATE_CONCEPT, {"label": "Haven", "aliases": [123], "description": ""})
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False

    def test_duplicate_aliases_rejected(self, validator, empty_graph):
        p = concept_proposal("Haven", aliases=["H", "H"])
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False
        assert "duplicate" in result.rejection_reason

    def test_duplicate_aliases_case_insensitive(self, validator, empty_graph):
        p = concept_proposal("Haven", aliases=["H", "h"])
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False

    def test_missing_description_key(self, validator, empty_graph):
        p = raw_proposal(ProposalType.CREATE_CONCEPT, {"label": "Haven", "aliases": []})
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False
        assert "description" in result.rejection_reason

    def test_non_str_description(self, validator, empty_graph):
        p = raw_proposal(ProposalType.CREATE_CONCEPT, {"label": "Haven", "aliases": [], "description": 0})
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False


# ---------------------------------------------------------------------------
# CREATE_RELATIONSHIP — happy path
# ---------------------------------------------------------------------------


class TestCreateRelationshipAccepted:
    def test_both_endpoints_in_graph(self, validator, graph_with_haven_and_claude):
        haven_id = _cid("Haven")
        claude_id = _cid("Claude")
        src, tgt = (haven_id, claude_id) if str(haven_id) <= str(claude_id) else (claude_id, haven_id)
        p = relationship_proposal(src, tgt)
        result = validator.validate([p], graph_with_haven_and_claude)[0]
        assert result.accepted is True

    def test_source_in_batch_target_in_graph(self, validator, graph_with_haven):
        proposals = [
            concept_proposal("NewConcept"),
            relationship_proposal(_cid("NewConcept"), _cid("Haven")),
        ]
        results = validator.validate(proposals, graph_with_haven)
        assert results[0].accepted is True
        assert results[1].accepted is True

    def test_both_endpoints_in_batch(self, validator, empty_graph):
        proposals = [
            concept_proposal("Alpha"),
            concept_proposal("Beta"),
            relationship_proposal(_cid("Alpha"), _cid("Beta")),
        ]
        results = validator.validate(proposals, empty_graph)
        assert all(r.accepted for r in results)

    def test_confidence_at_zero(self, validator, graph_with_haven_and_claude):
        haven_id = _cid("Haven")
        claude_id = _cid("Claude")
        src, tgt = (haven_id, claude_id) if str(haven_id) <= str(claude_id) else (claude_id, haven_id)
        p = relationship_proposal(src, tgt, confidence=0.0)
        result = validator.validate([p], graph_with_haven_and_claude)[0]
        assert result.accepted is True

    def test_confidence_at_one(self, validator, graph_with_haven_and_claude):
        haven_id = _cid("Haven")
        claude_id = _cid("Claude")
        src, tgt = (haven_id, claude_id) if str(haven_id) <= str(claude_id) else (claude_id, haven_id)
        p = relationship_proposal(src, tgt, confidence=1.0)
        result = validator.validate([p], graph_with_haven_and_claude)[0]
        assert result.accepted is True


# ---------------------------------------------------------------------------
# CREATE_RELATIONSHIP — rejection cases
# ---------------------------------------------------------------------------


class TestCreateRelationshipRejected:
    def _endpoints(self):
        a, b = _cid("Haven"), _cid("Claude")
        return (a, b) if str(a) <= str(b) else (b, a)

    def test_source_absent(self, validator, empty_graph):
        ghost = uuid4()
        target = add_concept(empty_graph, "Haven")
        p = relationship_proposal(ghost, _cid("Haven"))
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False
        assert "source" in result.rejection_reason

    def test_target_absent(self, validator, empty_graph):
        add_concept(empty_graph, "Haven")
        p = relationship_proposal(_cid("Haven"), uuid4())
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False
        assert "target" in result.rejection_reason

    def test_already_in_graph(self, validator, graph_with_haven_and_claude):
        src, tgt = self._endpoints()
        existing = Relationship.create(src, tgt, OntologyRelationshipType.RELATED_TO)
        graph_with_haven_and_claude.add_relationship(existing)
        p = relationship_proposal(src, tgt)
        result = validator.validate([p], graph_with_haven_and_claude)[0]
        assert result.accepted is False
        assert "already exists" in result.rejection_reason

    def test_duplicate_within_batch(self, validator, empty_graph):
        proposals = [
            concept_proposal("Alpha"),
            concept_proposal("Beta"),
            relationship_proposal(_cid("Alpha"), _cid("Beta")),
            relationship_proposal(_cid("Alpha"), _cid("Beta")),
        ]
        results = validator.validate(proposals, empty_graph)
        assert results[2].accepted is True
        assert results[3].accepted is False
        assert "duplicated within this batch" in results[3].rejection_reason

    def test_self_loop(self, validator, empty_graph):
        add_concept(empty_graph, "Haven")
        cid = _cid("Haven")
        p = relationship_proposal(cid, cid)
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False
        assert "self-loop" in result.rejection_reason

    def test_invalid_source_uuid(self, validator, empty_graph):
        add_concept(empty_graph, "Haven")
        p = raw_proposal(ProposalType.CREATE_RELATIONSHIP, {
            "source_id": "not-a-uuid",
            "target_id": str(_cid("Haven")),
            "relationship_type": "related_to",
            "confidence": 0.5,
        })
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False

    def test_invalid_target_uuid(self, validator, empty_graph):
        add_concept(empty_graph, "Haven")
        p = raw_proposal(ProposalType.CREATE_RELATIONSHIP, {
            "source_id": str(_cid("Haven")),
            "target_id": "bad-uuid",
            "relationship_type": "related_to",
            "confidence": 0.5,
        })
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False

    def test_non_str_source_id(self, validator, empty_graph):
        add_concept(empty_graph, "Haven")
        p = raw_proposal(ProposalType.CREATE_RELATIONSHIP, {
            "source_id": 12345,
            "target_id": str(_cid("Haven")),
            "relationship_type": "related_to",
            "confidence": 0.5,
        })
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False

    def test_invalid_relationship_type(self, validator, graph_with_haven_and_claude):
        src, tgt = self._endpoints()
        p = raw_proposal(ProposalType.CREATE_RELATIONSHIP, {
            "source_id": str(src),
            "target_id": str(tgt),
            "relationship_type": "flies_to",
            "confidence": 0.5,
        })
        result = validator.validate([p], graph_with_haven_and_claude)[0]
        assert result.accepted is False
        assert "relationship_type" in result.rejection_reason

    def test_confidence_below_zero(self, validator, graph_with_haven_and_claude):
        src, tgt = self._endpoints()
        p = relationship_proposal(src, tgt, confidence=-0.1)
        result = validator.validate([p], graph_with_haven_and_claude)[0]
        assert result.accepted is False
        assert "confidence" in result.rejection_reason

    def test_confidence_above_one(self, validator, graph_with_haven_and_claude):
        src, tgt = self._endpoints()
        p = relationship_proposal(src, tgt, confidence=1.1)
        result = validator.validate([p], graph_with_haven_and_claude)[0]
        assert result.accepted is False

    def test_missing_source_id_key(self, validator, graph_with_haven_and_claude):
        src, tgt = self._endpoints()
        p = raw_proposal(ProposalType.CREATE_RELATIONSHIP, {
            "target_id": str(tgt),
            "relationship_type": "related_to",
            "confidence": 0.5,
        })
        result = validator.validate([p], graph_with_haven_and_claude)[0]
        assert result.accepted is False
        assert "source_id" in result.rejection_reason

    def test_missing_target_id_key(self, validator, graph_with_haven_and_claude):
        src, tgt = self._endpoints()
        p = raw_proposal(ProposalType.CREATE_RELATIONSHIP, {
            "source_id": str(src),
            "relationship_type": "related_to",
            "confidence": 0.5,
        })
        result = validator.validate([p], graph_with_haven_and_claude)[0]
        assert result.accepted is False
        assert "target_id" in result.rejection_reason

    def test_missing_relationship_type_key(self, validator, graph_with_haven_and_claude):
        src, tgt = self._endpoints()
        p = raw_proposal(ProposalType.CREATE_RELATIONSHIP, {
            "source_id": str(src),
            "target_id": str(tgt),
            "confidence": 0.5,
        })
        result = validator.validate([p], graph_with_haven_and_claude)[0]
        assert result.accepted is False
        assert "relationship_type" in result.rejection_reason

    def test_missing_confidence_key(self, validator, graph_with_haven_and_claude):
        src, tgt = self._endpoints()
        p = raw_proposal(ProposalType.CREATE_RELATIONSHIP, {
            "source_id": str(src),
            "target_id": str(tgt),
            "relationship_type": "related_to",
        })
        result = validator.validate([p], graph_with_haven_and_claude)[0]
        assert result.accepted is False
        assert "confidence" in result.rejection_reason

    def test_non_numeric_confidence(self, validator, graph_with_haven_and_claude):
        src, tgt = self._endpoints()
        p = raw_proposal(ProposalType.CREATE_RELATIONSHIP, {
            "source_id": str(src),
            "target_id": str(tgt),
            "relationship_type": "related_to",
            "confidence": "high",
        })
        result = validator.validate([p], graph_with_haven_and_claude)[0]
        assert result.accepted is False


# ---------------------------------------------------------------------------
# ATTACH_KNOWLEDGE_OBJECT — happy path
# ---------------------------------------------------------------------------


class TestAttachAccepted:
    def test_concept_in_graph(self, validator, graph_with_haven):
        ko_id = uuid4()
        p = attach_proposal(ko_id, _cid("Haven"))
        result = validator.validate([p], graph_with_haven)[0]
        assert result.accepted is True

    def test_concept_in_batch(self, validator, empty_graph):
        ko_id = uuid4()
        proposals = [
            concept_proposal("Haven"),
            attach_proposal(ko_id, _cid("Haven")),
        ]
        results = validator.validate(proposals, empty_graph)
        assert results[1].accepted is True

    def test_relevance_at_zero(self, validator, graph_with_haven):
        p = attach_proposal(uuid4(), _cid("Haven"), relevance=0.0)
        result = validator.validate([p], graph_with_haven)[0]
        assert result.accepted is True

    def test_relevance_at_one(self, validator, graph_with_haven):
        p = attach_proposal(uuid4(), _cid("Haven"), relevance=1.0)
        result = validator.validate([p], graph_with_haven)[0]
        assert result.accepted is True


# ---------------------------------------------------------------------------
# ATTACH_KNOWLEDGE_OBJECT — rejection cases
# ---------------------------------------------------------------------------


class TestAttachRejected:
    def test_concept_absent(self, validator, empty_graph):
        p = attach_proposal(uuid4(), _cid("MissingConcept"))
        result = validator.validate([p], empty_graph)[0]
        assert result.accepted is False
        assert "concept" in result.rejection_reason.lower()

    def test_already_in_graph(self, validator, graph_with_haven):
        ko_id = uuid4()
        att = Attachment.create(ko_id, _cid("Haven"))
        graph_with_haven.add_attachment(att)
        p = attach_proposal(ko_id, _cid("Haven"))
        result = validator.validate([p], graph_with_haven)[0]
        assert result.accepted is False
        assert "already exists" in result.rejection_reason

    def test_duplicate_within_batch(self, validator, graph_with_haven):
        ko_id = uuid4()
        proposals = [
            attach_proposal(ko_id, _cid("Haven")),
            attach_proposal(ko_id, _cid("Haven")),
        ]
        results = validator.validate(proposals, graph_with_haven)
        assert results[0].accepted is True
        assert results[1].accepted is False
        assert "duplicated within this batch" in results[1].rejection_reason

    def test_invalid_ko_uuid(self, validator, graph_with_haven):
        p = raw_proposal(ProposalType.ATTACH_KNOWLEDGE_OBJECT, {
            "knowledge_object_id": "bad-uuid",
            "concept_id": str(_cid("Haven")),
            "relevance": 0.5,
        })
        result = validator.validate([p], graph_with_haven)[0]
        assert result.accepted is False

    def test_invalid_concept_uuid(self, validator, graph_with_haven):
        p = raw_proposal(ProposalType.ATTACH_KNOWLEDGE_OBJECT, {
            "knowledge_object_id": str(uuid4()),
            "concept_id": "not-a-uuid",
            "relevance": 0.5,
        })
        result = validator.validate([p], graph_with_haven)[0]
        assert result.accepted is False

    def test_non_str_ko_id(self, validator, graph_with_haven):
        p = raw_proposal(ProposalType.ATTACH_KNOWLEDGE_OBJECT, {
            "knowledge_object_id": 9999,
            "concept_id": str(_cid("Haven")),
            "relevance": 0.5,
        })
        result = validator.validate([p], graph_with_haven)[0]
        assert result.accepted is False

    def test_relevance_below_zero(self, validator, graph_with_haven):
        p = attach_proposal(uuid4(), _cid("Haven"), relevance=-0.1)
        result = validator.validate([p], graph_with_haven)[0]
        assert result.accepted is False
        assert "relevance" in result.rejection_reason

    def test_relevance_above_one(self, validator, graph_with_haven):
        p = attach_proposal(uuid4(), _cid("Haven"), relevance=1.01)
        result = validator.validate([p], graph_with_haven)[0]
        assert result.accepted is False

    def test_non_numeric_relevance(self, validator, graph_with_haven):
        p = raw_proposal(ProposalType.ATTACH_KNOWLEDGE_OBJECT, {
            "knowledge_object_id": str(uuid4()),
            "concept_id": str(_cid("Haven")),
            "relevance": "high",
        })
        result = validator.validate([p], graph_with_haven)[0]
        assert result.accepted is False

    def test_missing_ko_id_key(self, validator, graph_with_haven):
        p = raw_proposal(ProposalType.ATTACH_KNOWLEDGE_OBJECT, {
            "concept_id": str(_cid("Haven")),
            "relevance": 0.5,
        })
        result = validator.validate([p], graph_with_haven)[0]
        assert result.accepted is False
        assert "knowledge_object_id" in result.rejection_reason

    def test_missing_concept_id_key(self, validator, graph_with_haven):
        p = raw_proposal(ProposalType.ATTACH_KNOWLEDGE_OBJECT, {
            "knowledge_object_id": str(uuid4()),
            "relevance": 0.5,
        })
        result = validator.validate([p], graph_with_haven)[0]
        assert result.accepted is False
        assert "concept_id" in result.rejection_reason

    def test_missing_relevance_key(self, validator, graph_with_haven):
        p = raw_proposal(ProposalType.ATTACH_KNOWLEDGE_OBJECT, {
            "knowledge_object_id": str(uuid4()),
            "concept_id": str(_cid("Haven")),
        })
        result = validator.validate([p], graph_with_haven)[0]
        assert result.accepted is False
        assert "relevance" in result.rejection_reason


# ---------------------------------------------------------------------------
# Order-dependency / batch sequencing
# ---------------------------------------------------------------------------


class TestOrderDependency:
    def test_relationship_before_concept_rejected(self, validator, empty_graph):
        """A relationship proposed before its CREATE_CONCEPT must fail."""
        proposals = [
            relationship_proposal(_cid("Alpha"), _cid("Beta")),
            concept_proposal("Alpha"),
            concept_proposal("Beta"),
        ]
        results = validator.validate(proposals, empty_graph)
        # Relationship is first → endpoints not yet created → rejected
        assert results[0].accepted is False

    def test_relationship_after_concept_accepted(self, validator, empty_graph):
        proposals = [
            concept_proposal("Alpha"),
            concept_proposal("Beta"),
            relationship_proposal(_cid("Alpha"), _cid("Beta")),
        ]
        results = validator.validate(proposals, empty_graph)
        assert results[2].accepted is True

    def test_attach_before_concept_rejected(self, validator, empty_graph):
        ko_id = uuid4()
        proposals = [
            attach_proposal(ko_id, _cid("Haven")),
            concept_proposal("Haven"),
        ]
        results = validator.validate(proposals, empty_graph)
        assert results[0].accepted is False

    def test_attach_after_concept_accepted(self, validator, empty_graph):
        ko_id = uuid4()
        proposals = [
            concept_proposal("Haven"),
            attach_proposal(ko_id, _cid("Haven")),
        ]
        results = validator.validate(proposals, empty_graph)
        assert results[1].accepted is True

    def test_rejected_concept_not_available_for_relationship(self, validator, empty_graph):
        """A rejected CREATE_CONCEPT must not make its label available for later proposals."""
        # Alpha already in graph → first CREATE_CONCEPT for Alpha rejected
        add_concept(empty_graph, "Alpha")
        add_concept(empty_graph, "Beta")
        proposals = [
            concept_proposal("Gamma"),        # accepted
            concept_proposal("Alpha"),         # rejected (duplicate)
            relationship_proposal(_cid("Alpha"), _cid("Gamma")),  # Alpha IS in graph, so OK
        ]
        results = validator.validate(proposals, empty_graph)
        assert results[0].accepted is True   # Gamma → accepted
        assert results[1].accepted is False  # Alpha dup → rejected
        # Alpha is already in graph so relationship should succeed
        assert results[2].accepted is True

    def test_rejected_concept_blocks_downstream_relationship(self, validator, empty_graph):
        """Concept rejected because it's truly absent must block the relationship."""
        # Neither Alpha nor Beta in graph; first concept_proposal for Alpha
        # is malformed so it fails; then relationship using Alpha should fail.
        proposals = [
            raw_proposal(ProposalType.CREATE_CONCEPT, {"label": "", "aliases": [], "description": ""}),  # rejected
            concept_proposal("Beta"),  # accepted
            relationship_proposal(_cid(""), _cid("Beta")),  # empty label → _cid("") is a real UUID but concept doesn't exist
        ]
        results = validator.validate(proposals, empty_graph)
        assert results[0].accepted is False
        assert results[1].accepted is True
        # The empty-label concept was rejected, so its (arbitrary) UUID not in batch
        assert results[2].accepted is False


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_repeated_calls_same_result(self, validator, empty_graph):
        proposals = [
            concept_proposal("Haven"),
            concept_proposal("Claude"),
            relationship_proposal(_cid("Haven"), _cid("Claude")),
        ]
        add_concept(empty_graph, "Existing")

        r1 = [r.accepted for r in validator.validate(proposals, empty_graph)]
        r2 = [r.accepted for r in validator.validate(proposals, empty_graph)]
        assert r1 == r2

    def test_rejection_reasons_stable(self, validator, graph_with_haven):
        p = concept_proposal("Haven")  # will be rejected as duplicate
        r1 = validator.validate([p], graph_with_haven)[0].rejection_reason
        r2 = validator.validate([p], graph_with_haven)[0].rejection_reason
        assert r1 == r2


# ---------------------------------------------------------------------------
# No mutation
# ---------------------------------------------------------------------------


class TestNoMutation:
    def test_concepts_unchanged(self, validator, empty_graph):
        before = graph_concept_count(empty_graph)
        validator.validate([concept_proposal("Haven"), concept_proposal("Claude")], empty_graph)
        assert graph_concept_count(empty_graph) == before

    def test_relationships_unchanged(self, validator, graph_with_haven_and_claude):
        before = graph_relationship_count(graph_with_haven_and_claude)
        src, tgt = _cid("Haven"), _cid("Claude")
        if str(src) > str(tgt):
            src, tgt = tgt, src
        validator.validate([relationship_proposal(src, tgt)], graph_with_haven_and_claude)
        assert graph_relationship_count(graph_with_haven_and_claude) == before

    def test_attachments_unchanged(self, validator, graph_with_haven):
        before = graph_attachment_count(graph_with_haven)
        validator.validate([attach_proposal(uuid4(), _cid("Haven"))], graph_with_haven)
        assert graph_attachment_count(graph_with_haven) == before
