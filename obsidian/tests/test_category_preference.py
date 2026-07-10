"""Unit tests for obsidian.memory_engine.category_preference.

Test groups
-----------
TestRequestedCategoryReceivesBonus     — a candidate whose resolved category
                                          appears in ``ContextPlan.requirements``
                                          gets exactly ``CATEGORY_PREFERENCE_BONUS``
                                          added to its final_score.
TestUnrequestedCategoryNoBonus         — a candidate whose category is not
                                          requested is scored unchanged.
TestUnmappedMemoryTypeNoBonus          — a candidate whose ``MemoryType`` has
                                          no ``ContextCategory`` mapping at
                                          all never receives a bonus.
TestEmptyRequirementsIsNoOp            — the ``TaskMode.POINTED_QA`` sentinel
                                          (empty requirements) leaves every
                                          candidate's score untouched --
                                          backward compatibility.
TestBonusIsBounded                     — the bonus never pushes final_score
                                          above 1.0, and is never anything
                                          other than 0.0 or
                                          CATEGORY_PREFERENCE_BONUS.
TestUnrelatedCandidateStillWins        — a clearly-better unrequested-category
                                          candidate still outranks a weaker
                                          requested-category one; the bonus
                                          only closes small gaps.
TestDeterministicOrdering              — descending final_score, ties broken
                                          by ascending knowledge_object id,
                                          same as RankedCandidate's own
                                          contract.
TestRepeatedCallsAreDeterministic      — same input, same output, every time.
TestNoMutation                          — input list/candidates are never
                                          mutated.
TestAsRankedCandidate                   — projects back to a RankedCandidate
                                          AcceptanceStage can consume
                                          unmodified.
TestCategoryPreferenceScoreValidation  — final_score/bonus bound checks.
TestCategoryResolutionReusesCoverageAnalyzer
                                        — category resolution is the exact
                                          same table/function
                                          coverage_analyzer uses, so the two
                                          diagnostics never disagree.
TestStatelessReuse                      — one scorer instance, many calls,
                                          no leakage.
"""

from __future__ import annotations

import dataclasses
from typing import Optional
from uuid import UUID, uuid4

import pytest

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine.category_preference import (
    CATEGORY_PREFERENCE_BONUS,
    CategoryPreferenceScore,
    CategoryPreferenceScorer,
)
from obsidian.memory_engine.context_planner import (
    CategoryRequirement,
    ContextCategory,
    ContextPlan,
    Necessity,
    TaskMode,
)
from obsidian.memory_engine.coverage_analyzer import MEMORY_TYPE_CATEGORY, resolve_category
from obsidian.ontology.retrieval_models import Candidate, RankedCandidate

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def make_ko(
    memory_type: MemoryType = MemoryType.FACT,
    ko_id: Optional[UUID] = None,
    fact: str = "Haven uses Claude",
) -> KnowledgeObject:
    return KnowledgeObject(
        id=ko_id if ko_id is not None else uuid4(),
        canonical_fact=fact,
        memory_type=memory_type,
        importance=0.5,
        confidence=0.5,
    )


def make_candidate(memory_type: MemoryType = MemoryType.FACT, ko_id: Optional[UUID] = None) -> Candidate:
    return Candidate(
        knowledge_object=make_ko(memory_type=memory_type, ko_id=ko_id),
        supporting_concepts=(),
        attachment_relevance=0.0,
        activation_score=0.0,
    )


def make_ranked(
    memory_type: MemoryType = MemoryType.FACT,
    final_score: float = 0.5,
    ko_id: Optional[UUID] = None,
) -> RankedCandidate:
    return RankedCandidate(
        candidate=make_candidate(memory_type=memory_type, ko_id=ko_id),
        final_score=final_score,
        score_breakdown={"activation": final_score},
    )


def make_plan(*categories: ContextCategory, necessity: Necessity = Necessity.REQUIRED) -> ContextPlan:
    return ContextPlan(
        query="q",
        task_mode=TaskMode.CODING_DEBUGGING if categories else TaskMode.POINTED_QA,
        requirements=tuple(
            CategoryRequirement(category=c, necessity=necessity) for c in categories
        ),
    )


EMPTY_PLAN = ContextPlan(query="", task_mode=TaskMode.POINTED_QA, requirements=())


# ---------------------------------------------------------------------------
# TestRequestedCategoryReceivesBonus
# ---------------------------------------------------------------------------


