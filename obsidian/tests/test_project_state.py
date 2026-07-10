"""Unit tests for obsidian.memory_engine.project_state (Phase A only).

Test groups
-----------
TestStateRef                    — validation, serialisation.
TestProjectStateField           — validation, serialisation-shape via
                                    ProjectState.to_dict.
TestProjectStateValidation      — confidence bound checks.
TestProjectStateImmutability    — frozen, tuple coercion.
TestProjectStateSerialization   — to_dict/from_dict round-trips, with and
                                    without current_objective.
TestProjectStateBuilderCategoryResolution
                                — each MemoryType lands in the right
                                    ProjectState field, unmapped types are
                                    dropped, order is preserved.
TestProjectStateBuilderCurrentObjective
                                — top-ranked GOAL wins deterministically, no
                                    tie-break synthesis; None when absent.
TestProjectStateBuilderDecisionSplit
                                — DecisionMetadata.status routes DECISION
                                    candidates into decisions vs.
                                    superseded_decisions; missing metadata
                                    defaults to "current".
TestProjectStateBuilderGapsAndConfidence
                                — gaps lists exactly the empty tracked
                                    fields; confidence is the completeness
                                    fraction.
TestProjectStateBuilderDeterminism
                                — repeated calls with the same input (and
                                    shuffled input) produce an equal
                                    ProjectState.
TestProjectStateBuilderObservationalOnly
                                — the builder never mutates its input and
                                    never touches anything beyond the
                                    allocated list + now.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

import pytest

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import (
    DecisionMetadata,
    DecisionStatus,
    KnowledgeObject,
    with_decision_metadata,
)
from obsidian.memory_engine.project_state import (
    PROJECT_STATE_FIELD_NAMES,
    FieldDerivation,
    ProjectState,
    ProjectStateBuilder,
    ProjectStateField,
    StateRef,
)
from obsidian.ontology.retrieval_models import Candidate, RankedCandidate

FIXED_NOW = datetime(2026, 7, 9, 12, 0, 0)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def make_ko(
    fact: str = "a fact",
    memory_type: MemoryType = MemoryType.FACT,
    *,
    confidence: float = 0.5,
    importance: float = 0.5,
    valid_from: Optional[datetime] = None,
) -> KnowledgeObject:
    return KnowledgeObject(
        canonical_fact=fact,
        memory_type=memory_type,
        confidence=confidence,
        importance=importance,
        valid_from=valid_from if valid_from is not None else FIXED_NOW,
    )


def make_ranked(ko: KnowledgeObject, final_score: float = 0.5) -> RankedCandidate:
    candidate = Candidate(
        knowledge_object=ko,
        supporting_concepts=(),
        attachment_relevance=0.0,
        activation_score=0.0,
    )
    return RankedCandidate(
        candidate=candidate,
        final_score=final_score,
        score_breakdown={"importance": final_score},
    )


def make_state_ref(canonical_fact: str = "a fact") -> StateRef:
    return StateRef(
        knowledge_object_id=uuid4(),
        canonical_fact=canonical_fact,
        valid_from=FIXED_NOW,
        confidence=0.5,
        importance=0.5,
    )


def empty_state(**overrides) -> ProjectState:
    fields = dict(
        current_objective=None,
        decisions=(),
        superseded_decisions=(),
        active_tasks=(),
        blockers=(),
        constraints=(),
        implementation_state=(),
        code_areas=(),
        open_questions=(),
        gaps=PROJECT_STATE_FIELD_NAMES,
        confidence=0.0,
        generated_at=FIXED_NOW,
    )
    fields.update(overrides)
    return ProjectState(**fields)


# ===========================================================================
# StateRef
# ===========================================================================


class TestStateRef:
    def test_valid_construction(self) -> None:
        ref = make_state_ref("Ship Phase A")
        assert ref.canonical_fact == "Ship Phase A"

    def test_rejects_confidence_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            StateRef(
                knowledge_object_id=uuid4(),
                canonical_fact="x",
                valid_from=FIXED_NOW,
                confidence=1.5,
                importance=0.5,
            )

    def test_rejects_importance_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            StateRef(
                knowledge_object_id=uuid4(),
                canonical_fact="x",
                valid_from=FIXED_NOW,
                confidence=0.5,
                importance=-0.1,
            )

    def test_is_frozen(self) -> None:
        ref = make_state_ref()
        with pytest.raises(dataclasses.FrozenInstanceError):
            ref.canonical_fact = "changed"  # type: ignore[misc]

    def test_from_knowledge_object_is_verbatim(self) -> None:
        ko = make_ko("Haven uses Claude", confidence=0.8, importance=0.6)
        ref = StateRef.from_knowledge_object(ko)
        assert ref.knowledge_object_id == ko.id
        assert ref.canonical_fact == ko.canonical_fact
        assert ref.valid_from == ko.valid_from
        assert ref.confidence == ko.confidence
        assert ref.importance == ko.importance

    def test_serialisation_round_trip(self) -> None:
        ref = make_state_ref("Use JSON sidecars")
        restored = StateRef.from_dict(ref.to_dict())
        assert restored == ref


# ===========================================================================
# ProjectStateField
# ===========================================================================


class TestProjectStateField:
    def test_valid_construction(self) -> None:
        ref = make_state_ref()
        field = ProjectStateField(
            value=ref,
            derivation=FieldDerivation.MEMORY_DIRECT,
            source_ids=(ref.knowledge_object_id,),
            confidence=1.0,
            last_updated=FIXED_NOW,
        )
        assert field.value is ref
        assert field.derivation is FieldDerivation.MEMORY_DIRECT

    def test_rejects_confidence_out_of_range(self) -> None:
        ref = make_state_ref()
        with pytest.raises(ValueError):
            ProjectStateField(
                value=ref,
                derivation=FieldDerivation.MEMORY_DIRECT,
                source_ids=(),
                confidence=2.0,
                last_updated=FIXED_NOW,
            )

    def test_source_ids_coerced_to_tuple(self) -> None:
        ref = make_state_ref()
        field = ProjectStateField(
            value=ref,
            derivation=FieldDerivation.MEMORY_DIRECT,
            source_ids=[ref.knowledge_object_id],  # type: ignore[arg-type]
            confidence=1.0,
            last_updated=FIXED_NOW,
        )
        assert isinstance(field.source_ids, tuple)

    def test_is_frozen(self) -> None:
        ref = make_state_ref()
        field = ProjectStateField(
            value=ref,
            derivation=FieldDerivation.MEMORY_DIRECT,
            source_ids=(),
            confidence=1.0,
            last_updated=FIXED_NOW,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            field.confidence = 0.5  # type: ignore[misc]


# ===========================================================================
# ProjectState — validation
# ===========================================================================


class TestProjectStateValidation:
    def test_rejects_confidence_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            empty_state(confidence=1.5)

    def test_accepts_boundary_confidences(self) -> None:
        assert empty_state(confidence=0.0).confidence == 0.0
        assert empty_state(confidence=1.0).confidence == 1.0


# ===========================================================================
# ProjectState — immutability
# ===========================================================================


class TestProjectStateImmutability:
    def test_is_frozen(self) -> None:
        state = empty_state()
        with pytest.raises(dataclasses.FrozenInstanceError):
            state.confidence = 0.5  # type: ignore[misc]

    def test_list_fields_coerced_to_tuple(self) -> None:
        state = empty_state(
            decisions=[make_state_ref()],  # type: ignore[arg-type]
            gaps=["blockers"],  # type: ignore[arg-type]
        )
        assert isinstance(state.decisions, tuple)
        assert isinstance(state.gaps, tuple)


# ===========================================================================
# ProjectState — serialisation
# ===========================================================================


class TestProjectStateSerialization:
    def test_round_trip_with_no_current_objective(self) -> None:
        state = empty_state()
        restored = ProjectState.from_dict(state.to_dict())
        assert restored == state
        assert restored.current_objective is None

    def test_round_trip_with_current_objective(self) -> None:
        ref = make_state_ref("Ship Phase A")
        field = ProjectStateField(
            value=ref,
            derivation=FieldDerivation.MEMORY_DIRECT,
            source_ids=(ref.knowledge_object_id,),
            confidence=1.0,
            last_updated=FIXED_NOW,
        )
        state = empty_state(
            current_objective=field,
            decisions=(make_state_ref("Use JSON"),),
            gaps=(
                "active_tasks",
                "blockers",
                "constraints",
                "implementation_state",
                "code_areas",
                "open_questions",
            ),
            confidence=0.25,
        )
        restored = ProjectState.from_dict(state.to_dict())
        assert restored == state
        assert restored.current_objective.value == ref


# ===========================================================================
# ProjectStateBuilder — category resolution
# ===========================================================================


class TestProjectStateBuilderCategoryResolution:
    def _build_single(self, ko: KnowledgeObject) -> ProjectState:
        builder = ProjectStateBuilder()
        return builder.build([make_ranked(ko)], now=FIXED_NOW)

    def test_task_lands_in_active_tasks(self) -> None:
        ko = make_ko("Write tests", memory_type=MemoryType.TASK)
        state = self._build_single(ko)
        assert [r.canonical_fact for r in state.active_tasks] == ["Write tests"]
        assert state.blockers == ()

    def test_blocker_lands_in_blockers(self) -> None:
        ko = make_ko("Waiting on review", memory_type=MemoryType.BLOCKER)
        state = self._build_single(ko)
        assert [r.canonical_fact for r in state.blockers] == ["Waiting on review"]

    def test_rule_lands_in_constraints(self) -> None:
        ko = make_ko("Never skip hooks", memory_type=MemoryType.RULE)
        state = self._build_single(ko)
        assert [r.canonical_fact for r in state.constraints] == ["Never skip hooks"]

    def test_implementation_state_lands_in_implementation_state(self) -> None:
        ko = make_ko("Retrieval pipeline is done", memory_type=MemoryType.IMPLEMENTATION_STATE)
        state = self._build_single(ko)
        assert [r.canonical_fact for r in state.implementation_state] == [
            "Retrieval pipeline is done"
        ]

    def test_code_area_lands_in_code_areas(self) -> None:
        ko = make_ko("obsidian/memory_engine/engine.py", memory_type=MemoryType.CODE_AREA)
        state = self._build_single(ko)
        assert [r.canonical_fact for r in state.code_areas] == [
            "obsidian/memory_engine/engine.py"
        ]

    def test_open_question_lands_in_open_questions(self) -> None:
        ko = make_ko("Should we persist state?", memory_type=MemoryType.OPEN_QUESTION)
        state = self._build_single(ko)
        assert [r.canonical_fact for r in state.open_questions] == [
            "Should we persist state?"
        ]

    def test_unmapped_memory_type_is_dropped(self) -> None:
        # FACT resolves to ContextCategory.RESEARCH, which has no
        # ProjectState field in this phase -- it must be silently excluded,
        # not raise or land in some default bucket.
        ko = make_ko("Haven uses Claude", memory_type=MemoryType.FACT)
        state = self._build_single(ko)
        assert state.active_tasks == ()
        assert state.blockers == ()
        assert state.constraints == ()
        assert state.implementation_state == ()
        assert state.code_areas == ()
        assert state.open_questions == ()
        assert state.decisions == ()
        assert state.current_objective is None

    def test_belief_memory_type_is_dropped(self) -> None:
        ko = make_ko("Determinism matters", memory_type=MemoryType.BELIEF)
        state = self._build_single(ko)
        for field_name in PROJECT_STATE_FIELD_NAMES:
            value = getattr(state, field_name)
            assert value in ((), None)

    def test_order_preserved_within_a_category(self) -> None:
        ko_high = make_ko("High priority task", memory_type=MemoryType.TASK)
        ko_low = make_ko("Low priority task", memory_type=MemoryType.TASK)
        builder = ProjectStateBuilder()
        # Deliberately pass the lower-scoring candidate first to prove the
        # builder re-sorts rather than trusting input order.
        state = builder.build(
            [make_ranked(ko_low, final_score=0.2), make_ranked(ko_high, final_score=0.9)],
            now=FIXED_NOW,
        )
        assert [r.canonical_fact for r in state.active_tasks] == [
            "High priority task",
            "Low priority task",
        ]

    def test_empty_allocated_list_yields_fully_empty_state(self) -> None:
        builder = ProjectStateBuilder()
        state = builder.build([], now=FIXED_NOW)
        assert state == empty_state()


# ===========================================================================
# ProjectStateBuilder — current_objective
# ===========================================================================


class TestProjectStateBuilderCurrentObjective:
    def test_single_goal_becomes_current_objective(self) -> None:
        ko = make_ko("Ship Phase A", memory_type=MemoryType.GOAL)
        builder = ProjectStateBuilder()
        state = builder.build([make_ranked(ko)], now=FIXED_NOW)

        assert state.current_objective is not None
        assert state.current_objective.value.canonical_fact == "Ship Phase A"
        assert state.current_objective.derivation is FieldDerivation.MEMORY_DIRECT
        assert state.current_objective.confidence == 1.0
        assert state.current_objective.source_ids == (ko.id,)

    def test_no_goal_yields_none(self) -> None:
        ko = make_ko("Write tests", memory_type=MemoryType.TASK)
        builder = ProjectStateBuilder()
        state = builder.build([make_ranked(ko)], now=FIXED_NOW)
        assert state.current_objective is None

    def test_multiple_goals_pick_the_top_ranked_deterministically(self) -> None:
        # Design explicitly defers LLM tie-break synthesis to a later phase
        # (see project_state.py's module docstring, "No inference"); this
        # phase always takes the highest-scoring GOAL, never synthesizes a
        # choice among competing ones.
        weak = make_ko("Secondary goal", memory_type=MemoryType.GOAL)
        strong = make_ko("Primary goal", memory_type=MemoryType.GOAL)
        builder = ProjectStateBuilder()
        state = builder.build(
            [make_ranked(weak, final_score=0.3), make_ranked(strong, final_score=0.95)],
            now=FIXED_NOW,
        )
        assert state.current_objective.value.canonical_fact == "Primary goal"


# ===========================================================================
# ProjectStateBuilder — decision split
# ===========================================================================


class TestProjectStateBuilderDecisionSplit:
    def test_decision_with_no_metadata_is_current(self) -> None:
        ko = make_ko("Use JSON sidecars", memory_type=MemoryType.DECISION)
        builder = ProjectStateBuilder()
        state = builder.build([make_ranked(ko)], now=FIXED_NOW)
        assert [r.canonical_fact for r in state.decisions] == ["Use JSON sidecars"]
        assert state.superseded_decisions == ()

    def test_active_decision_status_is_current(self) -> None:
        ko = with_decision_metadata(
            make_ko("Use JSON sidecars", memory_type=MemoryType.DECISION),
            DecisionMetadata(status=DecisionStatus.ACTIVE),
        )
        builder = ProjectStateBuilder()
        state = builder.build([make_ranked(ko)], now=FIXED_NOW)
        assert [r.canonical_fact for r in state.decisions] == ["Use JSON sidecars"]
        assert state.superseded_decisions == ()

    def test_superseded_decision_status_routes_to_superseded_bucket(self) -> None:
        ko = with_decision_metadata(
            make_ko("Use YAML sidecars", memory_type=MemoryType.DECISION),
            DecisionMetadata(status=DecisionStatus.SUPERSEDED),
        )
        builder = ProjectStateBuilder()
        state = builder.build([make_ranked(ko)], now=FIXED_NOW)
        assert state.decisions == ()
        assert [r.canonical_fact for r in state.superseded_decisions] == [
            "Use YAML sidecars"
        ]

    def test_reversed_decision_status_is_current(self) -> None:
        # REVERSED is neither ACTIVE nor SUPERSEDED; the design's two-bucket
        # split (current vs. superseded) routes anything that is not
        # explicitly SUPERSEDED into the "current" bucket rather than
        # inventing a third bucket this phase's field list doesn't have.
        ko = with_decision_metadata(
            make_ko("Use YAML sidecars", memory_type=MemoryType.DECISION),
            DecisionMetadata(status=DecisionStatus.REVERSED),
        )
        builder = ProjectStateBuilder()
        state = builder.build([make_ranked(ko)], now=FIXED_NOW)
        assert [r.canonical_fact for r in state.decisions] == ["Use YAML sidecars"]
        assert state.superseded_decisions == ()


# ===========================================================================
# ProjectStateBuilder — gaps and confidence
# ===========================================================================


class TestProjectStateBuilderGapsAndConfidence:
    def test_fully_empty_state_has_every_gap_and_zero_confidence(self) -> None:
        builder = ProjectStateBuilder()
        state = builder.build([], now=FIXED_NOW)
        assert set(state.gaps) == set(PROJECT_STATE_FIELD_NAMES)
        assert state.confidence == 0.0

    def test_fully_populated_state_has_no_gaps_and_full_confidence(self) -> None:
        kos = [
            make_ko("Ship Phase A", memory_type=MemoryType.GOAL),
            make_ko("Use JSON sidecars", memory_type=MemoryType.DECISION),
            make_ko("Write tests", memory_type=MemoryType.TASK),
            make_ko("Waiting on review", memory_type=MemoryType.BLOCKER),
            make_ko("Never skip hooks", memory_type=MemoryType.RULE),
            make_ko("Retrieval is done", memory_type=MemoryType.IMPLEMENTATION_STATE),
            make_ko("engine.py", memory_type=MemoryType.CODE_AREA),
            make_ko("Persist state?", memory_type=MemoryType.OPEN_QUESTION),
        ]
        builder = ProjectStateBuilder()
        state = builder.build([make_ranked(ko) for ko in kos], now=FIXED_NOW)
        assert state.gaps == ()
        assert state.confidence == 1.0

    def test_partial_state_reports_exactly_the_empty_fields(self) -> None:
        kos = [
            make_ko("Ship Phase A", memory_type=MemoryType.GOAL),
            make_ko("Write tests", memory_type=MemoryType.TASK),
        ]
        builder = ProjectStateBuilder()
        state = builder.build([make_ranked(ko) for ko in kos], now=FIXED_NOW)
        assert set(state.gaps) == {
            "decisions",
            "blockers",
            "constraints",
            "implementation_state",
            "code_areas",
            "open_questions",
        }
        assert state.confidence == pytest.approx(2 / 8)

    def test_gaps_only_contains_known_field_names(self) -> None:
        builder = ProjectStateBuilder()
        state = builder.build([], now=FIXED_NOW)
        assert set(state.gaps).issubset(set(PROJECT_STATE_FIELD_NAMES))


# ===========================================================================
# ProjectStateBuilder — determinism
# ===========================================================================


class TestProjectStateBuilderDeterminism:
    def _sample_candidates(self):
        return [
            make_ranked(
                make_ko("Ship Phase A", memory_type=MemoryType.GOAL), final_score=0.9
            ),
            make_ranked(
                make_ko("Use JSON sidecars", memory_type=MemoryType.DECISION),
                final_score=0.8,
            ),
            make_ranked(
                make_ko("Write tests", memory_type=MemoryType.TASK), final_score=0.7
            ),
        ]

    def test_repeated_calls_produce_equal_state(self) -> None:
        builder = ProjectStateBuilder()
        candidates = self._sample_candidates()
        first = builder.build(candidates, now=FIXED_NOW)
        for _ in range(5):
            assert builder.build(candidates, now=FIXED_NOW) == first

    def test_shuffled_input_order_produces_equal_state(self) -> None:
        builder = ProjectStateBuilder()
        candidates = self._sample_candidates()
        forward = builder.build(candidates, now=FIXED_NOW)
        backward = builder.build(list(reversed(candidates)), now=FIXED_NOW)
        assert forward == backward

    def test_default_now_is_used_when_omitted(self) -> None:
        builder = ProjectStateBuilder()
        before = datetime.utcnow()
        state = builder.build([], now=None)
        after = datetime.utcnow()
        assert before <= state.generated_at <= after + timedelta(seconds=1)


# ===========================================================================
# ProjectStateBuilder — observational only
# ===========================================================================


class TestProjectStateBuilderObservationalOnly:
    def test_input_list_is_not_mutated(self) -> None:
        candidates = [
            make_ranked(make_ko("Write tests", memory_type=MemoryType.TASK)),
            make_ranked(make_ko("Ship Phase A", memory_type=MemoryType.GOAL)),
        ]
        original = list(candidates)
        builder = ProjectStateBuilder()
        builder.build(candidates, now=FIXED_NOW)
        assert candidates == original

    def test_result_never_shares_a_ranked_candidate_object(self) -> None:
        # ProjectState must never embed a transient RankedCandidate/Candidate
        # -- only StateRef/ProjectStateField -- per the module's own design
        # note. A structural check that nothing on the returned object *is*
        # a RankedCandidate/Candidate instance.
        ko = make_ko("Ship Phase A", memory_type=MemoryType.GOAL)
        ranked = make_ranked(ko)
        builder = ProjectStateBuilder()
        state = builder.build([ranked], now=FIXED_NOW)

        assert not isinstance(state.current_objective.value, (RankedCandidate, Candidate))
        for field_name in dataclasses.fields(ProjectState):
            value = getattr(state, field_name.name)
            assert not isinstance(value, (RankedCandidate, Candidate))
