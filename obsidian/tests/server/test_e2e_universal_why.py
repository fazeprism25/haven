"""End-to-end: the "Universal Why" affordance -- every explainable object in
the dashboard opens correctly, and the evidence it shows matches the
underlying data it claims to explain.

Scenario 5 of the Haven end-to-end suite. Per ``dashboard.html``'s own
"Universal Why?" comment (search ``.why-btn``), the same small button is
reused by memory cards, activity items, Project Overview fields, Working
Context buckets, and benchmark results -- and always funnels into one of
two things:

* ``openMemoryDetail`` -> ``GET /api/v1/dashboard/inspect/memory/{id}``
  (memory cards, activity items, Working Context bucket members, and
  Project Overview's ``StateRefTrace``-backed fields all resolve to a real
  memory id and use this same call).
* ``renderBenchmarkDetail`` -> ``GET /api/v1/dashboard/benchmarks/{id}``
  (benchmark rows).

This file drives both, for every object kind the dashboard actually opens,
and checks the evidence returned is *real*, not just present:

* a memory's ontology concepts really were detected from its own fact text;
* a memory's decision metadata is exactly what was set;
* an imported memory's provenance names the real source file;
* a memory found via a Working Context bucket opens to the same id;
* a benchmark case's conversation/judge evidence matches its committed
  dataset/result files on disk exactly (using the repo's own real, already
  -committed benchmark artifacts -- ``decision_basic_001`` -- rather than
  synthetic fixtures, since ``benchmark_explorer`` always reads the real
  ``benchmarks/`` directories, not an injectable path).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.models import (
    DecisionMetadata,
    DecisionStatus,
    KnowledgeObject,
    with_decision_metadata,
)
from obsidian.manager_ai.pipeline import ManagerPipeline


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))
    monkeypatch.setenv("HAVEN_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("HAVEN_WRITE_TRACE_DIR", str(tmp_path / "write_traces"))

    from obsidian.server.main import app

    with TestClient(app) as test_client:
        yield test_client


def _seed(client: TestClient, **kwargs) -> KnowledgeObject:
    app = client.app
    knowledge = KnowledgeObject(**kwargs)
    app.state.vault_writer.write(knowledge)
    app.state.ontology_pipeline.process(knowledge)
    return knowledge


class _ScriptedLLM:
    def __init__(
        self,
        extract_response: str,
        classify_responses: Sequence[str] = (),
        importance_responses: Sequence[str] = (),
    ) -> None:
        self._extract_response = extract_response
        self._classify_responses = list(classify_responses)
        self._importance_responses = list(importance_responses)

    def generate(self, prompt: str) -> str:
        if "Conversation:\n" in prompt:
            return self._extract_response
        if "Available memory types:" in prompt:
            return self._classify_responses.pop(0)
        if "Classification:\n" in prompt:
            return self._importance_responses.pop(0)
        raise AssertionError(f"Unrecognised prompt shape:\n{prompt}")


def _install_scripted_llm(
    client: TestClient,
    extract_response: str,
    classify_responses: Sequence[str] = (),
    importance_responses: Sequence[str] = (),
) -> None:
    llm = _ScriptedLLM(extract_response, classify_responses, importance_responses)
    client.app.state.manager_pipeline = ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )


# ---------------------------------------------------------------------------
# Memory cards / activity items -- openMemoryDetail
# ---------------------------------------------------------------------------


class TestMemoryWhyOpensWithRealEvidence:
    def test_ontology_evidence_matches_the_memorys_own_proper_nouns(
        self, client: TestClient
    ) -> None:
        knowledge = _seed(
            client, canonical_fact="Haven uses Terraform to manage infra."
        )

        body = client.get(f"/api/v1/dashboard/inspect/memory/{knowledge.id}").json()
        labels = {c["label"] for c in body["ontology"]["concepts"]}
        # Both proper nouns in the fact are detected -- not a fabricated
        # "why", but real ConceptGraph attachments this exact memory has.
        assert {"Haven", "Terraform"} <= labels

    def test_memory_with_no_proper_nouns_opens_with_honest_empty_evidence(
        self, client: TestClient
    ) -> None:
        knowledge = _seed(client, canonical_fact="zzz nonconceptual filler text")

        body = client.get(f"/api/v1/dashboard/inspect/memory/{knowledge.id}").json()
        assert body["ontology"] == {"concepts": [], "relationships": []}

    def test_decision_why_shows_the_exact_reason_and_alternatives_set(
        self, client: TestClient
    ) -> None:
        knowledge = with_decision_metadata(
            KnowledgeObject(
                canonical_fact="Use Qdrant for vector storage.",
                memory_type=MemoryType.DECISION,
            ),
            DecisionMetadata(
                reason="Best filtering support for our access patterns.",
                alternatives_considered=["Chroma", "Pinecone"],
                status=DecisionStatus.ACTIVE,
            ),
        )
        client.app.state.vault_writer.write(knowledge)
        client.app.state.ontology_pipeline.process(knowledge)

        body = client.get("/api/v1/dashboard").json()
        work_section = next(s for s in body["domains"] if s["domain"] == "work")
        dashboard_entry = work_section["by_type"]["decision"][0]
        assert dashboard_entry["decision"]["reason"] == (
            "Best filtering support for our access patterns."
        )
        assert dashboard_entry["decision"]["alternatives_considered"] == [
            "Chroma",
            "Pinecone",
        ]

    def test_self_candidate_in_the_trace_is_this_exact_memory_not_a_lookalike(
        self, client: TestClient
    ) -> None:
        knowledge = _seed(client, canonical_fact="Haven uses Terraform to manage infra.")
        _seed(client, canonical_fact="Haven uses Kubernetes for orchestration.")

        body = client.get(f"/api/v1/dashboard/inspect/memory/{knowledge.id}").json()
        self_candidate = next(
            c for c in body["trace"]["candidates"]
            if c["knowledge_object_id"] == str(knowledge.id)
        )
        assert self_candidate["canonical_fact"] == knowledge.canonical_fact

    def test_working_context_bucket_member_why_resolves_to_the_same_memory(
        self, client: TestClient
    ) -> None:
        """A memory surfaced inside a Working Context bucket (e.g. "Resume
        Work") is the same real memory a direct card-open would show --
        proving Working Context's "why" isn't a separately-summarized
        object with its own (possibly drifted) explanation."""
        knowledge = _seed(
            client,
            canonical_fact="The user decided to use Terraform over Ansible.",
            memory_type=MemoryType.DECISION,
        )

        contexts = client.get("/api/v1/dashboard").json()["working_contexts"]
        by_title = {c["title"]: c for c in contexts}
        assert knowledge.canonical_fact in by_title["Ansible"]["recent_decisions"]

        # The "Why?" affordance on that bucket member opens the same
        # inspector detail a direct card-open would.
        detail = client.get(
            f"/api/v1/dashboard/inspect/memory/{knowledge.id}"
        ).json()
        assert detail["source_memory_id"] == str(knowledge.id)


class TestImportedMemoryProvenanceWhy:
    """Complements ``test_import_obsidian.py``'s existing provenance
    inspector test with the "evidence matches underlying data" angle: the
    provenance shown for an imported memory names the *real* source file
    that was actually scanned/imported, not a placeholder."""

    def test_provenance_source_file_matches_the_real_imported_note(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        vault_root = tmp_path / "external_vault"
        vault_root.mkdir()
        note_path = vault_root / "project-notes.md"
        note_path.write_text(
            "# Project notes\n\nThe user uses Haven daily.\n", encoding="utf-8"
        )

        _install_scripted_llm(
            client,
            extract_response=json.dumps(
                [{"text": "The user uses Haven daily.", "evidence": "stated", "confidence": 0.9}]
            ),
            classify_responses=[
                json.dumps({"memory_type": "fact", "confidence": 0.9, "reason": "r"})
            ],
            importance_responses=[json.dumps({"score": 0.5, "reason": "r"})],
        )
        preview = client.post(
            "/api/v1/import/obsidian/preview",
            json={"root": str(vault_root), "source_file": "project-notes.md"},
        ).json()
        commit = client.post(
            "/api/v1/memory/commit",
            json={"review_id": preview["review_id"], "items": preview["items"]},
        ).json()

        why = client.get(
            f"/api/v1/dashboard/inspect/memory/{commit['id']}"
        ).json()
        assert why["provenance"]["source_file"] == "project-notes.md"
        assert why["provenance"]["source"] == "obsidian"


# ---------------------------------------------------------------------------
# Benchmark rows -- renderBenchmarkDetail, against real committed artifacts
# ---------------------------------------------------------------------------


class TestBenchmarkWhyMatchesCommittedArtifactsOnDisk:
    """``benchmark_explorer`` always reads the real repo's
    ``benchmarks/results/`` and ``benchmarks/datasets/`` directories (no env
    var to redirect it -- see that module's docstring), so this uses one of
    the repo's own already-committed, real (dataset, result) pairs rather
    than a synthetic fixture. If this specific case is ever removed from the
    repo, this test should be repointed at another committed pair."""

    _BENCHMARK_ID = "decision_basic_001"
    _ADAPTER = "bm25"
    _KIND = "base"

    def test_benchmark_appears_in_the_explorer_list(self, client: TestClient) -> None:
        body = client.get("/api/v1/dashboard/benchmarks").json()
        match = next(
            (
                r
                for r in body["rows"]
                if r["benchmark_id"] == self._BENCHMARK_ID and r["adapter"] == self._ADAPTER
            ),
            None,
        )
        assert match is not None
        assert match["has_result"] is True

    def test_detail_conversation_matches_the_dataset_file_on_disk_exactly(
        self, client: TestClient
    ) -> None:
        dataset_path = (
            Path(__file__).resolve().parents[3]
            / "benchmarks"
            / "datasets"
            / "decisions"
            / "basic_001.json"
        )
        on_disk = json.loads(dataset_path.read_text(encoding="utf-8"))

        detail = client.get(
            f"/api/v1/dashboard/benchmarks/{self._BENCHMARK_ID}",
            params={"adapter": self._ADAPTER, "kind": self._KIND},
        ).json()

        assert detail["conversation"] == on_disk["conversation"]
        assert detail["query"] == on_disk["query"]
        assert detail["expected"] == on_disk["expected"]

    def test_detail_judge_result_matches_the_result_file_on_disk_exactly(
        self, client: TestClient
    ) -> None:
        results_path = (
            Path(__file__).resolve().parents[3]
            / "benchmarks"
            / "results"
            / f"results_{self._ADAPTER}.json"
        )
        on_disk = json.loads(results_path.read_text(encoding="utf-8"))
        rows = on_disk if isinstance(on_disk, list) else on_disk.get("results", [])
        on_disk_row = next(r for r in rows if r["benchmark_id"] == self._BENCHMARK_ID)

        detail = client.get(
            f"/api/v1/dashboard/benchmarks/{self._BENCHMARK_ID}",
            params={"adapter": self._ADAPTER, "kind": self._KIND},
        ).json()

        assert detail["judge_result"]["passed"] == on_disk_row["passed"]
        assert detail["judge_result"]["score"] == on_disk_row["answer_score"]
        assert detail["judge_explanation"] == on_disk_row["judge_reason"]
        assert detail["answer"] == on_disk_row["answer"]

    def test_fields_no_committed_artifact_has_ever_captured_are_honestly_absent(
        self, client: TestClient
    ) -> None:
        """Universal Why must never fabricate evidence: fields this repo's
        benchmark artifacts have never captured (Working Context, the
        rendered Structured Prompt, a Retrieval Trace, Acceptance
        Decisions, ProjectState) come back explicitly null with a stated
        reason, not silently omitted or invented."""
        detail = client.get(
            f"/api/v1/dashboard/benchmarks/{self._BENCHMARK_ID}",
            params={"adapter": self._ADAPTER, "kind": self._KIND},
        ).json()

        for field in (
            "working_context",
            "structured_prompt",
            "retrieval_trace",
            "acceptance_decisions",
            "project_state",
        ):
            assert detail[field] is None
        assert field in detail["always_missing_fields"]
        assert detail["always_missing_reason"]

    def test_unknown_benchmark_combination_404s_rather_than_a_blank_panel(
        self, client: TestClient
    ) -> None:
        response = client.get(
            "/api/v1/dashboard/benchmarks/does-not-exist",
            params={"adapter": self._ADAPTER, "kind": "base"},
        )
        assert response.status_code == 404
