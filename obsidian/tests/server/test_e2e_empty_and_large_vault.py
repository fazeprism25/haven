"""End-to-end: the empty-vault and large-demo-vault extremes.

Scenarios 8 and 9 of the Haven end-to-end suite.

* **Empty vault**: every read surface a first-run user hits (vault info,
  dashboard, Retrieval Inspector, fresh query, Working Context preview,
  Write Inspector, Memory Review preview) must respond cleanly with
  honestly-empty data -- never a 500, never a null crash in a downstream
  field.
* **Large demo vault**: seeded via the real ``POST /api/v1/dev/seed_demo``
  endpoint (no test-only shortcut -- this is the same code path
  ``scripts/seed_demo.py`` and the dashboard's own "Import demo data"
  button use), which replays ``demo/demo_memories.md`` (57 bulk facts) and
  every conversation in ``demo/demo_conversations.md`` through the real
  Manager Pipeline (via a deterministic, marker-based fake LLM -- see
  ``obsidian/server/demo_seed.py``'s module docstring for why no API key is
  needed). Confirms the dashboard, retrieval, and Retrieval Inspector all
  keep working at this larger scale, and that reset/reseed is idempotent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from obsidian.server import demo_seed


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
# Scenario 8: empty vault
# ---------------------------------------------------------------------------


class TestEmptyVaultEverySurfaceRespondsCleanly:
    def test_vault_info(self, client: TestClient) -> None:
        body = client.get("/api/v1/vault").json()
        assert body["configured"] is True
        assert body["memory_count"] == 0

    def test_dashboard_every_section_honestly_empty(self, client: TestClient) -> None:
        body = client.get("/api/v1/dashboard").json()
        for section in ("projects", "decisions", "beliefs", "preferences", "tasks", "recent_memories"):
            assert body[section] == []
        assert body["vault_stats"]["total_memories"] == 0
        assert body["concept_stats"]["total_concepts"] == 0
        assert body["working_contexts"] == [
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
        assert body["project_overview"]["current_objective"] is None
        assert len(body["project_overview"]["gaps"]) == 8

    def test_fresh_query_returns_empty_context_not_an_error(
        self, client: TestClient
    ) -> None:
        assert client.post(
            "/api/v1/retrieve_context", json={"query": "anything at all"}
        ).json() == {"context": ""}

    def test_inspector_by_query_on_empty_vault(self, client: TestClient) -> None:
        body = client.get(
            "/api/v1/dashboard/inspect", params={"query": "anything"}
        ).json()
        assert body["context"] == ""
        assert body["trace"]["candidates"] == []
        assert body["trace"]["pipeline_stats"]["total_merged_candidates"] == 0

    def test_working_context_preview_still_available_on_empty_vault(
        self, client: TestClient
    ) -> None:
        body = client.post(
            "/api/v1/retrieve_working_context", json={"query": "anything"}
        ).json()
        assert body["available"] is True
        assert body["structured_prompt"] is not None

    def test_write_inspector_lists_no_traces(self, client: TestClient) -> None:
        body = client.get("/api/v1/dashboard/write-traces").json()
        assert body["traces"] == []

    def test_memory_review_preview_works_on_empty_vault(self, client: TestClient) -> None:
        from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
        from obsidian.manager_ai.classifier import Classifier
        from obsidian.manager_ai.extractor import Extractor
        from obsidian.manager_ai.importance import ImportanceScorer
        from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
        from obsidian.manager_ai.pipeline import ManagerPipeline

        class _EmptyLLM:
            def generate(self, prompt: str) -> str:
                return "[]"

        client.app.state.manager_pipeline = ManagerPipeline(
            extractor=Extractor(llm=_EmptyLLM()),
            classifier=Classifier(llm=_EmptyLLM()),
            importance_scorer=ImportanceScorer(llm=_EmptyLLM()),
            canonical_matcher=CanonicalMatcher(),
            knowledge_updater=KnowledgeUpdater(),
        )
        body = client.post(
            "/api/v1/memory/preview", json={"canonical_fact": "chit-chat only"}
        ).json()
        assert body["status"] == "ok"
        assert body["items"] == []

    def test_dashboard_html_serves_cleanly_on_empty_vault(self, client: TestClient) -> None:
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "Haven" in response.text


# ---------------------------------------------------------------------------
# Scenario 9: large demo vault
# ---------------------------------------------------------------------------


def _seed(client: TestClient) -> dict:
    response = client.post("/api/v1/dev/seed_demo")
    assert response.status_code == 200, response.text
    return response.json()


class TestLargeDemoVaultLoadsAndStaysUsable:
    def test_seeding_reports_the_real_parsed_counts(self, client: TestClient) -> None:
        result = _seed(client)
        expected_bulk = len(
            demo_seed.parse_bulk_memories(
                demo_seed.DEMO_MEMORIES_FILE.read_text(encoding="utf-8")
            )
        )
        expected_calls = len(
            demo_seed.parse_conversations(
                demo_seed.DEMO_CONVERSATIONS_FILE.read_text(encoding="utf-8")
            )
        )
        assert result["bulk_facts"] == expected_bulk
        assert result["conversation_calls"] == expected_calls
        assert expected_bulk >= 30  # a genuinely "large" demo dataset, not a toy fixture

    def test_vault_memory_count_reflects_bulk_and_conversation_facts(
        self, client: TestClient
    ) -> None:
        result = _seed(client)
        vault_info = client.get("/api/v1/vault").json()
        # At least every bulk fact landed (conversations may add more, or
        # CONFIRM into fewer distinct objects than raw call count -- but
        # never fewer than the bulk facts alone).
        assert vault_info["memory_count"] >= result["bulk_facts"]

    def test_large_vault_read_surfaces_all_stay_healthy(self, client: TestClient) -> None:
        """Every read surface a large vault exercises, checked against one
        shared seed (rather than one ``POST /dev/seed_demo`` replay per
        assertion) -- seeding drives the real Manager Pipeline once per
        demo conversation, so re-seeding per test would multiply this
        file's runtime without adding coverage beyond what a single seeded
        vault already lets every read endpoint be checked against."""
        result = _seed(client)

        # Dashboard aggregate stats scale with the larger vault.
        body = client.get("/api/v1/dashboard").json()
        assert body["vault_stats"]["total_memories"] > 30
        assert body["concept_stats"]["total_concepts"] > 0
        # Every typed section sums to at most the total (sanity, not a
        # precise partition since FACT-type memories appear in none of them).
        typed_total = sum(
            len(body[section])
            for section in ("projects", "decisions", "beliefs", "preferences", "tasks")
        )
        assert typed_total <= body["vault_stats"]["total_memories"]

        # recent_limit still bounds the feed correctly at this larger scale.
        limited = client.get("/api/v1/dashboard", params={"recent_limit": 5}).json()
        assert len(limited["recent_memories"]) == 5

        # A fresh query still finds a known demo fact.
        retrieval = client.post(
            "/api/v1/retrieve_context", json={"query": "Haven"}
        ).json()
        assert "Haven" in retrieval["context"]

        # The Inspector handles the larger candidate pool without error, and
        # its own aggregate counts stay internally consistent.
        inspected = client.get(
            "/api/v1/dashboard/inspect", params={"query": "Haven"}
        ).json()
        assert len(inspected["trace"]["candidates"]) > 0
        stats = inspected["trace"]["pipeline_stats"]
        assert stats["total_accepted_candidates"] + stats["total_rejected_candidates"] == (
            stats["total_merged_candidates"]
        )

        # A write trace was captured for every conversation call replayed.
        traces = client.get(
            "/api/v1/dashboard/write-traces", params={"limit": 200}
        ).json()["traces"]
        assert len(traces) == result["conversation_calls"]

    def test_reseeding_is_additive_not_an_error(self, client: TestClient) -> None:
        first = _seed(client)
        first_count = client.get("/api/v1/vault").json()["memory_count"]

        second = _seed(client)
        assert second["bulk_facts"] == first["bulk_facts"]
        second_count = client.get("/api/v1/vault").json()["memory_count"]
        # Additive: never fewer than before (some facts CONFIRM into
        # existing objects rather than creating new ones -- see
        # POST /dev/seed_demo's own docstring).
        assert second_count >= first_count

    def test_reset_demo_clears_then_reseeds_to_the_same_baseline(
        self, client: TestClient
    ) -> None:
        baseline = _seed(client)
        baseline_count = client.get("/api/v1/vault").json()["memory_count"]

        # Mutate further so the vault is not merely "already seeded".
        _seed(client)
        assert client.get("/api/v1/vault").json()["memory_count"] >= baseline_count

        reset = client.post("/api/v1/dev/reset_demo")
        assert reset.status_code == 200
        reset_body = reset.json()
        assert reset_body["bulk_facts"] == baseline["bulk_facts"]
        assert client.get("/api/v1/vault").json()["memory_count"] == baseline_count