class TestRequestedCategoryReceivesBonus:
    def test_matching_category_gets_the_fixed_bonus(self) -> None:
        # MemoryType.DECISION -> ContextCategory.DECISION (see MEMORY_TYPE_CATEGORY).
        ranked = make_ranked(memory_type=MemoryType.DECISION, final_score=0.5)
        plan = make_plan(ContextCategory.DECISION)

        scores = CategoryPreferenceScorer().score([ranked], plan)

        assert len(scores) == 1
        assert scores[0].category_preference_bonus == CATEGORY_PREFERENCE_BONUS
        assert scores[0].requested_category == ContextCategory.DECISION
        assert scores[0].base_score == 0.5
        assert scores[0].final_score == pytest.approx(0.5 + CATEGORY_PREFERENCE_BONUS)

    def test_multiple_requested_categories_each_get_the_bonus(self) -> None:
        ranked_decision = make_ranked(memory_type=MemoryType.DECISION, final_score=0.4)
        ranked_task = make_ranked(memory_type=MemoryType.TASK, final_score=0.3)
        plan = make_plan(ContextCategory.DECISION, ContextCategory.TASK)

        scores = CategoryPreferenceScorer().score([ranked_decision, ranked_task], plan)

        assert all(s.category_preference_bonus == CATEGORY_PREFERENCE_BONUS for s in scores)


# ---------------------------------------------------------------------------
# TestUnrequestedCategoryNoBonus
# ---------------------------------------------------------------------------


class TestUnrequestedCategoryNoBonus:
    def test_non_requested_category_is_untouched(self) -> None:
        # MemoryType.TASK -> ContextCategory.TASK, but only DECISION requested.
        ranked = make_ranked(memory_type=MemoryType.TASK, final_score=0.6)
        plan = make_plan(ContextCategory.DECISION)

        scores = CategoryPreferenceScorer().score([ranked], plan)

        assert scores[0].category_preference_bonus == 0.0
        assert scores[0].requested_category is None
        assert scores[0].final_score == 0.6
        assert scores[0].base_score == 0.6


# ---------------------------------------------------------------------------
# TestUnmappedMemoryTypeNoBonus
# ---------------------------------------------------------------------------


class TestUnmappedMemoryTypeNoBonus:
    @pytest.mark.parametrize(
        "memory_type",
        [
            MemoryType.GOAL,
            MemoryType.PROJECT,
            MemoryType.PERSON,
            MemoryType.PREFERENCE,
        ],
    )
    def test_unmapped_memory_types_never_receive_a_bonus(self, memory_type: MemoryType) -> None:
        assert memory_type not in MEMORY_TYPE_CATEGORY
        ranked = make_ranked(memory_type=memory_type, final_score=0.5)
        # A plan requesting every mapped category still can't match an
        # unmapped memory_type.
        plan = make_plan(*ContextCategory)

        scores = CategoryPreferenceScorer().score([ranked], plan)

        assert scores[0].category_preference_bonus == 0.0
        assert scores[0].requested_category is None


# ---------------------------------------------------------------------------
# TestEmptyRequirementsIsNoOp
# ---------------------------------------------------------------------------


class TestEmptyRequirementsIsNoOp:
    def test_pointed_qa_sentinel_never_applies_a_bonus(self) -> None:
        ranked = [
            make_ranked(memory_type=MemoryType.DECISION, final_score=0.9),
            make_ranked(memory_type=MemoryType.TASK, final_score=0.5),
            make_ranked(memory_type=MemoryType.RULE, final_score=0.1),
        ]

        scores = CategoryPreferenceScorer().score(ranked, EMPTY_PLAN)

        assert all(s.category_preference_bonus == 0.0 for s in scores)
        assert all(s.final_score == s.base_score for s in scores)

    def test_order_matches_plain_ranked_candidate_sort_when_no_requirements(self) -> None:
        ranked = [
            make_ranked(final_score=0.3),
            make_ranked(final_score=0.9),
            make_ranked(final_score=0.6),
        ]

        scores = CategoryPreferenceScorer().score(ranked, EMPTY_PLAN)

        assert [s.final_score for s in scores] == [0.9, 0.6, 0.3]
        assert [s.as_ranked_candidate() for s in scores] == sorted(ranked)


# ---------------------------------------------------------------------------
# TestBonusIsBounded
# ---------------------------------------------------------------------------


class TestBonusIsBounded:
    def test_final_score_clamped_at_one(self) -> None:
        ranked = make_ranked(memory_type=MemoryType.DECISION, final_score=0.98)
        plan = make_plan(ContextCategory.DECISION)

        scores = CategoryPreferenceScorer().score([ranked], plan)

        assert scores[0].final_score == 1.0

    def test_bonus_is_never_anything_but_zero_or_the_fixed_constant(self) -> None:
        candidates = [
            make_ranked(memory_type=MemoryType.DECISION, final_score=0.5),
            make_ranked(memory_type=MemoryType.TASK, final_score=0.5),
            make_ranked(memory_type=MemoryType.GOAL, final_score=0.5),
        ]
        plan = make_plan(ContextCategory.DECISION)

        scores = CategoryPreferenceScorer().score(candidates, plan)

        for s in scores:
            assert s.category_preference_bonus in (0.0, CATEGORY_PREFERENCE_BONUS)


