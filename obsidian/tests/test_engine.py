"""End-to-end integration tests for obsidian.memory_engine.engine.MemoryEngine.

These tests exercise the full retrieval pipeline through the single public
entry point, :meth:`MemoryEngine.query`, wiring together real (not mocked)
instances of every stage: HybridCandidateRetriever (which itself
orchestrates QueryResolver, ActivationSpreader, CandidateAssembler, and
KeywordCandidateRetriever), DeterministicRanker, DeterministicSlotAllocator,
and ContextBuilder. Each stage already has its own unit test suite; this
file verifies that MemoryEngine composes them correctly end to end.

Test groups
-----------
TestSimpleQuery              — a query resolving to one directly-attached
                                concept returns that concept's fact.
TestMultiConceptQuery        — a query resolving to several concepts
                                returns candidates from all of them.
TestActivationSpreadPullsIndirectConcept
                              — a query seeding only one concept still
                                surfaces a KnowledgeObject attached to a
                                neighbouring concept via relationship
                                propagation.
TestUnknownQuery              — a query matching no concept returns "".
TestDeterministicRepeatedQueries
                              — calling query() repeatedly with the same
                                input yields byte-identical output.
TestRankingRespected           — higher-scoring candidates are rendered
                                before lower-scoring ones.
TestAllocationRespected        — RetrievalConfig.max_results caps the
                                number of rendered blocks.
TestContextFormattingPreserved — the rendered block format matches
                                ContextBuilder's own documented format.
TestHybridRetrievalIntegration  — MemoryEngine goes through
                                HybridCandidateRetriever: ontology
                                evidence is no longer required to reach
                                ranking, a keyword-only match is wrapped
                                as a zero-evidence Candidate and still
                                scored/rendered on its non-ontology
                                components, and a fact found by both
                                paths is rendered exactly once (using the
                                richer, ontology-evidenced representation).
TestArchitecturalBoundaries    — engine.py orchestrates
                                HybridCandidateRetriever plus the
                                ranking/allocation/formatting stages
                                without duplicating retrieval-orchestration
                                logic or mutating the graph/vault itself.
TestQueryRewriterDefaultDisabled
                              — omitting query_rewriter (or passing
                                query_rewriter=None explicitly) produces
                                byte-for-byte identical output.
TestQueryRewriterEnabledSurfacesAdditionalFacts
                              — a fact only reachable via a rewrite phrase
                                (not the literal query text) is included
                                when a rewriter is configured, and absent
                                when it is not.
TestQueryRewriterDeduplication — a fact resolved by both the original
                                query and a rewrite is rendered exactly
                                once.
TestQueryRewriterDeterminism   — repeated calls with a rewriter configured
                                yield byte-identical output.
TestQueryRewriterFailOpenDegradation
                              — a rewriter that yields zero rewrites (as
                                QueryRewriter's own fail-open contract
                                guarantees on error) degrades to the same
                                output as the disabled path.
TestMergeCandidates            — direct unit tests of
                                engine._merge_candidates: ontology evidence
                                is preserved across duplicates regardless
                                of discovery order, distinct KnowledgeObjects
                                are never merged, and the result is sorted
                                deterministically.
TestQueryWithTrace              — query() and query_with_trace() return the
                                same context string; the trace correctly
                                reports accepted/rejected candidates,
                                rejection reasons, rank positions,
                                keyword/ontology provenance, rewritten
                                queries, and pipeline stats.
TestProjectStateIntegration     — Phase A: ProjectStateBuilder runs once per
                                query_with_trace() call, strictly after the
                                context string is already rendered; the
                                trace correctly buckets accepted candidates
                                by category, reports gaps/confidence,
                                cannot affect retrieval output, is
                                deterministic, and serialises/deserialises
                                with backward compatibility.
TestSharedRetrievalPrefixIntegration
                              — Steps 1-2 of
                                PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md:
                                query(), query_with_trace(), and
                                query_structured() now share exactly one
                                retrieval prefix; retrieval and Context
                                Planning each run exactly once per
                                query_structured()/query_working_context()
                                call; a CONTINUATION-mode query makes a real
                                ProjectState available to
                                StructuredPromptBuilder.render() and its
                                <ProjectState> element now appears in the
                                rendered prompt; every other task mode leaves
                                it None and the rendered prompt stays
                                byte-identical to rendering with no
                                project_state at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple
from uuid import uuid4

import pytest

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine import engine as engine_module
from obsidian.memory_engine.acceptance_stage import AcceptanceConfig
from obsidian.memory_engine.category_preference import CATEGORY_PREFERENCE_BONUS
from obsidian.memory_engine.context_planner import (
    CategoryRequirement,
    ContextCategory,
    ContextPlan,
    Necessity,
    PlanningMethod,
    TaskMode,
)
from obsidian.memory_engine.engine import MemoryEngine, _merge_candidates
from obsidian.memory_engine.gap_recovery import (
    DEFAULT_RETRY_BUDGET,
    GapRecoveryDecision,
    RecoveryStrategy,
    RetryReason,
)
from obsidian.memory_engine.hybrid_candidate_retriever import (
    HybridCandidateRetriever,
)
from obsidian.memory_engine.memory_store import MemoryStore
from obsidian.memory_engine.project_state import ProjectState
from obsidian.memory_engine.query_rewriter import RewriteResult
from obsidian.memory_engine.structured_prompt_builder import StructuredPromptBuilder
from obsidian.memory_engine.vault_writer import VaultWriter
from obsidian.memory_engine.working_context_builder import WorkingContextBuilder
from obsidian.ontology.alias_index import AliasIndex
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.enums import OntologyRelationshipType
from obsidian.ontology.models import Attachment, Concept, Relationship
from obsidian.ontology.retrieval_config import RetrievalConfig
from obsidian.ontology.retrieval_models import (
    REJECTION_BELOW_MINIMUM_SCORE,
    REJECTION_SLOT_BUDGET_EXCEEDED,
    Candidate,
    ContextKind,
    ContextStatus,
    RankedCandidate,
    WorkingContext,
    WorkingContextState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def concept(label: str) -> Concept:
    return Concept.from_label(label)


# AcceptanceStage runs downstream of ranking with real thresholds by
# default; tests in this file that exercise ranking order, allocation, or
# context formatting (not acceptance itself) use this permissive config so
# AcceptanceStage never rejects a candidate those tests otherwise expect to
# see rendered.
PERMISSIVE_ACCEPTANCE = AcceptanceConfig(
    abstention_score=0.0,
    min_gap=1.0,
    relative_floor_ratio=0.0,
    acceptance_max_k=50,
)


def make_ko(
    fact: str,
    *,
    importance: float = 0.5,
    confidence: float = 0.5,
    confirmation_count: int = 0,
) -> KnowledgeObject:
    return KnowledgeObject(
        id=uuid4(),
        canonical_fact=fact,
        memory_type=MemoryType.FACT,
        importance=importance,
        confidence=confidence,
        confirmation_count=confirmation_count,
    )


def build_engine(
    tmp_path: Path,
    concepts: Sequence[Concept],
    kos: Sequence[KnowledgeObject],
    attachments: Iterable[Attachment] = (),
    relationships: Iterable[Relationship] = (),
    config: Optional[RetrievalConfig] = None,
    acceptance_config: Optional[AcceptanceConfig] = None,
    query_rewriter: Optional[Any] = None,
) -> tuple[MemoryEngine, MemoryStore]:
    graph = ConceptGraph()
    for c in concepts:
        graph.add_concept(c)
    for rel in relationships:
        graph.add_relationship(rel)
    for att in attachments:
        graph.add_attachment(att)

    alias_index = AliasIndex()
    alias_index.build(concepts)

    writer = VaultWriter(tmp_path)
    for ko in kos:
        writer.write(ko)
    store = MemoryStore(tmp_path)
    store.load()

    engine = MemoryEngine(
        alias_index,
        graph,
        store,
        config=config,
        acceptance_config=acceptance_config,
        query_rewriter=query_rewriter,
    )
    return engine, store


class _FrozenDatetime(datetime):
    """A ``datetime`` subclass whose :meth:`utcnow` always returns a fixed instant.

    DeterministicRanker's recency component is a continuous function of
    wall-clock time, so two independently-timed pipeline runs can compute
    infinitesimally different floats even when nothing else differs.
    Monkeypatching ``engine.datetime`` to this class makes every
    ``datetime.utcnow()`` call inside :meth:`MemoryEngine._allocate` return
    the same instant, so repeated/parallel pipeline runs are byte-for-byte
    comparable rather than merely "close".
    """

    @classmethod
    def utcnow(cls) -> "datetime":
        return datetime(2024, 1, 1)


class _FixedRewriter:
    """Test double for QueryRewriter: returns a pre-set RewriteResult.

    Duck-typed to satisfy the only contract MemoryEngine relies on — a
    ``rewrite(query: str) -> RewriteResult`` method — without any real LLM
    call or environment configuration. Records every query it was asked to
    rewrite so tests can assert how MemoryEngine invoked it.
    """

    def __init__(self, rewrites: Sequence[str] = ()) -> None:
        self._rewrites = tuple(rewrites)
        self.queries_seen: List[str] = []

    def rewrite(self, query: str) -> RewriteResult:
        self.queries_seen.append(query)
        return RewriteResult(original=query, rewrites=self._rewrites)


def expected_block(index: int, ko: KnowledgeObject) -> str:
    """Reproduce ContextBuilder's exact per-candidate format for assertions."""
    valid_until = ko.valid_until.isoformat() if ko.valid_until is not None else "none"
    return (
        f"[{index}] {ko.canonical_fact}\n"
        f"    type: {ko.memory_type.value} | "
        f"confidence: {ko.confidence:.2f} | "
        f"importance: {ko.importance:.2f} | "
        f"confirmations: {ko.confirmation_count}\n"
        f"    valid_from: {ko.valid_from.isoformat()} | "
        f"valid_until: {valid_until}"
    )


# ---------------------------------------------------------------------------
# TestSimpleQuery
# ---------------------------------------------------------------------------


class TestSimpleQuery:
    def test_query_returns_directly_attached_fact(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)

        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        result = engine.query("Tell me about Haven")

        assert "Haven is a personal second-brain project" in result
        assert result.startswith("[1]")

    def test_query_with_no_attachments_returns_empty_string(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        engine, _ = build_engine(tmp_path, [haven], [])

        result = engine.query("Haven")

        assert result == ""


# ---------------------------------------------------------------------------
# TestMultiConceptQuery
# ---------------------------------------------------------------------------


class TestMultiConceptQuery:
    def test_query_resolving_two_concepts_returns_both_facts(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)
        rel = Relationship.create(haven.id, claude.id, OntologyRelationshipType.USES)

        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
            relationships=[rel],
        )

        result = engine.query("Haven and Claude")

        assert "Haven is a personal second-brain project" in result
        assert "Claude is an AI assistant by Anthropic" in result


# ---------------------------------------------------------------------------
# TestActivationSpreadPullsIndirectConcept
# ---------------------------------------------------------------------------


class TestActivationSpreadPullsIndirectConcept:
    def test_seeding_one_concept_surfaces_neighbours_fact(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        # Only Claude has a direct attachment; Haven has none.
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_claude = Attachment.create(ko_claude.id, claude.id)
        rel = Relationship.create(haven.id, claude.id, OntologyRelationshipType.USES)

        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_claude],
            attachments=[att_claude],
            relationships=[rel],
        )

        # Query resolves only the "Haven" seed; Claude's fact must arrive
        # via activation spreading across the USES relationship.
        result = engine.query("Haven")

        assert "Claude is an AI assistant by Anthropic" in result


# ---------------------------------------------------------------------------
# TestUnknownQuery
# ---------------------------------------------------------------------------


class TestUnknownQuery:
    def test_query_matching_no_concept_returns_empty_string(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)

        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        result = engine.query("completely unrelated banana smoothie recipe")

        assert result == ""

    def test_empty_graph_and_store_returns_empty_string(self, tmp_path: Path) -> None:
        engine, _ = build_engine(tmp_path, [], [])

        result = engine.query("anything at all")

        assert result == ""


