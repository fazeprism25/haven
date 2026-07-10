"""Unit tests for obsidian.memory_engine.deterministic_slot_allocator.DeterministicSlotAllocator.

Test groups
-----------
TestBudgetTruncation           — fewer/equal/more candidates than max_results.
TestSelectionNotTruncation      — unsorted input still yields the truly
                                    highest-ranked prefix, not the first N.
TestOrdering                    — output is sorted descending by final_score.
TestTieBreak                    — equal final_score -> ascending KO id,
                                    matching RankedCandidate's own contract.
TestIdentityPreservation        — returned entries are the exact same
                                    RankedCandidate objects, never copies.
TestNoMutation                  — input list/candidates/config untouched.
TestEmptyInput                  — allocate([]) == [].
TestStatelessReuse              — one instance, many calls, no leakage.
TestNoOutOfScopeImports         — module never imports retrieval/ranking/
                                    context-building/formatting code.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

import pytest

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine.deterministic_slot_allocator import (
    DeterministicSlotAllocator,
)
from obsidian.ontology.retrieval_config import RetrievalConfig
from obsidian.ontology.retrieval_models import ActivatedConcept, Candidate, RankedCandidate

NOW = datetime(2026, 7, 2, 12, 0, 0)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def make_ko(
    fact: str = "Haven uses Claude",
    ko_id: Optional[UUID] = None,
    valid_from: datetime = NOW,
) -> KnowledgeObject:
    return KnowledgeObject(
        id=ko_id if ko_id is not None else uuid4(),
        canonical_fact=fact,
        memory_type=MemoryType.FACT,
        valid_from=valid_from,
    )


def make_activated_concept() -> ActivatedConcept:
    cid = uuid4()
    return ActivatedConcept(
        concept_id=cid, activation_score=1.0, activation_depth=0, source_seed=cid
    )


def make_candidate(ko: Optional[KnowledgeObject] = None) -> Candidate:
    return Candidate(
        knowledge_object=ko if ko is not None else make_ko(),
        supporting_concepts=(make_activated_concept(),),
        attachment_relevance=0.5,
        activation_score=0.5,
    )


def make_ranked(
    final_score: float,
    ko_id: Optional[UUID] = None,
    fact: str = "fact",
) -> RankedCandidate:
    ko = make_ko(fact=fact, ko_id=ko_id)
    return RankedCandidate(
        candidate=make_candidate(ko=ko),
        final_score=final_score,
        score_breakdown={"activation": final_score},
    )


def config(max_results: int) -> RetrievalConfig:
    return RetrievalConfig(max_results=max_results)


# ---------------------------------------------------------------------------
# TestBudgetTruncation
# ---------------------------------------------------------------------------


class TestBudgetTruncation:
    def test_fewer_candidates_than_budget_returns_all(self) -> None:
        ranked = [make_ranked(0.9), make_ranked(0.5)]
        result = DeterministicSlotAllocator().allocate(ranked, config(max_results=10))
        assert len(result) == 2

    def test_equal_candidates_to_budget_returns_all(self) -> None:
        ranked = [make_ranked(0.9), make_ranked(0.5), make_ranked(0.1)]
        result = DeterministicSlotAllocator().allocate(ranked, config(max_results=3))
        assert len(result) == 3

    def test_more_candidates_than_budget_truncates(self) -> None:
        ranked = [make_ranked(score) for score in [0.9, 0.8, 0.7, 0.6, 0.5]]
        result = DeterministicSlotAllocator().allocate(ranked, config(max_results=2))
        assert len(result) == 2

    def test_budget_of_one_returns_single_highest(self) -> None:
        low = make_ranked(0.1)
        high = make_ranked(0.9)
        result = DeterministicSlotAllocator().allocate([low, high], config(max_results=1))
        assert len(result) == 1
        assert result[0] is high


# ---------------------------------------------------------------------------
# TestSelectionNotTruncation
# ---------------------------------------------------------------------------


class TestSelectionNotTruncation:
    def test_unsorted_input_still_selects_true_highest(self) -> None:
        """Feeding candidates in ascending (wrong) order must not fool the
        allocator into keeping the first N — it must re-derive rank order."""
        low = make_ranked(0.1)
        mid = make_ranked(0.5)
        high = make_ranked(0.9)
        # Deliberately out of rank order.
        result = DeterministicSlotAllocator().allocate([low, mid, high], config(max_results=2))
        assert result == [high, mid]

    def test_reverse_input_order_yields_same_result_as_forward(self) -> None:
        candidates = [make_ranked(s) for s in [0.2, 0.9, 0.5, 0.1, 0.7]]
        forward = DeterministicSlotAllocator().allocate(candidates, config(max_results=3))
        backward = DeterministicSlotAllocator().allocate(list(reversed(candidates)), config(max_results=3))
        assert [rc.candidate.knowledge_object.id for rc in forward] == [
            rc.candidate.knowledge_object.id for rc in backward
        ]


# ---------------------------------------------------------------------------
# TestOrdering
# ---------------------------------------------------------------------------


class TestOrdering:
    def test_output_sorted_descending_by_final_score(self) -> None:
        candidates = [make_ranked(s) for s in [0.3, 0.8, 0.1, 0.6]]
        result = DeterministicSlotAllocator().allocate(candidates, config(max_results=10))
        scores = [rc.final_score for rc in result]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# TestTieBreak
# ---------------------------------------------------------------------------


class TestTieBreak:
    def test_equal_final_score_broken_by_ascending_knowledge_object_id(self) -> None:
        ko_a = make_ko("fact A")
        ko_b = make_ko("fact B")
        first_id, second_id = sorted([ko_a.id, ko_b.id], key=str)
        ko_first = ko_a if ko_a.id == first_id else ko_b
        ko_second = ko_b if ko_first is ko_a else ko_a

        rc_first = make_ranked(0.5, ko_id=ko_first.id, fact=ko_first.canonical_fact)
        rc_second = make_ranked(0.5, ko_id=ko_second.id, fact=ko_second.canonical_fact)

        result = DeterministicSlotAllocator().allocate([rc_second, rc_first], config(max_results=2))

        assert result[0].candidate.knowledge_object.id == first_id
        assert result[1].candidate.knowledge_object.id == second_id


# ---------------------------------------------------------------------------
# TestIdentityPreservation
# ---------------------------------------------------------------------------


class TestIdentityPreservation:
    def test_returned_entries_are_the_same_objects(self) -> None:
        high = make_ranked(0.9)
        low = make_ranked(0.1)
        result = DeterministicSlotAllocator().allocate([low, high], config(max_results=2))
        assert result[0] is high
        assert result[1] is low

    def test_score_and_breakdown_are_not_recomputed(self) -> None:
        rc = make_ranked(0.42)
        result = DeterministicSlotAllocator().allocate([rc], config(max_results=1))
        assert result[0].final_score == 0.42
        assert dict(result[0].score_breakdown) == dict(rc.score_breakdown)


# ---------------------------------------------------------------------------
# TestNoMutation
# ---------------------------------------------------------------------------


class TestNoMutation:
    def test_input_list_is_not_mutated(self) -> None:
        low = make_ranked(0.1)
        high = make_ranked(0.9)
        ranked = [low, high]
        snapshot = list(ranked)
        DeterministicSlotAllocator().allocate(ranked, config(max_results=1))
        assert ranked == snapshot

    def test_config_is_not_mutated(self) -> None:
        cfg = config(max_results=1)
        snapshot = dataclasses.replace(cfg)
        DeterministicSlotAllocator().allocate([make_ranked(0.5)], cfg)
        assert cfg == snapshot


# ---------------------------------------------------------------------------
# TestEmptyInput
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_empty_candidates_returns_empty_list(self) -> None:
        result = DeterministicSlotAllocator().allocate([], config(max_results=5))
        assert result == []


# ---------------------------------------------------------------------------
# TestStatelessReuse
# ---------------------------------------------------------------------------


class TestStatelessReuse:
    def test_same_instance_reused_across_calls(self) -> None:
        allocator = DeterministicSlotAllocator()
        first = allocator.allocate([make_ranked(0.3)], config(max_results=1))
        second = allocator.allocate([make_ranked(0.8)], config(max_results=1))
        assert first[0].final_score != second[0].final_score


# ---------------------------------------------------------------------------
# TestNoOutOfScopeImports
# ---------------------------------------------------------------------------


class TestNoOutOfScopeImports:
    def test_module_does_not_bind_retrieval_ranking_or_formatting_names(self) -> None:
        """The module's docstring *discusses* DeterministicRanker/MemoryStore/
        ConceptGraph/ContextBuilder as out-of-scope context, but must never
        actually import or bind them — checked against the module namespace
        rather than raw source text so prose mentions don't produce false
        positives."""
        import obsidian.memory_engine.deterministic_slot_allocator as module

        forbidden = {
            "DeterministicRanker",
            "MemoryStore",
            "ConceptGraph",
            "ContextBuilder",
        }
        assert forbidden.isdisjoint(dir(module))
