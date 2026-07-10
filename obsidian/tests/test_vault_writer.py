"""Unit tests for obsidian.memory_engine.vault_writer's Obsidian compatibility.

Tests are grouped by concern:

* :class:`TestVaultWriterInstantiation` — construction with/without a
  ``concept_graph``.
* :class:`TestFrontmatterBackwardsCompatibility` — every pre-existing
  frontmatter key is unchanged, and ``KnowledgeObject.from_dict`` still
  hydrates identically from both old-shaped and new-shaped frontmatter.
* :class:`TestObsidianFrontmatterFields` — the new ``tags``/``aliases``
  keys.
* :class:`TestTitle` — the ``title`` frontmatter key and the body's H1
  heading.
* :class:`TestDecisionSection` — the ``## Decision`` body section: absent
  for non-decision memories, present with status/reason/alternatives/
  supersession links for decision memories carrying
  ``DecisionMetadata``.
* :class:`TestRelatedConceptsSection` — the Related Concepts body section,
  across "no graph", "graph but no attachments yet", and "attached"
  states.
* :class:`TestRelationshipsSection` — the Relationships body section,
  including dedup when both endpoints are attached to the same memory.
* :class:`TestWikiLinkSanitization` — pipe/bracket characters in a
  Concept's label never corrupt the wiki-link syntax.
* :class:`TestDeterminism` — byte-identical output for unchanged input.
* :class:`TestWrite` — filesystem operations.
* :class:`TestMemoryStoreRoundTrip` — end-to-end: a file written by the
  new VaultWriter still loads correctly through MemoryStore, and a
  hand-written "old format" file (no ``tags``/``aliases``) still does too.
* :class:`TestNewProjectStateMemoryTypes` — the four ``MemoryType`` members
  added by ``docs/architecture/PROJECT_STATE_KNOWLEDGE_MODEL.md`` round-trip
  through VaultWriter/MemoryStore with no code changes to either.

All tests are deterministic. Fixed UUIDs are used wherever a concept or
relationship identity matters for an assertion.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
import yaml

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import (
    DecisionMetadata,
    DecisionStatus,
    KnowledgeObject,
    with_decision_metadata,
)
from obsidian.memory_engine.memory_store import MemoryStore
from obsidian.memory_engine.vault_writer import VaultWriter
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.enums import OntologyRelationshipType
from obsidian.ontology.models import Attachment, Concept, Relationship

_EXISTING_FRONTMATTER_KEYS = {
    "id",
    "canonical_fact",
    "memory_type",
    "confidence",
    "importance",
    "valid_from",
    "valid_until",
    "last_confirmed",
    "confirmation_count",
    "metadata",
}


def _frontmatter(rendered: str) -> dict:
    _, yaml_text, _ = rendered.split("---\n", 2)
    return yaml.safe_load(yaml_text)


def _body(rendered: str) -> str:
    _, _, body = rendered.split("---\n", 2)
    return body


# ---------------------------------------------------------------------------
# TestVaultWriterInstantiation
# ---------------------------------------------------------------------------


class TestVaultWriterInstantiation:
    def test_instantiates_without_concept_graph(self, tmp_path: Path) -> None:
        writer = VaultWriter(tmp_path)
        assert isinstance(writer, VaultWriter)

    def test_instantiates_with_concept_graph(self, tmp_path: Path) -> None:
        writer = VaultWriter(tmp_path, concept_graph=ConceptGraph())
        assert isinstance(writer, VaultWriter)


# ---------------------------------------------------------------------------
# TestFrontmatterBackwardsCompatibility
# ---------------------------------------------------------------------------


class TestFrontmatterBackwardsCompatibility:
    def test_every_existing_key_is_still_present(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="Haven uses Claude.")
        path = VaultWriter(tmp_path).write(knowledge)
        frontmatter = _frontmatter(path.read_text(encoding="utf-8"))
        assert _EXISTING_FRONTMATTER_KEYS <= frontmatter.keys()

    def test_existing_key_values_are_unchanged(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(
            canonical_fact="Haven uses Claude.",
            memory_type=MemoryType.DECISION,
            confidence=0.8,
            importance=0.6,
            confirmation_count=3,
            metadata={"custom": "value"},
        )
        path = VaultWriter(tmp_path).write(knowledge)
        frontmatter = _frontmatter(path.read_text(encoding="utf-8"))

        assert frontmatter["id"] == str(knowledge.id)
        assert frontmatter["canonical_fact"] == "Haven uses Claude."
        assert frontmatter["memory_type"] == "decision"
        assert frontmatter["confidence"] == 0.8
        assert frontmatter["importance"] == 0.6
        assert frontmatter["confirmation_count"] == 3
        assert frontmatter["metadata"] == {"custom": "value"}

    def test_new_frontmatter_shape_still_hydrates_via_knowledge_object_from_dict(
        self, tmp_path: Path
    ) -> None:
        knowledge = KnowledgeObject(canonical_fact="Round trip check.")
        path = VaultWriter(tmp_path).write(knowledge)
        frontmatter = _frontmatter(path.read_text(encoding="utf-8"))

        reloaded = KnowledgeObject.from_dict(frontmatter)
        assert reloaded.id == knowledge.id
        assert reloaded.canonical_fact == knowledge.canonical_fact
        assert reloaded.memory_type == knowledge.memory_type

    def test_old_frontmatter_shape_without_tags_or_aliases_still_hydrates(self) -> None:
        """A file written by the pre-Obsidian-compat VaultWriter must still parse."""
        old_style_frontmatter = {
            "id": "12345678-1234-5678-1234-567812345678",
            "canonical_fact": "A fact written before this feature existed.",
            "memory_type": "fact",
            "confidence": 0.5,
            "importance": 0.5,
            "valid_from": "2026-01-01T00:00:00",
            "valid_until": None,
            "last_confirmed": None,
            "confirmation_count": 0,
            "metadata": {},
        }
        reloaded = KnowledgeObject.from_dict(old_style_frontmatter)
        assert str(reloaded.id) == old_style_frontmatter["id"]
        assert reloaded.canonical_fact == old_style_frontmatter["canonical_fact"]


# ---------------------------------------------------------------------------
# TestObsidianFrontmatterFields
# ---------------------------------------------------------------------------


class TestObsidianFrontmatterFields:
    @pytest.mark.parametrize(
        "memory_type, expected_tag",
        [
            (MemoryType.FACT, "memory/fact"),
            (MemoryType.DECISION, "memory/decision"),
            (MemoryType.GOAL, "memory/goal"),
        ],
    )
    def test_tags_reflect_memory_type(
        self, tmp_path: Path, memory_type: MemoryType, expected_tag: str
    ) -> None:
        knowledge = KnowledgeObject(canonical_fact="Something.", memory_type=memory_type)
        path = VaultWriter(tmp_path).write(knowledge)
        frontmatter = _frontmatter(path.read_text(encoding="utf-8"))
        assert frontmatter["tags"] == [expected_tag]

    def test_aliases_contains_canonical_fact(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="Haven uses Claude for extraction.")
        path = VaultWriter(tmp_path).write(knowledge)
        frontmatter = _frontmatter(path.read_text(encoding="utf-8"))
        assert frontmatter["aliases"] == ["Haven uses Claude for extraction."]


# ---------------------------------------------------------------------------
# TestNewProjectStateMemoryTypes
#
# See docs/architecture/PROJECT_STATE_KNOWLEDGE_MODEL.md. Confirms the four
# new MemoryType members round-trip through the real VaultWriter -> Markdown
# -> KnowledgeObject.from_dict path with zero VaultWriter/MemoryStore code
# changes, the same property Decision Memory established for its own
# metadata addition (docs/DECISION_MEMORY.md).
# ---------------------------------------------------------------------------


class TestNewProjectStateMemoryTypes:
    @pytest.mark.parametrize(
        "memory_type",
        [
            MemoryType.BLOCKER,
            MemoryType.IMPLEMENTATION_STATE,
            MemoryType.CODE_AREA,
            MemoryType.OPEN_QUESTION,
        ],
    )
    def test_new_memory_type_round_trips_through_vault_writer(
        self, tmp_path: Path, memory_type: MemoryType
    ) -> None:
        knowledge = KnowledgeObject(
            canonical_fact="Something worth remembering.", memory_type=memory_type
        )
        path = VaultWriter(tmp_path).write(knowledge)
        frontmatter = _frontmatter(path.read_text(encoding="utf-8"))

        assert frontmatter["memory_type"] == memory_type.value
        assert frontmatter["tags"] == [f"memory/{memory_type.value}"]

        reloaded = KnowledgeObject.from_dict(frontmatter)
        assert reloaded.memory_type is memory_type
        assert reloaded.canonical_fact == knowledge.canonical_fact

    def test_new_memory_type_loads_via_memory_store(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(
            canonical_fact="The retrieval pipeline is not yet wired to the planner.",
            memory_type=MemoryType.IMPLEMENTATION_STATE,
        )
        VaultWriter(tmp_path).write(knowledge)

        store = MemoryStore(tmp_path)
        store.load()
        loaded = store.all()
        assert len(loaded) == 1
        assert loaded[0].memory_type is MemoryType.IMPLEMENTATION_STATE
        assert loaded[0].canonical_fact == knowledge.canonical_fact

    def test_obsidian_fields_present_without_concept_graph(self, tmp_path: Path) -> None:
        """tags/aliases don't depend on a concept_graph being supplied."""
        knowledge = KnowledgeObject(canonical_fact="No graph needed for this.")
        path = VaultWriter(tmp_path).write(knowledge)
        frontmatter = _frontmatter(path.read_text(encoding="utf-8"))
        assert frontmatter["tags"] == ["memory/fact"]
        assert frontmatter["aliases"] == ["No graph needed for this."]


