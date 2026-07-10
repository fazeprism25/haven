"""Tests for PR 4 -- incremental ingestion with Working Context.

Companion to ``test_save_memory_checkpoint.py`` (same fixtures/scripted-LLM
harness, duplicated here rather than imported so this file stays
self-contained). These tests cover only what PR 4 adds on top of PR 3's
checkpoint wiring:

* ``TestFirstRun`` -- the very first save for a conversation sends every
  turn as evidence and never queries Working Context (nothing in the vault
  yet to contextualise).
* ``TestIncrementalAppend`` -- a clean append sends *only* the new turns to
  the Extractor, not the whole conversation, and the checkpoint's
  processing history records ``mode="incremental"`` with the correct
  ``turn_range``.
* ``TestFallbackAfterEditedEarlierTurn`` -- editing a turn at or before the
  last processed index breaks the append invariant: the whole conversation
  is resent as evidence (exactly like today's full-reprocess behaviour),
  with no Working Context, and the checkpoint records ``mode="fallback"``.
* ``TestWorkingContextInPrompt`` -- an incremental save's prompt carries an
  ``EXISTING CONTEXT`` section built from the vault's existing knowledge
  when there is something relevant to surface, and omits it entirely when
  there is nothing to show -- while first-run/fallback saves never carry
  the section at all.
* ``TestNoPR3Regression`` -- duplicate short-circuiting and the
  external_key-absent path are untouched by any of the above.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from fastapi.testclient import TestClient
import pytest

from obsidian.checkpoint.identity import derive_conversation_id
from obsidian.checkpoint.store import CheckpointStore
from obsidian.core.enums import SourceType
from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.pipeline import ManagerPipeline


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))
    monkeypatch.setenv("HAVEN_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("HAVEN_WRITE_TRACE_DIR", str(tmp_path / "write_traces"))

    from obsidian.server.main import app

    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Scripted fake LLM -- same shape as test_save_memory_checkpoint.py's, plus
# prompt capture so tests can assert what the Extractor actually saw.
# ---------------------------------------------------------------------------


class _CapturingScriptedLLM:
    def __init__(
        self,
        extract_response: str,
        classify_responses: Sequence[str] = (),
        importance_responses: Sequence[str] = (),
    ) -> None:
        self._extract_response = extract_response
        self._classify_responses = list(classify_responses)
        self._importance_responses = list(importance_responses)
        self.call_count = 0
        self.prompts: list = []

    def generate(self, prompt: str) -> str:
        self.call_count += 1
        self.prompts.append(prompt)
        if "Conversation:\n" in prompt:
            return self._extract_response
        if "Available memory types:" in prompt:
            return self._classify_responses.pop(0)
        if "Classification:\n" in prompt:
            return self._importance_responses.pop(0)
        raise AssertionError(f"Unrecognised prompt shape:\n{prompt}")

    @property
    def extractor_prompts(self) -> list:
        return [p for p in self.prompts if "Conversation:\n" in p]


def _install_scripted_llm(
    client: TestClient,
    extract_response: str,
    classify_responses: Sequence[str] = (),
    importance_responses: Sequence[str] = (),
) -> _CapturingScriptedLLM:
    llm = _CapturingScriptedLLM(extract_response, classify_responses, importance_responses)
    client.app.state.manager_pipeline = ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )
    return llm


def _extract_json(facts: Sequence[tuple]) -> str:
    return json.dumps(
        [
            {"text": text, "evidence": evidence, "confidence": confidence}
            for text, evidence, confidence in facts
        ]
    )


def _classify_json(memory_type: str, confidence: float = 0.9, reason: str = "stated") -> str:
    return json.dumps({"memory_type": memory_type, "confidence": confidence, "reason": reason})


def _importance_json(score: float, reason: str = "scored") -> str:
    return json.dumps({"score": score, "reason": reason})


_GOAL_CONVERSATION = [
    {"role": "user", "content": "I want to apply to MIT."},
    {"role": "assistant", "content": "That's a great goal! What's your plan?"},
]


def _install_goal_llm(client: TestClient) -> _CapturingScriptedLLM:
    return _install_scripted_llm(
        client,
        extract_response=_extract_json(
            [("The user's goal is to apply to MIT.", "stated in conversation", 0.9)]
        ),
        classify_responses=[_classify_json("goal")],
        importance_responses=[_importance_json(0.7)],
    )


def _get_checkpoint(client: TestClient, external_key: str, source: SourceType = SourceType.MANUAL):
    store = CheckpointStore(client.app.state.checkpoint_dir)
    store.load()
    conversation_id = derive_conversation_id(source, external_key)
    return store.get(conversation_id)


# ---------------------------------------------------------------------------
# TestFirstRun
# ---------------------------------------------------------------------------


class TestFirstRun:
    def test_first_run_sends_every_turn_as_evidence(self, client: TestClient) -> None:
        llm = _install_goal_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/first"},
        )
        [prompt] = llm.extractor_prompts
        assert "I want to apply to MIT." in prompt
        assert "That's a great goal! What's your plan?" in prompt

    def test_first_run_never_queries_working_context(self, client: TestClient) -> None:
        llm = _install_goal_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/first"},
        )
        [prompt] = llm.extractor_prompts
        assert "EXISTING CONTEXT" not in prompt

    def test_first_run_checkpoint_records_mode(self, client: TestClient) -> None:
        _install_goal_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/first"},
        )
        checkpoint = _get_checkpoint(client, "/c/first")
        last_run = checkpoint.processing_history[-1]
        assert last_run.mode == "first_run"
        assert last_run.turn_range == (0, len(_GOAL_CONVERSATION))


# ---------------------------------------------------------------------------
# TestIncrementalAppend
# ---------------------------------------------------------------------------


class TestIncrementalAppend:
    def test_only_new_turns_reach_the_extractor(self, client: TestClient) -> None:
        _install_goal_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/growing"},
        )

        grown = _GOAL_CONVERSATION + [
            {"role": "user", "content": "Also, I prefer tea over coffee."}
        ]
        llm = _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user prefers tea over coffee.", "stated", 0.8)]
            ),
            classify_responses=[_classify_json("preference")],
            importance_responses=[_importance_json(0.5)],
        )
        response = client.post(
            "/api/v1/memory",
            json={"conversation": grown, "external_key": "/c/growing"},
        )
        assert response.json()["status"] == "success"

        [prompt] = llm.extractor_prompts
        assert "Also, I prefer tea over coffee." in prompt
        assert "I want to apply to MIT." not in prompt
        assert "That's a great goal! What's your plan?" not in prompt

    def test_checkpoint_records_incremental_mode_and_turn_range(
        self, client: TestClient
    ) -> None:
        _install_goal_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/growing"},
        )

        grown = _GOAL_CONVERSATION + [
            {"role": "user", "content": "Also, I prefer tea over coffee."}
        ]
        _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user prefers tea over coffee.", "stated", 0.8)]
            ),
            classify_responses=[_classify_json("preference")],
            importance_responses=[_importance_json(0.5)],
        )
        client.post(
            "/api/v1/memory",
            json={"conversation": grown, "external_key": "/c/growing"},
        )

        checkpoint = _get_checkpoint(client, "/c/growing")
        last_run = checkpoint.processing_history[-1]
        assert last_run.mode == "incremental"
        assert last_run.turn_range == (len(_GOAL_CONVERSATION), len(grown))

    def test_checkpoint_still_reflects_full_turn_list_not_just_evidence(
        self, client: TestClient
    ) -> None:
        _install_goal_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/growing"},
        )

        grown = _GOAL_CONVERSATION + [
            {"role": "user", "content": "Also, I prefer tea over coffee."}
        ]
        _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user prefers tea over coffee.", "stated", 0.8)]
            ),
            classify_responses=[_classify_json("preference")],
            importance_responses=[_importance_json(0.5)],
        )
        client.post(
            "/api/v1/memory",
            json={"conversation": grown, "external_key": "/c/growing"},
        )

        checkpoint = _get_checkpoint(client, "/c/growing")
        assert checkpoint.turn_count == len(grown)
        assert checkpoint.last_processed_turn_index == len(grown) - 1
        assert len(checkpoint.turn_hashes) == len(grown)

    def test_new_fact_is_saved_alongside_the_original(self, client: TestClient) -> None:
        from obsidian.memory_engine.memory_store import MemoryStore

        _install_goal_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/growing"},
        )

        grown = _GOAL_CONVERSATION + [
            {"role": "user", "content": "Also, I prefer tea over coffee."}
        ]
        _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user prefers tea over coffee.", "stated", 0.8)]
            ),
            classify_responses=[_classify_json("preference")],
            importance_responses=[_importance_json(0.5)],
        )
        client.post(
            "/api/v1/memory",
            json={"conversation": grown, "external_key": "/c/growing"},
        )

        store = MemoryStore(client.app.state.vault_dir)
        store.load()
        facts = {ko.canonical_fact for ko in store.all()}
        assert "The user's goal is to apply to MIT." in facts
        assert "The user prefers tea over coffee." in facts


# ---------------------------------------------------------------------------
# TestFallbackAfterEditedEarlierTurn
# ---------------------------------------------------------------------------


class TestFallbackAfterEditedEarlierTurn:
    def test_edited_earlier_turn_resends_every_turn_as_evidence(
        self, client: TestClient
    ) -> None:
        _install_goal_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/edited"},
        )

        edited = [
            {"role": "user", "content": "I want to apply to Stanford instead."},
            {"role": "assistant", "content": "That's a great goal! What's your plan?"},
        ]
        llm = _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user's goal is to apply to Stanford.", "stated", 0.9)]
            ),
            classify_responses=[_classify_json("goal")],
            importance_responses=[_importance_json(0.7)],
        )
        response = client.post(
            "/api/v1/memory",
            json={"conversation": edited, "external_key": "/c/edited"},
        )
        assert response.json()["status"] == "success"

        [prompt] = llm.extractor_prompts
        assert "I want to apply to Stanford instead." in prompt
        assert "That's a great goal! What's your plan?" in prompt

    def test_fallback_never_carries_existing_context(self, client: TestClient) -> None:
        _install_goal_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/edited"},
        )

        edited = [
            {"role": "user", "content": "I want to apply to Stanford instead."},
            {"role": "assistant", "content": "That's a great goal! What's your plan?"},
        ]
        llm = _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user's goal is to apply to Stanford.", "stated", 0.9)]
            ),
            classify_responses=[_classify_json("goal")],
            importance_responses=[_importance_json(0.7)],
        )
        client.post(
            "/api/v1/memory",
            json={"conversation": edited, "external_key": "/c/edited"},
        )
        [prompt] = llm.extractor_prompts
        assert "EXISTING CONTEXT" not in prompt

    def test_checkpoint_records_fallback_mode(self, client: TestClient) -> None:
        _install_goal_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/edited"},
        )

        edited = [
            {"role": "user", "content": "I want to apply to Stanford instead."},
            {"role": "assistant", "content": "That's a great goal! What's your plan?"},
        ]
        _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user's goal is to apply to Stanford.", "stated", 0.9)]
            ),
            classify_responses=[_classify_json("goal")],
            importance_responses=[_importance_json(0.7)],
        )
        client.post(
            "/api/v1/memory",
            json={"conversation": edited, "external_key": "/c/edited"},
        )

        checkpoint = _get_checkpoint(client, "/c/edited")
        last_run = checkpoint.processing_history[-1]
        assert last_run.mode == "fallback"
        assert last_run.turn_range == (0, len(edited))

    def test_truncated_conversation_is_also_a_fallback(self, client: TestClient) -> None:
        three_turns = _GOAL_CONVERSATION + [
            {"role": "user", "content": "Also, I prefer tea over coffee."}
        ]
        _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [
                    ("The user's goal is to apply to MIT.", "stated", 0.9),
                    ("The user prefers tea over coffee.", "stated", 0.8),
                ]
            ),
            classify_responses=[_classify_json("goal"), _classify_json("preference")],
            importance_responses=[_importance_json(0.7), _importance_json(0.5)],
        )
        client.post(
            "/api/v1/memory",
            json={"conversation": three_turns, "external_key": "/c/truncated"},
        )

        llm = _install_goal_llm(client)
        response = client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/truncated"},
        )
        assert response.json()["status"] == "success"

        checkpoint = _get_checkpoint(client, "/c/truncated")
        assert checkpoint.processing_history[-1].mode == "fallback"
        [prompt] = llm.extractor_prompts
        assert "I want to apply to MIT." in prompt


# ---------------------------------------------------------------------------
# TestWorkingContextInPrompt
# ---------------------------------------------------------------------------


class TestWorkingContextInPrompt:
    def test_incremental_prompt_surfaces_the_saved_goal(self, client: TestClient) -> None:
        _install_goal_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/context"},
        )

        grown = _GOAL_CONVERSATION + [
            {"role": "user", "content": "Also, I prefer tea over coffee."}
        ]
        llm = _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user prefers tea over coffee.", "stated", 0.8)]
            ),
            classify_responses=[_classify_json("preference")],
            importance_responses=[_importance_json(0.5)],
        )
        client.post(
            "/api/v1/memory",
            json={"conversation": grown, "external_key": "/c/context"},
        )

        [prompt] = llm.extractor_prompts
        assert "EXISTING CONTEXT" in prompt
        assert "The user's goal is to apply to MIT." in prompt
        assert "NEW EVIDENCE" in prompt

    def test_existing_context_never_becomes_a_new_knowledge_object(
        self, client: TestClient
    ) -> None:
        from obsidian.memory_engine.memory_store import MemoryStore

        _install_goal_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/context"},
        )

        grown = _GOAL_CONVERSATION + [
            {"role": "user", "content": "Also, I prefer tea over coffee."}
        ]
        _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user prefers tea over coffee.", "stated", 0.8)]
            ),
            classify_responses=[_classify_json("preference")],
            importance_responses=[_importance_json(0.5)],
        )
        client.post(
            "/api/v1/memory",
            json={"conversation": grown, "external_key": "/c/context"},
        )

        store = MemoryStore(client.app.state.vault_dir)
        store.load()
        goal_facts = [
            ko for ko in store.all() if ko.canonical_fact == "The user's goal is to apply to MIT."
        ]
        # Confirmed once by the first save; never re-created by the second.
        assert len(goal_facts) == 1


# ---------------------------------------------------------------------------
# TestNoPR3Regression
# ---------------------------------------------------------------------------


class TestNoPR3Regression:
    def test_duplicate_short_circuit_still_skips_the_pipeline_entirely(
        self, client: TestClient
    ) -> None:
        _install_goal_llm(client)
        first = client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/dup"},
        )
        assert first.json()["status"] == "success"

        second_llm = _install_goal_llm(client)
        second = client.post(
            "/api/v1/memory",
            json={"conversation": _GOAL_CONVERSATION, "external_key": "/c/dup"},
        )
        assert second.json()["status"] == "duplicate"
        assert second_llm.call_count == 0

    def test_no_external_key_never_touches_diff_or_working_context(
        self, client: TestClient
    ) -> None:
        llm = _install_goal_llm(client)
        response = client.post(
            "/api/v1/memory", json={"conversation": _GOAL_CONVERSATION}
        )
        assert response.status_code == 200
        [prompt] = llm.extractor_prompts
        assert "EXISTING CONTEXT" not in prompt

        checkpoint_dir = Path(client.app.state.checkpoint_dir)
        assert list(checkpoint_dir.glob("*.json")) == []
