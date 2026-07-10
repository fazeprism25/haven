"""Unit tests for the Working Context prompt models in
``obsidian.ontology.retrieval_models``.

Test groups
-----------
TestResolveRole                 — MemoryType -> MemoryRole mapping is total and
                                   deterministic; metadata["role"] override wins;
                                   bad overrides fall back to the type mapping.
TestRoleBucket                   — construction, member tuple-ification, and
                                   to_dict/from_dict round-trip.
TestWorkingContextStateFromBuckets — deterministic status rule, current goal,
                                   recency ordering of decisions, and top_k caps.
TestWorkingContextStateSerialisation — to_dict/from_dict round-trip incl. None
                                   current_goal.
TestWorkingContextSerialisation  — to_dict/from_dict round-trip incl. optional
                                   anchor/member concept ids.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.ontology.retrieval_models import (
    Candidate,
    ContextKind,
    ContextStatus,
    MemoryRole,
    RankedCandidate,
    RoleBucket,
    WorkingContext,
    WorkingContextState,
    resolve_role,
)

NOW = datetime(2026, 7, 4, 12, 0, 0)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def make_ko(
    fact: str = "fact",
    ko_id: Optional[UUID] = None,
    memory_type: MemoryType = MemoryType.FACT,
    confidence: float = 0.5,
    importance: float = 0.5,
    confirmation_count: int = 0,
    valid_from: datetime = NOW,
    valid_until: Optional[datetime] = None,
    metadata: Optional[dict] = None,
) -> KnowledgeObject:
    return KnowledgeObject(
        id=ko_id if ko_id is not None else uuid4(),
        canonical_fact=fact,
        memory_type=memory_type,
        confidence=confidence,
        importance=importance,
        confirmation_count=confirmation_count,
        valid_from=valid_from,
        valid_until=valid_until,
        metadata=metadata if metadata is not None else {},
    )


def make_ranked(
    ko: Optional[KnowledgeObject] = None,
    final_score: float = 0.5,
) -> RankedCandidate:
    candidate = Candidate(
        knowledge_object=ko if ko is not None else make_ko(),
        supporting_concepts=(),
        attachment_relevance=0.0,
        activation_score=0.0,
    )
    return RankedCandidate(
        candidate=candidate,
        final_score=final_score,
        score_breakdown={"importance": final_score},
    )


# ---------------------------------------------------------------------------
# resolve_role
# ---------------------------------------------------------------------------


class TestResolveRole:
    def test_mapping_is_total_over_memory_type(self):
        # Every MemoryType must resolve without falling through to a KeyError.
        for memory_type in MemoryType:
            role = resolve_role(make_ko(memory_type=memory_type))
            assert isinstance(role, MemoryRole)

    def test_representative_mappings(self):
        assert resolve_role(make_ko(memory_type=MemoryType.DECISION)) is MemoryRole.DECISION
        assert resolve_role(make_ko(memory_type=MemoryType.GOAL)) is MemoryRole.GOAL
        assert resolve_role(make_ko(memory_type=MemoryType.TASK)) is MemoryRole.TASK
        assert resolve_role(make_ko(memory_type=MemoryType.BELIEF)) is MemoryRole.BELIEF
        assert resolve_role(make_ko(memory_type=MemoryType.RULE)) is MemoryRole.BELIEF
        assert resolve_role(make_ko(memory_type=MemoryType.FACT)) is MemoryRole.RESEARCH
        assert resolve_role(make_ko(memory_type=MemoryType.PROJECT)) is MemoryRole.REFERENCE

    def test_new_project_state_type_mappings(self):
        # BLOCKER/IMPLEMENTATION_STATE/CODE_AREA/OPEN_QUESTION -- see
        # docs/architecture/PROJECT_STATE_KNOWLEDGE_MODEL.md §3: the first
        # three reuse existing roles rather than introducing new ones;
        # OPEN_QUESTION maps directly onto the role of the same name.
        assert resolve_role(make_ko(memory_type=MemoryType.BLOCKER)) is MemoryRole.TASK
        assert (
            resolve_role(make_ko(memory_type=MemoryType.IMPLEMENTATION_STATE))
            is MemoryRole.REFERENCE
        )
        assert resolve_role(make_ko(memory_type=MemoryType.CODE_AREA)) is MemoryRole.REFERENCE
        assert (
            resolve_role(make_ko(memory_type=MemoryType.OPEN_QUESTION))
            is MemoryRole.OPEN_QUESTION
        )

    def test_metadata_override_wins(self):
        ko = make_ko(memory_type=MemoryType.FACT, metadata={"role": "open_question"})
        assert resolve_role(ko) is MemoryRole.OPEN_QUESTION

    def test_invalid_override_falls_back_to_type(self):
        ko = make_ko(memory_type=MemoryType.DECISION, metadata={"role": "not_a_role"})
        assert resolve_role(ko) is MemoryRole.DECISION

    def test_open_question_reachable_via_memory_type(self):
        # Unlike before MemoryType.OPEN_QUESTION existed, OPEN_QUESTION is now
        # reachable through the default MemoryType mapping, not only via an
        # explicit metadata["role"] override.
        ko = make_ko(memory_type=MemoryType.OPEN_QUESTION)
        assert resolve_role(ko) is MemoryRole.OPEN_QUESTION

    def test_open_question_still_reachable_via_override_for_other_types(self):
        # The override path is untouched for every type other than the new
        # MemoryType.OPEN_QUESTION default.
        ko = make_ko(memory_type=MemoryType.DECISION, metadata={"role": "open_question"})
        assert resolve_role(ko) is MemoryRole.OPEN_QUESTION


# ---------------------------------------------------------------------------
# RoleBucket
# ---------------------------------------------------------------------------


class TestRoleBucket:
    def test_members_tuple_ified_from_list(self):
        bucket = RoleBucket(role=MemoryRole.DECISION, members=[make_ranked()])
        assert isinstance(bucket.members, tuple)

    def test_default_members_empty(self):
        assert RoleBucket(role=MemoryRole.TASK).members == ()

    def test_round_trip(self):
        bucket = RoleBucket(
            role=MemoryRole.DECISION,
            members=(make_ranked(make_ko(fact="d1")), make_ranked(make_ko(fact="d2"))),
        )
        restored = RoleBucket.from_dict(bucket.to_dict())
        assert restored.role is MemoryRole.DECISION
        assert [m.candidate.knowledge_object.canonical_fact for m in restored.members] == [
            "d1",
            "d2",
        ]


# ---------------------------------------------------------------------------
# WorkingContextState.from_buckets
# ---------------------------------------------------------------------------


class TestWorkingContextStateFromBuckets:
    def test_status_active_when_tasks_present(self):
        buckets = [RoleBucket(role=MemoryRole.TASK, members=(make_ranked(),))]
        assert WorkingContextState.from_buckets(buckets).status is ContextStatus.ACTIVE

    def test_status_active_when_open_questions_present(self):
        buckets = [RoleBucket(role=MemoryRole.OPEN_QUESTION, members=(make_ranked(),))]
        assert WorkingContextState.from_buckets(buckets).status is ContextStatus.ACTIVE

    def test_status_decided_when_only_decisions(self):
        buckets = [RoleBucket(role=MemoryRole.DECISION, members=(make_ranked(),))]
        assert WorkingContextState.from_buckets(buckets).status is ContextStatus.DECIDED

    def test_status_reference_when_only_research(self):
        buckets = [RoleBucket(role=MemoryRole.RESEARCH, members=(make_ranked(),))]
        assert WorkingContextState.from_buckets(buckets).status is ContextStatus.REFERENCE

    def test_status_reference_when_empty(self):
        assert WorkingContextState.from_buckets([]).status is ContextStatus.REFERENCE

    def test_current_goal_is_first_goal_member(self):
        top = make_ranked(make_ko(fact="top goal"), final_score=0.9)
        second = make_ranked(make_ko(fact="second goal"), final_score=0.3)
        buckets = [RoleBucket(role=MemoryRole.GOAL, members=(top, second))]
        state = WorkingContextState.from_buckets(buckets)
        assert state.current_goal is top

    def test_current_goal_none_when_no_goals(self):
        buckets = [RoleBucket(role=MemoryRole.DECISION, members=(make_ranked(),))]
        assert WorkingContextState.from_buckets(buckets).current_goal is None

    def test_recent_decisions_sorted_by_valid_from_desc(self):
        old = make_ranked(make_ko(fact="old", valid_from=datetime(2026, 1, 1)))
        new = make_ranked(make_ko(fact="new", valid_from=datetime(2026, 6, 1)))
        mid = make_ranked(make_ko(fact="mid", valid_from=datetime(2026, 3, 1)))
        buckets = [RoleBucket(role=MemoryRole.DECISION, members=(old, new, mid))]
        state = WorkingContextState.from_buckets(buckets)
        facts = [m.candidate.knowledge_object.canonical_fact for m in state.recent_decisions]
        assert facts == ["new", "mid", "old"]

    def test_top_k_caps_each_list(self):
        tasks = tuple(make_ranked(make_ko(fact=f"t{i}"), final_score=0.9 - i * 0.1) for i in range(5))
        buckets = [RoleBucket(role=MemoryRole.TASK, members=tasks)]
        state = WorkingContextState.from_buckets(buckets, top_k=2)
        assert len(state.pending_tasks) == 2


# ---------------------------------------------------------------------------
# Serialisation round-trips
# ---------------------------------------------------------------------------


class TestWorkingContextStateSerialisation:
    def test_round_trip_with_goal(self):
        state = WorkingContextState(
            status=ContextStatus.ACTIVE,
            current_goal=make_ranked(make_ko(fact="goal")),
            recent_decisions=(make_ranked(make_ko(fact="d")),),
            pending_tasks=(make_ranked(make_ko(fact="t")),),
            open_questions=(),
        )
        restored = WorkingContextState.from_dict(state.to_dict())
        assert restored.status is ContextStatus.ACTIVE
        assert restored.current_goal.candidate.knowledge_object.canonical_fact == "goal"
        assert restored.open_questions == ()

    def test_round_trip_without_goal(self):
        state = WorkingContextState(status=ContextStatus.REFERENCE)
        restored = WorkingContextState.from_dict(state.to_dict())
        assert restored.current_goal is None
        assert restored.status is ContextStatus.REFERENCE


class TestWorkingContextSerialisation:
    def test_round_trip_full(self):
        anchor = uuid4()
        member = uuid4()
        context = WorkingContext(
            key="ctx:haven",
            title="Haven",
            kind=ContextKind.PROJECT,
            state=WorkingContextState(status=ContextStatus.DECIDED),
            buckets=(
                RoleBucket(role=MemoryRole.DECISION, members=(make_ranked(make_ko(fact="d")),)),
            ),
            anchor_concept_id=anchor,
            member_concept_ids=(member,),
        )
        restored = WorkingContext.from_dict(context.to_dict())
        assert restored.key == "ctx:haven"
        assert restored.title == "Haven"
        assert restored.kind is ContextKind.PROJECT
        assert restored.anchor_concept_id == anchor
        assert restored.member_concept_ids == (member,)
        assert restored.buckets[0].role is MemoryRole.DECISION

    def test_round_trip_without_anchor(self):
        context = WorkingContext(
            key="ctx:general",
            title="General",
            kind=ContextKind.GENERAL,
            state=WorkingContextState(status=ContextStatus.REFERENCE),
        )
        restored = WorkingContext.from_dict(context.to_dict())
        assert restored.anchor_concept_id is None
        assert restored.member_concept_ids == ()
        assert restored.buckets == ()