# ---------------------------------------------------------------------------
# TestTitle
# ---------------------------------------------------------------------------


class TestTitle:
    def test_title_frontmatter_matches_canonical_fact(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="Haven uses Claude for extraction.")
        path = VaultWriter(tmp_path).write(knowledge)
        frontmatter = _frontmatter(path.read_text(encoding="utf-8"))
        assert frontmatter["title"] == "Haven uses Claude for extraction."

    def test_body_starts_with_h1_matching_canonical_fact(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="Haven uses Claude for extraction.")
        path = VaultWriter(tmp_path).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert body.lstrip("\n").startswith("# Haven uses Claude for extraction.")

    def test_title_present_without_concept_graph(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="No graph needed for this.")
        path = VaultWriter(tmp_path).write(knowledge)
        rendered = path.read_text(encoding="utf-8")
        assert _frontmatter(rendered)["title"] == "No graph needed for this."
        assert "# No graph needed for this." in _body(rendered)


# ---------------------------------------------------------------------------
# TestDecisionSection
# ---------------------------------------------------------------------------


class TestDecisionSection:
    def test_non_decision_memory_has_no_decision_section(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="A plain fact.", memory_type=MemoryType.FACT)
        path = VaultWriter(tmp_path).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert "## Decision" not in body

    def test_decision_without_metadata_has_no_decision_section(
        self, tmp_path: Path
    ) -> None:
        """A decision written before this feature existed has no metadata key."""
        knowledge = KnowledgeObject(
            canonical_fact="A decision with no lineage yet.",
            memory_type=MemoryType.DECISION,
        )
        path = VaultWriter(tmp_path).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert "## Decision" not in body

    def test_decision_section_shows_status(self, tmp_path: Path) -> None:
        knowledge = with_decision_metadata(
            KnowledgeObject(
                canonical_fact="We chose Qdrant.", memory_type=MemoryType.DECISION
            ),
            DecisionMetadata(status=DecisionStatus.ACTIVE),
        )
        path = VaultWriter(tmp_path).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert "## Decision" in body
        assert "**Status**: active" in body

    def test_decision_section_shows_reason_and_alternatives(
        self, tmp_path: Path
    ) -> None:
        knowledge = with_decision_metadata(
            KnowledgeObject(
                canonical_fact="We chose Qdrant.", memory_type=MemoryType.DECISION
            ),
            DecisionMetadata(
                reason="Best fit for our latency budget.",
                alternatives_considered=["Pinecone", "Weaviate"],
                status=DecisionStatus.ACTIVE,
            ),
        )
        path = VaultWriter(tmp_path).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert "**Reason**: Best fit for our latency budget." in body
        assert "Pinecone" in body
        assert "Weaviate" in body

    def test_decision_section_links_supersedes(self, tmp_path: Path) -> None:
        old_id = UUID("11111111-1111-1111-1111-111111111111")
        knowledge = with_decision_metadata(
            KnowledgeObject(
                canonical_fact="We chose Qdrant instead.",
                memory_type=MemoryType.DECISION,
            ),
            DecisionMetadata(status=DecisionStatus.ACTIVE, supersedes=old_id),
        )
        path = VaultWriter(tmp_path).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert f"[[{old_id}]]" in body

    def test_decision_section_links_superseded_by(self, tmp_path: Path) -> None:
        new_id = UUID("22222222-2222-2222-2222-222222222222")
        knowledge = with_decision_metadata(
            KnowledgeObject(
                canonical_fact="We chose Pinecone (later replaced).",
                memory_type=MemoryType.DECISION,
            ),
            DecisionMetadata(status=DecisionStatus.SUPERSEDED, superseded_by=new_id),
        )
        path = VaultWriter(tmp_path).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert "**Status**: superseded" in body
        assert f"[[{new_id}]]" in body

    def test_decision_section_omits_absent_fields(self, tmp_path: Path) -> None:
        """No reason/alternatives/supersedes/superseded_by should render nothing."""
        knowledge = with_decision_metadata(
            KnowledgeObject(
                canonical_fact="A bare decision.", memory_type=MemoryType.DECISION
            ),
            DecisionMetadata(status=DecisionStatus.ACTIVE),
        )
        path = VaultWriter(tmp_path).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert "**Reason**" not in body
        assert "**Alternatives Considered**" not in body
        assert "**Supersedes**" not in body
        assert "**Superseded By**" not in body


