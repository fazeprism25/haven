"""Tests for the ``POST /api/v1/capture`` (Quick Capture) HTTP endpoint.

Companion to ``test_save_memory.py`` (same fixtures/scripted-LLM harness,
duplicated here rather than imported so this file stays self-contained --
see that file's docstring for the harness's own rationale). Quick Capture
is a thin front door onto the *unmodified* ``save_memory`` flow: it saves
the original Markdown into the vault's ``notes/`` folder, then runs that
same Markdown through the one Manager Pipeline. These tests therefore
assert only what Quick Capture itself adds on top of the already-tested
``save_memory`` behaviour:

* the original note is written verbatim into ``notes/`` (a sibling of the
  memory vault, so it never breaks ``MemoryStore``'s recursive scan);
* the note's Markdown produces ``KnowledgeObject``s exactly as a
  conversation would, with a Write Trace whose id is returned;
* a note that extracts nothing worth remembering is still saved (the
  ``POST /memory`` 422 case becomes ``status="no_memories"``, not an error).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest
import yaml
from fastapi.testclient import TestClient

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
    monkeypatch.setenv("HAVEN_WRITE_TRACE_DIR", str(tmp_path / "write_traces"))

    from obsidian.server.main import app

    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Scripted fake LLM -- injected into a real ManagerPipeline (see
# test_save_memory.py for the full rationale behind this harness).
# ---------------------------------------------------------------------------


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
    return json.dumps(
        {"memory_type": memory_type, "confidence": confidence, "reason": reason}
    )


def _importance_json(score: float, reason: str = "scored") -> str:
    return json.dumps({"score": score, "reason": reason})


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def test_capture_rejects_blank_content(client: TestClient) -> None:
    response = client.post("/api/v1/capture", json={"content": "   "})
    assert response.status_code == 422


def test_capture_requires_content(client: TestClient) -> None:
    response = client.post("/api/v1/capture", json={"title": "just a title"})
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Successful capture -> note saved + memory remembered
# ---------------------------------------------------------------------------


def test_capture_saves_note_and_remembers(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json(
            [("The user chose Postgres for billing.", "stated", 0.9)]
        ),
        classify_responses=[_classify_json("decision")],
        importance_responses=[_importance_json(0.8)],
    )
    response = client.post(
        "/api/v1/capture",
        json={
            "title": "Billing DB",
            "content": "Decided to use Postgres over Mongo for billing.",
            "tags": ["billing", "database"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["canonical_fact"] == "The user chose Postgres for billing."
    assert body["memory_type"] == "decision"
    assert isinstance(body["id"], str) and body["id"]
    # A normal Write Trace was generated and its id is returned so the
    # dashboard can open it.
    assert isinstance(body["trace_id"], str) and body["trace_id"]

    # The KnowledgeObject really landed in the vault.
    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    assert {ko.canonical_fact for ko in store.all()} == {
        "The user chose Postgres for billing."
    }


def test_capture_writes_original_markdown_into_notes_dir(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user chose Postgres.", "stated", 0.9)]),
        classify_responses=[_classify_json("decision")],
        importance_responses=[_importance_json(0.8)],
    )
    response = client.post(
        "/api/v1/capture",
        json={
            "title": "Billing DB",
            "content": "Decided to use Postgres over Mongo for billing.",
            "tags": ["billing"],
        },
    )
    assert response.status_code == 200
    note_path = Path(response.json()["note_path"])

    # Saved into notes/, a *sibling* of the memory vault -- never underneath
    # it (that would break MemoryStore's recursive *.md scan).
    assert note_path.exists()
    assert note_path.parent == client.app.state.notes_dir
    assert client.app.state.notes_dir.parent == client.app.state.vault_dir.parent
    assert not str(note_path).startswith(str(client.app.state.vault_dir))

    text = note_path.read_text(encoding="utf-8")
    front, _, body = text.partition("---\n")[2].partition("\n---\n")
    meta = yaml.safe_load(front)
    assert meta["title"] == "Billing DB"
    assert meta["source"] == "quick-capture"
    assert meta["tags"] == ["billing"]
    # The user's original Markdown is preserved verbatim in the body.
    assert "Decided to use Postgres over Mongo for billing." in body


def test_capture_note_does_not_break_memory_load(client: TestClient) -> None:
    """A saved note must never be picked up by the vault's KnowledgeObject scan."""
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user likes tea.", "stated", 0.9)]),
        classify_responses=[_classify_json("preference")],
        importance_responses=[_importance_json(0.4)],
    )
    client.post("/api/v1/capture", json={"content": "I like tea."})

    # The dashboard load path (which reloads MemoryStore from disk) must not
    # raise now that a raw note file exists next to the vault.
    dashboard = client.get("/api/v1/dashboard")
    assert dashboard.status_code == 200
    store = MemoryStore(client.app.state.vault_dir)
    store.load()  # would raise if it tried to parse the raw note as a KO
    assert store.count() == 1


# ---------------------------------------------------------------------------
# Nothing extracted -> note still saved, status "no_memories" (not an error)
# ---------------------------------------------------------------------------


def test_capture_with_no_extractable_memories_still_saves_note(
    client: TestClient,
) -> None:
    _install_scripted_llm(client, extract_response="[]")
    response = client.post(
        "/api/v1/capture", json={"content": "uh, hmm, nothing really"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "no_memories"
    assert body["id"] is None
    assert body["trace_id"] is None

    # The original note is on disk even though nothing was remembered.
    note_path = Path(body["note_path"])
    assert note_path.exists()
    assert "nothing really" in note_path.read_text(encoding="utf-8")


def test_capture_without_title_uses_first_line_as_note_title(
    client: TestClient,
) -> None:
    _install_scripted_llm(client, extract_response="[]")
    response = client.post(
        "/api/v1/capture", json={"content": "First line here\nSecond line"}
    )
    assert response.status_code == 200
    note_path = Path(response.json()["note_path"])
    front = note_path.read_text(encoding="utf-8").partition("---\n")[2].partition("\n---\n")[0]
    meta = yaml.safe_load(front)
    assert meta["title"] == "First line here"
