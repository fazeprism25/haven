"""Unit tests for obsidian.memory_engine.acceptance_stage.

Test groups
-----------
TestAcceptanceConfig         — validation of the four new thresholds.
TestAcceptanceDecision        — accepted/rejection_reason/abstained
                                 consistency checks.
TestFindGapCut                — direct unit tests of the stage-3 helper.
TestStageOneAbsoluteFloor     — minimum_candidate_score filtering.
TestStageTwoAbstention        — whole-query abstention.
TestStageThreeScoreGapCut     — gap-window cut behaviour, including the
                                 design doc's own "what is Project Nova"
                                 score list as a golden fixture.
TestSharesSupportingConcept   — direct unit tests of the stage-3
                                 shared-concept helper.
TestSharedConceptGapCutExemption — end-to-end AcceptanceStage.accept()
                                 behaviour for the Tier 1 exemption from
                                 RANKING_FAILURE_INVESTIGATION.md §6.
TestStageFourRelativeFloor    — relative-quality floor behaviour.
TestStageFiveHardCap          — acceptance_max_k cap.
TestAmbiguousTopGroupPreserved — the design doc's §2.3/§4.7 "secrets
                                 management system" scenario: a noise-level
                                 gap between right and wrong answers must
                                 not be arbitrarily narrowed.
TestDecisionOrderingAndCoverage — every input candidate gets exactly one
                                 decision, in descending score order.
TestEmptyInput                — accept([]) returns [].
"""

from __future__ import annotations

from typing import List, Optional, Sequence
from uuid import UUID, uuid4

import pytest

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine.acceptance_stage import (
    AcceptanceConfig,
    AcceptanceDecision,
    AcceptanceStage,
    _find_gap_cut,
    _shares_supporting_concept,
)
from obsidian.ontology.retrieval_config import RetrievalConfig
from obsidian.ontology.retrieval_models import (
    REJECTION_ACCEPTANCE_CAP_EXCEEDED,
    REJECTION_BELOW_ABSTENTION_FLOOR,
    REJECTION_BELOW_MINIMUM_SCORE,
    REJECTION_BELOW_RELATIVE_FLOOR,
    REJECTION_SCORE_GAP_CUT,
    ActivatedConcept,
    Candidate,
    RankedCandidate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_ranked(
    final_score: float,
    fact: str = "fact",
    concept_ids: Sequence[UUID] = (),
) -> RankedCandidate:
    ko = KnowledgeObject(canonical_fact=f"{fact} {uuid4()}", memory_type=MemoryType.FACT)
    supporting_concepts = tuple(
        ActivatedConcept(
            concept_id=cid,
            activation_score=1.0,
            activation_depth=0,
            source_seed=cid,
        )
        for cid in concept_ids
    )
    candidate = Candidate(
        knowledge_object=ko,
        supporting_concepts=supporting_concepts,
        attachment_relevance=1.0 if supporting_concepts else 0.0,
        activation_score=1.0 if supporting_concepts else 0.0,
    )
    return RankedCandidate(
        candidate=candidate,
        final_score=final_score,
        score_breakdown={"activation": final_score},
    )


def scores_of(candidates: List[RankedCandidate]) -> List[float]:
    return [c.final_score for c in candidates]


PERMISSIVE_RETRIEVAL = RetrievalConfig(minimum_candidate_score=0.0)
PERMISSIVE_ACCEPTANCE = AcceptanceConfig(
    abstention_score=0.0, min_gap=1.0, gap_window=10, relative_floor_ratio=0.0, acceptance_max_k=50
)


# ===========================================================================
# AcceptanceConfig
# ===========================================================================


class TestAcceptanceConfig:
    def test_default_construction(self) -> None:
        cfg = AcceptanceConfig()
        assert cfg.abstention_score == 0.25
        assert cfg.min_gap == 0.04
        assert cfg.gap_window == 10
        assert cfg.relative_floor_ratio == 0.55
        assert cfg.acceptance_max_k == 8

    @pytest.mark.parametrize("score", [-0.01, 1.01])
    def test_rejects_abstention_score_out_of_range(self, score: float) -> None:
        with pytest.raises(ValueError, match="abstention_score"):
            AcceptanceConfig(abstention_score=score)

    @pytest.mark.parametrize("gap", [-0.01, 1.01])
    def test_rejects_min_gap_out_of_range(self, gap: float) -> None:
        with pytest.raises(ValueError, match="min_gap"):
            AcceptanceConfig(min_gap=gap)

    def test_rejects_gap_window_below_one(self) -> None:
        with pytest.raises(ValueError, match="gap_window"):
            AcceptanceConfig(gap_window=0)

    @pytest.mark.parametrize("ratio", [-0.01, 1.01])
    def test_rejects_relative_floor_ratio_out_of_range(self, ratio: float) -> None:
        with pytest.raises(ValueError, match="relative_floor_ratio"):
            AcceptanceConfig(relative_floor_ratio=ratio)

    def test_rejects_acceptance_max_k_below_one(self) -> None:
        with pytest.raises(ValueError, match="acceptance_max_k"):
            AcceptanceConfig(acceptance_max_k=0)

    def test_is_frozen(self) -> None:
        import dataclasses

        cfg = AcceptanceConfig()
        with pytest.raises(dataclasses.FrozenInstanceError):
            cfg.min_gap = 0.1  # type: ignore[misc]


# ===========================================================================
# AcceptanceDecision
# ===========================================================================


class TestAcceptanceDecision:
    def _decision(
        self,
        accepted: bool = True,
        rejection_reason: Optional[str] = None,
        abstained: bool = False,
    ) -> AcceptanceDecision:
        return AcceptanceDecision(
            candidate=make_ranked(0.5),
            accepted=accepted,
            rejection_reason=rejection_reason,
            threshold_used=None,
            score_gap=None,
            relative_score=1.0,
            abstained=abstained,
        )

    def test_accepted_with_rejection_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="rejection_reason"):
            self._decision(accepted=True, rejection_reason=REJECTION_BELOW_MINIMUM_SCORE)

    def test_rejected_without_rejection_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="rejection_reason"):
            self._decision(accepted=False, rejection_reason=None)

    def test_abstained_without_abstention_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="abstained"):
            self._decision(
                accepted=False,
                rejection_reason=REJECTION_BELOW_MINIMUM_SCORE,
                abstained=True,
            )

    def test_valid_accepted_decision(self) -> None:
        d = self._decision(accepted=True, rejection_reason=None)
        assert d.accepted is True

    def test_valid_abstained_decision(self) -> None:
        d = self._decision(
            accepted=False,
            rejection_reason=REJECTION_BELOW_ABSTENTION_FLOOR,
            abstained=True,
        )
        assert d.abstained is True

    def test_is_frozen(self) -> None:
        import dataclasses

        d = self._decision()
        with pytest.raises(dataclasses.FrozenInstanceError):
            d.accepted = False  # type: ignore[misc]