# ---------------------------------------------------------------------------
# TestDeterministicRepeatedQueries
# ---------------------------------------------------------------------------


class TestDeterministicRepeatedQueries:
    def test_repeated_calls_produce_identical_output(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project", importance=0.8)
        ko_claude = make_ko("Claude is an AI assistant by Anthropic", importance=0.3)
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)
        rel = Relationship.create(haven.id, claude.id, OntologyRelationshipType.USES)

        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
            relationships=[rel],
        )

        first = engine.query("Haven and Claude")
        for _ in range(5):
            assert engine.query("Haven and Claude") == first

    def test_repeated_calls_on_fresh_engine_instances_match(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)

        engine_a, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])
        result_a = engine_a.query("Haven")

        engine_b, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])
        result_b = engine_b.query("Haven")

        assert result_a == result_b


# ---------------------------------------------------------------------------
# TestRankingRespected
# ---------------------------------------------------------------------------


class TestRankingRespected:
    def test_higher_scoring_candidate_rendered_first(self, tmp_path: Path) -> None:
        alpha = concept("Alpha")
        beta = concept("Beta")
        ko_alpha = make_ko(
            "Alpha fact", importance=0.9, confidence=0.9, confirmation_count=5
        )
        ko_beta = make_ko(
            "Beta fact", importance=0.1, confidence=0.1, confirmation_count=0
        )
        att_alpha = Attachment.create(ko_alpha.id, alpha.id)
        att_beta = Attachment.create(ko_beta.id, beta.id)

        engine, _ = build_engine(
            tmp_path,
            [alpha, beta],
            [ko_alpha, ko_beta],
            attachments=[att_alpha, att_beta],
            acceptance_config=PERMISSIVE_ACCEPTANCE,
        )

        result = engine.query("Alpha and Beta")

        assert result.index("Alpha fact") < result.index("Beta fact")
        assert result.startswith("[1] Alpha fact")

    def test_swapping_scores_swaps_rendered_order(self, tmp_path: Path) -> None:
        alpha = concept("Alpha")
        beta = concept("Beta")
        # Beta now scores higher than Alpha (inverse of the case above).
        ko_alpha = make_ko(
            "Alpha fact", importance=0.1, confidence=0.1, confirmation_count=0
        )
        ko_beta = make_ko(
            "Beta fact", importance=0.9, confidence=0.9, confirmation_count=5
        )
        att_alpha = Attachment.create(ko_alpha.id, alpha.id)
        att_beta = Attachment.create(ko_beta.id, beta.id)

        engine, _ = build_engine(
            tmp_path,
            [alpha, beta],
            [ko_alpha, ko_beta],
            attachments=[att_alpha, att_beta],
            acceptance_config=PERMISSIVE_ACCEPTANCE,
        )

        result = engine.query("Alpha and Beta")

        assert result.index("Beta fact") < result.index("Alpha fact")
        assert result.startswith("[1] Beta fact")


# ---------------------------------------------------------------------------
# TestAllocationRespected
# ---------------------------------------------------------------------------


class TestAllocationRespected:
    def test_max_results_caps_rendered_blocks(self, tmp_path: Path) -> None:
        alpha = concept("Alpha")
        beta = concept("Beta")
        ko_alpha = make_ko("Alpha fact", importance=0.9, confidence=0.9)
        ko_beta = make_ko("Beta fact", importance=0.2, confidence=0.2)
        att_alpha = Attachment.create(ko_alpha.id, alpha.id)
        att_beta = Attachment.create(ko_beta.id, beta.id)

        config = RetrievalConfig(max_results=1)
        engine, _ = build_engine(
            tmp_path,
            [alpha, beta],
            [ko_alpha, ko_beta],
            attachments=[att_alpha, att_beta],
            config=config,
        )

        result = engine.query("Alpha and Beta")

        assert "Alpha fact" in result
        assert "Beta fact" not in result
        assert "[2]" not in result

    def test_max_results_larger_than_candidates_returns_all(self, tmp_path: Path) -> None:
        alpha = concept("Alpha")
        beta = concept("Beta")
        ko_alpha = make_ko("Alpha fact")
        ko_beta = make_ko("Beta fact")
        att_alpha = Attachment.create(ko_alpha.id, alpha.id)
        att_beta = Attachment.create(ko_beta.id, beta.id)

        config = RetrievalConfig(max_results=50)
        engine, _ = build_engine(
            tmp_path,
            [alpha, beta],
            [ko_alpha, ko_beta],
            attachments=[att_alpha, att_beta],
            config=config,
        )

        result = engine.query("Alpha and Beta")

        assert "Alpha fact" in result
        assert "Beta fact" in result


# ---------------------------------------------------------------------------
# TestContextFormattingPreserved
# ---------------------------------------------------------------------------


class TestContextFormattingPreserved:
    def test_single_candidate_matches_context_builder_format(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko(
            "Haven is a personal second-brain project",
            importance=0.55,
            confidence=0.77,
            confirmation_count=3,
        )
        att = Attachment.create(ko.id, haven.id)

        engine, store = build_engine(tmp_path, [haven], [ko], attachments=[att])

        result = engine.query("Haven")

        hydrated = store.get(ko.id)
        assert result == expected_block(1, hydrated)

    def test_multiple_candidates_joined_by_blank_line(self, tmp_path: Path) -> None:
        alpha = concept("Alpha")
        beta = concept("Beta")
        ko_alpha = make_ko("Alpha fact", importance=0.9, confidence=0.9)
        ko_beta = make_ko("Beta fact", importance=0.2, confidence=0.2)
        att_alpha = Attachment.create(ko_alpha.id, alpha.id)
        att_beta = Attachment.create(ko_beta.id, beta.id)

        engine, store = build_engine(
            tmp_path,
            [alpha, beta],
            [ko_alpha, ko_beta],
            attachments=[att_alpha, att_beta],
            acceptance_config=PERMISSIVE_ACCEPTANCE,
        )

        result = engine.query("Alpha and Beta")

        expected = "\n\n".join(
            [
                expected_block(1, store.get(ko_alpha.id)),
                expected_block(2, store.get(ko_beta.id)),
            ]
        )
        assert result == expected


# ---------------------------------------------------------------------------
# TestHybridRetrievalIntegration
# ---------------------------------------------------------------------------


class TestHybridRetrievalIntegration:
    def test_keyword_only_match_with_no_ontology_evidence_is_rendered(
        self, tmp_path: Path
    ) -> None:
        # No concepts/attachments at all -- HybridCandidateRetriever's
        # keyword path finds this KO and wraps it as a zero-evidence
        # Candidate (supporting_concepts=(), activation_score=0.0,
        # attachment_relevance=0.0). DeterministicRanker still scores it
        # on its non-ontology components (importance, confidence,
        # recency, confirmation count), so it clears the default
        # minimum_candidate_score and is rendered.
        ko = make_ko("Claude is an AI assistant by Anthropic")

        engine, _ = build_engine(tmp_path, [], [ko])

        result = engine.query("claude")

        assert "Claude is an AI assistant by Anthropic" in result

    def test_keyword_only_match_below_minimum_score_is_absent(
        self, tmp_path: Path
    ) -> None:
        # Same zero-evidence Candidate as above, but a minimum_candidate_score
        # high enough that importance/confidence/recency alone can't clear
        # it -- the ranking cutoff still applies uniformly, keyword-only or not.
        ko = make_ko("Claude is an AI assistant by Anthropic")

        engine, _ = build_engine(
            tmp_path, [], [ko], config=RetrievalConfig(minimum_candidate_score=0.99)
        )

        result = engine.query("claude")

        assert result == ""

    def test_fact_found_by_both_paths_rendered_exactly_once(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        # Shares the "haven" keyword with the query AND is directly
        # attached, so both HybridCandidateRetriever paths find it.
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)

        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        result = engine.query("Haven")

        assert result.count("Haven is a personal second-brain project") == 1

    def test_ontology_evidenced_and_keyword_only_facts_together(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        ko_attached = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko_attached.id, haven.id)
        # No concept resolves this KO and it shares no keyword with the
        # query either, so it must not appear.
        ko_unrelated = make_ko("Bananas are a good source of potassium")

        engine, _ = build_engine(
            tmp_path, [haven], [ko_attached, ko_unrelated], attachments=[att]
        )

        result = engine.query("Haven")

        assert "Haven is a personal second-brain project" in result
        assert "Bananas are a good source of potassium" not in result


# ---------------------------------------------------------------------------
# TestArchitecturalBoundaries
# ---------------------------------------------------------------------------


class TestArchitecturalBoundaries:
    def test_module_orchestrates_hybrid_retrieval_and_downstream_stages(self) -> None:
        source = Path(engine_module.__file__).read_text(encoding="utf-8")
        for required in (
            "HybridCandidateRetriever",
            "DeterministicRanker",
            "DeterministicSlotAllocator",
            "ContextBuilder",
        ):
            assert required in source

    def test_module_does_not_reimplement_retrieval_orchestration(self) -> None:
        source = Path(engine_module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "from obsidian.ontology.query_resolver",
            "from obsidian.ontology.activation_spreader",
            "from obsidian.ontology.candidate_assembler",
        ):
            assert forbidden not in source

    def test_module_never_mutates_the_graph(self) -> None:
        source = Path(engine_module.__file__).read_text(encoding="utf-8")
        for forbidden in (".add_concept(", ".add_relationship(", ".add_attachment("):
            assert forbidden not in source

    def test_module_never_mutates_the_vault(self) -> None:
        source = Path(engine_module.__file__).read_text(encoding="utf-8")
        for forbidden in ("VaultWriter", ".load(", "MemoryParser"):
            assert forbidden not in source

    def test_public_api_is_query_query_with_trace_and_working_context_variants(
        self,
    ) -> None:
        # query_with_trace is the sanctioned Retrieval Inspector entry
        # point: query() is defined as its first return value, so the two
        # can never disagree about the returned context string.
        # query_working_context/query_structured are additive alternate
        # renderers (WorkingContextBuilder/StructuredPromptBuilder) layered
        # on the same retrieval/ranking/acceptance/allocation pipeline; see
        # TestWorkingContextQueries below. No other public surface is added.
        public_methods = [
            name
            for name in vars(MemoryEngine)
            if not name.startswith("_") and callable(getattr(MemoryEngine, name))
        ]
        assert sorted(public_methods) == [
            "query",
            "query_structured",
            "query_with_trace",
            "query_working_context",
        ]


# ---------------------------------------------------------------------------
# TestQueryRewriterDefaultDisabled
# ---------------------------------------------------------------------------


class TestQueryRewriterDefaultDisabled:
    def test_omitting_query_rewriter_matches_explicit_none(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)

        engine_default, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
        )
        engine_explicit_none, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
            query_rewriter=None,
        )

        assert engine_default.query("Haven and Claude") == engine_explicit_none.query(
            "Haven and Claude"
        )

    def test_disabled_path_never_touches_a_rewriter_it_was_not_given(
        self, tmp_path: Path
    ) -> None:
        # A rewriter that was never wired into the engine must never be
        # called just because one happens to exist in the test.
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        unused_rewriter = _FixedRewriter(rewrites=("second brain",))

        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])
        engine.query("Haven")

        assert unused_rewriter.queries_seen == []


# ---------------------------------------------------------------------------
# TestQueryRewriterEnabledSurfacesAdditionalFacts
# ---------------------------------------------------------------------------


