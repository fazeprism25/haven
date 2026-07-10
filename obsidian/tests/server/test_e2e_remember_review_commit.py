"""End-to-end: Remember a conversation -> Memory Review -> Commit -> Dashboard.

Scenario 1 of the Haven end-to-end suite (see also
``test_e2e_resume_workflow.py``, ``test_e2e_project_overview.py``, and
friends). Drives the real HTTP surface a user/extension actually calls, in
the order a real session would: ``POST /api/v1/memory/preview`` ->
``POST /api/v1/memory/commit`` -> ``GET /api/v1/dashboard`` -- and confirms
the dashboard's read model reflects exactly what was just committed, with
no server restart in between.

Mirrors ``test_memory_review.py``'s scripted-LLM harness (duplicated here
rather than imported, matching every other file in this directory -- see
that file's docstring for the rationale) since only the LLM boundary is
faked; Extractor/Classifier/ImportanceScorer/CanonicalMatcher/
KnowledgeUpdater/VaultWriter/OntologyPipeline/MemoryStore/ConceptGraph all
run for real.
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


_CONVERSATION = [
    {"role": "user", "content": "I'm building a personal knowledge base called Haven."},
    {"role": "assistant", "content": "Nice, what's the stack?"},
    {"role": "user", "content": "FastAPI backend, Markdown vault, no external DB."},
]


class TestFullRememberReviewCommitDashboardCycle:
    """The core Scenario 1 happy path: every dashboard section reflects the
    committed memory immediately after commit, with no restart."""

    def test_dashboard_is_empty_before_anything_is_remembered(
        self, client: TestClient
    ) -> None:
        body = client.get("/api/v1/dashboard").json()
        assert body["recent_memories"] == []
        assert body["vault_stats"]["total_memories"] == 0

    def test_preview_extracts_without_writing_to_vault_or_dashboard(
        self, client: TestClient
    ) -> None:
        _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user is building Haven, a personal knowledge base.", "stated", 0.9)]
            ),
            classify_responses=[_classify_json("project")],
            importance_responses=[_importance_json(0.8)],
        )
        preview = client.post(
            "/api/v1/memory/preview", json={"conversation": _CONVERSATION}
        ).json()
        assert preview["status"] == "ok"
        assert len(preview["items"]) == 1

        # Nothing durable happened yet: dashboard still empty.
        dashboard = client.get("/api/v1/dashboard").json()
        assert dashboard["recent_memories"] == []
        assert dashboard["projects"] == []

    def test_commit_then_dashboard_reflects_the_new_memory_everywhere_relevant(
        self, client: TestClient
    ) -> None:
        _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user is building Haven, a personal knowledge base.", "stated", 0.9)]
            ),
            classify_responses=[_classify_json("project")],
            importance_responses=[_importance_json(0.8)],
        )
        preview = client.post(
            "/api/v1/memory/preview", json={"conversation": _CONVERSATION}
        ).json()
        commit = client.post(
            "/api/v1/memory/commit",
            json={"review_id": preview["review_id"], "items": preview["items"]},
        ).json()
        assert commit["status"] == "success"

        dashboard = client.get("/api/v1/dashboard").json()

        # 1. Typed section (project).
        assert len(dashboard["projects"]) == 1
        assert dashboard["projects"][0]["canonical_fact"] == commit["canonical_fact"]
        assert dashboard["projects"][0]["id"] == commit["id"]

        # 2. Recent memories feed.
        assert [m["id"] for m in dashboard["recent_memories"]] == [commit["id"]]

        # 3. Vault + retrieval stats.
        assert dashboard["vault_stats"]["total_memories"] == 1
        assert dashboard["vault_stats"]["by_type"]["project"] == 1
        assert dashboard["retrieval_stats"]["vault_memory_count"] == 1

        # 4. Concept/ontology stats picked up the proper noun "Haven".
        assert dashboard["concept_stats"]["total_concepts"] >= 1

        # 5. Working Context / Resume panel reflects the new memory too.
        assert dashboard["working_contexts"] is not None
        assert any(
            dashboard["projects"][0]["canonical_fact"] in (ctx["current_focus"] or "")
            or dashboard["projects"][0]["canonical_fact"] in ctx.get("recent_decisions", [])
            or ctx["memory_count"] > 0
            for ctx in dashboard["working_contexts"]
        )

        # 6. The memory is immediately retrievable via the query pipeline too.
        retrieval = client.post(
            "/api/v1/retrieve_context", json={"query": "Haven"}
        ).json()
        assert "Haven" in retrieval["context"]

        # 7. The vault is durable on disk, not just in server memory.
        store = MemoryStore(client.app.state.vault_dir)
        store.load()
        assert store.count() == 1

    def test_write_trace_captured_for_the_commit_is_visible_in_dashboard(
        self, client: TestClient
    ) -> None:
        _install_scripted_llm(
            client,
            extract_response=_extract_json([("The user uses Postgres.", "stated", 0.9)]),
            classify_responses=[_classify_json("fact")],
            importance_responses=[_importance_json(0.6)],
        )
        preview = client.post(
            "/api/v1/memory/preview", json={"canonical_fact": "I use Postgres."}
        ).json()
        commit = client.post(
            "/api/v1/memory/commit",
            json={"review_id": preview["review_id"], "items": preview["items"]},
        ).json()

        traces = client.get("/api/v1/dashboard/write-traces").json()["traces"]
        assert any(t["trace_id"] == commit["trace_id"] for t in traces)

        detail = client.get(
            f"/api/v1/dashboard/write-traces/{commit['trace_id']}"
        ).json()["trace"]
        assert detail["status"] == "success"
        assert detail["knowledge_object_ids"] == [commit["id"]]


class TestReviewEditsReflectInDashboard:
    def test_edited_text_and_type_are_what_the_dashboard_shows(
        self, client: TestClient
    ) -> None:
        _install_scripted_llm(
            client,
            extract_response=_extract_json([("The user uses Notion.", "stated", 0.9)]),
            classify_responses=[_classify_json("fact")],
            importance_responses=[_importance_json(0.6)],
        )
        preview = client.post(
            "/api/v1/memory/preview", json={"canonical_fact": "I use Notion."}
        ).json()
        edited_items = [
            {
                **preview["items"][0],
                "text": "The user uses Notion for project planning.",
                "memory_type": "project",
            }
        ]
        commit = client.post(
            "/api/v1/memory/commit",
            json={"review_id": preview["review_id"], "items": edited_items},
        ).json()

        dashboard = client.get("/api/v1/dashboard").json()
        assert dashboard["projects"][0]["canonical_fact"] == (
            "The user uses Notion for project planning."
        )
        # The edit, not the raw extraction, is what got classified/typed.
        assert dashboard["decisions"] == []
        assert commit["review_summary"] == {"saved": 1, "edited": 1, "added": 0, "removed": 0}

    def test_deleted_item_never_appears_anywhere_in_dashboard(
        self, client: TestClient
    ) -> None:
        _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [
                    ("The user uses Obsidian.", "stated", 0.9),
                    ("The user studies at DTU.", "stated", 0.9),
                ]
            ),
            classify_responses=[_classify_json("fact"), _classify_json("fact")],
            importance_responses=[_importance_json(0.5), _importance_json(0.6)],
        )
        preview = client.post(
            "/api/v1/memory/preview",
            json={"canonical_fact": "I use Obsidian and study at DTU."},
        ).json()
        kept = next(i for i in preview["items"] if "Obsidian" in i["text"])
        commit = client.post(
            "/api/v1/memory/commit",
            json={"review_id": preview["review_id"], "items": [kept]},
        ).json()
        assert commit["review_summary"]["removed"] == 1

        dashboard = client.get("/api/v1/dashboard").json()
        facts = [m["canonical_fact"] for m in dashboard["recent_memories"]]
        assert facts == ["The user uses Obsidian."]
        assert "The user studies at DTU." not in facts


class TestCancelledReviewNeverReachesDashboard:
    def test_cancel_leaves_dashboard_untouched(self, client: TestClient) -> None:
        _install_scripted_llm(
            client,
            extract_response=_extract_json([("The user uses Vim.", "stated", 0.9)]),
            classify_responses=[_classify_json("fact")],
            importance_responses=[_importance_json(0.5)],
        )
        preview = client.post(
            "/api/v1/memory/preview", json={"canonical_fact": "I use Vim."}
        ).json()
        cancel = client.post(
            "/api/v1/memory/cancel", json={"review_id": preview["review_id"]}
        )
        assert cancel.status_code == 200

        dashboard = client.get("/api/v1/dashboard").json()
        assert dashboard["recent_memories"] == []
        assert dashboard["vault_stats"]["total_memories"] == 0

        # And it can no longer be committed after being cancelled.
        commit = client.post(
            "/api/v1/memory/commit",
            json={"review_id": preview["review_id"], "items": preview["items"]},
        )
        assert commit.status_code == 404


class TestDirectRememberWithoutReviewAlsoReachesDashboard:
    """The non-review "Remember" path (``POST /api/v1/memory`` directly)
    exercises the same dashboard projection, without a preview/commit
    round-trip -- proving the dashboard is driven by vault state, not by
    the review mechanism specifically."""

    def test_direct_save_memory_reflects_in_dashboard_immediately(
        self, client: TestClient
    ) -> None:
        _install_scripted_llm(
            client,
            extract_response=_extract_json(
                [("The user decided to use SQLite for local storage.", "stated", 0.9)]
            ),
            classify_responses=[_classify_json("decision")],
            importance_responses=[_importance_json(0.7)],
        )
        response = client.post(
            "/api/v1/memory",
            json={"canonical_fact": "I decided to use SQLite for local storage."},
        )
        assert response.status_code == 200

        dashboard = client.get("/api/v1/dashboard").json()
        assert len(dashboard["decisions"]) == 1
        assert dashboard["decisions"][0]["canonical_fact"] == (
            "The user decided to use SQLite for local storage."
        )
