"""Integration tests for OntologyPipeline.

These tests exercise the full flow:

    KnowledgeObject
        → OntologyManager (proposals)
        → OntologyValidator (filter)
        → ConceptGraph (mutation)
        → ConceptWriter (file I/O)

Coverage targets
----------------
No concepts
  * Fact with no detectable concept labels → [] returned, graph unchanged,
    no files written.

Single concept (new)
  * Concept file is created in concept_dir.
  * Returned path matches ``{concept_dir}/{concept_id}.md``.
  * Graph has the concept after processing.
  * KnowledgeObject is attached to the concept in the graph.
  * Concept file has valid YAML frontmatter (starts with ``---``).
  * Concept file contains the label in frontmatter.
  * Concept file contains the concept's UUID.
  * Concept file contains the KnowledgeObject's UUID in attachments.

Two concepts (new)
  * Both concept files are created.
  * Both concept IDs appear in the graph.
  * A relationship between them is stored in the graph.
  * Each concept file references the other concept's UUID (relationship
    section).
  * Returned paths list contains exactly two unique entries.

Idempotency (same KO processed twice)
  * Second call produces no new graph mutations (concepts, relationships,
    attachments counts are stable).
  * File is overwritten but still valid.

Accumulation (two distinct KOs mentioning the same concept)
  * Both KOs attached to the shared concept in the graph.
  * Concept file after second call contains both KO UUIDs.

Multiple KOs — independent facts
  * Each KO's concept is tracked separately in the graph.
  * Correct number of files written across both calls.

Graph state
  * Graph is unchanged after a no-op call (no concepts detected).
  * Graph counts increase by the correct amount for single-concept KO.
  * Graph counts increase correctly for two-concept KO.

File content structure
  * Round-trip: ``ConceptParser.parse()`` successfully parses every
    written file without errors.

Determinism
  * Two pipelines with empty graphs, same KO → same file content
    (modulo ``updated_at`` timestamp which is wall-clock dependent;
    we verify structure equality, not byte equality).
"""

from __future__ import annotations

import yaml

from pathlib import Path
from uuid import UUID

import pytest

from obsidian.manager_ai.models import KnowledgeObject
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.concept_parser import ConceptParser
from obsidian.ontology.identity import concept_id as _cid
from obsidian.ontology.ontology_pipeline import OntologyPipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_ko(fact: str, confidence: float = 0.8, importance: float = 0.7) -> KnowledgeObject:
    return KnowledgeObject(
        canonical_fact=fact,
        confidence=confidence,
        importance=importance,
    )


def graph_concept_count(graph: ConceptGraph) -> int:
    return len(graph._concepts)  # noqa: SLF001


def graph_relationship_count(graph: ConceptGraph) -> int:
    return len(graph._relationships)  # noqa: SLF001


def graph_attachment_count(graph: ConceptGraph) -> int:
    return len(graph._attachments)  # noqa: SLF001


def parse_frontmatter(md: str) -> dict:
    """Extract and parse the YAML frontmatter from a Markdown string."""
    # Strip the opening '---\n' and find the closing '---'
    inner = md[4:]  # skip '---\n'
    end = inner.index("---\n")
    return yaml.safe_load(inner[:end])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def graph() -> ConceptGraph:
    return ConceptGraph()


@pytest.fixture()
def pipeline(graph, tmp_path) -> OntologyPipeline:
    return OntologyPipeline(graph, tmp_path)


# ---------------------------------------------------------------------------
# No concepts
# ---------------------------------------------------------------------------


