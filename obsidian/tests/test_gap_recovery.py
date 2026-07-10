"""Unit tests for obsidian.memory_engine.gap_recovery.

Test groups
-----------
TestGapRecoveryDecisionValidation    — confidence/retry_budget bound checks
                                        and should_retry/retry_reason/
                                        recovery_strategy/missing_categories
                                        consistency invariants.
TestGapRecoveryDecisionSerialization — to_dict/from_dict round-trips.
TestGapRecoveryDecisionImmutability  — frozen, hashable, value equality.
TestDecideGapRecoveryNoGap           — a fully-satisfied CoverageReport
                                        (including the POINTED_QA sentinel)
                                        always yields should_retry=False.
TestDecideGapRecoveryLowConfidence    — a gap exists but the plan's own
                                        confidence is below threshold ->
                                        should_retry=False, gap still
                                        recorded.
TestDecideGapRecoveryRequiredMissing — a gap exists and the plan is
                                        confident -> should_retry=True with
                                        a fixed retry budget and strategy.
TestDecideGapRecoveryDeterminism     — repeated calls with the same input
                                        produce an equal decision.
TestDecideGapRecoveryObservationalOnly — decide_gap_recovery never mutates
                                        its inputs and is a pure function
                                        (no I/O, no clock dependency in its
                                        decision logic).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from obsidian.memory_engine.context_planner import (
    CategoryRequirement,
    ContextCategory,
    ContextPlan,
    Necessity,
    TaskMode,
)
from obsidian.memory_engine.coverage_analyzer import CategoryCoverage, CoverageReport
from obsidian.memory_engine.gap_recovery import (
    DEFAULT_RETRY_BUDGET,
    MIN_PLAN_CONFIDENCE_FOR_RETRY,
    GapRecoveryDecision,
    RecoveryStrategy,
    RetryReason,
    decide_gap_recovery,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def make_plan(
    requirements: tuple[CategoryRequirement, ...] = (),
    confidence: float = 1.0,
) -> ContextPlan:
    return ContextPlan(
        query="continue implementing Haven",
        task_mode=TaskMode.CONTINUATION if requirements else TaskMode.POINTED_QA,
        requirements=requirements,
        confidence=confidence,
    )


def make_requirement(
    category: ContextCategory,
    necessity: Necessity = Necessity.REQUIRED,
    min_count: int = 1,
) -> CategoryRequirement:
    return CategoryRequirement(category=category, necessity=necessity, min_count=min_count)


def make_coverage(
    entries: tuple[CategoryCoverage, ...] = (),
) -> CoverageReport:
    return CoverageReport(entries=entries)


def missing_entry(
    category: ContextCategory, required_minimum: int = 1, retrieved_count: int = 0
) -> CategoryCoverage:
    return CategoryCoverage(
        category=category,
        necessity=Necessity.REQUIRED,
        required_minimum=required_minimum,
        retrieved_count=retrieved_count,
    )


def satisfied_entry(category: ContextCategory) -> CategoryCoverage:
    return CategoryCoverage(
        category=category,
        necessity=Necessity.REQUIRED,
        required_minimum=1,
        retrieved_count=1,
    )


# ===========================================================================
# GapRecoveryDecision
# ===========================================================================


class TestGapRecoveryDecisionValidation:
    def test_confidence_out_of_range_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            GapRecoveryDecision(should_retry=False, confidence=1.5)

    def test_negative_confidence_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            GapRecoveryDecision(should_retry=False, confidence=-0.1)

    def test_negative_retry_budget_raises(self) -> None:
        with pytest.raises(ValueError, match="retry_budget"):
            GapRecoveryDecision(
                should_retry=True,
                missing_categories=(ContextCategory.DECISION,),
                retry_budget=-1,
                retry_reason=RetryReason.REQUIRED_CATEGORY_MISSING,
                recovery_strategy=RecoveryStrategy.RETRY_MISSING_CATEGORIES,
            )

    def test_should_retry_true_requires_required_category_missing_reason(self) -> None:
        with pytest.raises(ValueError, match="retry_reason"):
            GapRecoveryDecision(
                should_retry=True,
                missing_categories=(ContextCategory.DECISION,),
                retry_budget=1,
                retry_reason=RetryReason.NO_GAP,
                recovery_strategy=RecoveryStrategy.RETRY_MISSING_CATEGORIES,
            )

    def test_should_retry_false_with_required_category_missing_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="retry_reason"):
            GapRecoveryDecision(
                should_retry=False,
                missing_categories=(ContextCategory.DECISION,),
                retry_budget=0,
                retry_reason=RetryReason.REQUIRED_CATEGORY_MISSING,
                recovery_strategy=RecoveryStrategy.NONE,
            )

    def test_should_retry_true_requires_retry_missing_categories_strategy(self) -> None:
        with pytest.raises(ValueError, match="recovery_strategy"):
            GapRecoveryDecision(
                should_retry=True,
                missing_categories=(ContextCategory.DECISION,),
                retry_budget=1,
                retry_reason=RetryReason.REQUIRED_CATEGORY_MISSING,
                recovery_strategy=RecoveryStrategy.NONE,
            )

    def test_should_retry_false_requires_none_strategy(self) -> None:
        with pytest.raises(ValueError, match="recovery_strategy"):
            GapRecoveryDecision(
                should_retry=False,
                retry_reason=RetryReason.NO_GAP,
                recovery_strategy=RecoveryStrategy.RETRY_MISSING_CATEGORIES,
            )

    def test_should_retry_true_requires_positive_retry_budget(self) -> None:
        with pytest.raises(ValueError, match="retry_budget"):
            GapRecoveryDecision(
                should_retry=True,
                missing_categories=(ContextCategory.DECISION,),
                retry_budget=0,
                retry_reason=RetryReason.REQUIRED_CATEGORY_MISSING,
                recovery_strategy=RecoveryStrategy.RETRY_MISSING_CATEGORIES,
            )

    def test_should_retry_false_requires_zero_retry_budget(self) -> None:
        with pytest.raises(ValueError, match="retry_budget"):
            GapRecoveryDecision(
                should_retry=False,
                retry_reason=RetryReason.NO_GAP,
                retry_budget=1,
            )

    def test_should_retry_true_requires_nonempty_missing_categories(self) -> None:
        with pytest.raises(ValueError, match="missing_categories"):
            GapRecoveryDecision(
                should_retry=True,
                missing_categories=(),
                retry_budget=1,
                retry_reason=RetryReason.REQUIRED_CATEGORY_MISSING,
                recovery_strategy=RecoveryStrategy.RETRY_MISSING_CATEGORIES,
            )

    def test_no_gap_reason_requires_empty_missing_categories(self) -> None:
        with pytest.raises(ValueError, match="NO_GAP"):
            GapRecoveryDecision(
                should_retry=False,
                missing_categories=(ContextCategory.DECISION,),
                retry_reason=RetryReason.NO_GAP,
            )

    def test_low_plan_confidence_reason_requires_nonempty_missing_categories(self) -> None:
        with pytest.raises(ValueError, match="NO_GAP"):
            GapRecoveryDecision(
                should_retry=False,
                missing_categories=(),
                retry_reason=RetryReason.LOW_PLAN_CONFIDENCE,
            )

    def test_valid_no_retry_decision_does_not_raise(self) -> None:
        decision = GapRecoveryDecision(should_retry=False)
        assert decision.should_retry is False
        assert decision.retry_reason is RetryReason.NO_GAP
        assert decision.missing_categories == ()

    def test_valid_low_confidence_decision_does_not_raise(self) -> None:
        decision = GapRecoveryDecision(
            should_retry=False,
            missing_categories=(ContextCategory.BLOCKER,),
            retry_reason=RetryReason.LOW_PLAN_CONFIDENCE,
        )
        assert decision.missing_categories == (ContextCategory.BLOCKER,)

    def test_valid_retry_decision_does_not_raise(self) -> None:
        decision = GapRecoveryDecision(
            should_retry=True,
            missing_categories=(ContextCategory.TASK,),
            retry_budget=1,
            retry_reason=RetryReason.REQUIRED_CATEGORY_MISSING,
            recovery_strategy=RecoveryStrategy.RETRY_MISSING_CATEGORIES,
        )
        assert decision.should_retry is True


class TestGapRecoveryDecisionSerialization:
    def test_round_trip_no_retry(self) -> None:
        decision = GapRecoveryDecision(should_retry=False)
        restored = GapRecoveryDecision.from_dict(decision.to_dict())
        assert restored == decision

    def test_round_trip_retry(self) -> None:
        decision = GapRecoveryDecision(
            should_retry=True,
            missing_categories=(ContextCategory.CONSTRAINT, ContextCategory.BLOCKER),
            retry_budget=1,
            retry_reason=RetryReason.REQUIRED_CATEGORY_MISSING,
            recovery_strategy=RecoveryStrategy.RETRY_MISSING_CATEGORIES,
        )
        restored = GapRecoveryDecision.from_dict(decision.to_dict())
        assert restored == decision

    def test_to_dict_shape(self) -> None:
        decision = GapRecoveryDecision(
            should_retry=True,
            missing_categories=(ContextCategory.DECISION,),
            retry_budget=1,
            retry_reason=RetryReason.REQUIRED_CATEGORY_MISSING,
            confidence=1.0,
            recovery_strategy=RecoveryStrategy.RETRY_MISSING_CATEGORIES,
        )
        data = decision.to_dict()
        assert data["should_retry"] is True
        assert data["missing_categories"] == ["decision"]
        assert data["retry_budget"] == 1
        assert data["retry_reason"] == "required_category_missing"
        assert data["confidence"] == 1.0
        assert data["recovery_strategy"] == "retry_missing_categories"
        assert "created_at" in data

    def test_from_dict_missing_optional_keys_uses_defaults(self) -> None:
        restored = GapRecoveryDecision.from_dict({"should_retry": False})
        assert restored.missing_categories == ()
        assert restored.retry_budget == 0
        assert restored.retry_reason is RetryReason.NO_GAP
        assert restored.confidence == 1.0
        assert restored.recovery_strategy is RecoveryStrategy.NONE


class TestGapRecoveryDecisionImmutability:
    def test_frozen(self) -> None:
        decision = GapRecoveryDecision(should_retry=False)
        with pytest.raises(FrozenInstanceError):
            decision.should_retry = True  # type: ignore[misc]

    def test_equality_by_value(self) -> None:
        a = GapRecoveryDecision(should_retry=False)
        b = GapRecoveryDecision(should_retry=False)
        assert a.retry_reason == b.retry_reason
        assert a.missing_categories == b.missing_categories
        assert a.should_retry == b.should_retry


# ===========================================================================
# decide_gap_recovery
# ===========================================================================


class TestDecideGapRecoveryNoGap:
    def test_fully_satisfied_coverage_yields_no_retry(self) -> None:
        plan = make_plan((make_requirement(ContextCategory.DECISION),))
        coverage = make_coverage((satisfied_entry(ContextCategory.DECISION),))

        decision = decide_gap_recovery(plan, coverage)

        assert decision.should_retry is False
        assert decision.retry_reason is RetryReason.NO_GAP
        assert decision.missing_categories == ()
        assert decision.retry_budget == 0
        assert decision.recovery_strategy is RecoveryStrategy.NONE

    def test_pointed_qa_sentinel_yields_no_retry(self) -> None:
        # The TaskMode.POINTED_QA sentinel: empty requirements -> empty
        # CoverageReport -- there is nothing to recover from, trivially.
        plan = ContextPlan(query="what database do I use", task_mode=TaskMode.POINTED_QA)
        coverage = make_coverage(())

        decision = decide_gap_recovery(plan, coverage)

        assert decision.should_retry is False
        assert decision.retry_reason is RetryReason.NO_GAP
        assert decision.missing_categories == ()

    def test_empty_coverage_report_yields_no_retry_regardless_of_plan(self) -> None:
        plan = make_plan(confidence=0.0)
        coverage = make_coverage(())

        decision = decide_gap_recovery(plan, coverage)

        assert decision.should_retry is False
        assert decision.retry_reason is RetryReason.NO_GAP

    def test_optional_unmet_requirement_is_not_a_gap(self) -> None:
        plan = make_plan(
            (make_requirement(ContextCategory.BELIEF, necessity=Necessity.OPTIONAL),)
        )
        coverage = make_coverage(
            (
                CategoryCoverage(
                    category=ContextCategory.BELIEF,
                    necessity=Necessity.OPTIONAL,
                    required_minimum=1,
                    retrieved_count=0,
                ),
            )
        )

        decision = decide_gap_recovery(plan, coverage)

        assert decision.should_retry is False
        assert decision.retry_reason is RetryReason.NO_GAP


class TestDecideGapRecoveryLowConfidence:
    def test_gap_with_low_confidence_plan_does_not_retry_but_records_gap(self) -> None:
        plan = make_plan(
            (make_requirement(ContextCategory.BLOCKER),),
            confidence=MIN_PLAN_CONFIDENCE_FOR_RETRY - 0.01,
        )
        coverage = make_coverage((missing_entry(ContextCategory.BLOCKER),))

        decision = decide_gap_recovery(plan, coverage)

        assert decision.should_retry is False
        assert decision.retry_reason is RetryReason.LOW_PLAN_CONFIDENCE
        assert decision.missing_categories == (ContextCategory.BLOCKER,)
        assert decision.retry_budget == 0
        assert decision.recovery_strategy is RecoveryStrategy.NONE

    def test_confidence_exactly_at_threshold_does_retry(self) -> None:
        # The threshold is inclusive: >= MIN_PLAN_CONFIDENCE_FOR_RETRY retries.
        plan = make_plan(
            (make_requirement(ContextCategory.BLOCKER),),
            confidence=MIN_PLAN_CONFIDENCE_FOR_RETRY,
        )
        coverage = make_coverage((missing_entry(ContextCategory.BLOCKER),))

        decision = decide_gap_recovery(plan, coverage)

        assert decision.should_retry is True
        assert decision.retry_reason is RetryReason.REQUIRED_CATEGORY_MISSING


class TestDecideGapRecoveryRequiredMissing:
    def test_gap_with_confident_plan_recommends_retry(self) -> None:
        plan = make_plan((make_requirement(ContextCategory.CONSTRAINT),), confidence=1.0)
        coverage = make_coverage((missing_entry(ContextCategory.CONSTRAINT),))

        decision = decide_gap_recovery(plan, coverage)

        assert decision.should_retry is True
        assert decision.retry_reason is RetryReason.REQUIRED_CATEGORY_MISSING
        assert decision.missing_categories == (ContextCategory.CONSTRAINT,)
        assert decision.retry_budget == DEFAULT_RETRY_BUDGET
        assert decision.recovery_strategy is RecoveryStrategy.RETRY_MISSING_CATEGORIES
        assert decision.confidence == 1.0

    def test_multiple_missing_categories_all_recorded(self) -> None:
        plan = make_plan(
            (
                make_requirement(ContextCategory.BLOCKER),
                make_requirement(ContextCategory.OPEN_QUESTION),
            ),
            confidence=1.0,
        )
        coverage = make_coverage(
            (
                missing_entry(ContextCategory.BLOCKER),
                missing_entry(ContextCategory.OPEN_QUESTION),
            )
        )

        decision = decide_gap_recovery(plan, coverage)

        assert decision.should_retry is True
        assert set(decision.missing_categories) == {
            ContextCategory.BLOCKER,
            ContextCategory.OPEN_QUESTION,
        }

    def test_partial_coverage_below_minimum_still_counts_as_a_gap(self) -> None:
        # required_minimum=2, retrieved_count=1 -> PARTIAL, still unsatisfied.
        plan = make_plan((make_requirement(ContextCategory.TASK, min_count=2),), confidence=1.0)
        coverage = make_coverage(
            (missing_entry(ContextCategory.TASK, required_minimum=2, retrieved_count=1),)
        )

        decision = decide_gap_recovery(plan, coverage)

        assert decision.should_retry is True
        assert decision.missing_categories == (ContextCategory.TASK,)

    def test_mixed_satisfied_and_missing_categories_only_missing_recorded(self) -> None:
        plan = make_plan(
            (
                make_requirement(ContextCategory.DECISION),
                make_requirement(ContextCategory.TASK),
            ),
            confidence=1.0,
        )
        coverage = make_coverage(
            (
                satisfied_entry(ContextCategory.DECISION),
                missing_entry(ContextCategory.TASK),
            )
        )

        decision = decide_gap_recovery(plan, coverage)

        assert decision.should_retry is True
        assert decision.missing_categories == (ContextCategory.TASK,)


class TestDecideGapRecoveryDeterminism:
    def test_repeated_calls_produce_equal_decision(self) -> None:
        plan = make_plan((make_requirement(ContextCategory.DECISION),), confidence=1.0)
        coverage = make_coverage((missing_entry(ContextCategory.DECISION),))

        first = decide_gap_recovery(plan, coverage)
        for _ in range(5):
            again = decide_gap_recovery(plan, coverage)
            assert again.should_retry == first.should_retry
            assert again.missing_categories == first.missing_categories
            assert again.retry_reason == first.retry_reason
            assert again.retry_budget == first.retry_budget
            assert again.recovery_strategy == first.recovery_strategy

    def test_no_gap_case_is_also_deterministic(self) -> None:
        plan = make_plan()
        coverage = make_coverage(())

        first = decide_gap_recovery(plan, coverage)
        second = decide_gap_recovery(plan, coverage)
        assert first.should_retry == second.should_retry
        assert first.retry_reason == second.retry_reason


class TestDecideGapRecoveryObservationalOnly:
    def test_inputs_are_not_mutated(self) -> None:
        plan = make_plan((make_requirement(ContextCategory.DECISION),), confidence=1.0)
        coverage = make_coverage((missing_entry(ContextCategory.DECISION),))
        plan_before = plan
        coverage_before = coverage

        decide_gap_recovery(plan, coverage)

        # Frozen dataclasses can't be mutated in place, but assert identity
        # and equality to make the "read-only" contract explicit and
        # regression-proof against a future refactor that might try to
        # rebuild/replace either argument.
        assert plan is plan_before
        assert coverage is coverage_before
        assert plan == plan_before
        assert coverage == coverage_before

    def test_decision_has_no_field_referencing_retrieval_internals(self) -> None:
        # A structural guard: GapRecoveryDecision's fields are exactly the
        # ones documented -- no retriever/ranker/LLM-shaped field could have
        # been added without this test needing an update.
        plan = make_plan()
        coverage = make_coverage(())
        decision = decide_gap_recovery(plan, coverage)
        field_names = {f for f in decision.__dataclass_fields__}
        assert field_names == {
            "should_retry",
            "missing_categories",
            "retry_budget",
            "retry_reason",
            "confidence",
            "recovery_strategy",
            "created_at",
        }
