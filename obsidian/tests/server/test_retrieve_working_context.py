"""Tests for the ``POST /retrieve_working_context`` HTTP endpoint.

Mirrors ``test_retrieve_context.py``'s conventions: an isolated ``tmp_path``
vault/concept directory per test via ``HAVEN_VAULT_DIR``/``HAVEN_CONCEPT_DIR``,
driven through ``TestClient``, seeding state via the real
``vault_writer``/``ontology_pipeline`` collaborators on ``app.state``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))

    from obsidian.server.main import app

    with TestClient(app) as test_client:
        yield test_client


def _seed(client: TestClient, **kwargs) -> KnowledgeObject:
    app = client.app
    knowledge = KnowledgeObject(**kwargs)
    app.state.vault_writer.write(knowledge)
    app.state.ontology_pipeline.process(knowledge)
    return knowledge


class TestAvailability:
    def test_empty_vault_is_available_with_general_context(
        self, client: TestClient
    ) -> None:
        body = client.post(
            "/api/v1/retrieve_working_context", json={"query": "anything"}
        ).json()
        assert body["available"] is True
        assert body["structured_prompt"] is not None
        assert body["contexts"] == [
            {
                "key": "ctx:general",
                "title": "General",
                "kind": "general",
                "status": "reference",
                "memory_count": 0,
                "current_goal": None,
                "current_focus": None,
                "recent_decisions": [],
                "pending_tasks": [],
                "open_questions": [],
            }
        ]

    def test_unavailable_when_query_working_context_missing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(
            "obsidian.memory_engine.engine.MemoryEngine.query_working_context"
        )

        body = client.post(
            "/api/v1/retrieve_working_context", json={"query": "anything"}
        ).json()
        assert body == {"available": False, "structured_prompt": None, "contexts": []}

    def test_unavailable_when_query_working_context_raises(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from obsidian.memory_engine.engine import MemoryEngine

        def _boom(self, raw_query: str):
            raise RuntimeError("Working Context assembly is broken")

        monkeypatch.setattr(MemoryEngine, "query_working_context", _boom)

        response = client.post(
            "/api/v1/retrieve_working_context", json={"query": "anything"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["available"] is False
        assert body["structured_prompt"] is None
        assert body["contexts"] == []

    def test_unavailable_when_query_structured_raises(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from obsidian.memory_engine.engine import MemoryEngine

        def _boom(self, raw_query: str):
            raise RuntimeError("Structured prompt rendering is broken")

        monkeypatch.setattr(MemoryEngine, "query_structured", _boom)

        body = client.post(
            "/api/v1/retrieve_working_context", json={"query": "anything"}
        ).json()
        assert body == {"available": False, "structured_prompt": None, "contexts": []}


class TestPreviewContents:
    def test_seeded_memory_appears_in_a_topic_context(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="Haven uses Claude for extraction.")

        body = client.post(
            "/api/v1/retrieve_working_context", json={"query": "Claude"}
        ).json()

        assert body["available"] is True
        assert len(body["contexts"]) == 1
        context = body["contexts"][0]
        assert context["memory_count"] == 1
        assert "Haven uses Claude for extraction." in body["structured_prompt"]

    def test_structured_prompt_separates_context_from_user_request(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="Haven uses Claude for extraction.")

        body = client.post(
            "/api/v1/retrieve_working_context",
            json={"query": "Tell me about Claude"},
        ).json()

        prompt = body["structured_prompt"]
        assert "<HavenContext" in prompt
        assert "<UserRequest>" in prompt
        assert "Tell me about Claude" in prompt

    def test_decision_status_reflected_in_context_summary(
        self, client: TestClient
    ) -> None:
        _seed(
            client,
            canonical_fact="I decided to use Qdrant over Chroma.",
            memory_type=MemoryType.DECISION,
        )

        body = client.post(
            "/api/v1/retrieve_working_context", json={"query": "Qdrant"}
        ).json()

        context = body["contexts"][0]
        assert context["status"] == "decided"
        assert context["recent_decisions"] == ["I decided to use Qdrant over Chroma."]

    def test_no_match_yields_empty_general_context(self, client: TestClient) -> None:
        _seed(client, canonical_fact="Haven uses Claude for extraction.")

        body = client.post(
            "/api/v1/retrieve_working_context", json={"query": "nothing relevant here"}
        ).json()

        assert body["available"] is True
        assert body["contexts"] == [
            {
                "key": "ctx:general",
                "title": "General",
                "kind": "general",
                "status": "reference",
                "memory_count": 0,
                "current_goal": None,
                "current_focus": None,
                "recent_decisions": [],
                "pending_tasks": [],
                "open_questions": [],
            }
        ]

    def test_does_not_affect_retrieve_context_endpoint(
        self, client: TestClient
    ) -> None:
        """The new route is purely additive to the existing one."""
        _seed(client, canonical_fact="Haven uses Claude for extraction.")

        before = client.post(
            "/api/v1/retrieve_context", json={"query": "Claude"}
        ).json()
        client.post("/api/v1/retrieve_working_context", json={"query": "Claude"})
        after = client.post(
            "/api/v1/retrieve_context", json={"query": "Claude"}
        ).json()

        assert before == after
