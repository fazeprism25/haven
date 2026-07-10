"""Unit tests for obsidian.ontology.retrieval_models.

Test groups
-----------
TestActivatedConcept        — validation, immutability, serialisation.
TestCandidate                — validation, immutability, metadata freezing,
                                serialisation, optional ontology evidence
                                (``has_ontology_evidence``).
TestRankedCandidate          — validation, deterministic ordering,
                                serialisation.
TestCandidateTrace            — validation, accepted/rejection_reason
                                consistency, serialisation.
TestRetrievalPipelineStats    — validation, serialisation.
TestRetrievalTrace           — validation, immutability, serialisation,
                                debug-only intent.
TestContextCategoryRequirementTrace — validation, serialisation.
TestContextPlanTrace          — validation, immutability, serialisation.
TestCategoryCoverageTrace     — validation, serialisation.
TestCoverageReportTrace        — validation, immutability, serialisation.
TestGapRecoveryTrace           — validation, immutability, serialisation.
TestStateRefTrace              — validation, immutability, serialisation.
TestProjectStateFieldTrace     — validation, immutability, serialisation.
TestProjectStateTrace          — defaults, immutability, serialisation
                                  (with and without ``current_objective``).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from types import MappingProxyType
from uuid import UUID, uuid4

import pytest

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.ontology.retrieval_models import (
    REJECTION_ACCEPTANCE_CAP_EXCEEDED,
    REJECTION_BELOW_ABSTENTION_FLOOR,
    REJECTION_BELOW_MINIMUM_SCORE,
    REJECTION_BELOW_RELATIVE_FLOOR,
    REJECTION_SCORE_GAP_CUT,
    REJECTION_SLOT_BUDGET_EXCEEDED,
    ActivatedConcept,
    Candidate,
    CandidateTrace,
    CategoryCoverageTrace,
    ContextCategoryRequirementTrace,
    ContextPlanTrace,
    CoverageReportTrace,
    GapRecoveryTrace,
    ProjectStateFieldTrace,
    ProjectStateTrace,
    RankedCandidate,
    RetrievalPipelineStats,
    RetrievalTrace,
    StateRefTrace,
)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def make_ko(fact: str = "Haven uses Claude") -> KnowledgeObject:
    return KnowledgeObject(canonical_fact=fact, memory_type=MemoryType.FACT)


def make_activated_concept(
    concept_id: UUID | None = None,
    activation_score: float = 1.0,
    activation_depth: int = 0,
    source_seed: UUID | None = None,
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
    ko: KnowledgeObject | None = None,
    supporting_concepts: tuple[ActivatedConcept, ...] | None = None,
    attachment_relevance: float = 0.9,
    activation_score: float = 0.8,
    retrieval_metadata: dict | None = None,
    keyword_overlap_score: float = 0.0,
) -> Candidate:
    return Candidate(
        knowledge_object=ko if ko is not None else make_ko(),
        supporting_concepts=(
            supporting_concepts
            if supporting_concepts is not None
            else (make_activated_concept(),)
        ),
        attachment_relevance=attachment_relevance,
        activation_score=activation_score,
        retrieval_metadata=retrieval_metadata if retrieval_metadata is not None else {},
        keyword_overlap_score=keyword_overlap_score,
    )


def make_ranked(
    candidate: Candidate | None = None,
    final_score: float = 0.75,
    score_breakdown: dict | None = None,
) -> RankedCandidate:
    return RankedCandidate(
        candidate=candidate if candidate is not None else make_candidate(),
        final_score=final_score,
        score_breakdown=score_breakdown if score_breakdown is not None else {"activation": 0.75},
    )


def make_candidate_trace(
    knowledge_object_id: UUID | None = None,
    canonical_fact: str = "Haven uses Claude",
    memory_type: MemoryType = MemoryType.FACT,
    matched_by_keyword: bool = True,
    matched_by_ontology: bool = True,
    activation_score: float = 0.8,
    attachment_relevance: float = 0.9,
    keyword_overlap_score: float = 0.6,
    importance: float = 0.5,
    confidence: float = 0.5,
    final_score: float = 0.75,
    accepted: bool = True,
    rejection_reason: str | None = None,
    final_rank: int = 1,
    threshold_used: float | None = None,
    score_gap: float | None = None,
    relative_score: float = 1.0,
    abstained: bool = False,
    base_score: float | None = None,
    category_preference_bonus: float = 0.0,
) -> CandidateTrace:
    return CandidateTrace(
        knowledge_object_id=knowledge_object_id if knowledge_object_id is not None else uuid4(),
        canonical_fact=canonical_fact,
        memory_type=memory_type,
        matched_by_keyword=matched_by_keyword,
        matched_by_ontology=matched_by_ontology,
        activation_score=activation_score,
        attachment_relevance=attachment_relevance,
        keyword_overlap_score=keyword_overlap_score,
        importance=importance,
        confidence=confidence,
        final_score=final_score,
        accepted=accepted,
        rejection_reason=rejection_reason,
        final_rank=final_rank,
        threshold_used=threshold_used,
        score_gap=score_gap,
        relative_score=relative_score,
        abstained=abstained,
        base_score=base_score,
        category_preference_bonus=category_preference_bonus,
    )


def make_pipeline_stats(
    total_ontology_candidates: int = 2,
    total_keyword_candidates: int = 1,
    total_merged_candidates: int = 2,
    total_accepted_candidates: int = 1,
    total_rejected_candidates: int = 1,
    final_context_size: int = 42,
    retrieval_latency_ms: float = 3.5,
) -> RetrievalPipelineStats:
    return RetrievalPipelineStats(
        total_ontology_candidates=total_ontology_candidates,
        total_keyword_candidates=total_keyword_candidates,
        total_merged_candidates=total_merged_candidates,
        total_accepted_candidates=total_accepted_candidates,
        total_rejected_candidates=total_rejected_candidates,
        final_context_size=final_context_size,
        retrieval_latency_ms=retrieval_latency_ms,
    )


def make_requirement_trace(
    category: str = "decision",
    necessity: str = "required",
    min_count: int = 1,
    max_count: int | None = None,
    priority_tier: str = "normal",
) -> ContextCategoryRequirementTrace:
    return ContextCategoryRequirementTrace(
        category=category,
        necessity=necessity,
        min_count=min_count,
        max_count=max_count,
        priority_tier=priority_tier,
    )


def make_context_plan_trace(
    task_mode: str = "coding_debugging",
    planning_method: str = "deterministic",
    scope_concept_id: UUID | None = None,
    confidence: float = 1.0,
    requirements: tuple[ContextCategoryRequirementTrace, ...] = (),
) -> ContextPlanTrace:
    return ContextPlanTrace(
        task_mode=task_mode,
        planning_method=planning_method,
        scope_concept_id=scope_concept_id,
        confidence=confidence,
        requirements=requirements,
    )


def make_category_coverage_trace(
    category: str = "decision",
    necessity: str = "required",
    required_minimum: int = 1,
    retrieved_count: int = 1,
    satisfied: bool = True,
    status: str = "full",
) -> CategoryCoverageTrace:
    return CategoryCoverageTrace(
        category=category,
        necessity=necessity,
        required_minimum=required_minimum,
        retrieved_count=retrieved_count,
        satisfied=satisfied,
        status=status,
    )


def make_coverage_report_trace(
    entries: tuple[CategoryCoverageTrace, ...] = (),
    overall_coverage_percentage: float = 100.0,
    missing_required_categories: tuple[str, ...] = (),
    fully_satisfied: bool = True,
) -> CoverageReportTrace:
    return CoverageReportTrace(
        entries=entries,
        overall_coverage_percentage=overall_coverage_percentage,
        missing_required_categories=missing_required_categories,
        fully_satisfied=fully_satisfied,
    )


def make_gap_recovery_trace(
    should_retry: bool = False,
    missing_categories: tuple[str, ...] = (),
    retry_budget: int = 0,
    retry_reason: str = "no_gap",
    confidence: float = 1.0,
    recovery_strategy: str = "none",
) -> GapRecoveryTrace:
    return GapRecoveryTrace(
        should_retry=should_retry,
        missing_categories=missing_categories,
        retry_budget=retry_budget,
        retry_reason=retry_reason,
        confidence=confidence,
        recovery_strategy=recovery_strategy,
    )


def make_state_ref_trace(
    knowledge_object_id: UUID | None = None,
    canonical_fact: str = "a fact",
    valid_from: datetime | None = None,
    confidence: float = 0.5,
    importance: float = 0.5,
) -> StateRefTrace:
    return StateRefTrace(
        knowledge_object_id=knowledge_object_id if knowledge_object_id is not None else uuid4(),
        canonical_fact=canonical_fact,
        valid_from=valid_from if valid_from is not None else datetime(2024, 1, 1),
        confidence=confidence,
        importance=importance,
    )


def make_project_state_trace(
    current_objective: ProjectStateFieldTrace | None = None,
    decisions: tuple[StateRefTrace, ...] = (),
    superseded_decisions: tuple[StateRefTrace, ...] = (),
    active_tasks: tuple[StateRefTrace, ...] = (),
    blockers: tuple[StateRefTrace, ...] = (),
    constraints: tuple[StateRefTrace, ...] = (),
    implementation_state: tuple[StateRefTrace, ...] = (),
    code_areas: tuple[StateRefTrace, ...] = (),
    open_questions: tuple[StateRefTrace, ...] = (),
    gaps: tuple[str, ...] = (),
    confidence: float = 1.0,
    generated_at: datetime | None = None,
) -> ProjectStateTrace:
    return ProjectStateTrace(
        current_objective=current_objective,
        decisions=decisions,
        superseded_decisions=superseded_decisions,
        active_tasks=active_tasks,
        blockers=blockers,
        constraints=constraints,
        implementation_state=implementation_state,
        code_areas=code_areas,
        open_questions=open_questions,
        gaps=gaps,
        confidence=confidence,
        generated_at=generated_at if generated_at is not None else datetime(2024, 1, 1),
    )


# ===========================================================================
# ActivatedConcept
# ===========================================================================


class TestActivatedConcept:
    def test_valid_construction(self) -> None:
        cid = uuid4()
        ac = ActivatedConcept(
            concept_id=cid, activation_score=1.0, activation_depth=0, source_seed=cid
        )
        assert ac.concept_id == cid
        assert ac.activation_score == 1.0
        assert ac.activation_depth == 0
        assert ac.source_seed == cid

    def test_is_frozen(self) -> None:
        ac = make_activated_concept()
        with pytest.raises(dataclasses.FrozenInstanceError):
            ac.activation_score = 0.5  # type: ignore[misc]

    @pytest.mark.parametrize("score", [-0.01, 1.01, -1.0, 2.0])
    def test_rejects_activation_score_out_of_range(self, score: float) -> None:
        with pytest.raises(ValueError, match="activation_score"):
            make_activated_concept(activation_score=score)

    @pytest.mark.parametrize("score", [0.0, 0.5, 1.0])
    def test_accepts_activation_score_boundaries(self, score: float) -> None:
        ac = make_activated_concept(activation_score=score)
        assert ac.activation_score == score

    def test_rejects_negative_activation_depth(self) -> None:
        with pytest.raises(ValueError, match="activation_depth"):
            make_activated_concept(activation_depth=-1)

    def test_accepts_zero_activation_depth(self) -> None:
        ac = make_activated_concept(activation_depth=0)
        assert ac.activation_depth == 0

    def test_seed_may_differ_from_concept_id(self) -> None:
        cid, seed = uuid4(), uuid4()
        ac = ActivatedConcept(
            concept_id=cid, activation_score=0.5, activation_depth=2, source_seed=seed
        )
        assert ac.source_seed == seed
        assert ac.concept_id != ac.source_seed

    def test_serialisation_round_trip(self) -> None:
        ac = make_activated_concept(activation_score=0.42, activation_depth=3)
        restored = ActivatedConcept.from_dict(ac.to_dict())
        assert restored == ac

    def test_to_dict_uses_string_uuids(self) -> None:
        ac = make_activated_concept()
        d = ac.to_dict()
        assert isinstance(d["concept_id"], str)
        assert isinstance(d["source_seed"], str)


# ===========================================================================
# Candidate
# ===========================================================================


class TestCandidate:
    def test_valid_construction(self) -> None:
        ko = make_ko()
        ac = make_activated_concept()
        c = Candidate(
            knowledge_object=ko,
            supporting_concepts=(ac,),
            attachment_relevance=1.0,
            activation_score=1.0,
            retrieval_metadata={"pass": "direct_attachment"},
        )
        assert c.knowledge_object == ko
        assert c.supporting_concepts == (ac,)
        assert c.retrieval_metadata["pass"] == "direct_attachment"

    def test_is_frozen(self) -> None:
        c = make_candidate()
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.activation_score = 0.1  # type: ignore[misc]

    def test_accepts_empty_supporting_concepts_as_no_ontology_evidence(self) -> None:
        c = make_candidate(supporting_concepts=(), attachment_relevance=0.0, activation_score=0.0)
        assert c.supporting_concepts == ()
        assert c.has_ontology_evidence is False

    def test_has_ontology_evidence_true_with_supporting_concepts(self) -> None:
        c = make_candidate(supporting_concepts=(make_activated_concept(),))
        assert c.has_ontology_evidence is True

    def test_has_ontology_evidence_false_with_no_supporting_concepts(self) -> None:
        c = make_candidate(supporting_concepts=(), attachment_relevance=0.0, activation_score=0.0)
        assert c.has_ontology_evidence is False

    def test_supporting_concepts_coerced_to_tuple(self) -> None:
        ac = make_activated_concept()
        c = make_candidate(supporting_concepts=[ac])  # type: ignore[arg-type]
        assert isinstance(c.supporting_concepts, tuple)
        assert c.supporting_concepts == (ac,)

    @pytest.mark.parametrize("relevance", [-0.01, 1.01])
    def test_rejects_attachment_relevance_out_of_range(self, relevance: float) -> None:
        with pytest.raises(ValueError, match="attachment_relevance"):
            make_candidate(attachment_relevance=relevance)

    @pytest.mark.parametrize("score", [-0.01, 1.01])
    def test_rejects_activation_score_out_of_range(self, score: float) -> None:
        with pytest.raises(ValueError, match="activation_score"):
            make_candidate(activation_score=score)

    @pytest.mark.parametrize("score", [-0.01, 1.01])
    def test_rejects_keyword_overlap_score_out_of_range(self, score: float) -> None:
        with pytest.raises(ValueError, match="keyword_overlap_score"):
            make_candidate(keyword_overlap_score=score)

    def test_keyword_overlap_score_defaults_to_zero(self) -> None:
        c = make_candidate()
        assert c.keyword_overlap_score == 0.0

    def test_keyword_overlap_score_is_independent_of_ontology_evidence(self) -> None:
        c = make_candidate(
            supporting_concepts=(), attachment_relevance=0.0, activation_score=0.0,
            keyword_overlap_score=0.7,
        )
        assert c.has_ontology_evidence is False
        assert c.keyword_overlap_score == 0.7

    def test_retrieval_metadata_defaults_to_empty(self) -> None:
        c = make_candidate(retrieval_metadata=None)
        assert dict(c.retrieval_metadata) == {}

    def test_retrieval_metadata_is_immutable_mapping(self) -> None:
        c = make_candidate(retrieval_metadata={"k": "v"})
        assert isinstance(c.retrieval_metadata, MappingProxyType)
        with pytest.raises(TypeError):
            c.retrieval_metadata["k"] = "changed"  # type: ignore[index]

    def test_retrieval_metadata_is_defensively_copied(self) -> None:
        original = {"k": "v"}
        c = make_candidate(retrieval_metadata=original)
        original["k"] = "mutated"
        assert c.retrieval_metadata["k"] == "v"

    def test_supports_multiple_concepts(self) -> None:
        acs = (make_activated_concept(), make_activated_concept())
        c = make_candidate(supporting_concepts=acs)
        assert len(c.supporting_concepts) == 2

    def test_serialisation_round_trip(self) -> None:
        c = make_candidate(retrieval_metadata={"note": "test"}, keyword_overlap_score=0.42)
        restored = Candidate.from_dict(c.to_dict())
        assert restored.knowledge_object.id == c.knowledge_object.id
        assert restored.supporting_concepts == c.supporting_concepts
        assert restored.attachment_relevance == c.attachment_relevance
        assert restored.activation_score == c.activation_score
        assert restored.keyword_overlap_score == c.keyword_overlap_score
        assert dict(restored.retrieval_metadata) == dict(c.retrieval_metadata)


# ===========================================================================
# RankedCandidate
# ===========================================================================


class TestRankedCandidate:
    def test_valid_construction(self) -> None:
        candidate = make_candidate()
        rc = RankedCandidate(
            candidate=candidate,
            final_score=0.9,
            score_breakdown={"activation": 0.5, "importance": 0.4},
        )
        assert rc.candidate == candidate
        assert rc.final_score == 0.9

    def test_is_frozen(self) -> None:
        rc = make_ranked()
        with pytest.raises(dataclasses.FrozenInstanceError):
            rc.final_score = 0.1  # type: ignore[misc]

    @pytest.mark.parametrize("score", [-0.01, 1.01])
    def test_rejects_final_score_out_of_range(self, score: float) -> None:
        with pytest.raises(ValueError, match="final_score"):
            make_ranked(final_score=score)

    def test_rejects_empty_score_breakdown(self) -> None:
        with pytest.raises(ValueError, match="score_breakdown"):
            make_ranked(score_breakdown={})

    def test_score_breakdown_is_immutable_mapping(self) -> None:
        rc = make_ranked(score_breakdown={"activation": 0.5})
        assert isinstance(rc.score_breakdown, MappingProxyType)
        with pytest.raises(TypeError):
            rc.score_breakdown["activation"] = 1.0  # type: ignore[index]

    def test_score_breakdown_is_defensively_copied(self) -> None:
        original = {"activation": 0.5}
        rc = make_ranked(score_breakdown=original)
        original["activation"] = 0.9
        assert rc.score_breakdown["activation"] == 0.5

    def test_deterministic_ordering_by_descending_score(self) -> None:
        low = make_ranked(final_score=0.2, score_breakdown={"a": 0.2})
        high = make_ranked(final_score=0.8, score_breakdown={"a": 0.8})
        ordered = sorted([low, high])
        assert ordered == [high, low]

    def test_deterministic_tie_break_by_knowledge_object_id(self) -> None:
        ko_a = make_ko("fact A")
        ko_b = make_ko("fact B")
        # Force a known ordering regardless of random UUID generation.
        first_id, second_id = sorted([ko_a.id, ko_b.id], key=str)
        ko_first = ko_a if ko_a.id == first_id else ko_b
        ko_second = ko_b if ko_first is ko_a else ko_a

        rc_first = make_ranked(candidate=make_candidate(ko=ko_first), final_score=0.5)
        rc_second = make_ranked(candidate=make_candidate(ko=ko_second), final_score=0.5)

        ordered = sorted([rc_second, rc_first])
        assert ordered == [rc_first, rc_second]

    def test_ordering_is_stable_across_repeated_sorts(self) -> None:
        items = [make_ranked(final_score=s, score_breakdown={"a": s}) for s in [0.1, 0.9, 0.5, 0.9, 0.3]]
        first_sort = sorted(items)
        second_sort = sorted(list(reversed(items)))
        assert [r.final_score for r in first_sort] == [r.final_score for r in second_sort]

    def test_comparison_against_non_ranked_candidate_not_implemented(self) -> None:
        rc = make_ranked()
        assert rc.__lt__(object()) is NotImplemented
        assert rc.__le__(object()) is NotImplemented
        assert rc.__gt__(object()) is NotImplemented
        assert rc.__ge__(object()) is NotImplemented

    def test_serialisation_round_trip(self) -> None:
        rc = make_ranked(score_breakdown={"activation": 0.3, "importance": 0.45})
        restored = RankedCandidate.from_dict(rc.to_dict())
        assert restored.final_score == rc.final_score
        assert dict(restored.score_breakdown) == dict(rc.score_breakdown)
        assert restored.candidate.knowledge_object.id == rc.candidate.knowledge_object.id


# ===========================================================================
# CandidateTrace
# ===========================================================================


class TestCandidateTrace:
    def test_valid_construction_accepted(self) -> None:
        ct = make_candidate_trace(accepted=True, rejection_reason=None)
        assert ct.accepted is True
        assert ct.rejection_reason is None

    def test_valid_construction_rejected(self) -> None:
        ct = make_candidate_trace(
            accepted=False, rejection_reason=REJECTION_BELOW_MINIMUM_SCORE
        )
        assert ct.accepted is False
        assert ct.rejection_reason == REJECTION_BELOW_MINIMUM_SCORE

    def test_is_frozen(self) -> None:
        ct = make_candidate_trace()
        with pytest.raises(dataclasses.FrozenInstanceError):
            ct.final_score = 0.1  # type: ignore[misc]

    def test_accepted_with_rejection_reason_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="rejection_reason"):
            make_candidate_trace(
                accepted=True, rejection_reason=REJECTION_SLOT_BUDGET_EXCEEDED
            )

    def test_rejected_without_rejection_reason_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="rejection_reason"):
            make_candidate_trace(accepted=False, rejection_reason=None)

    @pytest.mark.parametrize(
        "field_name",
        [
            "activation_score",
            "attachment_relevance",
            "keyword_overlap_score",
            "importance",
            "confidence",
            "final_score",
        ],
    )
    def test_rejects_scores_out_of_range(self, field_name: str) -> None:
        with pytest.raises(ValueError, match=field_name):
            make_candidate_trace(**{field_name: 1.5})

    def test_rejects_final_rank_below_one(self) -> None:
        with pytest.raises(ValueError, match="final_rank"):
            make_candidate_trace(final_rank=0)

    def test_serialisation_round_trip(self) -> None:
        ct = make_candidate_trace(
            accepted=False, rejection_reason=REJECTION_SLOT_BUDGET_EXCEEDED, final_rank=7
        )
        restored = CandidateTrace.from_dict(ct.to_dict())
        assert restored == ct

    @pytest.mark.parametrize(
        "memory_type",
        [
            MemoryType.BLOCKER,
            MemoryType.IMPLEMENTATION_STATE,
            MemoryType.CODE_AREA,
            MemoryType.OPEN_QUESTION,
        ],
    )
    def test_new_memory_types_round_trip(self, memory_type: MemoryType) -> None:
        # See docs/architecture/PROJECT_STATE_KNOWLEDGE_MODEL.md -- these four
        # MemoryType members are new; CandidateTrace's generic
        # MemoryType(data["memory_type"]) lookup needs no code change to
        # serialize/deserialize them.
        ct = make_candidate_trace(memory_type=memory_type)
        restored = CandidateTrace.from_dict(ct.to_dict())
        assert restored == ct
        assert restored.memory_type is memory_type

    def test_to_dict_uses_string_uuid_and_enum_value(self) -> None:
        ct = make_candidate_trace()
        d = ct.to_dict()
        assert isinstance(d["knowledge_object_id"], str)
        assert d["memory_type"] == MemoryType.FACT.value

    def test_acceptance_fields_default(self) -> None:
        ct = make_candidate_trace()
        assert ct.threshold_used is None
        assert ct.score_gap is None
        assert ct.relative_score == 1.0
        assert ct.abstained is False

    @pytest.mark.parametrize(
        "reason",
        [
            REJECTION_BELOW_ABSTENTION_FLOOR,
            REJECTION_SCORE_GAP_CUT,
            REJECTION_BELOW_RELATIVE_FLOOR,
            REJECTION_ACCEPTANCE_CAP_EXCEEDED,
        ],
    )
    def test_accepts_every_acceptance_stage_rejection_reason(self, reason: str) -> None:
        ct = make_candidate_trace(
            accepted=False, rejection_reason=reason, threshold_used=0.3
        )
        assert ct.rejection_reason == reason

    def test_rejects_relative_score_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="relative_score"):
            make_candidate_trace(relative_score=1.5)

    def test_rejects_negative_score_gap(self) -> None:
        with pytest.raises(ValueError, match="score_gap"):
            make_candidate_trace(score_gap=-0.01)

    def test_abstained_requires_abstention_rejection_reason(self) -> None:
        with pytest.raises(ValueError, match="abstained"):
            make_candidate_trace(
                accepted=False,
                rejection_reason=REJECTION_BELOW_MINIMUM_SCORE,
                abstained=True,
            )

    def test_abstained_with_correct_reason_is_valid(self) -> None:
        ct = make_candidate_trace(
            accepted=False,
            rejection_reason=REJECTION_BELOW_ABSTENTION_FLOOR,
            threshold_used=0.25,
            abstained=True,
        )
        assert ct.abstained is True

    def test_serialisation_round_trip_with_acceptance_fields(self) -> None:
        ct = make_candidate_trace(
            accepted=False,
            rejection_reason=REJECTION_SCORE_GAP_CUT,
            threshold_used=0.04,
            score_gap=0.05,
            relative_score=0.8,
            abstained=False,
        )
        restored = CandidateTrace.from_dict(ct.to_dict())
        assert restored == ct

    def test_from_dict_defaults_acceptance_fields_when_absent(self) -> None:
        ct = make_candidate_trace()
        d = ct.to_dict()
        del d["threshold_used"], d["score_gap"], d["relative_score"], d["abstained"]
        restored = CandidateTrace.from_dict(d)
        assert restored.threshold_used is None
        assert restored.score_gap is None
        assert restored.relative_score == 0.0
        assert restored.abstained is False

    def test_score_breakdown_defaults_to_empty(self) -> None:
        ct = make_candidate_trace()
        assert dict(ct.score_breakdown) == {}

    def test_score_breakdown_is_stored_and_serialised(self) -> None:
        ct = CandidateTrace(
            knowledge_object_id=uuid4(),
            canonical_fact="Haven uses Claude",
            memory_type=MemoryType.FACT,
            matched_by_keyword=True,
            matched_by_ontology=True,
            activation_score=0.8,
            attachment_relevance=0.9,
            keyword_overlap_score=0.6,
            importance=0.5,
            confidence=0.5,
            final_score=0.75,
            accepted=True,
            rejection_reason=None,
            final_rank=1,
            score_breakdown={"recency": 0.12, "activation": 0.3},
        )
        assert dict(ct.score_breakdown) == {"recency": 0.12, "activation": 0.3}
        assert ct.to_dict()["score_breakdown"] == {"recency": 0.12, "activation": 0.3}

    def test_score_breakdown_is_immutable_mapping(self) -> None:
        ct = CandidateTrace(
            knowledge_object_id=uuid4(),
            canonical_fact="Haven uses Claude",
            memory_type=MemoryType.FACT,
            matched_by_keyword=True,
            matched_by_ontology=False,
            activation_score=0.0,
            attachment_relevance=0.0,
            keyword_overlap_score=0.6,
            importance=0.5,
            confidence=0.5,
            final_score=0.5,
            accepted=True,
            rejection_reason=None,
            final_rank=1,
            score_breakdown={"recency": 0.2},
        )
        assert isinstance(ct.score_breakdown, MappingProxyType)
        with pytest.raises(TypeError):
            ct.score_breakdown["recency"] = 0.9  # type: ignore[index]

    def test_serialisation_round_trip_with_score_breakdown(self) -> None:
        ct = CandidateTrace(
            knowledge_object_id=uuid4(),
            canonical_fact="Haven uses Claude",
            memory_type=MemoryType.FACT,
            matched_by_keyword=True,
            matched_by_ontology=True,
            activation_score=0.8,
            attachment_relevance=0.9,
            keyword_overlap_score=0.6,
            importance=0.5,
            confidence=0.5,
            final_score=0.75,
            accepted=True,
            rejection_reason=None,
            final_rank=1,
            score_breakdown={"recency": 0.12, "confirmation_count": 0.0},
        )
        restored = CandidateTrace.from_dict(ct.to_dict())
        assert restored == ct

    def test_from_dict_defaults_score_breakdown_when_absent(self) -> None:
        ct = make_candidate_trace()
        d = ct.to_dict()
        del d["score_breakdown"]
        restored = CandidateTrace.from_dict(d)
        assert dict(restored.score_breakdown) == {}

    # -- base_score / category_preference_bonus (Phase 3) ------------------

    def test_base_score_and_bonus_default(self) -> None:
        ct = make_candidate_trace()
        assert ct.base_score is None
        assert ct.category_preference_bonus == 0.0

    def test_base_score_and_bonus_are_stored(self) -> None:
        ct = make_candidate_trace(
            final_score=0.55, base_score=0.5, category_preference_bonus=0.05
        )
        assert ct.base_score == 0.5
        assert ct.category_preference_bonus == 0.05
        assert ct.final_score == pytest.approx(ct.base_score + ct.category_preference_bonus)

    def test_rejects_base_score_out_of_range(self) -> None:
        with pytest.raises(ValueError, match="base_score"):
            make_candidate_trace(base_score=1.5)

    def test_rejects_negative_category_preference_bonus(self) -> None:
        with pytest.raises(ValueError, match="category_preference_bonus"):
            make_candidate_trace(category_preference_bonus=-0.01)

    def test_rejects_category_preference_bonus_above_one(self) -> None:
        with pytest.raises(ValueError, match="category_preference_bonus"):
            make_candidate_trace(category_preference_bonus=1.5)

    def test_serialisation_round_trip_with_base_score_and_bonus(self) -> None:
        ct = make_candidate_trace(
            final_score=0.55, base_score=0.5, category_preference_bonus=0.05
        )
        restored = CandidateTrace.from_dict(ct.to_dict())
        assert restored == ct

    def test_from_dict_defaults_base_score_and_bonus_when_absent(self) -> None:
        # Backward compatibility: a trace serialised before Phase 3 (no
        # base_score/category_preference_bonus keys at all) must still
        # deserialise, defaulting to "no category-preference stage ran".
        ct = make_candidate_trace()
        d = ct.to_dict()
        del d["base_score"], d["category_preference_bonus"]
        restored = CandidateTrace.from_dict(d)
        assert restored.base_score is None
        assert restored.category_preference_bonus == 0.0


# ===========================================================================
# RetrievalPipelineStats
# ===========================================================================


class TestRetrievalPipelineStats:
    def test_valid_construction(self) -> None:
        stats = make_pipeline_stats()
        assert stats.total_merged_candidates == 2
        assert stats.retrieval_latency_ms == 3.5

    def test_is_frozen(self) -> None:
        stats = make_pipeline_stats()
        with pytest.raises(dataclasses.FrozenInstanceError):
            stats.total_merged_candidates = 99  # type: ignore[misc]

    @pytest.mark.parametrize(
        "field_name",
        [
            "total_ontology_candidates",
            "total_keyword_candidates",
            "total_merged_candidates",
            "total_accepted_candidates",
            "total_rejected_candidates",
            "final_context_size",
        ],
    )
    def test_rejects_negative_counts(self, field_name: str) -> None:
        with pytest.raises(ValueError, match=field_name):
            make_pipeline_stats(**{field_name: -1})

    def test_rejects_negative_latency(self) -> None:
        with pytest.raises(ValueError, match="retrieval_latency_ms"):
            make_pipeline_stats(retrieval_latency_ms=-0.1)

    def test_serialisation_round_trip(self) -> None:
        stats = make_pipeline_stats()
        restored = RetrievalPipelineStats.from_dict(stats.to_dict())
        assert restored == stats


# ===========================================================================
# RetrievalTrace
# ===========================================================================


class TestRetrievalTrace:
    def test_valid_construction(self) -> None:
        ct = make_candidate_trace()
        stats = make_pipeline_stats()
        trace = RetrievalTrace(
            query="What does Haven use?",
            rewritten_queries=("What tool does Haven use?",),
            candidates=(ct,),
            pipeline_stats=stats,
        )
        assert trace.query == "What does Haven use?"
        assert trace.rewritten_queries == ("What tool does Haven use?",)
        assert trace.candidates == (ct,)
        assert trace.pipeline_stats == stats
        assert isinstance(trace.created_at, datetime)

    def test_is_frozen(self) -> None:
        trace = RetrievalTrace(
            query="q",
            rewritten_queries=(),
            candidates=(),
            pipeline_stats=make_pipeline_stats(),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            trace.query = "changed"  # type: ignore[misc]

    def test_allows_empty_query(self) -> None:
        # MemoryEngine.query_with_trace builds a trace for every call it
        # makes, including a blank query -- MemoryEngine.query("") has
        # always returned "" rather than raising, so RetrievalTrace must
        # not introduce a new failure mode for that input.
        trace = RetrievalTrace(
            query="",
            rewritten_queries=(),
            candidates=(),
            pipeline_stats=make_pipeline_stats(),
        )
        assert trace.query == ""

    def test_allows_empty_downstream_collections(self) -> None:
        trace = RetrievalTrace(
            query="no matches",
            rewritten_queries=(),
            candidates=(),
            pipeline_stats=make_pipeline_stats(),
        )
        assert trace.candidates == ()
        assert trace.rewritten_queries == ()

    def test_collections_coerced_to_tuples(self) -> None:
        ct = make_candidate_trace()
        trace = RetrievalTrace(
            query="q",
            rewritten_queries=["a rewrite"],  # type: ignore[arg-type]
            candidates=[ct],  # type: ignore[arg-type]
            pipeline_stats=make_pipeline_stats(),
        )
        assert isinstance(trace.rewritten_queries, tuple)
        assert trace.rewritten_queries == ("a rewrite",)
        assert isinstance(trace.candidates, tuple)
        assert trace.candidates == (ct,)

    def test_created_at_defaults_to_utcnow(self) -> None:
        before = datetime.utcnow()
        trace = RetrievalTrace(
            query="q",
            rewritten_queries=(),
            candidates=(),
            pipeline_stats=make_pipeline_stats(),
        )
        after = datetime.utcnow()
        assert before <= trace.created_at <= after

    def test_serialisation_round_trip(self) -> None:
        ct = make_candidate_trace()
        stats = make_pipeline_stats()
        trace = RetrievalTrace(
            query="What does Haven use?",
            rewritten_queries=("rewrite one",),
            candidates=(ct,),
            pipeline_stats=stats,
        )
        restored = RetrievalTrace.from_dict(trace.to_dict())
        assert restored.query == trace.query
        assert restored.rewritten_queries == trace.rewritten_queries
        assert len(restored.candidates) == len(trace.candidates)
        assert restored.candidates[0] == trace.candidates[0]
        assert restored.pipeline_stats == trace.pipeline_stats
        assert restored.created_at == trace.created_at

    def test_not_part_of_public_ontology_api(self) -> None:
        """RetrievalTrace is a debug/benchmark artifact and must not be
        re-exported from obsidian.ontology's public __init__."""
        import obsidian.ontology as ontology_pkg

        assert "RetrievalTrace" not in getattr(ontology_pkg, "__all__", [])

    def test_context_plan_defaults_to_none(self) -> None:
        # A trace built the way it was before Phase 1.5 (no context_plan
        # kwarg) must still construct -- existing call sites elsewhere in
        # the codebase (e.g. hand-built traces in other test files) must
        # not break.
        trace = RetrievalTrace(
            query="q",
            rewritten_queries=(),
            candidates=(),
            pipeline_stats=make_pipeline_stats(),
        )
        assert trace.context_plan is None

    def test_context_plan_round_trips_through_serialisation(self) -> None:
        plan_trace = make_context_plan_trace(
            requirements=(make_requirement_trace(),)
        )
        trace = RetrievalTrace(
            query="continue implementing Haven",
            rewritten_queries=(),
            candidates=(),
            pipeline_stats=make_pipeline_stats(),
            context_plan=plan_trace,
        )
        restored = RetrievalTrace.from_dict(trace.to_dict())
        assert restored.context_plan == plan_trace

    def test_missing_context_plan_key_deserialises_to_none(self) -> None:
        # Backward compatibility: a RetrievalTrace serialised before this
        # field existed has no "context_plan" key at all.
        stats = make_pipeline_stats()
        data = {
            "query": "q",
            "rewritten_queries": [],
            "candidates": [],
            "pipeline_stats": stats.to_dict(),
            "created_at": datetime.utcnow().isoformat(),
        }
        restored = RetrievalTrace.from_dict(data)
        assert restored.context_plan is None

    def test_coverage_defaults_to_none(self) -> None:
        # A trace built the way it was before Phase 2 (no coverage kwarg)
        # must still construct -- existing call sites elsewhere in the
        # codebase (e.g. hand-built traces in other test files) must not
        # break.
        trace = RetrievalTrace(
            query="q",
            rewritten_queries=(),
            candidates=(),
            pipeline_stats=make_pipeline_stats(),
        )
        assert trace.coverage is None

    def test_coverage_round_trips_through_serialisation(self) -> None:
        coverage_trace = make_coverage_report_trace(
            entries=(make_category_coverage_trace(),)
        )
        trace = RetrievalTrace(
            query="continue implementing Haven",
            rewritten_queries=(),
            candidates=(),
            pipeline_stats=make_pipeline_stats(),
            coverage=coverage_trace,
        )
        restored = RetrievalTrace.from_dict(trace.to_dict())
        assert restored.coverage == coverage_trace

    def test_missing_coverage_key_deserialises_to_none(self) -> None:
        # Backward compatibility: a RetrievalTrace serialised before this
        # field existed has no "coverage" key at all.
        stats = make_pipeline_stats()
        data = {
            "query": "q",
            "rewritten_queries": [],
            "candidates": [],
            "pipeline_stats": stats.to_dict(),
            "created_at": datetime.utcnow().isoformat(),
        }
        restored = RetrievalTrace.from_dict(data)
        assert restored.coverage is None

    def test_gap_recovery_defaults_to_none(self) -> None:
        # A trace built the way it was before Phase 4 (no gap_recovery
        # kwarg) must still construct -- existing call sites elsewhere in
        # the codebase (e.g. hand-built traces in other test files) must
        # not break.
        trace = RetrievalTrace(
            query="q",
            rewritten_queries=(),
            candidates=(),
            pipeline_stats=make_pipeline_stats(),
        )
        assert trace.gap_recovery is None

    def test_gap_recovery_round_trips_through_serialisation(self) -> None:
        gap_recovery_trace = make_gap_recovery_trace(
            should_retry=True,
            missing_categories=("decision",),
            retry_budget=1,
            retry_reason="required_category_missing",
            recovery_strategy="retry_missing_categories",
        )
        trace = RetrievalTrace(
            query="continue implementing Haven",
            rewritten_queries=(),
            candidates=(),
            pipeline_stats=make_pipeline_stats(),
            gap_recovery=gap_recovery_trace,
        )
        restored = RetrievalTrace.from_dict(trace.to_dict())
        assert restored.gap_recovery == gap_recovery_trace

    def test_missing_gap_recovery_key_deserialises_to_none(self) -> None:
        # Backward compatibility: a RetrievalTrace serialised before this
        # field existed has no "gap_recovery" key at all.
        stats = make_pipeline_stats()
        data = {
            "query": "q",
            "rewritten_queries": [],
            "candidates": [],
            "pipeline_stats": stats.to_dict(),
            "created_at": datetime.utcnow().isoformat(),
        }
        restored = RetrievalTrace.from_dict(data)
        assert restored.gap_recovery is None

    def test_project_state_defaults_to_none(self) -> None:
        # A trace built the way it was before Phase A (no project_state
        # kwarg) must still construct -- existing call sites elsewhere in
        # the codebase (e.g. hand-built traces in other test files) must
        # not break.
        trace = RetrievalTrace(
            query="q",
            rewritten_queries=(),
            candidates=(),
            pipeline_stats=make_pipeline_stats(),
        )
        assert trace.project_state is None

    def test_project_state_round_trips_through_serialisation(self) -> None:
        project_state_trace = make_project_state_trace(
            current_objective=ProjectStateFieldTrace(
                value=make_state_ref_trace(canonical_fact="Ship Phase A"),
                derivation="memory_direct",
                confidence=1.0,
            ),
            decisions=(make_state_ref_trace(canonical_fact="Use JSON"),),
            gaps=("blockers", "code_areas"),
            confidence=0.75,
        )
        trace = RetrievalTrace(
            query="continue implementing Haven",
            rewritten_queries=(),
            candidates=(),
            pipeline_stats=make_pipeline_stats(),
            project_state=project_state_trace,
        )
        restored = RetrievalTrace.from_dict(trace.to_dict())
        assert restored.project_state == project_state_trace

    def test_missing_project_state_key_deserialises_to_none(self) -> None:
        # Backward compatibility: a RetrievalTrace serialised before this
        # field existed has no "project_state" key at all.
        stats = make_pipeline_stats()
        data = {
            "query": "q",
            "rewritten_queries": [],
            "candidates": [],
            "pipeline_stats": stats.to_dict(),
            "created_at": datetime.utcnow().isoformat(),
        }
        restored = RetrievalTrace.from_dict(data)
        assert restored.project_state is None


