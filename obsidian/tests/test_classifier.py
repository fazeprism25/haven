"""Unit tests for the Classifier stage (V2 ontology: domain-grouped prompt + topics).

No dedicated test file existed for ``Classifier`` before this (see
``obsidian/docs/KNOWN_ISSUES.md``'s "No direct unit tests for several
Manager AI stages") -- this is new coverage, not a fix-up. Follows
``test_extractor_existing_context.py``'s scripted-fake-LLM convention.

Test groups
-----------
TestDomainGroupedPrompt   -- build_prompt lists every MemoryType grouped by
                             domain, with disambiguation guidance, and the
                             topic-tagging instructions.
TestTopicsOptional        -- a response without a "topics" key still
                             validates (backward compatible with any
                             pre-V2 cached/replayed response).
TestTopicsValidation      -- malformed "topics" triggers the same one-shot
                             repair-retry-or-skip contract memory_type
                             already has.
TestMotivatingExamples    -- the four scenarios from the ontology redesign
                             request classify into the intended type, and
                             topics survive canonicalization end to end.
"""

from __future__ import annotations

from typing import List

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.classifier import Classifier, ClassificationError
from obsidian.manager_ai.models import ExtractedFact


class _FakeLLM:
    """Returns each queued response in order, one per ``generate`` call."""

    def __init__(self, responses: List[str]) -> None:
        self._responses = list(responses)
        self.calls: List[str] = []

    def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        return self._responses.pop(0)


def _fact(text: str) -> ExtractedFact:
    return ExtractedFact(text=text, evidence="stated", confidence=0.85)


# ---------------------------------------------------------------------------
# TestDomainGroupedPrompt
# ---------------------------------------------------------------------------


class TestDomainGroupedPrompt:
    def test_lists_every_memory_type(self) -> None:
        classifier = Classifier(llm=_FakeLLM([]))
        prompt = classifier.build_prompt(_fact("The user prefers dark mode."))
        for member in MemoryType:
            assert member.value in prompt

    def test_groups_by_domain_headings(self) -> None:
        classifier = Classifier(llm=_FakeLLM([]))
        prompt = classifier.build_prompt(_fact("The user prefers dark mode."))
        assert "Personal:" in prompt
        assert "Work:" in prompt
        assert "Knowledge:" in prompt
        # Personal section appears before Work, which appears before Knowledge.
        assert prompt.index("Personal:") < prompt.index("Work:") < prompt.index("Knowledge:")

    def test_includes_disambiguation_guidance(self) -> None:
        classifier = Classifier(llm=_FakeLLM([]))
        prompt = classifier.build_prompt(_fact("The user prefers dark mode."))
        assert "watching" in prompt  # interest guidance
        assert "do not default" in prompt.lower() or "preference" in prompt.lower()

    def test_includes_topic_instructions(self) -> None:
        classifier = Classifier(llm=_FakeLLM([]))
        prompt = classifier.build_prompt(_fact("The user prefers dark mode."))
        assert "topics" in prompt
        assert "AI" in prompt  # seed vocabulary example

    def test_repair_prompt_also_includes_topic_instructions(self) -> None:
        classifier = Classifier(llm=_FakeLLM([]))
        prompt = classifier.build_repair_prompt(
            _fact("x"), previous_response="not json", error=ValueError("bad")
        )
        assert "topics" in prompt


# ---------------------------------------------------------------------------
# TestTopicsOptional
# ---------------------------------------------------------------------------


class TestTopicsOptional:
    def test_response_without_topics_key_still_validates(self) -> None:
        llm = _FakeLLM(
            [
                '{"memory_type": "fact", "confidence": 0.9, "reason": "stated"}'
            ]
        )
        result = Classifier(llm=llm).classify(_fact("The user uses Obsidian."))
        assert result.memory_type == MemoryType.FACT
        assert result.topics == ()

    def test_response_with_empty_topics_list_yields_no_topics(self) -> None:
        llm = _FakeLLM(
            [
                '{"memory_type": "fact", "confidence": 0.9, "reason": "stated", "topics": []}'
            ]
        )
        result = Classifier(llm=llm).classify(_fact("The user uses Obsidian."))
        assert result.topics == ()


# ---------------------------------------------------------------------------
# TestTopicsValidation
# ---------------------------------------------------------------------------