# ===========================================================================
# _find_gap_cut
# ===========================================================================


class TestFindGapCut:
    def test_no_gap_meets_minimum_returns_full_length(self) -> None:
        survivors = [make_ranked(s) for s in [0.30, 0.29, 0.28, 0.27]]
        assert _find_gap_cut(survivors, min_gap=0.04, window=10) == 4

    def test_single_qualifying_gap_cuts_immediately_after_it(self) -> None:
        survivors = [make_ranked(s) for s in [0.410, 0.410, 0.410, 0.410, 0.331, 0.331, 0.331]]
        assert _find_gap_cut(survivors, min_gap=0.04, window=10) == 4

    def test_largest_qualifying_gap_wins_when_multiple_qualify(self) -> None:
        # Gaps: 0.05 (i=0), 0.10 (i=1) -- both qualify; the larger wins.
        survivors = [make_ranked(s) for s in [0.90, 0.85, 0.75, 0.74]]
        assert _find_gap_cut(survivors, min_gap=0.04, window=10) == 2

    def test_window_bounds_the_search(self) -> None:
        # The only qualifying gap is at position 5, outside a window of 2.
        survivors = [make_ranked(s) for s in [0.9, 0.89, 0.88, 0.87, 0.86, 0.40, 0.39]]
        assert _find_gap_cut(survivors, min_gap=0.04, window=2) == 7

    def test_single_candidate_returns_length_one(self) -> None:
        survivors = [make_ranked(0.5)]
        assert _find_gap_cut(survivors, min_gap=0.04, window=10) == 1

    def test_shared_concept_gap_is_never_a_cut_point(self) -> None:
        # Same qualifying gap as test_single_qualifying_gap_cuts_immediately_after_it,
        # but the two candidates straddling the gap share a supporting concept:
        # the cut must not happen there, and since no other gap qualifies, no
        # cut happens at all.
        shared = uuid4()
        survivors = [
            make_ranked(0.410),
            make_ranked(0.410),
            make_ranked(0.410),
            make_ranked(0.410, concept_ids=[shared]),
            make_ranked(0.331, concept_ids=[shared]),
            make_ranked(0.331),
            make_ranked(0.331),
        ]
        assert _find_gap_cut(survivors, min_gap=0.04, window=10) == 7

    def test_shared_concept_gap_is_skipped_in_favor_of_next_qualifying_gap(self) -> None:
        # Gaps: 0.05 at i=0 (shared concept -> exempt), 0.10 at i=1 (no shared
        # concept -> qualifies). Without the exemption the larger 0.10 gap
        # would win anyway, so also assert the smaller shared gap is not
        # chosen even when it would otherwise have been the only candidate.
        shared = uuid4()
        survivors = [
            make_ranked(0.90, concept_ids=[shared]),
            make_ranked(0.85, concept_ids=[shared]),
            make_ranked(0.75),
            make_ranked(0.74),
        ]
        assert _find_gap_cut(survivors, min_gap=0.04, window=10) == 2

    def test_shared_concept_exemption_falls_through_when_it_was_the_only_gap(self) -> None:
        shared = uuid4()
        survivors = [
            make_ranked(0.90, concept_ids=[shared]),
            make_ranked(0.60, concept_ids=[shared]),
            make_ranked(0.59),
        ]
        assert _find_gap_cut(survivors, min_gap=0.04, window=10) == 3

    def test_disjoint_concept_ids_do_not_exempt_the_gap(self) -> None:
        survivors = [
            make_ranked(0.410, concept_ids=[uuid4()]),
            make_ranked(0.331, concept_ids=[uuid4()]),
        ]
        assert _find_gap_cut(survivors, min_gap=0.04, window=10) == 1


