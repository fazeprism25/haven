"""Read routes that touch ``app.state.concept_graph`` must serialize against
writes through the same lock ``save_memory``/``commit_memory`` already hold.

Before this fix, ``retrieve_context``/``retrieve_working_context``
(``main.py``) and the dashboard's ``GET /api/v1/dashboard``, ``/inspect``,
``/inspect/memory/{id}`` (``dashboard.py``) read ``ConceptGraph`` -- a plain
``defaultdict(set)`` with no internal locking -- with no synchronization at
all, while FastAPI dispatches these sync ``def`` routes onto a shared thread
pool. A concurrent write mutating the same graph could race a read
iterating it (``RuntimeError: Set changed size during iteration``). These
tests prove the read routes now block on the same lock a write holds,
rather than asserting anything about wall-clock timing.
"""

from __future__ import annotations

import threading
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


class TestReadRoutesShareTheWriteLock:
    @pytest.mark.parametrize(
        "make_request",
        [
            lambda client: client.post("/api/v1/retrieve_context", json={"query": "x"}),
            lambda client: client.get("/api/v1/dashboard"),
            lambda client: client.get("/api/v1/dashboard/inspect", params={"query": "x"}),
        ],
        ids=["retrieve_context", "dashboard", "dashboard_inspect"],
    )
    def test_read_route_blocks_while_write_lock_is_held(
        self, client: TestClient, make_request
    ) -> None:
        from obsidian.server.main import app as fastapi_app

        held_lock = fastapi_app.state.write_lock
        result: dict = {}

        def call_read_route() -> None:
            result["response"] = make_request(client)

        held_lock.acquire()
        try:
            thread = threading.Thread(target=call_read_route)
            thread.start()
            # The read route must not be able to proceed while the lock is
            # held -- a generous but bounded wait, not a timing assertion.
            thread.join(timeout=0.3)
            assert thread.is_alive(), "read route ran without waiting for the write lock"
        finally:
            held_lock.release()

        thread.join(timeout=5)
        assert not thread.is_alive()
        assert result["response"].status_code == 200

    def test_dashboard_and_main_share_the_same_lock_object(self, client: TestClient) -> None:
        from obsidian.server.main import _write_lock, app as fastapi_app

        assert fastapi_app.state.write_lock is _write_lock