class TestTopicsValidation:
    def test_topics_not_a_list_triggers_repair_then_succeeds(self) -> None:
        llm = _FakeLLM(
            [
                '{"memory_type": "fact", "confidence": 0.9, "reason": "x", "topics": "AI"}',
                '{"memory_type": "fact", "confidence": 0.9, "reason": "x", "topics": []}',
            ]
        )
        result = Classifier(llm=llm).classify(_fact("x"))
        assert result.memory_type == MemoryType.FACT
        assert len(llm.calls) == 2

    def test_too_many_topics_triggers_repair(self) -> None:
        too_many = ", ".join(
            f'{{"topic": "t{i}", "confidence": 0.5}}' for i in range(5)
        )
        llm = _FakeLLM(
            [
                (
                    '{"memory_type": "fact", "confidence": 0.9, "reason": "x", '
                    f'"topics": [{too_many}]}}'
                ),
                '{"memory_type": "fact", "confidence": 0.9, "reason": "x", "topics": []}',
            ]
        )
        result = Classifier(llm=llm).classify(_fact("x"))
        assert result.memory_type == MemoryType.FACT

    def test_topic_entry_missing_topic_key_triggers_repair(self) -> None:
        llm = _FakeLLM(
            [
                (
                    '{"memory_type": "fact", "confidence": 0.9, "reason": "x", '
                    '"topics": [{"confidence": 0.5}]}'
                ),
                '{"memory_type": "fact", "confidence": 0.9, "reason": "x", "topics": []}',
            ]
        )
        result = Classifier(llm=llm).classify(_fact("x"))
        assert result.memory_type == MemoryType.FACT

    def test_still_bad_after_repair_raises_classification_error(self) -> None:
        llm = _FakeLLM(
            [
                '{"memory_type": "not_real", "confidence": 0.9, "reason": "x"}',
                '{"memory_type": "still_not_real", "confidence": 0.9, "reason": "x"}',
            ]
        )
        try:
            Classifier(llm=llm).classify(_fact("x"))
        except ClassificationError:
            pass
        else:  # pragma: no cover - fail path
            raise AssertionError("expected ClassificationError")


# ---------------------------------------------------------------------------
# TestMotivatingExamples
# ---------------------------------------------------------------------------


class TestMotivatingExamples:
    """The four concrete examples from the ontology redesign request.

    A unit test with a scripted LLM cannot verify semantic judgment (that
    is what the LLM itself is for) -- what it verifies is that when the
    LLM *does* choose the intended type/topics, the Classifier faithfully
    parses, validates, and canonicalizes them end to end, without
    silently collapsing everything to PREFERENCE the way the old flat
    enum listing invited.
    """

    def test_watching_llamaindex_is_interest_tagged_ai(self) -> None:
        llm = _FakeLLM(
            [
                '{"memory_type": "interest", "confidence": 0.85, '
                '"reason": "The user is following a technology, not a settled preference.", '
                '"topics": [{"topic": "AI", "confidence": 0.8}]}'
            ]
        )
        result = Classifier(llm=llm).classify(
            _fact("The user is watching LlamaIndex.")
        )
        assert result.memory_type == MemoryType.INTEREST
        assert result.topics[0].name == "AI"

    def test_watching_local_ai_is_interest(self) -> None:
        llm = _FakeLLM(
            [
                '{"memory_type": "interest", "confidence": 0.8, "reason": "watching", '
                '"topics": [{"topic": "artificial intelligence", "confidence": 0.7}]}'
            ]
        )
        result = Classifier(llm=llm).classify(_fact("The user is watching local AI."))
        assert result.memory_type == MemoryType.INTEREST
        # "artificial intelligence" canonicalizes to the same "AI" as the
        # seed vocabulary's display name.
        assert result.topics[0].name == "AI"

    def test_interested_in_research_papers_is_interest(self) -> None:
        llm = _FakeLLM(
            [
                '{"memory_type": "interest", "confidence": 0.75, "reason": "curious", '
                '"topics": [{"topic": "AI", "confidence": 0.6}]}'
            ]
        )
        result = Classifier(llm=llm).classify(
            _fact("The user is interested in research papers on retrieval.")
        )
        assert result.memory_type == MemoryType.INTEREST

    def test_likes_building_systems_is_trait(self) -> None:
        llm = _FakeLLM(
            [
                '{"memory_type": "trait", "confidence": 0.8, '
                '"reason": "An enduring disposition, not a one-off preference.", '
                '"topics": [{"topic": "Programming", "confidence": 0.6}]}'
            ]
        )
        result = Classifier(llm=llm).classify(
            _fact("The user likes building systems from scratch.")
        )
        assert result.memory_type == MemoryType.TRAIT
        assert result.topics[0].name == "Programming"
