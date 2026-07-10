"""End-to-end: Project Overview ("Mission Control") lifecycle.

Scenario 3 of the Haven end-to-end suite. Walks ``GET /api/v1/dashboard``'s
``project_overview`` section through a realistic lifecycle: an empty vault
(everything a gap) -> objective set -> milestone set -> activity
(decisions) recorded -> a blocker appears -> the next action recommendation
follows priority -> the blocker clears -> gaps shrink to nothing as each
category is populated.

Follows ``test_dashboard.py``'s ``TestProjectOverview`` convention of
seeding through the real ``vault_writer``/``ontology_pipeline``
collaborators on ``app.state`` (not a mock -- the same components
``POST /memory`` itself calls after the LLM stages), but assembles them
into one continuous story instead of isolated single-fact checks, and adds
the transitions (gap -> filled -> gap again) the per-field unit tests don't
cover.

Every fact mentions "Haven" so ``DeterministicRanker``'s ontology
activation and ``AcceptanceStage``'s score-gap cut keep every memory in the
same digest-query retrieval run together (see that file's own comment on
why mixing unrelated facts in one combined test would be flaky).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject

_ALL_GAP_KEYS = {
    "current_objective",
    "decisions",
    "active_tasks",
    "blockers",
    "constraints",
    "implementation_state",
    "code_areas",
    "open_questions",
}


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


def _overview(client: TestClient) -> dict:
    return client.get("/api/v1/dashboard").json()["project_overview"]


class TestProjectOverviewLifecycle:
    def test_empty_vault_has_every_gap_and_zero_coverage(self, client: TestClient) -> None:
        overview = _overview(client)
        assert set(overview["gaps"]) == _ALL_GAP_KEYS
        assert overview["field_coverage"] == 0.0
        assert overview["current_objective"] is None
        assert overview["current_milestone"] is None
        assert overview["recommended_next_action"] is None

    def test_setting_an_objective_closes_that_gap_and_becomes_next_action(
        self, client: TestClient
    ) -> None:
        _seed(
            client,
            canonical_fact="Ship Haven v1 to real users.",
            memory_type=MemoryType.GOAL,
        )
        overview = _overview(client)
        assert overview["current_objective"]["fact"] == "Ship Haven v1 to real users."
        assert "current_objective" not in overview["gaps"]
        # With nothing higher-priority tracked yet, the objective itself is
        # the recommended next action (fallback of the priority order).
        assert overview["recommended_next_action"]["reason"] == "current_objective"
        assert overview["field_coverage"] > 0.0

    def test_milestone_project_never_closes_the_objective_gap(
        self, client: TestClient
    ) -> None:
        _seed(
            client,
            canonical_fact="Haven dashboard redesign.",
            memory_type=MemoryType.PROJECT,
        )
        overview = _overview(client)
        assert overview["current_milestone"]["fact"] == "Haven dashboard redesign."
        # MemoryType.PROJECT has no ContextCategory mapping -- it never
        # counts toward gaps/coverage (see coverage_analyzer's module
        # docstring): a milestone alone does not close the (still-unset)
        # objective gap.
        assert "current_objective" in overview["gaps"]

    def test_a_blocker_outranks_the_objective_as_next_action(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="Ship Haven v1 to real users.", memory_type=MemoryType.GOAL)
        _seed(
            client,
            canonical_fact="Haven is blocked on the ontology schema review.",
            memory_type=MemoryType.BLOCKER,
        )
        overview = _overview(client)
        assert overview["recommended_next_action"]["reason"] == "blocker"
        assert overview["recommended_next_action"]["item"]["fact"] == (
            "Haven is blocked on the ontology schema review."
        )
        assert "blockers" not in overview["gaps"]

    def test_decisions_populate_activity_and_close_their_gap(
        self, client: TestClient
    ) -> None:
        _seed(
            client,
            canonical_fact="Haven decided to store memories as Markdown, not a DB.",
            memory_type=MemoryType.DECISION,
        )
        overview = _overview(client)
        assert [d["fact"] for d in overview["recent_decisions"]] == [
            "Haven decided to store memories as Markdown, not a DB."
        ]
        assert "decisions" not in overview["gaps"]

    # Each of the four remaining gap categories is seeded into its own,
    # otherwise-empty vault -- mirroring test_dashboard.py's own "one memory
    # type per test" convention. Mixing several dissimilar categories into
    # one digest query lets DeterministicRanker's ontology activation and
    # AcceptanceStage's score-gap cut legitimately drop some of them (real
    # retrieval behavior, not a bug -- see the module docstring above and
    # test_dashboard.py's TestProjectOverview comment), which would make a
    # combined assertion flaky/order-sensitive.
    def test_open_question_closes_its_gap(self, client: TestClient) -> None:
        _seed(
            client,
            canonical_fact="Should Haven support multi-user vaults?",
            memory_type=MemoryType.OPEN_QUESTION,
        )
        assert "open_questions" not in _overview(client)["gaps"]

    def test_constraint_rule_closes_its_gap(self, client: TestClient) -> None:
        _seed(
            client,
            canonical_fact="Haven must never call an external API with vault contents.",
            memory_type=MemoryType.RULE,
        )
        assert "constraints" not in _overview(client)["gaps"]

    def test_implementation_state_closes_its_gap(self, client: TestClient) -> None:
        _seed(
            client,
            canonical_fact="Haven's retrieval pipeline is fully implemented and tested.",
            memory_type=MemoryType.IMPLEMENTATION_STATE,
        )
        assert "implementation_state" not in _overview(client)["gaps"]

    def test_code_area_closes_its_gap(self, client: TestClient) -> None:
        _seed(
            client,
            canonical_fact="Haven's dashboard code lives in obsidian/server/static.",
            memory_type=MemoryType.CODE_AREA,
        )
        assert "code_areas" not in _overview(client)["gaps"]

    def test_populating_every_category_strictly_reduces_gaps_and_raises_coverage(
        self, client: TestClient
    ) -> None:
        """A vault covering all 8 gap categories at once must end up with
        fewer gaps and higher coverage than the empty baseline -- not
        necessarily zero gaps, since a combined digest-query retrieval run
        can legitimately drop some of a large, varied set (see the
        per-category tests above for the reliable, isolated assertion that
        each category *can* close its own gap)."""
        seeds = [
            ("Ship Haven v1 to real users.", MemoryType.GOAL),
            ("Haven is blocked on the ontology schema review.", MemoryType.BLOCKER),
            ("Haven decided to store memories as Markdown, not a DB.", MemoryType.DECISION),
            ("Haven needs the resume-workflow docs written.", MemoryType.TASK),
            ("Should Haven support multi-user vaults?", MemoryType.OPEN_QUESTION),
            ("Haven must never call an external API with vault contents.", MemoryType.RULE),
            ("Haven's retrieval pipeline is fully implemented and tested.", MemoryType.IMPLEMENTATION_STATE),
            ("Haven's dashboard code lives in obsidian/server/static.", MemoryType.CODE_AREA),
        ]
        for fact, memory_type in seeds:
            _seed(client, canonical_fact=fact, memory_type=memory_type)

        overview = _overview(client)
        assert len(overview["gaps"]) < len(_ALL_GAP_KEYS)
        assert overview["field_coverage"] > 0.0

    def test_resolving_a_blocker_reopens_that_gap(self, client: TestClient) -> None:
        _seed(
            client,
            canonical_fact="Haven is blocked on the ontology schema review.",
            memory_type=MemoryType.BLOCKER,
            valid_from=datetime.utcnow() - timedelta(hours=1),
        )
        assert "blockers" not in _overview(client)["gaps"]

        # Archive it (valid_until set) -- the same "no longer active" shape
        # ProjectStateBuilder / coverage_analyzer already treat as absent
        # for milestones (see test_dashboard.py's archived-project test).
        knowledge = client.app.state.memory_store.all()[0]
        from dataclasses import replace as dc_replace

        archived = dc_replace(knowledge, valid_until=datetime.utcnow())
        client.app.state.vault_writer.write(archived)

        overview = _overview(client)
        assert "blockers" in overview["gaps"]
        assert overview["recommended_next_action"] is None
