"""Unit tests for obsidian.memory_engine.deterministic_ranker.DeterministicRanker.

Test groups
-----------
TestScoringFormula          — the documented equation reproduced by hand for
                                a controlled candidate/config/now triple.
TestComponentIndependence     — activation_score, attachment_relevance, and
                                keyword_overlap_score contribute
                                independently; no weight leaks into
                                another component.
TestKeywordOverlapComponent    — keyword_overlap_score's contribution scales
                                with its weight and raw value like every
                                other component; production default weight
                                is nonzero.
TestRecencyComponent          — age 0 -> 1.0, one RECENCY_SCALE_DAYS -> 0.5,
                                future valid_from clamped to age 0.
TestConfirmationCountComponent — n / (n + 1) shape at n=0, 1, 9.
TestScoreBreakdownCompleteness — exactly seven keys; they sum to final_score.
TestZeroWeightConfig           — all seven weights zero -> final_score 0.0.
TestMinimumCandidateScoreFilter — below threshold dropped, at threshold kept.
TestMaxResultsNotApplied        — max_results is never used to truncate.
TestDeterministicOrdering       — descending final_score.
TestDeterministicTieBreak       — equal final_score -> ascending KO id.
TestNoMutation                  — candidates/config are never mutated.
TestNowParameter                 — explicit now is reproducible; omitted now
                                    defaults to utcnow().
TestEmptyInput                   — rank([]) == [].
TestStatelessReuse                — one instance, many calls, no leakage.
TestNoOutOfScopeImports           — module never imports retrieval/allocation/
                                    context-building code.
TestScoreAll                      — rank() is provably score_all() + a
                                    threshold filter; score_all() keeps
                                    below-threshold candidates rank() drops.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID, uuid4

import pytest

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine.deterministic_ranker import (
    RECENCY_SCALE_DAYS,
    DeterministicRanker,
)
from obsidian.ontology.retrieval_config import RetrievalConfig
from obsidian.ontology.retrieval_models import ActivatedConcept, Candidate

NOW = datetime(2026, 7, 2, 12, 0, 0)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def make_ko(
    fact: str = "Haven uses Claude",
    ko_id: Optional[UUID] = None,
    importance: float = 0.5,
    confidence: float = 0.5,
    confirmation_count: int = 0,
    valid_from: datetime = NOW,
) -> KnowledgeObject:
    return KnowledgeObject(
        id=ko_id if ko_id is not None else uuid4(),
        canonical_fact=fact,
        memory_type=MemoryType.FACT,
        importance=importance,
        confidence=confidence,
        confirmation_count=confirmation_count,
        valid_from=valid_from,
    )


def make_activated_concept(
    concept_id: Optional[UUID] = None,
    activation_score: float = 1.0,
    activation_depth: int = 0,
    source_seed: Optional[UUID] = None,
) -> ActivatedConcept:
    cid = concept_id if concept_id is not None else uuid4()
    seed = source_seed if source_seed is not None else cid
    return ActivatedConcept(
        concept_id=cid,
        activation_score=activation_score,
        activation_depth=activation_depth,
        source_seed=seed,
    )


def make_candidate(
    ko: Optional[KnowledgeObject] = None,
    attachment_relevance: float = 0.5,
    activation_score: float = 0.5,
    keyword_overlap_score: float = 0.0,
) -> Candidate:
    return Candidate(
        knowledge_object=ko if ko is not None else make_ko(),
        supporting_concepts=(make_activated_concept(),),
        attachment_relevance=attachment_relevance,
        activation_score=activation_score,
        keyword_overlap_score=keyword_overlap_score,
    )


def permissive_config(**overrides) -> RetrievalConfig:
    """A RetrievalConfig with minimum_candidate_score at 0.0, and
    weight_keyword_overlap at 0.0, so filtering and the newest scoring
    component don't interfere with tests that aren't specifically about
    them. Every pre-existing hand-computed test in this file was written
    against six components; zeroing this seventh weight keeps their
    total_weight denominator numerically identical to what they expect,
    since candidates built via make_candidate() also default
    keyword_overlap_score to 0.0 (0 contribution either way) -- only the
    denominator would otherwise shift. Tests that care about
    weight_keyword_overlap override it explicitly."""
    defaults = dict(minimum_candidate_score=0.0, weight_keyword_overlap=0.0)
    defaults.update(overrides)
    return RetrievalConfig(**defaults)


# ---------------------------------------------------------------------------
# TestScoringFormula
# ---------------------------------------------------------------------------


class TestScoringFormula:
    def test_matches_hand_computed_equation(self) -> None:
        config = permissive_config(
            weight_activation=0.35,
            weight_attachment_relevance=0.20,
            weight_importance=0.25,
            weight_confidence=0.20,
            weight_recency=0.15,
            weight_confirmation_count=0.05,
        )
        ko = make_ko(
            importance=0.8,
            confidence=0.6,
            confirmation_count=4,
            valid_from=NOW - timedelta(days=RECENCY_SCALE_DAYS),
        )
        candidate = make_candidate(ko=ko, attachment_relevance=0.9, activation_score=0.7)

        ranked = DeterministicRanker().rank([candidate], config, now=NOW)[0]

        w = dict(
            activation=0.35,
            attachment_relevance=0.20,
            importance=0.25,
            confidence=0.20,
            recency=0.15,
            confirmation_count=0.05,
        )
        raw = dict(
            activation=0.7,
            attachment_relevance=0.9,
            importance=0.8,
            confidence=0.6,
            recency=0.5,  # age == RECENCY_SCALE_DAYS -> 1 / (1 + 1) == 0.5
            confirmation_count=4 / 5,  # n / (n + 1)
        )
        total_weight = sum(w.values())
        expected_breakdown = {k: (w[k] * raw[k]) / total_weight for k in w}
        expected_final = sum(expected_breakdown.values())

        assert ranked.final_score == pytest.approx(expected_final)
        for key, value in expected_breakdown.items():
            assert ranked.score_breakdown[key] == pytest.approx(value)

    def test_final_score_within_bounds_for_arbitrary_weights(self) -> None:
        config = permissive_config(
            weight_activation=3.0,
            weight_attachment_relevance=7.0,
            weight_importance=0.001,
            weight_confidence=100.0,
            weight_recency=0.5,
            weight_confirmation_count=2.0,
        )
        candidate = make_candidate(
            ko=make_ko(importance=1.0, confidence=1.0, confirmation_count=50),
            attachment_relevance=1.0,
            activation_score=1.0,
        )
        ranked = DeterministicRanker().rank([candidate], config, now=NOW)[0]
        assert 0.0 <= ranked.final_score <= 1.0


# ---------------------------------------------------------------------------
# TestComponentIndependence
# ---------------------------------------------------------------------------


class TestComponentIndependence:
    def test_attachment_relevance_does_not_affect_activation_contribution(self) -> None:
        config = permissive_config()
        low = make_candidate(activation_score=0.5, attachment_relevance=0.0)
        high = make_candidate(ko=low.knowledge_object, activation_score=0.5, attachment_relevance=1.0)

        low_ranked = DeterministicRanker().rank([low], config, now=NOW)[0]
        high_ranked = DeterministicRanker().rank([high], config, now=NOW)[0]

        assert low_ranked.score_breakdown["activation"] == pytest.approx(high_ranked.score_breakdown["activation"])
        assert low_ranked.score_breakdown["attachment_relevance"] != pytest.approx(
            high_ranked.score_breakdown["attachment_relevance"]
        )

    def test_activation_does_not_affect_attachment_relevance_contribution(self) -> None:
        config = permissive_config()
        low = make_candidate(activation_score=0.0, attachment_relevance=0.5)
        high = make_candidate(ko=low.knowledge_object, activation_score=1.0, attachment_relevance=0.5)

        low_ranked = DeterministicRanker().rank([low], config, now=NOW)[0]
        high_ranked = DeterministicRanker().rank([high], config, now=NOW)[0]

        assert low_ranked.score_breakdown["attachment_relevance"] == pytest.approx(
            high_ranked.score_breakdown["attachment_relevance"]
        )
        assert low_ranked.score_breakdown["activation"] != pytest.approx(high_ranked.score_breakdown["activation"])

    def test_zero_weight_on_one_component_removes_its_contribution_only(self) -> None:
        config = permissive_config(weight_attachment_relevance=0.0)
        candidate = make_candidate(attachment_relevance=1.0, activation_score=0.3)

        ranked = DeterministicRanker().rank([candidate], config, now=NOW)[0]

        assert ranked.score_breakdown["attachment_relevance"] == 0.0
        assert ranked.score_breakdown["activation"] > 0.0

    def test_keyword_overlap_does_not_affect_activation_or_attachment_contribution(self) -> None:
        config = permissive_config(weight_keyword_overlap=1.0)
        low = make_candidate(activation_score=0.5, attachment_relevance=0.5, keyword_overlap_score=0.0)
        high = make_candidate(
            ko=low.knowledge_object, activation_score=0.5, attachment_relevance=0.5, keyword_overlap_score=1.0
        )

        low_ranked = DeterministicRanker().rank([low], config, now=NOW)[0]
        high_ranked = DeterministicRanker().rank([high], config, now=NOW)[0]

        assert low_ranked.score_breakdown["activation"] == pytest.approx(high_ranked.score_breakdown["activation"])
        assert low_ranked.score_breakdown["attachment_relevance"] == pytest.approx(
            high_ranked.score_breakdown["attachment_relevance"]
        )
        assert low_ranked.score_breakdown["keyword_overlap"] != pytest.approx(
            high_ranked.score_breakdown["keyword_overlap"]
        )


# ---------------------------------------------------------------------------
# TestKeywordOverlapComponent
# ---------------------------------------------------------------------------


class TestKeywordOverlapComponent:
    def test_zero_score_contributes_nothing(self) -> None:
        config = permissive_config(weight_keyword_overlap=1.0)
        candidate = make_candidate(keyword_overlap_score=0.0)
        ranked = DeterministicRanker().rank([candidate], config, now=NOW)[0]
        assert ranked.score_breakdown["keyword_overlap"] == 0.0

    def test_max_score_with_isolated_weight_yields_full_final_score(self) -> None:
        config = permissive_config(
            weight_keyword_overlap=1.0,
            weight_activation=0.0,
            weight_attachment_relevance=0.0,
            weight_importance=0.0,
            weight_confidence=0.0,
            weight_recency=0.0,
            weight_confirmation_count=0.0,
        )
        candidate = make_candidate(keyword_overlap_score=1.0)
        ranked = DeterministicRanker().rank([candidate], config, now=NOW)[0]
        assert ranked.score_breakdown["keyword_overlap"] == pytest.approx(1.0)
        assert ranked.final_score == pytest.approx(1.0)

    def test_contribution_scales_linearly_with_raw_score(self) -> None:
        config = permissive_config(weight_keyword_overlap=1.0)
        low = make_candidate(keyword_overlap_score=0.2)
        high = make_candidate(ko=low.knowledge_object, keyword_overlap_score=0.8)

        low_ranked = DeterministicRanker().rank([low], config, now=NOW)[0]
        high_ranked = DeterministicRanker().rank([high], config, now=NOW)[0]

        assert high_ranked.score_breakdown["keyword_overlap"] > low_ranked.score_breakdown["keyword_overlap"]

    def test_zero_weight_removes_contribution_regardless_of_score(self) -> None:
        config = permissive_config(weight_keyword_overlap=0.0)
        candidate = make_candidate(keyword_overlap_score=1.0)
        ranked = DeterministicRanker().rank([candidate], config, now=NOW)[0]
        assert ranked.score_breakdown["keyword_overlap"] == 0.0

    def test_default_config_gives_keyword_overlap_a_nonzero_weight(self) -> None:
        # The production default (RetrievalConfig(), not permissive_config())
        # must actually wire this component in -- a real signal that
        # defaults to weight 0.0 would never affect ranking in practice.
        assert RetrievalConfig().weight_keyword_overlap > 0.0


# ---------------------------------------------------------------------------
# TestRecencyComponent
# ---------------------------------------------------------------------------


class TestRecencyComponent:
    def test_zero_age_gives_full_recency(self) -> None:
        ko = make_ko(valid_from=NOW)
        candidate = make_candidate(ko=ko)
        ranked = DeterministicRanker().rank(
            [candidate],
            permissive_config(
                weight_recency=1.0,
                weight_activation=0.0,
                weight_attachment_relevance=0.0,
                weight_importance=0.0,
                weight_confidence=0.0,
                weight_confirmation_count=0.0,
            ),
            now=NOW,
        )[0]
        assert ranked.score_breakdown["recency"] == pytest.approx(1.0)

    def test_one_scale_period_halves_recency(self) -> None:
        ko = make_ko(valid_from=NOW - timedelta(days=RECENCY_SCALE_DAYS))
        candidate = make_candidate(ko=ko)
        config = permissive_config(
            weight_recency=1.0,
            weight_activation=0.0,
            weight_attachment_relevance=0.0,
            weight_importance=0.0,
            weight_confidence=0.0,
            weight_confirmation_count=0.0,
        )
        ranked = DeterministicRanker().rank([candidate], config, now=NOW)[0]
        assert ranked.score_breakdown["recency"] == pytest.approx(0.5)

    def test_future_valid_from_clamped_to_zero_age(self) -> None:
        ko = make_ko(valid_from=NOW + timedelta(days=30))
        candidate = make_candidate(ko=ko)
        config = permissive_config(
            weight_recency=1.0,
            weight_activation=0.0,
            weight_attachment_relevance=0.0,
            weight_importance=0.0,
            weight_confidence=0.0,
            weight_confirmation_count=0.0,
        )
        ranked = DeterministicRanker().rank([candidate], config, now=NOW)[0]
        assert ranked.score_breakdown["recency"] == pytest.approx(1.0)

    def test_recency_strictly_decreases_with_age(self) -> None:
        config = permissive_config(
            weight_recency=1.0,
            weight_activation=0.0,
            weight_attachment_relevance=0.0,
            weight_importance=0.0,
            weight_confidence=0.0,
            weight_confirmation_count=0.0,
        )
        younger = make_candidate(ko=make_ko(valid_from=NOW - timedelta(days=1)))
        older = make_candidate(
            ko=make_ko(valid_from=NOW - timedelta(days=30)),
        )
        younger_ranked = DeterministicRanker().rank([younger], config, now=NOW)[0]
        older_ranked = DeterministicRanker().rank([older], config, now=NOW)[0]
        assert younger_ranked.score_breakdown["recency"] > older_ranked.score_breakdown["recency"]


# ---------------------------------------------------------------------------
# TestConfirmationCountComponent
# ---------------------------------------------------------------------------


class TestConfirmationCountComponent:
    only_confirmation_config = permissive_config(
        weight_confirmation_count=1.0,
        weight_activation=0.0,
        weight_attachment_relevance=0.0,
        weight_importance=0.0,
        weight_confidence=0.0,
        weight_recency=0.0,
    )

    @pytest.mark.parametrize(
        "count, expected",
        [(0, 0.0), (1, 0.5), (9, 0.9), (99, 0.99)],
    )
    def test_matches_n_over_n_plus_one(self, count: int, expected: float) -> None:
        candidate = make_candidate(ko=make_ko(confirmation_count=count))
        ranked = DeterministicRanker().rank([candidate], self.only_confirmation_config, now=NOW)[0]
        assert ranked.score_breakdown["confirmation_count"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# TestScoreBreakdownCompleteness
# ---------------------------------------------------------------------------


class TestScoreBreakdownCompleteness:
    def test_breakdown_has_exactly_seven_keys(self) -> None:
        candidate = make_candidate()
        ranked = DeterministicRanker().rank([candidate], permissive_config(), now=NOW)[0]
        assert set(ranked.score_breakdown.keys()) == {
            "activation",
            "attachment_relevance",
            "keyword_overlap",
            "importance",
            "confidence",
            "recency",
            "confirmation_count",
        }

    def test_breakdown_sums_to_final_score(self) -> None:
        candidate = make_candidate(
            ko=make_ko(importance=0.3, confidence=0.9, confirmation_count=2, valid_from=NOW - timedelta(days=3)),
            attachment_relevance=0.4,
            activation_score=0.6,
        )
        ranked = DeterministicRanker().rank([candidate], permissive_config(), now=NOW)[0]
        assert sum(ranked.score_breakdown.values()) == pytest.approx(ranked.final_score)


# ---------------------------------------------------------------------------
# TestZeroWeightConfig
# ---------------------------------------------------------------------------


class TestZeroWeightConfig:
    def test_all_zero_weights_yields_zero_score(self) -> None:
        config = permissive_config(
            weight_activation=0.0,
            weight_attachment_relevance=0.0,
            weight_importance=0.0,
            weight_confidence=0.0,
            weight_recency=0.0,
            weight_confirmation_count=0.0,
        )
        candidate = make_candidate(
            ko=make_ko(importance=1.0, confidence=1.0, confirmation_count=100),
            attachment_relevance=1.0,
            activation_score=1.0,
        )
        ranked = DeterministicRanker().rank([candidate], config, now=NOW)[0]
        assert ranked.final_score == 0.0
        assert all(v == 0.0 for v in ranked.score_breakdown.values())


# ---------------------------------------------------------------------------
# TestMinimumCandidateScoreFilter
# ---------------------------------------------------------------------------


class TestMinimumCandidateScoreFilter:
    def test_below_threshold_dropped(self) -> None:
        config = permissive_config(minimum_candidate_score=0.9)
        low = make_candidate(activation_score=0.1, attachment_relevance=0.1)
        result = DeterministicRanker().rank([low], config, now=NOW)
        assert result == []

    def test_at_or_above_threshold_kept(self) -> None:
        config = permissive_config(minimum_candidate_score=0.0)
        candidate = make_candidate()
        result = DeterministicRanker().rank([candidate], config, now=NOW)
        assert len(result) == 1

    def test_exact_threshold_boundary_is_inclusive(self) -> None:
        config = permissive_config(
            weight_activation=1.0,
            weight_attachment_relevance=0.0,
            weight_importance=0.0,
            weight_confidence=0.0,
            weight_recency=0.0,
            weight_confirmation_count=0.0,
            minimum_candidate_score=0.5,
        )
        candidate = make_candidate(activation_score=0.5)
        result = DeterministicRanker().rank([candidate], config, now=NOW)
        assert len(result) == 1
        assert result[0].final_score == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# TestMaxResultsNotApplied
# ---------------------------------------------------------------------------


class TestMaxResultsNotApplied:
    def test_more_candidates_than_max_results_are_all_returned(self) -> None:
        config = permissive_config(max_results=1)
        candidates = [make_candidate() for _ in range(5)]
        result = DeterministicRanker().rank(candidates, config, now=NOW)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# TestDeterministicOrdering
# ---------------------------------------------------------------------------


class TestDeterministicOrdering:
    def test_sorted_descending_by_final_score(self) -> None:
        config = permissive_config()
        low = make_candidate(activation_score=0.1, attachment_relevance=0.1)
        high = make_candidate(activation_score=0.9, attachment_relevance=0.9)
        result = DeterministicRanker().rank([low, high], config, now=NOW)
        assert result[0].final_score >= result[1].final_score
        assert result[0].candidate.knowledge_object.id == high.knowledge_object.id

    def test_order_independent_of_input_order(self) -> None:
        config = permissive_config()
        candidates = [make_candidate(activation_score=s) for s in [0.2, 0.9, 0.5, 0.1, 0.7]]
        forward = DeterministicRanker().rank(candidates, config, now=NOW)
        backward = DeterministicRanker().rank(list(reversed(candidates)), config, now=NOW)
        assert [rc.candidate.knowledge_object.id for rc in forward] == [
            rc.candidate.knowledge_object.id for rc in backward
        ]


# ---------------------------------------------------------------------------
# TestDeterministicTieBreak
# ---------------------------------------------------------------------------


class TestDeterministicTieBreak:
    def test_equal_final_score_broken_by_knowledge_object_id(self) -> None:
        config = permissive_config()
        ko_a = make_ko("fact A")
        ko_b = make_ko("fact B")
        first_id, second_id = sorted([ko_a.id, ko_b.id], key=str)
        ko_first = ko_a if ko_a.id == first_id else ko_b
        ko_second = ko_b if ko_first is ko_a else ko_a

        candidate_first = make_candidate(ko=ko_first, activation_score=0.5, attachment_relevance=0.5)
        candidate_second = make_candidate(ko=ko_second, activation_score=0.5, attachment_relevance=0.5)

        result = DeterministicRanker().rank([candidate_second, candidate_first], config, now=NOW)

        assert result[0].final_score == pytest.approx(result[1].final_score)
        assert result[0].candidate.knowledge_object.id == first_id
        assert result[1].candidate.knowledge_object.id == second_id


# ---------------------------------------------------------------------------
# TestNoMutation
# ---------------------------------------------------------------------------


class TestNoMutation:
    def test_candidates_are_frozen_and_unchanged(self) -> None:
        config = permissive_config()
        candidate = make_candidate()
        before = dataclasses.replace(candidate)  # shallow copy for comparison
        result = DeterministicRanker().rank([candidate], config, now=NOW)
        assert result[0].candidate == before
        assert result[0].candidate is candidate

    def test_config_is_not_mutated(self) -> None:
        config = permissive_config()
        snapshot = dataclasses.replace(config)
        DeterministicRanker().rank([make_candidate()], config, now=NOW)
        assert config == snapshot


# ---------------------------------------------------------------------------
# TestNowParameter
# ---------------------------------------------------------------------------


class TestNowParameter:
    def test_explicit_now_is_reproducible(self) -> None:
        config = permissive_config()
        candidate = make_candidate(ko=make_ko(valid_from=NOW - timedelta(days=2)))
        first = DeterministicRanker().rank([candidate], config, now=NOW)[0]
        second = DeterministicRanker().rank([candidate], config, now=NOW)[0]
        assert first.final_score == second.final_score
        assert dict(first.score_breakdown) == dict(second.score_breakdown)

    def test_omitted_now_defaults_to_utcnow(self) -> None:
        config = permissive_config(
            weight_recency=1.0,
            weight_activation=0.0,
            weight_attachment_relevance=0.0,
            weight_importance=0.0,
            weight_confidence=0.0,
            weight_confirmation_count=0.0,
        )
        candidate = make_candidate(ko=make_ko(valid_from=datetime.utcnow()))
        ranked = DeterministicRanker().rank([candidate], config)[0]
        assert ranked.score_breakdown["recency"] == pytest.approx(1.0, abs=1e-3)


# ---------------------------------------------------------------------------
# TestEmptyInput
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_empty_candidates_returns_empty_list(self) -> None:
        result = DeterministicRanker().rank([], permissive_config(), now=NOW)
        assert result == []


# ---------------------------------------------------------------------------
# TestStatelessReuse
# ---------------------------------------------------------------------------


class TestStatelessReuse:
    def test_same_instance_reused_across_calls(self) -> None:
        ranker = DeterministicRanker()
        config = permissive_config()
        first = ranker.rank([make_candidate(activation_score=0.3)], config, now=NOW)
        second = ranker.rank([make_candidate(activation_score=0.8)], config, now=NOW)
        assert first[0].final_score != second[0].final_score


# ---------------------------------------------------------------------------
# TestNoOutOfScopeImports
# ---------------------------------------------------------------------------


class TestNoOutOfScopeImports:
    def test_module_does_not_bind_retrieval_or_allocation_names(self) -> None:
        """The module's docstring *discusses* MemoryStore/ConceptGraph/etc. as
        out-of-scope context, but must never actually import or bind them —
        checked against the module namespace rather than raw source text so
        prose mentions don't produce false positives."""
        import obsidian.memory_engine.deterministic_ranker as module

        forbidden = {"MemoryStore", "ConceptGraph", "ContextBuilder", "SlotAllocator"}
        assert forbidden.isdisjoint(dir(module))


