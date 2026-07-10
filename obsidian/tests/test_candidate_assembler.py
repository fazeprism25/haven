"""Unit tests for obsidian.ontology.candidate_assembler.CandidateAssembler.

Test groups
-----------
TestSingleCandidate          — one activated concept, one attachment, one KO.
TestMultipleSupportingConcepts — one KO attached to several activated
                                 concepts merges into a single Candidate.
TestMultipleCandidates        — several independent KOs assembled together.
TestActivationMerging          — activation_score is the max across
                                 supporting concepts.
TestAttachmentRelevanceMerging  — attachment_relevance is the max across
                                 attachments for a KO.
TestHydration                   — hydrated KnowledgeObject fields match what
                                 was written to the vault.
TestDeterministicOrdering       — output order and supporting_concepts order
                                 are stable regardless of input order.
TestMissingKnowledgeObject      — an attachment pointing at a KO absent from
                                 the MemoryStore is skipped (logged), not an
                                 error, and other candidates still assemble.
TestConceptNotInGraph           — an activated concept absent from the graph
                                 is skipped, not an error.
TestEmptyAndNoAttachments       — empty activation table / no attachments
                                 yields an empty candidate list.
TestNoOutOfScopeImports         — module never imports ranking/allocation/
                                 context-building code or traverses
                                 relationship edges.
"""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine.memory_store import MemoryStore
from obsidian.memory_engine.vault_writer import VaultWriter
from obsidian.ontology.candidate_assembler import CandidateAssembler
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.models import Attachment, Concept
from obsidian.ontology.retrieval_models import ActivatedConcept

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def concept(label: str) -> Concept:
    return Concept.from_label(label)


def graph_of(*concepts: Concept) -> ConceptGraph:
    g = ConceptGraph()
    for c in concepts:
        g.add_concept(c)
    return g


def attach(graph: ConceptGraph, ko_id: UUID, concept_obj: Concept, relevance: float = 1.0) -> None:
    graph.add_attachment(Attachment.create(ko_id, concept_obj.id, relevance=relevance))


def activated(
    concept_obj: Concept,
    activation_score: float = 1.0,
    activation_depth: int = 0,
    source_seed: UUID | None = None,
) -> ActivatedConcept:
    return ActivatedConcept(
        concept_id=concept_obj.id,
        activation_score=activation_score,
        activation_depth=activation_depth,
        source_seed=source_seed if source_seed is not None else concept_obj.id,
    )


def make_ko(fact: str = "Haven uses Claude", ko_id: UUID | None = None) -> KnowledgeObject:
    return KnowledgeObject(
        id=ko_id if ko_id is not None else uuid4(),
        canonical_fact=fact,
        memory_type=MemoryType.FACT,
    )


def store_with(tmp_path: Path, *kos: KnowledgeObject) -> MemoryStore:
    writer = VaultWriter(tmp_path)
    for ko in kos:
        writer.write(ko)
    store = MemoryStore(tmp_path)
    store.load()
    return store


def assemble(assembler: CandidateAssembler, table, graph: ConceptGraph, store: MemoryStore):
    return assembler.assemble(table, graph, store)


# ---------------------------------------------------------------------------
# TestSingleCandidate
# ---------------------------------------------------------------------------


