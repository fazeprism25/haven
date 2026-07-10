"""Unit tests for the Extractor's optional ``existing_context`` parameter.

Test groups
-----------
TestBackwardCompatibility -- omitting the parameter (or passing ``None``,
                             ``[]``, or a list of entirely-empty contexts)
                             must reproduce today's exact prompt/behaviour.
TestRendering             -- a context carrying goal/decisions/tasks/
                             questions renders under EXISTING CONTEXT, the
                             conversation renders under NEW EVIDENCE.
TestInstructions          -- the required framing language is present.
TestThreadedThroughExtract -- extract() passes existing_context to
                             build_prompt (verified via a capturing fake
                             LLM), and extraction results are otherwise
                             unaffected.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

from obsidian.core.enums import MemoryType, Role, SourceType
from obsidian.core.types import Conversation, Event
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.ontology.retrieval_models import (
    Candidate,
    ContextKind,
    ContextStatus,
    RankedCandidate,
    WorkingContext,
    WorkingContextState,
)

NOW = datetime(2026, 7, 4, 12, 0, 0)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def make_ko(fact: str, ko_id: Optional[UUID] = None) -> KnowledgeObject:
    return KnowledgeObject(
        id=ko_id if ko_id is not None else uuid4(),
        canonical_fact=fact,
        memory_type=MemoryType.FACT,
        confidence=0.9,
        importance=0.5,
        confirmation_count=0,
        valid_from=NOW,
        valid_until=None,
    )


def make_ranked(fact: str) -> RankedCandidate:
    candidate = Candidate(
        knowledge_object=make_ko(fact),
        supporting_concepts=(),
        attachment_relevance=0.0,
        activation_score=0.0,
    )
    return RankedCandidate(
        candidate=candidate, final_score=0.5, score_breakdown={"importance": 0.5}
    )


def make_context(
    title: str = "Haven",
    goal: Optional[str] = None,
    decisions: tuple = (),
    tasks: tuple = (),
    questions: tuple = (),
) -> WorkingContext:
    state = WorkingContextState(
        status=ContextStatus.ACTIVE,
        current_goal=make_ranked(goal) if goal is not None else None,
        recent_decisions=tuple(make_ranked(d) for d in decisions),
        pending_tasks=tuple(make_ranked(t) for t in tasks),
        open_questions=tuple(make_ranked(q) for q in questions),
    )
    return WorkingContext(
        key=f"ctx:{title}",
        title=title,
        kind=ContextKind.PROJECT,
        state=state,
        buckets=(),
    )


def make_conversation(text: str = "I also use Zsh now.") -> Conversation:
    return Conversation(
        title="Remember",
        source=SourceType.MANUAL,
        events=[Event(role=Role.USER, content=text, source=SourceType.MANUAL)],
    )


class _CapturingLLM:
    """Fake LLM that records the prompt it was given and returns ``[]``."""

    def __init__(self) -> None:
        self.prompts: List[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "[]"


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_omitted_equals_explicit_none(self) -> None:
        extractor = Extractor(llm=_CapturingLLM())
        conversation = make_conversation()
        assert extractor.build_prompt(conversation) == extractor.build_prompt(
            conversation, existing_context=None
        )

    def test_empty_list_equals_none(self) -> None:
        extractor = Extractor(llm=_CapturingLLM())
        conversation = make_conversation()
        assert extractor.build_prompt(
            conversation, existing_context=[]
        ) == extractor.build_prompt(conversation, existing_context=None)

    def test_context_with_no_populated_fields_equals_none(self) -> None:
        extractor = Extractor(llm=_CapturingLLM())
        conversation = make_conversation()
        empty_context = make_context()  # no goal/decisions/tasks/questions
        assert extractor.build_prompt(
            conversation, existing_context=[empty_context]
        ) == extractor.build_prompt(conversation, existing_context=None)

    def test_no_existing_context_section_when_omitted(self) -> None:
        extractor = Extractor(llm=_CapturingLLM())
        prompt = extractor.build_prompt(make_conversation())
        assert "EXISTING CONTEXT" not in prompt
        assert "NEW EVIDENCE" not in prompt
        assert "Conversation:\n" in prompt

    def test_extract_behaviour_unaffected_when_omitted(self) -> None:
        llm = _CapturingLLM()
        extractor = Extractor(llm=llm)
        conversation = make_conversation()

        facts_without_kw = extractor.extract(conversation)
        facts_with_explicit_none = extractor.extract(
            conversation, existing_context=None
        )

        assert facts_without_kw == facts_with_explicit_none
        assert llm.prompts[0] == llm.prompts[1]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRendering:
    def test_section_headers_present_when_context_has_content(self) -> None:
        extractor = Extractor(llm=_CapturingLLM())
        context = make_context(title="Haven", goal="The user's goal is to ship PR 4.")
        prompt = extractor.build_prompt(make_conversation(), existing_context=[context])

        assert "EXISTING CONTEXT" in prompt
        assert "NEW EVIDENCE" in prompt
        assert prompt.index("EXISTING CONTEXT") < prompt.index("NEW EVIDENCE")

    def test_goal_rendered(self) -> None:
        extractor = Extractor(llm=_CapturingLLM())
        context = make_context(goal="The user's goal is to ship PR 4.")
        prompt = extractor.build_prompt(make_conversation(), existing_context=[context])
        assert "The user's goal is to ship PR 4." in prompt

    def test_decisions_tasks_questions_rendered(self) -> None:
        extractor = Extractor(llm=_CapturingLLM())
        context = make_context(
            decisions=("The user decided to use FastAPI.",),
            tasks=("The user is working on the checkpoint diff module.",),
            questions=("Should PR 5 add semantic matching?",),
        )
        prompt = extractor.build_prompt(make_conversation(), existing_context=[context])
        assert "The user decided to use FastAPI." in prompt
        assert "The user is working on the checkpoint diff module." in prompt
        assert "Should PR 5 add semantic matching?" in prompt

    def test_new_evidence_conversation_text_present(self) -> None:
        extractor = Extractor(llm=_CapturingLLM())
        context = make_context(goal="The user's goal is to ship PR 4.")
        conversation = make_conversation("I also use Zsh now.")
        prompt = extractor.build_prompt(conversation, existing_context=[context])

        new_evidence_index = prompt.index("NEW EVIDENCE")
        conversation_index = prompt.index("I also use Zsh now.")
        assert conversation_index > new_evidence_index

    def test_context_with_no_content_is_skipped_among_others(self) -> None:
        extractor = Extractor(llm=_CapturingLLM())
        populated = make_context(title="Haven", goal="The user's goal is to ship PR 4.")
        empty = make_context(title="Empty")
        prompt = extractor.build_prompt(
            make_conversation(), existing_context=[empty, populated]
        )
        assert "- Haven:" in prompt
        assert "- Empty:" not in prompt

    def test_multiple_contexts_all_rendered(self) -> None:
        extractor = Extractor(llm=_CapturingLLM())
        haven = make_context(title="Haven", goal="Ship PR 4.")
        mit = make_context(title="MIT Application", goal="Apply to MIT.")
        prompt = extractor.build_prompt(
            make_conversation(), existing_context=[haven, mit]
        )
        assert "Ship PR 4." in prompt
        assert "Apply to MIT." in prompt


# ---------------------------------------------------------------------------
# Instruction language
# ---------------------------------------------------------------------------


class TestInstructions:
    def test_never_extract_solely_because_present_instruction(self) -> None:
        extractor = Extractor(llm=_CapturingLLM())
        context = make_context(goal="The user's goal is to ship PR 4.")
        prompt = extractor.build_prompt(make_conversation(), existing_context=[context])
        assert "Never extract a fact solely because it appears in Existing Context" in prompt

    def test_only_extract_facts_supported_by_new_evidence_instruction(self) -> None:
        extractor = Extractor(llm=_CapturingLLM())
        context = make_context(goal="The user's goal is to ship PR 4.")
        prompt = extractor.build_prompt(make_conversation(), existing_context=[context])
        assert "Only extract facts that are supported by the NEW EVIDENCE" in prompt

    def test_confirm_update_contradict_elaborate_instruction(self) -> None:
        extractor = Extractor(llm=_CapturingLLM())
        context = make_context(goal="The user's goal is to ship PR 4.")
        prompt = extractor.build_prompt(make_conversation(), existing_context=[context])
        assert "confirms, updates, contradicts, or elaborates" in prompt


# ---------------------------------------------------------------------------
# Threading through extract()
# ---------------------------------------------------------------------------


class TestThreadedThroughExtract:
    def test_existing_context_reaches_the_prompt_sent_to_llm(self) -> None:
        llm = _CapturingLLM()
        extractor = Extractor(llm=llm)
        context = make_context(goal="The user's goal is to ship PR 4.")

        extractor.extract(make_conversation(), existing_context=[context])

        assert "EXISTING CONTEXT" in llm.prompts[0]
        assert "The user's goal is to ship PR 4." in llm.prompts[0]

    def test_no_existing_context_reaches_llm_when_omitted(self) -> None:
        llm = _CapturingLLM()
        extractor = Extractor(llm=llm)

        extractor.extract(make_conversation())

        assert "EXISTING CONTEXT" not in llm.prompts[0]


# ---------------------------------------------------------------------------
# extract_with_trace() -- the Write Inspector's source of prompt/raw output
# ---------------------------------------------------------------------------


class TestExtractWithTrace:
    def test_facts_match_extract(self) -> None:
        llm = _CapturingLLM()  # always returns "[]"
        extractor = Extractor(llm=llm)
        conversation = make_conversation()

        facts = extractor.extract(conversation)
        trace = extractor.extract_with_trace(conversation)

        assert trace.facts == facts

    def test_trace_carries_prompt_and_raw_response(self) -> None:
        llm = _CapturingLLM()
        extractor = Extractor(llm=llm)
        context = make_context(goal="The user's goal is to ship PR 4.")

        trace = extractor.extract_with_trace(
            make_conversation(), existing_context=[context]
        )

        assert trace.prompt == llm.prompts[0]
        assert "EXISTING CONTEXT" in trace.prompt
        assert trace.raw_response == "[]"

    def test_extract_is_a_thin_wrapper_same_prompt(self) -> None:
        llm = _CapturingLLM()
        extractor = Extractor(llm=llm)
        conversation = make_conversation()

        extractor.extract(conversation)
        extractor.extract_with_trace(conversation)

        assert llm.prompts[0] == llm.prompts[1]
