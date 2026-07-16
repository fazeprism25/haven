"""Tests for the Query Rewriting dashboard setting
(``GET``/``PUT /api/v1/settings/query-rewriting``) and its wiring into every
``MemoryEngine`` construction site in :mod:`obsidian.server.main` and
:mod:`obsidian.server.dashboard`.

Covers: the setting defaults to off, persists across a simulated restart (a
fresh ``TestClient`` pointed at the same on-disk config), never touches disk
for a tier-1 (env-managed) deployment (mirroring
``test_memory_spaces.py``'s same tier-1 "no disk I/O" contract for
``config/spaces.json``), and that toggling it actually changes retrieval
behaviour with no server restart -- off is byte-for-byte identical to a
``MemoryEngine`` with no rewriter configured (see
``obsidian.memory_engine.engine``'s own "Disabled or absent" contract), on
wires in a configured ``QueryRewriter`` and the resulting
``RetrievalTrace.rewriting_enabled``/``rewritten_queries`` fields the
Retrieval Inspector exposes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Sequence

import pytest
from fastapi.testclient import TestClient

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine.query_rewriter import RewriteResult


class _FixedRewriter:
    """Test double for QueryRewriter: returns a pre-set RewriteResult.

    Duck-typed to satisfy the only contract MemoryEngine relies on -- a
    ``rewrite(query: str) -> RewriteResult`` method -- without any real LLM
    call. Mirrors ``obsidian/tests/test_engine.py``'s fixture of the same
    name (duplicated here rather than imported -- see
    ``feedback-minimal-scaffolding``: reuse patterns verbatim, don't add a
    new shared module for one file's benefit).
    """

    def __init__(self, rewrites: Sequence[str] = ()) -> None:
        self._rewrites = tuple(rewrites)
        self.queries_seen: List[str] = []

    def rewrite(self, query: str) -> RewriteResult:
        self.queries_seen.append(query)
        return RewriteResult(original=query, rewrites=self._rewrites)


def _env_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Tier 1 (env vars) -- mirrors every other server test file's fixture."""
    monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))

    from obsidian.server.main import app

    return TestClient(app)


def _unconfigured_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Tier 2/3 -- no env vars, CWD'd into an empty tmp_path so the setting's
    config file resolves under tmp_path/config/, never the real repo's."""
    for var in ("HAVEN_VAULT_DIR", "HAVEN_CONCEPT_DIR", "HAVEN_CHECKPOINT_DIR", "HAVEN_WRITE_TRACE_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)

    from obsidian.server.main import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Default / persistence
# ---------------------------------------------------------------------------


def test_defaults_to_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        res = client.get("/api/v1/settings/query-rewriting")
        assert res.status_code == 200
        assert res.json() == {"enabled": False}


def test_put_enables_and_persists_to_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        res = client.put("/api/v1/settings/query-rewriting", json={"enabled": True})
        assert res.status_code == 200
        assert res.json() == {"enabled": True}

        assert client.get("/api/v1/settings/query-rewriting").json() == {"enabled": True}

    config_path = tmp_path / "config" / "query_rewriting_setting.json"
    assert config_path.exists()
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"enabled": True}


def test_setting_survives_a_simulated_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        client.put("/api/v1/settings/query-rewriting", json={"enabled": True})

    # A fresh TestClient re-runs `lifespan()` from scratch, exactly like a
    # real process restart would -- the persisted file is all that carries
    # the setting across it.
    with _unconfigured_client(tmp_path, monkeypatch) as restarted_client:
        res = restarted_client.get("/api/v1/settings/query-rewriting")
        assert res.json() == {"enabled": True}


def test_put_off_persists_and_is_the_default_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        client.put("/api/v1/settings/query-rewriting", json={"enabled": True})
        res = client.put("/api/v1/settings/query-rewriting", json={"enabled": False})
        assert res.json() == {"enabled": False}
        assert client.get("/api/v1/settings/query-rewriting").json() == {"enabled": False}


def test_env_managed_deployment_never_touches_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _env_client(tmp_path, monkeypatch) as client:
        res = client.put("/api/v1/settings/query-rewriting", json={"enabled": True})
        assert res.json() == {"enabled": True}
        # In-process toggle still works for this run (see main.py's
        # _active_query_rewriter) even though nothing was persisted.
        assert client.get("/api/v1/settings/query-rewriting").json() == {"enabled": True}

    assert not (tmp_path / "config" / "query_rewriting_setting.json").exists()


# ---------------------------------------------------------------------------
# Retrieval behavior: OFF reproduces today's deterministic pipeline exactly
# ---------------------------------------------------------------------------