# ===========================================================================
# ContextCategoryRequirementTrace
# ===========================================================================


class TestContextCategoryRequirementTrace:
    def test_valid_construction(self) -> None:
        req = make_requirement_trace(
            category="constraint",
            necessity="required",
            min_count=2,
            max_count=5,
            priority_tier="never_drop",
        )
        assert req.category == "constraint"
        assert req.necessity == "required"
        assert req.min_count == 2
        assert req.max_count == 5
        assert req.priority_tier == "never_drop"

    def test_is_frozen(self) -> None:
        req = make_requirement_trace()
        with pytest.raises(dataclasses.FrozenInstanceError):
            req.category = "task"  # type: ignore[misc]

    def test_serialisation_round_trip(self) -> None:
        req = make_requirement_trace(max_count=3)
        restored = ContextCategoryRequirementTrace.from_dict(req.to_dict())
        assert restored == req

    def test_serialisation_round_trip_with_no_max_count(self) -> None:
        req = make_requirement_trace(max_count=None)
        restored = ContextCategoryRequirementTrace.from_dict(req.to_dict())
        assert restored.max_count is None


# ===========================================================================
# ContextPlanTrace
# ===========================================================================


class TestContextPlanTrace:
    def test_valid_construction(self) -> None:
        req = make_requirement_trace()
        plan = make_context_plan_trace(
            task_mode="continuation",
            planning_method="deterministic",
            confidence=1.0,
            requirements=(req,),
        )
        assert plan.task_mode == "continuation"
        assert plan.planning_method == "deterministic"
        assert plan.scope_concept_id is None
        assert plan.confidence == 1.0
        assert plan.requirements == (req,)

    def test_is_frozen(self) -> None:
        plan = make_context_plan_trace()
        with pytest.raises(dataclasses.FrozenInstanceError):
            plan.task_mode = "research"  # type: ignore[misc]

    def test_requirements_coerced_to_tuple(self) -> None:
        req = make_requirement_trace()
        plan = ContextPlanTrace(
            task_mode="coding_debugging",
            planning_method="deterministic",
            scope_concept_id=None,
            confidence=1.0,
            requirements=[req],  # type: ignore[arg-type]
        )
        assert isinstance(plan.requirements, tuple)
        assert plan.requirements == (req,)

    def test_allows_empty_requirements(self) -> None:
        # The sentinel meaning "no plan needed" (TaskMode.POINTED_QA) has an
        # empty requirements tuple.
        plan = make_context_plan_trace(task_mode="pointed_qa", requirements=())
        assert plan.requirements == ()

    @pytest.mark.parametrize("confidence", [-0.1, 1.1])
    def test_rejects_out_of_range_confidence(self, confidence: float) -> None:
        with pytest.raises(ValueError, match="confidence"):
            make_context_plan_trace(confidence=confidence)

    def test_serialisation_round_trip(self) -> None:
        req = make_requirement_trace(category="task", max_count=2)
        plan = make_context_plan_trace(
            scope_concept_id=uuid4(), requirements=(req,)
        )
        restored = ContextPlanTrace.from_dict(plan.to_dict())
        assert restored == plan

    def test_serialisation_round_trip_with_no_scope(self) -> None:
        plan = make_context_plan_trace(scope_concept_id=None)
        restored = ContextPlanTrace.from_dict(plan.to_dict())
        assert restored.scope_concept_id is None

    def test_to_dict_serialises_scope_concept_id_as_str(self) -> None:
        scope_id = uuid4()
        plan = make_context_plan_trace(scope_concept_id=scope_id)
        assert plan.to_dict()["scope_concept_id"] == str(scope_id)


