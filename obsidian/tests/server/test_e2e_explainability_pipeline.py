"""End-to-end: the Explainability Pipeline (Retrieval Inspector) -- every
stage renders, the trace matches reality, and no section is silently
missing.

Scenario 4 of the Haven end-to-end suite. Two complementary angles on the
same guarantee:

1. **Structural** (``TestDashboardUiRendersEveryStage``): the dashboard's
   static HTML/JS (``obsidian/server/static/dashboard.html``) contains every
   stage section the Inspector promises, and reads it off exactly the JSON
   field names ``GET /api/v1/dashboard/inspect*`` actually returns --
   proved by grepping the same field names out of both the served HTML and
   the live JSON response for one real query, so this test would fail if a
   future refactor renamed a trace field on one side but not the other.
2. **Semantic** (``TestTraceMatchesReality``): the trace's own aggregate
   numbers are internally consistent with its own candidate list -- e.g.
   ``pipeline_stats.total_accepted_candidates`` really does equal the count
   of candidates with ``accepted: true`` -- so "the trace matches reality"
   is checked against the trace's own data, not re-derived retrieval
   internals.

Every stage a real ``MemoryEngine.query_with_trace`` call populates today:
query rewrite, retrieved candidates, acceptance decisions, slot allocation
(``pipeline_stats``), ``context_plan``, ``coverage``, ``gap_recovery``,
``project_state``, plus the Inspector's own additive
``working_contexts``/``structured_prompt`` (WorkingContextBuilder /
StructuredPromptBuilder).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject

# Every stage section the Guided Demo / dashboard docs promise the Inspector
# shows (see dashboard.html's own test in test_dashboard.py).
_STAGE_HEADINGS = (
    "Query Rewrite",
    "Retrieved Candidates",
    "Acceptance Decisions",
    "Slot Allocation",
    "WorkingContext Assembly",
    "WorkingContextState",
    "Structured Prompt",
)

# JSON field names the JS reads off `trace`/the Inspector response -- the
# structural test below asserts each is both present in the HTML's JS
# source (dashboard.html grep) AND a real, non-missing key in a live
# response, so a rename on either side breaks this test.
_TRACE_FIELDS_READ_BY_JS = (
    "pipeline_stats",
    "candidates",
    "rewritten_queries",
    "context_plan",
    "coverage",
    "gap_recovery",
    "project_state",
)


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


class TestDashboardUiRendersEveryStage:
    def test_every_stage_heading_is_present_in_the_served_page(
        self, client: TestClient
    ) -> None:
        html = client.get("/dashboard").text
        for heading in _STAGE_HEADINGS:
            assert heading in html, f"missing stage heading: {heading}"

    def test_every_json_field_the_js_reads_is_a_real_field_on_a_live_trace(
        self, client: TestClient
    ) -> None:
        html = client.get("/dashboard").text
        _seed(client, canonical_fact="Haven uses Claude for extraction.")
        trace = client.get(
            "/api/v1/dashboard/inspect", params={"query": "Claude"}
        ).json()["trace"]

        for field in _TRACE_FIELDS_READ_BY_JS:
            assert f"trace.{field}" in html, f"dashboard.html no longer reads trace.{field}"
            assert field in trace, f"live trace response is missing {field!r}"


class TestNoStageIsSilentlyMissing:
    def test_every_optional_diagnostic_stage_is_populated_not_null(
        self, client: TestClient
    ) -> None:
        """context_plan/coverage/gap_recovery/project_state are all typed
        ``Optional`` on ``RetrievalTrace`` (for backward-compat with traces
        serialised before each field existed -- see that class's own
        docstring), but every trace a *live* ``query_with_trace`` call
        builds today must populate all four -- a null here would mean a
        pipeline stage silently stopped attaching its diagnostics."""
        _seed(client, canonical_fact="Haven uses Claude for extraction.")

        trace = client.get(
            "/api/v1/dashboard/inspect", params={"query": "Claude"}
        ).json()["trace"]

        for field in ("context_plan", "coverage", "gap_recovery", "project_state"):
            assert trace[field] is not None, f"{field} unexpectedly missing"

    def test_no_stage_missing_even_for_a_query_with_no_matches(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="Haven uses Claude for extraction.")

        trace = client.get(
            "/api/v1/dashboard/inspect", params={"query": "completely unrelated gibberish"}
        ).json()["trace"]

        for field in ("context_plan", "coverage", "gap_recovery", "project_state"):
            assert trace[field] is not None

    def test_working_contexts_and_structured_prompt_are_never_missing_for_a_healthy_engine(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="Haven uses Claude for extraction.")

        body = client.get(
            "/api/v1/dashboard/inspect", params={"query": "Claude"}
        ).json()
        assert body["working_contexts"] is not None
        assert len(body["working_contexts"]) >= 1
        assert body["structured_prompt"] is not None


class TestTraceMatchesReality:
    """The trace's own aggregate numbers must be internally consistent with
    its own candidate list -- proving the Inspector's summary counts are a
    real reflection of the detail it also shows, not independently
    computed (and therefore potentially drifting) numbers."""

    def test_accepted_and_rejected_counts_match_the_candidate_list(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="Haven uses Claude for extraction.")
        _seed(client, canonical_fact="Haven uses Terraform for infra.")
        _seed(client, canonical_fact="Haven uses Kubernetes for orchestration.")

        trace = client.get(
            "/api/v1/dashboard/inspect", params={"query": "Haven"}
        ).json()["trace"]

        candidates = trace["candidates"]
        stats = trace["pipeline_stats"]
        accepted = [c for c in candidates if c["accepted"]]
        rejected = [c for c in candidates if not c["accepted"]]

        assert stats["total_merged_candidates"] == len(candidates)
        assert stats["total_accepted_candidates"] == len(accepted)
        assert stats["total_rejected_candidates"] == len(rejected)
        assert stats["total_accepted_candidates"] + stats["total_rejected_candidates"] == (
            stats["total_merged_candidates"]
        )

    def test_every_accepted_candidate_has_no_rejection_reason_and_vice_versa(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="Haven uses Claude for extraction.")
        _seed(client, canonical_fact="zzz totally unrelated filler content")

        trace = client.get(
            "/api/v1/dashboard/inspect", params={"query": "Claude"}
        ).json()["trace"]

        for candidate in trace["candidates"]:
            if candidate["accepted"]:
                assert candidate["rejection_reason"] is None
            else:
                assert candidate["rejection_reason"] is not None

    def test_final_context_size_matches_the_actual_returned_context_length(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="Haven uses Claude for extraction.")

        body = client.get(
            "/api/v1/dashboard/inspect", params={"query": "Claude"}
        ).json()
        assert body["trace"]["pipeline_stats"]["final_context_size"] == len(body["context"])

    def test_accepted_candidate_facts_are_the_ones_present_in_the_context_string(
        self, client: TestClient
    ) -> None:
        knowledge = _seed(client, canonical_fact="Haven uses Claude for extraction.")
        _seed(client, canonical_fact="zzz totally unrelated filler content")

        body = client.get(
            "/api/v1/dashboard/inspect", params={"query": "Claude"}
        ).json()
        accepted_facts = {
            c["canonical_fact"] for c in body["trace"]["candidates"] if c["accepted"]
        }
        for fact in accepted_facts:
            assert fact in body["context"]
        assert knowledge.canonical_fact in accepted_facts

    def test_inspecting_a_specific_memory_by_id_shows_it_among_the_candidates(
        self, client: TestClient
    ) -> None:
        knowledge = _seed(
            client,
            canonical_fact="Haven uses Claude for extraction.",
            memory_type=MemoryType.PROJECT,
        )

        body = client.get(
            f"/api/v1/dashboard/inspect/memory/{knowledge.id}"
        ).json()
        assert body["source_memory_id"] == str(knowledge.id)
        ids_considered = {c["knowledge_object_id"] for c in body["trace"]["candidates"]}
        assert str(knowledge.id) in ids_considered
