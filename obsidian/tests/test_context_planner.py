"""Unit tests for obsidian.memory_engine.context_planner.

Test groups
-----------
TestCategoryRequirementValidation   — min_count/max_count bound checks.
TestCategoryRequirementDefaults     — default necessity/tier/count values.
TestCategoryRequirementSerialization — to_dict/from_dict round-trips.
TestCategoryRequirementImmutability  — frozen, hashable, value equality.
TestContextPlanValidation           — confidence bound checks, list->tuple
                                       coercion of requirements.
TestContextPlanDefaults             — default field values.
TestContextPlanSerialization        — to_dict/from_dict round-trips,
                                       including a None and a set
                                       scope_concept_id.
TestContextPlanImmutability         — frozen, hashable, value equality
                                       (with an explicit created_at so two
                                       plans can compare equal).
TestPointedQASentinel               — unmatched / empty queries produce
                                       task_mode=POINTED_QA and
                                       requirements=().
TestContinuationMode                — "continue"-shaped queries produce
                                       the full six-category table.
TestCodingDebuggingMode             — bug/error/fix-shaped queries produce
                                       the five-category table.
TestStructuringMode                 — plan/design-shaped queries produce
                                       the three-category table.
TestResearchMode                    — research/investigate-shaped queries
                                       produce the three-category table.
TestModeDisambiguation              — CONTINUATION wins over
                                       CODING_DEBUGGING when both patterns
                                       are present in one query.
TestScopePassthrough                — scope_concept_id is carried through
                                       verbatim, untouched by classification.
TestDeterminism                     — repeated calls with the same input
                                       classify identically; every mode's
                                       table is identical across calls.
TestStatelessReuse                  — one ContextPlanner instance handles
                                       many different queries without
                                       cross-call leakage.
TestPlanningMethodAndConfidence     — every produced plan is DETERMINISTIC
                                       with confidence == 1.0 in this phase.
TestNeverDropTiering                — CONSTRAINT/BLOCKER requirements are
                                       always PriorityTier.NEVER_DROP;
                                       everything else is NORMAL.
TestNoOutOfScopeImports             — module never imports retrieval/
                                       ranking/acceptance/allocation/
                                       WorkingContext/MemoryEngine/LLM code.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from obsidian.memory_engine.context_planner import (
    CategoryRequirement,
    ContextCategory,
    ContextPlan,
    ContextPlanner,
    Necessity,
    PlanningMethod,
    PriorityTier,
    TaskMode,
)

# ---------------------------------------------------------------------------
# CategoryRequirement
# ---------------------------------------------------------------------------


class TestCategoryRequirementValidation:
    def test_negative_min_count_raises(self) -> None:
        with pytest.raises(ValueError, match="min_count"):
            CategoryRequirement(
                category=ContextCategory.DECISION,
                necessity=Necessity.REQUIRED,
                min_count=-1,
            )

    def test_max_count_below_min_count_raises(self) -> None:
        with pytest.raises(ValueError, match="max_count"):
            CategoryRequirement(
                category=ContextCategory.CONSTRAINT,
                necessity=Necessity.OPTIONAL,
                min_count=3,
                max_count=2,
            )

    def test_max_count_equal_to_min_count_is_valid(self) -> None:
        req = CategoryRequirement(
            category=ContextCategory.CONSTRAINT,
            necessity=Necessity.OPTIONAL,
            min_count=2,
            max_count=2,
        )
        assert req.max_count == 2

    def test_zero_min_count_is_valid(self) -> None:
        req = CategoryRequirement(
            category=ContextCategory.BLOCKER,
            necessity=Necessity.OPTIONAL,
            min_count=0,
        )
        assert req.min_count == 0

    def test_max_count_none_is_valid_regardless_of_min_count(self) -> None:
        req = CategoryRequirement(
            category=ContextCategory.TASK,
            necessity=Necessity.REQUIRED,
            min_count=5,
            max_count=None,
        )
        assert req.max_count is None


class TestCategoryRequirementDefaults:
    def test_defaults(self) -> None:
        req = CategoryRequirement(
            category=ContextCategory.DECISION, necessity=Necessity.REQUIRED
        )
        assert req.min_count == 1
        assert req.max_count is None
        assert req.priority_tier is PriorityTier.NORMAL


class TestCategoryRequirementSerialization:
    def test_round_trip(self) -> None:
        req = CategoryRequirement(
            category=ContextCategory.CODE_AREA,
            necessity=Necessity.OPTIONAL,
            min_count=2,
            max_count=4,
            priority_tier=PriorityTier.DROP_FIRST,
        )
        restored = CategoryRequirement.from_dict(req.to_dict())
        assert restored == req

    def test_to_dict_is_json_shaped(self) -> None:
        req = CategoryRequirement(
            category=ContextCategory.BELIEF, necessity=Necessity.REQUIRED
        )
        data = req.to_dict()
        assert data == {
            "category": "belief",
            "necessity": "required",
            "min_count": 1,
            "max_count": None,
            "priority_tier": "normal",
        }

    def test_from_dict_applies_defaults_for_missing_optional_keys(self) -> None:
        restored = CategoryRequirement.from_dict(
            {"category": "task", "necessity": "required"}
        )
        assert restored.min_count == 1
        assert restored.max_count is None
        assert restored.priority_tier is PriorityTier.NORMAL


class TestCategoryRequirementImmutability:
    def test_frozen(self) -> None:
        req = CategoryRequirement(
            category=ContextCategory.DECISION, necessity=Necessity.REQUIRED
        )
        with pytest.raises(FrozenInstanceError):
            req.min_count = 9  # type: ignore[misc]

    def test_equality_by_value(self) -> None:
        a = CategoryRequirement(category=ContextCategory.DECISION, necessity=Necessity.REQUIRED)
        b = CategoryRequirement(category=ContextCategory.DECISION, necessity=Necessity.REQUIRED)
        assert a == b

    def test_inequality_on_differing_field(self) -> None:
        a = CategoryRequirement(category=ContextCategory.DECISION, necessity=Necessity.REQUIRED)
        b = CategoryRequirement(category=ContextCategory.DECISION, necessity=Necessity.OPTIONAL)
        assert a != b

    def test_hashable_and_usable_in_a_set(self) -> None:
        a = CategoryRequirement(category=ContextCategory.TASK, necessity=Necessity.REQUIRED)
        b = CategoryRequirement(category=ContextCategory.TASK, necessity=Necessity.REQUIRED)
        assert hash(a) == hash(b)
        assert {a, b} == {a}


# ---------------------------------------------------------------------------
# ContextPlan
# ---------------------------------------------------------------------------


class TestContextPlanValidation:
    def test_confidence_above_one_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            ContextPlan(query="q", task_mode=TaskMode.POINTED_QA, confidence=1.1)

    def test_confidence_below_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            ContextPlan(query="q", task_mode=TaskMode.POINTED_QA, confidence=-0.1)

    def test_confidence_boundary_values_are_valid(self) -> None:
        ContextPlan(query="q", task_mode=TaskMode.POINTED_QA, confidence=0.0)
        ContextPlan(query="q", task_mode=TaskMode.POINTED_QA, confidence=1.0)

    def test_requirements_list_is_coerced_to_tuple(self) -> None:
        req = CategoryRequirement(category=ContextCategory.DECISION, necessity=Necessity.REQUIRED)
        plan = ContextPlan(
            query="q",
            task_mode=TaskMode.RESEARCH,
            requirements=[req],  # type: ignore[arg-type]
        )
        assert isinstance(plan.requirements, tuple)
        assert plan.requirements == (req,)


class TestContextPlanDefaults:
    def test_defaults(self) -> None:
        plan = ContextPlan(query="what database do I use", task_mode=TaskMode.POINTED_QA)
        assert plan.requirements == ()
        assert plan.scope_concept_id is None
        assert plan.confidence == 1.0
        assert plan.planning_method is PlanningMethod.DETERMINISTIC
        assert plan.created_at is not None


class TestContextPlanSerialization:
    def test_round_trip_with_no_scope(self) -> None:
        plan = ContextPlan(query="continue implementing Haven", task_mode=TaskMode.CONTINUATION)
        restored = ContextPlan.from_dict(plan.to_dict())
        assert restored == plan

    def test_round_trip_with_scope_and_requirements(self) -> None:
        concept_id = uuid4()
        req = CategoryRequirement(
            category=ContextCategory.CONSTRAINT,
            necessity=Necessity.REQUIRED,
            priority_tier=PriorityTier.NEVER_DROP,
        )
        plan = ContextPlan(
            query="why is this failing",
            task_mode=TaskMode.CODING_DEBUGGING,
            requirements=(req,),
            scope_concept_id=concept_id,
        )
        restored = ContextPlan.from_dict(plan.to_dict())
        assert restored == plan
        assert restored.scope_concept_id == concept_id

    def test_to_dict_serialises_scope_concept_id_as_string(self) -> None:
        concept_id = uuid4()
        plan = ContextPlan(
            query="q", task_mode=TaskMode.POINTED_QA, scope_concept_id=concept_id
        )
        assert plan.to_dict()["scope_concept_id"] == str(concept_id)

    def test_to_dict_serialises_none_scope_concept_id_as_none(self) -> None:
        plan = ContextPlan(query="q", task_mode=TaskMode.POINTED_QA)
        assert plan.to_dict()["scope_concept_id"] is None

    def test_from_dict_applies_defaults_for_missing_optional_keys(self) -> None:
        restored = ContextPlan.from_dict({"query": "q", "task_mode": "pointed_qa"})
        assert restored.requirements == ()
        assert restored.scope_concept_id is None
        assert restored.confidence == 1.0
        assert restored.planning_method is PlanningMethod.DETERMINISTIC


class TestContextPlanImmutability:
    def test_frozen(self) -> None:
        plan = ContextPlan(query="q", task_mode=TaskMode.POINTED_QA)
        with pytest.raises(FrozenInstanceError):
            plan.confidence = 0.5  # type: ignore[misc]

    def test_equality_by_value_with_explicit_created_at(self) -> None:
        from datetime import datetime

        stamp = datetime(2026, 7, 9, 12, 0, 0)
        a = ContextPlan(query="q", task_mode=TaskMode.POINTED_QA, created_at=stamp)
        b = ContextPlan(query="q", task_mode=TaskMode.POINTED_QA, created_at=stamp)
        assert a == b

    def test_inequality_on_differing_task_mode(self) -> None:
        from datetime import datetime

        stamp = datetime(2026, 7, 9, 12, 0, 0)
        a = ContextPlan(query="q", task_mode=TaskMode.POINTED_QA, created_at=stamp)
        b = ContextPlan(query="q", task_mode=TaskMode.RESEARCH, created_at=stamp)
        assert a != b

    def test_hashable_and_usable_in_a_set(self) -> None:
        from datetime import datetime

        stamp = datetime(2026, 7, 9, 12, 0, 0)
        a = ContextPlan(query="q", task_mode=TaskMode.POINTED_QA, created_at=stamp)
        b = ContextPlan(query="q", task_mode=TaskMode.POINTED_QA, created_at=stamp)
        assert hash(a) == hash(b)
        assert {a, b} == {a}


# ---------------------------------------------------------------------------
# ContextPlanner — classification behaviour
# ---------------------------------------------------------------------------


class TestPointedQASentinel:
    @pytest.mark.parametrize(
        "query",
        [
            "what database do I use",
            "what's my editor preference",
            "who is Alice",
            "",
            "   ",
        ],
    )
    def test_unmatched_or_empty_queries_are_pointed_qa(self, query: str) -> None:
        plan = ContextPlanner().plan(query)
        assert plan.task_mode is TaskMode.POINTED_QA
        assert plan.requirements == ()


class TestContinuationMode:
    @pytest.mark.parametrize(
        "query",
        [
            "continue implementing Haven",
            "Where were we?",
            "let's pick up where we left off",
            "resume the benchmark work",
        ],
    )
    def test_continuation_queries_request_all_six_categories(self, query: str) -> None:
        plan = ContextPlanner().plan(query)
        assert plan.task_mode is TaskMode.CONTINUATION
        categories = {req.category for req in plan.requirements}
        assert categories == {
            ContextCategory.DECISION,
            ContextCategory.TASK,
            ContextCategory.CONSTRAINT,
            ContextCategory.BLOCKER,
            ContextCategory.RESEARCH,
            ContextCategory.OPEN_QUESTION,
        }
        assert all(req.necessity is Necessity.REQUIRED for req in plan.requirements)


class TestGenericContinuationQueries:
    """Regression coverage for the generic, content-free continuation
    phrasings named in ``docs/architecture/GENERIC_CONTINUATION_QUERY_ANALYSIS.md``
    §4 fix #2 -- queries that carry no vault-specific vocabulary but should
    still classify as CONTINUATION so the category-fallback retrieval path
    (``MemoryEngine._category_fallback_candidates``) gets a chance to run.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "Continue.",
            "Continue working.",
            "Continue implementing.",
            "Continue yesterday's work.",
            "What should we do next?",
            "What should I work on next?",
            "Where were we?",
            "Pick this back up.",
        ],
    )
    def test_generic_continuation_phrasings_classify_as_continuation(
        self, query: str
    ) -> None:
        plan = ContextPlanner().plan(query)
        assert plan.task_mode is TaskMode.CONTINUATION
        categories = {req.category for req in plan.requirements}
        assert categories == {
            ContextCategory.DECISION,
            ContextCategory.TASK,
            ContextCategory.CONSTRAINT,
            ContextCategory.BLOCKER,
            ContextCategory.RESEARCH,
            ContextCategory.OPEN_QUESTION,
        }