class TestSingleCandidate:
    def test_returns_one_candidate(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        g = graph_of(haven)
        ko = make_ko()
        attach(g, ko.id, haven)
        store = store_with(tmp_path, ko)

        result = assemble(CandidateAssembler(), {haven.id: activated(haven)}, g, store)

        assert len(result) == 1

    def test_candidate_has_hydrated_knowledge_object(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        g = graph_of(haven)
        ko = make_ko(fact="Haven uses Claude")
        attach(g, ko.id, haven)
        store = store_with(tmp_path, ko)

        result = assemble(CandidateAssembler(), {haven.id: activated(haven)}, g, store)

        assert result[0].knowledge_object.id == ko.id
        assert result[0].knowledge_object.canonical_fact == "Haven uses Claude"

    def test_candidate_has_one_supporting_concept(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        g = graph_of(haven)
        ko = make_ko()
        attach(g, ko.id, haven)
        store = store_with(tmp_path, ko)
        ac = activated(haven, activation_score=0.7)

        result = assemble(CandidateAssembler(), {haven.id: ac}, g, store)

        assert result[0].supporting_concepts == (ac,)

    def test_candidate_attachment_relevance_matches_single_attachment(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        g = graph_of(haven)
        ko = make_ko()
        attach(g, ko.id, haven, relevance=0.42)
        store = store_with(tmp_path, ko)

        result = assemble(CandidateAssembler(), {haven.id: activated(haven)}, g, store)

        assert result[0].attachment_relevance == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# TestMultipleSupportingConcepts
# ---------------------------------------------------------------------------


class TestMultipleSupportingConcepts:
    def test_one_ko_attached_to_two_concepts_yields_one_candidate(self, tmp_path: Path) -> None:
        haven, claude = concept("Haven"), concept("Claude")
        g = graph_of(haven, claude)
        ko = make_ko()
        attach(g, ko.id, haven)
        attach(g, ko.id, claude)
        store = store_with(tmp_path, ko)

        result = assemble(
            CandidateAssembler(),
            {haven.id: activated(haven), claude.id: activated(claude)},
            g,
            store,
        )

        assert len(result) == 1
        assert len(result[0].supporting_concepts) == 2

    def test_supporting_concepts_contains_both_activated_concepts(self, tmp_path: Path) -> None:
        haven, claude = concept("Haven"), concept("Claude")
        g = graph_of(haven, claude)
        ko = make_ko()
        attach(g, ko.id, haven)
        attach(g, ko.id, claude)
        store = store_with(tmp_path, ko)
        ac_haven, ac_claude = activated(haven, 0.9), activated(claude, 0.4)

        result = assemble(
            CandidateAssembler(),
            {haven.id: ac_haven, claude.id: ac_claude},
            g,
            store,
        )

        assert set(result[0].supporting_concepts) == {ac_haven, ac_claude}


# ---------------------------------------------------------------------------
# TestMultipleCandidates
# ---------------------------------------------------------------------------


class TestMultipleCandidates:
    def test_two_independent_concepts_yield_two_candidates(self, tmp_path: Path) -> None:
        haven, claude = concept("Haven"), concept("Claude")
        g = graph_of(haven, claude)
        ko1, ko2 = make_ko("fact 1"), make_ko("fact 2")
        attach(g, ko1.id, haven)
        attach(g, ko2.id, claude)
        store = store_with(tmp_path, ko1, ko2)

        result = assemble(
            CandidateAssembler(),
            {haven.id: activated(haven), claude.id: activated(claude)},
            g,
            store,
        )

        assert len(result) == 2
        result_ko_ids = {c.knowledge_object.id for c in result}
        assert result_ko_ids == {ko1.id, ko2.id}

    def test_one_concept_two_kos_yields_two_candidates(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        g = graph_of(haven)
        ko1, ko2 = make_ko("fact 1"), make_ko("fact 2")
        attach(g, ko1.id, haven)
        attach(g, ko2.id, haven)
        store = store_with(tmp_path, ko1, ko2)

        result = assemble(CandidateAssembler(), {haven.id: activated(haven)}, g, store)

        assert len(result) == 2


# ---------------------------------------------------------------------------
# TestActivationMerging
# ---------------------------------------------------------------------------


class TestActivationMerging:
    def test_activation_score_is_max_of_supporting_concepts(self, tmp_path: Path) -> None:
        haven, claude = concept("Haven"), concept("Claude")
        g = graph_of(haven, claude)
        ko = make_ko()
        attach(g, ko.id, haven)
        attach(g, ko.id, claude)
        store = store_with(tmp_path, ko)

        result = assemble(
            CandidateAssembler(),
            {haven.id: activated(haven, 0.3), claude.id: activated(claude, 0.8)},
            g,
            store,
        )

        assert result[0].activation_score == pytest.approx(0.8)

    def test_activation_score_from_weaker_concept_when_only_one_supports(self, tmp_path: Path) -> None:
        haven, claude = concept("Haven"), concept("Claude")
        g = graph_of(haven, claude)
        ko = make_ko()
        attach(g, ko.id, claude)  # haven has no attachment to this KO
        store = store_with(tmp_path, ko)

        result = assemble(
            CandidateAssembler(),
            {haven.id: activated(haven, 0.9), claude.id: activated(claude, 0.2)},
            g,
            store,
        )

        assert result[0].activation_score == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# TestAttachmentRelevanceMerging
# ---------------------------------------------------------------------------


class TestAttachmentRelevanceMerging:
    def test_attachment_relevance_is_max_across_attachments(self, tmp_path: Path) -> None:
        haven, claude = concept("Haven"), concept("Claude")
        g = graph_of(haven, claude)
        ko = make_ko()
        attach(g, ko.id, haven, relevance=0.3)
        attach(g, ko.id, claude, relevance=0.95)
        store = store_with(tmp_path, ko)

        result = assemble(
            CandidateAssembler(),
            {haven.id: activated(haven), claude.id: activated(claude)},
            g,
            store,
        )

        assert result[0].attachment_relevance == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# TestHydration
# ---------------------------------------------------------------------------


class TestHydration:
    def test_hydrated_fields_match_vault(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        g = graph_of(haven)
        ko = KnowledgeObject(
            id=uuid4(),
            canonical_fact="Haven is a personal second brain",
            memory_type=MemoryType.FACT,
            confidence=0.77,
            importance=0.55,
        )
        attach(g, ko.id, haven)
        store = store_with(tmp_path, ko)

        result = assemble(CandidateAssembler(), {haven.id: activated(haven)}, g, store)

        hydrated = result[0].knowledge_object
        assert hydrated.canonical_fact == ko.canonical_fact
        assert hydrated.confidence == pytest.approx(0.77)
        assert hydrated.importance == pytest.approx(0.55)

    def test_hydrated_object_is_not_the_same_instance(self, tmp_path: Path) -> None:
        # Hydration goes through the vault round-trip, not object passthrough.
        haven = concept("Haven")
        g = graph_of(haven)
        ko = make_ko()
        attach(g, ko.id, haven)
        store = store_with(tmp_path, ko)

        result = assemble(CandidateAssembler(), {haven.id: activated(haven)}, g, store)

        assert result[0].knowledge_object is not ko
        assert result[0].knowledge_object == store.get(ko.id)


# ---------------------------------------------------------------------------
# TestDeterministicOrdering
# ---------------------------------------------------------------------------


class TestDeterministicOrdering:
    def test_repeated_calls_produce_identical_results(self, tmp_path: Path) -> None:
        haven, claude = concept("Haven"), concept("Claude")
        g = graph_of(haven, claude)
        ko1, ko2 = make_ko("fact 1"), make_ko("fact 2")
        attach(g, ko1.id, haven)
        attach(g, ko2.id, claude)
        store = store_with(tmp_path, ko1, ko2)
        table = {haven.id: activated(haven), claude.id: activated(claude)}

        assembler = CandidateAssembler()
        first = assemble(assembler, table, g, store)
        for _ in range(5):
            assert assemble(assembler, table, g, store) == first

    def test_output_sorted_by_knowledge_object_id(self, tmp_path: Path) -> None:
        haven, claude = concept("Haven"), concept("Claude")
        g = graph_of(haven, claude)
        ko1, ko2 = make_ko("fact 1"), make_ko("fact 2")
        attach(g, ko1.id, haven)
        attach(g, ko2.id, claude)
        store = store_with(tmp_path, ko1, ko2)
        table = {haven.id: activated(haven), claude.id: activated(claude)}

        result = assemble(CandidateAssembler(), table, g, store)

        ids = [str(c.knowledge_object.id) for c in result]
        assert ids == sorted(ids)

    def test_dict_insertion_order_does_not_affect_output(self, tmp_path: Path) -> None:
        haven, claude = concept("Haven"), concept("Claude")
        g = graph_of(haven, claude)
        ko1, ko2 = make_ko("fact 1"), make_ko("fact 2")
        attach(g, ko1.id, haven)
        attach(g, ko2.id, claude)
        store = store_with(tmp_path, ko1, ko2)

        table_ab = {haven.id: activated(haven), claude.id: activated(claude)}
        table_ba = {claude.id: activated(claude), haven.id: activated(haven)}

        assembler = CandidateAssembler()
        assert assemble(assembler, table_ab, g, store) == assemble(assembler, table_ba, g, store)

    def test_supporting_concepts_sorted_by_concept_id(self, tmp_path: Path) -> None:
        haven, claude = concept("Haven"), concept("Claude")
        g = graph_of(haven, claude)
        ko = make_ko()
        attach(g, ko.id, haven)
        attach(g, ko.id, claude)
        store = store_with(tmp_path, ko)

        result = assemble(
            CandidateAssembler(),
            {haven.id: activated(haven), claude.id: activated(claude)},
            g,
            store,
        )

        ids = [str(ac.concept_id) for ac in result[0].supporting_concepts]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# TestMissingKnowledgeObject
# ---------------------------------------------------------------------------


class TestMissingKnowledgeObject:
    def test_attachment_to_unloaded_ko_is_skipped(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        haven = concept("Haven")
        g = graph_of(haven)
        missing_ko_id = uuid4()
        attach(g, missing_ko_id, haven)
        store = MemoryStore(tmp_path)
        store.load()  # empty vault; missing_ko_id was never written

        with caplog.at_level(logging.WARNING):
            result = assemble(CandidateAssembler(), {haven.id: activated(haven)}, g, store)

        assert result == []
        assert str(missing_ko_id) in caplog.text

    def test_other_candidates_still_assemble_around_a_missing_ko(
        self, tmp_path: Path
    ) -> None:
        haven, claude = concept("Haven"), concept("Claude")
        g = graph_of(haven, claude)
        missing_ko_id = uuid4()
        attach(g, missing_ko_id, haven)
        ko = make_ko()
        attach(g, ko.id, claude)
        store = store_with(tmp_path, ko)

        result = assemble(
            CandidateAssembler(),
            {haven.id: activated(haven), claude.id: activated(claude)},
            g,
            store,
        )

        assert len(result) == 1
        assert result[0].knowledge_object.id == ko.id


# ---------------------------------------------------------------------------
# TestConceptNotInGraph
# ---------------------------------------------------------------------------


class TestConceptNotInGraph:
    def test_activated_concept_absent_from_graph_is_skipped(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        phantom = concept("Phantom")  # never added to the graph
        g = graph_of(haven)
        ko = make_ko()
        attach(g, ko.id, haven)
        store = store_with(tmp_path, ko)

        result = assemble(
            CandidateAssembler(),
            {haven.id: activated(haven), phantom.id: activated(phantom)},
            g,
            store,
        )

        assert len(result) == 1
        assert result[0].knowledge_object.id == ko.id


# ---------------------------------------------------------------------------
# TestEmptyAndNoAttachments
# ---------------------------------------------------------------------------


class TestEmptyAndNoAttachments:
    def test_empty_activation_table_returns_empty(self, tmp_path: Path) -> None:
        g = ConceptGraph()
        store = MemoryStore(tmp_path)
        store.load()

        result = assemble(CandidateAssembler(), {}, g, store)

        assert result == []

    def test_activated_concept_with_no_attachments_returns_empty(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        g = graph_of(haven)  # no attachments registered
        store = MemoryStore(tmp_path)
        store.load()

        result = assemble(CandidateAssembler(), {haven.id: activated(haven)}, g, store)

        assert result == []


# ---------------------------------------------------------------------------
# TestNoOutOfScopeImports
# ---------------------------------------------------------------------------


class TestNoOutOfScopeImports:
    def test_module_does_not_import_ranking_allocation_or_context_code(self) -> None:
        import obsidian.ontology.candidate_assembler as module

        import_lines = [
            line
            for line in Path(module.__file__).read_text(encoding="utf-8").splitlines()
            if line.startswith(("import ", "from "))
        ]
        source = "\n".join(import_lines)
        assert "retrieval_config" not in source
        assert "deterministic_ranker" not in source
        assert "deterministic_slot_allocator" not in source
        assert "context_builder" not in source

    def test_module_never_traverses_relationship_edges(self) -> None:
        import obsidian.ontology.candidate_assembler as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in (".relationships(", ".neighbors(", ".parents(", ".children("):
            assert forbidden not in source