# ---------------------------------------------------------------------------
# TestUnrelatedCandidateStillWins
# ---------------------------------------------------------------------------


class TestUnrelatedCandidateStillWins:
    def test_a_clearly_better_unrequested_candidate_still_ranks_first(self) -> None:
        # The gap (0.4) is far larger than CATEGORY_PREFERENCE_BONUS (0.05),
        # so the requested-category candidate's bonus cannot close it.
        strong_unrelated = make_ranked(memory_type=MemoryType.TASK, final_score=0.9)
        weak_requested = make_ranked(memory_type=MemoryType.DECISION, final_score=0.5)
        plan = make_plan(ContextCategory.DECISION)

        scores = CategoryPreferenceScorer().score(
            [strong_unrelated, weak_requested], plan
        )

        assert scores[0].ranked_candidate is strong_unrelated
        assert scores[0].final_score > scores[1].final_score

    def test_a_close_race_can_be_tipped_by_the_bonus(self) -> None:
        # A small enough gap (less than CATEGORY_PREFERENCE_BONUS) CAN be
        # closed -- this is the intended "soft preference" effect.
        slightly_ahead_unrelated = make_ranked(memory_type=MemoryType.TASK, final_score=0.52)
        close_requested = make_ranked(memory_type=MemoryType.DECISION, final_score=0.5)
        plan = make_plan(ContextCategory.DECISION)

        scores = CategoryPreferenceScorer().score(
            [slightly_ahead_unrelated, close_requested], plan
        )

        assert scores[0].ranked_candidate is close_requested
        assert scores[0].final_score == pytest.approx(0.55)


# ---------------------------------------------------------------------------
# TestDeterministicOrdering
# ---------------------------------------------------------------------------


class TestDeterministicOrdering:
    def test_descending_final_score(self) -> None:
        ranked = [
            make_ranked(final_score=0.2),
            make_ranked(final_score=0.8),
            make_ranked(final_score=0.5),
        ]

        scores = CategoryPreferenceScorer().score(ranked, EMPTY_PLAN)

        assert [s.final_score for s in scores] == sorted(
            (s.final_score for s in scores), reverse=True
        )

    def test_tie_break_is_ascending_knowledge_object_id(self) -> None:
        low_id = make_ranked(final_score=0.5, ko_id=UUID(int=1))
        high_id = make_ranked(final_score=0.5, ko_id=UUID(int=2))

        scores = CategoryPreferenceScorer().score([high_id, low_id], EMPTY_PLAN)

        assert scores[0].ranked_candidate is low_id
        assert scores[1].ranked_candidate is high_id

    def test_bonus_induced_tie_is_broken_deterministically(self) -> None:
        # Two different base scores that land on the same final_score once
        # only one receives the bonus -- still must tie-break by id, not by
        # input order.
        higher_id = UUID(int=2)
        lower_id = UUID(int=1)
        requested = make_ranked(
            memory_type=MemoryType.DECISION, final_score=0.50, ko_id=higher_id
        )
        unrequested = make_ranked(
            memory_type=MemoryType.TASK, final_score=0.55, ko_id=lower_id
        )
        plan = make_plan(ContextCategory.DECISION)

        scores = CategoryPreferenceScorer().score([requested, unrequested], plan)

        assert scores[0].final_score == scores[1].final_score == pytest.approx(0.55)
        assert scores[0].ranked_candidate.candidate.knowledge_object.id == lower_id
        assert scores[1].ranked_candidate.candidate.knowledge_object.id == higher_id


# ---------------------------------------------------------------------------
# TestRepeatedCallsAreDeterministic
# ---------------------------------------------------------------------------


class TestRepeatedCallsAreDeterministic:
    def test_same_input_same_output_every_time(self) -> None:
        ranked = [
            make_ranked(memory_type=MemoryType.DECISION, final_score=0.4),
            make_ranked(memory_type=MemoryType.TASK, final_score=0.6),
        ]
        plan = make_plan(ContextCategory.DECISION)
        scorer = CategoryPreferenceScorer()

        first = scorer.score(ranked, plan)
        for _ in range(5):
            again = scorer.score(ranked, plan)
            assert [s.final_score for s in again] == [s.final_score for s in first]
            assert [s.category_preference_bonus for s in again] == [
                s.category_preference_bonus for s in first
            ]


# ---------------------------------------------------------------------------
# TestNoMutation
# ---------------------------------------------------------------------------