# ---------------------------------------------------------------------------
# TestRelatedConceptsSection
# ---------------------------------------------------------------------------


class TestRelatedConceptsSection:
    def test_no_concept_graph_shows_placeholder(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="Untracked fact.")
        path = VaultWriter(tmp_path).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert "## Related Concepts" in body
        assert "_No concept graph was supplied when this note was written._" in body

    def test_graph_with_no_attachments_yet_shows_placeholder(
        self, tmp_path: Path
    ) -> None:
        knowledge = KnowledgeObject(canonical_fact="Brand new memory.")
        graph = ConceptGraph()
        path = VaultWriter(tmp_path, concept_graph=graph).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert "_No concepts attached yet._" in body

    def test_attached_concept_renders_piped_wiki_link(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="I use Terraform for infra.")
        graph = ConceptGraph()
        terraform = Concept.from_label("Terraform")
        graph.add_concept(terraform)
        graph.add_attachment(Attachment.create(knowledge.id, terraform.id))

        path = VaultWriter(tmp_path, concept_graph=graph).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert f"[[{terraform.id}|Terraform]]" in body

    def test_mentions_already_known_concept_not_yet_attached_to_this_ko(
        self, tmp_path: Path
    ) -> None:
        """The critical case: vault_writer.write() always runs before
        ontology_pipeline.process() for a brand-new memory (see module
        docstring), so concepts_for_knowledge_object(this_id) is always
        empty at write time. Without the text-mention fallback, a freshly
        written memory would NEVER show a wiki-link on its first write —
        this asserts the fallback actually closes that gap."""
        knowledge = KnowledgeObject(canonical_fact="I also use Terraform for infra.")
        graph = ConceptGraph()
        terraform = Concept.from_label("Terraform")
        graph.add_concept(terraform)
        # Deliberately NOT attached to `knowledge` — simulates the
        # documented call order where this memory's own attachment does
        # not exist yet at write() time.

        path = VaultWriter(tmp_path, concept_graph=graph).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert f"[[{terraform.id}|Terraform]]" in body

    def test_mention_via_alias_is_linked(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="We use K8s for orchestration.")
        graph = ConceptGraph()
        kubernetes = Concept.from_label("Kubernetes", aliases=("K8s",))
        graph.add_concept(kubernetes)

        path = VaultWriter(tmp_path, concept_graph=graph).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert f"[[{kubernetes.id}|Kubernetes]]" in body

    def test_partial_word_match_is_not_linked(self, tmp_path: Path) -> None:
        """A short label must not match inside an unrelated longer word."""
        knowledge = KnowledgeObject(canonical_fact="This memory said nothing relevant.")
        graph = ConceptGraph()
        ai = Concept.from_label("AI")
        graph.add_concept(ai)

        path = VaultWriter(tmp_path, concept_graph=graph).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert f"[[{ai.id}|AI]]" not in body
        assert "_No concepts attached yet._" in body

    def test_unrelated_known_concept_is_not_linked(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="I use Terraform for infra.")
        graph = ConceptGraph()
        terraform = Concept.from_label("Terraform")
        unrelated = Concept.from_label("Kubernetes")
        graph.add_concept(terraform)
        graph.add_concept(unrelated)

        path = VaultWriter(tmp_path, concept_graph=graph).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        section = body.split("## Related Concepts")[1].split("## Relationships")[0]
        assert f"[[{terraform.id}|Terraform]]" in section
        assert f"[[{unrelated.id}|Kubernetes]]" not in section

    def test_multiple_attached_concepts_sorted_by_concept_uuid(
        self, tmp_path: Path
    ) -> None:
        knowledge = KnowledgeObject(canonical_fact="I use Terraform and Kubernetes.")
        graph = ConceptGraph()
        terraform = Concept.from_label("Terraform")
        kubernetes = Concept.from_label("Kubernetes")
        graph.add_concept(terraform)
        graph.add_concept(kubernetes)
        graph.add_attachment(Attachment.create(knowledge.id, terraform.id))
        graph.add_attachment(Attachment.create(knowledge.id, kubernetes.id))

        path = VaultWriter(tmp_path, concept_graph=graph).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        section = body.split("## Related Concepts")[1].split("## Relationships")[0]

        expected_order = sorted([terraform, kubernetes], key=lambda c: str(c.id))
        lines = [line for line in section.splitlines() if line.startswith("- [[")]
        assert lines == [f"- [[{c.id}|{c.label}]]" for c in expected_order]