# ===========================================================================
# CategoryCoverageTrace
# ===========================================================================


class TestCategoryCoverageTrace:
    def test_valid_construction(self) -> None:
        entry = make_category_coverage_trace(
            category="constraint",
            necessity="required",
            required_minimum=2,
            retrieved_count=1,
            satisfied=False,
            status="partial",
        )
        assert entry.category == "constraint"
        assert entry.necessity == "required"
        assert entry.required_minimum == 2
        assert entry.retrieved_count == 1
        assert entry.satisfied is False
        assert entry.status == "partial"

    def test_is_frozen(self) -> None:
        entry = make_category_coverage_trace()
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.retrieved_count = 9  # type: ignore[misc]

    def test_serialisation_round_trip(self) -> None:
        entry = make_category_coverage_trace(category="task", retrieved_count=0, satisfied=False, status="missing")
        restored = CategoryCoverageTrace.from_dict(entry.to_dict())
        assert restored == entry


# ===========================================================================
# CoverageReportTrace
# ===========================================================================


class TestCoverageReportTrace:
    def test_valid_construction(self) -> None:
        entry = make_category_coverage_trace()
        report = make_coverage_report_trace(
            entries=(entry,),
            overall_coverage_percentage=100.0,
            missing_required_categories=(),
            fully_satisfied=True,
        )
        assert report.entries == (entry,)
        assert report.overall_coverage_percentage == 100.0
        assert report.missing_required_categories == ()
        assert report.fully_satisfied is True

    def test_is_frozen(self) -> None:
        report = make_coverage_report_trace()
        with pytest.raises(dataclasses.FrozenInstanceError):
            report.fully_satisfied = False  # type: ignore[misc]

    def test_entries_coerced_to_tuple(self) -> None:
        entry = make_category_coverage_trace()
        report = CoverageReportTrace(
            entries=[entry],  # type: ignore[arg-type]
            overall_coverage_percentage=100.0,
            missing_required_categories=[],  # type: ignore[arg-type]
            fully_satisfied=True,
        )
        assert isinstance(report.entries, tuple)
        assert report.entries == (entry,)

    def test_missing_required_categories_coerced_to_tuple(self) -> None:
        report = CoverageReportTrace(
            missing_required_categories=["blocker"],  # type: ignore[arg-type]
            fully_satisfied=False,
        )
        assert isinstance(report.missing_required_categories, tuple)
        assert report.missing_required_categories == ("blocker",)

    def test_allows_empty_entries(self) -> None:
        # The TaskMode.POINTED_QA sentinel plan requests nothing, so its
        # coverage report has no entries at all.
        report = make_coverage_report_trace(entries=())
        assert report.entries == ()

    def test_serialisation_round_trip(self) -> None:
        entry = make_category_coverage_trace(
            category="blocker", retrieved_count=0, satisfied=False, status="missing"
        )
        report = make_coverage_report_trace(
            entries=(entry,),
            overall_coverage_percentage=0.0,
            missing_required_categories=("blocker",),
            fully_satisfied=False,
        )
        restored = CoverageReportTrace.from_dict(report.to_dict())
        assert restored == report

    def test_from_dict_applies_defaults_for_missing_optional_keys(self) -> None:
        restored = CoverageReportTrace.from_dict({})
        assert restored.entries == ()
        assert restored.overall_coverage_percentage == 100.0
        assert restored.missing_required_categories == ()
        assert restored.fully_satisfied is True