class TestCodingDebuggingMode:
    @pytest.mark.parametrize(
        "query",
        [
            "there's a bug in the retriever",
            "why is this failing",
            "the tests aren't working",
            "help me fix this error",
        ],
    )
    def test_coding_debugging_queries_request_five_categories(self, query: str) -> None:
        plan = ContextPlanner().plan(query)
        assert plan.task_mode is TaskMode.CODING_DEBUGGING
        categories = {req.category for req in plan.requirements}
        assert categories == {
            ContextCategory.DECISION,
            ContextCategory.CONSTRAINT,
            ContextCategory.IMPLEMENTATION_STATE,
            ContextCategory.CODE_AREA,
            ContextCategory.OPEN_QUESTION,
        }


class TestStructuringMode:
    @pytest.mark.parametrize(
        "query",
        [
            "let's design the new architecture",
            "help me plan the next milestone",
            "what's the roadmap here",
        ],
    )
    def test_structuring_queries_request_three_categories(self, query: str) -> None:
        plan = ContextPlanner().plan(query)
        assert plan.task_mode is TaskMode.STRUCTURING
        categories = {req.category for req in plan.requirements}
        assert categories == {
            ContextCategory.DECISION,
            ContextCategory.RESEARCH,
            ContextCategory.CONSTRAINT,
        }


