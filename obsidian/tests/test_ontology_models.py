"""Unit tests for obsidian.ontology Phase 1 data models.

Tests are grouped by module and then by class/function.  Every test is
deterministic: no random UUIDs appear in assertions.

Coverage targets
----------------
* :mod:`obsidian.ontology.enums`   – enum membership and str coercion.
* :mod:`obsidian.ontology.identity` – determinism, direction-sensitivity,
  case-insensitivity, and type checks.
* :mod:`obsidian.ontology.models`  – validation, immutability, factory
  methods, and serialisation round-trips.
* :mod:`obsidian.ontology.text_utils` – normalisation, tokenisation,
  stop-word removal, and label indexing.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from uuid import UUID

import pytest

from obsidian.ontology.enums import OntologyRelationshipType, ProposalType
from obsidian.ontology.identity import (
    ONTOLOGY_NAMESPACE,
    attachment_id,
    concept_id,
    relationship_id,
)
from obsidian.ontology.models import (
    Attachment,
    Concept,
    OntologyProposal,
    Relationship,
)
from obsidian.ontology.text_utils import (
    STOP_WORDS,
    normalize,
    tokenize,
    tokenize_label,
    tokenize_query,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def haven_id() -> UUID:
    """Deterministic UUID for the "Haven" concept."""
    return concept_id("Haven")


@pytest.fixture
def claude_id() -> UUID:
    """Deterministic UUID for the "Claude" concept."""
    return concept_id("Claude")


@pytest.fixture
def ko_id() -> UUID:
    """Fixed KnowledgeObject ID used across attachment tests."""
    return UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
def haven_concept() -> Concept:
    return Concept.from_label("Haven", description="Personal second-brain project")


@pytest.fixture
def claude_concept() -> Concept:
    return Concept.from_label(
        "Claude",
        aliases=("Claude AI", "Anthropic Claude"),
        description="Anthropic's assistant",
    )


# ===========================================================================
# obsidian.ontology.enums
# ===========================================================================


class TestOntologyRelationshipType:
    """Verify all spec-defined relationship types exist and coerce to strings."""

    EXPECTED_VALUES = {
        "is_a",
        "part_of",
        "uses",
        "depends_on",
        "created_by",
        "located_in",
        "related_to",
        "supports",
    }

    def test_all_spec_values_present(self) -> None:
        actual = {r.value for r in OntologyRelationshipType}
        assert actual == self.EXPECTED_VALUES

    def test_count_matches_spec(self) -> None:
        assert len(OntologyRelationshipType) == len(self.EXPECTED_VALUES)

    def test_is_str_enum(self) -> None:
        assert OntologyRelationshipType.IS_A == "is_a"
        assert OntologyRelationshipType.PART_OF == "part_of"
        assert OntologyRelationshipType.USES == "uses"

    def test_round_trip_from_value(self) -> None:
        for rel_type in OntologyRelationshipType:
            assert OntologyRelationshipType(rel_type.value) is rel_type


class TestProposalType:
    """Verify all proposal types exist and coerce to strings."""

    EXPECTED_VALUES = {
        "create_concept",
        "create_relationship",
        "attach_knowledge_object",
    }

    def test_all_spec_values_present(self) -> None:
        actual = {p.value for p in ProposalType}
        assert actual == self.EXPECTED_VALUES

    def test_is_str_enum(self) -> None:
        assert ProposalType.CREATE_CONCEPT == "create_concept"
        assert ProposalType.CREATE_RELATIONSHIP == "create_relationship"
        assert ProposalType.ATTACH_KNOWLEDGE_OBJECT == "attach_knowledge_object"

    def test_round_trip_from_value(self) -> None:
        for pt in ProposalType:
            assert ProposalType(pt.value) is pt


# ===========================================================================
# obsidian.ontology.identity
# ===========================================================================


class TestOntologyNamespace:
    def test_is_uuid(self) -> None:
        assert isinstance(ONTOLOGY_NAMESPACE, UUID)

    def test_is_version_5(self) -> None:
        assert ONTOLOGY_NAMESPACE.version == 5

    def test_stable_across_calls(self) -> None:
        from obsidian.ontology.identity import ONTOLOGY_NAMESPACE as ns2
        assert ONTOLOGY_NAMESPACE == ns2


class TestConceptId:
    def test_returns_uuid(self) -> None:
        assert isinstance(concept_id("Haven"), UUID)

    def test_deterministic_same_label(self) -> None:
        assert concept_id("Haven") == concept_id("Haven")

    def test_case_insensitive(self) -> None:
        assert concept_id("Haven") == concept_id("haven")
        assert concept_id("Haven") == concept_id("HAVEN")

    def test_strips_whitespace(self) -> None:
        assert concept_id("Haven") == concept_id("  Haven  ")
        assert concept_id("Haven") == concept_id("\tHaven\n")

    def test_different_labels_differ(self) -> None:
        assert concept_id("Haven") != concept_id("Claude")

    def test_empty_string_produces_uuid(self) -> None:
        # Empty string is a valid UUID5 input; we just verify it returns a UUID.
        assert isinstance(concept_id(""), UUID)

    def test_is_version_5(self) -> None:
        assert concept_id("Haven").version == 5


class TestRelationshipId:
    def test_returns_uuid(self, haven_id: UUID, claude_id: UUID) -> None:
        rid = relationship_id(haven_id, claude_id, OntologyRelationshipType.USES.value)
        assert isinstance(rid, UUID)

    def test_deterministic(self, haven_id: UUID, claude_id: UUID) -> None:
        rid1 = relationship_id(haven_id, claude_id, "uses")
        rid2 = relationship_id(haven_id, claude_id, "uses")
        assert rid1 == rid2

    def test_direction_sensitive(self, haven_id: UUID, claude_id: UUID) -> None:
        forward = relationship_id(haven_id, claude_id, "uses")
        backward = relationship_id(claude_id, haven_id, "uses")
        assert forward != backward

    def test_type_sensitive(self, haven_id: UUID, claude_id: UUID) -> None:
        uses = relationship_id(haven_id, claude_id, "uses")
        related = relationship_id(haven_id, claude_id, "related_to")
        assert uses != related

    def test_is_version_5(self, haven_id: UUID, claude_id: UUID) -> None:
        rid = relationship_id(haven_id, claude_id, "uses")
        assert rid.version == 5


class TestAttachmentId:
    def test_returns_uuid(self, ko_id: UUID, haven_id: UUID) -> None:
        assert isinstance(attachment_id(ko_id, haven_id), UUID)

    def test_deterministic(self, ko_id: UUID, haven_id: UUID) -> None:
        aid1 = attachment_id(ko_id, haven_id)
        aid2 = attachment_id(ko_id, haven_id)
        assert aid1 == aid2

    def test_different_concept_differs(self, ko_id: UUID, haven_id: UUID, claude_id: UUID) -> None:
        aid1 = attachment_id(ko_id, haven_id)
        aid2 = attachment_id(ko_id, claude_id)
        assert aid1 != aid2

    def test_different_ko_differs(self, ko_id: UUID, haven_id: UUID) -> None:
        other_ko = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        aid1 = attachment_id(ko_id, haven_id)
        aid2 = attachment_id(other_ko, haven_id)
        assert aid1 != aid2

    def test_is_version_5(self, ko_id: UUID, haven_id: UUID) -> None:
        assert attachment_id(ko_id, haven_id).version == 5


# ===========================================================================
# obsidian.ontology.models – Concept
# ===========================================================================


class TestConceptFromLabel:
    def test_returns_concept(self) -> None:
        c = Concept.from_label("Haven")
        assert isinstance(c, Concept)

    def test_label_preserved(self) -> None:
        c = Concept.from_label("Haven")
        assert c.label == "Haven"

    def test_id_is_deterministic(self) -> None:
        c1 = Concept.from_label("Haven")
        c2 = Concept.from_label("Haven")
        assert c1.id == c2.id

    def test_id_matches_identity_function(self) -> None:
        c = Concept.from_label("Haven")
        assert c.id == concept_id("Haven")

    def test_different_labels_different_ids(self) -> None:
        assert Concept.from_label("Haven").id != Concept.from_label("Claude").id

    def test_aliases_stored(self) -> None:
        c = Concept.from_label("Claude", aliases=("Claude AI", "Anthropic Claude"))
        assert "Claude AI" in c.aliases
        assert "Anthropic Claude" in c.aliases

    def test_description_stored(self) -> None:
        c = Concept.from_label("Haven", description="second brain")
        assert c.description == "second brain"

    def test_created_at_defaults_to_now(self) -> None:
        before = datetime.utcnow()
        c = Concept.from_label("Haven")
        after = datetime.utcnow()
        assert before <= c.created_at <= after

    def test_created_at_override(self) -> None:
        ts = datetime(2026, 1, 1, 12, 0, 0)
        c = Concept.from_label("Haven", created_at=ts)
        assert c.created_at == ts


class TestConceptValidation:
    def test_empty_label_raises(self) -> None:
        with pytest.raises(ValueError, match="label"):
            Concept(id=concept_id("x"), label="")

    def test_whitespace_only_label_raises(self) -> None:
        with pytest.raises(ValueError, match="label"):
            Concept(id=concept_id("x"), label="   ")

    def test_duplicate_aliases_raise(self) -> None:
        with pytest.raises(ValueError, match="aliases"):
            Concept(
                id=concept_id("Haven"),
                label="Haven",
                aliases=("alpha", "alpha"),
            )

    def test_unique_aliases_accepted(self) -> None:
        c = Concept(
            id=concept_id("Haven"),
            label="Haven",
            aliases=("alpha", "beta"),
        )
        assert len(c.aliases) == 2


class TestConceptImmutability:
    def test_label_is_immutable(self, haven_concept: Concept) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            haven_concept.label = "Other"  # type: ignore[misc]

    def test_id_is_immutable(self, haven_concept: Concept) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            haven_concept.id = concept_id("Other")  # type: ignore[misc]

    def test_aliases_is_immutable(self, haven_concept: Concept) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            haven_concept.aliases = ("x",)  # type: ignore[misc]


class TestConceptSerialisation:
    def test_to_dict_keys(self, haven_concept: Concept) -> None:
        d = haven_concept.to_dict()
        assert {"id", "label", "aliases", "description", "created_at"} == set(d.keys())

    def test_to_dict_id_is_str(self, haven_concept: Concept) -> None:
        d = haven_concept.to_dict()
        assert isinstance(d["id"], str)
        UUID(d["id"])  # must be a valid UUID string

    def test_to_dict_aliases_is_list(self, claude_concept: Concept) -> None:
        d = claude_concept.to_dict()
        assert isinstance(d["aliases"], list)

    def test_to_dict_created_at_is_iso_str(self, haven_concept: Concept) -> None:
        d = haven_concept.to_dict()
        # fromisoformat should not raise
        datetime.fromisoformat(d["created_at"])

    def test_round_trip_id(self, haven_concept: Concept) -> None:
        restored = Concept.from_dict(haven_concept.to_dict())
        assert restored.id == haven_concept.id

    def test_round_trip_label(self, haven_concept: Concept) -> None:
        restored = Concept.from_dict(haven_concept.to_dict())
        assert restored.label == haven_concept.label

    def test_round_trip_aliases(self, claude_concept: Concept) -> None:
        restored = Concept.from_dict(claude_concept.to_dict())
        assert restored.aliases == claude_concept.aliases

    def test_round_trip_description(self, haven_concept: Concept) -> None:
        restored = Concept.from_dict(haven_concept.to_dict())
        assert restored.description == haven_concept.description

    def test_round_trip_created_at(self, haven_concept: Concept) -> None:
        restored = Concept.from_dict(haven_concept.to_dict())
        # isoformat round-trip truncates microseconds consistently
        assert restored.created_at.isoformat() == haven_concept.created_at.isoformat()


# ===========================================================================
# obsidian.ontology.models – Relationship
# ===========================================================================


class TestRelationshipCreate:
    def test_returns_relationship(self, haven_id: UUID, claude_id: UUID) -> None:
        r = Relationship.create(haven_id, claude_id, OntologyRelationshipType.USES)
        assert isinstance(r, Relationship)

    def test_id_is_deterministic(self, haven_id: UUID, claude_id: UUID) -> None:
        r1 = Relationship.create(haven_id, claude_id, OntologyRelationshipType.USES)
        r2 = Relationship.create(haven_id, claude_id, OntologyRelationshipType.USES)
        assert r1.id == r2.id

    def test_id_matches_identity_function(self, haven_id: UUID, claude_id: UUID) -> None:
        r = Relationship.create(haven_id, claude_id, OntologyRelationshipType.USES)
        expected = relationship_id(haven_id, claude_id, "uses")
        assert r.id == expected

    def test_different_types_differ(self, haven_id: UUID, claude_id: UUID) -> None:
        r1 = Relationship.create(haven_id, claude_id, OntologyRelationshipType.USES)
        r2 = Relationship.create(haven_id, claude_id, OntologyRelationshipType.RELATED_TO)
        assert r1.id != r2.id

    def test_direction_matters(self, haven_id: UUID, claude_id: UUID) -> None:
        r1 = Relationship.create(haven_id, claude_id, OntologyRelationshipType.USES)
        r2 = Relationship.create(claude_id, haven_id, OntologyRelationshipType.USES)
        assert r1.id != r2.id

    def test_all_relationship_types_accepted(
        self, haven_id: UUID, claude_id: UUID
    ) -> None:
        for rel_type in OntologyRelationshipType:
            r = Relationship.create(haven_id, claude_id, rel_type)
            assert r.relationship_type == rel_type

    def test_default_confidence_is_one(self, haven_id: UUID, claude_id: UUID) -> None:
        r = Relationship.create(haven_id, claude_id, OntologyRelationshipType.USES)
        assert r.confidence == 1.0

    def test_custom_confidence_stored(self, haven_id: UUID, claude_id: UUID) -> None:
        r = Relationship.create(
            haven_id, claude_id, OntologyRelationshipType.USES, confidence=0.7
        )
        assert r.confidence == 0.7


class TestRelationshipValidation:
    def test_self_loop_via_create_raises(self, haven_id: UUID) -> None:
        with pytest.raises(ValueError, match="self"):
            Relationship.create(haven_id, haven_id, OntologyRelationshipType.RELATED_TO)

    def test_self_loop_via_direct_constructor_raises(self, haven_id: UUID) -> None:
        rid = relationship_id(haven_id, haven_id, "related_to")
        with pytest.raises(ValueError):
            Relationship(
                id=rid,
                source_id=haven_id,
                target_id=haven_id,
                relationship_type=OntologyRelationshipType.RELATED_TO,
            )

    def test_confidence_above_one_raises(self, haven_id: UUID, claude_id: UUID) -> None:
        with pytest.raises(ValueError, match="confidence"):
            Relationship.create(
                haven_id, claude_id, OntologyRelationshipType.USES, confidence=1.1
            )

    def test_negative_confidence_raises(self, haven_id: UUID, claude_id: UUID) -> None:
        with pytest.raises(ValueError, match="confidence"):
            Relationship.create(
                haven_id, claude_id, OntologyRelationshipType.USES, confidence=-0.1
            )

    def test_zero_confidence_accepted(self, haven_id: UUID, claude_id: UUID) -> None:
        r = Relationship.create(
            haven_id, claude_id, OntologyRelationshipType.USES, confidence=0.0
        )
        assert r.confidence == 0.0

    def test_one_confidence_accepted(self, haven_id: UUID, claude_id: UUID) -> None:
        r = Relationship.create(
            haven_id, claude_id, OntologyRelationshipType.USES, confidence=1.0
        )
        assert r.confidence == 1.0


class TestRelationshipImmutability:
    def test_source_id_immutable(self, haven_id: UUID, claude_id: UUID) -> None:
        r = Relationship.create(haven_id, claude_id, OntologyRelationshipType.USES)
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.source_id = claude_id  # type: ignore[misc]

    def test_confidence_immutable(self, haven_id: UUID, claude_id: UUID) -> None:
        r = Relationship.create(haven_id, claude_id, OntologyRelationshipType.USES)
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.confidence = 0.5  # type: ignore[misc]


class TestRelationshipSerialisation:
    def test_to_dict_keys(self, haven_id: UUID, claude_id: UUID) -> None:
        r = Relationship.create(haven_id, claude_id, OntologyRelationshipType.USES)
        d = r.to_dict()
        assert {"id", "source_id", "target_id", "relationship_type", "confidence"} == set(
            d.keys()
        )

    def test_to_dict_ids_are_strings(self, haven_id: UUID, claude_id: UUID) -> None:
        r = Relationship.create(haven_id, claude_id, OntologyRelationshipType.USES)
        d = r.to_dict()
        UUID(d["id"])
        UUID(d["source_id"])
        UUID(d["target_id"])

    def test_round_trip(self, haven_id: UUID, claude_id: UUID) -> None:
        original = Relationship.create(
            haven_id, claude_id, OntologyRelationshipType.USES, confidence=0.85
        )
        restored = Relationship.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.source_id == original.source_id
        assert restored.target_id == original.target_id
        assert restored.relationship_type == original.relationship_type
        assert restored.confidence == original.confidence


# ===========================================================================
# obsidian.ontology.models – Attachment
# ===========================================================================


class TestAttachmentCreate:
    def test_returns_attachment(self, ko_id: UUID, haven_id: UUID) -> None:
        a = Attachment.create(ko_id, haven_id)
        assert isinstance(a, Attachment)

    def test_id_is_deterministic(self, ko_id: UUID, haven_id: UUID) -> None:
        a1 = Attachment.create(ko_id, haven_id)
        a2 = Attachment.create(ko_id, haven_id)
        assert a1.id == a2.id

    def test_id_matches_identity_function(self, ko_id: UUID, haven_id: UUID) -> None:
        a = Attachment.create(ko_id, haven_id)
        assert a.id == attachment_id(ko_id, haven_id)

    def test_different_concept_differs(
        self, ko_id: UUID, haven_id: UUID, claude_id: UUID
    ) -> None:
        a1 = Attachment.create(ko_id, haven_id)
        a2 = Attachment.create(ko_id, claude_id)
        assert a1.id != a2.id

    def test_default_relevance_is_one(self, ko_id: UUID, haven_id: UUID) -> None:
        a = Attachment.create(ko_id, haven_id)
        assert a.relevance == 1.0

    def test_custom_relevance_stored(self, ko_id: UUID, haven_id: UUID) -> None:
        a = Attachment.create(ko_id, haven_id, relevance=0.6)
        assert a.relevance == 0.6

    def test_fields_stored_correctly(self, ko_id: UUID, haven_id: UUID) -> None:
        a = Attachment.create(ko_id, haven_id, relevance=0.8)
        assert a.knowledge_object_id == ko_id
        assert a.concept_id == haven_id


class TestAttachmentValidation:
    def test_relevance_above_one_raises(self, ko_id: UUID, haven_id: UUID) -> None:
        with pytest.raises(ValueError, match="relevance"):
            Attachment.create(ko_id, haven_id, relevance=1.01)

    def test_negative_relevance_raises(self, ko_id: UUID, haven_id: UUID) -> None:
        with pytest.raises(ValueError, match="relevance"):
            Attachment.create(ko_id, haven_id, relevance=-0.1)

    def test_zero_relevance_accepted(self, ko_id: UUID, haven_id: UUID) -> None:
        a = Attachment.create(ko_id, haven_id, relevance=0.0)
        assert a.relevance == 0.0

    def test_one_relevance_accepted(self, ko_id: UUID, haven_id: UUID) -> None:
        a = Attachment.create(ko_id, haven_id, relevance=1.0)
        assert a.relevance == 1.0


class TestAttachmentImmutability:
    def test_relevance_immutable(self, ko_id: UUID, haven_id: UUID) -> None:
        a = Attachment.create(ko_id, haven_id)
        with pytest.raises(dataclasses.FrozenInstanceError):
            a.relevance = 0.5  # type: ignore[misc]

    def test_concept_id_immutable(self, ko_id: UUID, haven_id: UUID) -> None:
        a = Attachment.create(ko_id, haven_id)
        with pytest.raises(dataclasses.FrozenInstanceError):
            a.concept_id = ko_id  # type: ignore[misc]


class TestAttachmentSerialisation:
    def test_to_dict_keys(self, ko_id: UUID, haven_id: UUID) -> None:
        a = Attachment.create(ko_id, haven_id)
        d = a.to_dict()
        assert {"id", "knowledge_object_id", "concept_id", "relevance"} == set(d.keys())

    def test_to_dict_ids_are_strings(self, ko_id: UUID, haven_id: UUID) -> None:
        a = Attachment.create(ko_id, haven_id)
        d = a.to_dict()
        UUID(d["id"])
        UUID(d["knowledge_object_id"])
        UUID(d["concept_id"])

    def test_round_trip(self, ko_id: UUID, haven_id: UUID) -> None:
        original = Attachment.create(ko_id, haven_id, relevance=0.75)
        restored = Attachment.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.knowledge_object_id == original.knowledge_object_id
        assert restored.concept_id == original.concept_id
        assert restored.relevance == original.relevance


# ===========================================================================
# obsidian.ontology.models – OntologyProposal
# ===========================================================================


class TestOntologyProposalConstruction:
    def test_create_concept_proposal(self) -> None:
        p = OntologyProposal(
            proposal_type=ProposalType.CREATE_CONCEPT,
            payload={"label": "Haven", "aliases": [], "description": ""},
            reason="New entity detected",
        )
        assert p.proposal_type == ProposalType.CREATE_CONCEPT
        assert p.reason == "New entity detected"

    def test_create_relationship_proposal(
        self, haven_id: UUID, claude_id: UUID
    ) -> None:
        p = OntologyProposal(
            proposal_type=ProposalType.CREATE_RELATIONSHIP,
            payload={
                "source_id": str(haven_id),
                "target_id": str(claude_id),
                "relationship_type": OntologyRelationshipType.USES.value,
                "confidence": 0.9,
            },
        )
        assert p.proposal_type == ProposalType.CREATE_RELATIONSHIP

    def test_attach_knowledge_object_proposal(
        self, ko_id: UUID, haven_id: UUID
    ) -> None:
        p = OntologyProposal(
            proposal_type=ProposalType.ATTACH_KNOWLEDGE_OBJECT,
            payload={
                "knowledge_object_id": str(ko_id),
                "concept_id": str(haven_id),
                "relevance": 0.8,
            },
        )
        assert p.proposal_type == ProposalType.ATTACH_KNOWLEDGE_OBJECT

    def test_reason_defaults_to_empty_string(self) -> None:
        p = OntologyProposal(
            proposal_type=ProposalType.CREATE_CONCEPT,
            payload={"label": "Haven"},
        )
        assert p.reason == ""


class TestOntologyProposalValidation:
    def test_empty_payload_raises(self) -> None:
        with pytest.raises(ValueError, match="payload"):
            OntologyProposal(
                proposal_type=ProposalType.CREATE_CONCEPT,
                payload={},
            )


class TestOntologyProposalImmutability:
    def test_proposal_type_immutable(self) -> None:
        p = OntologyProposal(
            proposal_type=ProposalType.CREATE_CONCEPT,
            payload={"label": "Haven"},
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.proposal_type = ProposalType.CREATE_RELATIONSHIP  # type: ignore[misc]

    def test_reason_immutable(self) -> None:
        p = OntologyProposal(
            proposal_type=ProposalType.CREATE_CONCEPT,
            payload={"label": "Haven"},
            reason="original",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.reason = "changed"  # type: ignore[misc]


class TestOntologyProposalSerialisation:
    def test_to_dict_keys(self) -> None:
        p = OntologyProposal(
            proposal_type=ProposalType.CREATE_CONCEPT,
            payload={"label": "Haven"},
        )
        d = p.to_dict()
        assert {"proposal_type", "payload", "reason"} == set(d.keys())

    def test_to_dict_proposal_type_is_string(self) -> None:
        p = OntologyProposal(
            proposal_type=ProposalType.CREATE_CONCEPT,
            payload={"label": "Haven"},
        )
        d = p.to_dict()
        assert isinstance(d["proposal_type"], str)

    def test_round_trip_proposal_type(self) -> None:
        p = OntologyProposal(
            proposal_type=ProposalType.CREATE_CONCEPT,
            payload={"label": "Haven"},
            reason="detected",
        )
        restored = OntologyProposal.from_dict(p.to_dict())
        assert restored.proposal_type == p.proposal_type

    def test_round_trip_payload(self) -> None:
        payload = {"label": "Haven", "aliases": ["H"], "description": "brain"}
        p = OntologyProposal(
            proposal_type=ProposalType.CREATE_CONCEPT,
            payload=payload,
        )
        restored = OntologyProposal.from_dict(p.to_dict())
        assert restored.payload == payload

    def test_round_trip_reason(self) -> None:
        p = OntologyProposal(
            proposal_type=ProposalType.CREATE_CONCEPT,
            payload={"label": "Haven"},
            reason="Detected new entity",
        )
        restored = OntologyProposal.from_dict(p.to_dict())
        assert restored.reason == p.reason


# ===========================================================================
# obsidian.ontology.text_utils
# ===========================================================================


class TestNormalize:
    def test_lowercases(self) -> None:
        assert normalize("Haven") == "haven"

    def test_strips_leading_whitespace(self) -> None:
        assert normalize("  Haven") == "haven"

    def test_strips_trailing_whitespace(self) -> None:
        assert normalize("Haven  ") == "haven"

    def test_strips_both_sides(self) -> None:
        assert normalize("  Haven  ") == "haven"

    def test_preserves_internal_spaces(self) -> None:
        assert normalize("Memory Engine") == "memory engine"

    def test_empty_string(self) -> None:
        assert normalize("") == ""

    def test_already_normalised(self) -> None:
        assert normalize("memory engine") == "memory engine"


class TestTokenize:
    def test_basic_split(self) -> None:
        assert tokenize("memory engine") == ["memory", "engine"]

    def test_lowercases(self) -> None:
        assert tokenize("Haven Engine") == ["haven", "engine"]

    def test_strips_punctuation(self) -> None:
        result = tokenize("haven, obsidian!")
        assert result == ["haven", "obsidian"]

    def test_multiple_delimiters(self) -> None:
        result = tokenize("a.b-c d")
        assert result == ["a", "b", "c", "d"]

    def test_preserves_numbers(self) -> None:
        result = tokenize("phase 1 implementation")
        assert "1" in result
        assert "phase" in result
        assert "implementation" in result

    def test_empty_string(self) -> None:
        assert tokenize("") == []

    def test_only_punctuation(self) -> None:
        assert tokenize("!!! ???") == []

    def test_multiple_spaces_between_words(self) -> None:
        result = tokenize("  memory   engine  ")
        assert "memory" in result
        assert "engine" in result

    def test_unicode_letters_excluded(self) -> None:
        # Only ASCII alphanumeric tokens are matched
        result = tokenize("café")
        # "caf" and "e" or similar – we just verify it doesn't crash
        assert isinstance(result, list)


class TestTokenizeQuery:
    def test_removes_stop_words(self) -> None:
        tokens = tokenize_query("what is the memory engine")
        assert "what" not in tokens
        assert "is" not in tokens
        assert "the" not in tokens
        assert "memory" in tokens
        assert "engine" in tokens

    def test_empty_string(self) -> None:
        assert tokenize_query("") == []

    def test_all_stop_words_returns_empty(self) -> None:
        assert tokenize_query("the a an is are") == []

    def test_non_stop_words_preserved(self) -> None:
        tokens = tokenize_query("Haven retrieval quality benchmark")
        assert "haven" in tokens
        assert "retrieval" in tokens
        assert "quality" in tokens
        assert "benchmark" in tokens

    def test_stop_words_case_insensitive(self) -> None:
        # tokenize lowercases before stop-word check
        tokens = tokenize_query("THE MEMORY ENGINE")
        assert "the" not in tokens
        assert "memory" in tokens
        assert "engine" in tokens

    def test_preserves_numbers(self) -> None:
        tokens = tokenize_query("phase 1 results")
        assert "1" in tokens
        assert "phase" in tokens
        assert "results" in tokens


class TestTokenizeLabel:
    def test_returns_all_tokens(self) -> None:
        tokens = tokenize_label("Memory Engine")
        assert tokens == ["memory", "engine"]

    def test_stop_words_not_removed(self) -> None:
        tokens = tokenize_label("Tower of London")
        assert "of" in tokens

    def test_empty_string(self) -> None:
        assert tokenize_label("") == []

    def test_single_word(self) -> None:
        assert tokenize_label("Haven") == ["haven"]

    def test_multi_word_with_prepositions(self) -> None:
        tokens = tokenize_label("State of the Art")
        assert "of" in tokens
        assert "the" in tokens


class TestStopWords:
    def test_is_frozenset(self) -> None:
        assert isinstance(STOP_WORDS, frozenset)

    def test_contains_articles(self) -> None:
        for word in ("the", "a", "an"):
            assert word in STOP_WORDS

    def test_contains_common_verbs(self) -> None:
        for word in ("is", "are", "was", "were", "be"):
            assert word in STOP_WORDS

    def test_contains_pronouns(self) -> None:
        for word in ("i", "you", "he", "she", "we", "they"):
            assert word in STOP_WORDS

    def test_all_lowercase(self) -> None:
        for word in STOP_WORDS:
            assert word == word.lower()
