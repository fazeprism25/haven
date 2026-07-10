"""Tests for Memory Spaces (``/api/v1/spaces*``) and the migration that
seeds ``config/spaces.json`` from whichever vault-selection tier was
already active.

Unlike every other server test file, these deliberately exercise all 3
vault-resolution tiers, not just tier 1 (``HAVEN_VAULT_DIR`` env vars) --
so most fixtures here ``monkeypatch.chdir(tmp_path)`` and clear the env
vars themselves, rather than reusing the module-wide ``client`` fixture
convention. This also guards the tier-1 "never touches disk" contract:
without it, every other test file in this repo (all of which set
``HAVEN_VAULT_DIR``) would read/write the real repo's
``config/spaces.json`` on every run.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _env_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Tier 1 (env vars) -- mirrors every other server test file's fixture."""
    monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))
    monkeypatch.setenv("HAVEN_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("HAVEN_WRITE_TRACE_DIR", str(tmp_path / "write_traces"))

    from obsidian.server.main import app

    return TestClient(app)


def _unconfigured_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Tier 2/3 -- no env vars, CWD'd into an empty tmp_path so nothing
    resolves against the real repo (``config/``, ``haven_data/``)."""
    for var in (
        "HAVEN_VAULT_DIR",
        "HAVEN_CONCEPT_DIR",
        "HAVEN_CHECKPOINT_DIR",
        "HAVEN_WRITE_TRACE_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)

    from obsidian.server.main import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Migration synthesis
# ---------------------------------------------------------------------------


def test_tier1_env_managed_synthesizes_default_space_without_touching_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _env_client(tmp_path, monkeypatch) as client:
        res = client.get("/api/v1/spaces")
        assert res.status_code == 200
        data = res.json()
        assert data["env_managed"] is True
        assert len(data["spaces"]) == 1
        space = data["spaces"][0]
        assert space["name"] == "Default"
        assert space["root"] is None
        assert data["active_space_id"] == space["id"]

    assert not (tmp_path / "config" / "spaces.json").exists()


def test_tier3_unconfigured_synthesizes_default_space_named_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        res = client.get("/api/v1/spaces")
        assert res.status_code == 200
        data = res.json()
        assert data["env_managed"] is False
        assert len(data["spaces"]) == 1
        assert data["spaces"][0]["name"] == "Default"
        assert data["spaces"][0]["root"] is None

    assert (tmp_path / "config" / "spaces.json").exists()


def test_tier2_persisted_root_synthesizes_space_named_after_root_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault_root = tmp_path / "MyObsidianVault"
    vault_root.mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "vault_selection.json").write_text(
        json.dumps({"vault_root": str(vault_root)}), encoding="utf-8"
    )

    for var in (
        "HAVEN_VAULT_DIR",
        "HAVEN_CONCEPT_DIR",
        "HAVEN_CHECKPOINT_DIR",
        "HAVEN_WRITE_TRACE_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)

    from obsidian.server.main import app

    with TestClient(app) as client:
        res = client.get("/api/v1/spaces")
        assert res.status_code == 200
        data = res.json()
        space = data["spaces"][0]
        assert space["name"] == "MyObsidianVault"
        assert space["root"] == str(vault_root)

        # No recomputation via _paths_for_root at migration time -- the
        # resolved dirs are copied verbatim from _resolve_initial_vault_paths.
        vault_info = client.get("/api/v1/vault").json()
        assert vault_info["vault_dir"] == str(vault_root / "vault")
        assert vault_info["concept_dir"] == str(vault_root / "concepts")


def test_tier3_flat_layout_is_not_recomputed_through_paths_for_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The critical correctness case from the plan: an unconfigured
    deployment's real on-disk layout is FLAT (``haven_data/checkpoints``),
    not nested under ``.haven/`` like ``_paths_for_root`` would produce.
    Migration must preserve the flat layout, not silently move it."""
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        vault_info = client.get("/api/v1/vault").json()
        # _resolve_initial_vault_paths returns these relative (CWD == tmp_path
        # here, via monkeypatch.chdir) -- never .resolve()'d, matching
        # pre-existing tier-3 behaviour exactly.
        assert vault_info["vault_dir"] == str(Path("haven_data") / "vault")
        assert vault_info["concept_dir"] == str(Path("haven_data") / "concepts")

    spaces = json.loads((tmp_path / "config" / "spaces.json").read_text(encoding="utf-8"))
    space = spaces["spaces"][0]
    assert space["checkpoint_dir"] == str(Path("haven_data") / "checkpoints")
    assert space["write_trace_dir"] == str(Path("haven_data") / "write_traces")
    # NOT the _paths_for_root nested convention:
    assert ".haven" not in space["checkpoint_dir"]


# ---------------------------------------------------------------------------
# Create / activate round-trip, isolation
# ---------------------------------------------------------------------------


def test_create_and_activate_space_rebuilds_app_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        work_root = tmp_path / "Work"
        res = client.post("/api/v1/spaces", json={"name": "Work", "root": str(work_root)})
        assert res.status_code == 201
        work_space = res.json()
        assert work_space["name"] == "Work"

        # Creating does not activate.
        assert client.get("/api/v1/spaces").json()["active_space_id"] != work_space["id"]
        assert client.app.state.active_space_id != work_space["id"]

        res = client.post(f"/api/v1/spaces/{work_space['id']}/activate", json={})
        assert res.status_code == 200

        assert client.app.state.active_space_id == work_space["id"]
        assert client.app.state.vault_dir == work_root / "vault"
        assert client.app.state.concept_dir == work_root / "concepts"
        assert client.get("/api/v1/spaces").json()["active_space_id"] == work_space["id"]


def test_spaces_are_isolated_across_switches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        default_id = client.get("/api/v1/spaces").json()["active_space_id"]

        res = client.post(
            "/api/v1/spaces", json={"name": "Work", "root": str(tmp_path / "Work")}
        )
        work_id = res.json()["id"]

        # Seed demo data only into "Work".
        client.post(f"/api/v1/spaces/{work_id}/activate", json={})
        seed_res = client.post("/api/v1/dev/seed_demo")
        assert seed_res.status_code == 200
        work_count = client.get("/api/v1/vault").json()["memory_count"]
        assert work_count > 0

        # Switch back to Default -- must be empty, unaffected by Work's seed.
        client.post(f"/api/v1/spaces/{default_id}/activate", json={})
        assert client.get("/api/v1/vault").json()["memory_count"] == 0

        # Switch back to Work -- its data must still be there.
        client.post(f"/api/v1/spaces/{work_id}/activate", json={})
        assert client.get("/api/v1/vault").json()["memory_count"] == work_count


# ---------------------------------------------------------------------------
# Overlap guard
# ---------------------------------------------------------------------------


def test_create_rejects_exact_duplicate_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        root = tmp_path / "Shared"
        first = client.post("/api/v1/spaces", json={"name": "A", "root": str(root)})
        assert first.status_code == 201

        dup = client.post("/api/v1/spaces", json={"name": "B", "root": str(root)})
        assert dup.status_code == 409


def test_create_rejects_root_as_parent_of_existing_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        child = tmp_path / "Parent" / "Child"
        child.mkdir(parents=True)
        first = client.post("/api/v1/spaces", json={"name": "Child", "root": str(child)})
        assert first.status_code == 201

        parent_res = client.post(
            "/api/v1/spaces", json={"name": "Parent", "root": str(tmp_path / "Parent")}
        )
        assert parent_res.status_code == 409


def test_create_rejects_root_as_child_of_existing_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        parent = tmp_path / "Parent"
        first = client.post("/api/v1/spaces", json={"name": "Parent", "root": str(parent)})
        assert first.status_code == 201

        child_res = client.post(
            "/api/v1/spaces",
            json={"name": "Child", "root": str(parent / "Child")},
        )
        assert child_res.status_code == 409


def test_create_accepts_sibling_unrelated_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        first = client.post(
            "/api/v1/spaces", json={"name": "A", "root": str(tmp_path / "A")}
        )
        assert first.status_code == 201
        second = client.post(
            "/api/v1/spaces", json={"name": "B", "root": str(tmp_path / "B")}
        )
        assert second.status_code == 201


# ---------------------------------------------------------------------------
# Delete guards
# ---------------------------------------------------------------------------


def test_delete_rejects_active_space(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        default_id = client.get("/api/v1/spaces").json()["active_space_id"]
        res = client.delete(f"/api/v1/spaces/{default_id}")
        assert res.status_code == 409


def test_delete_rejects_last_remaining_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        default_id = client.get("/api/v1/spaces").json()["active_space_id"]
        work = client.post(
            "/api/v1/spaces", json={"name": "Work", "root": str(tmp_path / "Work")}
        ).json()

        # Switch to Work so Default is no longer active, then delete it --
        # allowed since Default is neither active nor last remaining.
        client.post(f"/api/v1/spaces/{work['id']}/activate", json={})
        res = client.delete(f"/api/v1/spaces/{default_id}")
        assert res.status_code == 204

        # Only "Work" remains, and it's the active space -- deleting it must
        # be rejected on both grounds (active AND last remaining).
        remaining = client.get("/api/v1/spaces").json()["spaces"]
        assert len(remaining) == 1
        res = client.delete(f"/api/v1/spaces/{work['id']}")
        assert res.status_code == 409


def test_delete_removes_registry_entry_without_touching_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        root = tmp_path / "Work"
        work = client.post("/api/v1/spaces", json={"name": "Work", "root": str(root)}).json()
        assert root.exists()

        res = client.delete(f"/api/v1/spaces/{work['id']}")
        assert res.status_code == 204
        assert root.exists()  # never deleted from disk

        ids = [s["id"] for s in client.get("/api/v1/spaces").json()["spaces"]]
        assert work["id"] not in ids


# ---------------------------------------------------------------------------
# env_managed lockdown
# ---------------------------------------------------------------------------


def test_env_managed_rejects_create_rename_delete_activate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _env_client(tmp_path, monkeypatch) as client:
        space_id = client.get("/api/v1/spaces").json()["active_space_id"]

        assert client.post(
            "/api/v1/spaces", json={"name": "X", "root": str(tmp_path / "X")}
        ).status_code == 409
        assert client.patch(f"/api/v1/spaces/{space_id}", json={"name": "Y"}).status_code == 409
        assert client.delete(f"/api/v1/spaces/{space_id}").status_code == 409
        assert client.post(f"/api/v1/spaces/{space_id}/activate", json={}).status_code == 409


# ---------------------------------------------------------------------------
# PATCH root editing
# ---------------------------------------------------------------------------


def test_patch_root_on_active_space_rebuilds_app_state_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        default_id = client.get("/api/v1/spaces").json()["active_space_id"]
        new_root = tmp_path / "NewRoot"

        res = client.patch(f"/api/v1/spaces/{default_id}", json={"root": str(new_root)})
        assert res.status_code == 200
        assert res.json()["root"] == str(new_root)

        # No separate activate call needed.
        assert client.app.state.vault_dir == new_root / "vault"
        assert client.get("/api/v1/vault").json()["root"] == str(new_root)


def test_patch_root_on_non_active_space_only_updates_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        default_id = client.get("/api/v1/spaces").json()["active_space_id"]
        work = client.post(
            "/api/v1/spaces", json={"name": "Work", "root": str(tmp_path / "Work")}
        ).json()
        active_vault_dir_before = client.app.state.vault_dir

        new_root = tmp_path / "WorkMoved"
        res = client.patch(f"/api/v1/spaces/{work['id']}", json={"root": str(new_root)})
        assert res.status_code == 200

        # Active space (Default) untouched.
        assert client.app.state.vault_dir == active_vault_dir_before
        assert client.app.state.active_space_id == default_id

        spaces = client.get("/api/v1/spaces").json()["spaces"]
        updated = next(s for s in spaces if s["id"] == work["id"])
        assert updated["root"] == str(new_root)


def test_patch_rejects_overlapping_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        default_id = client.get("/api/v1/spaces").json()["active_space_id"]
        other_root = tmp_path / "Other"
        client.post("/api/v1/spaces", json={"name": "Other", "root": str(other_root)})

        res = client.patch(f"/api/v1/spaces/{default_id}", json={"root": str(other_root)})
        assert res.status_code == 409


def test_patch_rename_only_is_registry_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        default_id = client.get("/api/v1/spaces").json()["active_space_id"]
        vault_dir_before = client.app.state.vault_dir

        res = client.patch(f"/api/v1/spaces/{default_id}", json={"name": "Renamed"})
        assert res.status_code == 200
        assert res.json()["name"] == "Renamed"
        assert client.app.state.vault_dir == vault_dir_before


# ---------------------------------------------------------------------------
# Pending Memory Review confirmation gate on activate
# ---------------------------------------------------------------------------


def test_activate_with_pending_review_requires_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        default_id = client.get("/api/v1/spaces").json()["active_space_id"]
        work = client.post(
            "/api/v1/spaces", json={"name": "Work", "root": str(tmp_path / "Work")}
        ).json()

        # Fake a pending review directly -- exercising the real preview/LLM
        # flow is out of scope for this isolation test.
        client.app.state.pending_reviews["fake-review-id"] = object()

        res = client.post(f"/api/v1/spaces/{work['id']}/activate", json={})
        assert res.status_code == 409
        assert res.json()["detail"]["pending_review_count"] == 1
        # Did not switch.
        assert client.app.state.active_space_id == default_id

        res = client.post(
            f"/api/v1/spaces/{work['id']}/activate", json={"confirm": True}
        )
        assert res.status_code == 200
        assert client.app.state.active_space_id == work["id"]
        # _configure_vault_state always resets pending_reviews.
        assert client.app.state.pending_reviews == {}


def test_activate_without_pending_review_needs_no_confirm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        work = client.post(
            "/api/v1/spaces", json={"name": "Work", "root": str(tmp_path / "Work")}
        ).json()
        res = client.post(f"/api/v1/spaces/{work['id']}/activate", json={})
        assert res.status_code == 200


# ---------------------------------------------------------------------------
# Robustness audit: activating a space whose directories can't be (re)created
# (e.g. permission-denied, or the root was deleted out from under Haven)
# must return a clean 400, the same OSError-to-400 contract select_vault
# already uses -- not a raw 500 leaving app.state half-reconfigured.
# ---------------------------------------------------------------------------


def test_activate_returns_clean_400_when_directory_creation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        work_root = tmp_path / "Work"
        created = client.post(
            "/api/v1/spaces", json={"name": "Work", "root": str(work_root)}
        )
        assert created.status_code == 201
        work_space = created.json()
        default_id = client.app.state.active_space_id

        original_mkdir = Path.mkdir

        def _boom_mkdir(self: Path, *args, **kwargs):
            if self == work_root / "vault":
                raise PermissionError("simulated permission-denied creating vault dir")
            return original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", _boom_mkdir)

        res = client.post(f"/api/v1/spaces/{work_space['id']}/activate", json={})
        assert res.status_code == 400, res.text
        assert "Could not activate Memory Space" in res.json()["detail"]
        # Did not switch -- the failed activation left the previous space active.
        assert client.app.state.active_space_id == default_id
