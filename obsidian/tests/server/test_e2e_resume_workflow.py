"""End-to-end: Remember -> fresh query -> ProjectState -> WorkingContext ->
Structured Prompt ("Resume Work" / "continue in a new conversation").

Scenario 2 of the Haven end-to-end suite. Models the real product workflow
this feature exists for: a user remembers things in one conversation, then
opens a *brand-new* conversation/session and asks Haven to resume -- no
extension state, no server restart, nothing carried over except what is on
disk. This file drives that through the real HTTP surface end to end:

    POST /api/v1/memory (remember)
        -> POST /api/v1/retrieve_context           (fresh query, ContextBuilder)
        -> POST /api/v1/retrieve_context?include_trace (ProjectState via RetrievalTrace)
        -> POST /api/v1/retrieve_working_context    (WorkingContext + StructuredPrompt)
        -> GET  /api/v1/dashboard                   (same state, dashboard's own view)

Every one of these calls hits the real, unmodified ``MemoryEngine`` --
nothing here mocks retrieval, ranking, planning, or the ontology.
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


def _remember(client: TestClient, fact_text: str, memory_type: str, importance: float = 0.7) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([(fact_text, "stated", 0.9)]),
        classify_responses=[_classify_json(memory_type)],
        importance_responses=[_importance_json(importance)],
    )
    response = client.post("/api/v1/memory", json={"canonical_fact": fact_text})
    assert response.status_code == 200, response.text


class TestFreshQueryAfterRemembering:
    def test_a_brand_new_query_call_finds_what_was_remembered(
        self, client: TestClient
    ) -> None:
        _remember(
            client,
            "The user is building a project called Haven for long-term memory.",
            "project",
        )

        # A "fresh" call: no session state, just the raw query string, as a
        # brand-new conversation would send.
        fresh = client.post("/api/v1/retrieve_context", json={"query": "Haven"}).json()
        assert "Haven" in fresh["context"]

    def test_empty_vault_fresh_query_returns_nothing_to_resume(
        self, client: TestClient
    ) -> None:
        fresh = client.post("/api/v1/retrieve_context", json={"query": "anything"}).json()
        assert fresh == {"context": ""}


class TestProjectStateViaRetrievalTrace:
    """ProjectState is reachable off ``RetrievalTrace.project_state`` --
    the same field a fresh query's ``include_trace=True`` call exposes."""

    def test_project_state_reflects_remembered_goal_and_task(
        self, client: TestClient
    ) -> None:
        _remember(client, "The user's goal is to ship Haven v1.", "goal")
        _remember(client, "The user needs to write the resume-workflow docs.", "task")

        trace = client.post(
            "/api/v1/retrieve_context",
            json={"query": "Haven v1", "include_trace": True},
        ).json()["trace"]

        assert trace["project_state"] is not None
        state = trace["project_state"]
        assert state["current_objective"] is not None
        assert state["current_objective"]["value"]["canonical_fact"] == (
            "The user's goal is to ship Haven v1."
        )

    def test_project_state_is_consistent_with_dashboard_project_overview(
        self, client: TestClient
    ) -> None:
        _remember(client, "The user's goal is to launch the beta.", "goal")

        trace = client.post(
            "/api/v1/retrieve_context",
            json={"query": "beta launch", "include_trace": True},
        ).json()["trace"]
        overview = client.get("/api/v1/dashboard").json()["project_overview"]

        assert overview["current_objective"]["fact"] == (
            trace["project_state"]["current_objective"]["value"]["canonical_fact"]
        )


class TestWorkingContextAndStructuredPrompt:
    def test_working_context_preview_surfaces_the_remembered_project(
        self, client: TestClient
    ) -> None:
        _remember(
            client,
            "The user is building a project called Haven for long-term memory.",
            "project",
        )

        preview = client.post(
            "/api/v1/retrieve_working_context", json={"query": "Haven"}
        ).json()
        assert preview["available"] is True
        assert preview["contexts"]
        assert any("Haven" in (c["title"] or "") for c in preview["contexts"])

    def test_structured_prompt_carries_the_remembered_fact_text(
        self, client: TestClient
    ) -> None:
        _remember(client, "The user prefers dark mode across all tools.", "preference")

        preview = client.post(
            "/api/v1/retrieve_working_context", json={"query": "dark mode"}
        ).json()
        assert preview["structured_prompt"] is not None
        assert "dark mode" in preview["structured_prompt"].lower()

    def test_working_context_preview_matches_dashboard_working_contexts(
        self, client: TestClient
    ) -> None:
        _remember(client, "The user is learning Rust this quarter.", "goal")

        dashboard_contexts = client.get("/api/v1/dashboard").json()["working_contexts"]
        preview = client.post(
            "/api/v1/retrieve_working_context", json={"query": "Rust"}
        ).json()["contexts"]

        dashboard_titles = {c["title"] for c in dashboard_contexts}
        preview_titles = {c["title"] for c in preview}
        # Both are built from the same MemoryEngine.query_working_context
        # method (different seed queries), so the same topic must appear in
        # both -- proving the dashboard's "Resume Work" panel and the
        # extension-facing preview are the same underlying feature.
        assert dashboard_titles & preview_titles


class TestEndToEndResumeAcrossASimulatedNewSession:
    """The full chain a real "new conversation, resume my work" moment
    exercises, asserted as one continuous story rather than isolated
    endpoint checks."""

    def test_remember_then_resume_in_a_fresh_session(self, client: TestClient) -> None:
        # Session 1: user tells Haven about their project and next task.
        _remember(
            client,
            "The user is building a project called Haven for long-term memory.",
            "project",
        )
        _remember(client, "The user needs to finish the Haven resume-workflow feature.", "task")

        # Session 2 (fresh -- no shared in-memory state beyond the vault on
        # disk): the user (or their assistant) asks Haven to resume.
        working_context = client.post(
            "/api/v1/retrieve_working_context", json={"query": "What was I working on?"}
        ).json()
        assert working_context["available"] is True

        overview = client.get("/api/v1/dashboard").json()["project_overview"]
        assert overview["current_milestone"]["fact"] == (
            "The user is building a project called Haven for long-term memory."
        )
        # Both facts share the "Haven" concept so the digest-query ranking
        # keeps both (see test_dashboard.py's TestProjectOverview docstring
        # on why unrelated facts mixed into one digest query can legitimately
        # drop one via AcceptanceStage's score-gap cut).
        assert [t["fact"] for t in overview["active_tasks"]] == [
            "The user needs to finish the Haven resume-workflow feature."
        ]
        assert overview["recommended_next_action"]["reason"] == "active_task"