# ===========================================================================
# _shares_supporting_concept
# ===========================================================================


class TestSharesSupportingConcept:
    def test_no_supporting_concepts_on_either_side_returns_false(self) -> None:
        a, b = make_ranked(0.5), make_ranked(0.4)
        assert _shares_supporting_concept(a, b) is False

    def test_supporting_concepts_only_on_one_side_returns_false(self) -> None:
        a = make_ranked(0.5, concept_ids=[uuid4()])
        b = make_ranked(0.4)
        assert _shares_supporting_concept(a, b) is False
        assert _shares_supporting_concept(b, a) is False

    def test_disjoint_supporting_concepts_returns_false(self) -> None:
        a = make_ranked(0.5, concept_ids=[uuid4()])
        b = make_ranked(0.4, concept_ids=[uuid4()])
        assert _shares_supporting_concept(a, b) is False

    def test_identical_single_concept_returns_true(self) -> None:
        shared = uuid4()
        a = make_ranked(0.5, concept_ids=[shared])
        b = make_ranked(0.4, concept_ids=[shared])
        assert _shares_supporting_concept(a, b) is True
        assert _shares_supporting_concept(b, a) is True

    def test_partial_overlap_among_several_concepts_returns_true(self) -> None:
        shared = uuid4()
        a = make_ranked(0.5, concept_ids=[uuid4(), shared])
        b = make_ranked(0.4, concept_ids=[shared, uuid4()])
        assert _shares_supporting_concept(a, b) is True


# ===========================================================================
# Stage 1 — absolute floor
# ===========================================================================


class TestStageOneAbsoluteFloor:
    def test_candidate_below_minimum_is_rejected(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(0.05), make_ranked(0.5)]
        decisions = stage.accept(
            candidates, RetrievalConfig(minimum_candidate_score=0.1), PERMISSIVE_ACCEPTANCE
        )
        by_score = {d.candidate.final_score: d for d in decisions}
        assert by_score[0.05].accepted is False
        assert by_score[0.05].rejection_reason == REJECTION_BELOW_MINIMUM_SCORE
        assert by_score[0.05].threshold_used == 0.1
        assert by_score[0.05].abstained is False

    def test_all_below_minimum_returns_all_rejected(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(0.01), make_ranked(0.02)]
        decisions = stage.accept(
            candidates, RetrievalConfig(minimum_candidate_score=0.5), AcceptanceConfig()
        )
        assert len(decisions) == 2
        assert all(not d.accepted for d in decisions)
        assert all(d.rejection_reason == REJECTION_BELOW_MINIMUM_SCORE for d in decisions)