# ---------------------------------------------------------------------------
# TestScoreAll
# ---------------------------------------------------------------------------


class TestScoreAll:
    def test_rank_is_score_all_filtered_by_threshold(self) -> None:
        config = permissive_config(minimum_candidate_score=0.5)
        candidates = [make_candidate(activation_score=s) for s in [0.1, 0.9, 0.5, 0.3, 0.7]]
        ranker = DeterministicRanker()

        scored = ranker.score_all(candidates, config, now=NOW)
        ranked = ranker.rank(candidates, config, now=NOW)

        assert ranked == [rc for rc in scored if rc.final_score >= config.minimum_candidate_score]

    def test_score_all_retains_below_threshold_candidates(self) -> None:
        config = permissive_config(minimum_candidate_score=0.9)
        low = make_candidate(activation_score=0.1, attachment_relevance=0.1)

        scored = DeterministicRanker().score_all([low], config, now=NOW)
        ranked = DeterministicRanker().rank([low], config, now=NOW)

        assert len(scored) == 1
        assert ranked == []

    def test_score_all_and_rank_agree_on_scores_for_surviving_candidates(self) -> None:
        config = permissive_config(minimum_candidate_score=0.0)
        candidates = [make_candidate(activation_score=s) for s in [0.2, 0.9, 0.5]]

        scored = DeterministicRanker().score_all(candidates, config, now=NOW)
        ranked = DeterministicRanker().rank(candidates, config, now=NOW)

        assert [rc.final_score for rc in scored] == [rc.final_score for rc in ranked]

    def test_score_all_empty_input_returns_empty_list(self) -> None:
        assert DeterministicRanker().score_all([], permissive_config(), now=NOW) == []