class TestResearchMode:
    @pytest.mark.parametrize(
        "query",
        [
            "research how Zep handles staleness",
            "investigate the ranking failure",
            "look into GraphRAG's routing approach",
        ],
    )
    def test_research_queries_request_three_categories(self, query: str) -> None:
        plan = ContextPlanner().plan(query)
        assert plan.task_mode is TaskMode.RESEARCH
        categories = {req.category for req in plan.requirements}
        assert categories == {
            ContextCategory.RESEARCH,
            ContextCategory.BELIEF,
            ContextCategory.DECISION,
        }


class TestModeDisambiguation:
    def test_continuation_pattern_wins_over_coding_debugging_pattern(self) -> None:
        # Contains both "continue" (CONTINUATION) and "implement"/"bug"
        # (CODING_DEBUGGING) -- CONTINUATION must win per the table order.
        plan = ContextPlanner().plan("continue implementing the bug fix")
        assert plan.task_mode is TaskMode.CONTINUATION


# ---------------------------------------------------------------------------
# ContextPlanner — cross-cutting behaviour
# ---------------------------------------------------------------------------


class TestScopePassthrough:
    def test_scope_is_carried_through_verbatim(self) -> None:
        concept_id = uuid4()
        plan = ContextPlanner().plan("continue implementing Haven", scope_concept_id=concept_id)
        assert plan.scope_concept_id == concept_id

    def test_scope_defaults_to_none(self) -> None:
        plan = ContextPlanner().plan("continue implementing Haven")
        assert plan.scope_concept_id is None


