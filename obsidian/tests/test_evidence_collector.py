"""Unit tests for obsidian.ontology.evidence_collector.

Test groups
-----------
TestSingleAttachment        — one concept with one KO attachment.
TestMultipleAttachments     — one concept with several KO attachments.
TestDuplicateAttachments    — same concept supplied twice in the input list.
TestMultipleConcepts        — two or more concepts each with attachments.
TestDeterministicOrdering   — output order is stable across calls and
                              insertion orders.
TestEmptyGraph              — no attachments registered in the graph.
TestNoAttachments           — concepts present in the graph but with no
                              attachments.
TestRelevancePreservation   — attachment_relevance is carried forward exactly.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.evidence_collector import EvidenceCollector, ScoredCandidate
from obsidian.ontology.models import Attachment, Concept


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_concept(label: str) -> Concept:
    return Concept.from_label(label)


def make_graph(*concepts: Concept) -> ConceptGraph:
    g = ConceptGraph()
    for c in concepts:
        g.add_concept(c)
    return g


def attach(graph: ConceptGraph, ko_id: UUID, concept: Concept, relevance: float = 1.0) -> None:
    graph.add_attachment(Attachment.create(ko_id, concept.id, relevance=relevance))


def collect(concepts: list[Concept], graph: ConceptGraph) -> list[ScoredCandidate]:
    return EvidenceCollector().collect(concepts, graph)


# ---------------------------------------------------------------------------
# TestSingleAttachment
# ---------------------------------------------------------------------------


class TestSingleAttachment:
    def test_returns_one_candidate(self) -> None:
        c = make_concept("Haven")
        ko = uuid4()
        g = make_graph(c)
        attach(g, ko, c)
        result = collect([c], g)
        assert len(result) == 1

    def test_candidate_has_correct_knowledge_object_id(self) -> None:
        c = make_concept("Haven")
        ko = uuid4()
        g = make_graph(c)
        attach(g, ko, c)
        result = collect([c], g)
        assert result[0].knowledge_object_id == ko

    def test_candidate_has_correct_concept_id(self) -> None:
        c = make_concept("Haven")
        ko = uuid4()
        g = make_graph(c)
        attach(g, ko, c)
        result = collect([c], g)
        assert result[0].concept_id == c.id

    def test_candidate_has_default_relevance(self) -> None:
        c = make_concept("Haven")
        ko = uuid4()
        g = make_graph(c)
        attach(g, ko, c, relevance=1.0)
        result = collect([c], g)
        assert result[0].attachment_relevance == 1.0

    def test_empty_concept_list_returns_empty(self) -> None:
        c = make_concept("Haven")
        ko = uuid4()
        g = make_graph(c)
        attach(g, ko, c)
        result = collect([], g)
        assert result == []


# ---------------------------------------------------------------------------
# TestMultipleAttachments
# ---------------------------------------------------------------------------


class TestMultipleAttachments:
    def test_two_attachments_on_one_concept(self) -> None:
        c = make_concept("Claude")
        ko1, ko2 = uuid4(), uuid4()
        g = make_graph(c)
        attach(g, ko1, c)
        attach(g, ko2, c)
        result = collect([c], g)
        assert len(result) == 2

    def test_three_attachments_on_one_concept(self) -> None:
        c = make_concept("Claude")
        kos = [uuid4() for _ in range(3)]
        g = make_graph(c)
        for ko in kos:
            attach(g, ko, c)
        result = collect([c], g)
        assert len(result) == 3

    def test_all_knowledge_object_ids_present(self) -> None:
        c = make_concept("Claude")
        kos = {uuid4(), uuid4(), uuid4()}
        g = make_graph(c)
        for ko in kos:
            attach(g, ko, c)
        result_ids = {r.knowledge_object_id for r in collect([c], g)}
        assert result_ids == kos

    def test_all_candidates_share_same_concept_id(self) -> None:
        c = make_concept("Claude")
        g = make_graph(c)
        attach(g, uuid4(), c)
        attach(g, uuid4(), c)
        for cand in collect([c], g):
            assert cand.concept_id == c.id


# ---------------------------------------------------------------------------
# TestDuplicateAttachments
# ---------------------------------------------------------------------------


class TestDuplicateAttachments:
    def test_same_concept_twice_in_input_deduplicates(self) -> None:
        c = make_concept("Haven")
        ko = uuid4()
        g = make_graph(c)
        attach(g, ko, c)
        # supply c twice
        result = collect([c, c], g)
        assert len(result) == 1

    def test_same_concept_three_times_in_input_deduplicates(self) -> None:
        c = make_concept("Haven")
        kos = [uuid4(), uuid4()]
        g = make_graph(c)
        for ko in kos:
            attach(g, ko, c)
        result = collect([c, c, c], g)
        assert len(result) == 2

    def test_graph_idempotent_add_same_attachment_twice(self) -> None:
        c = make_concept("Haven")
        ko = uuid4()
        g = make_graph(c)
        a = Attachment.create(ko, c.id, relevance=0.7)
        g.add_attachment(a)
        g.add_attachment(a)  # second add is a no-op
        result = collect([c], g)
        assert len(result) == 1
        assert result[0].attachment_relevance == 0.7

    def test_duplicate_concept_in_input_does_not_double_count(self) -> None:
        c = make_concept("Haven")
        ko = uuid4()
        g = make_graph(c)
        attach(g, ko, c, relevance=0.5)
        result = collect([c, c], g)
        assert len(result) == 1
        assert result[0].attachment_relevance == 0.5


# ---------------------------------------------------------------------------
# TestMultipleConcepts
# ---------------------------------------------------------------------------


class TestMultipleConcepts:
    def test_two_concepts_independent_attachments(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        ko1, ko2 = uuid4(), uuid4()
        g = make_graph(c1, c2)
        attach(g, ko1, c1)
        attach(g, ko2, c2)
        result = collect([c1, c2], g)
        assert len(result) == 2

    def test_shared_ko_across_two_concepts_yields_two_candidates(self) -> None:
        # Same KO attached to two different concepts → two distinct candidates
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        ko = uuid4()
        g = make_graph(c1, c2)
        attach(g, ko, c1)
        attach(g, ko, c2)
        result = collect([c1, c2], g)
        assert len(result) == 2
        concept_ids = {r.concept_id for r in result}
        assert concept_ids == {c1.id, c2.id}
        # both reference the same KO
        assert all(r.knowledge_object_id == ko for r in result)

    def test_concept_not_in_graph_is_skipped(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        ko = uuid4()
        g = make_graph(c1)  # c2 is NOT added
        attach(g, ko, c1)
        result = collect([c1, c2], g)
        assert len(result) == 1
        assert result[0].concept_id == c1.id

    def test_three_concepts_aggregated_correctly(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        c3 = make_concept("DTU")
        g = make_graph(c1, c2, c3)
        ko1, ko2, ko3 = uuid4(), uuid4(), uuid4()
        attach(g, ko1, c1)
        attach(g, ko2, c2)
        attach(g, ko3, c3)
        result = collect([c1, c2, c3], g)
        assert len(result) == 3
        result_kos = {r.knowledge_object_id for r in result}
        assert result_kos == {ko1, ko2, ko3}


# ---------------------------------------------------------------------------
# TestDeterministicOrdering
# ---------------------------------------------------------------------------


class TestDeterministicOrdering:
    def test_repeated_calls_same_result(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        g = make_graph(c1, c2)
        ko1, ko2 = uuid4(), uuid4()
        attach(g, ko1, c1)
        attach(g, ko2, c2)
        first = collect([c1, c2], g)
        for _ in range(9):
            assert collect([c1, c2], g) == first

    def test_input_order_does_not_affect_output_order(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        g = make_graph(c1, c2)
        ko1, ko2 = uuid4(), uuid4()
        attach(g, ko1, c1)
        attach(g, ko2, c2)
        result_ab = collect([c1, c2], g)
        result_ba = collect([c2, c1], g)
        assert result_ab == result_ba

    def test_sorted_by_concept_id_then_ko_id(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        g = make_graph(c1, c2)
        ko1, ko2 = uuid4(), uuid4()
        attach(g, ko1, c1)
        attach(g, ko2, c2)
        result = collect([c1, c2], g)
        keys = [(str(r.concept_id), str(r.knowledge_object_id)) for r in result]
        assert keys == sorted(keys)

    def test_two_kos_on_same_concept_sorted_by_ko_id(self) -> None:
        c = make_concept("Haven")
        g = make_graph(c)
        ko_a, ko_b = uuid4(), uuid4()
        attach(g, ko_a, c)
        attach(g, ko_b, c)
        result = collect([c], g)
        ko_strs = [str(r.knowledge_object_id) for r in result]
        assert ko_strs == sorted(ko_strs)


# ---------------------------------------------------------------------------
# TestEmptyGraph
# ---------------------------------------------------------------------------


class TestEmptyGraph:
    def test_no_concepts_in_graph_returns_empty(self) -> None:
        c = make_concept("Haven")
        g = ConceptGraph()  # nothing added
        result = collect([c], g)
        assert result == []

    def test_empty_concept_list_and_empty_graph_returns_empty(self) -> None:
        g = ConceptGraph()
        result = collect([], g)
        assert result == []

    def test_concept_in_input_but_not_graph_returns_empty(self) -> None:
        c = make_concept("Haven")
        g = ConceptGraph()  # c never added
        result = collect([c], g)
        assert result == []


# ---------------------------------------------------------------------------
# TestNoAttachments
# ---------------------------------------------------------------------------


class TestNoAttachments:
    def test_concept_with_no_attachments_returns_empty(self) -> None:
        c = make_concept("Haven")
        g = make_graph(c)  # concept added, but no attachments
        result = collect([c], g)
        assert result == []

    def test_multiple_concepts_none_with_attachments(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        g = make_graph(c1, c2)
        result = collect([c1, c2], g)
        assert result == []

    def test_one_with_attachments_one_without(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        ko = uuid4()
        g = make_graph(c1, c2)
        attach(g, ko, c1)  # c2 has no attachment
        result = collect([c1, c2], g)
        assert len(result) == 1
        assert result[0].concept_id == c1.id


# ---------------------------------------------------------------------------
# TestRelevancePreservation
# ---------------------------------------------------------------------------


class TestRelevancePreservation:
    def test_zero_relevance_preserved(self) -> None:
        c = make_concept("Haven")
        ko = uuid4()
        g = make_graph(c)
        attach(g, ko, c, relevance=0.0)
        result = collect([c], g)
        assert result[0].attachment_relevance == 0.0

    def test_one_relevance_preserved(self) -> None:
        c = make_concept("Haven")
        ko = uuid4()
        g = make_graph(c)
        attach(g, ko, c, relevance=1.0)
        result = collect([c], g)
        assert result[0].attachment_relevance == 1.0

    def test_fractional_relevance_preserved(self) -> None:
        c = make_concept("Haven")
        ko = uuid4()
        g = make_graph(c)
        attach(g, ko, c, relevance=0.42)
        result = collect([c], g)
        assert result[0].attachment_relevance == pytest.approx(0.42)

    def test_distinct_relevances_per_attachment(self) -> None:
        c = make_concept("Haven")
        ko1, ko2 = uuid4(), uuid4()
        g = make_graph(c)
        attach(g, ko1, c, relevance=0.3)
        attach(g, ko2, c, relevance=0.9)
        result = collect([c], g)
        relevances = {r.knowledge_object_id: r.attachment_relevance for r in result}
        assert relevances[ko1] == pytest.approx(0.3)
        assert relevances[ko2] == pytest.approx(0.9)

    def test_same_ko_different_concepts_independent_relevances(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        ko = uuid4()
        g = make_graph(c1, c2)
        attach(g, ko, c1, relevance=0.6)
        attach(g, ko, c2, relevance=0.8)
        result = collect([c1, c2], g)
        relevances = {r.concept_id: r.attachment_relevance for r in result}
        assert relevances[c1.id] == pytest.approx(0.6)
        assert relevances[c2.id] == pytest.approx(0.8)
