"""Unit tests for obsidian.ontology.concept_parser – Phase 2C.

Tests are grouped by concern:

* :class:`TestConceptParserInstantiation` — stateless construction.
* :class:`TestConceptParseResultStructure` — result dataclass shape.
* :class:`TestParseConceptFields` — UUID, label, aliases, description, created_at.
* :class:`TestParseRelationships` — full reconstruction with all fields.
* :class:`TestParseAttachments` — full reconstruction with all fields.
* :class:`TestParseUpdatedAt` — null / ISO handling.
* :class:`TestRoundTrip` — write → parse → compare (core correctness).
* :class:`TestParseStructuralErrors` — missing fences, invalid YAML, etc.
* :class:`TestParseTopLevelFieldErrors` — missing/invalid required fields.
* :class:`TestParseRelationshipErrors` — per-field relationship validation.
* :class:`TestParseAttachmentErrors` — per-field attachment validation.
* :class:`TestParseDeterminism` — identical inputs produce identical output.
* :class:`TestReadFromFile` — filesystem operations via :meth:`ConceptParser.read`.

All tests are deterministic.  Fixed timestamps and fixed UUIDs are used
throughout so that assertions never depend on ``datetime.utcnow()``.
"""

from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from obsidian.ontology.concept_parser import ConceptParseError, ConceptParseResult, ConceptParser
from obsidian.ontology.concept_writer import ConceptWriter
from obsidian.ontology.enums import OntologyRelationshipType
from obsidian.ontology.identity import concept_id
from obsidian.ontology.models import Attachment, Concept, Relationship


# ---------------------------------------------------------------------------
# Fixed test data (mirrors test_concept_writer.py for round-trip symmetry)
# ---------------------------------------------------------------------------

_CREATED_AT = datetime(2026, 1, 1, 12, 0, 0)
_UPDATED_AT = datetime(2026, 6, 30, 10, 0, 0)

_KO_ID_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_KO_ID_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def writer() -> ConceptWriter:
    return ConceptWriter()


@pytest.fixture
def parser() -> ConceptParser:
    return ConceptParser()


@pytest.fixture
def haven() -> Concept:
    return Concept.from_label(
        "Haven",
        aliases=("HKMS", "Personal Second Brain"),
        description="The personal knowledge management system.",
        created_at=_CREATED_AT,
    )


@pytest.fixture
def claude() -> Concept:
    return Concept.from_label("Claude", created_at=_CREATED_AT)


@pytest.fixture
def bare() -> Concept:
    return Concept.from_label("Bare", created_at=_CREATED_AT)


@pytest.fixture
def haven_uses_claude(haven: Concept, claude: Concept) -> Relationship:
    return Relationship.create(haven.id, claude.id, OntologyRelationshipType.USES)


@pytest.fixture
def claude_created_by_anthropic(claude: Concept) -> Relationship:
    return Relationship.create(
        concept_id("Anthropic"), claude.id, OntologyRelationshipType.CREATED_BY
    )


@pytest.fixture
def attachment_a(haven: Concept) -> Attachment:
    return Attachment.create(_KO_ID_A, haven.id, relevance=0.9)


@pytest.fixture
def attachment_b(haven: Concept) -> Attachment:
    return Attachment.create(_KO_ID_B, haven.id, relevance=0.75)


@pytest.fixture
def rendered_bare(writer: ConceptWriter, bare: Concept) -> str:
    return writer.render(bare)


@pytest.fixture
def rendered_haven(writer: ConceptWriter, haven: Concept) -> str:
    return writer.render(haven)


@pytest.fixture
def rendered_full(
    writer: ConceptWriter,
    haven: Concept,
    haven_uses_claude: Relationship,
    attachment_a: Attachment,
) -> str:
    return writer.render(
        haven,
        relationships=[haven_uses_claude],
        attachments=[attachment_a],
        updated_at=_UPDATED_AT,
    )


# ---------------------------------------------------------------------------
# TestConceptParserInstantiation
# ---------------------------------------------------------------------------


class TestConceptParserInstantiation:
    def test_instantiates_without_arguments(self) -> None:
        p = ConceptParser()
        assert isinstance(p, ConceptParser)

    def test_has_parse_method(self) -> None:
        assert callable(ConceptParser().parse)

    def test_has_read_method(self) -> None:
        assert callable(ConceptParser().read)

    def test_no_graph_attribute(self) -> None:
        assert not hasattr(ConceptParser(), "graph")

    def test_no_write_attribute(self) -> None:
        assert not hasattr(ConceptParser(), "write")