# ===========================================================================
# GapRecoveryTrace
# ===========================================================================


class TestGapRecoveryTrace:
    def test_valid_construction(self) -> None:
        trace = make_gap_recovery_trace(
            should_retry=True,
            missing_categories=("blocker", "constraint"),
            retry_budget=1,
            retry_reason="required_category_missing",
            confidence=1.0,
            recovery_strategy="retry_missing_categories",
        )
        assert trace.should_retry is True
        assert trace.missing_categories == ("blocker", "constraint")
        assert trace.retry_budget == 1
        assert trace.retry_reason == "required_category_missing"
        assert trace.confidence == 1.0
        assert trace.recovery_strategy == "retry_missing_categories"

    def test_defaults_are_the_no_retry_shape(self) -> None:
        trace = GapRecoveryTrace(should_retry=False)
        assert trace.missing_categories == ()
        assert trace.retry_budget == 0
        assert trace.retry_reason == "no_gap"
        assert trace.confidence == 1.0
        assert trace.recovery_strategy == "none"

    def test_is_frozen(self) -> None:
        trace = make_gap_recovery_trace()
        with pytest.raises(dataclasses.FrozenInstanceError):
            trace.should_retry = True  # type: ignore[misc]

    def test_missing_categories_coerced_to_tuple(self) -> None:
        trace = GapRecoveryTrace(
            should_retry=False,
            missing_categories=["decision"],  # type: ignore[arg-type]
        )
        assert isinstance(trace.missing_categories, tuple)
        assert trace.missing_categories == ("decision",)

    def test_serialisation_round_trip(self) -> None:
        trace = make_gap_recovery_trace(
            should_retry=True,
            missing_categories=("open_question",),
            retry_budget=1,
            retry_reason="required_category_missing",
            recovery_strategy="retry_missing_categories",
        )
        restored = GapRecoveryTrace.from_dict(trace.to_dict())
        assert restored == trace

    def test_from_dict_applies_defaults_for_missing_optional_keys(self) -> None:
        restored = GapRecoveryTrace.from_dict({"should_retry": False})
        assert restored.missing_categories == ()
        assert restored.retry_budget == 0
        assert restored.retry_reason == "no_gap"
        assert restored.confidence == 1.0
        assert restored.recovery_strategy == "none"


