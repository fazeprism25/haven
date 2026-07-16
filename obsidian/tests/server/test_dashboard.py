"""Tests for the Memory Dashboard API (``/api/v1/dashboard/*``).

Mirrors ``test_retrieve_context.py``'s and ``test_save_memory.py``'s
conventions: an isolated ``tmp_path`` vault/concept directory per test via
``HAVEN_VAULT_DIR``/``HAVEN_CONCEPT_DIR``, driven through ``TestClient``,
seeding state via the real ``vault_writer``/``ontology_pipeline``
collaborators on ``app.state`` rather than hand-written fixtures.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import (
    DecisionMetadata,
    DecisionStatus,
    KnowledgeObject,
    with_decision_metadata,
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


def _section(body: dict, domain: str, memory_type: str) -> list:
    """Return the ``DashboardMemory`` list for *memory_type* under *domain*.

    ``[]`` both when the domain has no entry for that type at all (the
    common case -- ``DomainSection.by_type`` omits empty types) and when
    the domain itself is somehow missing, so callers can assert emptiness
    without a ``KeyError``.
    """
    for section in body["domains"]:
        if section["domain"] == domain:
            return section["by_type"].get(memory_type, [])
    return []


#: Every (domain, memory_type value) pair the V2 ontology defines -- see
#: obsidian/core/memory_domain.py's MEMORY_TYPE_DOMAIN.
ALL_DOMAIN_TYPES = [
    ("personal", "preference"), ("personal", "interest"), ("personal", "trait"),
    ("personal", "habit"), ("personal", "skill"), ("personal", "goal"),
    ("work", "project"), ("work", "task"), ("work", "decision"),
    ("work", "open_question"), ("work", "blocker"),
    ("work", "implementation_state"), ("work", "code_area"),
    ("knowledge", "fact"), ("knowledge", "belief"), ("knowledge", "person"),
    ("knowledge", "event"), ("knowledge", "rule"),
]


# ---------------------------------------------------------------------------
# Empty vault
# ---------------------------------------------------------------------------


class TestEmptyVault:
    def test_returns_empty_sections(self, client: TestClient) -> None:
        response = client.get("/api/v1/dashboard")
        assert response.status_code == 200
        body = response.json()
        assert body["recent_memories"] == []
        assert {s["domain"] for s in body["domains"]} == {
            "personal",
            "work",
            "knowledge",
        }
        for section in body["domains"]:
            assert section["by_type"] == {}

    def test_zeroed_stats(self, client: TestClient) -> None:
        body = client.get("/api/v1/dashboard").json()
        assert body["vault_stats"]["total_memories"] == 0
        assert body["vault_stats"]["active_count"] == 0
        assert body["vault_stats"]["archived_count"] == 0
        assert body["vault_stats"]["average_confidence"] == 0.0
        assert body["vault_stats"]["average_importance"] == 0.0
        assert body["vault_stats"]["by_domain"] == {
            "personal": 0,
            "work": 0,
            "knowledge": 0,
        }
        assert body["concept_stats"]["total_concepts"] == 0
        assert body["concept_stats"]["total_relationships"] == 0
        assert body["concept_stats"]["total_attachments"] == 0
        assert body["retrieval_stats"]["vault_memory_count"] == 0


# ---------------------------------------------------------------------------
# Memory-type sections
# ---------------------------------------------------------------------------


class TestMemoryTypeSections:
    @pytest.mark.parametrize(
        "memory_type, domain, type_value",
        [
            (MemoryType.PROJECT, "work", "project"),
            (MemoryType.DECISION, "work", "decision"),
            (MemoryType.BELIEF, "knowledge", "belief"),
            (MemoryType.PREFERENCE, "personal", "preference"),
            (MemoryType.TASK, "work", "task"),
            (MemoryType.INTEREST, "personal", "interest"),
            (MemoryType.TRAIT, "personal", "trait"),
            (MemoryType.HABIT, "personal", "habit"),
            (MemoryType.FACT, "knowledge", "fact"),
        ],
    )
    def test_memory_appears_in_its_own_section_only(
        self,
        client: TestClient,
        memory_type: MemoryType,
        domain: str,
        type_value: str,
    ) -> None:
        _seed(client, canonical_fact="A seeded fact.", memory_type=memory_type)

        body = client.get("/api/v1/dashboard").json()
        own = _section(body, domain, type_value)
        assert len(own) == 1
        assert own[0]["canonical_fact"] == "A seeded fact."

        for other_domain, other_type in ALL_DOMAIN_TYPES:
            if (other_domain, other_type) == (domain, type_value):
                continue
            assert _section(body, other_domain, other_type) == []

    def test_memory_fields_are_complete(self, client: TestClient) -> None:
        knowledge = _seed(
            client,
            canonical_fact="Haven uses Claude.",
            memory_type=MemoryType.PROJECT,
            confidence=0.8,
            importance=0.7,
            confirmation_count=3,
        )

        body = client.get("/api/v1/dashboard").json()
        entry = _section(body, "work", "project")[0]
        assert entry["id"] == str(knowledge.id)
        assert entry["canonical_fact"] == "Haven uses Claude."
        assert entry["memory_type"] == "project"
        assert entry["confidence"] == 0.8
        assert entry["importance"] == 0.7
        assert entry["confirmation_count"] == 3
        assert entry["valid_from"] is not None
        assert entry["valid_until"] is None
        assert entry["last_confirmed"] is None
        assert entry["decision"] is None
        assert entry["topics"] == []


# ---------------------------------------------------------------------------
# Decision Memory
# ---------------------------------------------------------------------------


class TestDecisionMemory:
    def test_decision_without_metadata_has_null_decision_field(
        self, client: TestClient
    ) -> None:
        _seed(
            client,
            canonical_fact="Build Manager AI first.",
            memory_type=MemoryType.DECISION,
        )

        body = client.get("/api/v1/dashboard").json()
        entry = _section(body, "work", "decision")[0]
        assert entry["decision"] is None

    def test_decision_with_metadata_is_fully_projected(
        self, client: TestClient
    ) -> None:
        app = client.app
        old_id = UUID("11111111-1111-1111-1111-111111111111")
        new_id = UUID("22222222-2222-2222-2222-222222222222")
        knowledge = with_decision_metadata(
            KnowledgeObject(
                canonical_fact="Use Qdrant.", memory_type=MemoryType.DECISION
            ),
            DecisionMetadata(
                reason="Better filtering support.",
                alternatives_considered=["Chroma", "Pinecone"],
                status=DecisionStatus.SUPERSEDED,
                supersedes=old_id,
                superseded_by=new_id,
            ),
        )
        app.state.vault_writer.write(knowledge)
        app.state.ontology_pipeline.process(knowledge)

        body = client.get("/api/v1/dashboard").json()
        entry = _section(body, "work", "decision")[0]
        assert entry["decision"] == {
            "reason": "Better filtering support.",
            "alternatives_considered": ["Chroma", "Pinecone"],
            "status": "superseded",
            "supersedes": str(old_id),
            "superseded_by": str(new_id),
        }

    def test_last_confirmed_is_projected(self, client: TestClient) -> None:
        now = datetime.utcnow()
        knowledge = KnowledgeObject(
            canonical_fact="Confirmed decision.",
            memory_type=MemoryType.DECISION,
            last_confirmed=now,
        )
        app = client.app
        app.state.vault_writer.write(knowledge)
        app.state.ontology_pipeline.process(knowledge)

        body = client.get("/api/v1/dashboard").json()
        entry = _section(body, "work", "decision")[0]
        assert entry["last_confirmed"] is not None

    def test_non_decision_memory_never_carries_decision_field(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="A plain project.", memory_type=MemoryType.PROJECT)

        body = client.get("/api/v1/dashboard").json()
        entry = _section(body, "work", "project")[0]
        assert entry["decision"] is None

    def test_inspect_memory_by_id_projects_decision_metadata(
        self, client: TestClient
    ) -> None:
        app = client.app
        knowledge = with_decision_metadata(
            KnowledgeObject(canonical_fact="Use Qdrant.", memory_type=MemoryType.DECISION),
            DecisionMetadata(reason="Better filtering support."),
        )
        app.state.vault_writer.write(knowledge)
        app.state.ontology_pipeline.process(knowledge)

        body = client.get(
            f"/api/v1/dashboard/inspect/memory/{knowledge.id}"
        ).json()
        assert body["source_memory_id"] == str(knowledge.id)


# ---------------------------------------------------------------------------
# Recent memories
# ---------------------------------------------------------------------------


class TestRecentMemories:
    def test_sorted_most_recent_first(self, client: TestClient) -> None:
        now = datetime.utcnow()
        _seed(client, canonical_fact="Oldest.", valid_from=now - timedelta(days=2))
        _seed(client, canonical_fact="Newest.", valid_from=now)
        _seed(client, canonical_fact="Middle.", valid_from=now - timedelta(days=1))

        recent = client.get("/api/v1/dashboard").json()["recent_memories"]
        assert [m["canonical_fact"] for m in recent] == [
            "Newest.",
            "Middle.",
            "Oldest.",
        ]

    def test_recent_limit_query_param(self, client: TestClient) -> None:
        for i in range(5):
            _seed(client, canonical_fact=f"Fact {i}.")

        response = client.get("/api/v1/dashboard", params={"recent_limit": 2})
        assert len(response.json()["recent_memories"]) == 2

    def test_recent_limit_out_of_range_is_rejected(self, client: TestClient) -> None:
        response = client.get("/api/v1/dashboard", params={"recent_limit": 0})
        assert response.status_code == 422

    def test_response_is_not_cacheable(self, client: TestClient) -> None:
        """A new memory must be visible on the very next fetch.

        Without ``Cache-Control: no-store``, a browser (or intermediate
        proxy) is free to serve an earlier, stale response for the exact
        same URL instead of re-fetching — which would make a just-created
        memory silently absent from ``recent_memories`` until whatever
        cached the response expires, even though the API itself already
        returns the correct data (see ``test_sorted_most_recent_first``
        above).
        """
        response = client.get("/api/v1/dashboard")
        assert response.headers["cache-control"] == "no-store"


# ---------------------------------------------------------------------------
# Vault statistics
# ---------------------------------------------------------------------------


class TestVaultStats:
    def test_counts_and_averages(self, client: TestClient) -> None:
        _seed(
            client,
            canonical_fact="A.",
            memory_type=MemoryType.FACT,
            confidence=1.0,
            importance=1.0,
        )
        _seed(
            client,
            canonical_fact="B.",
            memory_type=MemoryType.TASK,
            confidence=0.0,
            importance=0.0,
        )

        stats = client.get("/api/v1/dashboard").json()["vault_stats"]
        assert stats["total_memories"] == 2
        assert stats["by_type"]["fact"] == 1
        assert stats["by_type"]["task"] == 1
        assert stats["by_type"]["project"] == 0
        assert stats["average_confidence"] == 0.5
        assert stats["average_importance"] == 0.5

    def test_active_vs_archived(self, client: TestClient) -> None:
        _seed(client, canonical_fact="Still active.")
        _seed(
            client,
            canonical_fact="Superseded.",
            valid_until=datetime.utcnow(),
        )

        stats = client.get("/api/v1/dashboard").json()["vault_stats"]
        assert stats["active_count"] == 1
        assert stats["archived_count"] == 1


# ---------------------------------------------------------------------------
# Concept / retrieval statistics
# ---------------------------------------------------------------------------


class TestConceptAndRetrievalStats:
    def test_concept_count_reflects_detected_concepts(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="I use Terraform for infra.")

        body = client.get("/api/v1/dashboard").json()
        assert body["concept_stats"]["total_concepts"] >= 1
        assert body["retrieval_stats"]["concept_count"] == (
            body["concept_stats"]["total_concepts"]
        )

    def test_retrieval_stats_config_matches_retrieval_config_defaults(
        self, client: TestClient
    ) -> None:
        from obsidian.ontology.retrieval_config import RetrievalConfig

        config = client.get("/api/v1/dashboard").json()["retrieval_stats"]["config"]
        assert config["max_results"] == RetrievalConfig().max_results
        assert config["minimum_candidate_score"] == (
            RetrievalConfig().minimum_candidate_score
        )

    def test_vault_memory_count_matches_total(self, client: TestClient) -> None:
        _seed(client, canonical_fact="One.")
        _seed(client, canonical_fact="Two.")

        body = client.get("/api/v1/dashboard").json()
        assert body["retrieval_stats"]["vault_memory_count"] == 2
        assert body["vault_stats"]["total_memories"] == 2


# ---------------------------------------------------------------------------
# Retrieval Inspector — by query
# ---------------------------------------------------------------------------


class TestInspectQuery:
    def test_matches_retrieve_context_include_trace(self, client: TestClient) -> None:
        _seed(client, canonical_fact="Haven uses Claude for extraction.")

        inspector = client.get(
            "/api/v1/dashboard/inspect", params={"query": "Claude"}
        ).json()
        retrieve = client.post(
            "/api/v1/retrieve_context",
            json={"query": "Claude", "include_trace": True},
        ).json()

        assert inspector["context"] == retrieve["context"]
        assert inspector["trace"]["query"] == retrieve["trace"]["query"]
        # retrieval_latency_ms is a wall-clock reading and legitimately
        # differs between two separate calls; every other stat must match.
        inspector_stats = dict(inspector["trace"]["pipeline_stats"])
        retrieve_stats = dict(retrieve["trace"]["pipeline_stats"])
        del inspector_stats["retrieval_latency_ms"]
        del retrieve_stats["retrieval_latency_ms"]
        assert inspector_stats == retrieve_stats
        assert inspector["source_memory_id"] is None

    def test_no_match_returns_empty_context_and_trace(
        self, client: TestClient
    ) -> None:
        body = client.get(
            "/api/v1/dashboard/inspect", params={"query": "nothing here"}
        ).json()
        assert body["context"] == ""
        assert body["trace"]["candidates"] == []

    def test_requires_query_param(self, client: TestClient) -> None:
        response = client.get("/api/v1/dashboard/inspect")
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Retrieval Inspector — by memory id
# ---------------------------------------------------------------------------


class TestInspectMemory:
    def test_inspects_seeded_memory_by_id(self, client: TestClient) -> None:
        knowledge = _seed(
            client, canonical_fact="I use Terraform for infra."
        )

        body = client.get(
            f"/api/v1/dashboard/inspect/memory/{knowledge.id}"
        ).json()
        assert body["source_memory_id"] == str(knowledge.id)
        assert any(
            c["knowledge_object_id"] == str(knowledge.id)
            for c in body["trace"]["candidates"]
        )

    def test_unknown_uuid_returns_404(self, client: TestClient) -> None:
        response = client.get(
            "/api/v1/dashboard/inspect/memory/00000000-0000-0000-0000-000000000000"
        )
        assert response.status_code == 404

    def test_malformed_id_returns_404(self, client: TestClient) -> None:
        response = client.get("/api/v1/dashboard/inspect/memory/not-a-uuid")
        assert response.status_code == 404

    def test_picks_up_writes_without_restart(self, client: TestClient) -> None:
        app = client.app
        knowledge = KnowledgeObject(canonical_fact="Written after startup.")
        app.state.vault_writer.write(knowledge)
        app.state.ontology_pipeline.process(knowledge)

        response = client.get(f"/api/v1/dashboard/inspect/memory/{knowledge.id}")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Memory Inspector — ontology (concepts/relationships) attached to a memory
# ---------------------------------------------------------------------------


class TestInspectMemoryOntology:
    def test_returns_attached_concepts_with_aliases(self, client: TestClient) -> None:
        knowledge = _seed(client, canonical_fact="I use Terraform for infra.")

        body = client.get(
            f"/api/v1/dashboard/inspect/memory/{knowledge.id}"
        ).json()

        assert body["ontology"] is not None
        labels = [c["label"] for c in body["ontology"]["concepts"]]
        assert "Terraform" in labels
        for concept in body["ontology"]["concepts"]:
            assert isinstance(concept["aliases"], list)
            assert isinstance(concept["id"], str)

    def test_relationships_are_deduplicated_and_labelled(
        self, client: TestClient
    ) -> None:
        knowledge = _seed(client, canonical_fact="Haven uses Terraform for infra.")

        body = client.get(
            f"/api/v1/dashboard/inspect/memory/{knowledge.id}"
        ).json()

        relationships = body["ontology"]["relationships"]
        ids = [r["id"] for r in relationships]
        assert len(ids) == len(set(ids))
        for relationship in relationships:
            assert relationship["source_label"]
            assert relationship["target_label"]

    def test_no_capitalised_terms_yields_empty_ontology(
        self, client: TestClient
    ) -> None:
        knowledge = _seed(client, canonical_fact="zzz nonconceptual filler text")

        body = client.get(
            f"/api/v1/dashboard/inspect/memory/{knowledge.id}"
        ).json()

        assert body["ontology"] == {"concepts": [], "relationships": []}

    def test_inspect_by_query_route_leaves_ontology_none(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="Haven uses Claude for extraction.")

        body = client.get(
            "/api/v1/dashboard/inspect", params={"query": "Claude"}
        ).json()

        assert body["ontology"] is None


# ---------------------------------------------------------------------------
# Retrieval Inspector — WorkingContext / StructuredPrompt extension
# ---------------------------------------------------------------------------


class TestInspectorWorkingContextExtension:
    """The Inspector's additive coverage past ``ContextBuilder``.

    Mirrors :class:`TestWorkingContexts`' fail-open conventions below —
    ``working_contexts``/``structured_prompt`` are populated via the same
    unmodified ``query_working_context``/``query_structured`` calls, and
    degrade to ``None`` the same way on an older or broken engine.
    """

    def test_inspect_query_includes_working_contexts_and_structured_prompt(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="Haven uses Claude for extraction.")

        body = client.get(
            "/api/v1/dashboard/inspect", params={"query": "Claude"}
        ).json()

        assert body["working_contexts"] is not None
        assert len(body["working_contexts"]) >= 1
        assert body["structured_prompt"] is not None
        assert isinstance(body["structured_prompt"], str)

    def test_working_context_matches_query_working_context_endpoint_shape(
        self, client: TestClient
    ) -> None:
        knowledge = _seed(client, canonical_fact="I use Terraform for infra.")

        body = client.get(
            f"/api/v1/dashboard/inspect/memory/{knowledge.id}"
        ).json()

        assert body["working_contexts"] is not None
        context = body["working_contexts"][0]
        assert "key" in context
        assert "title" in context
        assert "kind" in context
        assert "state" in context
        assert "buckets" in context
        assert "status" in context["state"]

    def test_no_match_still_returns_working_contexts(
        self, client: TestClient
    ) -> None:
        # WorkingContextBuilder always returns at least the GENERAL context,
        # even when the query itself matched no candidates.
        body = client.get(
            "/api/v1/dashboard/inspect", params={"query": "nothing here"}
        ).json()

        assert body["context"] == ""
        assert body["working_contexts"] is not None
        assert len(body["working_contexts"]) >= 1

    def test_graceful_fallback_when_query_working_context_raises(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from obsidian.memory_engine.engine import MemoryEngine

        def _boom(self, raw_query: str):
            raise RuntimeError("Working Context assembly is broken")

        monkeypatch.setattr(MemoryEngine, "query_working_context", _boom)
        _seed(client, canonical_fact="Haven uses Claude for extraction.")

        response = client.get(
            "/api/v1/dashboard/inspect", params={"query": "Claude"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["working_contexts"] is None
        assert body["structured_prompt"] is None
        # The rest of the Inspector is unaffected by the failure.
        assert body["context"] != ""
        assert body["trace"]["candidates"]

    def test_graceful_fallback_when_query_working_context_missing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(
            "obsidian.memory_engine.engine.MemoryEngine.query_working_context"
        )
        knowledge = _seed(client, canonical_fact="I use Terraform for infra.")

        response = client.get(
            f"/api/v1/dashboard/inspect/memory/{knowledge.id}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["working_contexts"] is None
        assert body["structured_prompt"] is None
        assert body["source_memory_id"] == str(knowledge.id)


# ---------------------------------------------------------------------------
# Working Contexts ("Resume Work" panel)
# ---------------------------------------------------------------------------


class TestWorkingContexts:
    def test_empty_vault_yields_single_general_context(self, client: TestClient) -> None:
        body = client.get("/api/v1/dashboard").json()
        contexts = body["working_contexts"]
        assert contexts == [
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

    def test_general_fallback_for_memory_with_no_ontology_evidence(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="zzz nonconceptual filler text with no proper nouns")

        contexts = client.get("/api/v1/dashboard").json()["working_contexts"]
        assert len(contexts) == 1
        general = contexts[0]
        assert general["key"] == "ctx:general"
        assert general["kind"] == "general"
        assert general["memory_count"] == 1
        assert general["current_focus"] == (
            "zzz nonconceptual filler text with no proper nouns"
        )

    def test_multiple_concepts_produce_multiple_contexts(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="I use Terraform for infra.", memory_type=MemoryType.FACT)
        _seed(
            client,
            canonical_fact="I use Kubernetes for orchestration.",
            memory_type=MemoryType.FACT,
        )

        contexts = client.get("/api/v1/dashboard").json()["working_contexts"]
        assert len(contexts) >= 2
        titles = {c["title"] for c in contexts}
        assert "Terraform" in titles
        assert "Kubernetes" in titles
        # A TOPIC context's title is the concept's human-readable label, not
        # its raw UUID (WorkingContextBuilder names it after the anchor
        # concept's id; the dashboard enriches it via ConceptGraph).
        for context in contexts:
            if context["kind"] == "topic":
                assert "-" not in context["title"] or len(context["title"]) < 20

    def test_status_reflects_role_buckets(self, client: TestClient) -> None:
        # Status is a pure projection of which role buckets are populated
        # (WorkingContextState.from_buckets): ACTIVE needs a pending task or
        # open question — a goal alone does not imply ACTIVE.
        _seed(
            client,
            canonical_fact="I plan to adopt Kubernetes operators.",
            memory_type=MemoryType.GOAL,
        )
        _seed(
            client,
            canonical_fact="I need to upgrade the Kubernetes cluster.",
            memory_type=MemoryType.TASK,
        )
        _seed(
            client,
            canonical_fact="I decided to use Terraform over Ansible.",
            memory_type=MemoryType.DECISION,
        )
        _seed(
            client,
            canonical_fact="I use Terraform for infra.",
            memory_type=MemoryType.FACT,
        )

        contexts = client.get("/api/v1/dashboard").json()["working_contexts"]
        by_title = {c["title"]: c for c in contexts}

        assert by_title["Kubernetes"]["status"] == "active"
        assert by_title["Kubernetes"]["current_goal"] == (
            "I plan to adopt Kubernetes operators."
        )
        assert by_title["Kubernetes"]["pending_tasks"] == [
            "I need to upgrade the Kubernetes cluster."
        ]
        assert by_title["Ansible"]["status"] == "decided"
        assert by_title["Ansible"]["recent_decisions"] == [
            "I decided to use Terraform over Ansible."
        ]
        assert by_title["Terraform"]["status"] == "reference"

    def test_memory_count_matches_total_bucket_members(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="zzz nonconceptual filler one")
        _seed(client, canonical_fact="zzz nonconceptual filler two")

        contexts = client.get("/api/v1/dashboard").json()["working_contexts"]
        general = next(c for c in contexts if c["key"] == "ctx:general")
        assert general["memory_count"] == 2

    def test_graceful_fallback_when_query_working_context_raises(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from obsidian.memory_engine.engine import MemoryEngine

        def _boom(self, raw_query: str):
            raise RuntimeError("Working Context assembly is broken")

        monkeypatch.setattr(MemoryEngine, "query_working_context", _boom)
        _seed(client, canonical_fact="Still here.", memory_type=MemoryType.PROJECT)

        response = client.get("/api/v1/dashboard")
        assert response.status_code == 200
        body = response.json()
        assert body["working_contexts"] is None
        # The rest of the dashboard is unaffected by the failure.
        assert len(_section(body, "work", "project")) == 1
        assert body["vault_stats"]["total_memories"] == 1

    def test_graceful_fallback_when_query_working_context_missing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(
            "obsidian.memory_engine.engine.MemoryEngine.query_working_context"
        )
        _seed(client, canonical_fact="Still here too.", memory_type=MemoryType.PROJECT)

        response = client.get("/api/v1/dashboard")
        assert response.status_code == 200
        body = response.json()
        assert body["working_contexts"] is None
        assert len(_section(body, "work", "project")) == 1


# ---------------------------------------------------------------------------
# Project Overview (Mission Control)
# ---------------------------------------------------------------------------


class TestProjectOverview:
    def test_empty_vault_yields_fully_gapped_overview(self, client: TestClient) -> None:
        body = client.get("/api/v1/dashboard").json()
        overview = body["project_overview"]
        assert overview is not None
        assert overview["current_objective"] is None
        assert overview["current_milestone"] is None
        assert overview["current_focus"] is None
        assert overview["active_tasks"] == []
        assert overview["active_blockers"] == []
        assert overview["open_questions"] == []
        assert overview["recent_decisions"] == []
        assert overview["recommended_next_action"] is None
        assert set(overview["gaps"]) == {
            "current_objective",
            "decisions",
            "active_tasks",
            "blockers",
            "constraints",
            "implementation_state",
            "code_areas",
            "open_questions",
        }
        assert overview["field_coverage"] == 0.0
        assert overview["generated_at"] is not None

    def test_goal_becomes_current_objective(self, client: TestClient) -> None:
        _seed(
            client,
            canonical_fact="Ship the Project Overview page.",
            memory_type=MemoryType.GOAL,
        )

        overview = client.get("/api/v1/dashboard").json()["project_overview"]
        assert overview["current_objective"]["fact"] == "Ship the Project Overview page."

    def test_active_project_becomes_current_milestone(self, client: TestClient) -> None:
        _seed(client, canonical_fact="Haven v1.0 release candidate.", memory_type=MemoryType.PROJECT)

        overview = client.get("/api/v1/dashboard").json()["project_overview"]
        assert overview["current_milestone"]["fact"] == "Haven v1.0 release candidate."

    def test_archived_project_is_not_current_milestone(self, client: TestClient) -> None:
        _seed(
            client,
            canonical_fact="An old, closed-out project.",
            memory_type=MemoryType.PROJECT,
            valid_until=datetime.utcnow(),
        )

        overview = client.get("/api/v1/dashboard").json()["project_overview"]
        assert overview["current_milestone"] is None

    # One memory per test (not four mixed together): with several
    # dissimilar facts in one digest query, DeterministicRanker's ontology
    # activation can favor the ones that happen to match a concept, and
    # AcceptanceStage's score-gap cut (see docs/architecture/
    # HAVEN_STRESS_TEST.md's M16) can then legitimately drop an unrelated
    # one — real retrieval behavior, not a bug in the overview builder, but
    # it would make a combined test flaky/order-sensitive. Testing one
    # category at a time keeps each assertion about ProjectState's own
    # bucketing, not about ranking interactions between unrelated facts.
    def test_task_is_tracked_as_active_task(self, client: TestClient) -> None:
        _seed(client, canonical_fact="Write the dashboard tests.", memory_type=MemoryType.TASK)

        overview = client.get("/api/v1/dashboard").json()["project_overview"]
        assert [t["fact"] for t in overview["active_tasks"]] == ["Write the dashboard tests."]
        assert "active_tasks" not in overview["gaps"]

    def test_blocker_is_tracked_as_active_blocker(self, client: TestClient) -> None:
        _seed(
            client,
            canonical_fact="Blocked on the schema review.",
            memory_type=MemoryType.BLOCKER,
        )

        overview = client.get("/api/v1/dashboard").json()["project_overview"]
        assert [b["fact"] for b in overview["active_blockers"]] == [
            "Blocked on the schema review."
        ]
        assert "blockers" not in overview["gaps"]

    def test_open_question_is_tracked(self, client: TestClient) -> None:
        _seed(
            client,
            canonical_fact="Should this ship behind a flag?",
            memory_type=MemoryType.OPEN_QUESTION,
        )

        overview = client.get("/api/v1/dashboard").json()["project_overview"]
        assert [q["fact"] for q in overview["open_questions"]] == [
            "Should this ship behind a flag?"
        ]
        assert "open_questions" not in overview["gaps"]

    def test_decision_is_tracked_as_recent_decision(self, client: TestClient) -> None:
        _seed(
            client,
            canonical_fact="Decided to use Postgres over Mongo.",
            memory_type=MemoryType.DECISION,
        )

        overview = client.get("/api/v1/dashboard").json()["project_overview"]
        assert [d["fact"] for d in overview["recent_decisions"]] == [
            "Decided to use Postgres over Mongo."
        ]
        assert "decisions" not in overview["gaps"]

    def test_recommended_next_action_prefers_blocker_over_task(
        self, client: TestClient
    ) -> None:
        _seed(client, canonical_fact="Write the dashboard tests.", memory_type=MemoryType.TASK)
        _seed(
            client,
            canonical_fact="Blocked on the schema review.",
            memory_type=MemoryType.BLOCKER,
        )

        overview = client.get("/api/v1/dashboard").json()["project_overview"]
        action = overview["recommended_next_action"]
        assert action["reason"] == "blocker"
        assert action["item"]["fact"] == "Blocked on the schema review."

    def test_recommended_next_action_falls_back_to_objective(
        self, client: TestClient
    ) -> None:
        _seed(
            client,
            canonical_fact="Ship the Project Overview page.",
            memory_type=MemoryType.GOAL,
        )

        overview = client.get("/api/v1/dashboard").json()["project_overview"]
        action = overview["recommended_next_action"]
        assert action["reason"] == "current_objective"
        assert action["item"]["fact"] == "Ship the Project Overview page."

    def test_graceful_fallback_when_query_with_trace_raises(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from obsidian.memory_engine.engine import MemoryEngine

        def _boom(self, raw_query: str):
            raise RuntimeError("ProjectState assembly is broken")

        monkeypatch.setattr(MemoryEngine, "query_with_trace", _boom)
        _seed(client, canonical_fact="Still here.", memory_type=MemoryType.PROJECT)

        response = client.get("/api/v1/dashboard")
        assert response.status_code == 200
        body = response.json()
        assert body["project_overview"] is None
        # The rest of the dashboard is unaffected by the failure.
        assert len(_section(body, "work", "project")) == 1
        assert body["vault_stats"]["total_memories"] == 1


# ---------------------------------------------------------------------------
# Dashboard UI (GET /dashboard)
# ---------------------------------------------------------------------------


class TestDashboardUi:
    def test_serves_html(self, client: TestClient) -> None:
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "<title>Haven — Long-term memory for your AI</title>" in response.text

    def test_references_dashboard_api_only(self, client: TestClient) -> None:
        """The page must consume the existing JSON API, not reimplement it."""
        html = client.get("/dashboard").text
        assert "API_BASE = '/api/v1/dashboard'" in html
        assert "${API_BASE}/inspect" in html

    def test_includes_resume_work_panel(self, client: TestClient) -> None:
        """The Working Context panel ("Current focus") is additive to the
        existing sections. Section headings carry the product-facing names
        from the dashboard redesign; the element ids are the stable API."""
        html = client.get("/dashboard").text
        assert "Current focus" in html
        assert 'id="resume-grid"' in html
        assert 'id="resume-unavailable"' in html
        # Existing sections/functionality are still present, untouched.
        assert "Browse memories" in html
        assert 'id="recent-list"' in html
        assert "Ask your memory" in html

    def test_includes_expandable_pipeline_stages(self, client: TestClient) -> None:
        """The Inspector's pipeline stages are additive and individually expandable."""
        html = client.get("/dashboard").text
        assert 'id="inspector-stages"' in html
        # Every stage the extended Inspector must show, additive to the
        # pre-existing candidates table/pipeline visualization.
        for stage_name in (
            "Query Rewrite",
            "Retrieved Candidates",
            "Acceptance Decisions",
            "Slot Allocation",
            "WorkingContext Assembly",
            "WorkingContextState",
            "Structured Prompt",
        ):
            assert stage_name in html
        # Stages use the native <details>/<summary> expand/collapse pattern.
        assert 'class="stage-details"' in html
        assert "<summary>" in html
        # Existing candidates table and pipeline visualization are untouched.
        assert 'id="inspector-table"' in html
        assert 'function renderVisualizationSection(trace, c' in html
