"""Regression tests for the route-level safety net around the Manager AI pipeline.

Before this fix, an unexpected exception from ``ManagerPipeline`` (a
programmer bug in the Extractor/Classifier/ImportanceScorer/CanonicalMatcher/
KnowledgeUpdater chain -- not one of the already-handled failure modes like
``ExtractionError``/``ClassificationError``/``ImportanceScoringError``) would
propagate straight out of ``save_memory``/``preview_memory``, past FastAPI's
routing layer, and surface as a raw, unhandled-exception 500 -- no clean JSON
body, and (in a real deployment) potentially no server-side record of what
happened beyond whatever ASGI server logs by default.

The fix wraps *only* the top-level pipeline invocation in each of
``save_memory``/``preview_memory`` -- not any broader area, and no new
try/except blocks inside the pipeline stages themselves. On an unexpected
exception it:

* logs the full traceback server-side via ``logger.exception``,
* raises a clean ``HTTPException(500, detail="Manager AI failed while
  processing this request.")`` instead of letting the raw exception surface.

This is proven directly against ``POST /memory`` and ``POST /memory/preview``,
and then against the two routes that call them in-process -- Quick Capture
(``POST /capture`` -> ``save_memory``) and Obsidian Import
(``POST /import/obsidian/preview`` -> ``preview_memory``) -- to confirm the
clean 500 propagates through those call chains too, rather than being masked
or re-raised raw.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

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
    monkeypatch.setenv("HAVEN_WRITE_TRACE_DIR", str(tmp_path / "write_traces"))
    monkeypatch.setenv("HAVEN_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))

    from obsidian.server.main import app

    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client


class _ScriptedLLM:
    """Fake ``LLMInterface`` shared by Extractor/Classifier/ImportanceScorer.

    Identical to ``test_save_memory.py``'s fixture of the same name -- these
    tests need a real, successful extraction so the *downstream* vault-write/
    checkpoint-write failure under test is what actually gets exercised, not
    an earlier LLM failure. Duplicated here rather than imported (see
    ``feedback-minimal-scaffolding``: reuse patterns verbatim, don't add a
    new shared module for one file's benefit).
    """

    def __init__(self, extract_response: str, classify_response: str, importance_response: str) -> None:
        self._extract_response = extract_response
        self._classify_response = classify_response
        self._importance_response = importance_response

    def generate(self, prompt: str) -> str:
        if "Conversation:\n" in prompt:
            return self._extract_response
        if "Available memory types:" in prompt:
            return self._classify_response
        if "Classification:\n" in prompt:
            return self._importance_response
        raise AssertionError(f"Unrecognised prompt shape:\n{prompt}")


def _install_scripted_llm(client: TestClient) -> None:
    """Install a scripted LLM that always extracts a single "I use Terraform." fact."""
    llm = _ScriptedLLM(
        extract_response=json.dumps(
            [{"text": "I use Terraform.", "evidence": "stated in conversation", "confidence": 0.9}]
        ),
        classify_response=json.dumps({"memory_type": "fact", "confidence": 0.9, "reason": "stated"}),
        importance_response=json.dumps({"score": 0.6, "reason": "scored"}),
    )
    client.app.state.manager_pipeline = ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )


class _BoomPipeline:
    """Stands in for ``ManagerPipeline``; every call is an unexpected bug.

    Deliberately a bare ``RuntimeError`` -- not ``ExtractionError``,
    ``ClassificationError``, or anything else the pipeline already knows how
    to handle -- to simulate a genuine programmer bug reaching the route
    layer, which is exactly the case the new safety net exists for.
    """

    def process_with_trace(self, *args, **kwargs):
        raise RuntimeError("boom: unexpected pipeline bug")

    def extract_classify_score(self, *args, **kwargs):
        raise RuntimeError("boom: unexpected pipeline bug")


def _assert_clean_500(response, caplog: pytest.LogCaptureFixture) -> None:
    assert response.status_code == 500, response.text
    body = response.json()
    assert "Manager AI" in body["detail"]

    # The full traceback must still reach server logs -- a programmer bug is
    # never silently swallowed, only converted into a clean HTTP response.
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records, "expected the pipeline failure to be logged"
    assert any(r.exc_info for r in error_records), (
        "expected the logged record to carry the full traceback (exc_info)"
    )
    assert any(
        "boom: unexpected pipeline bug" in str(r.exc_info[1])
        for r in error_records
        if r.exc_info
    )


# ---------------------------------------------------------------------------
# Remember (POST /memory)
# ---------------------------------------------------------------------------


def test_save_memory_returns_clean_500_when_pipeline_raises(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    client.app.state.manager_pipeline = _BoomPipeline()

    with caplog.at_level(logging.ERROR):
        response = client.post(
            "/api/v1/memory", json={"canonical_fact": "I use Terraform."}
        )

    _assert_clean_500(response, caplog)


# ---------------------------------------------------------------------------
# Memory Review (POST /memory/preview)
# ---------------------------------------------------------------------------


def test_preview_memory_returns_clean_500_when_pipeline_raises(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    client.app.state.manager_pipeline = _BoomPipeline()

    with caplog.at_level(logging.ERROR):
        response = client.post(
            "/api/v1/memory/preview", json={"canonical_fact": "I use Terraform."}
        )

    _assert_clean_500(response, caplog)


# ---------------------------------------------------------------------------
# Quick Capture (POST /capture -> save_memory in-process)
# ---------------------------------------------------------------------------


def test_capture_note_propagates_clean_500_from_save_memory(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    client.app.state.manager_pipeline = _BoomPipeline()

    with caplog.at_level(logging.ERROR):
        response = client.post(
            "/api/v1/capture", json={"content": "Some quick-capture note text."}
        )

    # capture_note only special-cases a 422 ("nothing worth remembering") from
    # save_memory; a 500 must propagate unchanged, not be swallowed into
    # status="no_memories".
    _assert_clean_500(response, caplog)


# ---------------------------------------------------------------------------
# Obsidian Import (POST /import/obsidian/preview -> preview_memory in-process)
# ---------------------------------------------------------------------------


def test_import_preview_propagates_clean_500_from_preview_memory(
    client: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "external_vault"
    root.mkdir(parents=True, exist_ok=True)
    (root / "note.md").write_text("# Note\n\nSome content.\n", encoding="utf-8")

    client.app.state.manager_pipeline = _BoomPipeline()

    with caplog.at_level(logging.ERROR):
        response = client.post(
            "/api/v1/import/obsidian/preview",
            json={"root": str(root), "source_file": "note.md"},
        )

    _assert_clean_500(response, caplog)


# ---------------------------------------------------------------------------
# Robustness audit: global exception handler
#
# Before this fix, an unhandled exception from ANY route not already
# wrapped in its own try/except (e.g. GET /api/v1/vault -- a plain read
# with no risky-I/O guard of its own) fell through to Starlette's default
# behaviour: a plain-text 500 response, not the JSON `{"detail": ...}`
# shape every other error path in this API returns. This proves the new
# app-wide exception handler normalizes that to clean JSON, for a route
# that was never individually hardened.
# ---------------------------------------------------------------------------


def test_unhandled_exception_on_an_unguarded_route_returns_clean_json_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom() -> None:
        raise RuntimeError("boom: simulated unexpected bug with no local guard")

    monkeypatch.setattr(client.app.state.memory_store, "load", _boom)

    # Starlette's ServerErrorMiddleware always re-raises the original
    # exception after building the response, specifically so a real ASGI
    # server can log it -- ``TestClient`` mirrors that into Python-level
    # re-raising by default (``raise_server_exceptions=True``), which would
    # make this test raise instead of letting us inspect the HTTP response
    # a real client actually receives. Disabling it here observes exactly
    # what a real client/browser would get: the clean response our handler
    # already built, not the un-httpified exception.
    no_raise_client = TestClient(client.app, raise_server_exceptions=False)

    with caplog.at_level(logging.ERROR):
        response = no_raise_client.get("/api/v1/vault")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Internal server error."}

    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any(
        r.exc_info and "boom: simulated unexpected bug" in str(r.exc_info[1])
        for r in error_records
    ), "expected the unhandled exception's traceback to still reach server logs"


# ---------------------------------------------------------------------------
# Robustness audit: vault-write filesystem failures return a clean 500
# instead of a raw, unhandled OSError/PermissionError.
# ---------------------------------------------------------------------------


def test_save_memory_returns_clean_500_when_vault_write_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_scripted_llm(client)

    def _boom(self, knowledge):
        raise PermissionError("simulated permission-denied writing to the vault")

    monkeypatch.setattr(
        type(client.app.state.vault_writer), "write", _boom, raising=True
    )

    response = client.post(
        "/api/v1/memory", json={"canonical_fact": "I use Terraform."}
    )
    assert response.status_code == 500, response.text
    assert "Could not write to the vault" in response.json()["detail"]


def test_commit_memory_returns_clean_500_when_vault_write_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_scripted_llm(client)

    preview = client.post(
        "/api/v1/memory/preview", json={"canonical_fact": "I use Terraform."}
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()

    def _boom(self, knowledge):
        raise PermissionError("simulated permission-denied writing to the vault")

    monkeypatch.setattr(
        type(client.app.state.vault_writer), "write", _boom, raising=True
    )

    response = client.post(
        "/api/v1/memory/commit",
        json={"review_id": body["review_id"], "items": body["items"]},
    )
    assert response.status_code == 500, response.text
    assert "Could not write to the vault" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Robustness audit: a checkpoint-write failure is best-effort and must not
# turn an already-successful memory save into an error response.
# ---------------------------------------------------------------------------


def test_save_memory_still_succeeds_when_checkpoint_write_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_scripted_llm(client)

    def _boom(self, checkpoint):
        raise OSError("simulated disk-full writing the checkpoint")

    monkeypatch.setattr(
        type(client.app.state.checkpoint_writer), "write", _boom, raising=True
    )

    with caplog.at_level(logging.WARNING):
        response = client.post(
            "/api/v1/memory",
            json={
                "canonical_fact": "I use Terraform.",
                "external_key": "conv-checkpoint-failure",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success"
    assert any(
        "checkpoint" in r.message.lower()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    )


# ---------------------------------------------------------------------------
# Robustness audit: Quick Capture's note write returns a clean 500 instead
# of a raw, unhandled OSError.
# ---------------------------------------------------------------------------


def test_capture_note_returns_clean_500_when_note_write_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import obsidian.server.main as main_module

    def _boom(request, created):
        raise OSError("simulated permission-denied writing the note")

    monkeypatch.setattr(main_module, "_write_capture_note", _boom)

    response = client.post(
        "/api/v1/capture", json={"content": "Some quick-capture note text."}
    )
    assert response.status_code == 500, response.text
    assert "Could not save the note" in response.json()["detail"]