# ---------------------------------------------------------------------------
# TestConceptParseResultStructure
# ---------------------------------------------------------------------------


class TestConceptParseResultStructure:
    def test_result_has_concept(
        self, parser: ConceptParser, rendered_bare: str, bare: Concept
    ) -> None:
        result = parser.parse(rendered_bare)
        assert hasattr(result, "concept")

    def test_result_has_relationships(
        self, parser: ConceptParser, rendered_bare: str
    ) -> None:
        result = parser.parse(rendered_bare)
        assert hasattr(result, "relationships")

    def test_result_has_attachments(
        self, parser: ConceptParser, rendered_bare: str
    ) -> None:
        result = parser.parse(rendered_bare)
        assert hasattr(result, "attachments")

    def test_result_has_updated_at(
        self, parser: ConceptParser, rendered_bare: str
    ) -> None:
        result = parser.parse(rendered_bare)
        assert hasattr(result, "updated_at")

    def test_result_is_concept_parse_result(
        self, parser: ConceptParser, rendered_bare: str
    ) -> None:
        result = parser.parse(rendered_bare)
        assert isinstance(result, ConceptParseResult)

    def test_relationships_is_list(
        self, parser: ConceptParser, rendered_bare: str
    ) -> None:
        result = parser.parse(rendered_bare)
        assert isinstance(result.relationships, list)

    def test_attachments_is_list(
        self, parser: ConceptParser, rendered_bare: str
    ) -> None:
        result = parser.parse(rendered_bare)
        assert isinstance(result.attachments, list)

    def test_empty_collections_for_bare_concept(
        self, parser: ConceptParser, rendered_bare: str
    ) -> None:
        result = parser.parse(rendered_bare)
        assert result.relationships == []
        assert result.attachments == []

    def test_updated_at_none_by_default(
        self, parser: ConceptParser, rendered_bare: str
    ) -> None:
        result = parser.parse(rendered_bare)
        assert result.updated_at is None


# ---------------------------------------------------------------------------
# TestParseConceptFields
# ---------------------------------------------------------------------------


class TestParseConceptFields:
    def test_concept_id_is_uuid(
        self, parser: ConceptParser, rendered_haven: str
    ) -> None:
        result = parser.parse(rendered_haven)
        assert isinstance(result.concept.id, UUID)

    def test_concept_id_matches(
        self, parser: ConceptParser, rendered_haven: str, haven: Concept
    ) -> None:
        result = parser.parse(rendered_haven)
        assert result.concept.id == haven.id

    def test_concept_label_matches(
        self, parser: ConceptParser, rendered_haven: str
    ) -> None:
        result = parser.parse(rendered_haven)
        assert result.concept.label == "Haven"

    def test_concept_description_matches(
        self, parser: ConceptParser, rendered_haven: str
    ) -> None:
        result = parser.parse(rendered_haven)
        assert result.concept.description == "The personal knowledge management system."

    def test_empty_description_returns_empty_string(
        self, parser: ConceptParser, rendered_bare: str
    ) -> None:
        result = parser.parse(rendered_bare)
        assert result.concept.description == ""

    def test_created_at_matches(
        self, parser: ConceptParser, rendered_haven: str
    ) -> None:
        result = parser.parse(rendered_haven)
        assert result.concept.created_at == _CREATED_AT

    def test_aliases_is_tuple(
        self, parser: ConceptParser, rendered_haven: str
    ) -> None:
        result = parser.parse(rendered_haven)
        assert isinstance(result.concept.aliases, tuple)

    def test_aliases_content_matches(
        self, parser: ConceptParser, rendered_haven: str
    ) -> None:
        result = parser.parse(rendered_haven)
        assert set(result.concept.aliases) == {"HKMS", "Personal Second Brain"}

    def test_aliases_in_sorted_order(
        self, parser: ConceptParser, rendered_haven: str
    ) -> None:
        result = parser.parse(rendered_haven)
        aliases = list(result.concept.aliases)
        assert aliases == sorted(aliases)

    def test_empty_aliases_for_bare_concept(
        self, parser: ConceptParser, rendered_bare: str
    ) -> None:
        result = parser.parse(rendered_bare)
        assert result.concept.aliases == ()

    def test_concept_is_concept_instance(
        self, parser: ConceptParser, rendered_haven: str
    ) -> None:
        result = parser.parse(rendered_haven)
        assert isinstance(result.concept, Concept)


