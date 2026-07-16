"""End-to-end: the dashboard reflects state changes with no server restart.

Scenario 10 of the Haven end-to-end suite. ``obsidian/server/main.py``'s
own module docstring makes an explicit promise: ``memory_store`` and
``alias_index`` are rebuilt from disk at the top of *every* request, "so
edits made directly to the vault on disk (e.g. hand-editing Markdown) are
picked up without a server restart." This file exercises that promise
across every kind of state change a real session can produce between two
dashboard loads:

* a new memory written through the pipeline (already covered end-to-end by
  ``test_e2e_remember_review_commit.py``; not repeated here);
* a memory file edited directly on disk, bypassing the API entirely;
* a memory archived (``valid_until`` set) after the dashboard already
  showed it active;
* switching the active Memory Space;
* selecting a brand-new vault root via ``POST /api/v1/vault`` -- a route no
  other test file in this repo currently drives directly (only the
  Memory-Spaces migration/activation paths are covered elsewhere).

Every check re-fetches ``GET /api/v1/dashboard`` (or another read route)
against the *same, already-running* ``TestClient``/``app`` instance used to
make the change -- there is no restart between "before" and "after" in any
of these tests.
"""

from __future__ import annotations

from dataclasses import replace as dc_replace
from datetime import datetime
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

    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client


def _seed(client: TestClient, **kwargs) -> KnowledgeObject:
    app = client.app
    knowledge = KnowledgeObject(**kwargs)
    app.state.vault_writer.write(knowledge)
    app.state.ontology_pipeline.process(knowledge)
    return knowledge


class TestHandEditedMarkdownIsPickedUpWithoutRestart:
    def test_editing_the_fact_text_on_disk_changes_the_next_dashboard_load(
        self, client: TestClient
    ) -> None:
        knowledge = _seed(client, canonical_fact="Original fact text.")
        before = client.get("/api/v1/dashboard").json()["recent_memories"][0]
        assert before["canonical_fact"] == "Original fact text."

        # Simulate a hand-edit: the same memory id, re-written with
        # different content, directly via VaultWriter -- exactly what
        # editing the Markdown file's frontmatter/body by hand in Obsidian
        # and saving would produce (filename is the memory's UUID, so this
        # overwrites the same file).
        edited = dc_replace(knowledge, canonical_fact="Hand-edited fact text.")
        client.app.state.vault_writer.write(edited)

        after = client.get("/api/v1/dashboard").json()["recent_memories"][0]
        assert after["canonical_fact"] == "Hand-edited fact text."
        assert after["id"] == before["id"]

    def test_a_note_deleted_from_disk_disappears_from_the_dashboard(
        self, client: TestClient
    ) -> None:
        knowledge = _seed(client, canonical_fact="Will be deleted by hand.")
        assert client.get("/api/v1/dashboard").json()["vault_stats"]["total_memories"] == 1

        # Delete the underlying Markdown file directly (not via any API).
        vault_dir: Path = client.app.state.vault_dir
        matches = list(vault_dir.glob(f"{knowledge.id}*.md"))
        assert matches, "expected VaultWriter to name the file after the memory id"
        for path in matches:
            path.unlink()

        body = client.get("/api/v1/dashboard").json()
        assert body["vault_stats"]["total_memories"] == 0
        assert body["recent_memories"] == []


class TestArchivingReflectsImmediately:
    def test_archiving_a_memory_moves_it_from_active_to_archived(
        self, client: TestClient
    ) -> None:
        knowledge = _seed(client, canonical_fact="Currently active.")
        before = client.get("/api/v1/dashboard").json()["vault_stats"]
        assert before["active_count"] == 1
        assert before["archived_count"] == 0

        archived = dc_replace(knowledge, valid_until=datetime.utcnow())
        client.app.state.vault_writer.write(archived)

        after = client.get("/api/v1/dashboard").json()["vault_stats"]
        assert after["active_count"] == 0
        assert after["archived_count"] == 1

    def test_archiving_the_only_active_project_clears_current_milestone(
        self, client: TestClient
    ) -> None:
        knowledge = _seed(
            client, canonical_fact="Milestone project.", memory_type=MemoryType.PROJECT
        )
        before = client.get("/api/v1/dashboard").json()["project_overview"]
        assert before["current_milestone"]["fact"] == "Milestone project."

        archived = dc_replace(knowledge, valid_until=datetime.utcnow())
        client.app.state.vault_writer.write(archived)

        after = client.get("/api/v1/dashboard").json()["project_overview"]
        assert after["current_milestone"] is None