def test_off_never_calls_the_configured_rewriter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        app_state = client.app.state
        fake = _FixedRewriter(rewrites=("infrastructure tooling",))
        app_state.query_rewriter = fake
        # Setting left at its off-by-default value on purpose.

        knowledge = KnowledgeObject(
            canonical_fact="Haven uses Terraform for infra.",
            memory_type=MemoryType.FACT,
        )
        app_state.vault_writer.write(knowledge)
        app_state.ontology_pipeline.process(knowledge)

        response = client.post(
            "/api/v1/retrieve_context",
            json={"query": "Terraform", "include_trace": True},
        )
        body = response.json()

        assert fake.queries_seen == []
        assert body["trace"]["rewriting_enabled"] is False
        assert body["trace"]["rewritten_queries"] == []
        assert "Haven uses Terraform for infra." in body["context"]


def test_off_leaves_a_query_only_the_rewrite_would_match_unanswered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        app_state = client.app.state
        app_state.query_rewriter = _FixedRewriter(rewrites=("Terraform",))

        knowledge = KnowledgeObject(
            canonical_fact="Haven uses Terraform for infra.",
            memory_type=MemoryType.FACT,
        )
        app_state.vault_writer.write(knowledge)
        app_state.ontology_pipeline.process(knowledge)

        # "cloud provisioning question" shares no keyword with the stored fact; only the
        # (unused, since the setting is off) rewrite "Terraform" would match.
        response = client.post(
            "/api/v1/retrieve_context",
            json={"query": "cloud provisioning question", "include_trace": True},
        )
        body = response.json()

        assert body["context"] == ""
        assert body["trace"]["rewritten_queries"] == []


# ---------------------------------------------------------------------------
# Retrieval behavior: ON wires the configured rewriter in
# ---------------------------------------------------------------------------


def test_on_surfaces_a_memory_only_the_rewrite_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        client.put("/api/v1/settings/query-rewriting", json={"enabled": True})

        app_state = client.app.state
        fake = _FixedRewriter(rewrites=("Terraform",))
        app_state.query_rewriter = fake

        knowledge = KnowledgeObject(
            canonical_fact="Haven uses Terraform for infra.",
            memory_type=MemoryType.FACT,
        )
        app_state.vault_writer.write(knowledge)
        app_state.ontology_pipeline.process(knowledge)

        response = client.post(
            "/api/v1/retrieve_context",
            json={"query": "cloud provisioning question", "include_trace": True},
        )
        body = response.json()

        assert fake.queries_seen == ["cloud provisioning question"]
        assert body["trace"]["rewriting_enabled"] is True
        assert body["trace"]["rewritten_queries"] == ["Terraform"]
        assert "Haven uses Terraform for infra." in body["context"]


def test_toggle_takes_effect_without_reconstructing_the_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No restart required: the same TestClient sees the new behavior on
    the very next request after PUT, both switching on and back off."""
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        app_state = client.app.state
        app_state.query_rewriter = _FixedRewriter(rewrites=("Terraform",))

        knowledge = KnowledgeObject(
            canonical_fact="Haven uses Terraform for infra.",
            memory_type=MemoryType.FACT,
        )
        app_state.vault_writer.write(knowledge)
        app_state.ontology_pipeline.process(knowledge)

        payload = {"query": "cloud provisioning question", "include_trace": True}

        before = client.post("/api/v1/retrieve_context", json=payload).json()
        assert before["context"] == ""

        client.put("/api/v1/settings/query-rewriting", json={"enabled": True})
        during = client.post("/api/v1/retrieve_context", json=payload).json()
        assert "Haven uses Terraform for infra." in during["context"]

        client.put("/api/v1/settings/query-rewriting", json={"enabled": False})
        after = client.post("/api/v1/retrieve_context", json=payload).json()
        assert after["context"] == ""


def test_dashboard_inspector_respects_the_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _unconfigured_client(tmp_path, monkeypatch) as client:
        client.put("/api/v1/settings/query-rewriting", json={"enabled": True})

        app_state = client.app.state
        app_state.query_rewriter = _FixedRewriter(rewrites=("Terraform",))

        knowledge = KnowledgeObject(
            canonical_fact="Haven uses Terraform for infra.",
            memory_type=MemoryType.FACT,
        )
        app_state.vault_writer.write(knowledge)
        app_state.ontology_pipeline.process(knowledge)

        response = client.get(
            "/api/v1/dashboard/inspect", params={"query": "cloud provisioning question"}
        )
        body = response.json()

        assert body["trace"]["rewriting_enabled"] is True
        assert body["trace"]["rewritten_queries"] == ["Terraform"]