# ===========================================================================
# StateRefTrace
# ===========================================================================


class TestStateRefTrace:
    def test_valid_construction(self) -> None:
        ref = make_state_ref_trace(canonical_fact="Ship Phase A", confidence=0.8, importance=0.6)
        assert ref.canonical_fact == "Ship Phase A"
        assert ref.confidence == 0.8
        assert ref.importance == 0.6

    def test_is_frozen(self) -> None:
        ref = make_state_ref_trace()
        with pytest.raises(dataclasses.FrozenInstanceError):
            ref.canonical_fact = "changed"  # type: ignore[misc]

    def test_serialisation_round_trip(self) -> None:
        ref = make_state_ref_trace(canonical_fact="Use JSON sidecars", confidence=0.9)
        restored = StateRefTrace.from_dict(ref.to_dict())
        assert restored == ref


# ===========================================================================
# ProjectStateFieldTrace
# ===========================================================================


class TestProjectStateFieldTrace:
    def test_valid_construction(self) -> None:
        field_trace = ProjectStateFieldTrace(
            value=make_state_ref_trace(canonical_fact="Ship Phase A"),
            derivation="memory_direct",
            confidence=1.0,
        )
        assert field_trace.derivation == "memory_direct"
        assert field_trace.confidence == 1.0
        assert field_trace.value.canonical_fact == "Ship Phase A"

    def test_is_frozen(self) -> None:
        field_trace = ProjectStateFieldTrace(
            value=make_state_ref_trace(), derivation="memory_direct", confidence=1.0
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            field_trace.derivation = "inferred"  # type: ignore[misc]

    def test_serialisation_round_trip(self) -> None:
        field_trace = ProjectStateFieldTrace(
            value=make_state_ref_trace(canonical_fact="Ship Phase A"),
            derivation="memory_direct",
            confidence=1.0,
        )
        restored = ProjectStateFieldTrace.from_dict(field_trace.to_dict())
        assert restored == field_trace


# ===========================================================================
# ProjectStateTrace
# ===========================================================================


class TestProjectStateTrace:
    def test_defaults_are_the_empty_shape(self) -> None:
        trace = ProjectStateTrace()
        assert trace.current_objective is None
        assert trace.decisions == ()
        assert trace.superseded_decisions == ()
        assert trace.active_tasks == ()
        assert trace.blockers == ()
        assert trace.constraints == ()
        assert trace.implementation_state == ()
        assert trace.code_areas == ()
        assert trace.open_questions == ()
        assert trace.gaps == ()
        assert trace.confidence == 1.0

    def test_is_frozen(self) -> None:
        trace = make_project_state_trace()
        with pytest.raises(dataclasses.FrozenInstanceError):
            trace.confidence = 0.5  # type: ignore[misc]

    def test_list_fields_coerced_to_tuple(self) -> None:
        trace = ProjectStateTrace(
            decisions=[make_state_ref_trace()],  # type: ignore[arg-type]
            gaps=["blockers"],  # type: ignore[arg-type]
        )
        assert isinstance(trace.decisions, tuple)
        assert isinstance(trace.gaps, tuple)

    def test_serialisation_round_trip_with_current_objective(self) -> None:
        trace = make_project_state_trace(
            current_objective=ProjectStateFieldTrace(
                value=make_state_ref_trace(canonical_fact="Ship Phase A"),
                derivation="memory_direct",
                confidence=1.0,
            ),
            decisions=(make_state_ref_trace(canonical_fact="Use JSON"),),
            blockers=(make_state_ref_trace(canonical_fact="Waiting on review"),),
            gaps=("code_areas",),
            confidence=0.875,
        )
        restored = ProjectStateTrace.from_dict(trace.to_dict())
        assert restored == trace

    def test_serialisation_round_trip_with_no_current_objective(self) -> None:
        trace = make_project_state_trace(gaps=(
            "current_objective",
            "decisions",
            "active_tasks",
            "blockers",
            "constraints",
            "implementation_state",
            "code_areas",
            "open_questions",
        ), confidence=0.0)
        restored = ProjectStateTrace.from_dict(trace.to_dict())
        assert restored == trace
        assert restored.current_objective is None

    def test_from_dict_applies_defaults_for_missing_optional_keys(self) -> None:
        restored = ProjectStateTrace.from_dict({})
        assert restored.current_objective is None
        assert restored.decisions == ()
        assert restored.gaps == ()
        assert restored.confidence == 1.0