# ===========================================================================
# Stage 2 — abstention
# ===========================================================================


class TestStageTwoAbstention:
    def test_top_score_below_abstention_floor_rejects_everything(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(0.20), make_ranked(0.15)]
        decisions = stage.accept(
            candidates, PERMISSIVE_RETRIEVAL, AcceptanceConfig(abstention_score=0.25)
        )
        assert all(not d.accepted for d in decisions)
        assert all(d.rejection_reason == REJECTION_BELOW_ABSTENTION_FLOOR for d in decisions)
        assert all(d.abstained for d in decisions)
        assert all(d.threshold_used == 0.25 for d in decisions)

    def test_top_score_at_abstention_floor_does_not_abstain(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(0.25)]
        decisions = stage.accept(
            candidates, PERMISSIVE_RETRIEVAL, AcceptanceConfig(abstention_score=0.25, acceptance_max_k=50)
        )
        assert decisions[0].accepted is True
        assert decisions[0].abstained is False

    def test_top_score_above_floor_does_not_abstain_even_if_others_are_low(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(0.9), make_ranked(0.01)]
        decisions = stage.accept(
            candidates, PERMISSIVE_RETRIEVAL, AcceptanceConfig(abstention_score=0.25, min_gap=1.0, acceptance_max_k=50)
        )
        assert all(not d.abstained for d in decisions)


# ===========================================================================
# Stage 3 — score-gap cut
# ===========================================================================


class TestStageThreeScoreGapCut:
    def test_project_nova_score_list_cuts_at_the_real_gap(self) -> None:
        # Golden fixture from ACCEPTANCE_STAGE_DESIGN.md §2.2: "what is
        # Project Nova" -- a real gap of 0.079 after the fourth candidate.
        stage = AcceptanceStage()
        candidates = [make_ranked(s) for s in [0.410, 0.410, 0.410, 0.410, 0.331, 0.331, 0.331]]
        decisions = stage.accept(
            candidates,
            PERMISSIVE_RETRIEVAL,
            AcceptanceConfig(abstention_score=0.25, min_gap=0.04, gap_window=10, relative_floor_ratio=0.0, acceptance_max_k=50),
        )
        accepted_scores = sorted((d.candidate.final_score for d in decisions if d.accepted), reverse=True)
        assert accepted_scores == [0.410, 0.410, 0.410, 0.410]
        rejected = [d for d in decisions if not d.accepted]
        assert len(rejected) == 3
        assert all(d.rejection_reason == REJECTION_SCORE_GAP_CUT for d in rejected)
        assert all(d.threshold_used == 0.04 for d in rejected)

    def test_flat_plateau_with_no_qualifying_gap_is_not_cut_by_stage_three(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(s) for s in [0.30, 0.29, 0.28, 0.27, 0.26]]
        decisions = stage.accept(candidates, PERMISSIVE_RETRIEVAL, PERMISSIVE_ACCEPTANCE)
        assert all(d.rejection_reason != REJECTION_SCORE_GAP_CUT for d in decisions)


# ===========================================================================
# Shared-supporting-concept gap-cut exemption (Tier 1,
# RANKING_FAILURE_INVESTIGATION.md §6) — end-to-end via AcceptanceStage.accept()
# ===========================================================================