class TestDeterminism:
    @pytest.mark.parametrize(
        "query",
        [
            "continue implementing Haven",
            "there's a bug in the retriever",
            "let's design the new architecture",
            "research how Zep handles staleness",
            "what database do I use",
        ],
    )
    def test_repeated_calls_classify_identically(self, query: str) -> None:
        planner = ContextPlanner()
        first = planner.plan(query)
        second = planner.plan(query)
        assert first.task_mode == second.task_mode
        assert first.requirements == second.requirements

    def test_every_mode_table_is_stable_across_calls(self) -> None:
        planner = ContextPlanner()
        for _ in range(5):
            assert planner.plan("continue implementing Haven").requirements == (
                planner.plan("continue implementing Haven").requirements
            )


class TestStatelessReuse:
    def test_one_instance_handles_many_distinct_queries(self) -> None:
        planner = ContextPlanner()
        results = [
            planner.plan("continue implementing Haven").task_mode,
            planner.plan("what database do I use").task_mode,
            planner.plan("there's a bug in the retriever").task_mode,
            planner.plan("let's design the new architecture").task_mode,
            planner.plan("research how Zep handles staleness").task_mode,
        ]
        assert results == [
            TaskMode.CONTINUATION,
            TaskMode.POINTED_QA,
            TaskMode.CODING_DEBUGGING,
            TaskMode.STRUCTURING,
            TaskMode.RESEARCH,
        ]


