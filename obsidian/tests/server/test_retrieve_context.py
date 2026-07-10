"""Tests for the ``POST /retrieve_context`` HTTP endpoint.

Points ``HAVEN_VAULT_DIR``/``HAVEN_CONCEPT_DIR`` at an isolated ``tmp_path``
per test (this is the one place a temp directory is correct — it's a test,
not the running service) and drives the app exactly as a real HTTP client
would: through ``TestClient``, never by calling internal collaborators
directly except to seed vault/concept state ahead of a request, mirroring
how ``benchmarks/tests/test_haven_adapter.py`` seeds state via the real
write pipeline rather than hand-written fixtures.
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

    # Import (or re-import) after env vars are set so a fresh `app` module
    # object isn't required — the lifespan reads the env vars at startup,
    # which happens on `with TestClient(app) as c:` below.
    from obsidian.server.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_empty_vault_returns_empty_context(client: TestClient) -> None:
    response = client.post("/api/v1/retrieve_context", json={"query": "anything"})
    assert response.status_code == 200
    assert response.json() == {"context": ""}


def test_health_check_returns_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_retrieves_seeded_knowledge(client: TestClient) -> None:
    app = client.app
    knowledge = KnowledgeObject(
        canonical_fact="Haven uses Claude for extraction.",
        memory_type=MemoryType.FACT,
    )
    app.state.vault_writer.write(knowledge)
    app.state.ontology_pipeline.process(knowledge)

    response = client.post("/api/v1/retrieve_context", json={"query": "Claude"})
    assert response.status_code == 200
    assert "Haven uses Claude for extraction." in response.json()["context"]


def test_reload_picks_up_writes_without_restart(client: TestClient) -> None:
    app = client.app

    first = client.post("/api/v1/retrieve_context", json={"query": "Qdrant"})
    assert first.json() == {"context": ""}

    knowledge = KnowledgeObject(
        canonical_fact="Haven uses Qdrant for vector storage.",
        memory_type=MemoryType.FACT,
    )
    app.state.vault_writer.write(knowledge)
    app.state.ontology_pipeline.process(knowledge)

    second = client.post("/api/v1/retrieve_context", json={"query": "Qdrant"})
    assert "Haven uses Qdrant for vector storage." in second.json()["context"]


def test_include_trace_omitted_by_default(client: TestClient) -> None:
    # Existing callers that never set include_trace must see the exact
    # same response shape as before this field existed.
    response = client.post("/api/v1/retrieve_context", json={"query": "anything"})
    assert response.json() == {"context": ""}
    assert "trace" not in response.json()


def test_include_trace_attaches_serialised_trace(client: TestClient) -> None:
    app = client.app
    knowledge = KnowledgeObject(
        canonical_fact="Haven uses Claude for extraction.",
        memory_type=MemoryType.FACT,
    )
    app.state.vault_writer.write(knowledge)
    app.state.ontology_pipeline.process(knowledge)

    response = client.post(
        "/api/v1/retrieve_context",
        json={"query": "Claude", "include_trace": True},
    )
    body = response.json()

    assert "Haven uses Claude for extraction." in body["context"]
    assert body["trace"]["query"] == "Claude"
    assert len(body["trace"]["candidates"]) == 1
    assert body["trace"]["candidates"][0]["canonical_fact"] == knowledge.canonical_fact
    assert body["trace"]["candidates"][0]["accepted"] is True
    assert body["trace"]["candidates"][0]["keyword_overlap_score"] > 0.0
    assert body["trace"]["pipeline_stats"]["total_accepted_candidates"] == 1
