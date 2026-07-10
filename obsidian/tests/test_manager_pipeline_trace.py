"""Unit tests for ManagerPipeline.process_with_trace and ExtractionDecision.decision.

Test groups
-----------
TestDecisionField      -- ExtractionDecision.decision is populated with the
                          CanonicalMatcher's raw decision for NEW and CONFIRM.
TestProcessWithTrace    -- process_with_trace() returns the same decisions as
                          process(), plus an ExtractionTrace carrying the
                          prompt/raw_response/facts process() itself discards.
TestPipelineVersion     -- PIPELINE_VERSION is a plain int constant.
"""

from __future__ import annotations

import json
from typing import Sequence

from obsidian.core.enums import Role, SourceType
from obsidian.core.types import Conversation, Event
from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.models import KnowledgeDecision
from obsidian.manager_ai.pipeline import PIPELINE_VERSION, ManagerPipeline


class _ScriptedLLM:
    """Same prompt-shape dispatch used by the server-level tests' fakes."""

    def __init__(
        self,
        extract_response: str,
        classify_responses: Sequence[str] = (),
        importance_responses: Sequence[str] = (),
    ) -> None:
        self._extract_response = extract_response
        self._classify_responses = list(classify_responses)
        self._importance_responses = list(importance_responses)

    def generate(self, prompt: str) -> str:
        if "Conversation:\n" in prompt:
            return self._extract_response
        if "Available memory types:" in prompt:
            return self._classify_responses.pop(0)
        if "Classification:\n" in prompt:
            return self._importance_responses.pop(0)
        raise AssertionError(f"Unrecognised prompt shape:\n{prompt}")


def _build_pipeline(llm) -> ManagerPipeline:
    return ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )


def _conversation(text: str) -> Conversation:
    return Conversation(
        title="Remember",
        source=SourceType.MANUAL,
        events=[Event(role=Role.USER, content=text, source=SourceType.MANUAL)],
    )


_EXTRACT_ONE_FACT = json.dumps(
    [{"text": "The user lives in Muscat.", "evidence": "stated directly", "confidence": 0.9}]
)
_CLASSIFY_FACT = json.dumps({"memory_type": "fact", "confidence": 0.9, "reason": "identity"})
_IMPORTANCE_FACT = json.dumps({"score": 0.7, "reason": "durable"})


# ---------------------------------------------------------------------------
# TestDecisionField
# ---------------------------------------------------------------------------


class TestDecisionField:
    def test_new_fact_decision_is_new(self) -> None:
        llm = _ScriptedLLM(_EXTRACT_ONE_FACT, [_CLASSIFY_FACT], [_IMPORTANCE_FACT])
        pipeline = _build_pipeline(llm)

        decisions = pipeline.process(_conversation("I live in Muscat."), existing_knowledge=[])

        assert len(decisions) == 1
        assert decisions[0].decision == KnowledgeDecision.NEW
        assert decisions[0].knowledge is not None

    def test_confirm_fact_decision_is_confirm(self) -> None:
        llm = _ScriptedLLM(
            _EXTRACT_ONE_FACT,
            [_CLASSIFY_FACT, _CLASSIFY_FACT],
            [_IMPORTANCE_FACT, _IMPORTANCE_FACT],
        )
        pipeline = _build_pipeline(llm)

        # First call creates the knowledge object...
        first = pipeline.process(_conversation("I live in Muscat."), existing_knowledge=[])
        existing = [d.knowledge for d in first if d.knowledge is not None]

        # ...second call with the same fact against that existing knowledge
        # should CONFIRM rather than create a new one.
        second = pipeline.process(
            _conversation("I live in Muscat."), existing_knowledge=existing
        )

        assert len(second) == 1
        assert second[0].decision == KnowledgeDecision.CONFIRM
        assert second[0].knowledge is not None
        assert second[0].knowledge.id == existing[0].id


# ---------------------------------------------------------------------------
# TestProcessWithTrace
# ---------------------------------------------------------------------------