class TestQueryRewriterEnabledSurfacesAdditionalFacts:
    def test_rewrite_only_reachable_fact_is_included_when_enabled(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)

        # The literal query text only resolves "Haven"; "Claude" is only
        # reachable through the rewrite phrase.
        rewriter = _FixedRewriter(rewrites=("Tell me about Claude",))
        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
            query_rewriter=rewriter,
        )

        result = engine.query("Haven")

        assert "Haven is a personal second-brain project" in result
        assert "Claude is an AI assistant by Anthropic" in result

    def test_same_scenario_without_rewriter_misses_the_rewrite_only_fact(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)

        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
        )

        result = engine.query("Haven")

        assert "Haven is a personal second-brain project" in result
        assert "Claude is an AI assistant by Anthropic" not in result

    def test_rewriter_is_invoked_with_the_raw_query(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        rewriter = _FixedRewriter(rewrites=("second brain",))

        engine, _ = build_engine(
            tmp_path, [haven], [ko], attachments=[att], query_rewriter=rewriter
        )
        engine.query("Haven")

        assert rewriter.queries_seen == ["Haven"]

    def test_retrieval_runs_for_original_and_every_rewrite(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)
        rewriter = _FixedRewriter(rewrites=("Claude", "second brain"))

        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
            query_rewriter=rewriter,
        )

        result = engine.query("Haven")

        # Both facts only surface if retrieval actually ran for both the
        # original query and the "Claude" rewrite.
        assert "Haven is a personal second-brain project" in result
        assert "Claude is an AI assistant by Anthropic" in result


# ---------------------------------------------------------------------------
# TestQueryRewriterDeduplication
# ---------------------------------------------------------------------------


class TestQueryRewriterDeduplication:
    def test_fact_found_by_original_and_rewrite_rendered_once(
        self, tmp_path: Path
    ) -> None:
        # The rewrite is a different surface form (alias) of the *same*
        # concept, so both queries resolve to the same KnowledgeObject.
        haven = Concept.from_label("Haven", aliases=("Second Brain",))
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        rewriter = _FixedRewriter(rewrites=("Second Brain",))

        engine, _ = build_engine(
            tmp_path, [haven], [ko], attachments=[att], query_rewriter=rewriter
        )

        result = engine.query("Haven")

        assert result.count("Haven is a personal second-brain project") == 1

    def test_unrelated_facts_alongside_a_duplicate_all_render(
        self, tmp_path: Path
    ) -> None:
        haven = Concept.from_label("Haven", aliases=("Second Brain",))
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)
        # "Second Brain" duplicates ko_haven; "Claude" is a genuinely new fact.
        rewriter = _FixedRewriter(rewrites=("Second Brain", "Claude"))

        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
            query_rewriter=rewriter,
        )

        result = engine.query("Haven")

        assert result.count("Haven is a personal second-brain project") == 1
        assert result.count("Claude is an AI assistant by Anthropic") == 1


# ---------------------------------------------------------------------------
# TestQueryRewriterDeterminism
# ---------------------------------------------------------------------------


class TestQueryRewriterDeterminism:
    def test_repeated_calls_produce_identical_output(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project", importance=0.8)
        ko_claude = make_ko("Claude is an AI assistant by Anthropic", importance=0.3)
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)
        rewriter = _FixedRewriter(rewrites=("Claude",))

        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
            query_rewriter=rewriter,
        )

        first = engine.query("Haven")
        for _ in range(5):
            assert engine.query("Haven") == first

    def test_fresh_engine_instances_with_equivalent_rewriters_match(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)

        engine_a, _ = build_engine(
            tmp_path,
            [haven],
            [ko],
            attachments=[att],
            query_rewriter=_FixedRewriter(rewrites=("second brain",)),
        )
        engine_b, _ = build_engine(
            tmp_path,
            [haven],
            [ko],
            attachments=[att],
            query_rewriter=_FixedRewriter(rewrites=("second brain",)),
        )

        assert engine_a.query("Haven") == engine_b.query("Haven")


# ---------------------------------------------------------------------------
# TestQueryRewriterFailOpenDegradation
# ---------------------------------------------------------------------------


class TestQueryRewriterFailOpenDegradation:
    def test_rewriter_returning_no_rewrites_matches_disabled_behavior(
        self, tmp_path: Path
    ) -> None:
        # QueryRewriter's own fail-open contract guarantees rewrites=() on
        # any internal error; this simulates that outcome without needing
        # to fake an actual API failure.
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)

        engine_disabled, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])
        engine_enabled_no_rewrites, _ = build_engine(
            tmp_path,
            [haven],
            [ko],
            attachments=[att],
            query_rewriter=_FixedRewriter(rewrites=()),
        )

        assert engine_disabled.query("Haven") == engine_enabled_no_rewrites.query(
            "Haven"
        )


# ---------------------------------------------------------------------------
# TestMergeCandidates
# ---------------------------------------------------------------------------


class TestMergeCandidates:
    """Direct unit tests of :func:`obsidian.memory_engine.engine._merge_candidates`.

    Ontology-evidenced and zero-evidence ``Candidate`` instances are built
    via the real, unmodified :class:`HybridCandidateRetriever` (the same
    pattern :class:`TestHybridRetrievalIntegration` above uses) rather than
    hand-constructed, so these tests never guess at ``Candidate``'s
    internal field semantics -- only its already-documented
    ``has_ontology_evidence`` contract is relied on.
    """

    def _evidenced_and_zero_evidence_pair(
        self, tmp_path: Path
    ) -> Tuple[Candidate, Candidate]:
        ko = make_ko("Haven is a personal second-brain project")
        haven = concept("Haven")
        att = Attachment.create(ko.id, haven.id)

        writer = VaultWriter(tmp_path)
        writer.write(ko)
        store = MemoryStore(tmp_path)
        store.load()

        graph_with_concept = ConceptGraph()
        graph_with_concept.add_concept(haven)
        graph_with_concept.add_attachment(att)
        alias_index_with_concept = AliasIndex()
        alias_index_with_concept.build([haven])
        retriever_evidenced = HybridCandidateRetriever(
            alias_index_with_concept, graph_with_concept, store
        )
        evidenced = retriever_evidenced.retrieve("Haven")[0]
        assert evidenced.has_ontology_evidence

        graph_without_concept = ConceptGraph()
        alias_index_without_concept = AliasIndex()
        alias_index_without_concept.build([])
        retriever_zero = HybridCandidateRetriever(
            alias_index_without_concept, graph_without_concept, store
        )
        zero = retriever_zero.retrieve("haven")[0]
        assert not zero.has_ontology_evidence

        assert evidenced.knowledge_object.id == zero.knowledge_object.id
        return evidenced, zero

    def test_ontology_evidence_wins_when_found_second(self, tmp_path: Path) -> None:
        evidenced, zero = self._evidenced_and_zero_evidence_pair(tmp_path)

        merged = _merge_candidates([[zero], [evidenced]])

        assert len(merged) == 1
        assert merged[0] is evidenced

    def test_ontology_evidence_wins_when_found_first(self, tmp_path: Path) -> None:
        evidenced, zero = self._evidenced_and_zero_evidence_pair(tmp_path)

        merged = _merge_candidates([[evidenced], [zero]])

        assert len(merged) == 1
        assert merged[0] is evidenced

    def test_two_zero_evidence_duplicates_keep_the_first(
        self, tmp_path: Path
    ) -> None:
        _, zero = self._evidenced_and_zero_evidence_pair(tmp_path)

        merged = _merge_candidates([[zero], [zero]])

        assert len(merged) == 1
        assert merged[0] is zero

    def test_distinct_knowledge_objects_are_not_merged(self, tmp_path: Path) -> None:
        alpha = concept("Alpha")
        beta = concept("Beta")
        ko_alpha = make_ko("Alpha fact")
        ko_beta = make_ko("Beta fact")
        att_alpha = Attachment.create(ko_alpha.id, alpha.id)
        att_beta = Attachment.create(ko_beta.id, beta.id)
        engine, _ = build_engine(
            tmp_path,
            [alpha, beta],
            [ko_alpha, ko_beta],
            attachments=[att_alpha, att_beta],
        )
        candidates = engine._candidate_retriever.retrieve("Alpha and Beta")
        assert len(candidates) == 2

        merged = _merge_candidates([candidates, candidates])

        assert len(merged) == 2

    def test_merged_result_sorted_by_knowledge_object_id(self, tmp_path: Path) -> None:
        alpha = concept("Alpha")
        beta = concept("Beta")
        ko_alpha = make_ko("Alpha fact")
        ko_beta = make_ko("Beta fact")
        att_alpha = Attachment.create(ko_alpha.id, alpha.id)
        att_beta = Attachment.create(ko_beta.id, beta.id)
        engine, _ = build_engine(
            tmp_path,
            [alpha, beta],
            [ko_alpha, ko_beta],
            attachments=[att_alpha, att_beta],
        )
        candidates = engine._candidate_retriever.retrieve("Alpha and Beta")

        # Feed them across two "query" lists in reverse to prove the merge
        # re-sorts rather than preserving input order.
        merged = _merge_candidates([[candidates[-1]], [candidates[0]]])

        assert [str(c.knowledge_object.id) for c in merged] == sorted(
            str(c.knowledge_object.id) for c in candidates
        )

    def test_empty_input_returns_empty_list(self) -> None:
        assert _merge_candidates([]) == []
        assert _merge_candidates([[], []]) == []


# ---------------------------------------------------------------------------
# TestQueryWithTrace
# ---------------------------------------------------------------------------


