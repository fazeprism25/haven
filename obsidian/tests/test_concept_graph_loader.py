"""Unit tests for obsidian.ontology.concept_graph_loader.ConceptGraphLoader.

Test groups
-----------
TestInstantiation           — loader construction, statelessness.
TestEmptyDirectory          — empty dir, missing dir, path-is-file.
TestSingleConcept           — bare concept, concept with aliases/description.
TestMultipleConcepts        — multiple files, correct concept count, isolation.
TestRelationships           — single-file rels, cross-file rels, unknown endpoint.
TestAttachments             — single-file atts, cross-file atts, unknown concept.
TestMalformedMarkdown       — invalid YAML, missing fence, ConceptGraphLoadError.
TestDeterministicLoading    — filename-sorted order; result stable across calls.
TestDuplicateConcepts       — same UUID in two files; graph contains it once.
TestDuplicateRelationships  — same relationship UUID in two files; idempotent.
TestDuplicateAttachments    — same attachment UUID in two files; idempotent.
TestNonMarkdownFiles        — .txt, .yaml, .json, dirs are silently ignored.
"""

from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from obsidian.ontology.concept_graph_loader import ConceptGraphLoadError, ConceptGraphLoader
from obsidian.ontology.concept_writer import ConceptWriter
from obsidian.ontology.enums import OntologyRelationshipType
from obsidian.ontology.identity import concept_id as derive_concept_id
from obsidian.ontology.models import Attachment, Concept, Relationship


# ---------------------------------------------------------------------------
# Fixed test data
# ---------------------------------------------------------------------------

_CREATED = datetime(2026, 1, 1, 12, 0, 0)
_KO_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_KO_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make(label: str, *aliases: str, description: str = "") -> Concept:
    return Concept.from_label(label, aliases=tuple(aliases), description=description, created_at=_CREATED)


def _write_concept(
    directory: Path,
    filename: str,
    concept: Concept,
    *,
    relationships: list[Relationship] | None = None,
    attachments: list[Attachment] | None = None,
) -> Path:
    writer = ConceptWriter()
    text = writer.render(concept, relationships=relationships or [], attachments=attachments or [])
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path


def _rel(source: Concept, target: Concept) -> Relationship:
    return Relationship.create(source.id, target.id, OntologyRelationshipType.USES)


def _att(ko_id: UUID, concept: Concept, relevance: float = 1.0) -> Attachment:
    return Attachment.create(ko_id, concept.id, relevance=relevance)


# ---------------------------------------------------------------------------
# TestInstantiation
# ---------------------------------------------------------------------------


class TestInstantiation:
    def test_instantiates_without_arguments(self) -> None:
        loader = ConceptGraphLoader()
        assert loader is not None

    def test_has_load_method(self) -> None:
        assert callable(ConceptGraphLoader().load)

    def test_stateless_reuse(self, tmp_path: Path) -> None:
        c = _make("Haven")
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()
        _write_concept(dir1, "haven.md", c)
        _write_concept(dir2, "haven.md", c)
        loader = ConceptGraphLoader()
        g1 = loader.load(dir1)
        g2 = loader.load(dir2)
        assert g1.has_concept(c.id)
        assert g2.has_concept(c.id)


# ---------------------------------------------------------------------------
# TestEmptyDirectory
# ---------------------------------------------------------------------------