# ---------------------------------------------------------------------------
# TestParseRelationships
# ---------------------------------------------------------------------------


class TestParseRelationships:
    def test_relationship_count(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        result = parser.parse(rendered)
        assert len(result.relationships) == 1

    def test_relationship_is_relationship_instance(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        result = parser.parse(rendered)
        assert isinstance(result.relationships[0], Relationship)

    def test_relationship_id_matches(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        result = parser.parse(rendered)
        assert result.relationships[0].id == haven_uses_claude.id

    def test_relationship_source_id_matches(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        result = parser.parse(rendered)
        assert result.relationships[0].source_id == haven.id

    def test_relationship_target_id_matches(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
        claude: Concept,
    ) -> None:
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        result = parser.parse(rendered)
        assert result.relationships[0].target_id == claude.id

    def test_relationship_type_matches(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        result = parser.parse(rendered)
        assert result.relationships[0].relationship_type is OntologyRelationshipType.USES

    def test_relationship_confidence_matches(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        result = parser.parse(rendered)
        assert result.relationships[0].confidence == 1.0

    def test_multiple_relationships_count(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        dtu_id = concept_id("DTU")
        part_of = Relationship.create(haven.id, dtu_id, OntologyRelationshipType.PART_OF)
        rendered = writer.render(haven, relationships=[haven_uses_claude, part_of])
        result = parser.parse(rendered)
        assert len(result.relationships) == 2

    def test_relationships_order_preserved(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        dtu_id = concept_id("DTU")
        depends_on = Relationship.create(
            haven.id, dtu_id, OntologyRelationshipType.DEPENDS_ON
        )
        rendered = writer.render(
            haven, relationships=[haven_uses_claude, depends_on]
        )
        result = parser.parse(rendered)
        types = [r.relationship_type.value for r in result.relationships]
        assert types == sorted(types)

    def test_fractional_confidence_preserved(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        claude: Concept,
    ) -> None:
        rel = Relationship.create(
            haven.id, claude.id, OntologyRelationshipType.RELATED_TO, confidence=0.75
        )
        rendered = writer.render(haven, relationships=[rel])
        result = parser.parse(rendered)
        assert result.relationships[0].confidence == 0.75

    def test_incoming_relationship_reconstructed(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        claude: Concept,
        claude_created_by_anthropic: Relationship,
    ) -> None:
        rendered = writer.render(claude, relationships=[claude_created_by_anthropic])
        result = parser.parse(rendered)
        assert len(result.relationships) == 1
        assert result.relationships[0].target_id == claude.id


# ---------------------------------------------------------------------------
# TestParseAttachments
# ---------------------------------------------------------------------------


class TestParseAttachments:
    def test_attachment_count(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        attachment_a: Attachment,
    ) -> None:
        rendered = writer.render(haven, attachments=[attachment_a])
        result = parser.parse(rendered)
        assert len(result.attachments) == 1

    def test_attachment_is_attachment_instance(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        attachment_a: Attachment,
    ) -> None:
        rendered = writer.render(haven, attachments=[attachment_a])
        result = parser.parse(rendered)
        assert isinstance(result.attachments[0], Attachment)

    def test_attachment_id_matches(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        attachment_a: Attachment,
    ) -> None:
        rendered = writer.render(haven, attachments=[attachment_a])
        result = parser.parse(rendered)
        assert result.attachments[0].id == attachment_a.id

    def test_attachment_knowledge_object_id_matches(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        attachment_a: Attachment,
    ) -> None:
        rendered = writer.render(haven, attachments=[attachment_a])
        result = parser.parse(rendered)
        assert result.attachments[0].knowledge_object_id == _KO_ID_A

    def test_attachment_concept_id_matches(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        attachment_a: Attachment,
    ) -> None:
        rendered = writer.render(haven, attachments=[attachment_a])
        result = parser.parse(rendered)
        assert result.attachments[0].concept_id == haven.id

    def test_attachment_relevance_matches(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        attachment_a: Attachment,
    ) -> None:
        rendered = writer.render(haven, attachments=[attachment_a])
        result = parser.parse(rendered)
        assert result.attachments[0].relevance == 0.9

    def test_multiple_attachments_count(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        attachment_a: Attachment,
        attachment_b: Attachment,
    ) -> None:
        rendered = writer.render(haven, attachments=[attachment_a, attachment_b])
        result = parser.parse(rendered)
        assert len(result.attachments) == 2

    def test_attachments_order_preserved(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        attachment_a: Attachment,
        attachment_b: Attachment,
    ) -> None:
        rendered = writer.render(
            haven, attachments=[attachment_b, attachment_a]  # reversed input
        )
        result = parser.parse(rendered)
        ko_ids = [str(a.knowledge_object_id) for a in result.attachments]
        assert ko_ids == sorted(ko_ids)

    def test_zero_relevance_preserved(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
    ) -> None:
        att = Attachment.create(_KO_ID_A, haven.id, relevance=0.0)
        rendered = writer.render(haven, attachments=[att])
        result = parser.parse(rendered)
        assert result.attachments[0].relevance == 0.0


# ---------------------------------------------------------------------------
# TestParseUpdatedAt
# ---------------------------------------------------------------------------


class TestParseUpdatedAt:
    def test_updated_at_none_when_not_provided(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
    ) -> None:
        result = parser.parse(writer.render(haven))
        assert result.updated_at is None

    def test_updated_at_reconstructed_when_provided(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
    ) -> None:
        rendered = writer.render(haven, updated_at=_UPDATED_AT)
        result = parser.parse(rendered)
        assert result.updated_at == _UPDATED_AT

    def test_updated_at_different_values_preserved(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
    ) -> None:
        ts1 = datetime(2026, 1, 1, 0, 0, 0)
        ts2 = datetime(2026, 12, 31, 23, 59, 59)
        r1 = parser.parse(writer.render(haven, updated_at=ts1))
        r2 = parser.parse(writer.render(haven, updated_at=ts2))
        assert r1.updated_at == ts1
        assert r2.updated_at == ts2
        assert r1.updated_at != r2.updated_at


# ---------------------------------------------------------------------------
# TestRoundTrip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_bare_concept_round_trip(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        bare: Concept,
    ) -> None:
        result = parser.parse(writer.render(bare))
        assert result.concept == bare

    def test_haven_concept_round_trip(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
    ) -> None:
        result = parser.parse(writer.render(haven))
        assert result.concept == haven

    def test_round_trip_preserves_id(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
    ) -> None:
        result = parser.parse(writer.render(haven))
        assert result.concept.id == haven.id

    def test_round_trip_preserves_label(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
    ) -> None:
        result = parser.parse(writer.render(haven))
        assert result.concept.label == haven.label

    def test_round_trip_preserves_aliases(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
    ) -> None:
        result = parser.parse(writer.render(haven))
        assert set(result.concept.aliases) == set(haven.aliases)

    def test_round_trip_preserves_description(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
    ) -> None:
        result = parser.parse(writer.render(haven))
        assert result.concept.description == haven.description

    def test_round_trip_preserves_created_at(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
    ) -> None:
        result = parser.parse(writer.render(haven))
        assert result.concept.created_at == haven.created_at

    def test_round_trip_with_relationships(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        result = parser.parse(rendered)
        assert len(result.relationships) == 1
        assert result.relationships[0] == haven_uses_claude

    def test_round_trip_with_attachments(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        attachment_a: Attachment,
    ) -> None:
        rendered = writer.render(haven, attachments=[attachment_a])
        result = parser.parse(rendered)
        assert len(result.attachments) == 1
        assert result.attachments[0] == attachment_a

    def test_round_trip_with_updated_at(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
    ) -> None:
        rendered = writer.render(haven, updated_at=_UPDATED_AT)
        result = parser.parse(rendered)
        assert result.updated_at == _UPDATED_AT

    def test_full_round_trip(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
        attachment_a: Attachment,
    ) -> None:
        rendered = writer.render(
            haven,
            relationships=[haven_uses_claude],
            attachments=[attachment_a],
            updated_at=_UPDATED_AT,
        )
        result = parser.parse(rendered)
        assert result.concept == haven
        assert result.relationships == [haven_uses_claude]
        assert result.attachments == [attachment_a]
        assert result.updated_at == _UPDATED_AT

    def test_double_round_trip_is_byte_identical(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
        attachment_a: Attachment,
    ) -> None:
        rendered1 = writer.render(
            haven,
            relationships=[haven_uses_claude],
            attachments=[attachment_a],
            updated_at=_UPDATED_AT,
        )
        result = parser.parse(rendered1)
        rendered2 = writer.render(
            result.concept,
            relationships=result.relationships,
            attachments=result.attachments,
            updated_at=result.updated_at,
        )
        assert rendered1 == rendered2

    def test_round_trip_unicode_content(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
    ) -> None:
        c = Concept.from_label(
            "Göteborg",
            description="Swedish city with åäö characters",
            created_at=_CREATED_AT,
        )
        result = parser.parse(writer.render(c))
        assert result.concept.label == "Göteborg"
        assert "åäö" in result.concept.description

    def test_round_trip_multiple_relationships(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        dtu_id = concept_id("DTU")
        depends = Relationship.create(
            haven.id, dtu_id, OntologyRelationshipType.DEPENDS_ON
        )
        rendered = writer.render(haven, relationships=[haven_uses_claude, depends])
        result = parser.parse(rendered)
        assert len(result.relationships) == 2
        types = {r.relationship_type for r in result.relationships}
        assert types == {OntologyRelationshipType.USES, OntologyRelationshipType.DEPENDS_ON}

    def test_round_trip_multiple_attachments(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        attachment_a: Attachment,
        attachment_b: Attachment,
    ) -> None:
        rendered = writer.render(haven, attachments=[attachment_a, attachment_b])
        result = parser.parse(rendered)
        assert len(result.attachments) == 2
        ko_ids = {a.knowledge_object_id for a in result.attachments}
        assert ko_ids == {_KO_ID_A, _KO_ID_B}


# ---------------------------------------------------------------------------
# TestParseStructuralErrors
# ---------------------------------------------------------------------------


class TestParseStructuralErrors:
    def test_empty_string_raises(self, parser: ConceptParser) -> None:
        with pytest.raises(ConceptParseError, match="does not begin"):
            parser.parse("")

    def test_no_opening_fence_raises(self, parser: ConceptParser) -> None:
        with pytest.raises(ConceptParseError, match="does not begin"):
            parser.parse("id: abc\nlabel: Test\n")

    def test_no_closing_fence_raises(self, parser: ConceptParser) -> None:
        with pytest.raises(ConceptParseError, match="missing the closing"):
            parser.parse("---\nid: abc\nlabel: Test\n")

    def test_invalid_yaml_raises(self, parser: ConceptParser) -> None:
        bad = "---\n: bad: yaml: {unclosed\n---\n\n# title\n"
        with pytest.raises(ConceptParseError, match="syntactically invalid"):
            parser.parse(bad)

    def test_yaml_not_mapping_raises(self, parser: ConceptParser) -> None:
        bad = "---\n- item1\n- item2\n---\n\n# title\n"
        with pytest.raises(ConceptParseError, match="must be a YAML mapping"):
            parser.parse(bad)

    def test_parse_error_is_value_error_subclass(
        self, parser: ConceptParser
    ) -> None:
        with pytest.raises(ValueError):
            parser.parse("")


# ---------------------------------------------------------------------------
# TestParseTopLevelFieldErrors
# ---------------------------------------------------------------------------


class TestParseTopLevelFieldErrors:
    def _make_minimal(self, overrides: dict) -> str:
        """Return a minimal valid rendered doc, then apply YAML overrides."""
        fm: dict = {
            "id": str(concept_id("Test")),
            "label": "Test",
            "aliases": [],
            "description": "",
            "created_at": _CREATED_AT.isoformat(),
            "updated_at": None,
            "relationships": [],
            "attachments": [],
        }
        fm.update(overrides)
        yaml_str = yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True)
        return f"---\n{yaml_str}---\n\n# Test\n"

    def test_missing_id_raises(self, parser: ConceptParser) -> None:
        fm = {
            "label": "Test",
            "aliases": [],
            "description": "",
            "created_at": _CREATED_AT.isoformat(),
            "updated_at": None,
            "relationships": [],
            "attachments": [],
        }
        yaml_str = yaml.dump(fm, default_flow_style=False, sort_keys=False)
        doc = f"---\n{yaml_str}---\n\n# Test\n"
        with pytest.raises(ConceptParseError, match="missing required field"):
            parser.parse(doc)

    def test_missing_label_raises(self, parser: ConceptParser) -> None:
        fm = {
            "id": str(concept_id("Test")),
            "aliases": [],
            "description": "",
            "created_at": _CREATED_AT.isoformat(),
            "updated_at": None,
            "relationships": [],
            "attachments": [],
        }
        yaml_str = yaml.dump(fm, default_flow_style=False, sort_keys=False)
        doc = f"---\n{yaml_str}---\n\n# Test\n"
        with pytest.raises(ConceptParseError, match="missing required field"):
            parser.parse(doc)

    def test_missing_created_at_raises(self, parser: ConceptParser) -> None:
        fm = {
            "id": str(concept_id("Test")),
            "label": "Test",
            "aliases": [],
            "description": "",
            "updated_at": None,
            "relationships": [],
            "attachments": [],
        }
        yaml_str = yaml.dump(fm, default_flow_style=False, sort_keys=False)
        doc = f"---\n{yaml_str}---\n\n# Test\n"
        with pytest.raises(ConceptParseError, match="missing required field"):
            parser.parse(doc)

    def test_invalid_uuid_raises(self, parser: ConceptParser) -> None:
        doc = self._make_minimal({"id": "not-a-uuid"})
        with pytest.raises(ConceptParseError, match="not a valid UUID"):
            parser.parse(doc)

    def test_empty_label_raises(self, parser: ConceptParser) -> None:
        doc = self._make_minimal({"label": "   "})
        with pytest.raises(ConceptParseError, match="non-empty string"):
            parser.parse(doc)

    def test_invalid_created_at_raises(self, parser: ConceptParser) -> None:
        doc = self._make_minimal({"created_at": "not-a-datetime"})
        with pytest.raises(ConceptParseError, match="not a valid ISO datetime"):
            parser.parse(doc)

    def test_aliases_not_list_raises(self, parser: ConceptParser) -> None:
        doc = self._make_minimal({"aliases": "single-alias"})
        with pytest.raises(ConceptParseError, match="'aliases' must be a list"):
            parser.parse(doc)

    def test_non_string_alias_raises(self, parser: ConceptParser) -> None:
        doc = self._make_minimal({"aliases": [123, "valid"]})
        with pytest.raises(ConceptParseError, match="must be a string"):
            parser.parse(doc)


# ---------------------------------------------------------------------------
# TestParseRelationshipErrors
# ---------------------------------------------------------------------------


class TestParseRelationshipErrors:
    def _make_with_relationships(
        self, parser: ConceptParser, rel_entries: list
    ) -> None:
        fm = {
            "id": str(concept_id("Test")),
            "label": "Test",
            "aliases": [],
            "description": "",
            "created_at": _CREATED_AT.isoformat(),
            "updated_at": None,
            "relationships": rel_entries,
            "attachments": [],
        }
        yaml_str = yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True)
        doc = f"---\n{yaml_str}---\n\n# Test\n"
        parser.parse(doc)

    def _valid_rel_entry(self) -> dict:
        src = concept_id("A")
        tgt = concept_id("B")
        from obsidian.ontology.identity import relationship_id
        rid = relationship_id(src, tgt, "uses")
        return {
            "id": str(rid),
            "source_id": str(src),
            "target_id": str(tgt),
            "relationship_type": "uses",
            "confidence": 1.0,
        }

    def test_relationships_not_list_raises(self, parser: ConceptParser) -> None:
        fm = {
            "id": str(concept_id("Test")),
            "label": "Test",
            "aliases": [],
            "description": "",
            "created_at": _CREATED_AT.isoformat(),
            "updated_at": None,
            "relationships": "not-a-list",
            "attachments": [],
        }
        yaml_str = yaml.dump(fm, default_flow_style=False, sort_keys=False)
        doc = f"---\n{yaml_str}---\n\n# Test\n"
        with pytest.raises(ConceptParseError, match="'relationships' must be a list"):
            parser.parse(doc)

    def test_relationship_entry_not_dict_raises(
        self, parser: ConceptParser
    ) -> None:
        with pytest.raises(ConceptParseError, match="must be a mapping"):
            self._make_with_relationships(parser, ["not-a-dict"])

    def test_missing_relationship_id_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_rel_entry()
        del entry["id"]
        with pytest.raises(ConceptParseError, match="missing required field"):
            self._make_with_relationships(parser, [entry])

    def test_missing_source_id_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_rel_entry()
        del entry["source_id"]
        with pytest.raises(ConceptParseError, match="missing required field"):
            self._make_with_relationships(parser, [entry])

    def test_missing_target_id_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_rel_entry()
        del entry["target_id"]
        with pytest.raises(ConceptParseError, match="missing required field"):
            self._make_with_relationships(parser, [entry])

    def test_missing_relationship_type_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_rel_entry()
        del entry["relationship_type"]
        with pytest.raises(ConceptParseError, match="missing required field"):
            self._make_with_relationships(parser, [entry])

    def test_missing_confidence_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_rel_entry()
        del entry["confidence"]
        with pytest.raises(ConceptParseError, match="missing required field"):
            self._make_with_relationships(parser, [entry])

    def test_invalid_relationship_type_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_rel_entry()
        entry["relationship_type"] = "invented_type"
        with pytest.raises(ConceptParseError, match="not a recognised relationship type"):
            self._make_with_relationships(parser, [entry])

    def test_invalid_uuid_in_relationship_raises(
        self, parser: ConceptParser
    ) -> None:
        entry = self._valid_rel_entry()
        entry["source_id"] = "not-a-uuid"
        with pytest.raises(ConceptParseError, match="not a valid UUID"):
            self._make_with_relationships(parser, [entry])

    def test_confidence_out_of_range_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_rel_entry()
        entry["confidence"] = 2.0
        with pytest.raises(ConceptParseError, match="Cannot construct relationships"):
            self._make_with_relationships(parser, [entry])

    def test_self_loop_relationship_raises(self, parser: ConceptParser) -> None:
        same_id = concept_id("A")
        from obsidian.ontology.identity import relationship_id
        entry = {
            "id": str(relationship_id(same_id, same_id, "uses")),
            "source_id": str(same_id),
            "target_id": str(same_id),
            "relationship_type": "uses",
            "confidence": 1.0,
        }
        with pytest.raises(ConceptParseError, match="Cannot construct relationships"):
            self._make_with_relationships(parser, [entry])

    def test_non_numeric_confidence_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_rel_entry()
        entry["confidence"] = "high"
        with pytest.raises(ConceptParseError, match="must be a number"):
            self._make_with_relationships(parser, [entry])


# ---------------------------------------------------------------------------
# TestParseAttachmentErrors
# ---------------------------------------------------------------------------


class TestParseAttachmentErrors:
    def _make_with_attachments(
        self, parser: ConceptParser, att_entries: list
    ) -> None:
        fm = {
            "id": str(concept_id("Test")),
            "label": "Test",
            "aliases": [],
            "description": "",
            "created_at": _CREATED_AT.isoformat(),
            "updated_at": None,
            "relationships": [],
            "attachments": att_entries,
        }
        yaml_str = yaml.dump(fm, default_flow_style=False, sort_keys=False, allow_unicode=True)
        doc = f"---\n{yaml_str}---\n\n# Test\n"
        parser.parse(doc)

    def _valid_att_entry(self, concept: Concept | None = None) -> dict:
        cid = concept_id("Test") if concept is None else concept.id
        from obsidian.ontology.identity import attachment_id
        aid = attachment_id(_KO_ID_A, cid)
        return {
            "id": str(aid),
            "knowledge_object_id": str(_KO_ID_A),
            "concept_id": str(cid),
            "relevance": 0.9,
        }

    def test_attachments_not_list_raises(self, parser: ConceptParser) -> None:
        fm = {
            "id": str(concept_id("Test")),
            "label": "Test",
            "aliases": [],
            "description": "",
            "created_at": _CREATED_AT.isoformat(),
            "updated_at": None,
            "relationships": [],
            "attachments": "not-a-list",
        }
        yaml_str = yaml.dump(fm, default_flow_style=False, sort_keys=False)
        doc = f"---\n{yaml_str}---\n\n# Test\n"
        with pytest.raises(ConceptParseError, match="'attachments' must be a list"):
            parser.parse(doc)

    def test_attachment_entry_not_dict_raises(
        self, parser: ConceptParser
    ) -> None:
        with pytest.raises(ConceptParseError, match="must be a mapping"):
            self._make_with_attachments(parser, ["not-a-dict"])

    def test_missing_attachment_id_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_att_entry()
        del entry["id"]
        with pytest.raises(ConceptParseError, match="missing required field"):
            self._make_with_attachments(parser, [entry])

    def test_missing_knowledge_object_id_raises(
        self, parser: ConceptParser
    ) -> None:
        entry = self._valid_att_entry()
        del entry["knowledge_object_id"]
        with pytest.raises(ConceptParseError, match="missing required field"):
            self._make_with_attachments(parser, [entry])

    def test_missing_concept_id_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_att_entry()
        del entry["concept_id"]
        with pytest.raises(ConceptParseError, match="missing required field"):
            self._make_with_attachments(parser, [entry])

    def test_missing_relevance_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_att_entry()
        del entry["relevance"]
        with pytest.raises(ConceptParseError, match="missing required field"):
            self._make_with_attachments(parser, [entry])

    def test_invalid_uuid_in_attachment_raises(
        self, parser: ConceptParser
    ) -> None:
        entry = self._valid_att_entry()
        entry["knowledge_object_id"] = "not-a-uuid"
        with pytest.raises(ConceptParseError, match="not a valid UUID"):
            self._make_with_attachments(parser, [entry])

    def test_relevance_out_of_range_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_att_entry()
        entry["relevance"] = 1.5
        with pytest.raises(ConceptParseError, match="Cannot construct attachments"):
            self._make_with_attachments(parser, [entry])

    def test_negative_relevance_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_att_entry()
        entry["relevance"] = -0.1
        with pytest.raises(ConceptParseError, match="Cannot construct attachments"):
            self._make_with_attachments(parser, [entry])

    def test_non_numeric_relevance_raises(self, parser: ConceptParser) -> None:
        entry = self._valid_att_entry()
        entry["relevance"] = "high"
        with pytest.raises(ConceptParseError, match="must be a number"):
            self._make_with_attachments(parser, [entry])


# ---------------------------------------------------------------------------
# TestParseDeterminism
# ---------------------------------------------------------------------------


class TestParseDeterminism:
    def test_same_text_same_result(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
    ) -> None:
        rendered = writer.render(haven)
        r1 = parser.parse(rendered)
        r2 = parser.parse(rendered)
        assert r1.concept == r2.concept
        assert r1.relationships == r2.relationships
        assert r1.attachments == r2.attachments
        assert r1.updated_at == r2.updated_at

    def test_same_text_same_result_with_collections(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
        attachment_a: Attachment,
    ) -> None:
        rendered = writer.render(
            haven,
            relationships=[haven_uses_claude],
            attachments=[attachment_a],
            updated_at=_UPDATED_AT,
        )
        r1 = parser.parse(rendered)
        r2 = parser.parse(rendered)
        assert r1.concept == r2.concept
        assert r1.relationships == r2.relationships
        assert r1.attachments == r2.attachments
        assert r1.updated_at == r2.updated_at

    def test_different_concepts_different_results(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        claude: Concept,
    ) -> None:
        r_haven = parser.parse(writer.render(haven))
        r_claude = parser.parse(writer.render(claude))
        assert r_haven.concept != r_claude.concept

    def test_input_list_order_does_not_affect_parse(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        attachment_a: Attachment,
        attachment_b: Attachment,
    ) -> None:
        r_ab = parser.parse(writer.render(haven, attachments=[attachment_a, attachment_b]))
        r_ba = parser.parse(writer.render(haven, attachments=[attachment_b, attachment_a]))
        assert r_ab.attachments == r_ba.attachments


# ---------------------------------------------------------------------------
# TestReadFromFile
# ---------------------------------------------------------------------------


class TestReadFromFile:
    def test_read_existing_file(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        tmp_path: Path,
    ) -> None:
        path = writer.write(haven, tmp_path)
        result = parser.read(path)
        assert result.concept == haven

    def test_read_returns_concept_parse_result(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        tmp_path: Path,
    ) -> None:
        path = writer.write(haven, tmp_path)
        result = parser.read(path)
        assert isinstance(result, ConceptParseResult)

    def test_read_nonexistent_file_raises(
        self, parser: ConceptParser, tmp_path: Path
    ) -> None:
        missing = tmp_path / "does-not-exist.md"
        with pytest.raises(ConceptParseError, match="File not found"):
            parser.read(missing)

    def test_read_matches_parse(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        tmp_path: Path,
    ) -> None:
        path = writer.write(haven, tmp_path)
        rendered = writer.render(haven)
        r_read = parser.read(path)
        r_parse = parser.parse(rendered)
        assert r_read.concept == r_parse.concept

    def test_read_full_file_with_collections(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        haven: Concept,
        haven_uses_claude: Relationship,
        attachment_a: Attachment,
        tmp_path: Path,
    ) -> None:
        path = writer.write(
            haven,
            tmp_path,
            relationships=[haven_uses_claude],
            attachments=[attachment_a],
            updated_at=_UPDATED_AT,
        )
        result = parser.read(path)
        assert result.concept == haven
        assert len(result.relationships) == 1
        assert len(result.attachments) == 1
        assert result.updated_at == _UPDATED_AT

    def test_read_utf8_file(
        self,
        writer: ConceptWriter,
        parser: ConceptParser,
        tmp_path: Path,
    ) -> None:
        c = Concept.from_label(
            "Göteborg",
            description="Swedish city: åäö",
            created_at=_CREATED_AT,
        )
        path = writer.write(c, tmp_path)
        result = parser.read(path)
        assert result.concept.label == "Göteborg"
        assert "åäö" in result.concept.description
