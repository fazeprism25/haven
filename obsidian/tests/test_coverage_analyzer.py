"""Unit tests for obsidian.memory_engine.coverage_analyzer.

Test groups
-----------
TestCategoryCoverageValidation   — required_minimum/retrieved_count bound
                                    checks.
TestCategoryCoverageStatus       — FULL/PARTIAL/MISSING derivation from
                                    retrieved_count vs. required_minimum.
TestCategoryCoverageSerialization — to_dict/from_dict round-trips, including
                                    ignoring stale derived keys.
TestCategoryCoverageImmutability  — frozen, hashable, value equality.
TestCoverageReportDerivedFields   — missing_required_categories,
                                     fully_satisfied, overall_coverage_percentage.
TestCoverageReportSerialization   — to_dict/from_dict round-trips.
TestAnalyzeCoverageCompleteCoverage — every REQUIRED category fully met.
TestAnalyzeCoveragePartialCoverage  — some categories under-retrieved.
TestAnalyzeCoveragePointedQASentinel — empty plan.requirements -> empty report.
TestAnalyzeCoverageMissingRequired   — REQUIRED categories with zero retrieved.
TestAnalyzeCoverageOptionalNeverGaps — unmet OPTIONAL requirements never
                                        appear in missing_required_categories.
TestAnalyzeCoverageIgnoresRejected   — rejected candidates never count toward
                                        retrieved_count.
TestAnalyzeCoverageUnmappedMemoryType — memory types with no ContextCategory
                                         mapping contribute to no category.
TestAnalyzeCoverageDeterminism        — repeated calls with the same input
                                         produce an equal report.
TestAnalyzeCoverageProjectStateCategories — CONSTRAINT (via RULE, previously
                                         untested) and the newly-added
                                         BLOCKER/IMPLEMENTATION_STATE/
                                         CODE_AREA/OPEN_QUESTION MemoryTypes
                                         resolve to their ContextCategory and
                                         are no longer permanently MISSING.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from obsidian.core.enums import MemoryType
from obsidian.memory_engine.context_planner import (
    CategoryRequirement,
    ContextCategory,
    ContextPlan,
    Necessity,
    TaskMode,
)
from obsidian.memory_engine.coverage_analyzer import (
    CategoryCoverage,
    CoverageReport,
    CoverageStatus,
    analyze_coverage,
)
from obsidian.ontology.retrieval_models import CandidateTrace

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def make_candidate_trace(
    memory_type: MemoryType = MemoryType.DECISION,
    accepted: bool = True,
    rejection_reason: str | None = None,
    final_rank: int = 1,
) -> CandidateTrace:
    return CandidateTrace(
        knowledge_object_id=uuid4(),
        canonical_fact="a fact",
        memory_type=memory_type,
        matched_by_keyword=True,
        matched_by_ontology=False,
        activation_score=0.0,
        attachment_relevance=0.0,
        keyword_overlap_score=0.5,
        importance=0.5,
        confidence=0.5,
        final_score=0.6,
        accepted=accepted,
        rejection_reason=rejection_reason,
        final_rank=final_rank,
    )


def make_requirement(
    category: ContextCategory,
    necessity: Necessity = Necessity.REQUIRED,
    min_count: int = 1,
) -> CategoryRequirement:
    return CategoryRequirement(category=category, necessity=necessity, min_count=min_count)


def make_plan(requirements: tuple[CategoryRequirement, ...]) -> ContextPlan:
    return ContextPlan(
        query="continue implementing Haven",
        task_mode=TaskMode.CONTINUATION,
        requirements=requirements,
    )


# ===========================================================================
# CategoryCoverage
# ===========================================================================


class TestCategoryCoverageValidation:
    def test_negative_required_minimum_raises(self) -> None:
        with pytest.raises(ValueError, match="required_minimum"):
            CategoryCoverage(
                category=ContextCategory.DECISION,
                necessity=Necessity.REQUIRED,
                required_minimum=-1,
                retrieved_count=0,
            )

    def test_negative_retrieved_count_raises(self) -> None:
        with pytest.raises(ValueError, match="retrieved_count"):
            CategoryCoverage(
                category=ContextCategory.DECISION,
                necessity=Necessity.REQUIRED,
                required_minimum=1,
                retrieved_count=-1,
            )

    def test_zero_values_are_valid(self) -> None:
        entry = CategoryCoverage(
            category=ContextCategory.DECISION,
            necessity=Necessity.OPTIONAL,
            required_minimum=0,
            retrieved_count=0,
        )
        assert entry.required_minimum == 0
        assert entry.retrieved_count == 0


class TestCategoryCoverageStatus:
    def test_retrieved_at_or_above_minimum_is_full(self) -> None:
        entry = CategoryCoverage(
            category=ContextCategory.TASK,
            necessity=Necessity.REQUIRED,
            required_minimum=2,
            retrieved_count=2,
        )
        assert entry.status is CoverageStatus.FULL
        assert entry.satisfied is True

    def test_retrieved_above_minimum_is_still_full(self) -> None:
        entry = CategoryCoverage(
            category=ContextCategory.TASK,
            necessity=Necessity.REQUIRED,
            required_minimum=1,
            retrieved_count=5,
        )
        assert entry.status is CoverageStatus.FULL

    def test_retrieved_below_minimum_but_nonzero_is_partial(self) -> None:
        entry = CategoryCoverage(
            category=ContextCategory.TASK,
            necessity=Necessity.REQUIRED,
            required_minimum=3,
            retrieved_count=1,
        )
        assert entry.status is CoverageStatus.PARTIAL
        assert entry.satisfied is False

    def test_zero_retrieved_with_positive_minimum_is_missing(self) -> None:
        entry = CategoryCoverage(
            category=ContextCategory.TASK,
            necessity=Necessity.REQUIRED,
            required_minimum=1,
            retrieved_count=0,
        )
        assert entry.status is CoverageStatus.MISSING
        assert entry.satisfied is False

    def test_zero_retrieved_with_zero_minimum_is_full(self) -> None:
        entry = CategoryCoverage(
            category=ContextCategory.TASK,
            necessity=Necessity.OPTIONAL,
            required_minimum=0,
            retrieved_count=0,
        )
        assert entry.status is CoverageStatus.FULL
        assert entry.satisfied is True


class TestCategoryCoverageSerialization:
    def test_round_trip(self) -> None:
        entry = CategoryCoverage(
            category=ContextCategory.CONSTRAINT,
            necessity=Necessity.REQUIRED,
            required_minimum=2,
            retrieved_count=1,
        )
        restored = CategoryCoverage.from_dict(entry.to_dict())
        assert restored == entry

    def test_to_dict_includes_derived_fields(self) -> None:
        entry = CategoryCoverage(
            category=ContextCategory.BELIEF,
            necessity=Necessity.OPTIONAL,
            required_minimum=1,
            retrieved_count=1,
        )
        data = entry.to_dict()
        assert data == {
            "category": "belief",
            "necessity": "optional",
            "required_minimum": 1,
            "retrieved_count": 1,
            "satisfied": True,
            "status": "full",
        }

    def test_from_dict_ignores_stale_derived_keys(self) -> None:
        # satisfied/status in the payload are recomputed, not trusted --
        # a hand-edited or stale dict cannot desynchronise them.
        restored = CategoryCoverage.from_dict(
            {
                "category": "task",
                "necessity": "required",
                "required_minimum": 5,
                "retrieved_count": 0,
                "satisfied": True,
                "status": "full",
            }
        )
        assert restored.satisfied is False
        assert restored.status is CoverageStatus.MISSING


class TestCategoryCoverageImmutability:
    def test_frozen(self) -> None:
        entry = CategoryCoverage(
            category=ContextCategory.DECISION,
            necessity=Necessity.REQUIRED,
            required_minimum=1,
            retrieved_count=1,
        )
        with pytest.raises(FrozenInstanceError):
            entry.retrieved_count = 9  # type: ignore[misc]

    def test_equality_by_value(self) -> None:
        a = CategoryCoverage(
            category=ContextCategory.DECISION,
            necessity=Necessity.REQUIRED,
            required_minimum=1,
            retrieved_count=1,
        )
        b = CategoryCoverage(
            category=ContextCategory.DECISION,
            necessity=Necessity.REQUIRED,
            required_minimum=1,
            retrieved_count=1,
        )
        assert a == b
        assert hash(a) == hash(b)


# ===========================================================================
# CoverageReport
# ===========================================================================


class TestCoverageReportDerivedFields:
    def test_empty_entries_is_fully_satisfied_with_full_percentage(self) -> None:
        report = CoverageReport(entries=())
        assert report.missing_required_categories == ()
        assert report.fully_satisfied is True
        assert report.overall_coverage_percentage == 100.0

    def test_all_required_satisfied(self) -> None:
        report = CoverageReport(
            entries=(
                CategoryCoverage(
                    category=ContextCategory.DECISION,
                    necessity=Necessity.REQUIRED,
                    required_minimum=1,
                    retrieved_count=1,
                ),
                CategoryCoverage(
                    category=ContextCategory.TASK,
                    necessity=Necessity.REQUIRED,
                    required_minimum=2,
                    retrieved_count=3,
                ),
            )
        )
        assert report.fully_satisfied is True
        assert report.missing_required_categories == ()
        assert report.overall_coverage_percentage == 100.0

    def test_one_of_two_required_unsatisfied_is_fifty_percent(self) -> None:
        report = CoverageReport(
            entries=(
                CategoryCoverage(
                    category=ContextCategory.DECISION,
                    necessity=Necessity.REQUIRED,
                    required_minimum=1,
                    retrieved_count=1,
                ),
                CategoryCoverage(
                    category=ContextCategory.TASK,
                    necessity=Necessity.REQUIRED,
                    required_minimum=2,
                    retrieved_count=0,
                ),
            )
        )
        assert report.fully_satisfied is False
        assert report.missing_required_categories == (ContextCategory.TASK,)
        assert report.overall_coverage_percentage == 50.0

    def test_optional_unsatisfied_never_appears_as_missing(self) -> None:
        report = CoverageReport(
            entries=(
                CategoryCoverage(
                    category=ContextCategory.BELIEF,
                    necessity=Necessity.OPTIONAL,
                    required_minimum=2,
                    retrieved_count=0,
                ),
            )
        )
        assert report.missing_required_categories == ()
        assert report.fully_satisfied is True
        # No REQUIRED entries at all -> percentage is trivially full.
        assert report.overall_coverage_percentage == 100.0

    def test_optional_entries_excluded_from_percentage_denominator(self) -> None:
        report = CoverageReport(
            entries=(
                CategoryCoverage(
                    category=ContextCategory.DECISION,
                    necessity=Necessity.REQUIRED,
                    required_minimum=1,
                    retrieved_count=0,
                ),
                CategoryCoverage(
                    category=ContextCategory.BELIEF,
                    necessity=Necessity.OPTIONAL,
                    required_minimum=5,
                    retrieved_count=5,
                ),
            )
        )
        # Only one REQUIRED entry, unsatisfied -> 0%, regardless of the
        # fully-satisfied OPTIONAL entry alongside it.
        assert report.overall_coverage_percentage == 0.0


class TestCoverageReportSerialization:
    def test_round_trip(self) -> None:
        report = CoverageReport(
            entries=(
                CategoryCoverage(
                    category=ContextCategory.DECISION,
                    necessity=Necessity.REQUIRED,
                    required_minimum=1,
                    retrieved_count=1,
                ),
                CategoryCoverage(
                    category=ContextCategory.OPEN_QUESTION,
                    necessity=Necessity.REQUIRED,
                    required_minimum=1,
                    retrieved_count=0,
                ),
            )
        )
        restored = CoverageReport.from_dict(report.to_dict())
        assert restored == report
        assert restored.fully_satisfied == report.fully_satisfied
        assert (
            restored.overall_coverage_percentage == report.overall_coverage_percentage
        )
        assert (
            restored.missing_required_categories == report.missing_required_categories
        )

    def test_round_trip_empty_entries(self) -> None:
        report = CoverageReport(entries=())
        restored = CoverageReport.from_dict(report.to_dict())
        assert restored == report


# ===========================================================================
# analyze_coverage
# ===========================================================================


class TestAnalyzeCoverageCompleteCoverage:
    def test_every_required_category_fully_met(self) -> None:
        plan = make_plan(
            (
                make_requirement(ContextCategory.DECISION, min_count=1),
                make_requirement(ContextCategory.TASK, min_count=1),
            )
        )
        candidates = (
            make_candidate_trace(memory_type=MemoryType.DECISION, accepted=True),
            make_candidate_trace(memory_type=MemoryType.TASK, accepted=True),
        )
        report = analyze_coverage(plan, candidates)
        assert report.fully_satisfied is True
        assert report.overall_coverage_percentage == 100.0
        assert all(e.satisfied for e in report.entries)


class TestAnalyzeCoveragePartialCoverage:
    def test_some_categories_under_retrieved(self) -> None:
        plan = make_plan(
            (
                make_requirement(ContextCategory.DECISION, min_count=2),
                make_requirement(ContextCategory.TASK, min_count=1),
            )
        )
        candidates = (
            make_candidate_trace(memory_type=MemoryType.DECISION, accepted=True),
            make_candidate_trace(memory_type=MemoryType.TASK, accepted=True),
        )
        report = analyze_coverage(plan, candidates)
        decision_entry = next(
            e for e in report.entries if e.category is ContextCategory.DECISION
        )
        task_entry = next(
            e for e in report.entries if e.category is ContextCategory.TASK
        )
        assert decision_entry.status is CoverageStatus.PARTIAL
        assert decision_entry.retrieved_count == 1
        assert task_entry.status is CoverageStatus.FULL
        assert report.fully_satisfied is False
        assert report.missing_required_categories == (ContextCategory.DECISION,)
        assert report.overall_coverage_percentage == 50.0


class TestAnalyzeCoveragePointedQASentinel:
    def test_no_requirements_produces_empty_report(self) -> None:
        plan = ContextPlan(query="what database do I use", task_mode=TaskMode.POINTED_QA)
        assert plan.requirements == ()
        report = analyze_coverage(plan, candidates=())
        assert report.entries == ()
        assert report.fully_satisfied is True
        assert report.overall_coverage_percentage == 100.0
        assert report.missing_required_categories == ()

    def test_no_requirements_ignores_any_candidates_passed(self) -> None:
        plan = ContextPlan(query="", task_mode=TaskMode.POINTED_QA)
        candidates = (make_candidate_trace(memory_type=MemoryType.DECISION, accepted=True),)
        report = analyze_coverage(plan, candidates)
        assert report.entries == ()


class TestAnalyzeCoverageMissingRequired:
    def test_required_category_with_zero_retrieved_is_missing(self) -> None:
        plan = make_plan((make_requirement(ContextCategory.BLOCKER, min_count=1),))
        report = analyze_coverage(plan, candidates=())
        assert len(report.entries) == 1
        entry = report.entries[0]
        assert entry.status is CoverageStatus.MISSING
        assert entry.retrieved_count == 0
        assert report.missing_required_categories == (ContextCategory.BLOCKER,)
        assert report.fully_satisfied is False
        assert report.overall_coverage_percentage == 0.0

    def test_multiple_missing_required_categories_all_reported(self) -> None:
        plan = make_plan(
            (
                make_requirement(ContextCategory.BLOCKER, min_count=1),
                make_requirement(ContextCategory.OPEN_QUESTION, min_count=1),
            )
        )
        report = analyze_coverage(plan, candidates=())
        assert set(report.missing_required_categories) == {
            ContextCategory.BLOCKER,
            ContextCategory.OPEN_QUESTION,
        }


class TestAnalyzeCoverageOptionalNeverGaps:
    def test_unmet_optional_requirement_not_in_missing_required(self) -> None:
        plan = make_plan(
            (
                make_requirement(
                    ContextCategory.BELIEF, necessity=Necessity.OPTIONAL, min_count=3
                ),
            )
        )
        report = analyze_coverage(plan, candidates=())
        assert report.entries[0].satisfied is False
        assert report.missing_required_categories == ()
        assert report.fully_satisfied is True


class TestAnalyzeCoverageIgnoresRejected:
    def test_rejected_candidates_do_not_count_toward_retrieved_count(self) -> None:
        plan = make_plan((make_requirement(ContextCategory.DECISION, min_count=1),))
        candidates = (
            make_candidate_trace(
                memory_type=MemoryType.DECISION,
                accepted=False,
                rejection_reason="below_minimum_score",
            ),
        )
        report = analyze_coverage(plan, candidates)
        assert report.entries[0].retrieved_count == 0
        assert report.entries[0].status is CoverageStatus.MISSING


class TestAnalyzeCoverageUnmappedMemoryType:
    @pytest.mark.parametrize(
        "memory_type",
        [
            MemoryType.GOAL,
            MemoryType.PROJECT,
            MemoryType.PERSON,
            MemoryType.EVENT,
            MemoryType.SKILL,
            MemoryType.PREFERENCE,
        ],
    )
    def test_unmapped_memory_types_contribute_to_no_category(
        self, memory_type: MemoryType
    ) -> None:
        plan = make_plan((make_requirement(ContextCategory.DECISION, min_count=1),))
        candidates = (make_candidate_trace(memory_type=memory_type, accepted=True),)
        report = analyze_coverage(plan, candidates)
        assert report.entries[0].retrieved_count == 0


class TestAnalyzeCoverageDeterminism:
    def test_repeated_calls_produce_equal_reports(self) -> None:
        plan = make_plan(
            (
                make_requirement(ContextCategory.DECISION, min_count=1),
                make_requirement(ContextCategory.TASK, min_count=2),
            )
        )
        candidates = (
            make_candidate_trace(memory_type=MemoryType.DECISION, accepted=True, final_rank=1),
            make_candidate_trace(memory_type=MemoryType.TASK, accepted=True, final_rank=2),
        )
        first = analyze_coverage(plan, candidates)
        second = analyze_coverage(plan, candidates)
        assert first == second
        assert first.overall_coverage_percentage == second.overall_coverage_percentage
        assert first.missing_required_categories == second.missing_required_categories


# ===========================================================================
# Project-state category resolution
#
# See docs/architecture/PROJECT_STATE_KNOWLEDGE_MODEL.md. CONSTRAINT was
# already resolvable via MemoryType.RULE before this change but had no test
# coverage; BLOCKER/IMPLEMENTATION_STATE/CODE_AREA/OPEN_QUESTION are newly
# resolvable via the MemoryType members added alongside this test.
# ===========================================================================


class TestAnalyzeCoverageProjectStateCategories:
    @pytest.mark.parametrize(
        "memory_type,category",
        [
            (MemoryType.RULE, ContextCategory.CONSTRAINT),
            (MemoryType.BLOCKER, ContextCategory.BLOCKER),
            (MemoryType.IMPLEMENTATION_STATE, ContextCategory.IMPLEMENTATION_STATE),
            (MemoryType.CODE_AREA, ContextCategory.CODE_AREA),
            (MemoryType.OPEN_QUESTION, ContextCategory.OPEN_QUESTION),
        ],
    )
    def test_accepted_candidate_satisfies_its_category(
        self, memory_type: MemoryType, category: ContextCategory
    ) -> None:
        plan = make_plan((make_requirement(category, min_count=1),))
        candidates = (make_candidate_trace(memory_type=memory_type, accepted=True),)
        report = analyze_coverage(plan, candidates)
        assert report.entries[0].retrieved_count == 1
        assert report.entries[0].status is CoverageStatus.FULL
        assert report.fully_satisfied is True

    @pytest.mark.parametrize(
        "memory_type,category",
        [
            (MemoryType.BLOCKER, ContextCategory.BLOCKER),
            (MemoryType.IMPLEMENTATION_STATE, ContextCategory.IMPLEMENTATION_STATE),
            (MemoryType.CODE_AREA, ContextCategory.CODE_AREA),
            (MemoryType.OPEN_QUESTION, ContextCategory.OPEN_QUESTION),
        ],
    )
    def test_requirement_no_longer_permanently_missing(
        self, memory_type: MemoryType, category: ContextCategory
    ) -> None:
        # Before this change, these four categories could never leave MISSING
        # regardless of what was retrieved -- no MemoryType resolved to them.
        plan = make_plan((make_requirement(category, min_count=1),))
        report_without = analyze_coverage(plan, candidates=())
        assert report_without.entries[0].status is CoverageStatus.MISSING

        candidates = (make_candidate_trace(memory_type=memory_type, accepted=True),)
        report_with = analyze_coverage(plan, candidates)
        assert report_with.entries[0].status is CoverageStatus.FULL