class TestMemorySpaceSwitchReloadsTheDashboard:
    def test_dashboard_content_is_space_scoped_and_updates_on_switch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in (
            "HAVEN_VAULT_DIR",
            "HAVEN_CONCEPT_DIR",
            "HAVEN_CHECKPOINT_DIR",
            "HAVEN_WRITE_TRACE_DIR",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.chdir(tmp_path)

        from obsidian.server.main import app

        with TestClient(app, base_url="http://localhost") as client:
            default_id = client.get("/api/v1/spaces").json()["active_space_id"]
            work = client.post(
                "/api/v1/spaces", json={"name": "Work", "root": str(tmp_path / "Work")}
            ).json()

            _seed(client, canonical_fact="Only visible in Default.")
            assert client.get("/api/v1/dashboard").json()["vault_stats"]["total_memories"] == 1

            client.post(f"/api/v1/spaces/{work['id']}/activate", json={})
            assert client.get("/api/v1/dashboard").json()["vault_stats"]["total_memories"] == 0

            _seed(client, canonical_fact="Only visible in Work.")
            work_dashboard = client.get("/api/v1/dashboard").json()
            assert work_dashboard["vault_stats"]["total_memories"] == 1
            assert work_dashboard["recent_memories"][0]["canonical_fact"] == (
                "Only visible in Work."
            )

            client.post(f"/api/v1/spaces/{default_id}/activate", json={})
            default_dashboard = client.get("/api/v1/dashboard").json()
            assert default_dashboard["vault_stats"]["total_memories"] == 1
            assert default_dashboard["recent_memories"][0]["canonical_fact"] == (
                "Only visible in Default."
            )


class TestSelectingANewVaultRootReloadsTheDashboard:
    """``POST /api/v1/vault`` itself -- not just the Memory-Spaces
    migration/activation paths ``test_memory_spaces.py`` already covers --
    rebuilds every vault-scoped collaborator on ``app.state`` in place, so
    the very next dashboard read reflects the new (empty) vault, with no
    restart. Uses ``monkeypatch.chdir`` into ``tmp_path`` (mirroring
    ``test_memory_spaces.py``'s ``_unconfigured_client``), since this route
    persists the choice to ``config/vault_selection.json`` relative to the
    process CWD and must never touch the real repo's copy of that file.
    """

    def test_switching_vault_root_empties_then_repopulates_the_dashboard(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in (
            "HAVEN_VAULT_DIR",
            "HAVEN_CONCEPT_DIR",
            "HAVEN_CHECKPOINT_DIR",
            "HAVEN_WRITE_TRACE_DIR",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.chdir(tmp_path)

        from obsidian.server.main import app

        with TestClient(app, base_url="http://localhost") as client:
            _seed(client, canonical_fact="In the original, unconfigured vault.")
            assert client.get("/api/v1/dashboard").json()["vault_stats"]["total_memories"] == 1

            new_root = tmp_path / "NewVaultRoot"
            response = client.post("/api/v1/vault", json={"root": str(new_root)})
            assert response.status_code == 200, response.text
            assert response.json()["created"] is True

            # Immediately reflects the new, empty vault -- no restart.
            after_switch = client.get("/api/v1/dashboard").json()
            assert after_switch["vault_stats"]["total_memories"] == 0

            _seed(client, canonical_fact="In the newly selected vault.")
            after_write = client.get("/api/v1/dashboard").json()
            assert after_write["vault_stats"]["total_memories"] == 1
            assert after_write["recent_memories"][0]["canonical_fact"] == (
                "In the newly selected vault."
            )

    def test_reselecting_the_same_root_is_non_destructive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for var in (
            "HAVEN_VAULT_DIR",
            "HAVEN_CONCEPT_DIR",
            "HAVEN_CHECKPOINT_DIR",
            "HAVEN_WRITE_TRACE_DIR",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.chdir(tmp_path)

        from obsidian.server.main import app

        with TestClient(app, base_url="http://localhost") as client:
            root = tmp_path / "MyVault"
            first = client.post("/api/v1/vault", json={"root": str(root)})
            assert first.json()["created"] is True

            _seed(client, canonical_fact="Should survive a re-select of the same root.")

            second = client.post("/api/v1/vault", json={"root": str(root)})
            assert second.status_code == 200
            assert second.json()["created"] is False

            body = client.get("/api/v1/dashboard").json()
            assert body["vault_stats"]["total_memories"] == 1