class TestSharedConceptGapCutExemption:
    def test_candidate_sharing_a_concept_with_the_top_pick_survives_the_gap_cut(self) -> None:
        # Same shape as the design doc's Project Nova fixture (a real 0.079
        # gap), except the top candidate and the first would-be-cut
        # candidate share a supporting concept. The gap must be skipped, and
        # since no other gap qualifies, everything falls through to stage 4
        # (permissive here) and stage 5.
        shared = uuid4()
        candidates = [
            make_ranked(0.410, concept_ids=[shared]),
            make_ranked(0.410),
            make_ranked(0.410),
            make_ranked(0.331, concept_ids=[shared]),
            make_ranked(0.331),
        ]
        stage = AcceptanceStage()
        decisions = stage.accept(candidates, PERMISSIVE_RETRIEVAL, PERMISSIVE_ACCEPTANCE)
        assert all(d.accepted for d in decisions)
        assert all(d.rejection_reason != REJECTION_SCORE_GAP_CUT for d in decisions)

    def test_exempted_candidate_can_still_be_rejected_by_the_relative_floor(self) -> None:
        # The gap-cut exemption only skips stage 3; a candidate that falls
        # through still has to clear stage 4 on its own merits.
        shared = uuid4()
        candidates = [
            make_ranked(0.90, concept_ids=[shared]),
            make_ranked(0.40, concept_ids=[shared]),
        ]
        stage = AcceptanceStage()
        decisions = stage.accept(
            candidates,
            PERMISSIVE_RETRIEVAL,
            AcceptanceConfig(
                abstention_score=0.0, min_gap=0.04, gap_window=10,
                relative_floor_ratio=0.55, acceptance_max_k=50,
            ),
        )
        by_score = {d.candidate.final_score: d for d in decisions}
        assert by_score[0.90].accepted is True
        assert by_score[0.40].accepted is False
        assert by_score[0.40].rejection_reason == REJECTION_BELOW_RELATIVE_FLOOR

    def test_no_shared_concept_still_cuts_exactly_as_before(self) -> None:
        # Regression guard: unrelated candidates (no ontology evidence, the
        # keyword-only shape make_ranked already produced pre-fix) must see
        # unchanged stage-3 behaviour.
        candidates = [make_ranked(s) for s in [0.410, 0.410, 0.410, 0.410, 0.331, 0.331, 0.331]]
        stage = AcceptanceStage()
        decisions = stage.accept(
            candidates,
            PERMISSIVE_RETRIEVAL,
            AcceptanceConfig(abstention_score=0.25, min_gap=0.04, gap_window=10, relative_floor_ratio=0.0, acceptance_max_k=50),
        )
        accepted_scores = sorted((d.candidate.final_score for d in decisions if d.accepted), reverse=True)
        assert accepted_scores == [0.410, 0.410, 0.410, 0.410]
        rejected = [d for d in decisions if not d.accepted]
        assert len(rejected) == 3
        assert all(d.rejection_reason == REJECTION_SCORE_GAP_CUT for d in rejected)


# ===========================================================================
# Stage 4 — relative quality floor
# ===========================================================================


class TestStageFourRelativeFloor:
    def test_candidate_below_relative_floor_is_rejected(self) -> None:
        stage = AcceptanceStage()
        # top=0.662, ratio=0.55 -> floor=0.3641; 0.337 falls below it.
        candidates = [make_ranked(0.662), make_ranked(0.40), make_ranked(0.337)]
        decisions = stage.accept(
            candidates,
            PERMISSIVE_RETRIEVAL,
            AcceptanceConfig(abstention_score=0.0, min_gap=1.0, relative_floor_ratio=0.55, acceptance_max_k=50),
        )
        by_score = {round(d.candidate.final_score, 3): d for d in decisions}
        assert by_score[0.662].accepted is True
        assert by_score[0.4].accepted is True
        assert by_score[0.337].accepted is False
        assert by_score[0.337].rejection_reason == REJECTION_BELOW_RELATIVE_FLOOR
        assert by_score[0.337].threshold_used == pytest.approx(0.662 * 0.55)

    def test_tightly_clustered_scores_all_survive_the_floor(self) -> None:
        # Atlas-style spread from the design doc: 0.304-0.376, within 19%
        # of the top score, all comfortably above a 0.55 floor.
        stage = AcceptanceStage()
        candidates = [make_ranked(s) for s in [0.376, 0.326, 0.323, 0.304]]
        decisions = stage.accept(
            candidates,
            PERMISSIVE_RETRIEVAL,
            AcceptanceConfig(abstention_score=0.0, min_gap=1.0, relative_floor_ratio=0.55, acceptance_max_k=50),
        )
        assert all(d.accepted for d in decisions)


# ===========================================================================
# Stage 5 — hard cap
# ===========================================================================