# ---------------------------------------------------------------------------
# TestRelationshipsSection
# ---------------------------------------------------------------------------


class TestRelationshipsSection:
    def test_no_concepts_attached_shows_placeholder(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="Nothing attached.")
        graph = ConceptGraph()
        path = VaultWriter(tmp_path, concept_graph=graph).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        section = body.split("## Relationships")[1]
        assert "_No concepts attached yet._" in section

    def test_attached_concept_with_no_relationships_shows_placeholder(
        self, tmp_path: Path
    ) -> None:
        knowledge = KnowledgeObject(canonical_fact="I use Terraform.")
        graph = ConceptGraph()
        terraform = Concept.from_label("Terraform")
        graph.add_concept(terraform)
        graph.add_attachment(Attachment.create(knowledge.id, terraform.id))

        path = VaultWriter(tmp_path, concept_graph=graph).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        section = body.split("## Relationships")[1]
        assert "_No relationships among attached concepts._" in section

    def test_relationship_from_attached_concept_is_linked(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="I use Terraform with Kubernetes.")
        graph = ConceptGraph()
        terraform = Concept.from_label("Terraform")
        kubernetes = Concept.from_label("Kubernetes")
        graph.add_concept(terraform)
        graph.add_concept(kubernetes)
        graph.add_attachment(Attachment.create(knowledge.id, terraform.id))
        relationship = Relationship.create(
            terraform.id, kubernetes.id, OntologyRelationshipType.USES
        )
        graph.add_relationship(relationship)

        path = VaultWriter(tmp_path, concept_graph=graph).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        section = body.split("## Relationships")[1]

        assert f"[[{terraform.id}|Terraform]]" in section
        assert f"[[{kubernetes.id}|Kubernetes]]" in section
        assert "**uses**" in section
        assert "(confidence: 1.0000)" in section

    def test_relationship_between_two_attached_concepts_is_not_duplicated(
        self, tmp_path: Path
    ) -> None:
        knowledge = KnowledgeObject(canonical_fact="Terraform and Kubernetes, together.")
        graph = ConceptGraph()
        terraform = Concept.from_label("Terraform")
        kubernetes = Concept.from_label("Kubernetes")
        graph.add_concept(terraform)
        graph.add_concept(kubernetes)
        # Both endpoints of the same relationship are attached to this
        # memory — the relationship must still appear exactly once.
        graph.add_attachment(Attachment.create(knowledge.id, terraform.id))
        graph.add_attachment(Attachment.create(knowledge.id, kubernetes.id))
        relationship = Relationship.create(
            terraform.id, kubernetes.id, OntologyRelationshipType.USES
        )
        graph.add_relationship(relationship)

        path = VaultWriter(tmp_path, concept_graph=graph).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        section = body.split("## Relationships")[1]
        assert section.count("**uses**") == 1