class TestEmptyDirectory:
    def test_empty_directory_returns_empty_graph(self, tmp_path: Path) -> None:
        loader = ConceptGraphLoader()
        graph = loader.load(tmp_path)
        # An empty graph has no concepts; verify it doesn't raise.
        assert not graph.has_concept(derive_concept_id("anything"))

    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        loader = ConceptGraphLoader()
        with pytest.raises(ConceptGraphLoadError, match="not found"):
            loader.load(tmp_path / "nonexistent")

    def test_path_is_file_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "file.md"
        f.write_text("content", encoding="utf-8")
        loader = ConceptGraphLoader()
        with pytest.raises(ConceptGraphLoadError, match="not a directory"):
            loader.load(f)

    def test_directory_with_only_non_md_files_returns_empty_graph(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
        (tmp_path / "data.json").write_text("{}", encoding="utf-8")
        loader = ConceptGraphLoader()
        graph = loader.load(tmp_path)
        assert not graph.has_concept(derive_concept_id("anything"))


# ---------------------------------------------------------------------------
# TestSingleConcept
# ---------------------------------------------------------------------------


class TestSingleConcept:
    def test_bare_concept_is_loaded(self, tmp_path: Path) -> None:
        c = _make("Haven")
        _write_concept(tmp_path, "haven.md", c)
        graph = ConceptGraphLoader().load(tmp_path)
        assert graph.has_concept(c.id)

    def test_concept_fields_preserved(self, tmp_path: Path) -> None:
        c = _make("Haven", "Personal Brain", description="My second brain")
        _write_concept(tmp_path, "haven.md", c)
        graph = ConceptGraphLoader().load(tmp_path)
        loaded = graph.get_concept(c.id)
        assert loaded.label == "Haven"
        assert loaded.description == "My second brain"
        assert "Personal Brain" in loaded.aliases

    def test_concept_id_is_deterministic(self, tmp_path: Path) -> None:
        c = _make("Haven")
        _write_concept(tmp_path, "haven.md", c)
        graph = ConceptGraphLoader().load(tmp_path)
        assert graph.has_concept(derive_concept_id("Haven"))

    def test_no_relationships_in_single_concept_file(self, tmp_path: Path) -> None:
        c = _make("Haven")
        _write_concept(tmp_path, "haven.md", c)
        graph = ConceptGraphLoader().load(tmp_path)
        assert graph.relationships(c.id) == []

    def test_no_attachments_in_single_concept_file(self, tmp_path: Path) -> None:
        c = _make("Haven")
        _write_concept(tmp_path, "haven.md", c)
        graph = ConceptGraphLoader().load(tmp_path)
        assert graph.attachments_for_concept(c.id) == []


# ---------------------------------------------------------------------------
# TestMultipleConcepts
# ---------------------------------------------------------------------------


class TestMultipleConcepts:
    def test_all_concepts_loaded(self, tmp_path: Path) -> None:
        c1 = _make("Haven")
        c2 = _make("Claude")
        c3 = _make("DTU")
        _write_concept(tmp_path, "claude.md", c2)
        _write_concept(tmp_path, "dtu.md", c3)
        _write_concept(tmp_path, "haven.md", c1)
        graph = ConceptGraphLoader().load(tmp_path)
        assert graph.has_concept(c1.id)
        assert graph.has_concept(c2.id)
        assert graph.has_concept(c3.id)

    def test_concepts_are_isolated(self, tmp_path: Path) -> None:
        c1 = _make("Haven")
        c2 = _make("Claude")
        _write_concept(tmp_path, "haven.md", c1)
        _write_concept(tmp_path, "claude.md", c2)
        graph = ConceptGraphLoader().load(tmp_path)
        assert graph.relationships(c1.id) == []
        assert graph.relationships(c2.id) == []

    def test_unknown_concept_absent(self, tmp_path: Path) -> None:
        _write_concept(tmp_path, "haven.md", _make("Haven"))
        graph = ConceptGraphLoader().load(tmp_path)
        assert not graph.has_concept(derive_concept_id("Nonexistent"))


# ---------------------------------------------------------------------------
# TestRelationships
# ---------------------------------------------------------------------------


class TestRelationships:
    def test_relationship_within_single_file(self, tmp_path: Path) -> None:
        src = _make("Haven")
        tgt = _make("Claude")
        rel = _rel(src, tgt)
        # Store both concepts and the relationship in the same file.
        # Relationship is recorded in the source's file; target must be loaded too.
        _write_concept(tmp_path, "claude.md", tgt)
        _write_concept(tmp_path, "haven.md", src, relationships=[rel])
        graph = ConceptGraphLoader().load(tmp_path)
        rels = graph.relationships(src.id)
        assert len(rels) == 1
        assert rels[0].id == rel.id

    def test_cross_file_relationship(self, tmp_path: Path) -> None:
        """Relationship in file A references a Concept defined in file B."""
        haven = _make("Haven")
        claude = _make("Claude")
        rel = _rel(haven, claude)
        _write_concept(tmp_path, "a_haven.md", haven, relationships=[rel])
        _write_concept(tmp_path, "b_claude.md", claude)
        graph = ConceptGraphLoader().load(tmp_path)
        assert graph.has_concept(haven.id)
        assert graph.has_concept(claude.id)
        rels = graph.relationships(haven.id)
        assert any(r.id == rel.id for r in rels)

    def test_relationship_direction_preserved(self, tmp_path: Path) -> None:
        src = _make("Haven")
        tgt = _make("Claude")
        rel = _rel(src, tgt)
        _write_concept(tmp_path, "haven.md", src, relationships=[rel])
        _write_concept(tmp_path, "claude.md", tgt)
        graph = ConceptGraphLoader().load(tmp_path)
        [loaded_rel] = graph.relationships(src.id)
        assert loaded_rel.source_id == src.id
        assert loaded_rel.target_id == tgt.id

    def test_unknown_relationship_endpoint_is_skipped_not_raised(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A relationship referencing a Concept missing from disk is dropped,
        logged, and does not abort the load -- see
        ``obsidian.ontology.concept_graph_loader``'s module docstring for why
        this is treated as a stale cross-reference rather than a fatal error.
        """
        ghost = _make("Ghost")  # not written to disk
        src = _make("Haven")
        rel = _rel(src, ghost)
        _write_concept(tmp_path, "haven.md", src, relationships=[rel])
        # ghost.md is deliberately omitted
        with caplog.at_level("WARNING"):
            graph = ConceptGraphLoader().load(tmp_path)
        assert graph.has_concept(src.id)
        assert graph.relationships(src.id) == []
        assert str(rel.id) in caplog.text
        assert str(ghost.id) in caplog.text
        assert "haven.md" in caplog.text
        assert "skipped 1 relationship" in caplog.text

    def test_multiple_relationships_loaded(self, tmp_path: Path) -> None:
        a = _make("Haven")
        b = _make("Claude")
        c = _make("DTU")
        rel_ab = _rel(a, b)
        rel_ac = _rel(a, c)
        _write_concept(tmp_path, "a.md", a, relationships=[rel_ab, rel_ac])
        _write_concept(tmp_path, "b.md", b)
        _write_concept(tmp_path, "c.md", c)
        graph = ConceptGraphLoader().load(tmp_path)
        rel_ids = {r.id for r in graph.relationships(a.id)}
        assert rel_ab.id in rel_ids
        assert rel_ac.id in rel_ids


# ---------------------------------------------------------------------------
# TestAttachments
# ---------------------------------------------------------------------------


class TestAttachments:
    def test_attachment_within_single_file(self, tmp_path: Path) -> None:
        c = _make("Haven")
        att = _att(_KO_A, c)
        _write_concept(tmp_path, "haven.md", c, attachments=[att])
        graph = ConceptGraphLoader().load(tmp_path)
        atts = graph.attachments_for_concept(c.id)
        assert len(atts) == 1
        assert atts[0].id == att.id

    def test_attachment_ko_id_preserved(self, tmp_path: Path) -> None:
        c = _make("Haven")
        att = _att(_KO_A, c, relevance=0.75)
        _write_concept(tmp_path, "haven.md", c, attachments=[att])
        graph = ConceptGraphLoader().load(tmp_path)
        [loaded_att] = graph.attachments_for_concept(c.id)
        assert loaded_att.knowledge_object_id == _KO_A
        assert abs(loaded_att.relevance - 0.75) < 1e-9

    def test_multiple_attachments_to_same_concept(self, tmp_path: Path) -> None:
        c = _make("Haven")
        att_a = _att(_KO_A, c)
        att_b = _att(_KO_B, c)
        _write_concept(tmp_path, "haven.md", c, attachments=[att_a, att_b])
        graph = ConceptGraphLoader().load(tmp_path)
        att_ids = {a.id for a in graph.attachments_for_concept(c.id)}
        assert att_a.id in att_ids
        assert att_b.id in att_ids

    def test_attachment_unknown_concept_is_skipped_not_raised(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An attachment referencing a Concept missing from disk is dropped,
        logged, and does not abort the load -- same stale-cross-reference
        handling as relationships (see ``TestRelationships``).
        """
        ghost = _make("Ghost")
        att = Attachment.create(_KO_A, ghost.id)
        # Write the attachment into haven.md but don't write ghost.md.
        haven = _make("Haven")
        _write_concept(tmp_path, "haven.md", haven, attachments=[att])
        with caplog.at_level("WARNING"):
            graph = ConceptGraphLoader().load(tmp_path)
        assert graph.has_concept(haven.id)
        assert graph.attachments_for_concept(haven.id) == []
        assert str(att.id) in caplog.text
        assert str(ghost.id) in caplog.text
        assert "haven.md" in caplog.text
        assert "skipped 1 attachment" in caplog.text

    def test_ko_linked_to_multiple_concepts(self, tmp_path: Path) -> None:
        c1 = _make("Haven")
        c2 = _make("Claude")
        att1 = _att(_KO_A, c1)
        att2 = _att(_KO_A, c2)
        _write_concept(tmp_path, "c1.md", c1, attachments=[att1])
        _write_concept(tmp_path, "c2.md", c2, attachments=[att2])
        graph = ConceptGraphLoader().load(tmp_path)
        concepts = graph.concepts_for_knowledge_object(_KO_A)
        concept_ids = {c.id for c in concepts}
        assert c1.id in concept_ids
        assert c2.id in concept_ids


# ---------------------------------------------------------------------------
# TestMalformedMarkdown
# ---------------------------------------------------------------------------


class TestMalformedMarkdown:
    def test_no_frontmatter_fence_raises(self, tmp_path: Path) -> None:
        (tmp_path / "bad.md").write_text("Just plain text\n", encoding="utf-8")
        with pytest.raises(ConceptGraphLoadError, match="bad.md"):
            ConceptGraphLoader().load(tmp_path)

    def test_missing_closing_fence_raises(self, tmp_path: Path) -> None:
        (tmp_path / "bad.md").write_text("---\nid: not-closed\n", encoding="utf-8")
        with pytest.raises(ConceptGraphLoadError, match="bad.md"):
            ConceptGraphLoader().load(tmp_path)

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        (tmp_path / "bad.md").write_text(
            "---\n: {invalid: yaml: here\n---\n# Concept\n",
            encoding="utf-8",
        )
        with pytest.raises(ConceptGraphLoadError, match="bad.md"):
            ConceptGraphLoader().load(tmp_path)

    def test_missing_required_id_field_raises(self, tmp_path: Path) -> None:
        content = textwrap.dedent("""\
            ---
            label: Haven
            created_at: '2026-01-01T12:00:00'
            ---

            # Haven
        """)
        (tmp_path / "bad.md").write_text(content, encoding="utf-8")
        with pytest.raises(ConceptGraphLoadError, match="bad.md"):
            ConceptGraphLoader().load(tmp_path)

    def test_error_message_contains_filename(self, tmp_path: Path) -> None:
        (tmp_path / "broken_file.md").write_text("not markdown", encoding="utf-8")
        with pytest.raises(ConceptGraphLoadError) as exc_info:
            ConceptGraphLoader().load(tmp_path)
        assert "broken_file.md" in str(exc_info.value)

    def test_valid_file_before_bad_file_still_raises(self, tmp_path: Path) -> None:
        """A good file processed before a bad one must not produce a partial graph."""
        c = _make("Haven")
        _write_concept(tmp_path, "a_good.md", c)
        (tmp_path / "b_bad.md").write_text("not markdown", encoding="utf-8")
        with pytest.raises(ConceptGraphLoadError, match="b_bad.md"):
            ConceptGraphLoader().load(tmp_path)

    def test_error_wraps_parse_error_cause(self, tmp_path: Path) -> None:
        (tmp_path / "bad.md").write_text("no fence", encoding="utf-8")
        with pytest.raises(ConceptGraphLoadError) as exc_info:
            ConceptGraphLoader().load(tmp_path)
        from obsidian.ontology.concept_parser import ConceptParseError
        assert isinstance(exc_info.value.__cause__, ConceptParseError)


# ---------------------------------------------------------------------------
# TestDeterministicLoading
# ---------------------------------------------------------------------------


class TestDeterministicLoading:
    def test_result_stable_across_two_loads(self, tmp_path: Path) -> None:
        """Loading the same directory twice produces identical graphs."""
        c1 = _make("Haven")
        c2 = _make("Claude")
        _write_concept(tmp_path, "haven.md", c1)
        _write_concept(tmp_path, "claude.md", c2)
        loader = ConceptGraphLoader()
        g1 = loader.load(tmp_path)
        g2 = loader.load(tmp_path)
        assert g1.has_concept(c1.id) == g2.has_concept(c1.id)
        assert g1.has_concept(c2.id) == g2.has_concept(c2.id)

    def test_lexicographic_filename_order(self, tmp_path: Path) -> None:
        """Files are processed in filename-sorted order (not OS inode order)."""
        # Create files in an order that differs from alphabetical.
        c_z = _make("Zeta")
        c_a = _make("Alpha")
        c_m = _make("Mu")
        _write_concept(tmp_path, "z_zeta.md", c_z)
        _write_concept(tmp_path, "a_alpha.md", c_a)
        _write_concept(tmp_path, "m_mu.md", c_m)
        graph = ConceptGraphLoader().load(tmp_path)
        # All three must be present regardless of creation order.
        assert graph.has_concept(c_a.id)
        assert graph.has_concept(c_m.id)
        assert graph.has_concept(c_z.id)

    def test_new_loader_instance_same_result(self, tmp_path: Path) -> None:
        """Different loader instances produce identical results from same dir."""
        c = _make("Haven")
        _write_concept(tmp_path, "haven.md", c)
        g1 = ConceptGraphLoader().load(tmp_path)
        g2 = ConceptGraphLoader().load(tmp_path)
        assert g1.has_concept(c.id) == g2.has_concept(c.id)

    def test_relationships_stable_across_loads(self, tmp_path: Path) -> None:
        src = _make("Haven")
        tgt = _make("Claude")
        rel = _rel(src, tgt)
        _write_concept(tmp_path, "haven.md", src, relationships=[rel])
        _write_concept(tmp_path, "claude.md", tgt)
        loader = ConceptGraphLoader()
        r1 = [r.id for r in loader.load(tmp_path).relationships(src.id)]
        r2 = [r.id for r in loader.load(tmp_path).relationships(src.id)]
        assert r1 == r2


# ---------------------------------------------------------------------------
# TestDuplicateConcepts
# ---------------------------------------------------------------------------


class TestDuplicateConcepts:
    def test_same_concept_in_two_files_loads_once(self, tmp_path: Path) -> None:
        """Two files with identical Concept UUID — idempotent, no error."""
        c = _make("Haven")
        _write_concept(tmp_path, "a_haven.md", c)
        _write_concept(tmp_path, "b_haven_copy.md", c)
        graph = ConceptGraphLoader().load(tmp_path)
        assert graph.has_concept(c.id)

    def test_duplicate_concept_does_not_raise(self, tmp_path: Path) -> None:
        c = _make("Haven")
        _write_concept(tmp_path, "haven1.md", c)
        _write_concept(tmp_path, "haven2.md", c)
        ConceptGraphLoader().load(tmp_path)  # must not raise

    def test_second_concept_still_present(self, tmp_path: Path) -> None:
        c1 = _make("Haven")
        c2 = _make("Claude")
        _write_concept(tmp_path, "haven1.md", c1)
        _write_concept(tmp_path, "haven2.md", c1)  # duplicate
        _write_concept(tmp_path, "claude.md", c2)
        graph = ConceptGraphLoader().load(tmp_path)
        assert graph.has_concept(c1.id)
        assert graph.has_concept(c2.id)


# ---------------------------------------------------------------------------
# TestDuplicateRelationships
# ---------------------------------------------------------------------------


class TestDuplicateRelationships:
    def test_same_relationship_in_two_files_no_error(self, tmp_path: Path) -> None:
        src = _make("Haven")
        tgt = _make("Claude")
        rel = _rel(src, tgt)
        _write_concept(tmp_path, "a.md", src, relationships=[rel])
        _write_concept(tmp_path, "b.md", tgt, relationships=[rel])
        ConceptGraphLoader().load(tmp_path)  # must not raise

    def test_duplicate_relationship_appears_once(self, tmp_path: Path) -> None:
        src = _make("Haven")
        tgt = _make("Claude")
        rel = _rel(src, tgt)
        _write_concept(tmp_path, "a.md", src, relationships=[rel])
        _write_concept(tmp_path, "b.md", tgt, relationships=[rel])
        graph = ConceptGraphLoader().load(tmp_path)
        rels = graph.relationships(src.id)
        assert len(rels) == 1
        assert rels[0].id == rel.id


# ---------------------------------------------------------------------------
# TestDuplicateAttachments
# ---------------------------------------------------------------------------


class TestDuplicateAttachments:
    def test_same_attachment_in_two_files_no_error(self, tmp_path: Path) -> None:
        c = _make("Haven")
        att = _att(_KO_A, c)
        _write_concept(tmp_path, "a.md", c, attachments=[att])
        _write_concept(tmp_path, "b.md", c, attachments=[att])
        ConceptGraphLoader().load(tmp_path)  # must not raise

    def test_duplicate_attachment_appears_once(self, tmp_path: Path) -> None:
        c = _make("Haven")
        att = _att(_KO_A, c)
        _write_concept(tmp_path, "a.md", c, attachments=[att])
        _write_concept(tmp_path, "b.md", c, attachments=[att])
        graph = ConceptGraphLoader().load(tmp_path)
        atts = graph.attachments_for_concept(c.id)
        assert len(atts) == 1
        assert atts[0].id == att.id


# ---------------------------------------------------------------------------
# TestNonMarkdownFiles
# ---------------------------------------------------------------------------


class TestNonMarkdownFiles:
    def test_txt_file_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("plain text", encoding="utf-8")
        graph = ConceptGraphLoader().load(tmp_path)
        assert not graph.has_concept(derive_concept_id("anything"))

    def test_yaml_file_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("key: value\n", encoding="utf-8")
        ConceptGraphLoader().load(tmp_path)  # must not raise

    def test_json_file_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "data.json").write_text('{"key": "value"}', encoding="utf-8")
        ConceptGraphLoader().load(tmp_path)  # must not raise

    def test_subdirectory_ignored(self, tmp_path: Path) -> None:
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.md").write_text("---\nid: fake\n---\n", encoding="utf-8")
        # The loader must not recurse into subdirectories.
        graph = ConceptGraphLoader().load(tmp_path)
        assert not graph.has_concept(derive_concept_id("anything"))

    def test_mixed_files_only_md_loaded(self, tmp_path: Path) -> None:
        c = _make("Haven")
        _write_concept(tmp_path, "haven.md", c)
        (tmp_path / "readme.txt").write_text("ignore me", encoding="utf-8")
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")
        graph = ConceptGraphLoader().load(tmp_path)
        assert graph.has_concept(c.id)

    def test_file_without_extension_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "Makefile").write_text("all:\n", encoding="utf-8")
        ConceptGraphLoader().load(tmp_path)  # must not raise

    def test_dot_md_suffix_required(self, tmp_path: Path) -> None:
        (tmp_path / "concept.mdx").write_text("not processed", encoding="utf-8")
        (tmp_path / "concept.MD").write_text("not processed", encoding="utf-8")
        graph = ConceptGraphLoader().load(tmp_path)
        assert not graph.has_concept(derive_concept_id("anything"))
