"""End-to-end tests for the Write Inspector (``/api/v1/dashboard/write-traces*``).

Companion to ``test_save_memory_checkpoint.py``/``test_save_memory_incremental.py``
(same fixtures/scripted-LLM harness, duplicated here rather than imported so
this file stays self-contained). These tests exercise the trace *captured*
by ``POST /memory`` and exposed read-only via ``GET
/api/v1/dashboard/write-traces`` (list) and ``GET
/api/v1/dashboard/write-traces/{trace_id}`` (detail) -- not the pipeline
logic itself (already covered by the checkpoint/incremental test files and
``test_manager_pipeline_trace.py``).

Test groups
-----------
TestFirstRunTrace        -- a first, successful save persists a trace with
                            mode="first_run", the Extractor's prompt/raw
                            output, and one FactTrace per extracted fact.
TestDuplicateTrace        -- the duplicate short-circuit still persists a
                            trace (mode="duplicate", zero facts).
TestNoFactsExtractedTrace -- a 422 "nothing worth remembering" response
                            still persists a trace (status="no_facts_extracted"),
                            not just successful writes.
TestIncrementalTrace      -- an incremental append's trace records mode
                            "incremental" and a non-null new_turn_start_index.
TestCaptureLlmIoToggle    -- HAVEN_WRITE_TRACE_CAPTURE_LLM_IO=false redacts
                            prompt/raw_response but leaves every other
                            field populated.
TestDetailNotFound        -- an unknown/invalid trace id 404s.
TestRetentionWiring       -- HAVEN_WRITE_TRACE_MAX_COUNT is honoured end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest
from fastapi.testclient import TestClient

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


class _ScriptedLLM:
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


def _install_scripted_llm(
    client: TestClient,
    extract_response: str,
    classify_responses: Sequence[str] = (),
    importance_responses: Sequence[str] = (),
) -> None:
    llm = _ScriptedLLM(extract_response, classify_responses, importance_responses)
    client.app.state.manager_pipeline = ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )


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


def _install_one_fact_llm(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json(
            [("The user's goal is to apply to MIT.", "stated in conversation", 0.9)]
        ),
        classify_responses=[_classify_json("goal")],
        importance_responses=[_importance_json(0.7)],
    )


def _list_traces(client: TestClient) -> list:
    res = client.get("/api/v1/dashboard/write-traces")
    assert res.status_code == 200
    return res.json()["traces"]


def _get_trace_detail(client: TestClient, trace_id: str) -> dict:
    res = client.get(f"/api/v1/dashboard/write-traces/{trace_id}")
    assert res.status_code == 200
    return res.json()["trace"]


# ---------------------------------------------------------------------------
# TestFirstRunTrace
# ---------------------------------------------------------------------------


class TestFirstRunTrace:
    def test_successful_save_persists_a_trace(self, client: TestClient) -> None:
        _install_one_fact_llm(client)
        response = client.post(
            "/api/v1/memory", json={"conversation": _ONE_FACT_CONVERSATION}
        )
        assert response.status_code == 200

        traces = _list_traces(client)
        assert len(traces) == 1
        assert traces[0]["mode"] == "first_run"
        assert traces[0]["status"] == "success"
        assert traces[0]["fact_count"] == 1
        assert traces[0]["knowledge_object_count"] == 1
        assert traces[0]["total_duration_ms"] is not None

    def test_detail_contains_extractor_prompt_and_raw_response(
        self, client: TestClient
    ) -> None:
        _install_one_fact_llm(client)
        client.post("/api/v1/memory", json={"conversation": _ONE_FACT_CONVERSATION})

        trace_id = _list_traces(client)[0]["trace_id"]
        detail = _get_trace_detail(client, trace_id)

        assert "Conversation:\n" in detail["extractor"]["prompt"]
        assert detail["extractor"]["raw_response"] is not None
        assert detail["extractor"]["fact_count"] == 1

    def test_detail_contains_one_fact_trace_with_full_pipeline_outcome(
        self, client: TestClient
    ) -> None:
        _install_one_fact_llm(client)
        client.post("/api/v1/memory", json={"conversation": _ONE_FACT_CONVERSATION})

        trace_id = _list_traces(client)[0]["trace_id"]
        detail = _get_trace_detail(client, trace_id)

        assert len(detail["facts"]) == 1
        fact = detail["facts"][0]
        assert fact["fact_index"] == 0
        assert fact["fact_text"] == "The user's goal is to apply to MIT."
        assert fact["memory_type"] == "goal"
        assert fact["decision"] == "new"
        assert fact["knowledge_object_id"] is not None

    def test_detail_contains_vault_and_ontology_and_timings(
        self, client: TestClient
    ) -> None:
        _install_one_fact_llm(client)
        client.post("/api/v1/memory", json={"conversation": _ONE_FACT_CONVERSATION})

        trace_id = _list_traces(client)[0]["trace_id"]
        detail = _get_trace_detail(client, trace_id)

        assert len(detail["vault_paths"]) == 1
        assert "total" in detail["stage_timings_ms"]
        assert detail["pipeline_version"] >= 1
        assert detail["extractor_prompt_version"] >= 1

    def test_no_external_key_still_gets_a_trace(self, client: TestClient) -> None:
        # Trace capture is unconditional, unlike checkpoints (which are
        # gated on external_key) -- a legacy caller still gets debuggability.
        _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user uses Notion for planning.", "stated", 0.9)]
            ),
            classify_responses=[_classify_json("fact")],
            importance_responses=[_importance_json(0.5)],
        )
        client.post(
            "/api/v1/memory", json={"canonical_fact": "I use Notion for planning."}
        )

        traces = _list_traces(client)
        assert len(traces) == 1
        assert traces[0]["conversation_id"] is None


# ---------------------------------------------------------------------------
# TestDuplicateTrace
# ---------------------------------------------------------------------------


class TestDuplicateTrace:
    def test_duplicate_short_circuit_still_persists_a_trace(
        self, client: TestClient
    ) -> None:
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
        assert second.json()["status"] == "duplicate"

        traces = _list_traces(client)
        assert len(traces) == 2
        duplicate_trace = next(t for t in traces if t["mode"] == "duplicate")
        assert duplicate_trace["status"] == "duplicate"
        assert duplicate_trace["fact_count"] == 0
        assert duplicate_trace["knowledge_object_count"] == 0

    def test_duplicate_trace_detail_has_no_extractor_stage(
        self, client: TestClient
    ) -> None:
        _install_one_fact_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/abc123"},
        )
        _install_one_fact_llm(client)
        client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/abc123"},
        )

        traces = _list_traces(client)
        duplicate_id = next(t["trace_id"] for t in traces if t["mode"] == "duplicate")
        detail = _get_trace_detail(client, duplicate_id)
        assert detail["extractor"] is None
        assert detail["facts"] == []


# ---------------------------------------------------------------------------
# TestNoFactsExtractedTrace
# ---------------------------------------------------------------------------


class TestNoFactsExtractedTrace:
    def test_422_response_still_persists_a_trace(self, client: TestClient) -> None:
        _install_scripted_llm(client, extract_response="[]")
        response = client.post(
            "/api/v1/memory", json={"canonical_fact": "uh, hmm, nothing really"}
        )
        assert response.status_code == 422

        traces = _list_traces(client)
        assert len(traces) == 1
        assert traces[0]["status"] == "no_facts_extracted"
        assert traces[0]["fact_count"] == 0
        assert traces[0]["knowledge_object_count"] == 0

    def test_no_facts_trace_still_has_extractor_prompt(self, client: TestClient) -> None:
        _install_scripted_llm(client, extract_response="[]")
        client.post(
            "/api/v1/memory", json={"canonical_fact": "uh, hmm, nothing really"}
        )

        trace_id = _list_traces(client)[0]["trace_id"]
        detail = _get_trace_detail(client, trace_id)
        assert detail["extractor"] is not None
        assert detail["extractor"]["fact_count"] == 0


# ---------------------------------------------------------------------------
# TestIncrementalTrace
# ---------------------------------------------------------------------------


class TestIncrementalTrace:
    def test_appended_conversation_is_traced_as_incremental(
        self, client: TestClient
    ) -> None:
        _install_one_fact_llm(client)
        first = client.post(
            "/api/v1/memory",
            json={"conversation": _ONE_FACT_CONVERSATION, "external_key": "/c/grow"},
        )
        assert first.status_code == 200

        grown_conversation = _ONE_FACT_CONVERSATION + [
            {"role": "user", "content": "Also, I decided to apply early action."}
        ]
        _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user decided to apply early action.", "stated", 0.9)]
            ),
            classify_responses=[_classify_json("decision")],
            importance_responses=[_importance_json(0.6)],
        )
        second = client.post(
            "/api/v1/memory",
            json={"conversation": grown_conversation, "external_key": "/c/grow"},
        )
        assert second.status_code == 200

        traces = _list_traces(client)
        incremental_trace = next(t for t in traces if t["mode"] == "incremental")
        detail = _get_trace_detail(client, incremental_trace["trace_id"])
        assert detail["checkpoint"]["mode"] == "incremental"
        assert detail["checkpoint"]["new_turn_start_index"] == len(_ONE_FACT_CONVERSATION)
        assert detail["checkpoint"]["had_existing_checkpoint"] is True


# ---------------------------------------------------------------------------
# TestCaptureLlmIoToggle
# ---------------------------------------------------------------------------


class TestCaptureLlmIoToggle:
    def test_disabled_capture_redacts_prompt_and_raw_response(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
        monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))
        monkeypatch.setenv("HAVEN_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
        monkeypatch.setenv("HAVEN_WRITE_TRACE_DIR", str(tmp_path / "write_traces"))
        monkeypatch.setenv("HAVEN_WRITE_TRACE_CAPTURE_LLM_IO", "false")

        from obsidian.server.main import app

        with TestClient(app) as no_capture_client:
            _install_one_fact_llm(no_capture_client)
            no_capture_client.post(
                "/api/v1/memory", json={"conversation": _ONE_FACT_CONVERSATION}
            )

            trace_id = _list_traces(no_capture_client)[0]["trace_id"]
            detail = _get_trace_detail(no_capture_client, trace_id)

            assert detail["extractor"]["prompt"] is None
            assert detail["extractor"]["raw_response"] is None
            # Everything else is still fully captured.
            assert detail["extractor"]["fact_count"] == 1
            assert len(detail["facts"]) == 1
            assert detail["facts"][0]["decision"] == "new"
            assert detail["checkpoint"]["mode"] == "first_run"
            assert len(detail["vault_paths"]) == 1
            assert "total" in detail["stage_timings_ms"]


# ---------------------------------------------------------------------------
# TestDetailNotFound
# ---------------------------------------------------------------------------


class TestDetailNotFound:
    def test_invalid_uuid_404s(self, client: TestClient) -> None:
        response = client.get("/api/v1/dashboard/write-traces/not-a-uuid")
        assert response.status_code == 404

    def test_unknown_uuid_404s(self, client: TestClient) -> None:
        from uuid import uuid4

        response = client.get(f"/api/v1/dashboard/write-traces/{uuid4()}")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# TestRetentionWiring
# ---------------------------------------------------------------------------


class TestRetentionWiring:
    def test_max_count_env_var_prunes_old_traces(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
        monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))
        monkeypatch.setenv("HAVEN_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
        monkeypatch.setenv("HAVEN_WRITE_TRACE_DIR", str(tmp_path / "write_traces"))
        monkeypatch.setenv("HAVEN_WRITE_TRACE_MAX_COUNT", "2")

        from obsidian.server.main import app

        with TestClient(app) as limited_client:
            for i in range(4):
                _install_scripted_llm(
                    limited_client,
                    extract_response=_extract_json(
                        [(f"The user likes fact {i}.", "stated", 0.9)]
                    ),
                    classify_responses=[_classify_json("fact")],
                    importance_responses=[_importance_json(0.5)],
                )
                limited_client.post(
                    "/api/v1/memory",
                    json={"canonical_fact": f"I like fact {i}."},
                )

            traces = _list_traces(limited_client)
            assert len(traces) == 2