class TestNoMutation:
    def test_input_list_is_not_mutated(self) -> None:
        ranked = [make_ranked(final_score=0.5)]
        original = list(ranked)
        plan = make_plan(ContextCategory.DECISION)

        CategoryPreferenceScorer().score(ranked, plan)

        assert ranked == original

    def test_wrapped_ranked_candidate_is_the_original_object(self) -> None:
        ranked = make_ranked(memory_type=MemoryType.DECISION, final_score=0.5)
        plan = make_plan(ContextCategory.DECISION)

        scores = CategoryPreferenceScorer().score([ranked], plan)

        assert scores[0].ranked_candidate is ranked
        assert ranked.final_score == 0.5  # RankedCandidate itself untouched


# ---------------------------------------------------------------------------
# TestAsRankedCandidate
# ---------------------------------------------------------------------------


class TestAsRankedCandidate:
    def test_projects_adjusted_score_with_original_candidate_and_breakdown(self) -> None:
        ranked = make_ranked(memory_type=MemoryType.DECISION, final_score=0.5)
        plan = make_plan(ContextCategory.DECISION)

        projected = CategoryPreferenceScorer().score([ranked], plan)[0].as_ranked_candidate()

        assert projected.candidate is ranked.candidate
        assert dict(projected.score_breakdown) == dict(ranked.score_breakdown)
        assert projected.final_score == pytest.approx(0.5 + CATEGORY_PREFERENCE_BONUS)

    def test_projected_value_is_a_valid_ranked_candidate(self) -> None:
        ranked = make_ranked(memory_type=MemoryType.DECISION, final_score=0.99)
        plan = make_plan(ContextCategory.DECISION)

        projected = CategoryPreferenceScorer().score([ranked], plan)[0].as_ranked_candidate()

        assert isinstance(projected, RankedCandidate)
        assert 0.0 <= projected.final_score <= 1.0


# ---------------------------------------------------------------------------
# TestCategoryPreferenceScoreValidation
# ---------------------------------------------------------------------------


class TestCategoryPreferenceScoreValidation:
    def test_rejects_final_score_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="final_score"):
            CategoryPreferenceScore(
                ranked_candidate=make_ranked(),
                requested_category=None,
                category_preference_bonus=0.0,
                final_score=1.5,
            )

    def test_rejects_bonus_above_the_fixed_maximum(self) -> None:
        with pytest.raises(ValueError, match="category_preference_bonus"):
            CategoryPreferenceScore(
                ranked_candidate=make_ranked(),
                requested_category=ContextCategory.DECISION,
                category_preference_bonus=CATEGORY_PREFERENCE_BONUS + 0.5,
                final_score=1.0,
            )

    def test_rejects_negative_bonus(self) -> None:
        with pytest.raises(ValueError, match="category_preference_bonus"):
            CategoryPreferenceScore(
                ranked_candidate=make_ranked(),
                requested_category=None,
                category_preference_bonus=-0.01,
                final_score=0.5,
            )

    def test_is_frozen(self) -> None:
        score = CategoryPreferenceScore(
            ranked_candidate=make_ranked(),
            requested_category=None,
            category_preference_bonus=0.0,
            final_score=0.5,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            score.final_score = 0.9  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestCategoryResolutionReusesCoverageAnalyzer
# ---------------------------------------------------------------------------


class TestCategoryResolutionReusesCoverageAnalyzer:
    def test_every_mapped_memory_type_bonus_agrees_with_resolve_category(self) -> None:
        # Whatever category coverage_analyzer.resolve_category() says a
        # memory_type belongs to is exactly the category this module checks
        # for a plan match against -- the two never disagree.
        for memory_type, category in MEMORY_TYPE_CATEGORY.items():
            ranked = make_ranked(memory_type=memory_type, final_score=0.5)
            plan = make_plan(category)

            scores = CategoryPreferenceScorer().score([ranked], plan)

            assert scores[0].requested_category == resolve_category(memory_type)
            assert scores[0].category_preference_bonus == CATEGORY_PREFERENCE_BONUS


# ---------------------------------------------------------------------------
# TestStatelessReuse
# ---------------------------------------------------------------------------


class TestStatelessReuse:
    def test_one_instance_many_calls_no_leakage(self) -> None:
        scorer = CategoryPreferenceScorer()

        plan_a = make_plan(ContextCategory.DECISION)
        plan_b = make_plan(ContextCategory.TASK)
        ranked = make_ranked(memory_type=MemoryType.DECISION, final_score=0.5)

        first = scorer.score([ranked], plan_a)
        second = scorer.score([ranked], plan_b)

        assert first[0].category_preference_bonus == CATEGORY_PREFERENCE_BONUS
        assert second[0].category_preference_bonus == 0.0