class TestPlanningMethodAndConfidence:
    @pytest.mark.parametrize(
        "query",
        [
            "continue implementing Haven",
            "what database do I use",
            "there's a bug in the retriever",
        ],
    )
    def test_every_plan_is_deterministic_with_full_confidence(self, query: str) -> None:
        plan = ContextPlanner().plan(query)
        assert plan.planning_method is PlanningMethod.DETERMINISTIC
        assert plan.confidence == 1.0


class TestNeverDropTiering:
    def test_constraint_and_blocker_are_never_drop_everywhere_they_appear(self) -> None:
        planner = ContextPlanner()
        for query in (
            "continue implementing Haven",
            "there's a bug in the retriever",
            "let's design the new architecture",
        ):
            plan = planner.plan(query)
            for req in plan.requirements:
                if req.category in (ContextCategory.CONSTRAINT, ContextCategory.BLOCKER):
                    assert req.priority_tier is PriorityTier.NEVER_DROP
                else:
                    assert req.priority_tier is PriorityTier.NORMAL


# ---------------------------------------------------------------------------
# Scope isolation
# ---------------------------------------------------------------------------


class TestNoOutOfScopeImports:
    def test_module_does_not_bind_retrieval_ranking_or_engine_names(self) -> None:
        """The module's docstring *discusses* HybridCandidateRetriever,
        DeterministicRanker, AcceptanceStage, WorkingContextBuilder,
        MemoryEngine, and an LLM client as out-of-scope context, but must
        never actually import or bind them — checked against the module
        namespace rather than raw source text so prose mentions don't
        produce false positives."""
        import obsidian.memory_engine.context_planner as module

        forbidden = {
            "HybridCandidateRetriever",
            "DeterministicRanker",
            "AcceptanceStage",
            "DeterministicSlotAllocator",
            "WorkingContextBuilder",
            "MemoryEngine",
            "StructuredPromptBuilder",
            "QueryRewriter",
            "OpenAI",
        }
        assert forbidden.isdisjoint(dir(module))
