"""Unit tests for
:class:`~obsidian.memory_engine.working_context_builder.WorkingContextBuilder`.

Test groups
-----------
TestEmptyInput                  — build([]) returns exactly one empty
                                   GENERAL context.
TestNoOntologyEvidence           — candidates with no supporting_concepts all
                                   land in a single default GENERAL context.
TestGroupingByPrimaryConcept     — candidates sharing a primary concept are
                                   grouped into one context per concept;
                                   ties broken deterministically.
TestMixedGrouping                — concept-anchored contexts plus a shared
                                   GENERAL context for ungrouped leftovers.
TestRoleBucketAssignment         — every MemoryRole is present as a bucket;
                                   members land in the bucket resolve_role
                                   picks, in rank order.
TestStateDerivation              — each context's state is exactly
                                   WorkingContextState.from_buckets(buckets).
TestMemberConceptIds              — member_concept_ids is the sorted union of
                                   all supporting concept ids in the context.
TestNoMutation                   — input list/candidates untouched.
TestStatelessReuse                — one instance, many calls, no leakage.
TestDeterminism                   — repeated calls over a shuffled input
                                   produce identical output.
TestNoOutOfScopeImports           — module never imports retrieval/ranking/
                                   acceptance/allocation/formatting code.
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Optional, Tuple
from uuid import UUID, uuid4

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine.working_context_builder import WorkingContextBuilder
from obsidian.ontology.retrieval_models import (
    ActivatedConcept,
    Candidate,
    ContextKind,
    MemoryRole,
    RankedCandidate,
    WorkingContextState,
)

NOW = datetime(2026, 7, 4, 12, 0, 0)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def make_ko(
    fact: str = "fact",
    ko_id: Optional[UUID] = None,
    memory_type: MemoryType = MemoryType.FACT,
    valid_from: datetime = NOW,
    metadata: Optional[dict] = None,
) -> KnowledgeObject:
    return KnowledgeObject(
        id=ko_id if ko_id is not None else uuid4(),
        canonical_fact=fact,
        memory_type=memory_type,
        confidence=0.5,
        importance=0.5,
        confirmation_count=0,
        valid_from=valid_from,
        valid_until=None,
        metadata=metadata if metadata is not None else {},
    )


def make_activated(
    concept_id: UUID, activation_score: float = 1.0, activation_depth: int = 0
) -> ActivatedConcept:
    return ActivatedConcept(
        concept_id=concept_id,
        activation_score=activation_score,
        activation_depth=activation_depth,
        source_seed=concept_id,
    )


def make_ranked(
    ko: Optional[KnowledgeObject] = None,
    final_score: float = 0.5,
    supporting_concepts: Tuple[ActivatedConcept, ...] = (),
) -> RankedCandidate:
    candidate = Candidate(
        knowledge_object=ko if ko is not None else make_ko(),
        supporting_concepts=supporting_concepts,
        attachment_relevance=1.0 if supporting_concepts else 0.0,
        activation_score=1.0 if supporting_concepts else 0.0,
    )
    return RankedCandidate(
        candidate=candidate,
        final_score=final_score,
        score_breakdown={"importance": final_score},
    )


# ---------------------------------------------------------------------------
# TestEmptyInput
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_empty_input_returns_one_general_context(self) -> None:
        contexts = WorkingContextBuilder().build([])
        assert len(contexts) == 1
        assert contexts[0].kind is ContextKind.GENERAL
        assert contexts[0].key == "ctx:general"
        assert contexts[0].anchor_concept_id is None
        assert all(bucket.members == () for bucket in contexts[0].buckets)


# ---------------------------------------------------------------------------
# TestNoOntologyEvidence
# ---------------------------------------------------------------------------


class TestNoOntologyEvidence:
    def test_all_candidates_without_evidence_form_single_default_context(self) -> None:
        candidates = [make_ranked(make_ko(fact=f"f{i}"), final_score=0.1 * i) for i in range(4)]
        contexts = WorkingContextBuilder().build(candidates)
        assert len(contexts) == 1
        assert contexts[0].kind is ContextKind.GENERAL
        assert contexts[0].anchor_concept_id is None
        all_members = [m for bucket in contexts[0].buckets for m in bucket.members]
        assert len(all_members) == len(candidates)


# ---------------------------------------------------------------------------
# TestGroupingByPrimaryConcept
# ---------------------------------------------------------------------------


class TestGroupingByPrimaryConcept:
    def test_candidates_sharing_a_concept_land_in_one_context(self) -> None:
        concept = uuid4()
        a = make_ranked(make_ko(fact="a"), supporting_concepts=(make_activated(concept),))
        b = make_ranked(make_ko(fact="b"), supporting_concepts=(make_activated(concept),))
        contexts = WorkingContextBuilder().build([a, b])
        assert len(contexts) == 1
        assert contexts[0].anchor_concept_id == concept
        assert contexts[0].kind is ContextKind.TOPIC

    def test_distinct_concepts_produce_distinct_contexts(self) -> None:
        c1, c2 = uuid4(), uuid4()
        a = make_ranked(make_ko(fact="a"), supporting_concepts=(make_activated(c1),))
        b = make_ranked(make_ko(fact="b"), supporting_concepts=(make_activated(c2),))
        contexts = WorkingContextBuilder().build([a, b])
        assert len(contexts) == 2
        anchors = {c.anchor_concept_id for c in contexts}
        assert anchors == {c1, c2}

    def test_context_order_is_ascending_str_concept_id(self) -> None:
        c1, c2 = uuid4(), uuid4()
        a = make_ranked(make_ko(fact="a"), supporting_concepts=(make_activated(c1),))
        b = make_ranked(make_ko(fact="b"), supporting_concepts=(make_activated(c2),))
        contexts = WorkingContextBuilder().build([b, a])
        expected_order = sorted([c1, c2], key=str)
        assert [c.anchor_concept_id for c in contexts] == expected_order

    def test_primary_concept_is_highest_activation(self) -> None:
        strong, weak = uuid4(), uuid4()
        ranked = make_ranked(
            make_ko(fact="a"),
            supporting_concepts=(
                make_activated(weak, activation_score=0.2),
                make_activated(strong, activation_score=0.9),
            ),
        )
        contexts = WorkingContextBuilder().build([ranked])
        assert len(contexts) == 1
        assert contexts[0].anchor_concept_id == strong

    def test_activation_tie_broken_by_concept_id_string(self) -> None:
        c1, c2 = uuid4(), uuid4()
        expected = min(c1, c2, key=str)
        ranked = make_ranked(
            make_ko(fact="a"),
            supporting_concepts=(
                make_activated(c1, activation_score=0.5),
                make_activated(c2, activation_score=0.5),
            ),
        )
        contexts = WorkingContextBuilder().build([ranked])
        assert contexts[0].anchor_concept_id == expected


# ---------------------------------------------------------------------------
# TestMixedGrouping
# ---------------------------------------------------------------------------


class TestMixedGrouping:
    def test_concept_anchored_and_general_contexts_coexist(self) -> None:
        concept = uuid4()
        anchored = make_ranked(make_ko(fact="a"), supporting_concepts=(make_activated(concept),))
        loose = make_ranked(make_ko(fact="b"))
        contexts = WorkingContextBuilder().build([anchored, loose])
        assert len(contexts) == 2
        kinds = {c.kind for c in contexts}
        assert kinds == {ContextKind.TOPIC, ContextKind.GENERAL}
        general = next(c for c in contexts if c.kind is ContextKind.GENERAL)
        assert general.key == "ctx:general"

    def test_general_context_omitted_when_no_leftovers(self) -> None:
        concept = uuid4()
        anchored = make_ranked(make_ko(fact="a"), supporting_concepts=(make_activated(concept),))
        contexts = WorkingContextBuilder().build([anchored])
        assert len(contexts) == 1
        assert contexts[0].kind is ContextKind.TOPIC


# ---------------------------------------------------------------------------
# TestRoleBucketAssignment
# ---------------------------------------------------------------------------


class TestRoleBucketAssignment:
    def test_every_role_present_as_a_bucket(self) -> None:
        contexts = WorkingContextBuilder().build([make_ranked()])
        roles = {bucket.role for bucket in contexts[0].buckets}
        assert roles == set(MemoryRole)

    def test_member_lands_in_role_resolved_by_memory_type(self) -> None:
        task = make_ranked(make_ko(fact="t", memory_type=MemoryType.TASK))
        contexts = WorkingContextBuilder().build([task])
        task_bucket = next(b for b in contexts[0].buckets if b.role is MemoryRole.TASK)
        assert task_bucket.members == (task,)

    def test_members_preserve_rank_order_within_bucket(self) -> None:
        low = make_ranked(make_ko(fact="low", memory_type=MemoryType.TASK), final_score=0.1)
        high = make_ranked(make_ko(fact="high", memory_type=MemoryType.TASK), final_score=0.9)
        contexts = WorkingContextBuilder().build([low, high])
        task_bucket = next(b for b in contexts[0].buckets if b.role is MemoryRole.TASK)
        assert task_bucket.members == (high, low)


# ---------------------------------------------------------------------------
# TestStateDerivation
# ---------------------------------------------------------------------------


class TestStateDerivation:
    def test_state_matches_from_buckets_over_same_buckets(self) -> None:
        task = make_ranked(make_ko(fact="t", memory_type=MemoryType.TASK))
        contexts = WorkingContextBuilder().build([task])
        expected = WorkingContextState.from_buckets(list(contexts[0].buckets))
        assert contexts[0].state == expected


# ---------------------------------------------------------------------------
# TestMemberConceptIds
# ---------------------------------------------------------------------------


class TestMemberConceptIds:
    def test_member_concept_ids_is_sorted_union(self) -> None:
        c1, c2 = uuid4(), uuid4()
        a = make_ranked(make_ko(fact="a"), supporting_concepts=(make_activated(c1),))
        b = make_ranked(make_ko(fact="b"), supporting_concepts=(make_activated(c1), make_activated(c2)))
        contexts = WorkingContextBuilder().build([a, b])
        assert contexts[0].member_concept_ids == tuple(sorted([c1, c2], key=str))

    def test_general_context_has_no_member_concept_ids(self) -> None:
        contexts = WorkingContextBuilder().build([make_ranked()])
        assert contexts[0].member_concept_ids == ()


# ---------------------------------------------------------------------------
# TestNoMutation
# ---------------------------------------------------------------------------


class TestNoMutation:
    def test_input_list_is_not_mutated(self) -> None:
        low = make_ranked(final_score=0.1)
        high = make_ranked(final_score=0.9)
        ranked = [low, high]
        snapshot = list(ranked)
        WorkingContextBuilder().build(ranked)
        assert ranked == snapshot

    def test_candidate_instances_are_reused_not_copied(self) -> None:
        ranked = make_ranked(make_ko(fact="a", memory_type=MemoryType.TASK))
        contexts = WorkingContextBuilder().build([ranked])
        task_bucket = next(b for b in contexts[0].buckets if b.role is MemoryRole.TASK)
        assert task_bucket.members[0] is ranked


# ---------------------------------------------------------------------------
# TestStatelessReuse
# ---------------------------------------------------------------------------


class TestStatelessReuse:
    def test_same_instance_reused_across_calls(self) -> None:
        builder = WorkingContextBuilder()
        first = builder.build([make_ranked(final_score=0.3)])
        second = builder.build([make_ranked(final_score=0.8)])
        assert first[0].kind is ContextKind.GENERAL
        assert second[0].kind is ContextKind.GENERAL
        assert first is not second


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_shuffled_input_produces_identical_output(self) -> None:
        concept = uuid4()
        candidates = [
            make_ranked(
                make_ko(fact=f"f{i}"),
                final_score=0.05 * i,
                supporting_concepts=(make_activated(concept),) if i % 2 == 0 else (),
            )
            for i in range(10)
        ]
        builder = WorkingContextBuilder()
        baseline = builder.build(candidates)

        shuffled = list(candidates)
        random.Random(42).shuffle(shuffled)
        result = builder.build(shuffled)

        assert [c.to_dict() for c in result] == [c.to_dict() for c in baseline]


# ---------------------------------------------------------------------------
# TestNoOutOfScopeImports
# ---------------------------------------------------------------------------


class TestNoOutOfScopeImports:
    def test_module_does_not_bind_retrieval_ranking_or_formatting_names(self) -> None:
        """The module's docstring *discusses* MemoryStore/ConceptGraph/
        DeterministicRanker/AcceptanceStage/DeterministicSlotAllocator/
        StructuredPromptBuilder as out-of-scope context, but must never
        actually import or bind them — checked against the module namespace
        rather than raw source text so prose mentions don't produce false
        positives."""
        import obsidian.memory_engine.working_context_builder as module

        forbidden = {
            "MemoryStore",
            "ConceptGraph",
            "DeterministicRanker",
            "AcceptanceStage",
            "DeterministicSlotAllocator",
            "StructuredPromptBuilder",
        }
        assert forbidden.isdisjoint(dir(module))
