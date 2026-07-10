"""Tests for conversation-level duplicate prevention on ``POST /memory``.

Companion to ``test_save_memory.py`` (same fixtures/scripted-LLM harness,
duplicated here rather than imported so this file stays self-contained --
see that file's docstring for the harness's own rationale). These tests
cover only what PR 3 (checkpoint wiring) adds:

* ``TestNoExternalKeyPreservesTodaysBehaviour`` -- the regression suite:
  a request that never supplies ``external_key`` must behave identically
  to before this feature existed, including never invoking the
  checkpoint store/writer at all.
* ``TestDuplicateDetection`` -- resending an unchanged transcript with the
  same ``external_key`` short-circuits: the pipeline (and therefore every
  LLM call) runs exactly once, ``VaultWriter``/``OntologyPipeline`` are
  never invoked on the second call, and the response reports
  ``status="duplicate"``.
* ``TestConversationIsolation`` -- different ``external_key``s (or
  different ``source``s with the same key) never share a checkpoint.
* ``TestGrowingConversation`` -- a conversation that grew since the last
  checkpoint still ends up fully saved and its checkpoint still reflects
  the whole (grown) turn list -- true both before and after PR 4's
  incremental slicing (see ``test_save_memory_incremental.py`` for
  PR 4's own tests, which additionally assert *what the Extractor was
  shown* for a grown conversation).
* ``TestCheckpointPersistence`` -- the checkpoint file itself reflects
  what was actually processed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from obsidian.checkpoint.identity import derive_conversation_id
from obsidian.checkpoint.store import CheckpointStore
from obsidian.core.enums import SourceType
from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.pipeline import ManagerPipeline
from obsidian.memory_engine.memory_store import MemoryStore


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
# Scripted fake LLM -- counts calls so tests can assert "pipeline ran once"
# ---------------------------------------------------------------------------


class _CountingScriptedLLM:
    """Same prompt-shape dispatch as ``test_save_memory.py``'s ``_ScriptedLLM``,
    plus a call counter so tests can assert the pipeline ran (or didn't), and
    a ``prompts`` list (PR 4) so tests can assert *what* the Extractor was
    actually shown -- e.g. that an incremental save's prompt carries only
    the new turns, or that a fallback's carries every turn.
    """

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


def _install_scripted_llm(
    client: TestClient,
    extract_response: str,
    classify_responses: Sequence[str] = (),
    importance_responses: Sequence[str] = (),
) -> _CountingScriptedLLM:
    llm = _CountingScriptedLLM(extract_response, classify_responses, importance_responses)
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


_ONE_FACT_CONVERSATION = [
    {"role": "user", "content": "I want to apply to MIT."},
    {"role": "assistant", "content": "That's a great goal! What's your plan?"},
]


def _install_one_fact_llm(client: TestClient) -> _CountingScriptedLLM:
    return _install_scripted_llm(
        client,
        extract_response=_extract_json(
            [("The user's goal is to apply to MIT.", "stated in conversation", 0.9)]
        ),
        classify_responses=[_classify_json("goal")],
        importance_responses=[_importance_json(0.7)],
    )


# ---------------------------------------------------------------------------
# TestNoExternalKeyPreservesTodaysBehaviour
# ---------------------------------------------------------------------------


class TestNoExternalKeyPreservesTodaysBehaviour:
    def test_legacy_request_still_saves_successfully(self, client: TestClient) -> None:
        _install_one_fact_llm(client)
        response = client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "success"
        assert body["canonical_fact"] == "The user's goal is to apply to MIT."

    def test_resending_identical_conversation_without_external_key_reruns_pipeline(
        self, client: TestClient
    ) -> None:
        # Without external_key there is nothing to check a checkpoint
        # against, so this must behave exactly as it did before PR 3:
        # every send is treated as brand new and reruns the full pipeline.
        first_llm = _install_one_fact_llm(client)
        first = client.post(
            "/api/v1/memory", json={"conversation": _ONE_FACT_CONVERSATION}
        )
        assert first.status_code == 200
        assert first_llm.call_count > 0

        second_llm = _install_one_fact_llm(client)
        second = client.post(
            "/api/v1/memory", json={"conversation": _ONE_FACT_CONVERSATION}
        )
        assert second.status_code == 200
        assert second_llm.call_count > 0
        assert second.json()["status"] == "success"

    def test_no_checkpoint_file_is_ever_written(self, client: TestClient) -> None:
        _install_one_fact_llm(client)
        client.post("/api/v1/memory", json={"conversation": _ONE_FACT_CONVERSATION})

        checkpoint_dir = Path(client.app.state.checkpoint_dir)
        assert list(checkpoint_dir.glob("*.json")) == []

    def test_nothing_extracted_still_returns_422(self, client: TestClient) -> None:
        _install_scripted_llm(client, extract_response="[]")
        response = client.post(
            "/api/v1/memory", json={"canonical_fact": "uh, hmm, nothing really"}
        )
        assert response.status_code == 422
        assert "nothing" in response.json()["detail"].lower()

    def test_legacy_canonical_fact_shape_unaffected(self, client: TestClient) -> None:
        _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user uses Notion for planning.", "stated", 0.9)]
            ),
            classify_responses=[_classify_json("fact")],
            importance_responses=[_importance_json(0.5)],
        )
        response = client.post(
            "/api/v1/memory", json={"canonical_fact": "I use Notion for planning."}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["canonical_fact"] == "The user uses Notion for planning."


# ---------------------------------------------------------------------------
# TestDuplicateDetection
# ---------------------------------------------------------------------------


class TestDuplicateDetection:
    def test_second_identical_send_is_marked_duplicate(self, client: TestClient) -> None:
        _install_one_fact_llm(client)
        first = client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/abc123"},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "success"

        _install_one_fact_llm(client)
        second = client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/abc123"},
        )
        assert second.status_code == 200
        assert second.json()["status"] == "duplicate"

    def test_pipeline_llm_is_invoked_only_once_across_two_sends(
        self, client: TestClient
    ) -> None:
        first_llm = _install_one_fact_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/abc123"},
        )
        assert first_llm.call_count > 0

        second_llm = _install_one_fact_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/abc123"},
        )
        # The duplicate short-circuit must return before the pipeline (and
        # therefore this fake LLM) is ever called.
        assert second_llm.call_count == 0

    def test_no_new_knowledge_object_written_on_duplicate(self, client: TestClient) -> None:
        _install_one_fact_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/abc123"},
        )
        store = MemoryStore(client.app.state.vault_dir)
        store.load()
        count_after_first = store.count()

        _install_one_fact_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/abc123"},
        )
        store.load()
        assert store.count() == count_after_first

    def test_duplicate_response_has_no_object_fields(self, client: TestClient) -> None:
        _install_one_fact_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/abc123"},
        )
        _install_one_fact_llm(client)
        second = client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/abc123"},
        )
        body = second.json()
        assert body["id"] is None
        assert body["canonical_fact"] is None
        assert body["memory_type"] is None

    def test_duplicate_detection_also_works_for_legacy_canonical_fact_shape(
        self, client: TestClient
    ) -> None:
        _install_scripted_llm(
            client,
            extract_response=_extract_json([("The user lives in Muscat.", "stated", 0.9)]),
            classify_responses=[_classify_json("fact")],
            importance_responses=[_importance_json(0.6)],
        )
        first = client.post(
            "/api/v1/memory",
            json={"canonical_fact": "I live in Muscat.", "external_key": "manual-note-1"},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "success"

        second_llm = _install_scripted_llm(
            client,
            extract_response=_extract_json([("The user lives in Muscat.", "stated", 0.9)]),
            classify_responses=[_classify_json("fact")],
            importance_responses=[_importance_json(0.6)],
        )
        second = client.post(
            "/api/v1/memory",
            json={"canonical_fact": "I live in Muscat.", "external_key": "manual-note-1"},
        )
        assert second.json()["status"] == "duplicate"
        assert second_llm.call_count == 0


# ---------------------------------------------------------------------------
# TestConversationIsolation
# ---------------------------------------------------------------------------


class TestConversationIsolation:
    def test_different_external_keys_do_not_short_circuit_each_other(
        self, client: TestClient
    ) -> None:
        _install_one_fact_llm(client)
        first = client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/aaa"},
        )
        assert first.json()["status"] == "success"

        second_llm = _install_one_fact_llm(client)
        second = client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/bbb"},
        )
        assert second.json()["status"] == "success"
        assert second_llm.call_count > 0

    def test_same_external_key_different_source_do_not_share_a_checkpoint(
        self, client: TestClient
    ) -> None:
        _install_one_fact_llm(client)
        first = client.post(
            "/api/v1/memory",
            json={
                "conversation": _ONE_FACT_CONVERSATION,
                "external_key": "/c/shared-key",
                "source": "chatgpt",
            },
        )
        assert first.json()["status"] == "success"

        second_llm = _install_one_fact_llm(client)
        second = client.post(
            "/api/v1/memory",
            json={
                "conversation": _ONE_FACT_CONVERSATION,
                "external_key": "/c/shared-key",
                "source": "claude",
            },
        )
        assert second.json()["status"] == "success"
        assert second_llm.call_count > 0

    def test_two_distinct_checkpoint_files_are_written(self, client: TestClient) -> None:
        _install_one_fact_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/aaa"},
        )
        _install_one_fact_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/bbb"},
        )

        checkpoint_dir = Path(client.app.state.checkpoint_dir)
        assert len(list(checkpoint_dir.glob("*.json"))) == 2


# ---------------------------------------------------------------------------
# TestGrowingConversation
# ---------------------------------------------------------------------------


class TestGrowingConversation:
    def test_grown_conversation_is_reprocessed_and_saved(
        self, client: TestClient
    ) -> None:
        # A conversation that grew since the last checkpoint must be
        # treated as "changed" (never skipped as a duplicate) and the new
        # fact must end up saved. As of PR 4, a clean append like this one
        # sends only the new turns to the Extractor (see
        # test_save_memory_incremental.py for tests that assert that
        # slicing directly) -- this test only asserts the end result, which
        # is scripted-LLM-response-driven either way and therefore
        # unaffected by whether the Extractor saw the whole conversation or
        # just the new turn.
        _install_one_fact_llm(client)
        first = client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/growing"},
        )
        assert first.json()["status"] == "success"

        grown_conversation = _ONE_FACT_CONVERSATION + [
            {"role": "user", "content": "Also, I prefer tea over coffee."},
        ]
        second_llm = _install_scripted_llm(
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
        second = client.post(
            "/api/v1/memory",
            json={"conversation": grown_conversation, "external_key": "/c/growing"},
        )
        assert second.json()["status"] == "success"
        assert second_llm.call_count > 0

        store = MemoryStore(client.app.state.vault_dir)
        store.load()
        facts = {ko.canonical_fact for ko in store.all()}
        assert "The user prefers tea over coffee." in facts

    def test_checkpoint_turn_count_reflects_the_grown_conversation(
        self, client: TestClient
    ) -> None:
        _install_one_fact_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/growing"},
        )

        grown_conversation = _ONE_FACT_CONVERSATION + [
            {"role": "user", "content": "Also, I prefer tea over coffee."},
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
            json={"conversation": grown_conversation, "external_key": "/c/growing"},
        )

        store = CheckpointStore(client.app.state.checkpoint_dir)
        store.load()
        conversation_id = derive_conversation_id(SourceType.MANUAL, "/c/growing")
        checkpoint = store.get(conversation_id)
        assert checkpoint.turn_count == len(grown_conversation)
        assert checkpoint.last_processed_turn_index == len(grown_conversation) - 1


# ---------------------------------------------------------------------------
# TestCheckpointPersistence
# ---------------------------------------------------------------------------


class TestCheckpointPersistence:
    def test_checkpoint_written_after_successful_save(self, client: TestClient) -> None:
        _install_one_fact_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/abc123"},
        )

        store = CheckpointStore(client.app.state.checkpoint_dir)
        store.load()
        assert store.count() == 1

    def test_checkpoint_conversation_id_matches_derivation(self, client: TestClient) -> None:
        _install_one_fact_llm(client)
        client.post(
            "/api/v1/memory",
            json={
                "conversation": _ONE_FACT_CONVERSATION,
                "external_key": "/c/abc123",
                "source": "chatgpt",
            },
        )

        expected_id = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        store = CheckpointStore(client.app.state.checkpoint_dir)
        store.load()
        assert store.has(expected_id)

    def test_checkpoint_records_produced_knowledge_object_ids(
        self, client: TestClient
    ) -> None:
        response = None
        _install_one_fact_llm(client)
        response = client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/abc123"},
        )
        knowledge_id = UUID(response.json()["id"])

        store = CheckpointStore(client.app.state.checkpoint_dir)
        store.load()
        conversation_id = derive_conversation_id(SourceType.MANUAL, "/c/abc123")
        checkpoint = store.get(conversation_id)
        assert knowledge_id in checkpoint.knowledge_object_ids

    def test_checkpoint_not_written_when_nothing_extracted(self, client: TestClient) -> None:
        _install_scripted_llm(client, extract_response="[]")
        response = client.post(
            "/api/v1/memory",
            json={"canonical_fact": "uh, nothing really", "external_key": "/c/empty"},
        )
        assert response.status_code == 422

        checkpoint_dir = Path(client.app.state.checkpoint_dir)
        assert list(checkpoint_dir.glob("*.json")) == []

    def test_vault_and_ontology_still_invoked_on_success(self, client: TestClient) -> None:
        _install_one_fact_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/abc123"},
        )

        store = MemoryStore(client.app.state.vault_dir)
        store.load()
        assert "The user's goal is to apply to MIT." in [
            ko.canonical_fact for ko in store.all()
        ]

        response = client.post(
            "/api/v1/retrieve_context",
            json={"query": "MIT", "include_trace": True},
        )
        body = response.json()
        assert len(body["trace"]["candidates"]) == 1