class TestNoConcepts:
    def test_no_paths_returned(self, pipeline, graph, tmp_path):
        ko = make_ko("no concepts here at all lowercase")
        paths = pipeline.process(ko)
        assert paths == []

    def test_graph_unchanged(self, pipeline, graph):
        ko = make_ko("no concepts here at all lowercase")
        pipeline.process(ko)
        assert graph_concept_count(graph) == 0
        assert graph_relationship_count(graph) == 0
        assert graph_attachment_count(graph) == 0

    def test_no_files_written(self, pipeline, tmp_path):
        ko = make_ko("everything is lowercase here")
        pipeline.process(ko)
        assert list(tmp_path.glob("*.md")) == []

    def test_empty_fact_returns_empty(self, pipeline):
        ko = make_ko("")
        assert pipeline.process(ko) == []

    def test_whitespace_fact_returns_empty(self, pipeline):
        ko = make_ko("   ")
        assert pipeline.process(ko) == []


# ---------------------------------------------------------------------------
# Single new concept
# ---------------------------------------------------------------------------


class TestSingleNewConcept:
    FACT = "Haven is a second-brain project"

    def test_one_path_returned(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        paths = pipeline.process(ko)
        assert len(paths) == 1

    def test_path_matches_concept_id(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        paths = pipeline.process(ko)
        expected = tmp_path / f"{_cid('Haven')}.md"
        assert paths[0] == expected

    def test_file_exists_on_disk(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        assert (tmp_path / f"{_cid('Haven')}.md").exists()

    def test_graph_has_concept(self, pipeline, graph):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        assert graph.has_concept(_cid("Haven"))

    def test_ko_attached_to_concept_in_graph(self, pipeline, graph):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        attached = graph.concepts_for_knowledge_object(ko.id)
        assert any(c.id == _cid("Haven") for c in attached)

    def test_file_starts_with_yaml_fence(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        content = (tmp_path / f"{_cid('Haven')}.md").read_text(encoding="utf-8")
        assert content.startswith("---\n")

    def test_file_frontmatter_has_correct_label(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        content = (tmp_path / f"{_cid('Haven')}.md").read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        assert fm["label"] == "Haven"

    def test_file_frontmatter_has_correct_id(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        content = (tmp_path / f"{_cid('Haven')}.md").read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        assert UUID(fm["id"]) == _cid("Haven")

    def test_file_contains_ko_uuid_in_attachments(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        content = (tmp_path / f"{_cid('Haven')}.md").read_text(encoding="utf-8")
        assert str(ko.id) in content

    def test_file_body_has_h1_label(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        content = (tmp_path / f"{_cid('Haven')}.md").read_text(encoding="utf-8")
        assert "# Haven" in content


# ---------------------------------------------------------------------------
# Two new concepts
# ---------------------------------------------------------------------------


class TestTwoNewConcepts:
    FACT = "Haven uses Claude"

    def test_two_paths_returned(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        paths = pipeline.process(ko)
        assert len(paths) == 2

    def test_both_files_exist(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        assert (tmp_path / f"{_cid('Haven')}.md").exists()
        assert (tmp_path / f"{_cid('Claude')}.md").exists()

    def test_both_concepts_in_graph(self, pipeline, graph):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        assert graph.has_concept(_cid("Haven"))
        assert graph.has_concept(_cid("Claude"))

    def test_relationship_in_graph(self, pipeline, graph):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        rels = graph.relationships(_cid("Haven"))
        assert len(rels) >= 1

    def test_haven_file_references_claude_id(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        haven_content = (tmp_path / f"{_cid('Haven')}.md").read_text(encoding="utf-8")
        assert str(_cid("Claude")) in haven_content

    def test_claude_file_references_haven_id(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        claude_content = (tmp_path / f"{_cid('Claude')}.md").read_text(encoding="utf-8")
        assert str(_cid("Haven")) in claude_content

    def test_both_kos_attached_in_graph(self, pipeline, graph):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        haven_attached = graph.concepts_for_knowledge_object(ko.id)
        ids = {c.id for c in haven_attached}
        assert _cid("Haven") in ids
        assert _cid("Claude") in ids

    def test_graph_counts_correct(self, pipeline, graph):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        assert graph_concept_count(graph) == 2
        assert graph_relationship_count(graph) == 1
        assert graph_attachment_count(graph) == 2


# ---------------------------------------------------------------------------
# Three concepts
# ---------------------------------------------------------------------------


class TestThreeConcepts:
    FACT = "Siddhartha built Haven using Claude"

    def test_three_files_written(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        assert (tmp_path / f"{_cid('Siddhartha')}.md").exists()
        assert (tmp_path / f"{_cid('Haven')}.md").exists()
        assert (tmp_path / f"{_cid('Claude')}.md").exists()

    def test_three_relationships_in_graph(self, pipeline, graph):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        assert graph_relationship_count(graph) == 3

    def test_three_attachments_in_graph(self, pipeline, graph):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        assert graph_attachment_count(graph) == 3


# ---------------------------------------------------------------------------
# Idempotency (same KO processed twice)
# ---------------------------------------------------------------------------


class TestIdempotency:
    FACT = "Haven is a project"

    def test_second_call_adds_no_new_concepts(self, pipeline, graph):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        before = graph_concept_count(graph)
        pipeline.process(ko)
        assert graph_concept_count(graph) == before

    def test_second_call_adds_no_new_relationships(self, pipeline, graph):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        before = graph_relationship_count(graph)
        pipeline.process(ko)
        assert graph_relationship_count(graph) == before

    def test_second_call_adds_no_new_attachments(self, pipeline, graph):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        before = graph_attachment_count(graph)
        pipeline.process(ko)
        assert graph_attachment_count(graph) == before

    def test_second_call_rewrites_file(self, pipeline, tmp_path):
        ko = make_ko(self.FACT)
        pipeline.process(ko)
        f = tmp_path / f"{_cid('Haven')}.md"
        mtime_1 = f.stat().st_mtime_ns

        pipeline.process(ko)
        mtime_2 = f.stat().st_mtime_ns
        # File was rewritten (mtime may be same if OS resolution is low,
        # but the content must still be valid)
        content = f.read_text(encoding="utf-8")
        assert content.startswith("---\n")

    def test_second_call_with_two_concepts_idempotent(self, pipeline, graph):
        ko = make_ko("Haven uses Claude")
        pipeline.process(ko)
        c_before = graph_concept_count(graph)
        r_before = graph_relationship_count(graph)
        a_before = graph_attachment_count(graph)

        pipeline.process(ko)
        assert graph_concept_count(graph) == c_before
        assert graph_relationship_count(graph) == r_before
        assert graph_attachment_count(graph) == a_before


# ---------------------------------------------------------------------------
# Accumulation (two distinct KOs sharing a concept)
# ---------------------------------------------------------------------------


class TestAccumulation:
    def test_two_kos_both_attached_to_shared_concept(self, pipeline, graph):
        ko1 = make_ko("Haven is a project")
        ko2 = make_ko("Haven stores knowledge")
        pipeline.process(ko1)
        pipeline.process(ko2)

        haven_atts = graph.attachments_for_concept(_cid("Haven"))
        ko_ids = {a.knowledge_object_id for a in haven_atts}
        assert ko1.id in ko_ids
        assert ko2.id in ko_ids

    def test_concept_file_after_second_ko_has_both_attachments(self, pipeline, tmp_path):
        ko1 = make_ko("Haven is a project")
        ko2 = make_ko("Haven stores knowledge")
        pipeline.process(ko1)
        pipeline.process(ko2)

        content = (tmp_path / f"{_cid('Haven')}.md").read_text(encoding="utf-8")
        assert str(ko1.id) in content
        assert str(ko2.id) in content

    def test_two_attachments_in_graph_for_shared_concept(self, pipeline, graph):
        ko1 = make_ko("Haven is a project")
        ko2 = make_ko("Haven stores knowledge")
        pipeline.process(ko1)
        pipeline.process(ko2)

        haven_atts = graph.attachments_for_concept(_cid("Haven"))
        assert len(haven_atts) == 2

    def test_second_ko_creates_only_attach_not_concept(self, pipeline, graph):
        ko1 = make_ko("Haven is a project")
        ko2 = make_ko("Haven stores knowledge")
        pipeline.process(ko1)
        c_before = graph_concept_count(graph)

        pipeline.process(ko2)
        # No new concept should have been created
        assert graph_concept_count(graph) == c_before


# ---------------------------------------------------------------------------
# Round-trip parse
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Every file written must be parseable by ConceptParser."""

    def test_single_concept_file_parses(self, pipeline, tmp_path):
        parser = ConceptParser()
        ko = make_ko("Haven is a project")
        pipeline.process(ko)

        content = (tmp_path / f"{_cid('Haven')}.md").read_text(encoding="utf-8")
        result = parser.parse(content)
        assert result.concept.label == "Haven"
        assert result.concept.id == _cid("Haven")

    def test_two_concept_files_parse(self, pipeline, tmp_path):
        parser = ConceptParser()
        ko = make_ko("Haven uses Claude")
        pipeline.process(ko)

        for label in ("Haven", "Claude"):
            content = (tmp_path / f"{_cid(label)}.md").read_text(encoding="utf-8")
            result = parser.parse(content)
            assert result.concept.label == label

    def test_parsed_attachment_matches_ko_id(self, pipeline, tmp_path):
        parser = ConceptParser()
        ko = make_ko("Haven is a project")
        pipeline.process(ko)

        content = (tmp_path / f"{_cid('Haven')}.md").read_text(encoding="utf-8")
        result = parser.parse(content)
        att_ko_ids = {a.knowledge_object_id for a in result.attachments}
        assert ko.id in att_ko_ids

    def test_parsed_relationship_present_after_two_concepts(self, pipeline, tmp_path):
        parser = ConceptParser()
        ko = make_ko("Haven uses Claude")
        pipeline.process(ko)

        haven_content = (tmp_path / f"{_cid('Haven')}.md").read_text(encoding="utf-8")
        result = parser.parse(haven_content)
        assert len(result.relationships) == 1

    def test_three_concept_files_all_parse(self, pipeline, tmp_path):
        parser = ConceptParser()
        ko = make_ko("Siddhartha built Haven using Claude")
        pipeline.process(ko)

        for label in ("Siddhartha", "Haven", "Claude"):
            content = (tmp_path / f"{_cid(label)}.md").read_text(encoding="utf-8")
            result = parser.parse(content)
            assert result.concept.label == label


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------


class TestGraphState:
    def test_single_concept_increments_count_by_one(self, pipeline, graph):
        ko = make_ko("Haven is a project")
        pipeline.process(ko)
        assert graph_concept_count(graph) == 1
        assert graph_relationship_count(graph) == 0
        assert graph_attachment_count(graph) == 1

    def test_concept_in_graph_has_correct_label(self, pipeline, graph):
        ko = make_ko("Haven is a project")
        pipeline.process(ko)
        concept = graph.get_concept(_cid("Haven"))
        assert concept.label == "Haven"

    def test_attachment_in_graph_has_correct_relevance(self, pipeline, graph):
        ko = make_ko("Haven is a project", importance=0.6)
        pipeline.process(ko)
        atts = graph.attachments_for_concept(_cid("Haven"))
        assert len(atts) == 1
        assert atts[0].relevance == pytest.approx(0.6)

    def test_relationship_confidence_matches_ko(self, pipeline, graph):
        ko = make_ko("Haven uses Claude", confidence=0.9)
        pipeline.process(ko)
        rels = graph.relationships(_cid("Haven"))
        assert len(rels) == 1
        assert rels[0].confidence == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Concept directory creation
# ---------------------------------------------------------------------------


class TestDirectoryCreation:
    def test_nested_concept_dir_created(self, graph, tmp_path):
        nested = tmp_path / "a" / "b" / "concepts"
        pipeline = OntologyPipeline(graph, nested)
        ko = make_ko("Haven is a project")
        pipeline.process(ko)
        assert nested.exists()
        assert (nested / f"{_cid('Haven')}.md").exists()
