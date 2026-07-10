"""A stray non-Haven ``.md`` file dropped into the vault directory must
surface as a clear 422, never an opaque 500.

``MemoryStore.load()`` raises ``MemoryEngineError`` for any file under the
vault directory missing its ``id`` frontmatter -- which is exactly what
happens when a user creates a throwaway note directly in their opened
Obsidian vault (the same folder Haven's ``vault_dir`` points at). Every
route that reloads the vault (``retrieve_context``, ``retrieve_working_context``,
``save_memory``, ``preview_memory``, ``commit_memory``, the dashboard
routes) shares this failure mode. See ``obsidian/server/main.py``'s
``_memory_engine_error_handler``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))
    monkeypatch.setenv("HAVEN_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("HAVEN_WRITE_TRACE_DIR", str(tmp_path / "write_traces"))

    from obsidian.server.main import app

    with TestClient(app) as test_client:
        yield test_client


def _drop_stray_note(tmp_path: Path) -> None:
    stray = tmp_path / "vault" / "My random Obsidian note.md"
    stray.write_text("# Just some unrelated note\n\nNothing Haven wrote.\n", encoding="utf-8")


class TestStrayVaultFileReturnsClear422:
    def test_retrieve_context_returns_422_not_500(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        _drop_stray_note(tmp_path)

        response = client.post("/api/v1/retrieve_context", json={"query": "anything"})

        assert response.status_code == 422
        body = response.json()
        assert "detail" in body
        assert "My random Obsidian note.md" in body["detail"]

    def test_dashboard_returns_422_not_500(self, client: TestClient, tmp_path: Path) -> None:
        _drop_stray_note(tmp_path)

        response = client.get("/api/v1/dashboard")

        assert response.status_code == 422
        assert "My random Obsidian note.md" in response.json()["detail"]

    def test_save_memory_returns_422_not_500(self, client: TestClient, tmp_path: Path) -> None:
        _drop_stray_note(tmp_path)

        response = client.post(
            "/api/v1/memory",
            json={"canonical_fact": "A brand new fact.", "memory_type": "fact"},
        )

        assert response.status_code == 422
        assert "My random Obsidian note.md" in response.json()["detail"]