# ---------------------------------------------------------------------------
# TestWikiLinkSanitization
# ---------------------------------------------------------------------------


class TestWikiLinkSanitization:
    def test_pipe_in_label_is_sanitized(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="Weird label test.")
        graph = ConceptGraph()
        weird = Concept.from_label("Weird|Label")
        graph.add_concept(weird)
        graph.add_attachment(Attachment.create(knowledge.id, weird.id))

        path = VaultWriter(tmp_path, concept_graph=graph).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert f"[[{weird.id}|Weird/Label]]" in body

    def test_double_brackets_in_label_are_sanitized(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="Bracket label test.")
        graph = ConceptGraph()
        weird = Concept.from_label("Odd[[Label")
        graph.add_concept(weird)
        graph.add_attachment(Attachment.create(knowledge.id, weird.id))

        path = VaultWriter(tmp_path, concept_graph=graph).write(knowledge)
        body = _body(path.read_text(encoding="utf-8"))
        assert f"[[{weird.id}|OddLabel]]" in body


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_twice_is_byte_identical(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="Deterministic check.")
        graph = ConceptGraph()
        concept = Concept.from_label("Determinism")
        graph.add_concept(concept)
        graph.add_attachment(Attachment.create(knowledge.id, concept.id))

        writer = VaultWriter(tmp_path, concept_graph=graph)
        first = writer.write(knowledge).read_text(encoding="utf-8")
        second = writer.write(knowledge).read_text(encoding="utf-8")
        assert first == second