class TestProcessWithTrace:
    def test_decisions_match_process(self) -> None:
        llm_a = _ScriptedLLM(_EXTRACT_ONE_FACT, [_CLASSIFY_FACT], [_IMPORTANCE_FACT])
        llm_b = _ScriptedLLM(_EXTRACT_ONE_FACT, [_CLASSIFY_FACT], [_IMPORTANCE_FACT])

        decisions_a = _build_pipeline(llm_a).process(
            _conversation("I live in Muscat."), existing_knowledge=[]
        )
        decisions_b, extraction_trace = _build_pipeline(llm_b).process_with_trace(
            _conversation("I live in Muscat."), existing_knowledge=[]
        )

        assert len(decisions_a) == len(decisions_b) == 1
        assert decisions_a[0].decision == decisions_b[0].decision
        assert decisions_a[0].fact.text == decisions_b[0].fact.text

    def test_extraction_trace_carries_prompt_and_raw_response(self) -> None:
        llm = _ScriptedLLM(_EXTRACT_ONE_FACT, [_CLASSIFY_FACT], [_IMPORTANCE_FACT])
        _decisions, extraction_trace = _build_pipeline(llm).process_with_trace(
            _conversation("I live in Muscat."), existing_knowledge=[]
        )

        assert "Conversation:\n" in extraction_trace.prompt
        assert extraction_trace.raw_response == _EXTRACT_ONE_FACT
        assert len(extraction_trace.facts) == 1
        assert extraction_trace.facts[0].text == "The user lives in Muscat."

    def test_zero_facts_extracted(self) -> None:
        llm = _ScriptedLLM("[]")
        decisions, extraction_trace = _build_pipeline(llm).process_with_trace(
            _conversation("nothing memorable"), existing_knowledge=[]
        )

        assert decisions == []
        assert extraction_trace.facts == []
        assert extraction_trace.raw_response == "[]"


# ---------------------------------------------------------------------------
# TestPipelineVersion
# ---------------------------------------------------------------------------


class TestPipelineVersion:
    def test_pipeline_version_is_an_int(self) -> None:
        assert isinstance(PIPELINE_VERSION, int)
        assert PIPELINE_VERSION >= 1


# ---------------------------------------------------------------------------
# TestUpdateWiring
# ---------------------------------------------------------------------------


class TestUpdateWiring:
    """match_and_apply drives UPDATE in place, exercised without the LLM.

    The LLM stages are never invoked by match_and_apply, so the extractor/
    classifier/importance_scorer can be ``None`` here -- this isolates the
    deterministic CanonicalMatcher/KnowledgeUpdater pair.
    """

    def _pipeline(self) -> ManagerPipeline:
        return ManagerPipeline(
            extractor=None,
            classifier=None,
            importance_scorer=None,
            canonical_matcher=CanonicalMatcher(),
            knowledge_updater=KnowledgeUpdater(),
        )

    def _scored(self, text: str):
        from obsidian.core.enums import MemoryType
        from obsidian.manager_ai.models import (
            ClassificationResult,
            ExtractedFact,
            ImportanceResult,
        )

        return (
            ExtractedFact(text=text, confidence=0.9),
            ClassificationResult(memory_type=MemoryType.FACT, confidence=0.9),
            ImportanceResult(score=0.5),
        )

    def test_refinement_updates_in_place(self) -> None:
        from obsidian.manager_ai.models import KnowledgeObject

        base = KnowledgeObject(canonical_fact="I work at Google", confidence=0.6)
        existing = [base]

        decisions = self._pipeline().match_and_apply(
            [self._scored("I work at Google as a staff engineer")], existing
        )

        assert len(decisions) == 1
        d = decisions[0]
        assert d.decision == KnowledgeDecision.UPDATE
        assert d.knowledge is not None
        # id preserved, canonical_fact overwritten.
        assert d.knowledge.id == base.id
        assert d.knowledge.canonical_fact == "I work at Google as a staff engineer"
        # existing was refined in place (same id, no new entry appended).
        assert len(existing) == 1
        assert existing[0].id == base.id
        assert existing[0].canonical_fact == "I work at Google as a staff engineer"

    def test_update_populates_supersession_provenance(self) -> None:
        from obsidian.manager_ai.models import (
            KnowledgeObject,
            SupersessionOperation,
        )

        base = KnowledgeObject(canonical_fact="I work at Google", confidence=0.6)
        decisions = self._pipeline().match_and_apply(
            [self._scored("I work at Google as a staff engineer")], [base]
        )

        supersession = decisions[0].supersession
        assert supersession is not None
        assert supersession.operation == SupersessionOperation.UPDATE
        assert supersession.matched_identity == base.id
        # The reason carries the previous text so the write trace fully
        # explains what changed.
        assert "I work at Google" in supersession.reason

    def test_new_and_confirm_carry_no_supersession(self) -> None:
        from obsidian.manager_ai.models import KnowledgeObject

        pipeline = self._pipeline()
        existing: list = []

        # NEW
        new_decisions = pipeline.match_and_apply(
            [self._scored("I live in Muscat")], existing
        )
        assert new_decisions[0].decision == KnowledgeDecision.NEW
        assert new_decisions[0].supersession is None

        # CONFIRM against the just-created object.
        confirm_decisions = pipeline.match_and_apply(
            [self._scored("I live in Muscat")], existing
        )
        assert confirm_decisions[0].decision == KnowledgeDecision.CONFIRM
        assert confirm_decisions[0].supersession is None
