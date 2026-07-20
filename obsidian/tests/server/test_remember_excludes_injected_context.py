"""Regression tests: "Remember" must never re-propose a memory that "Use
Haven" itself just injected into the conversation.

Bug being guarded against
--------------------------
"Use Haven" replaces the compose box outright with a structured prompt that
embeds every retrieved memory under ``<HavenContext>`` (see
``obsidian.memory_engine.structured_prompt_builder.StructuredPromptBuilder``).
Once the user sends that message, it becomes one literal turn in the visible
conversation, and "Remember" (``extension/content/adapters/chatgpt.js``'s
``getConversationTurns``) scrapes it back out verbatim as evidence. Left
unstripped, those already-known memories reach the Extractor phrased in the
exact canonical templates it's told to produce (e.g. "The user decided to
..."), so it re-extracts them as if the user had just typed them --
proposing memories that already exist in Haven.

``obsidian.server.main._strip_injected_haven_context`` (applied via
``_turns_from_request``, shared by ``save_memory``/``preview_memory``)
fixes this by reducing a turn that matches Haven's own injected shape down
to just its ``<UserRequest>`` text before it ever reaches the Extractor.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.manager_ai.pipeline import ManagerPipeline
from obsidian.memory_engine.structured_prompt_builder import StructuredPromptBuilder
from obsidian.ontology.retrieval_models import (
    Candidate,
    ContextKind,
    ContextStatus,
    RankedCandidate,
    WorkingContext,
    WorkingContextState,
)
from obsidian.server.main import _strip_injected_haven_context


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))
    monkeypatch.setenv("HAVEN_WRITE_TRACE_DIR", str(tmp_path / "write_traces"))

    from obsidian.server.main import app

    with TestClient(app) as test_client:
        yield test_client


def _preview(client: TestClient, **payload) -> dict:
    response = client.post("/api/v1/memory/preview", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _injected_prompt(user_request: str) -> str:
    """A structured prompt exactly as "Use Haven" would inject it.

    Carries one already-known memory ("The user's goal is to ship PR 4.")
    under ``<HavenContext>``, phrased in the extractor's own canonical
    template -- the shape most likely to fool a naive extraction.
    """
    ko = KnowledgeObject(
        id=uuid4(),
        canonical_fact="The user's goal is to ship PR 4.",
        memory_type=MemoryType.GOAL,
        confidence=0.9,
        importance=0.7,
        confirmation_count=1,
        valid_from=datetime(2026, 7, 1),
        valid_until=None,
    )
    candidate = Candidate(
        knowledge_object=ko,
        supporting_concepts=(),
        attachment_relevance=0.0,
        activation_score=0.0,
    )
    ranked = RankedCandidate(
        candidate=candidate, final_score=0.8, score_breakdown={"importance": 0.7}
    )
    state = WorkingContextState(
        status=ContextStatus.ACTIVE,
        current_goal=ranked,
        recent_decisions=(),
        pending_tasks=(),
        open_questions=(),
    )
    context = WorkingContext(
        key="ctx:Haven", title="Haven", kind=ContextKind.PROJECT, state=state, buckets=()
    )
    return StructuredPromptBuilder().render([context], user_request)


# ---------------------------------------------------------------------------
# Unit tests: _strip_injected_haven_context
# ---------------------------------------------------------------------------


class TestStripInjectedHavenContext:
    def test_recovers_the_original_user_request(self) -> None:
        injected = _injected_prompt("What should I work on next?")
        assert _strip_injected_haven_context(injected) == "What should I work on next?"

    def test_recovers_a_multiline_user_request(self) -> None:
        request = "Line one.\nLine two."
        injected = _injected_prompt(request)
        assert _strip_injected_haven_context(injected) == request

    def test_unrelated_text_is_returned_unchanged(self) -> None:
        text = "I use Terraform and I live in Muscat."
        assert _strip_injected_haven_context(text) == text

    def test_partial_lookalike_is_returned_unchanged(self) -> None:
        # A user manually typing/editing something that merely starts with
        # "<System>" without the full injected shape must not be rewritten.
        text = "<System>\nI was just talking about XML.\n</System>"
        assert _strip_injected_haven_context(text) == text

    def test_xml_special_characters_round_trip(self) -> None:
        request = "Is 2 < 3 and 5 > 4? Also A & B."
        injected = _injected_prompt(request)
        assert _strip_injected_haven_context(injected) == request


# ---------------------------------------------------------------------------
# Server-level tests: /memory/preview
# ---------------------------------------------------------------------------


class _CapturingExtractLLM:
    """Captures every prompt sent to the Extractor; always returns *extract_response*.

    classify/importance calls raise -- if either fires, more was extracted
    than the test expected.
    """

    def __init__(self, extract_response: str = "[]") -> None:
        self.extract_response = extract_response
        self.extract_prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        if "Conversation:\n" in prompt:
            self.extract_prompts.append(prompt)
            return self.extract_response
        raise AssertionError(
            f"Classifier/ImportanceScorer must not run when nothing is "
            f"extracted:\n{prompt}"
        )


def _install_llm(client: TestClient, llm) -> None:
    client.app.state.manager_pipeline = ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )


def test_conversation_with_no_new_information_proposes_nothing(
    client: TestClient,
) -> None:
    injected = _injected_prompt("What should I work on next?")
    llm = _CapturingExtractLLM(extract_response="[]")
    _install_llm(client, llm)

    body = _preview(client, conversation=[{"role": "user", "content": injected}])

    assert body["status"] == "ok"
    assert body["items"] == []
    assert len(llm.extract_prompts) == 1
    prompt = llm.extract_prompts[0]
    assert "HavenContext" not in prompt
    assert "The user's goal is to ship PR 4." not in prompt
    assert "What should I work on next?" in prompt


def test_new_decision_after_injected_context_is_still_extracted(
    client: TestClient,
) -> None:
    import json

    injected = _injected_prompt("What should I work on next?")
    conversation = [
        {"role": "user", "content": injected},
        {"role": "assistant", "content": "You could finish PR 4 or start the DB migration."},
        {"role": "user", "content": "Actually, I've decided to use Postgres instead of SQLite."},
    ]

    extract_response = json.dumps(
        [
            {
                "text": "The user decided to use Postgres instead of SQLite.",
                "evidence": "stated",
                "confidence": 0.9,
            }
        ]
    )

    class _FullLLM(_CapturingExtractLLM):
        def generate(self, prompt: str) -> str:
            if "Conversation:\n" in prompt:
                self.extract_prompts.append(prompt)
                return self.extract_response
            if "Available memory types:" in prompt:
                return json.dumps(
                    {"memory_type": "decision", "confidence": 0.9, "reason": "stated"}
                )
            if "Classification:\n" in prompt:
                return json.dumps({"score": 0.7, "reason": "scored"})
            raise AssertionError(f"Unrecognised prompt shape:\n{prompt}")

    llm = _FullLLM(extract_response=extract_response)
    _install_llm(client, llm)

    body = _preview(client, conversation=conversation)

    assert body["status"] == "ok"
    assert len(body["items"]) == 1
    assert body["items"][0]["text"] == (
        "The user decided to use Postgres instead of SQLite."
    )

    assert len(llm.extract_prompts) == 1
    prompt = llm.extract_prompts[0]
    assert "HavenContext" not in prompt
    assert "The user's goal is to ship PR 4." not in prompt
    assert "Actually, I've decided to use Postgres instead of SQLite." in prompt
