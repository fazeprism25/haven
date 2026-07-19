"""Tests for ``POST /api/v1/query/rewrite`` -- the browser extension's
typing-time rewrite-suggestion endpoint (see ``content/rewrite-suggestion.js``
and ``content/controller.js``'s ``onComposeInput``/``requestRewriteSuggestion``).

Distinct from the "Query Rewriting" dashboard setting covered in
``test_query_rewriting_setting.py``: that setting silently expands a query
with extra phrasings *during* ``retrieve_context``. This endpoint always
runs, regardless of that setting, and returns a single suggestion for the
extension to show the user before retrieval ever happens.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence

import pytest
from fastapi.testclient import TestClient

from obsidian.memory_engine.query_rewriter import RewriteResult


class _FixedRewriter:
    """Test double for QueryRewriter: returns a pre-set RewriteResult.

    Duck-typed to the only contract this endpoint relies on -- a
    ``rewrite(query: str) -> RewriteResult`` method -- without any real LLM
    call. Duplicated from ``test_query_rewriting_setting.py`` rather than
    imported -- see ``feedback-minimal-scaffolding``: reuse patterns
    verbatim, don't add a new shared module for two files' benefit.
    """

    def __init__(self, rewrites: Sequence[str] = ()) -> None:
        self._rewrites = tuple(rewrites)
        self.queries_seen: List[str] = []

    def rewrite(self, query: str) -> RewriteResult:
        self.queries_seen.append(query)
        return RewriteResult(original=query, rewrites=self._rewrites)


def _unconfigured_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Tier 2/3 -- no env vars, CWD'd into an empty tmp_path so nothing this
    test does can touch the real repo's config/ or haven_data/."""
    for var in ("HAVEN_VAULT_DIR", "HAVEN_CONCEPT_DIR", "HAVEN_CHECKPOINT_DIR", "HAVEN_WRITE_TRACE_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)

    from obsidian.server.main import app

    return TestClient(app)


def test_returns_the_rewrite_when_the_rewriter_produced_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        fake = _FixedRewriter(rewrites=(
            "Retrieve the latest Haven architecture, implementation decisions, "
            "current blockers, and recommended next development tasks.",
        ))
        client.app.state.query_rewriter = fake

        response = client.post(
            "/api/v1/query/rewrite",
            json={"query": "remind me where we left off"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "original": "remind me where we left off",
            "rewritten": (
                "Retrieve the latest Haven architecture, implementation decisions, "
                "current blockers, and recommended next development tasks."
            ),
            "changed": True,
        }
        assert fake.queries_seen == ["remind me where we left off"]


def test_no_suggestion_when_the_rewriter_produced_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        # Mirrors QueryRewriter's own fail-open contract (blank/failed/
        # no-op call) and its _parse_rewrites dedup, which already drops any
        # rewrite identical to the original -- either way this endpoint
        # must tell the caller "nothing to show", not echo the input as if
        # it were a real suggestion.
        client.app.state.query_rewriter = _FixedRewriter(rewrites=())

        response = client.post(
            "/api/v1/query/rewrite",
            json={"query": "What is Python?"},
        )
        assert response.status_code == 200
        assert response.json() == {
            "original": "What is Python?",
            "rewritten": "What is Python?",
            "changed": False,
        }


def test_runs_regardless_of_the_query_rewriting_dashboard_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike retrieve_context's internal expansion, this endpoint is not
    gated by GET/PUT /api/v1/settings/query-rewriting -- it's an
    independent, always-on feature the extension calls directly."""
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        assert client.get("/api/v1/settings/query-rewriting").json() == {"enabled": False}

        fake = _FixedRewriter(rewrites=("clearer phrasing",))
        client.app.state.query_rewriter = fake

        response = client.post("/api/v1/query/rewrite", json={"query": "vague draft"})
        assert response.json()["changed"] is True
        assert fake.queries_seen == ["vague draft"]


def test_blank_query_returns_no_suggestion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/v1/query/rewrite", json={"query": "   "})
        assert response.status_code == 200
        body = response.json()
        assert body["changed"] is False
        assert body["rewritten"] == body["original"]
