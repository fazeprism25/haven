"""Tests for the Obsidian vault import endpoints.

Covers the three properties the feature promises:

* **Scan never calls the LLM.** It is a pure filesystem walk + checkpoint
  hashing, so it must succeed with an LLM that raises on any call.
* **Imported memories carry provenance and reuse the existing pipeline.** A
  scan -> preview -> commit round-trip writes ``KnowledgeObject``s through the
  unchanged ``/memory/preview`` + ``/memory/commit`` path, each stamped with
  ``metadata["provenance"]`` (source/source_file/imported_at/memory_space),
  and the commit reports its ``decision_counts``.
* **Checkpointing skips unchanged notes.** Re-scanning after an import reports
  the note as ``skipped`` -- the same duplicate short-circuit a re-clicked
  ChatGPT "Remember" gets, via the note's relative path as ``external_key``.

Mirrors ``test_memory_review.py``'s harness: an isolated ``tmp_path`` vault via
``HAVEN_VAULT_DIR``/``HAVEN_CONCEPT_DIR`` env vars, a scripted fake LLM inside a
real ``ManagerPipeline``.
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
from obsidian.memory_engine.memory_store import MemoryStore


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))
    monkeypatch.setenv("HAVEN_WRITE_TRACE_DIR", str(tmp_path / "write_traces"))
    monkeypatch.setenv("HAVEN_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))

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


class _ExplodingLLM:
    def generate(self, prompt: str) -> str:  # pragma: no cover - must never run
        raise AssertionError("LLM must not be called during a scan")


def _install_llm(client: TestClient, llm) -> None:
    client.app.state.manager_pipeline = ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )


def _scripted_one_fact(client: TestClient, text: str, memory_type: str = "fact") -> None:
    _install_llm(
        client,
        _ScriptedLLM(
            extract_response=json.dumps(
                [{"text": text, "evidence": "stated", "confidence": 0.9}]
            ),
            classify_responses=[
                json.dumps({"memory_type": memory_type, "confidence": 0.9, "reason": "r"})
            ],
            importance_responses=[json.dumps({"score": 0.6, "reason": "r"})],
        ),
    )


def _make_vault(tmp_path: Path) -> Path:
    root = tmp_path / "external_vault"
    (root / "sub").mkdir(parents=True, exist_ok=True)
    (root / "a.md").write_text(
        "---\ntitle: Note A\n---\n\nThe user loves [[Terraform]].\n", encoding="utf-8"
    )
    (root / "sub" / "b.md").write_text("# Note B\n\nBody two.\n", encoding="utf-8")
    return root


def _scan(client: TestClient, root: Path) -> dict:
    response = client.post("/api/v1/import/obsidian/scan", json={"root": str(root)})
    assert response.status_code == 200, response.text
    return response.json()


def test_scan_classifies_without_calling_the_llm(client: TestClient, tmp_path: Path) -> None:
    root = _make_vault(tmp_path)
    _install_llm(client, _ExplodingLLM())  # any LLM call would raise

    body = _scan(client, root)

    assert body["scanned"] == 2
    assert body["changed"] == 2
    assert body["skipped"] == 0
    assert body["review_mode"] == "flat"
    statuses = {n["source_file"]: n["status"] for n in body["notes"]}
    assert statuses == {"a.md": "needs_review", "sub/b.md": "needs_review"}


def test_import_stamps_provenance_reports_decisions_and_dedupes(
    client: TestClient, tmp_path: Path
) -> None:
    root = _make_vault(tmp_path)

    # Scan: both notes are new.
    assert _scan(client, root)["changed"] == 2

    # Preview + commit note a.md through the shared pipeline routes.
    _scripted_one_fact(client, "The user loves Terraform.")
    preview = client.post(
        "/api/v1/import/obsidian/preview",
        json={"root": str(root), "source_file": "a.md"},
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["status"] == "ok"
    assert len(preview_body["items"]) == 1

    commit = client.post(
        "/api/v1/memory/commit",
        json={"review_id": preview_body["review_id"], "items": preview_body["items"]},
    )
    assert commit.status_code == 200, commit.text
    commit_body = commit.json()
    assert commit_body["status"] == "success"
    assert commit_body["decision_counts"] == {"new": 1}

    # The persisted KnowledgeObject carries full provenance.
    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    memories = store.all()
    assert len(memories) == 1
    prov = memories[0].metadata["provenance"]
    assert prov["source"] == "obsidian"
    assert prov["source_file"] == "a.md"
    assert prov["memory_space"] == "Default"
    assert "imported_at" in prov

    # Re-scan: a.md is now skipped (checkpoint dedup), b.md still needs review.
    _install_llm(client, _ExplodingLLM())
    rescan = _scan(client, root)
    statuses = {n["source_file"]: n["status"] for n in rescan["notes"]}
    assert statuses["a.md"] == "skipped"
    assert statuses["sub/b.md"] == "needs_review"
    assert rescan["skipped"] == 1
    assert rescan["changed"] == 1


def test_inspector_surfaces_provenance_for_imported_memory(
    client: TestClient, tmp_path: Path
) -> None:
    root = _make_vault(tmp_path)

    _scripted_one_fact(client, "The user loves Terraform.")
    preview = client.post(
        "/api/v1/import/obsidian/preview",
        json={"root": str(root), "source_file": "a.md"},
    ).json()
    imported_id = client.post(
        "/api/v1/memory/commit",
        json={"review_id": preview["review_id"], "items": preview["items"]},
    ).json()["id"]

    # A plain, non-imported memory (no provenance) for contrast.
    _scripted_one_fact(client, "The user is named Sam.")
    plain_id = client.post(
        "/api/v1/memory", json={"canonical_fact": "My name is Sam."}
    ).json()["id"]

    imported = client.get(
        f"/api/v1/dashboard/inspect/memory/{imported_id}"
    ).json()
    assert imported["provenance"]["source"] == "obsidian"
    assert imported["provenance"]["source_file"] == "a.md"

    plain = client.get(f"/api/v1/dashboard/inspect/memory/{plain_id}").json()
    assert plain["provenance"] is None


def test_scan_without_root_and_no_active_root_is_400(client: TestClient) -> None:
    # Env-managed deployments have no single vault root to default to.
    response = client.post("/api/v1/import/obsidian/scan", json={})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Robustness audit: a binary/non-UTF-8 file dropped into an Obsidian vault
# (e.g. a PDF, image, or other attachment matching a stray ``.md`` glob
# mistake, or a Markdown file saved with a non-UTF-8 encoding) must not
# crash the scan or preview -- UnicodeDecodeError is a ValueError subclass,
# not an OSError, so it slips past a bare ``except OSError`` guard.
# ---------------------------------------------------------------------------


def test_scan_survives_a_binary_non_utf8_file_without_crashing(
    client: TestClient, tmp_path: Path
) -> None:
    root = _make_vault(tmp_path)
    # Invalid UTF-8 byte sequence -- decoding this as UTF-8 raises
    # UnicodeDecodeError.
    (root / "binary.md").write_bytes(b"\xff\xfe\x00\x01\x02\x03")
    _install_llm(client, _ExplodingLLM())  # any LLM call would raise

    body = _scan(client, root)

    assert body["scanned"] == 3
    statuses = {n["source_file"]: n["status"] for n in body["notes"]}
    # Unreadable notes are surfaced for review, same as note_to_turns
    # raising OSError -- not silently dropped, and not a 500.
    assert statuses["binary.md"] == "needs_review"
    assert statuses["a.md"] == "needs_review"
    assert statuses["sub/b.md"] == "needs_review"


def test_preview_of_a_binary_non_utf8_file_returns_clean_404_not_500(
    client: TestClient, tmp_path: Path
) -> None:
    root = _make_vault(tmp_path)
    (root / "binary.md").write_bytes(b"\xff\xfe\x00\x01\x02\x03")
    _install_llm(client, _ExplodingLLM())  # any LLM call would raise

    response = client.post(
        "/api/v1/import/obsidian/preview",
        json={"root": str(root), "source_file": "binary.md"},
    )
    assert response.status_code == 404, response.text
    assert "Could not read note as text" in response.json()["detail"]