class TestQueryWithTrace:
    def test_context_matches_plain_query(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project", importance=0.8)
        ko_claude = make_ko("Claude is an AI assistant by Anthropic", importance=0.3)
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)

        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
        )

        expected = engine.query("Haven and Claude")
        context, trace = engine.query_with_trace("Haven and Claude")

        assert context == expected
        assert trace.query == "Haven and Claude"

    def test_accepted_candidate_has_no_rejection_reason(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        _, trace = engine.query_with_trace("Haven")

        assert len(trace.candidates) == 1
        ct = trace.candidates[0]
        assert ct.knowledge_object_id == ko.id
        assert ct.canonical_fact == ko.canonical_fact
        assert ct.accepted is True
        assert ct.rejection_reason is None
        assert ct.final_rank == 1
        assert ct.matched_by_ontology is True
        assert ct.matched_by_keyword is True  # "Haven" also token-matches the fact
        assert ct.keyword_overlap_score > 0.0

    def test_score_breakdown_carries_ranker_contributions(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        _, trace = engine.query_with_trace("Haven")

        ct = trace.candidates[0]
        assert set(ct.score_breakdown.keys()) == {
            "activation",
            "attachment_relevance",
            "keyword_overlap",
            "importance",
            "confidence",
            "recency",
            "confirmation_count",
        }
        assert ct.score_breakdown["recency"] >= 0.0
        assert sum(ct.score_breakdown.values()) == pytest.approx(ct.final_score)

    def test_keyword_overlap_score_is_zero_for_ontology_only_match(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        # "Haven" does not appear in the fact text, so only the ontology
        # path finds it.
        ko = make_ko("Personal second-brain project used daily")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        _, trace = engine.query_with_trace("Haven")

        assert len(trace.candidates) == 1
        ct = trace.candidates[0]
        assert ct.matched_by_ontology is True
        assert ct.matched_by_keyword is False
        assert ct.keyword_overlap_score == 0.0

    def test_below_minimum_score_candidate_is_rejected_with_reason(
        self, tmp_path: Path
    ) -> None:
        ko = make_ko("Claude is an AI assistant by Anthropic")
        engine, _ = build_engine(
            tmp_path, [], [ko], config=RetrievalConfig(minimum_candidate_score=0.99)
        )

        context, trace = engine.query_with_trace("claude")

        assert context == ""
        assert len(trace.candidates) == 1
        ct = trace.candidates[0]
        assert ct.accepted is False
        assert ct.rejection_reason == REJECTION_BELOW_MINIMUM_SCORE
        assert ct.matched_by_keyword is True
        assert ct.matched_by_ontology is False

    def test_slot_budget_exceeded_candidate_is_rejected_with_reason(
        self, tmp_path: Path
    ) -> None:
        alpha = concept("Alpha")
        beta = concept("Beta")
        ko_alpha = make_ko("Alpha fact", importance=0.9, confidence=0.9)
        ko_beta = make_ko("Beta fact", importance=0.2, confidence=0.2)
        att_alpha = Attachment.create(ko_alpha.id, alpha.id)
        att_beta = Attachment.create(ko_beta.id, beta.id)
        engine, _ = build_engine(
            tmp_path,
            [alpha, beta],
            [ko_alpha, ko_beta],
            attachments=[att_alpha, att_beta],
            config=RetrievalConfig(max_results=1),
            acceptance_config=PERMISSIVE_ACCEPTANCE,
        )

        _, trace = engine.query_with_trace("Alpha and Beta")

        by_id = {ct.knowledge_object_id: ct for ct in trace.candidates}
        assert by_id[ko_alpha.id].accepted is True
        assert by_id[ko_alpha.id].final_rank == 1
        assert by_id[ko_beta.id].accepted is False
        assert by_id[ko_beta.id].rejection_reason == REJECTION_SLOT_BUDGET_EXCEEDED
        assert by_id[ko_beta.id].final_rank == 2
        assert by_id[ko_beta.id].threshold_used == 1.0

    def test_rewritten_queries_recorded(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        rewriter = _FixedRewriter(rewrites=("second brain",))
        engine, _ = build_engine(
            tmp_path, [haven], [ko], attachments=[att], query_rewriter=rewriter
        )

        _, trace = engine.query_with_trace("Haven")

        assert trace.rewritten_queries == ("second brain",)

    def test_no_rewriter_records_no_rewritten_queries(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        _, trace = engine.query_with_trace("Haven")

        assert trace.rewritten_queries == ()

    def test_rewriting_enabled_true_when_rewriter_configured(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        rewriter = _FixedRewriter(rewrites=("second brain",))
        engine, _ = build_engine(
            tmp_path, [haven], [ko], attachments=[att], query_rewriter=rewriter
        )

        _, trace = engine.query_with_trace("Haven")

        assert trace.rewriting_enabled is True

    def test_rewriting_enabled_false_by_default(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        _, trace = engine.query_with_trace("Haven")

        assert trace.rewriting_enabled is False

    def test_rewriting_enabled_true_even_when_rewriter_fails_open(
        self, tmp_path: Path
    ) -> None:
        # A configured-but-empty-result rewriter (QueryRewriter's own
        # fail-open contract -- see that module's docstring) is still
        # "enabled": rewriting_enabled distinguishes "no rewriter
        # configured" from "configured, found/returned nothing to add",
        # which rewritten_queries alone cannot (both give ()).
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        rewriter = _FixedRewriter(rewrites=())
        engine, _ = build_engine(
            tmp_path, [haven], [ko], attachments=[att], query_rewriter=rewriter
        )

        _, trace = engine.query_with_trace("Haven")

        assert trace.rewriting_enabled is True
        assert trace.rewritten_queries == ()

    def test_pipeline_stats_reflect_the_run(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        context, trace = engine.query_with_trace("Haven")

        stats = trace.pipeline_stats
        assert stats.total_ontology_candidates == 1
        assert stats.total_keyword_candidates == 1
        assert stats.total_merged_candidates == 1
        assert stats.total_accepted_candidates == 1
        assert stats.total_rejected_candidates == 0
        assert stats.final_context_size == len(context)
        assert stats.retrieval_latency_ms >= 0.0

    def test_unknown_query_yields_empty_trace(self, tmp_path: Path) -> None:
        engine, _ = build_engine(tmp_path, [], [])

        context, trace = engine.query_with_trace("anything at all")

        assert context == ""
        assert trace.candidates == ()
        assert trace.pipeline_stats.total_merged_candidates == 0
        assert trace.pipeline_stats.final_context_size == 0

    def test_empty_query_does_not_raise(self, tmp_path: Path) -> None:
        engine, _ = build_engine(tmp_path, [], [])

        context, trace = engine.query_with_trace("")

        assert context == ""
        assert trace.query == ""

    def test_final_rank_is_dense_descending_by_score(self, tmp_path: Path) -> None:
        alpha = concept("Alpha")
        beta = concept("Beta")
        ko_alpha = make_ko("Alpha fact", importance=0.9, confidence=0.9)
        ko_beta = make_ko("Beta fact", importance=0.1, confidence=0.1)
        att_alpha = Attachment.create(ko_alpha.id, alpha.id)
        att_beta = Attachment.create(ko_beta.id, beta.id)
        engine, _ = build_engine(
            tmp_path,
            [alpha, beta],
            [ko_alpha, ko_beta],
            attachments=[att_alpha, att_beta],
        )

        _, trace = engine.query_with_trace("Alpha and Beta")

        by_id = {ct.knowledge_object_id: ct for ct in trace.candidates}
        assert by_id[ko_alpha.id].final_rank == 1
        assert by_id[ko_beta.id].final_rank == 2
        assert by_id[ko_alpha.id].final_score >= by_id[ko_beta.id].final_score


# ---------------------------------------------------------------------------
# TestWorkingContextQueries
# ---------------------------------------------------------------------------


class TestWorkingContextQueries:
    """Tests for the additive :meth:`MemoryEngine.query_working_context`.

    These exercise the new method against the same fixtures used above for
    :meth:`query`/:meth:`query_with_trace`, and separately confirm those two
    pre-existing methods are byte-for-byte unaffected by this addition.
    """

    def test_matches_manually_composed_pipeline(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)
        rel = Relationship.create(haven.id, claude.id, OntologyRelationshipType.USES)

        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
            relationships=[rel],
        )

        monkeypatch.setattr(engine_module, "datetime", _FrozenDatetime)

        contexts = engine.query_working_context("Haven and Claude")

        allocated = engine._allocate("Haven and Claude")
        expected = engine._resolve_topic_titles(WorkingContextBuilder().build(allocated))
        assert contexts == expected

    def test_directly_attached_fact_is_reachable_via_buckets(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        contexts = engine.query_working_context("Haven")

        facts = {
            ranked.candidate.knowledge_object.canonical_fact
            for context in contexts
            for bucket in context.buckets
            for ranked in bucket.members
        }
        assert "Haven is a personal second-brain project" in facts

    def test_no_match_returns_single_general_context(self, tmp_path: Path) -> None:
        engine, _ = build_engine(tmp_path, [], [])

        contexts = engine.query_working_context("anything at all")

        assert len(contexts) == 1
        assert contexts[0].kind == ContextKind.GENERAL
        assert all(bucket.members == () for bucket in contexts[0].buckets)

    def test_deterministic_repeated_calls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        monkeypatch.setattr(engine_module, "datetime", _FrozenDatetime)

        first = engine.query_working_context("Haven")
        for _ in range(5):
            assert engine.query_working_context("Haven") == first

    def test_does_not_affect_query_or_query_with_trace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)

        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
        )

        monkeypatch.setattr(engine_module, "datetime", _FrozenDatetime)

        before_query = engine.query("Haven and Claude")
        before_context, before_trace = engine.query_with_trace("Haven and Claude")

        engine.query_working_context("Haven and Claude")

        assert engine.query("Haven and Claude") == before_query
        after_context, after_trace = engine.query_with_trace("Haven and Claude")
        assert after_context == before_context
        assert after_trace.candidates == before_trace.candidates

    def test_respects_slot_allocation_budget(self, tmp_path: Path) -> None:
        alpha = concept("Alpha")
        beta = concept("Beta")
        ko_alpha = make_ko("Alpha fact", importance=0.9, confidence=0.9)
        ko_beta = make_ko("Beta fact", importance=0.2, confidence=0.2)
        att_alpha = Attachment.create(ko_alpha.id, alpha.id)
        att_beta = Attachment.create(ko_beta.id, beta.id)
        engine, _ = build_engine(
            tmp_path,
            [alpha, beta],
            [ko_alpha, ko_beta],
            attachments=[att_alpha, att_beta],
            config=RetrievalConfig(max_results=1),
        )

        contexts = engine.query_working_context("Alpha and Beta")

        facts = {
            ranked.candidate.knowledge_object.canonical_fact
            for context in contexts
            for bucket in context.buckets
            for ranked in bucket.members
        }
        assert facts == {"Alpha fact"}

    def test_uses_query_rewriter_when_configured(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)
        rewriter = _FixedRewriter(rewrites=("Tell me about Claude",))

        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
            query_rewriter=rewriter,
        )

        contexts = engine.query_working_context("Haven")

        facts = {
            ranked.candidate.knowledge_object.canonical_fact
            for context in contexts
            for bucket in context.buckets
            for ranked in bucket.members
        }
        assert "Claude is an AI assistant by Anthropic" in facts


# ---------------------------------------------------------------------------
# TestStructuredQueries
# ---------------------------------------------------------------------------


class TestValidityAwareRetrieval:
    """An archived/superseded KnowledgeObject (valid_until <= now) is excluded.

    These use the keyword-only retrieval path (no concepts/attachments), so
    the only thing separating "returned" from "excluded" is the validity gate
    added to the engine -- not ontology evidence.
    """

    def _ko_with_validity(
        self, fact: str, valid_until: Optional[datetime]
    ) -> KnowledgeObject:
        return KnowledgeObject(
            id=uuid4(),
            canonical_fact=fact,
            memory_type=MemoryType.FACT,
            valid_until=valid_until,
        )

    def test_expired_memory_is_excluded(self, tmp_path: Path) -> None:
        ko = self._ko_with_validity(
            "Claude is an AI assistant by Anthropic", datetime(2000, 1, 1)
        )
        engine, _ = build_engine(tmp_path, [], [ko])

        assert engine.query("claude") == ""

    def test_active_memory_with_no_valid_until_is_returned(self, tmp_path: Path) -> None:
        ko = self._ko_with_validity("Claude is an AI assistant by Anthropic", None)
        engine, _ = build_engine(tmp_path, [], [ko])

        assert "Claude is an AI assistant by Anthropic" in engine.query("claude")

    def test_future_valid_until_is_still_active(self, tmp_path: Path) -> None:
        # valid_until in the future means the memory has not expired yet.
        ko = self._ko_with_validity(
            "Claude is an AI assistant by Anthropic", datetime(2999, 1, 1)
        )
        engine, _ = build_engine(tmp_path, [], [ko])

        assert "Claude is an AI assistant by Anthropic" in engine.query("claude")

    def test_expired_memory_absent_from_trace(self, tmp_path: Path) -> None:
        # Excluded before ranking → it is not a candidate at all, so it never
        # appears in the RetrievalTrace.
        ko = self._ko_with_validity(
            "Claude is an AI assistant by Anthropic", datetime(2000, 1, 1)
        )
        engine, _ = build_engine(tmp_path, [], [ko])

        _context, trace = engine.query_with_trace("claude")

        assert trace.candidates == ()
        assert trace.pipeline_stats.total_merged_candidates == 0

    def test_expired_excluded_while_active_sibling_returned(
        self, tmp_path: Path
    ) -> None:
        expired = self._ko_with_validity("Claude runs on old hardware", datetime(2000, 1, 1))
        active = self._ko_with_validity("Claude is fast", None)
        engine, _ = build_engine(tmp_path, [], [expired, active])

        result = engine.query("claude")

        assert "Claude is fast" in result
        assert "Claude runs on old hardware" not in result


class TestStructuredQueries:
    """Tests for the additive :meth:`MemoryEngine.query_structured`."""

    def test_matches_manually_composed_pipeline(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        result = engine.query_structured("Haven")

        contexts = engine.query_working_context("Haven")
        expected = StructuredPromptBuilder().render(contexts, "Haven")
        assert result == expected

    def test_result_is_xml_delimited_and_carries_the_raw_query(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        result = engine.query_structured("Tell me about Haven")

        assert result.startswith("<System>")
        assert result.endswith("</System>")
        assert "<HavenContext" in result
        assert "<UserRequest>" in result
        assert "Tell me about Haven" in result
        assert "Haven is a personal second-brain project" in result

    def test_empty_query_still_renders_a_valid_prompt(self, tmp_path: Path) -> None:
        engine, _ = build_engine(tmp_path, [], [])

        result = engine.query_structured("anything at all")

        assert result.startswith("<System>")
        assert result.endswith("</System>")
        assert "anything at all" in result

    def test_deterministic_repeated_calls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        monkeypatch.setattr(engine_module, "datetime", _FrozenDatetime)

        first = engine.query_structured("Haven")
        for _ in range(5):
            assert engine.query_structured("Haven") == first

    def test_does_not_affect_query_query_with_trace_or_working_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        monkeypatch.setattr(engine_module, "datetime", _FrozenDatetime)

        before_query = engine.query("Haven")
        before_context, before_trace = engine.query_with_trace("Haven")
        before_working_context = engine.query_working_context("Haven")

        engine.query_structured("Haven")

        assert engine.query("Haven") == before_query
        after_context, after_trace = engine.query_with_trace("Haven")
        assert after_context == before_context
        assert after_trace.candidates == before_trace.candidates
        assert engine.query_working_context("Haven") == before_working_context


# ---------------------------------------------------------------------------
# TestTopicTitleResolution
# ---------------------------------------------------------------------------


class TestTopicTitleResolution:
    """A ``TOPIC`` context's title resolves to its concept's label.

    ``WorkingContextBuilder`` titles a ``TOPIC`` context with
    ``str(anchor_concept_id)`` (it has no ``ConceptGraph`` access by design).
    ``MemoryEngine._resolve_topic_titles`` resolves that UUID to
    ``Concept.label`` wherever the graph knows it, applied consistently by
    both :meth:`~MemoryEngine.query_working_context` and
    :meth:`~MemoryEngine.query_structured` (see
    ``docs/architecture/PROMPT_CONTINUATION_EVALUATION.md`` §0/§10).
    """

    def test_topic_title_resolves_to_concept_label(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        contexts = engine.query_working_context("Haven")

        topic = next(c for c in contexts if c.kind is ContextKind.TOPIC)
        assert topic.title == "Haven"

    def test_resolved_title_appears_in_structured_prompt(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        result = engine.query_structured("Haven")

        assert 'title="Haven"' in result
        assert str(haven.id) not in result

    def test_general_context_title_is_untouched(self, tmp_path: Path) -> None:
        ko = make_ko("A fact with no ontology evidence at all")
        engine, _ = build_engine(tmp_path, [], [ko])

        contexts = engine.query_working_context("fact")

        general = next(c for c in contexts if c.kind is ContextKind.GENERAL)
        assert general.title == "General"

    def test_anchor_concept_absent_from_graph_keeps_uuid_title(
        self, tmp_path: Path
    ) -> None:
        # Not reachable through the public query methods (every anchor comes
        # from a candidate's own supporting_concepts, which always names a
        # concept the engine's own graph knows) -- exercised directly against
        # the resolver to pin the deterministic fallback.
        engine, _ = build_engine(tmp_path, [], [])
        unknown_id = uuid4()
        context = WorkingContext(
            key=f"ctx:{unknown_id}",
            title=str(unknown_id),
            kind=ContextKind.TOPIC,
            state=WorkingContextState(status=ContextStatus.REFERENCE),
            anchor_concept_id=unknown_id,
        )

        resolved = engine._resolve_topic_titles([context])

        assert resolved[0].title == str(unknown_id)

    def test_two_topics_resolve_to_distinct_labels(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)
        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
        )

        contexts = engine.query_working_context("Haven and Claude")

        titles = {c.title for c in contexts if c.kind is ContextKind.TOPIC}
        assert titles == {"Haven", "Claude"}


# ---------------------------------------------------------------------------
# TestContextPlanIntegration
# ---------------------------------------------------------------------------


class TestContextPlanIntegration:
    """Phase 1.5: ContextPlanner runs as diagnostics only.

    ``query_with_trace`` runs ``ContextPlanner`` once per call, before
    retrieval begins, and attaches the result to
    ``RetrievalTrace.context_plan``. These tests prove: the planner runs
    exactly once per request; the trace exposes task mode, planning method,
    scope, confidence, and category requirements; and repeated calls are
    deterministic.

    As of Phase 3 (see ``TestCategoryAwareRetrieval`` below),
    ``context_plan.requirements`` genuinely does influence retrieval output
    through ``CategoryPreferenceScorer`` -- the "unaffected by what the plan
    says" claim below only still holds for
    ``test_context_plan_content_does_not_affect_retrieval_output``
    specifically because both plans compared there carry ``requirements=()``
    (no requested categories, hence a zero bonus for every candidate either
    way), not because plan content in general is inert. See
    ``TestCategoryAwareRetrieval`` for the tests proving the opposite for a
    populated plan.
    """

    def test_trace_exposes_pointed_qa_plan_for_a_simple_lookup(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        _, trace = engine.query_with_trace("Haven")

        assert trace.context_plan is not None
        assert trace.context_plan.task_mode == "pointed_qa"
        assert trace.context_plan.planning_method == "deterministic"
        assert trace.context_plan.scope_concept_id is None
        assert trace.context_plan.confidence == 1.0
        assert trace.context_plan.requirements == ()

    def test_trace_exposes_populated_plan_for_a_coding_query(
        self, tmp_path: Path
    ) -> None:
        engine, _ = build_engine(tmp_path, [], [])

        _, trace = engine.query_with_trace("debug why this is failing")

        plan = trace.context_plan
        assert plan is not None
        assert plan.task_mode == "coding_debugging"
        assert len(plan.requirements) > 0
        by_category = {r.category: r for r in plan.requirements}
        assert "constraint" in by_category
        assert by_category["constraint"].priority_tier == "never_drop"
        assert by_category["constraint"].necessity == "required"

    def test_planner_runs_exactly_once_per_query_with_trace_call(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        real_planner = engine._context_planner
        calls: List[str] = []

        class _CountingPlanner:
            def plan(self, raw_query: str, scope_concept_id: Any = None) -> Any:
                calls.append(raw_query)
                return real_planner.plan(raw_query, scope_concept_id=scope_concept_id)

        engine._context_planner = _CountingPlanner()

        engine.query_with_trace("Haven")
        assert calls == ["Haven"]

        engine.query_with_trace("Haven")
        assert calls == ["Haven", "Haven"]

    def test_query_also_triggers_exactly_one_planner_call(
        self, tmp_path: Path
    ) -> None:
        # query() is defined as query_with_trace(raw_query)[0], so it must
        # not cause a second, independent planner invocation.
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        real_planner = engine._context_planner
        calls: List[str] = []

        class _CountingPlanner:
            def plan(self, raw_query: str, scope_concept_id: Any = None) -> Any:
                calls.append(raw_query)
                return real_planner.plan(raw_query, scope_concept_id=scope_concept_id)

        engine._context_planner = _CountingPlanner()

        engine.query("Haven")
        assert calls == ["Haven"]

    def test_context_plan_content_does_not_affect_retrieval_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A wildly different plan (different task_mode/confidence/scope),
        # but with the SAME empty requirements=() as the baseline plan, must
        # not change the context string, the candidate trace, or pipeline
        # stats -- only trace.context_plan itself should differ. Both plans
        # request zero categories, so CategoryPreferenceScorer (Phase 3)
        # applies a zero bonus either way -- this test isolates "does
        # non-requirements plan content leak into retrieval" from "does a
        # requested category change retrieval," which it now deliberately
        # does (see TestCategoryAwareRetrieval). The clock is frozen (as
        # other byte-identical comparisons in this file do) so the ranker's
        # recency component can't introduce an unrelated difference between
        # the two query_with_trace calls below.
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        monkeypatch.setattr(engine_module, "datetime", _FrozenDatetime)

        baseline_context, baseline_trace = engine.query_with_trace("Haven")

        class _FixedPlanner:
            def plan(self, raw_query: str, scope_concept_id: Any = None) -> ContextPlan:
                return ContextPlan(
                    query=raw_query,
                    task_mode=TaskMode.CONTINUATION,
                    requirements=(),
                    scope_concept_id=uuid4(),
                    confidence=0.01,
                    planning_method=PlanningMethod.DETERMINISTIC,
                )

        engine._context_planner = _FixedPlanner()

        context, trace = engine.query_with_trace("Haven")

        assert context == baseline_context
        assert trace.candidates == baseline_trace.candidates
        # Compare every pipeline_stats field except retrieval_latency_ms,
        # which is a live wall-clock measurement (not frozen) and so
        # legitimately differs between the two independent calls below.
        assert trace.pipeline_stats.total_ontology_candidates == (
            baseline_trace.pipeline_stats.total_ontology_candidates
        )
        assert trace.pipeline_stats.total_keyword_candidates == (
            baseline_trace.pipeline_stats.total_keyword_candidates
        )
        assert trace.pipeline_stats.total_merged_candidates == (
            baseline_trace.pipeline_stats.total_merged_candidates
        )
        assert trace.pipeline_stats.total_accepted_candidates == (
            baseline_trace.pipeline_stats.total_accepted_candidates
        )
        assert trace.pipeline_stats.total_rejected_candidates == (
            baseline_trace.pipeline_stats.total_rejected_candidates
        )
        assert trace.pipeline_stats.final_context_size == (
            baseline_trace.pipeline_stats.final_context_size
        )
        # The diagnostic projection itself does reflect the forced plan --
        # proving the trace really is wired to whatever the planner (real
        # or fake) returns, while nothing else moved.
        assert trace.context_plan.task_mode == "continuation"
        assert trace.context_plan.confidence == 0.01

    def test_repeated_calls_produce_deterministic_context_plan(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        _, first_trace = engine.query_with_trace("continue implementing Haven")
        for _ in range(5):
            _, trace = engine.query_with_trace("continue implementing Haven")
            assert trace.context_plan == first_trace.context_plan

    def test_context_plan_serialises_through_trace_to_dict(
        self, tmp_path: Path
    ) -> None:
        engine, _ = build_engine(tmp_path, [], [])

        _, trace = engine.query_with_trace("debug why this is failing")

        restored = type(trace).from_dict(trace.to_dict())
        assert restored.context_plan == trace.context_plan


# ---------------------------------------------------------------------------
# TestCategoryAwareRetrieval
# ---------------------------------------------------------------------------


def make_typed_ko(
    fact: str,
    memory_type: MemoryType,
    *,
    importance: float = 0.5,
    confidence: float = 0.5,
) -> KnowledgeObject:
    """Like ``make_ko``, but with a caller-chosen ``memory_type``.

    ``make_ko`` (module-level, above) always builds ``MemoryType.FACT`` --
    these tests need to control ``memory_type`` directly to exercise
    ``CategoryPreferenceScorer``'s ``MemoryType -> ContextCategory``
    resolution.
    """
    return KnowledgeObject(
        id=uuid4(),
        canonical_fact=fact,
        memory_type=memory_type,
        importance=importance,
        confidence=confidence,
    )


def make_ranked(ko: KnowledgeObject, final_score: float) -> RankedCandidate:
    return RankedCandidate(
        candidate=Candidate(
            knowledge_object=ko,
            supporting_concepts=(),
            attachment_relevance=0.0,
            activation_score=0.0,
        ),
        final_score=final_score,
        score_breakdown={"activation": final_score},
    )


class _FixedRanker:
    """Test double for DeterministicRanker: returns a pre-set ranking.

    Duck-typed to satisfy the only contract ``MemoryEngine`` relies on --
    ``score_all(candidates, config, *, now=None) -> list[RankedCandidate]``
    -- ignoring whatever ``candidates`` real retrieval produced. This lets
    these tests control ``final_score``/``memory_type`` directly rather than
    reverse-engineering ``DeterministicRanker``'s seven-component formula
    (already covered by ``test_deterministic_ranker.py``) to hit an exact
    target score. Mirrors this file's existing ``_FixedRewriter``/
    ``_CountingPlanner`` test-double pattern for other collaborators.
    """

    def __init__(self, ranked: List[RankedCandidate]) -> None:
        self._ranked = ranked

    def score_all(
        self, candidates: Any, config: Any, *, now: Any = None
    ) -> List[RankedCandidate]:
        return list(self._ranked)


class _FixedPlanner:
    """Test double for ContextPlanner: returns a pre-set ContextPlan.

    Decouples these tests from ``ContextPlanner``'s lexical classification
    table (already covered by ``test_context_planner.py``) so a specific
    set of requested categories can be asserted against directly.
    """

    def __init__(self, plan: ContextPlan) -> None:
        self._plan = plan

    def plan(self, raw_query: str, scope_concept_id: Any = None) -> ContextPlan:
        return self._plan


def make_plan(*categories: ContextCategory) -> ContextPlan:
    return ContextPlan(
        query="q",
        task_mode=TaskMode.CODING_DEBUGGING,
        requirements=tuple(
            CategoryRequirement(category=c, necessity=Necessity.REQUIRED)
            for c in categories
        ),
    )


class TestCategoryAwareRetrieval:
    """Phase 3: CategoryPreferenceScorer makes ContextPlan.requirements

    genuinely influence retrieval output for the first time. These tests
    substitute ``engine._ranker`` and ``engine._context_planner`` with fixed
    test doubles so ``final_score``/``memory_type`` and the requested
    categories are both under direct control, decoupled from
    ``DeterministicRanker``'s scoring formula and ``ContextPlanner``'s
    lexical classification (each independently tested elsewhere). Proves:
    a requested category can tip a close race; a clearly-better unrequested
    candidate still wins; the trace exposes base_score/bonus/final_score for
    every candidate; repeated calls are deterministic; and the
    ``TaskMode.POINTED_QA`` sentinel (empty requirements) leaves real,
    non-faked retrieval completely unaffected -- backward compatibility.
    """

    def test_requested_category_wins_a_close_race(self, tmp_path: Path) -> None:
        # DECISION is requested; a DECISION-typed candidate ranked just
        # below an untyped-for-this-plan TASK candidate is nudged ahead of
        # it once CATEGORY_PREFERENCE_BONUS is added (gap is 0.02, smaller
        # than the 0.05 bonus).
        decision_ko = make_typed_ko("Ship the retrieval feature", MemoryType.DECISION)
        task_ko = make_typed_ko("Write the tests", MemoryType.TASK)
        decision_ranked = make_ranked(decision_ko, final_score=0.50)
        task_ranked = make_ranked(task_ko, final_score=0.52)

        engine, _ = build_engine(
            tmp_path, [], [], acceptance_config=PERMISSIVE_ACCEPTANCE
        )
        engine._ranker = _FixedRanker([task_ranked, decision_ranked])
        engine._context_planner = _FixedPlanner(make_plan(ContextCategory.DECISION))

        context, trace = engine.query_with_trace("does not matter")

        assert context.index("Ship the retrieval feature") < context.index(
            "Write the tests"
        )
        by_fact = {c.canonical_fact: c for c in trace.candidates}
        assert by_fact["Ship the retrieval feature"].final_rank == 1
        assert by_fact["Write the tests"].final_rank == 2

    def test_unrelated_candidate_still_wins_when_clearly_better(
        self, tmp_path: Path
    ) -> None:
        # The gap between the two (0.4) is far larger than
        # CATEGORY_PREFERENCE_BONUS (0.05), so requesting DECISION cannot
        # flip the order -- the bonus only closes small gaps.
        decision_ko = make_typed_ko("Ship the retrieval feature", MemoryType.DECISION)
        task_ko = make_typed_ko("Write the tests", MemoryType.TASK)
        decision_ranked = make_ranked(decision_ko, final_score=0.50)
        task_ranked = make_ranked(task_ko, final_score=0.90)

        engine, _ = build_engine(
            tmp_path, [], [], acceptance_config=PERMISSIVE_ACCEPTANCE
        )
        engine._ranker = _FixedRanker([task_ranked, decision_ranked])
        engine._context_planner = _FixedPlanner(make_plan(ContextCategory.DECISION))

        context, _ = engine.query_with_trace("does not matter")

        assert context.index("Write the tests") < context.index(
            "Ship the retrieval feature"
        )

    def test_trace_exposes_base_score_bonus_and_final_score(
        self, tmp_path: Path
    ) -> None:
        decision_ko = make_typed_ko("Ship the retrieval feature", MemoryType.DECISION)
        task_ko = make_typed_ko("Write the tests", MemoryType.TASK)
        decision_ranked = make_ranked(decision_ko, final_score=0.50)
        task_ranked = make_ranked(task_ko, final_score=0.52)

        engine, _ = build_engine(
            tmp_path, [], [], acceptance_config=PERMISSIVE_ACCEPTANCE
        )
        engine._ranker = _FixedRanker([task_ranked, decision_ranked])
        engine._context_planner = _FixedPlanner(make_plan(ContextCategory.DECISION))

        _, trace = engine.query_with_trace("does not matter")

        by_fact = {c.canonical_fact: c for c in trace.candidates}
        decision_trace = by_fact["Ship the retrieval feature"]
        task_trace = by_fact["Write the tests"]

        assert decision_trace.base_score == pytest.approx(0.50)
        assert decision_trace.category_preference_bonus == pytest.approx(
            CATEGORY_PREFERENCE_BONUS
        )
        assert decision_trace.final_score == pytest.approx(
            0.50 + CATEGORY_PREFERENCE_BONUS
        )

        assert task_trace.base_score == pytest.approx(0.52)
        assert task_trace.category_preference_bonus == 0.0
        assert task_trace.final_score == pytest.approx(0.52)

    def test_repeated_calls_are_deterministic(self, tmp_path: Path) -> None:
        decision_ko = make_typed_ko("Ship the retrieval feature", MemoryType.DECISION)
        task_ko = make_typed_ko("Write the tests", MemoryType.TASK)
        decision_ranked = make_ranked(decision_ko, final_score=0.50)
        task_ranked = make_ranked(task_ko, final_score=0.52)

        engine, _ = build_engine(
            tmp_path, [], [], acceptance_config=PERMISSIVE_ACCEPTANCE
        )
        engine._ranker = _FixedRanker([task_ranked, decision_ranked])
        engine._context_planner = _FixedPlanner(make_plan(ContextCategory.DECISION))

        first_context, first_trace = engine.query_with_trace("does not matter")
        for _ in range(5):
            context, trace = engine.query_with_trace("does not matter")
            assert context == first_context
            assert trace.candidates == first_trace.candidates

    def test_pointed_qa_sentinel_is_backward_compatible(
        self, tmp_path: Path
    ) -> None:
        # No fixed planner/ranker here -- this exercises the real
        # ContextPlanner and real DeterministicRanker end to end, for a
        # query ("Haven") that classifies as TaskMode.POINTED_QA (empty
        # requirements). Every accepted candidate's base_score must equal
        # its final_score and carry a zero bonus, proving Phase 3 changes
        # nothing for the sentinel case that was already the common path
        # before this phase existed.
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        _, trace = engine.query_with_trace("Haven")

        assert trace.context_plan.requirements == ()
        accepted = [c for c in trace.candidates if c.accepted]
        assert len(accepted) == 1
        assert accepted[0].category_preference_bonus == 0.0
        assert accepted[0].base_score == accepted[0].final_score


# ---------------------------------------------------------------------------
# TestGapRecoveryIntegration
# ---------------------------------------------------------------------------


class TestGapRecoveryIntegration:
    """Phase 4: GapRecoveryDecision runs as diagnostics only.

    ``query_with_trace`` runs ``decide_gap_recovery`` once per call,
    immediately after coverage analysis, and attaches the result to
    ``RetrievalTrace.gap_recovery``. These tests prove: the decision reflects
    real plan/coverage state end to end (both the no-gap and the
    gap-with-confident-plan cases); it runs exactly once per request; it
    never changes the returned context string, candidate trace, or pipeline
    stats even when it recommends a retry; repeated calls are deterministic;
    and the trace serialises/deserialises, including backward compatibility
    with a payload that predates this field entirely.
    """

    def test_trace_exposes_no_retry_for_pointed_qa(self, tmp_path: Path) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        _, trace = engine.query_with_trace("Haven")

        assert trace.gap_recovery is not None
        assert trace.gap_recovery.should_retry is False
        assert trace.gap_recovery.retry_reason == "no_gap"
        assert trace.gap_recovery.missing_categories == ()
        assert trace.gap_recovery.retry_budget == 0
        assert trace.gap_recovery.recovery_strategy == "none"

    def test_trace_exposes_retry_recommendation_for_unmet_continuation_plan(
        self, tmp_path: Path
    ) -> None:
        # Empty vault: a CONTINUATION-mode query's every required category
        # goes entirely unretrieved, so the real CoverageAnalyzer output has
        # a genuine, non-empty set of missing REQUIRED categories, and the
        # real ContextPlanner's confidence is 1.0 (deterministic-only) --
        # decide_gap_recovery should recommend a retry from real pipeline
        # state, not a stubbed one.
        engine, _ = build_engine(tmp_path, [], [])

        _, trace = engine.query_with_trace("continue implementing Haven")

        assert trace.context_plan.task_mode == "continuation"
        assert trace.coverage.fully_satisfied is False
        assert trace.gap_recovery is not None
        assert trace.gap_recovery.should_retry is True
        assert trace.gap_recovery.retry_reason == "required_category_missing"
        assert set(trace.gap_recovery.missing_categories) == set(
            trace.coverage.missing_required_categories
        )
        assert trace.gap_recovery.retry_budget == DEFAULT_RETRY_BUDGET
        assert trace.gap_recovery.recovery_strategy == "retry_missing_categories"

    def test_decision_runs_exactly_once_per_query_with_trace_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        real_decide = engine_module.decide_gap_recovery
        calls: List[Any] = []

        def _counting_decide(plan: Any, coverage: Any) -> Any:
            calls.append((plan, coverage))
            return real_decide(plan, coverage)

        monkeypatch.setattr(engine_module, "decide_gap_recovery", _counting_decide)

        engine.query_with_trace("Haven")
        assert len(calls) == 1

        engine.query_with_trace("Haven")
        assert len(calls) == 2

    def test_query_also_triggers_exactly_one_decision_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # query() is defined as query_with_trace(raw_query)[0], so it must
        # not cause a second, independent decision call.
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        real_decide = engine_module.decide_gap_recovery
        calls: List[Any] = []

        def _counting_decide(plan: Any, coverage: Any) -> Any:
            calls.append((plan, coverage))
            return real_decide(plan, coverage)

        monkeypatch.setattr(engine_module, "decide_gap_recovery", _counting_decide)

        engine.query("Haven")
        assert len(calls) == 1

    def test_gap_recovery_decision_does_not_affect_retrieval_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force a should_retry=True decision (unlike anything the real
        # pipeline would produce for this simple query) and prove the
        # context string, candidate trace, and pipeline stats are
        # byte-identical to the undoctored baseline -- only
        # trace.gap_recovery itself should differ. The clock is frozen (as
        # other byte-identical comparisons in this file do) so the ranker's
        # recency component can't introduce an unrelated difference between
        # the two query_with_trace calls below.
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        monkeypatch.setattr(engine_module, "datetime", _FrozenDatetime)

        baseline_context, baseline_trace = engine.query_with_trace("Haven")

        forced = GapRecoveryDecision(
            should_retry=True,
            missing_categories=(ContextCategory.DECISION,),
            retry_budget=1,
            retry_reason=RetryReason.REQUIRED_CATEGORY_MISSING,
            recovery_strategy=RecoveryStrategy.RETRY_MISSING_CATEGORIES,
        )
        monkeypatch.setattr(
            engine_module, "decide_gap_recovery", lambda plan, coverage: forced
        )

        context, trace = engine.query_with_trace("Haven")

        assert context == baseline_context
        assert trace.candidates == baseline_trace.candidates
        assert trace.pipeline_stats.total_ontology_candidates == (
            baseline_trace.pipeline_stats.total_ontology_candidates
        )
        assert trace.pipeline_stats.total_keyword_candidates == (
            baseline_trace.pipeline_stats.total_keyword_candidates
        )
        assert trace.pipeline_stats.total_merged_candidates == (
            baseline_trace.pipeline_stats.total_merged_candidates
        )
        assert trace.pipeline_stats.total_accepted_candidates == (
            baseline_trace.pipeline_stats.total_accepted_candidates
        )
        assert trace.pipeline_stats.total_rejected_candidates == (
            baseline_trace.pipeline_stats.total_rejected_candidates
        )
        assert trace.pipeline_stats.final_context_size == (
            baseline_trace.pipeline_stats.final_context_size
        )
        # The diagnostic projection itself does reflect the forced decision
        # -- proving the trace really is wired to whatever decide_gap_recovery
        # (real or faked) returns, while nothing else moved.
        assert trace.gap_recovery.should_retry is True
        assert trace.gap_recovery.retry_reason == "required_category_missing"

    def test_repeated_calls_produce_deterministic_gap_recovery(
        self, tmp_path: Path
    ) -> None:
        engine, _ = build_engine(tmp_path, [], [])

        _, first_trace = engine.query_with_trace("continue implementing Haven")
        for _ in range(5):
            _, trace = engine.query_with_trace("continue implementing Haven")
            assert trace.gap_recovery == first_trace.gap_recovery

    def test_gap_recovery_serialises_through_trace_to_dict(
        self, tmp_path: Path
    ) -> None:
        engine, _ = build_engine(tmp_path, [], [])

        _, trace = engine.query_with_trace("continue implementing Haven")

        restored = type(trace).from_dict(trace.to_dict())
        assert restored.gap_recovery == trace.gap_recovery

    def test_trace_from_dict_without_gap_recovery_key_defaults_to_none(
        self, tmp_path: Path
    ) -> None:
        # Backward compatibility: a serialised trace predating this field
        # (no "gap_recovery" key at all) must still deserialise cleanly.
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        _, trace = engine.query_with_trace("Haven")
        data = trace.to_dict()
        del data["gap_recovery"]

        restored = type(trace).from_dict(data)
        assert restored.gap_recovery is None
        # Everything else on the trace still round-trips normally.
        assert restored.query == trace.query
        assert restored.candidates == trace.candidates


# ---------------------------------------------------------------------------
# TestProjectStateIntegration
# ---------------------------------------------------------------------------


class TestProjectStateIntegration:
    """Phase A: ProjectState runs as diagnostics only.

    ``query_with_trace`` runs ``ProjectStateBuilder.build`` once per call,
    immediately after slot allocation, over the exact same ``allocated``
    list ``ContextBuilder`` already rendered, and attaches the result to
    ``RetrievalTrace.project_state``. These tests prove: the state reflects
    real accepted-candidate content end to end; it is built strictly after
    the context string is already final, so it cannot change the returned
    context string, candidate trace, or pipeline stats; repeated calls are
    deterministic; and the trace serialises/deserialises, including
    backward compatibility with a payload that predates this field
    entirely.
    """

    def _build_full_engine(self, tmp_path: Path) -> MemoryEngine:
        haven = concept("Haven")
        kos = [
            make_typed_ko("Ship Phase A", MemoryType.GOAL),
            make_typed_ko("Use JSON sidecars", MemoryType.DECISION),
            make_typed_ko("Write more tests", MemoryType.TASK),
            make_typed_ko("Waiting on code review", MemoryType.BLOCKER),
            make_typed_ko("Never skip hooks", MemoryType.RULE),
            make_typed_ko("Retrieval pipeline is done", MemoryType.IMPLEMENTATION_STATE),
            make_typed_ko("obsidian/memory_engine/engine.py", MemoryType.CODE_AREA),
            make_typed_ko("Should ProjectState persist?", MemoryType.OPEN_QUESTION),
        ]
        atts = [Attachment.create(ko.id, haven.id) for ko in kos]
        engine, _ = build_engine(
            tmp_path,
            [haven],
            kos,
            attachments=atts,
            config=RetrievalConfig(max_results=20),
            acceptance_config=PERMISSIVE_ACCEPTANCE,
        )
        return engine

    def test_trace_reflects_accepted_candidates_by_category(
        self, tmp_path: Path
    ) -> None:
        engine = self._build_full_engine(tmp_path)

        _, trace = engine.query_with_trace("Haven")

        ps = trace.project_state
        assert ps is not None
        assert ps.current_objective is not None
        assert ps.current_objective.value.canonical_fact == "Ship Phase A"
        assert ps.current_objective.derivation == "memory_direct"
        assert [r.canonical_fact for r in ps.decisions] == ["Use JSON sidecars"]
        assert [r.canonical_fact for r in ps.active_tasks] == ["Write more tests"]
        assert [r.canonical_fact for r in ps.blockers] == ["Waiting on code review"]
        assert [r.canonical_fact for r in ps.constraints] == ["Never skip hooks"]
        assert [r.canonical_fact for r in ps.implementation_state] == [
            "Retrieval pipeline is done"
        ]
        assert [r.canonical_fact for r in ps.code_areas] == [
            "obsidian/memory_engine/engine.py"
        ]
        assert [r.canonical_fact for r in ps.open_questions] == [
            "Should ProjectState persist?"
        ]
        assert ps.gaps == ()
        assert ps.confidence == 1.0

    def test_trace_exposes_full_gaps_for_empty_vault(self, tmp_path: Path) -> None:
        engine, _ = build_engine(tmp_path, [], [])

        _, trace = engine.query_with_trace("continue implementing Haven")

        ps = trace.project_state
        assert ps is not None
        assert ps.current_objective is None
        assert set(ps.gaps) == {
            "current_objective",
            "decisions",
            "active_tasks",
            "blockers",
            "constraints",
            "implementation_state",
            "code_areas",
            "open_questions",
        }
        assert ps.confidence == 0.0

    def test_project_state_does_not_affect_retrieval_output(
        self, tmp_path: Path
    ) -> None:
        engine = self._build_full_engine(tmp_path)

        expected = engine.query("Haven")
        context, trace = engine.query_with_trace("Haven")

        assert context == expected
        assert trace.pipeline_stats.final_context_size == len(context)

    def test_project_state_does_not_affect_candidate_trace(
        self, tmp_path: Path
    ) -> None:
        engine = self._build_full_engine(tmp_path)

        _, trace = engine.query_with_trace("Haven")

        # Every candidate in trace.candidates already carries the same
        # information ProjectState re-derives -- ProjectState must not
        # change any of it (e.g. by re-scoring or re-accepting).
        assert all(ct.accepted for ct in trace.candidates)
        assert len(trace.candidates) == 8

    def test_builder_runs_strictly_after_context_is_rendered(self) -> None:
        # Structural guarantee, not just behavioral: ContextBuilder.build
        # must appear (and therefore run) before ProjectStateBuilder.build
        # in query_with_trace's source, so ProjectState cannot possibly
        # influence the context string -- it does not exist yet when
        # ProjectStateBuilder runs.
        source = Path(engine_module.__file__).read_text(encoding="utf-8")
        context_build_index = source.index("context = self._context_builder.build(")
        project_state_build_index = source.index(
            "project_state = self._project_state_builder.build("
        )
        assert context_build_index < project_state_build_index

    def test_repeated_calls_produce_deterministic_project_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # query_with_trace reads datetime.utcnow() once per call for `now`
        # (which ProjectStateBuilder.build uses for `generated_at`), so two
        # real calls only differ in that one timestamp -- freeze the clock
        # the same way TestWorkingContextQueries.test_deterministic_repeated_calls
        # already does, so the whole ProjectState (not just its content
        # fields) compares equal across calls.
        engine = self._build_full_engine(tmp_path)
        monkeypatch.setattr(engine_module, "datetime", _FrozenDatetime)

        _, first_trace = engine.query_with_trace("Haven")
        for _ in range(5):
            _, trace = engine.query_with_trace("Haven")
            assert trace.project_state == first_trace.project_state

    def test_project_state_serialises_through_trace_to_dict(
        self, tmp_path: Path
    ) -> None:
        engine = self._build_full_engine(tmp_path)

        _, trace = engine.query_with_trace("Haven")

        restored = type(trace).from_dict(trace.to_dict())
        assert restored.project_state == trace.project_state

    def test_trace_from_dict_without_project_state_key_defaults_to_none(
        self, tmp_path: Path
    ) -> None:
        # Backward compatibility: a serialised trace predating this field
        # (no "project_state" key at all) must still deserialise cleanly.
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        _, trace = engine.query_with_trace("Haven")
        data = trace.to_dict()
        del data["project_state"]

        restored = type(trace).from_dict(data)
        assert restored.project_state is None
        # Everything else on the trace still round-trips normally.
        assert restored.query == trace.query
        assert restored.candidates == trace.candidates


# ---------------------------------------------------------------------------
# TestSharedRetrievalPrefixIntegration
# ---------------------------------------------------------------------------


class TestSharedRetrievalPrefixIntegration:
    """Steps 1-2 of ``PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md``.

    ``query()``, ``query_with_trace()``, and ``query_structured()`` (via
    ``_allocate``/``query_working_context``) now share exactly one
    retrieval prefix (``MemoryEngine._run_retrieval``) instead of two
    independently-maintained copies. These tests prove: retrieval and
    Context Planning each execute exactly once per ``query_structured()``
    (and ``query_working_context()``) call -- threading a ``ContextPlan``
    through the shared prefix introduced no duplicate work; a
    ``CONTINUATION``-mode query makes a real ``ProjectState`` available to
    ``StructuredPromptBuilder.render()`` *and* its ``<ProjectState>`` element
    now appears in the rendered prompt (Step 2); a ``POINTED_QA`` (or any
    other non-``CONTINUATION``) query leaves ``project_state`` ``None`` and
    its rendered prompt stays byte-identical to rendering without
    ``project_state`` at all; and the existing ``query``/``query_with_trace``
    pipelines remain byte-identical to before this refactor.
    """

    def test_retrieval_executes_exactly_once_per_query_structured_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        real_retrieve = engine._candidate_retriever.retrieve_with_diagnostics
        calls: List[str] = []

        def _counting_retrieve(query: str) -> Any:
            calls.append(query)
            return real_retrieve(query)

        monkeypatch.setattr(
            engine._candidate_retriever, "retrieve_with_diagnostics", _counting_retrieve
        )

        engine.query_structured("Haven")

        assert calls == ["Haven"]

    def test_retrieval_executes_exactly_once_per_query_working_context_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        real_retrieve = engine._candidate_retriever.retrieve_with_diagnostics
        calls: List[str] = []

        def _counting_retrieve(query: str) -> Any:
            calls.append(query)
            return real_retrieve(query)

        monkeypatch.setattr(
            engine._candidate_retriever, "retrieve_with_diagnostics", _counting_retrieve
        )

        engine.query_working_context("Haven")

        assert calls == ["Haven"]

    def test_context_planner_runs_exactly_once_per_query_structured_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        real_plan = engine._context_planner.plan
        calls: List[str] = []

        def _counting_plan(raw_query: str, scope_concept_id: Any = None) -> Any:
            calls.append(raw_query)
            return real_plan(raw_query, scope_concept_id)

        monkeypatch.setattr(engine._context_planner, "plan", _counting_plan)

        engine.query_structured("Haven")

        assert calls == ["Haven"]

    def test_continuation_mode_makes_project_state_available_to_structured_prompt_builder(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        haven = concept("Haven")
        kos = [
            make_typed_ko("Ship Phase A", MemoryType.GOAL),
            make_typed_ko("Use JSON sidecars", MemoryType.DECISION),
        ]
        atts = [Attachment.create(ko.id, haven.id) for ko in kos]
        engine, _ = build_engine(
            tmp_path,
            [haven],
            kos,
            attachments=atts,
            acceptance_config=PERMISSIVE_ACCEPTANCE,
        )

        captured: dict = {}
        real_render = engine._structured_prompt_builder.render

        def _capturing_render(
            working_contexts: Any, user_request: Any, **kwargs: Any
        ) -> str:
            captured["project_state"] = kwargs.get("project_state")
            return real_render(working_contexts, user_request, **kwargs)

        monkeypatch.setattr(
            engine._structured_prompt_builder, "render", _capturing_render
        )

        engine.query_structured("continue implementing Haven")

        project_state = captured["project_state"]
        assert isinstance(project_state, ProjectState)
        assert project_state.current_objective is not None
        assert project_state.current_objective.value.canonical_fact == "Ship Phase A"
        assert [r.canonical_fact for r in project_state.decisions] == [
            "Use JSON sidecars"
        ]

    def test_non_continuation_mode_leaves_project_state_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        captured: dict = {}
        real_render = engine._structured_prompt_builder.render

        def _capturing_render(
            working_contexts: Any, user_request: Any, **kwargs: Any
        ) -> str:
            captured["project_state"] = kwargs.get("project_state")
            return real_render(working_contexts, user_request, **kwargs)

        monkeypatch.setattr(
            engine._structured_prompt_builder, "render", _capturing_render
        )

        engine.query_structured("Haven")

        assert captured["project_state"] is None

    def test_pointed_qa_prompt_is_byte_identical_to_rendering_without_project_state(
        self, tmp_path: Path
    ) -> None:
        haven = concept("Haven")
        ko = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko.id, haven.id)
        engine, _ = build_engine(tmp_path, [haven], [ko], attachments=[att])

        result = engine.query_structured("Haven")

        contexts = engine.query_working_context("Haven")
        expected = StructuredPromptBuilder().render(contexts, "Haven")
        assert result == expected

    def test_continuation_prompt_renders_project_state(
        self, tmp_path: Path
    ) -> None:
        # Step 2: a CONTINUATION query's prompt now diverges from rendering
        # the same Working Contexts with no project_state -- it carries an
        # additional <ProjectState> element surfacing the same current
        # objective and decision the engine already builds a ProjectState
        # from, via the exact same [N] index its WorkingContext memory uses.
        haven = concept("Haven")
        kos = [
            make_typed_ko("Ship Phase A", MemoryType.GOAL),
            make_typed_ko("Use JSON sidecars", MemoryType.DECISION),
        ]
        atts = [Attachment.create(ko.id, haven.id) for ko in kos]
        engine, _ = build_engine(
            tmp_path,
            [haven],
            kos,
            attachments=atts,
            acceptance_config=PERMISSIVE_ACCEPTANCE,
        )

        result = engine.query_structured("continue implementing Haven")

        contexts = engine.query_working_context("continue implementing Haven")
        without_project_state = StructuredPromptBuilder().render(
            contexts, "continue implementing Haven"
        )
        assert result != without_project_state

        assert "<ProjectState" in result
        assert result.index("</Guidance>") < result.index("<ProjectState")
        assert result.index("<ProjectState") < result.index("<WorkingContext ")
        assert "<CurrentObjective>" in result and "Ship Phase A" in result
        assert "Use JSON sidecars" in result
        assert "<Gaps>" in result  # blockers/active_tasks/etc. are gaps here

    def test_query_and_query_with_trace_unaffected_by_shared_prefix_refactor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        haven = concept("Haven")
        claude = concept("Claude")
        ko_haven = make_ko("Haven is a personal second-brain project")
        ko_claude = make_ko("Claude is an AI assistant by Anthropic")
        att_haven = Attachment.create(ko_haven.id, haven.id)
        att_claude = Attachment.create(ko_claude.id, claude.id)

        engine, _ = build_engine(
            tmp_path,
            [haven, claude],
            [ko_haven, ko_claude],
            attachments=[att_haven, att_claude],
        )

        monkeypatch.setattr(engine_module, "datetime", _FrozenDatetime)

        expected_context, expected_trace = engine.query_with_trace("Haven and Claude")

        # Interleave calls to the other two public methods -- if the shared
        # prefix accidentally carried state across calls, this would surface
        # it.
        engine.query_structured("Haven and Claude")
        engine.query_working_context("Haven and Claude")

        assert engine.query("Haven and Claude") == expected_context
        context, trace = engine.query_with_trace("Haven and Claude")
        assert context == expected_context
        assert trace.candidates == expected_trace.candidates


# ---------------------------------------------------------------------------
# TestCategoryFallbackRetrieval
# ---------------------------------------------------------------------------


class TestCategoryFallbackRetrieval:
    """Fix #2 of ``docs/architecture/GENERIC_CONTINUATION_QUERY_ANALYSIS.md`` §5:
    category-fallback retrieval, wired into ``MemoryEngine._accept_and_allocate``
    /``_category_fallback_candidates``.

    Fires only when a query classifies as ``TaskMode.CONTINUATION`` *and*
    normal acceptance found nothing at all -- these tests confirm both the
    positive case (a generic, vault-vocabulary-free continuation query now
    surfaces real content) and every guard rail named in the architecture
    doc's regression-risk analysis: never fires for a non-CONTINUATION
    query, never fires when normal retrieval already found something, never
    pulls a category the plan didn't request, never re-proposes a
    KnowledgeObject normal retrieval already considered, never surfaces an
    archived memory, and stays bounded per category.
    """

    def test_generic_continuation_query_populates_project_state_and_working_context(
        self, tmp_path: Path
    ) -> None:
        # No concepts/attachments at all (ontology path finds nothing) and
        # every fact below shares no vocabulary with "Continue." (keyword
        # path finds nothing either) -- exactly the structural failure
        # GENERIC_CONTINUATION_QUERY_ANALYSIS.md §2 documents for this query
        # shape. Uses the *default* AcceptanceConfig deliberately, to prove
        # the architecture doc's own claim (§3/§5) that ordinary-quality
        # fallback candidates clear acceptance without any threshold change.
        kos = [
            make_typed_ko("Adopted JSON sidecars for persistence", MemoryType.DECISION),
            make_typed_ko("Write the vault export tests", MemoryType.TASK),
            make_typed_ko("Never delete a KnowledgeObject file directly", MemoryType.RULE),
            make_typed_ko("The CI pipeline is failing intermittently", MemoryType.BLOCKER),
        ]
        engine, _ = build_engine(tmp_path, [], kos)

        result = engine.query_structured("Continue.")

        assert "<ProjectState" in result
        assert "Adopted JSON sidecars for persistence" in result
        assert "Write the vault export tests" in result
        assert "Never delete a KnowledgeObject file directly" in result
        assert "The CI pipeline is failing intermittently" in result

        contexts = engine.query_working_context("Continue.")
        facts = {
            ranked.candidate.knowledge_object.canonical_fact
            for context in contexts
            for bucket in context.buckets
            for ranked in bucket.members
        }
        assert "Adopted JSON sidecars for persistence" in facts
        assert "Write the vault export tests" in facts

    def test_fallback_does_not_fire_for_non_continuation_mode(
        self, tmp_path: Path
    ) -> None:
        kos = [
            make_typed_ko("Adopted JSON sidecars for persistence", MemoryType.DECISION),
            make_typed_ko("Write the vault export tests", MemoryType.TASK),
        ]
        engine, _ = build_engine(
            tmp_path, [], kos, acceptance_config=PERMISSIVE_ACCEPTANCE
        )

        # No CONTINUATION pattern anywhere in this phrasing -> POINTED_QA,
        # requirements=() -- the fallback's own gate must never fire here,
        # regardless of how permissive acceptance is.
        result = engine.query_structured("completely unrelated banana smoothie recipe")
        assert "<ProjectState" not in result
        assert "Adopted JSON sidecars for persistence" not in result

        contexts = engine.query_working_context(
            "completely unrelated banana smoothie recipe"
        )
        assert len(contexts) == 1
        assert contexts[0].kind == ContextKind.GENERAL
        assert all(bucket.members == () for bucket in contexts[0].buckets)

    def test_fallback_is_additive_and_inert_when_normal_retrieval_already_found_something(
        self, tmp_path: Path
    ) -> None:
        # Haven is reachable through the ordinary ontology path for a
        # CONTINUATION query, so normal acceptance is non-empty -- the
        # fallback branch must never run, and must never dilute this
        # already-working result with unrelated category filler.
        haven = concept("Haven")
        ko_haven = make_ko("Haven is a personal second-brain project")
        att = Attachment.create(ko_haven.id, haven.id)
        unrelated_task = make_typed_ko("Totally unrelated task filler", MemoryType.TASK)

        engine, _ = build_engine(
            tmp_path,
            [haven],
            [ko_haven, unrelated_task],
            attachments=[att],
            acceptance_config=PERMISSIVE_ACCEPTANCE,
        )

        result = engine.query_structured("continue implementing Haven")

        assert "Haven is a personal second-brain project" in result
        assert "Totally unrelated task filler" not in result

    def test_fallback_only_pulls_categories_the_plan_requires(
        self, tmp_path: Path
    ) -> None:
        # TASK resolves to ContextCategory.TASK, which CONTINUATION
        # requires. PREFERENCE has no ContextCategory mapping at all
        # (coverage_analyzer.MEMORY_TYPE_CATEGORY) and must never appear via
        # fallback, plan-required or not.
        kos = [
            make_typed_ko("Write the vault export tests", MemoryType.TASK),
            make_typed_ko("Prefers dark mode editors", MemoryType.PREFERENCE),
        ]
        engine, _ = build_engine(
            tmp_path, [], kos, acceptance_config=PERMISSIVE_ACCEPTANCE
        )

        result = engine.query_structured("Continue.")

        assert "Write the vault export tests" in result
        assert "Prefers dark mode editors" not in result

    def test_fallback_excludes_archived_knowledge_objects(self, tmp_path: Path) -> None:
        active = make_typed_ko("Adopted JSON sidecars for persistence", MemoryType.DECISION)
        archived = KnowledgeObject(
            id=uuid4(),
            canonical_fact="Superseded decision from last quarter",
            memory_type=MemoryType.DECISION,
            valid_until=datetime.utcnow() - timedelta(days=1),
        )
        engine, _ = build_engine(
            tmp_path, [], [active, archived], acceptance_config=PERMISSIVE_ACCEPTANCE
        )

        result = engine.query_structured("Continue.")

        assert "Adopted JSON sidecars for persistence" in result
        assert "Superseded decision from last quarter" not in result

    def test_fallback_is_bounded_per_category(self, tmp_path: Path) -> None:
        # Eight TASK-category candidates, strictly ordered by importance so
        # the top five are unambiguous; _CATEGORY_FALLBACK_PER_CATEGORY_LIMIT
        # (5) must keep only the five highest-scoring ones, dropping the
        # three lowest, deterministically.
        kos = [
            make_typed_ko(f"Task number {i}", MemoryType.TASK, importance=0.3 + i * 0.05)
            for i in range(8)
        ]
        engine, _ = build_engine(
            tmp_path, [], kos, acceptance_config=PERMISSIVE_ACCEPTANCE
        )

        contexts = engine.query_working_context("Continue.")
        facts = {
            ranked.candidate.knowledge_object.canonical_fact
            for context in contexts
            for bucket in context.buckets
            for ranked in bucket.members
        }
        task_facts = {f for f in facts if f.startswith("Task number")}
        assert len(task_facts) == 5
        # Highest-importance five (indices 3-7) survive; lowest three (0-2)
        # are dropped.
        assert task_facts == {f"Task number {i}" for i in range(3, 8)}

    def test_fallback_excludes_knowledge_object_ids_already_in_ranked_all(
        self, tmp_path: Path
    ) -> None:
        # Direct unit test of _category_fallback_candidates (same convention
        # TestMergeCandidates already uses for _merge_candidates): a
        # KnowledgeObject id already present in ranked_all -- considered by
        # normal retrieval, whatever its acceptance outcome -- must never be
        # re-proposed by the fallback just because its category still has
        # room; a fresh, never-considered KO of the same category must be.
        considered = make_typed_ko("Already considered decision", MemoryType.DECISION)
        fresh = make_typed_ko("A fresh, unconsidered decision", MemoryType.DECISION)
        engine, _ = build_engine(tmp_path, [], [considered, fresh])

        plan = ContextPlan(
            query="Continue.",
            task_mode=TaskMode.CONTINUATION,
            requirements=(
                CategoryRequirement(
                    category=ContextCategory.DECISION, necessity=Necessity.REQUIRED
                ),
            ),
        )
        already_ranked = [
            RankedCandidate(
                candidate=Candidate(
                    knowledge_object=considered,
                    supporting_concepts=(),
                    attachment_relevance=0.0,
                    activation_score=0.0,
                ),
                final_score=0.05,
                score_breakdown={"importance": 0.05},
            )
        ]

        fallback = engine._category_fallback_candidates(
            plan, already_ranked, datetime.utcnow()
        )

        fallback_ids = {rc.candidate.knowledge_object.id for rc in fallback}
        assert considered.id not in fallback_ids
        assert fresh.id in fallback_ids
