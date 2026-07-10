"""Unit tests for obsidian.memory_engine.hybrid_candidate_retriever.HybridCandidateRetriever.

Test groups
-----------
TestOntologyOnlyRetrieval    — a fact reachable only via concept attachment
                                (no keyword overlap) is returned as a Candidate
                                with has_ontology_evidence True.
TestKeywordOnlyRetrieval      — a fact with no concept attachment but a
                                keyword match is returned as a zero-evidence
                                Candidate (has_ontology_evidence False).
TestOverlapBetweenPaths       — a fact found by both paths appears exactly once.
TestDuplicateElimination      — the ontology-evidenced Candidate wins over the
                                zero-evidence keyword stand-in; no double-counting.
TestDeterministicOrdering     — output sorted by KnowledgeObject id, stable
                                across repeated calls and independent builds.
TestEmptyQuery                 — an empty/whitespace query yields no candidates.
TestEmptyGraph                  — an empty ConceptGraph doesn't break keyword-only retrieval.
TestEmptyStore                   — an empty MemoryStore doesn't break ontology-only retrieval.
TestStatelessReuse                — one instance, many different queries, no leakage.
TestNoGraphMutation                 — the ConceptGraph is never mutated by retrieve().
TestNoRankingPerformed                — no scoring/ranking is applied; ordering is
                                        purely the id tie-break, independent of
                                        activation/importance/confidence values.
TestArchitecturalBoundaries              — orchestrates the four components without
                                            reimplementing or mutating them.
TestRetrieveWithDiagnostics               — retrieve() equals
                                            retrieve_with_diagnostics()[0];
                                            provenance counts/flags match
                                            which path(s) actually found
                                            each candidate.
TestKeywordOverlapScoreWiring               — keyword_overlap_score is
                                            attached to keyword-only,
                                            ontology-only, and both-path
                                            candidates correctly, without
                                            displacing ontology evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Sequence
from uuid import uuid4

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine import hybrid_candidate_retriever as hybrid_module
from obsidian.memory_engine.hybrid_candidate_retriever import (
    Candidate,
    HybridCandidateRetriever,
)
from obsidian.memory_engine.memory_store import MemoryStore
from obsidian.memory_engine.vault_writer import VaultWriter
from obsidian.ontology.alias_index import AliasIndex
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.enums import OntologyRelationshipType
from obsidian.ontology.models import Attachment, Concept, Relationship
from obsidian.ontology.retrieval_config import RetrievalConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def concept(label: str) -> Concept:
    return Concept.from_label(label)


def make_ko(
    fact: str,
    *,
    importance: float = 0.5,
    confidence: float = 0.5,
    confirmation_count: int = 0,
) -> KnowledgeObject:
    return KnowledgeObject(
        id=uuid4(),
        canonical_fact=fact,
        memory_type=MemoryType.FACT,
        importance=importance,
        confidence=confidence,
        confirmation_count=confirmation_count,
    )


def build_retriever(
    tmp_path: Path,
    concepts: Sequence[Concept],
    kos: Sequence[KnowledgeObject],
    attachments: Iterable[Attachment] = (),
    relationships: Iterable[Relationship] = (),
    config: Optional[RetrievalConfig] = None,
) -> tuple[HybridCandidateRetriever, MemoryStore, ConceptGraph]:
    graph = ConceptGraph()
    for c in concepts:
        graph.add_concept(c)
    for rel in relationships:
        graph.add_relationship(rel)
    for att in attachments:
        graph.add_attachment(att)

    alias_index = AliasIndex()
    alias_index.build(concepts)

    writer = VaultWriter(tmp_path)
    for ko in kos:
        writer.write(ko)
    store = MemoryStore(tmp_path)
    store.load()

    retriever = HybridCandidateRetriever(alias_index, graph, store, config=config)
    return retriever, store, graph


def ids_of(results: List) -> List:
    return [item.knowledge_object.id for item in results]


# ---------------------------------------------------------------------------
# TestOntologyOnlyRetrieval
# ---------------------------------------------------------------------------


class TestOntologyOnlyRetrieval:
    def test_concept_attached_fact_with_no_keyword_overlap_returns_candidate(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        # "Haven" does not appear in the fact text, so the keyword path
        # cannot independently find this KnowledgeObject.
        ko = make_ko("Personal second-brain project used daily")
        att = Attachment.create(ko.id, haven.id)

        retriever, store, _ = build_retriever(tmp_path, [haven], [ko], attachments=[att])

        results = retriever.retrieve("Haven")

        assert len(results) == 1
        assert isinstance(results[0], Candidate)
        assert results[0].has_ontology_evidence is True
        assert results[0].knowledge_object.id == ko.id
        assert results[0].knowledge_object.canonical_fact == store.get(ko.id).canonical_fact

    def test_activation_spread_surfaces_indirect_concept_fact(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        # Only Claude has a direct attachment; Haven has none. The fact text
        # shares no keyword with the query either.
        ko_claude = make_ko("An AI assistant developed by a lab")
        att_claude = Attachment.create(ko_claude.id, claude.id)
        rel = Relationship.create(haven.id, claude.id, OntologyRelationshipType.USES)

        retriever, _, _ = build_retriever(
            tmp_path,
            [haven, claude],
            [ko_claude],
            attachments=[att_claude],
            relationships=[rel],
        )

        results = retriever.retrieve("Haven")

        assert len(results) == 1
        assert isinstance(results[0], Candidate)
        assert results[0].has_ontology_evidence is True
        assert results[0].knowledge_object.id == ko_claude.id


# ---------------------------------------------------------------------------
# TestKeywordOnlyRetrieval
# ---------------------------------------------------------------------------


class TestKeywordOnlyRetrieval:
    def test_unattached_fact_with_keyword_match_returns_zero_evidence_candidate(
        self, tmp_path: Path
    ) -> None:
        # No concepts/attachments/relationships at all.
        ko = make_ko("Claude is an AI assistant by Anthropic")

        retriever, _, _ = build_retriever(tmp_path, [], [ko])

        results = retriever.retrieve("claude")

        assert len(results) == 1
        assert isinstance(results[0], Candidate)
        assert results[0].has_ontology_evidence is False
        assert results[0].supporting_concepts == ()
        assert results[0].activation_score == 0.0
        assert results[0].attachment_relevance == 0.0
        assert results[0].knowledge_object.id == ko.id

    def test_concept_exists_but_ko_is_unattached_still_found_by_keyword(
        self, tmp_path: Path
    ) -> None:
        # A concept resolves from the query, but the KO of interest has no
        # attachment to it (or anything else) -- only keyword search finds it.
        haven = concept("Haven")
        ko = make_ko("Haven uses Claude for assistance")

        retriever, _, _ = build_retriever(tmp_path, [haven], [ko])

        results = retriever.retrieve("Haven")

        assert len(results) == 1
        assert isinstance(results[0], Candidate)
        assert results[0].has_ontology_evidence is False
        assert results[0].knowledge_object.id == ko.id


# ---------------------------------------------------------------------------
# TestOverlapBetweenPaths
# ---------------------------------------------------------------------------


class TestOverlapBetweenPaths:
    def test_fact_found_by_both_paths_appears_once(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        # Fact text shares the "haven" keyword with the query AND is
        # directly attached to the resolved concept.
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)

        retriever, _, _ = build_retriever(tmp_path, [haven], [ko], attachments=[att])

        results = retriever.retrieve("Haven")

        matching = [r for r in results if _id(r) == ko.id]
        assert len(matching) == 1


# ---------------------------------------------------------------------------
# TestDuplicateElimination
# ---------------------------------------------------------------------------


class TestDuplicateElimination:
    def test_ontology_candidate_wins_over_keyword_duplicate(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)

        retriever, _, _ = build_retriever(tmp_path, [haven], [ko], attachments=[att])

        results = retriever.retrieve("Haven")

        assert len(results) == 1
        # The richer, evidence-carrying representation must be kept, not
        # overwritten by a zero-evidence stand-in for the keyword-path hit.
        assert isinstance(results[0], Candidate)
        assert results[0].has_ontology_evidence is True

    def test_no_double_counting_across_multiple_matching_facts(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude assists with the Haven project")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)

        retriever, _, _ = build_retriever(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
        )

        results = retriever.retrieve("Haven")

        # Both facts mention "haven" (keyword path) and both are directly
        # attached (ontology path); still exactly one entry per KO.
        result_ids = ids_of(results)
        assert sorted(result_ids) == sorted({ko_haven.id, ko_claude.id})
        assert len(result_ids) == len(set(result_ids))


# ---------------------------------------------------------------------------
# TestDeterministicOrdering
# ---------------------------------------------------------------------------


class TestDeterministicOrdering:
    def test_output_sorted_by_knowledge_object_id(self, tmp_path: Path) -> None:
        alpha = concept("Alpha")
        beta = concept("Beta")
        ko_alpha = make_ko("Alpha fact mentions banana")
        ko_beta = make_ko("Beta fact mentions banana")
        att_alpha = Attachment.create(ko_alpha.id, alpha.id)
        att_beta = Attachment.create(ko_beta.id, beta.id)

        retriever, _, _ = build_retriever(
            tmp_path,
            [alpha, beta],
            [ko_alpha, ko_beta],
            attachments=[att_alpha, att_beta],
        )

        results = retriever.retrieve("banana")
        result_ids = ids_of(results)

        assert result_ids == sorted(result_ids, key=str)

    def test_repeated_calls_on_same_instance_are_stable(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)

        retriever, _, _ = build_retriever(tmp_path, [haven], [ko], attachments=[att])

        first = ids_of(retriever.retrieve("Haven"))
        for _ in range(3):
            assert ids_of(retriever.retrieve("Haven")) == first

    def test_independent_builds_agree(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude assists with the Haven project")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)
        rel = Relationship.create(haven.id, claude.id, OntologyRelationshipType.USES)

        retriever_a, _, _ = build_retriever(
            tmp_path / "a",
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
            relationships=[rel],
        )
        retriever_b, _, _ = build_retriever(
            tmp_path / "b",
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
            relationships=[rel],
        )

        assert ids_of(retriever_a.retrieve("Haven")) == ids_of(retriever_b.retrieve("Haven"))


# ---------------------------------------------------------------------------
# TestEmptyQuery
# ---------------------------------------------------------------------------


class TestEmptyQuery:
    def test_empty_query_returns_no_candidates(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)

        retriever, _, _ = build_retriever(tmp_path, [haven], [ko], attachments=[att])

        assert retriever.retrieve("") == []

    def test_whitespace_only_query_returns_no_candidates(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)

        retriever, _, _ = build_retriever(tmp_path, [haven], [ko], attachments=[att])

        assert retriever.retrieve("   ") == []


# ---------------------------------------------------------------------------
# TestEmptyGraph
# ---------------------------------------------------------------------------


class TestEmptyGraph:
    def test_empty_graph_still_returns_keyword_matches(self, tmp_path: Path) -> None:
        ko = make_ko("Claude is an AI assistant by Anthropic")

        retriever, _, _ = build_retriever(tmp_path, [], [ko])

        results = retriever.retrieve("claude")

        assert len(results) == 1
        assert isinstance(results[0], Candidate)
        assert results[0].has_ontology_evidence is False
        assert results[0].knowledge_object.id == ko.id

    def test_empty_graph_and_store_returns_nothing(self, tmp_path: Path) -> None:
        retriever, _, _ = build_retriever(tmp_path, [], [])

        assert retriever.retrieve("anything at all") == []


# ---------------------------------------------------------------------------
# TestEmptyStore
# ---------------------------------------------------------------------------


class TestEmptyStore:
    def test_empty_store_with_unattached_concept_returns_nothing(self, tmp_path: Path) -> None:
        haven = concept("Haven")

        retriever, store, _ = build_retriever(tmp_path, [haven], [])

        assert store.count() == 0
        assert retriever.retrieve("Haven") == []


# ---------------------------------------------------------------------------
# TestStatelessReuse
# ---------------------------------------------------------------------------


class TestStatelessReuse:
    def test_one_instance_many_queries_no_leakage(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)

        retriever, _, _ = build_retriever(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
        )

        first = ids_of(retriever.retrieve("Haven"))
        second = ids_of(retriever.retrieve("Claude"))
        third = ids_of(retriever.retrieve("Haven"))

        assert first == [ko_haven.id]
        assert second == [ko_claude.id]
        assert third == first


# ---------------------------------------------------------------------------
# TestNoGraphMutation
# ---------------------------------------------------------------------------


class TestNoGraphMutation:
    def test_graph_contents_unchanged_after_retrieve(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_claude = Attachment.create(ko_claude.id, claude.id)
        rel = Relationship.create(haven.id, claude.id, OntologyRelationshipType.USES)

        retriever, _, graph = build_retriever(
            tmp_path,
            [haven, claude],
            [ko_claude],
            attachments=[att_claude],
            relationships=[rel],
        )

        before_concepts = {haven.id, claude.id}
        before_relationships = graph.relationships(haven.id)
        before_attachments = graph.attachments_for_concept(claude.id)

        retriever.retrieve("Haven")
        retriever.retrieve("Claude")

        assert graph.has_concept(haven.id) and graph.has_concept(claude.id)
        assert {haven.id, claude.id} == before_concepts
        assert graph.relationships(haven.id) == before_relationships
        assert graph.attachments_for_concept(claude.id) == before_attachments


# ---------------------------------------------------------------------------
# TestNoRankingPerformed
# ---------------------------------------------------------------------------


class TestNoRankingPerformed:
    def test_ordering_ignores_importance_and_activation_strength(self, tmp_path: Path) -> None:
        # Two directly-attached facts with very different importance/confidence;
        # if any ranking were applied, the "stronger" one might sort first.
        # Output order must depend only on KnowledgeObject id.
        alpha = concept("Alpha")
        beta = concept("Beta")
        ko_alpha = make_ko("Alpha fact", importance=0.9, confidence=0.9, confirmation_count=10)
        ko_beta = make_ko("Beta fact", importance=0.1, confidence=0.1, confirmation_count=0)
        att_alpha = Attachment.create(ko_alpha.id, alpha.id)
        att_beta = Attachment.create(ko_beta.id, beta.id)

        retriever, _, _ = build_retriever(
            tmp_path,
            [alpha, beta],
            [ko_alpha, ko_beta],
            attachments=[att_alpha, att_beta],
        )

        results = retriever.retrieve("Alpha and Beta")
        result_ids = ids_of(results)

        assert result_ids == sorted([ko_alpha.id, ko_beta.id], key=str)

    def test_no_ranked_candidate_or_score_breakdown_produced(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)

        retriever, _, _ = build_retriever(tmp_path, [haven], [ko], attachments=[att])

        results = retriever.retrieve("Haven")

        for item in results:
            assert not hasattr(item, "final_score")
            assert not hasattr(item, "score_breakdown")


# ---------------------------------------------------------------------------
# TestArchitecturalBoundaries
# ---------------------------------------------------------------------------


class TestArchitecturalBoundaries:
    def test_module_orchestrates_all_four_components(self) -> None:
        source = Path(hybrid_module.__file__).read_text(encoding="utf-8")
        for required in (
            "QueryResolver",
            "ActivationSpreader",
            "CandidateAssembler",
            "KeywordCandidateRetriever",
        ):
            assert required in source

    def test_module_never_mutates_the_graph(self) -> None:
        source = Path(hybrid_module.__file__).read_text(encoding="utf-8")
        for forbidden in (".add_concept(", ".add_relationship(", ".add_attachment("):
            assert forbidden not in source

    def test_module_never_mutates_the_vault(self) -> None:
        source = Path(hybrid_module.__file__).read_text(encoding="utf-8")
        for forbidden in ("VaultWriter", ".load(", "MemoryParser"):
            assert forbidden not in source

    def test_module_never_imports_ranking_or_allocation_stages(self) -> None:
        source = Path(hybrid_module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "from obsidian.memory_engine.deterministic_ranker",
            "from obsidian.memory_engine.deterministic_slot_allocator",
            "from obsidian.memory_engine.context_builder",
        ):
            assert forbidden not in source

    def test_public_api_is_retrieve_and_retrieve_with_diagnostics(self) -> None:
        # retrieve_with_diagnostics is the sanctioned Retrieval Inspector
        # entry point: retrieve() is defined as its first return value, so
        # the two can never disagree about the returned candidate list.
        public_methods = [
            name
            for name in vars(HybridCandidateRetriever)
            if not name.startswith("_") and callable(getattr(HybridCandidateRetriever, name))
        ]
        assert sorted(public_methods) == ["retrieve", "retrieve_with_diagnostics"]


# ---------------------------------------------------------------------------
# TestRetrieveWithDiagnostics
# ---------------------------------------------------------------------------


class TestRetrieveWithDiagnostics:
    def test_matches_retrieve_output(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        retriever, _, _ = build_retriever(tmp_path, [haven], [ko], attachments=[att])

        via_retrieve = retriever.retrieve("Haven")
        via_diagnostics, _ = retriever.retrieve_with_diagnostics("Haven")

        assert ids_of(via_retrieve) == ids_of(via_diagnostics)

    def test_ontology_only_candidate_flags(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        # "Haven" does not appear in the fact text, so the keyword path
        # cannot independently find this KnowledgeObject.
        ko = make_ko("Personal second-brain project used daily")
        att = Attachment.create(ko.id, haven.id)
        retriever, _, _ = build_retriever(tmp_path, [haven], [ko], attachments=[att])

        candidates, provenance = retriever.retrieve_with_diagnostics("Haven")

        assert ko.id in provenance.matched_by_ontology
        assert ko.id not in provenance.matched_by_keyword
        assert provenance.ontology_candidate_count == 1
        assert provenance.keyword_candidate_count == 0

    def test_keyword_only_candidate_flags(self, tmp_path: Path) -> None:
        ko = make_ko("Claude is an AI assistant by Anthropic")
        retriever, _, _ = build_retriever(tmp_path, [], [ko])

        candidates, provenance = retriever.retrieve_with_diagnostics("claude")

        assert ko.id in provenance.matched_by_keyword
        assert ko.id not in provenance.matched_by_ontology
        assert provenance.ontology_candidate_count == 0
        assert provenance.keyword_candidate_count == 1

    def test_both_paths_candidate_flags(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        retriever, _, _ = build_retriever(tmp_path, [haven], [ko], attachments=[att])

        candidates, provenance = retriever.retrieve_with_diagnostics("Haven")

        assert ko.id in provenance.matched_by_ontology
        assert ko.id in provenance.matched_by_keyword
        assert len(candidates) == 1

    def test_empty_query_yields_zero_counts(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        retriever, _, _ = build_retriever(tmp_path, [haven], [ko], attachments=[att])

        candidates, provenance = retriever.retrieve_with_diagnostics("")

        assert candidates == []
        assert provenance.ontology_candidate_count == 0
        assert provenance.keyword_candidate_count == 0


# ---------------------------------------------------------------------------
# TestKeywordOverlapScoreWiring
# ---------------------------------------------------------------------------


class TestKeywordOverlapScoreWiring:
    def test_keyword_only_candidate_carries_its_score(self, tmp_path: Path) -> None:
        ko = make_ko("Claude is an AI assistant by Anthropic")
        retriever, _, _ = build_retriever(tmp_path, [], [ko])

        candidates = retriever.retrieve("claude")

        assert len(candidates) == 1
        assert candidates[0].has_ontology_evidence is False
        assert candidates[0].keyword_overlap_score > 0.0

    def test_ontology_only_candidate_has_zero_keyword_score(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        # "Haven" does not appear in the fact text, so the keyword path
        # cannot independently find this KnowledgeObject.
        ko = make_ko("Personal second-brain project used daily")
        att = Attachment.create(ko.id, haven.id)
        retriever, _, _ = build_retriever(tmp_path, [haven], [ko], attachments=[att])

        candidates = retriever.retrieve("Haven")

        assert len(candidates) == 1
        assert candidates[0].has_ontology_evidence is True
        assert candidates[0].keyword_overlap_score == 0.0

    def test_candidate_found_by_both_paths_keeps_both_ontology_evidence_and_keyword_score(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        # Shares the "haven" keyword with the query AND is directly
        # attached, so both paths find it.
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        retriever, _, _ = build_retriever(tmp_path, [haven], [ko], attachments=[att])

        candidates = retriever.retrieve("Haven")

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.has_ontology_evidence is True
        assert candidate.attachment_relevance > 0.0
        assert candidate.keyword_overlap_score > 0.0

    def test_unrelated_candidate_never_produced(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko_attached = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko_attached.id, haven.id)
        # No concept resolves this KO and it shares no keyword with the
        # query either.
        ko_unrelated = make_ko("Bananas are a good source of potassium")
        retriever, _, _ = build_retriever(
            tmp_path, [haven], [ko_attached, ko_unrelated], attachments=[att]
        )

        candidates = retriever.retrieve("Haven")

        assert len(candidates) == 1
        assert candidates[0].knowledge_object.id == ko_attached.id


def _id(item):
    return item.knowledge_object.id