# ---------------------------------------------------------------------------
# TestWrite
# ---------------------------------------------------------------------------


class TestWrite:
    def test_returns_path_named_after_id(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="Filesystem check.")
        path = VaultWriter(tmp_path).write(knowledge)
        assert path == tmp_path / f"{knowledge.id}.md"
        assert path.exists()

    def test_creates_vault_directory_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested" / "vault"
        knowledge = KnowledgeObject(canonical_fact="Nested dir check.")
        path = VaultWriter(nested).write(knowledge)
        assert path.exists()

    def test_file_is_utf8_encoded(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(canonical_fact="Non-ASCII: café, naïve, 日本語.")
        path = VaultWriter(tmp_path).write(knowledge)
        text = path.read_text(encoding="utf-8")
        assert "café" in text
        assert "日本語" in text


# ---------------------------------------------------------------------------
# TestMemoryStoreRoundTrip
# ---------------------------------------------------------------------------


class TestMemoryStoreRoundTrip:
    def test_new_format_file_loads_through_memory_store(self, tmp_path: Path) -> None:
        knowledge = KnowledgeObject(
            canonical_fact="Retrieval must not change.", memory_type=MemoryType.FACT
        )
        graph = ConceptGraph()
        concept = Concept.from_label("Retrieval")
        graph.add_concept(concept)
        graph.add_attachment(Attachment.create(knowledge.id, concept.id))
        VaultWriter(tmp_path, concept_graph=graph).write(knowledge)

        store = MemoryStore(tmp_path)
        store.load()
        loaded = store.get(knowledge.id)

        assert loaded.id == knowledge.id
        assert loaded.canonical_fact == knowledge.canonical_fact
        assert loaded.memory_type == knowledge.memory_type

    def test_old_format_file_still_loads_through_memory_store(
        self, tmp_path: Path
    ) -> None:
        """A hand-written pre-Obsidian-compat file (no tags/aliases) still parses."""
        old_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        content = (
            "---\n"
            f"id: {old_id}\n"
            "canonical_fact: Written before this feature existed.\n"
            "memory_type: fact\n"
            "confidence: 0.5\n"
            "importance: 0.5\n"
            "valid_from: '2026-01-01T00:00:00'\n"
            "valid_until: null\n"
            "last_confirmed: null\n"
            "confirmation_count: 0\n"
            "metadata: {}\n"
            "---\n\n"
            "## Canonical Fact\n\n"
            "Written before this feature existed.\n"
        )
        (tmp_path / f"{old_id}.md").write_text(content, encoding="utf-8")

        store = MemoryStore(tmp_path)
        store.load()
        loaded = store.get(UUID(old_id))

        assert loaded.canonical_fact == "Written before this feature existed."
