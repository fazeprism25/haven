"""Tests for ``POST /api/v1/dev/reset_demo``.

See ``obsidian.server.main.reset_demo_data``'s own docstring for the design
these exercise: each of the four vault-scoped directories is renamed aside
(``_rename_aside``) rather than deleted in place before reseeding, so a
reset survives the transient Windows file locks (OneDrive/antivirus/Search
indexing/a live Obsidian window's file watcher) that made ``shutil.rmtree``
raise an unhandled 500 on a real local vault. If reseeding itself then
fails, the half-seeded fresh directories are discarded and the pre-reset
ones are restored from their backups.

Covers: reset on an empty space, a populated space (including that stray,
non-demo content is actually cleared), repeated/idempotent resets, and
rollback on a reseed failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest
from fastapi.testclient import TestClient

from obsidian.manager_ai.models import KnowledgeObject
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


def _backup_dirs(tmp_path: Path) -> List[Path]:
    """Every leftover ``*.reset-bak-*`` sibling directory directly under
    *tmp_path* -- the fixture above puts vault/concepts/checkpoints/
    write_traces there as direct children, so a successful reset (backups
    discarded) or a rolled-back one (backups renamed back) must never leave
    one of these behind."""
    return [p for p in tmp_path.iterdir() if p.is_dir() and ".reset-bak-" in p.name]


def _all_facts(client: TestClient) -> List[str]:
    app = client.app
    app.state.memory_store.load()
    return sorted(ko.canonical_fact for ko in app.state.memory_store.all())


class TestResetOnEmptySpace:
    """Nothing has been seeded yet -- the four directories don't exist."""

    def test_returns_200_and_seeds_the_bundled_demo_dataset(
        self, client: TestClient
    ) -> None:
        response = client.post("/api/v1/dev/reset_demo")
        assert response.status_code == 200
        body = response.json()
        assert body["bulk_facts"] > 0
        assert body["conversation_calls"] > 0

        vault_info = client.get("/api/v1/vault").json()
        assert vault_info["memory_count"] > 0

    def test_leaves_no_backup_directories_behind(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        response = client.post("/api/v1/dev/reset_demo")
        assert response.status_code == 200
        assert _backup_dirs(tmp_path) == []


class TestResetOnPopulatedSpace:
    """A space that already has demo data *plus* stray, non-demo content."""

    def test_clears_stray_content_and_reseeds_deterministically(
        self, client: TestClient
    ) -> None:
        first = client.post("/api/v1/dev/reset_demo")
        assert first.status_code == 200
        baseline_facts = _all_facts(client)

        marker_text = "MANUAL_MARKER: added by hand, not part of the demo dataset."
        app = client.app
        marker = KnowledgeObject(canonical_fact=marker_text)
        app.state.vault_writer.write(marker)
        app.state.ontology_pipeline.process(marker)
        assert marker_text in _all_facts(client)

        second = client.post("/api/v1/dev/reset_demo")
        assert second.status_code == 200
        assert second.json() == first.json()

        facts_after = _all_facts(client)
        assert marker_text not in facts_after
        assert facts_after == baseline_facts

    def test_leaves_no_backup_directories_behind(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        client.post("/api/v1/dev/reset_demo")
        response = client.post("/api/v1/dev/reset_demo")
        assert response.status_code == 200
        assert _backup_dirs(tmp_path) == []


class TestRepeatedResets:
    def test_three_resets_in_a_row_stay_idempotent(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        responses = [client.post("/api/v1/dev/reset_demo") for _ in range(3)]
        for response in responses:
            assert response.status_code == 200

        bodies = [r.json() for r in responses]
        assert bodies[0] == bodies[1] == bodies[2]

        assert _backup_dirs(tmp_path) == []
        assert client.get("/api/v1/vault").json()["memory_count"] > 0


class TestResetFailureRollback:
    """A reseed failure must restore the pre-reset space, not leave it
    emptied or half-seeded, and must surface a real error -- not a bare
    500 with the cause swallowed."""

    def test_failed_reseed_restores_prior_content_and_reports_the_error(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seeded = client.post("/api/v1/dev/reset_demo")
        assert seeded.status_code == 200
        baseline_facts = _all_facts(client)
        assert baseline_facts

        def _boom(*args: object, **kwargs: object) -> int:
            raise RuntimeError("simulated reseed failure")

        monkeypatch.setattr(demo_seed, "seed_bulk_facts", _boom)

        failed = client.post("/api/v1/dev/reset_demo")
        assert failed.status_code == 500
        detail = failed.json()["detail"]
        assert "simulated reseed failure" in detail
        assert "rolled back" in detail.lower()

        # The space must be back exactly where it was before the failed
        # attempt -- not empty, not half-seeded.
        assert _all_facts(client) == baseline_facts
        assert client.get("/api/v1/vault").json()["memory_count"] == len(baseline_facts)

        # Rollback restores the original directories from their renamed-
        # aside backups; it must not leave the backups sitting around.
        assert _backup_dirs(tmp_path) == []

    def test_a_subsequent_reset_succeeds_normally_after_a_rollback(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client.post("/api/v1/dev/reset_demo")

        def _boom(*args: object, **kwargs: object) -> int:
            raise RuntimeError("simulated reseed failure")

        monkeypatch.setattr(demo_seed, "seed_bulk_facts", _boom)
        failed = client.post("/api/v1/dev/reset_demo")
        assert failed.status_code == 500

        monkeypatch.undo()

        recovered = client.post("/api/v1/dev/reset_demo")
        assert recovered.status_code == 200
        assert recovered.json()["bulk_facts"] > 0
        assert _backup_dirs(tmp_path) == []
