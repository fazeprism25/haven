"""Regression tests for the curated ENTITY_CAT -> CATEGORY IS_A bridge (Phase 2).

Replays the exact query text and the load-bearing conversation turns from
each of the 11 curated benchmark cases identified in
``docs/architecture/ENTITY_CAT_INVESTIGATION.md`` Task 5, through the real,
unmodified pipeline:

    KnowledgeObject (one per conversation turn, written verbatim)
        -> OntologyPipeline.process (OntologyManager -> OntologyValidator
           -> ConceptGraph -> ConceptWriter)
        -> HybridCandidateRetriever.retrieve_with_diagnostics(query)

Before this bridge, none of these targets were reachable via the ontology
path (``matched_by_ontology`` did not contain the target id) — that is
precisely the candidate-generation gap
``docs/architecture/ENTITY_CAT_INVESTIGATION.md`` Task 1 documented. Each
test here asserts the target now *is* reachable via the ontology path, and
a companion "bridge disabled" variant (monkeypatching the taxonomy lookup
to always miss) proves the fix is causally due to the curated bridge, not
an incidental keyword-path match.

This module does not touch the keyword path, ranking, or acceptance —
only asserts what candidate generation is able to find, matching the
docs' own definition of a "candidate-generation" fix.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Sequence
from uuid import uuid4

import pytest

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine.hybrid_candidate_retriever import HybridCandidateRetriever
from obsidian.memory_engine.memory_store import MemoryStore
from obsidian.memory_engine.vault_writer import VaultWriter
from obsidian.ontology import ontology_manager as ontology_manager_module
from obsidian.ontology.alias_index import AliasIndex
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.ontology_pipeline import OntologyPipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_ko(fact: str) -> KnowledgeObject:
    return KnowledgeObject(
        id=uuid4(),
        canonical_fact=fact,
        memory_type=MemoryType.FACT,
        importance=0.7,
        confidence=0.8,
    )


def ingest_and_retrieve(
    tmp_path: Path, facts: Sequence[str], query: str
) -> tuple[List[KnowledgeObject], "frozenset"]:
    """Write each of *facts* through the real OntologyPipeline, then run *query*
    through the real HybridCandidateRetriever. Returns (kos, matched_by_ontology)."""
    graph = ConceptGraph()
    pipeline = OntologyPipeline(graph, tmp_path / "concepts")
    kos = [make_ko(fact) for fact in facts]
    for ko in kos:
        pipeline.process(ko)

    vault_dir = tmp_path / "vault"
    writer = VaultWriter(vault_dir)
    for ko in kos:
        writer.write(ko)
    store = MemoryStore(vault_dir)
    store.load()

    alias_index = AliasIndex()
    alias_index.build(graph.all_concepts())

    retriever = HybridCandidateRetriever(alias_index, graph, store)
    _, provenance = retriever.retrieve_with_diagnostics(query)
    return kos, provenance.matched_by_ontology


# ---------------------------------------------------------------------------
# The 11 curated cases, (benchmark_id, decoy_facts, target_fact, query)
# ---------------------------------------------------------------------------

CURATED_CASES = [
    (
        "decision_reconstruction_basic_009",
        ["Need a new laptop for work -- deciding between a MacBook, a ThinkPad, and a Dell XPS."],
        "Went with the ThinkPad. The keyboard feel and the solid Linux support for my side projects made the difference over the MacBook and the Dell XPS.",
        "Which laptop did the user decide to buy?",
    ),
    (
        "decision_reconstruction_basic_016",
        ["Talked to my doctor about diet options -- keto, Mediterranean, and intermittent fasting all came up."],
        "Going with the Mediterranean approach. My doctor's recommendation given my cholesterol numbers, plus how sustainable it is long term, settled it over keto and fasting.",
        "What eating plan did the user decide to follow?",
    ),
    (
        "decision_reconstruction_basic_018",
        ["Setting up CI/CD for the new repo -- weighing GitHub Actions, CircleCI, and Jenkins."],
        "Going with GitHub Actions. It's already tied into the repo, there's nothing extra to host, and it does everything we need out of the box.",
        "Which CI/CD provider did the team decide to use?",
    ),
    (
        "decision_reconstruction_basic_019",
        ["Trying to settle on a note-taking app -- Notion, Obsidian, and Roam are the ones I keep coming back to."],
        "Going with Obsidian. The local markdown files and not being locked into a vendor mattered more to me than Notion's nicer out-of-the-box look.",
        "Which note-taking app did the user decide to use?",
    ),
    (
        "decisions_basic_020",
        [
            "We're discussing how to handle background jobs. I'm choosing between BullMQ, Sidekiq, and a simple cron-based approach.",
        ],
        "Given the Python migration, I'll go with Celery instead. It integrates naturally with our new Python services and Redis still works as the broker.",
        "What background job system did the user ultimately decide to use?",
    ),
    (
        "supersession_basic_003",
        ["I planned to use GPT for all tasks."],
        "I replaced that plan with GPT for planning and Qwen for coding.",
        "What is the current model strategy?",
    ),
    (
        "supersession_basic_033",
        ["I use MongoDB for my database."],
        "I migrated everything to PostgreSQL.",
        "What database does the user currently use?",
    ),
    (
        "supersession_basic_034",
        ["I started with Vue for the frontend.", "I switched to React."],
        "I've now moved the whole team to Svelte.",
        "What frontend framework is the user currently using?",
    ),
    (
        "supersession_basic_054",
        ["We decided to use Jira for tracking."],
        "Half a year later: we moved everything to Linear because Jira got too slow for us.",
        "What tracking tool is the team currently using?",
    ),
]


# ---------------------------------------------------------------------------
# Bridge produces a candidate for every curated case
# ---------------------------------------------------------------------------


class TestCuratedCasesReachableViaOntologyPath:
    @pytest.mark.parametrize(
        "benchmark_id,decoys,target,query",
        CURATED_CASES,
        ids=[c[0] for c in CURATED_CASES],
    )
    def test_target_fact_is_ontology_candidate(
        self, tmp_path, benchmark_id, decoys, target, query
    ):
        facts = list(decoys) + [target]
        kos, matched_by_ontology = ingest_and_retrieve(tmp_path, facts, query)
        target_ko = kos[-1]
        assert target_ko.id in matched_by_ontology, (
            f"{benchmark_id}: expected '{target}' to be reachable via the "
            f"ontology path for query {query!r}, but it was not in "
            f"matched_by_ontology (candidate-generation gap not fixed)"
        )


# ---------------------------------------------------------------------------
# Causality check — disabling the curated bridge reopens the gap
# ---------------------------------------------------------------------------


class TestBridgeIsCausallyResponsible:
    @pytest.mark.parametrize(
        "benchmark_id,decoys,target,query",
        CURATED_CASES,
        ids=[c[0] for c in CURATED_CASES],
    )
    def test_gap_reopens_when_taxonomy_lookup_disabled(
        self, tmp_path, monkeypatch, benchmark_id, decoys, target, query
    ):
        monkeypatch.setattr(
            ontology_manager_module, "lookup_category", lambda label: None
        )
        facts = list(decoys) + [target]
        kos, matched_by_ontology = ingest_and_retrieve(tmp_path, facts, query)
        target_ko = kos[-1]
        assert target_ko.id not in matched_by_ontology, (
            f"{benchmark_id}: target was reachable via the ontology path even "
            "with the curated taxonomy lookup disabled — the fix is not "
            "actually caused by the IS_A bridge for this case"
        )