class TestStageFiveHardCap:
    def test_excess_candidates_beyond_cap_are_rejected(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(0.9 - 0.001 * i) for i in range(10)]
        decisions = stage.accept(
            candidates,
            PERMISSIVE_RETRIEVAL,
            AcceptanceConfig(abstention_score=0.0, min_gap=1.0, relative_floor_ratio=0.0, acceptance_max_k=3),
        )
        accepted = [d for d in decisions if d.accepted]
        rejected = [d for d in decisions if not d.accepted]
        assert len(accepted) == 3
        assert len(rejected) == 7
        assert all(d.rejection_reason == REJECTION_ACCEPTANCE_CAP_EXCEEDED for d in rejected)
        assert all(d.threshold_used == 3.0 for d in rejected)
        # The three highest-scoring candidates are the ones kept.
        assert min(d.candidate.final_score for d in accepted) >= max(
            d.candidate.final_score for d in rejected
        )

    def test_fewer_candidates_than_cap_all_survive(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(0.9), make_ranked(0.8)]
        decisions = stage.accept(
            candidates,
            PERMISSIVE_RETRIEVAL,
            AcceptanceConfig(abstention_score=0.0, min_gap=1.0, relative_floor_ratio=0.0, acceptance_max_k=8),
        )
        assert all(d.accepted for d in decisions)


# ===========================================================================
# The "secrets management system" scenario (design doc §2.3 / §4.7)
# ===========================================================================


class TestAmbiguousTopGroupPreserved:
    def test_noise_level_gap_between_right_and_wrong_answer_keeps_both(self) -> None:
        # rank1 (wrong, dark mode) = 0.331, rank3 (right, HashiCorp Vault)
        # = 0.317 -- a gap of 0.014, below the noise floor. The design's
        # own position: acceptance cannot fix a ranking mistake it has no
        # evidence for, so it must not arbitrarily narrow this group.
        stage = AcceptanceStage()
        candidates = [make_ranked(s) for s in [0.331, 0.317, 0.317, 0.310]]
        decisions = stage.accept(candidates, PERMISSIVE_RETRIEVAL, AcceptanceConfig())
        assert all(d.accepted for d in decisions)
        assert all(not d.abstained for d in decisions)


# ===========================================================================
# Decision ordering / coverage
# ===========================================================================


class TestDecisionOrderingAndCoverage:
    def test_one_decision_per_input_candidate(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(s) for s in [0.9, 0.1, 0.5, 0.02]]
        decisions = stage.accept(candidates, PERMISSIVE_RETRIEVAL, PERMISSIVE_ACCEPTANCE)
        assert len(decisions) == len(candidates)
        assert {id(d.candidate) for d in decisions} == {id(c) for c in candidates}

    def test_decisions_are_in_descending_score_order(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(s) for s in [0.1, 0.9, 0.5]]
        decisions = stage.accept(candidates, PERMISSIVE_RETRIEVAL, PERMISSIVE_ACCEPTANCE)
        scores = [d.candidate.final_score for d in decisions]
        assert scores == sorted(scores, reverse=True)

    def test_relative_score_is_always_populated(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(0.5), make_ranked(0.25)]
        decisions = stage.accept(
            candidates, RetrievalConfig(minimum_candidate_score=0.9), AcceptanceConfig()
        )
        # Both rejected by stage 1, but relative_score is still computed.
        by_score = {d.candidate.final_score: d for d in decisions}
        assert by_score[0.5].relative_score == 1.0
        assert by_score[0.25].relative_score == 0.5

    def test_relative_score_is_zero_when_top_score_is_zero(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(0.0), make_ranked(0.0)]
        decisions = stage.accept(candidates, PERMISSIVE_RETRIEVAL, PERMISSIVE_ACCEPTANCE)
        assert all(d.relative_score == 0.0 for d in decisions)

    def test_score_gap_is_none_for_lowest_ranked_candidate(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(0.9), make_ranked(0.5)]
        decisions = stage.accept(candidates, PERMISSIVE_RETRIEVAL, PERMISSIVE_ACCEPTANCE)
        lowest = min(decisions, key=lambda d: d.candidate.final_score)
        assert lowest.score_gap is None

    def test_score_gap_matches_consecutive_difference(self) -> None:
        stage = AcceptanceStage()
        candidates = [make_ranked(0.9), make_ranked(0.5)]
        decisions = stage.accept(candidates, PERMISSIVE_RETRIEVAL, PERMISSIVE_ACCEPTANCE)
        highest = max(decisions, key=lambda d: d.candidate.final_score)
        assert highest.score_gap == pytest.approx(0.4)


# ===========================================================================
# Empty input
# ===========================================================================


class TestEmptyInput:
    def test_accept_on_empty_list_returns_empty_list(self) -> None:
        stage = AcceptanceStage()
        assert stage.accept([], RetrievalConfig(), AcceptanceConfig()) == []
