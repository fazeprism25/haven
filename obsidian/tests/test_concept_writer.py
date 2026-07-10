"""Unit tests for obsidian.ontology.concept_writer – Phase 2A/2B.

Tests are grouped by concern:

* :class:`TestConceptWriterInstantiation` — stateless construction.
* :class:`TestRenderStructure` — overall Markdown shape.
* :class:`TestRenderFrontmatterValues` — every YAML field.
* :class:`TestRenderFrontmatterYAML` — YAML validity and parseability.
* :class:`TestRenderBody` — H1 title, description, section headings.
* :class:`TestRenderRelationships` — direction, sorting, float format.
* :class:`TestRenderAttachments` — sorting, float format.
* :class:`TestRenderEmptyCollections` — ``_None._`` sentinels.
* :class:`TestRenderDeterminism` — byte-identical output guarantee.
* :class:`TestRenderUpdatedAt` — null / ISO handling.
* :class:`TestRenderAliasesSorted` — alias ordering.
* :class:`TestWrite` — filesystem operations.

All tests are deterministic.  Fixed timestamps and fixed UUIDs are used
throughout so that assertions never depend on ``datetime.utcnow()``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from obsidian.ontology.concept_writer import ConceptWriter
from obsidian.ontology.enums import OntologyRelationshipType
from obsidian.ontology.identity import concept_id
from obsidian.ontology.models import Attachment, Concept, Relationship


# ---------------------------------------------------------------------------
# Fixed test data
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
    """Concept with no aliases, no description."""
    return Concept.from_label("Bare", created_at=_CREATED_AT)


@pytest.fixture
def haven_uses_claude(haven: Concept, claude: Concept) -> Relationship:
    return Relationship.create(haven.id, claude.id, OntologyRelationshipType.USES)


@pytest.fixture
def claude_created_by_anthropic(claude: Concept) -> Relationship:
    anthropic_id = concept_id("Anthropic")
    return Relationship.create(
        anthropic_id, claude.id, OntologyRelationshipType.CREATED_BY
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


# ---------------------------------------------------------------------------
# TestConceptWriterInstantiation
# ---------------------------------------------------------------------------


class TestConceptWriterInstantiation:
    def test_instantiates_without_arguments(self) -> None:
        cw = ConceptWriter()
        assert isinstance(cw, ConceptWriter)

    def test_has_render_method(self) -> None:
        assert callable(ConceptWriter().render)

    def test_has_write_method(self) -> None:
        assert callable(ConceptWriter().write)

    def test_no_graph_attribute(self) -> None:
        assert not hasattr(ConceptWriter(), "graph")

    def test_no_retrieve_attribute(self) -> None:
        assert not hasattr(ConceptWriter(), "retrieve")

    def test_no_index_attribute(self) -> None:
        assert not hasattr(ConceptWriter(), "index")


# ---------------------------------------------------------------------------
# TestRenderStructure
# ---------------------------------------------------------------------------


class TestRenderStructure:
    def test_returns_str(self, rendered_bare: str) -> None:
        assert isinstance(rendered_bare, str)

    def test_starts_with_yaml_open_fence(self, rendered_bare: str) -> None:
        assert rendered_bare.startswith("---\n")

    def test_ends_with_single_newline(self, rendered_bare: str) -> None:
        assert rendered_bare.endswith("\n")
        assert not rendered_bare.endswith("\n\n")

    def test_contains_yaml_close_fence(self, rendered_bare: str) -> None:
        assert "\n---\n" in rendered_bare

    def test_body_follows_close_fence(self, rendered_bare: str) -> None:
        # After "---\n\n" the body begins
        assert "\n---\n\n#" in rendered_bare

    def test_contains_relationships_heading(self, rendered_bare: str) -> None:
        assert "## Relationships" in rendered_bare

    def test_contains_attachments_heading(self, rendered_bare: str) -> None:
        assert "## Attachments" in rendered_bare

    def test_sections_in_order(self, rendered_haven: str) -> None:
        rel_pos = rendered_haven.index("## Relationships")
        att_pos = rendered_haven.index("## Attachments")
        assert rel_pos < att_pos

    def test_title_before_relationships(self, rendered_haven: str) -> None:
        title_pos = rendered_haven.index("# Haven")
        rel_pos = rendered_haven.index("## Relationships")
        assert title_pos < rel_pos

    def test_no_trailing_blank_lines(self, rendered_bare: str) -> None:
        # The final character must be '\n' but the second-to-last must not be '\n'
        assert rendered_bare[-1] == "\n"
        assert rendered_bare[-2] != "\n"


# ---------------------------------------------------------------------------
# TestRenderFrontmatterValues
# ---------------------------------------------------------------------------


class TestRenderFrontmatterValues:
    def _fm(self, rendered: str) -> dict:
        """Extract and parse YAML frontmatter from *rendered*."""
        _, fm_block, _ = rendered.split("---\n", 2)
        return yaml.safe_load(fm_block)

    def test_id_is_uuid_string(self, writer: ConceptWriter, haven: Concept) -> None:
        fm = self._fm(writer.render(haven))
        UUID(fm["id"])  # must not raise

    def test_id_matches_concept_id(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        fm = self._fm(writer.render(haven))
        assert fm["id"] == str(haven.id)

    def test_label_matches(self, writer: ConceptWriter, haven: Concept) -> None:
        fm = self._fm(writer.render(haven))
        assert fm["label"] == "Haven"

    def test_description_matches(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        fm = self._fm(writer.render(haven))
        assert fm["description"] == "The personal knowledge management system."

    def test_empty_description_is_empty_string(
        self, writer: ConceptWriter, bare: Concept
    ) -> None:
        fm = self._fm(writer.render(bare))
        assert fm["description"] == ""

    def test_created_at_matches(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        fm = self._fm(writer.render(haven))
        assert datetime.fromisoformat(fm["created_at"]) == _CREATED_AT

    def test_updated_at_is_null_by_default(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        fm = self._fm(writer.render(haven))
        assert fm["updated_at"] is None

    def test_updated_at_stored_when_provided(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        fm = self._fm(writer.render(haven, updated_at=_UPDATED_AT))
        assert datetime.fromisoformat(fm["updated_at"]) == _UPDATED_AT

    def test_aliases_is_list(self, writer: ConceptWriter, haven: Concept) -> None:
        fm = self._fm(writer.render(haven))
        assert isinstance(fm["aliases"], list)

    def test_aliases_content(self, writer: ConceptWriter, haven: Concept) -> None:
        fm = self._fm(writer.render(haven))
        assert set(fm["aliases"]) == {"HKMS", "Personal Second Brain"}

    def test_aliases_empty_for_bare_concept(
        self, writer: ConceptWriter, bare: Concept
    ) -> None:
        fm = self._fm(writer.render(bare))
        assert fm["aliases"] == []

    def test_relationships_is_list(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        fm = self._fm(writer.render(haven))
        assert isinstance(fm["relationships"], list)

    def test_no_relationships_gives_empty_list(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        fm = self._fm(writer.render(haven))
        assert fm["relationships"] == []

    def test_relationship_entry_keys(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        fm = self._fm(writer.render(haven, relationships=[haven_uses_claude]))
        r = fm["relationships"][0]
        assert set(r.keys()) == {"id", "source_id", "target_id", "relationship_type", "confidence"}

    def test_relationship_entry_values(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        fm = self._fm(writer.render(haven, relationships=[haven_uses_claude]))
        r = fm["relationships"][0]
        assert r["relationship_type"] == "uses"
        assert r["source_id"] == str(haven.id)
        assert r["confidence"] == 1.0

    def test_attachments_is_list(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        fm = self._fm(writer.render(haven))
        assert isinstance(fm["attachments"], list)

    def test_no_attachments_gives_empty_list(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        fm = self._fm(writer.render(haven))
        assert fm["attachments"] == []

    def test_attachment_entry_keys(
        self,
        writer: ConceptWriter,
        haven: Concept,
        attachment_a: Attachment,
    ) -> None:
        fm = self._fm(writer.render(haven, attachments=[attachment_a]))
        a = fm["attachments"][0]
        assert set(a.keys()) == {"id", "knowledge_object_id", "concept_id", "relevance"}

    def test_attachment_entry_values(
        self,
        writer: ConceptWriter,
        haven: Concept,
        attachment_a: Attachment,
    ) -> None:
        fm = self._fm(writer.render(haven, attachments=[attachment_a]))
        a = fm["attachments"][0]
        assert a["knowledge_object_id"] == str(_KO_ID_A)
        assert a["concept_id"] == str(haven.id)
        assert a["relevance"] == 0.9


# ---------------------------------------------------------------------------
# TestRenderFrontmatterYAML
# ---------------------------------------------------------------------------


class TestRenderFrontmatterYAML:
    def test_frontmatter_parses_as_valid_yaml(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        rendered = writer.render(haven)
        _, fm_block, _ = rendered.split("---\n", 2)
        result = yaml.safe_load(fm_block)
        assert isinstance(result, dict)

    def test_frontmatter_has_eight_top_level_keys(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        rendered = writer.render(haven)
        _, fm_block, _ = rendered.split("---\n", 2)
        fm = yaml.safe_load(fm_block)
        assert set(fm.keys()) == {
            "id",
            "label",
            "aliases",
            "description",
            "created_at",
            "updated_at",
            "relationships",
            "attachments",
        }

    def test_full_round_trip_with_relationships_and_attachments(
        self,
        writer: ConceptWriter,
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
        _, fm_block, _ = rendered.split("---\n", 2)
        fm = yaml.safe_load(fm_block)
        assert len(fm["relationships"]) == 1
        assert len(fm["attachments"]) == 1
        assert fm["updated_at"] is not None


# ---------------------------------------------------------------------------
# TestRenderBody
# ---------------------------------------------------------------------------


class TestRenderBody:
    def _body(self, rendered: str) -> str:
        parts = rendered.split("---\n", 2)
        return parts[2]

    def test_h1_title_present(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        body = self._body(writer.render(haven))
        assert "# Haven\n" in body

    def test_h1_title_for_bare_concept(
        self, writer: ConceptWriter, bare: Concept
    ) -> None:
        body = self._body(writer.render(bare))
        assert "# Bare\n" in body

    def test_description_present_when_non_empty(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        body = self._body(writer.render(haven))
        assert "The personal knowledge management system." in body

    def test_description_absent_when_empty(
        self, writer: ConceptWriter, bare: Concept
    ) -> None:
        body = self._body(writer.render(bare))
        # No description paragraph between title and ## Relationships
        h1_pos = body.index("# Bare")
        rel_pos = body.index("## Relationships")
        between = body[h1_pos + len("# Bare"):rel_pos]
        # The only content should be blank lines
        assert between.strip() == ""

    def test_relationships_heading_present(
        self, writer: ConceptWriter, bare: Concept
    ) -> None:
        body = self._body(writer.render(bare))
        assert "## Relationships\n" in body

    def test_attachments_heading_present(
        self, writer: ConceptWriter, bare: Concept
    ) -> None:
        body = self._body(writer.render(bare))
        assert "## Attachments\n" in body


# ---------------------------------------------------------------------------
# TestRenderEmptyCollections
# ---------------------------------------------------------------------------


class TestRenderEmptyCollections:
    def test_no_relationships_shows_none_sentinel(
        self, writer: ConceptWriter, bare: Concept
    ) -> None:
        rendered = writer.render(bare)
        assert "_None._" in rendered

    def test_no_attachments_shows_none_sentinel(
        self, writer: ConceptWriter, bare: Concept
    ) -> None:
        rendered = writer.render(bare)
        assert "_None._" in rendered

    def test_relationships_present_no_none_sentinel(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        # _None._ should not appear in the relationships section
        lines = rendered.splitlines()
        rel_start = next(i for i, l in enumerate(lines) if l == "## Relationships")
        att_start = next(i for i, l in enumerate(lines) if l == "## Attachments")
        relationship_block = "\n".join(lines[rel_start:att_start])
        assert "_None._" not in relationship_block

    def test_attachments_present_no_none_sentinel(
        self,
        writer: ConceptWriter,
        haven: Concept,
        attachment_a: Attachment,
    ) -> None:
        rendered = writer.render(haven, attachments=[attachment_a])
        lines = rendered.splitlines()
        att_start = next(i for i, l in enumerate(lines) if l == "## Attachments")
        attachment_block = "\n".join(lines[att_start:])
        assert "_None._" not in attachment_block

    def test_none_list_same_as_empty_list(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        out_none = writer.render(haven, relationships=None, attachments=None)
        out_empty = writer.render(haven, relationships=[], attachments=[])
        assert out_none == out_empty


# ---------------------------------------------------------------------------
# TestRenderRelationships
# ---------------------------------------------------------------------------


class TestRenderRelationships:
    def test_outgoing_uses_right_arrow(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        assert "→" in rendered

    def test_outgoing_shows_target_id(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
        claude: Concept,
    ) -> None:
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        assert str(claude.id) in rendered

    def test_incoming_uses_left_arrow(
        self,
        writer: ConceptWriter,
        claude: Concept,
        claude_created_by_anthropic: Relationship,
    ) -> None:
        rendered = writer.render(claude, relationships=[claude_created_by_anthropic])
        assert "←" in rendered

    def test_incoming_shows_source_id(
        self,
        writer: ConceptWriter,
        claude: Concept,
        claude_created_by_anthropic: Relationship,
    ) -> None:
        rendered = writer.render(claude, relationships=[claude_created_by_anthropic])
        assert str(claude_created_by_anthropic.source_id) in rendered

    def test_relationship_type_uppercase_in_body(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        assert "`USES`" in rendered

    def test_confidence_four_decimal_places(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        assert "confidence: 1.0000" in rendered

    def test_multiple_relationships_all_present(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
        claude: Concept,
    ) -> None:
        dtu_id = concept_id("DTU")
        part_of = Relationship.create(haven.id, dtu_id, OntologyRelationshipType.PART_OF)
        rendered = writer.render(haven, relationships=[haven_uses_claude, part_of])
        assert "`USES`" in rendered
        assert "`PART_OF`" in rendered

    def test_relationships_sorted_in_body(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
        claude: Concept,
    ) -> None:
        dtu_id = concept_id("DTU")
        depends_on = Relationship.create(
            haven.id, dtu_id, OntologyRelationshipType.DEPENDS_ON
        )
        # DEPENDS_ON sorts before USES lexicographically
        rendered = writer.render(
            haven, relationships=[haven_uses_claude, depends_on]
        )
        dep_pos = rendered.index("`DEPENDS_ON`")
        uses_pos = rendered.index("`USES`")
        assert dep_pos < uses_pos

    def test_relationships_sorted_in_frontmatter(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
        claude: Concept,
    ) -> None:
        dtu_id = concept_id("DTU")
        depends_on = Relationship.create(
            haven.id, dtu_id, OntologyRelationshipType.DEPENDS_ON
        )
        rendered = writer.render(
            haven, relationships=[haven_uses_claude, depends_on]
        )
        _, fm_block, _ = rendered.split("---\n", 2)
        fm = yaml.safe_load(fm_block)
        types = [r["relationship_type"] for r in fm["relationships"]]
        assert types == sorted(types)

    def test_relationship_count_in_frontmatter(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
        claude: Concept,
    ) -> None:
        dtu_id = concept_id("DTU")
        part_of = Relationship.create(haven.id, dtu_id, OntologyRelationshipType.PART_OF)
        rendered = writer.render(
            haven, relationships=[haven_uses_claude, part_of]
        )
        _, fm_block, _ = rendered.split("---\n", 2)
        fm = yaml.safe_load(fm_block)
        assert len(fm["relationships"]) == 2

    def test_outgoing_target_id_is_wiki_link(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
        claude: Concept,
    ) -> None:
        """The other Concept's id resolves as a real link, not inert text.

        Concept notes are filed by UUID, so ``[[<target_id>]]`` resolves
        directly — this is what makes a Concept note a clickable jumping-off
        point to its neighbouring Concepts.
        """
        rendered = writer.render(haven, relationships=[haven_uses_claude])
        assert f"[[{claude.id}]]" in rendered

    def test_incoming_source_id_is_wiki_link(
        self,
        writer: ConceptWriter,
        claude: Concept,
        claude_created_by_anthropic: Relationship,
    ) -> None:
        rendered = writer.render(claude, relationships=[claude_created_by_anthropic])
        assert f"[[{claude_created_by_anthropic.source_id}]]" in rendered


# ---------------------------------------------------------------------------
# TestRenderAttachments
# ---------------------------------------------------------------------------


class TestRenderAttachments:
    def test_attachment_ko_id_in_body(
        self,
        writer: ConceptWriter,
        haven: Concept,
        attachment_a: Attachment,
    ) -> None:
        rendered = writer.render(haven, attachments=[attachment_a])
        assert str(_KO_ID_A) in rendered

    def test_attachment_ko_id_is_wiki_link(
        self,
        writer: ConceptWriter,
        haven: Concept,
        attachment_a: Attachment,
    ) -> None:
        """Memory notes are filed by UUID, so ``[[<knowledge_object_id>]]``
        resolves directly — this is what makes a Concept note a real,
        clickable jumping-off point to every Memory attached to it (the
        reverse of the Memory→Concept links VaultWriter already emits)."""
        rendered = writer.render(haven, attachments=[attachment_a])
        assert f"[[{_KO_ID_A}|Evidence]]" in rendered

    def test_relevance_four_decimal_places(
        self,
        writer: ConceptWriter,
        haven: Concept,
        attachment_a: Attachment,
    ) -> None:
        rendered = writer.render(haven, attachments=[attachment_a])
        assert "relevance: 0.9000" in rendered

    def test_relevance_zero_formatted(
        self,
        writer: ConceptWriter,
        haven: Concept,
    ) -> None:
        a = Attachment.create(_KO_ID_A, haven.id, relevance=0.0)
        rendered = writer.render(haven, attachments=[a])
        assert "relevance: 0.0000" in rendered

    def test_multiple_attachments_all_present(
        self,
        writer: ConceptWriter,
        haven: Concept,
        attachment_a: Attachment,
        attachment_b: Attachment,
    ) -> None:
        rendered = writer.render(
            haven, attachments=[attachment_a, attachment_b]
        )
        assert str(_KO_ID_A) in rendered
        assert str(_KO_ID_B) in rendered

    def test_attachments_sorted_by_ko_id(
        self,
        writer: ConceptWriter,
        haven: Concept,
        attachment_a: Attachment,
        attachment_b: Attachment,
    ) -> None:
        # _KO_ID_A ("aaa...") sorts before _KO_ID_B ("bbb...")
        rendered = writer.render(
            haven, attachments=[attachment_b, attachment_a]  # reversed input
        )
        pos_a = rendered.index(str(_KO_ID_A))
        pos_b = rendered.index(str(_KO_ID_B))
        assert pos_a < pos_b

    def test_attachments_sorted_in_frontmatter(
        self,
        writer: ConceptWriter,
        haven: Concept,
        attachment_a: Attachment,
        attachment_b: Attachment,
    ) -> None:
        rendered = writer.render(
            haven, attachments=[attachment_b, attachment_a]
        )
        _, fm_block, _ = rendered.split("---\n", 2)
        fm = yaml.safe_load(fm_block)
        ko_ids = [a["knowledge_object_id"] for a in fm["attachments"]]
        assert ko_ids == sorted(ko_ids)


# ---------------------------------------------------------------------------
# TestRenderUpdatedAt
# ---------------------------------------------------------------------------


class TestRenderUpdatedAt:
    def test_null_when_not_supplied(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        rendered = writer.render(haven)
        assert "updated_at: null" in rendered or "updated_at:\n" in rendered

    def test_iso_string_when_supplied(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        rendered = writer.render(haven, updated_at=_UPDATED_AT)
        assert _UPDATED_AT.isoformat() in rendered

    def test_two_different_updated_at_produce_different_output(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        ts1 = datetime(2026, 1, 1, 0, 0, 0)
        ts2 = datetime(2026, 6, 30, 0, 0, 0)
        out1 = writer.render(haven, updated_at=ts1)
        out2 = writer.render(haven, updated_at=ts2)
        assert out1 != out2


# ---------------------------------------------------------------------------
# TestRenderAliasesSorted
# ---------------------------------------------------------------------------


class TestRenderAliasesSorted:
    def test_aliases_alphabetically_sorted_in_frontmatter(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        _, fm_block, _ = writer.render(haven).split("---\n", 2)
        fm = yaml.safe_load(fm_block)
        aliases = fm["aliases"]
        assert aliases == sorted(aliases)

    def test_aliases_sorted_regardless_of_input_order(
        self, writer: ConceptWriter
    ) -> None:
        # "Z-alias" before "A-alias" in tuple but should sort to "A-alias" first
        c = Concept.from_label(
            "Test",
            aliases=("Z-alias", "A-alias"),
            created_at=_CREATED_AT,
        )
        _, fm_block, _ = writer.render(c).split("---\n", 2)
        fm = yaml.safe_load(fm_block)
        assert fm["aliases"] == ["A-alias", "Z-alias"]


# ---------------------------------------------------------------------------
# TestRenderDeterminism
# ---------------------------------------------------------------------------


class TestRenderDeterminism:
    def test_same_concept_same_output(
        self, writer: ConceptWriter, haven: Concept
    ) -> None:
        out1 = writer.render(haven)
        out2 = writer.render(haven)
        assert out1 == out2

    def test_same_inputs_same_output_with_collections(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
        attachment_a: Attachment,
    ) -> None:
        kwargs = dict(
            relationships=[haven_uses_claude],
            attachments=[attachment_a],
            updated_at=_UPDATED_AT,
        )
        out1 = writer.render(haven, **kwargs)
        out2 = writer.render(haven, **kwargs)
        assert out1 == out2

    def test_different_concepts_different_output(
        self, writer: ConceptWriter, haven: Concept, claude: Concept
    ) -> None:
        assert writer.render(haven) != writer.render(claude)

    def test_different_relationship_set_different_output(
        self,
        writer: ConceptWriter,
        haven: Concept,
        haven_uses_claude: Relationship,
    ) -> None:
        out_no_rel = writer.render(haven)
        out_with_rel = writer.render(haven, relationships=[haven_uses_claude])
        assert out_no_rel != out_with_rel

    def test_input_list_order_does_not_affect_output(
        self,
        writer: ConceptWriter,
        haven: Concept,
        attachment_a: Attachment,
        attachment_b: Attachment,
    ) -> None:
        out_ab = writer.render(haven, attachments=[attachment_a, attachment_b])
        out_ba = writer.render(haven, attachments=[attachment_b, attachment_a])
        assert out_ab == out_ba


# ---------------------------------------------------------------------------
# TestWrite
# ---------------------------------------------------------------------------


class TestWrite:
    def test_creates_file(
        self, writer: ConceptWriter, haven: Concept, tmp_path: Path
    ) -> None:
        path = writer.write(haven, tmp_path)
        assert path.exists()

    def test_returns_path_object(
        self, writer: ConceptWriter, haven: Concept, tmp_path: Path
    ) -> None:
        path = writer.write(haven, tmp_path)
        assert isinstance(path, Path)

    def test_filename_is_concept_uuid(
        self, writer: ConceptWriter, haven: Concept, tmp_path: Path
    ) -> None:
        path = writer.write(haven, tmp_path)
        assert path.name == f"{haven.id}.md"

    def test_different_concepts_different_files(
        self,
        writer: ConceptWriter,
        haven: Concept,
        claude: Concept,
        tmp_path: Path,
    ) -> None:
        path_haven = writer.write(haven, tmp_path)
        path_claude = writer.write(claude, tmp_path)
        assert path_haven != path_claude

    def test_creates_output_directory_if_missing(
        self, writer: ConceptWriter, haven: Concept, tmp_path: Path
    ) -> None:
        deep = tmp_path / "a" / "b" / "c"
        assert not deep.exists()
        writer.write(haven, deep)
        assert deep.exists()

    def test_file_content_matches_render(
        self, writer: ConceptWriter, haven: Concept, tmp_path: Path
    ) -> None:
        path = writer.write(haven, tmp_path)
        expected = writer.render(haven)
        actual = path.read_text(encoding="utf-8")
        assert actual == expected

    def test_write_is_deterministic(
        self, writer: ConceptWriter, haven: Concept, tmp_path: Path
    ) -> None:
        path1 = writer.write(haven, tmp_path)
        content1 = path1.read_text(encoding="utf-8")
        path2 = writer.write(haven, tmp_path)
        content2 = path2.read_text(encoding="utf-8")
        assert content1 == content2
        assert path1 == path2

    def test_write_overwrites_existing_file(
        self, writer: ConceptWriter, haven: Concept, tmp_path: Path
    ) -> None:
        path = writer.write(haven, tmp_path)
        path.write_text("stale content", encoding="utf-8")
        writer.write(haven, tmp_path)
        assert path.read_text(encoding="utf-8") != "stale content"

    def test_write_with_relationships_and_attachments(
        self,
        writer: ConceptWriter,
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
        content = path.read_text(encoding="utf-8")
        assert str(haven.id) in content
        assert "uses" in content
        assert str(_KO_ID_A) in content

    def test_write_utf8_encoding(
        self, writer: ConceptWriter, tmp_path: Path
    ) -> None:
        c = Concept.from_label(
            "Göteborg",
            description="Swedish city with non-ASCII chars: åäö",
            created_at=_CREATED_AT,
        )
        path = writer.write(c, tmp_path)
        content = path.read_text(encoding="utf-8")
        assert "Göteborg" in content
        assert "åäö" in content
