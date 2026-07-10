"""Tests for :mod:`benchmarks.adapters.haven_adapter`.

Exercises HavenAdapter purely through the mem0-shaped interface the
benchmark runner drives (``from_config`` / ``delete_all`` / ``add`` /
``search``), plus the runner's own answer-assembly convention
(``" ".join(mem["memory"] for mem in results["results"])``), so these
tests fail if the adapter ever drifts from what
``benchmarks/runners/run_benchmarks.py`` actually calls.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from benchmarks.adapters.haven_adapter import HavenAdapter
from obsidian.memory_engine.query_rewriter import QueryRewriter


def _messages(*texts: str) -> list[dict]:
    return [{"role": "user", "content": text} for text in texts]


def _answer(result: dict) -> str:
    return " ".join(mem["memory"] for mem in result.get("results", []))


class TestFromConfig:
    def test_returns_haven_adapter_instance(self) -> None:
        adapter = HavenAdapter.from_config({"embedder": {"provider": "fastembed"}})
        assert isinstance(adapter, HavenAdapter)

    def test_accepts_empty_config(self) -> None:
        adapter = HavenAdapter.from_config({})
        assert isinstance(adapter, HavenAdapter)

    def test_accepts_none_config(self) -> None:
        adapter = HavenAdapter.from_config(None)
        assert isinstance(adapter, HavenAdapter)

    def test_each_instance_gets_isolated_storage(self) -> None:
        a = HavenAdapter.from_config({})
        b = HavenAdapter.from_config({})
        assert a._vault_dir != b._vault_dir
        assert a._concept_dir != b._concept_dir

    def test_fresh_instance_has_no_prior_data(self) -> None:
        adapter = HavenAdapter.from_config({})
        result = adapter.search("anything")
        assert result == {"results": []}


class TestAdd:
    def test_add_returns_one_result_per_message(self) -> None:
        adapter = HavenAdapter.from_config({})
        result = adapter.add(
            _messages("Haven uses Claude.", "Haven uses Qdrant."),
            user_id="user123",
            agent_id="agent456",
            infer=False,
        )
        assert len(result["results"]) == 2
        assert result["results"][0]["memory"] == "Haven uses Claude."
        assert result["results"][1]["memory"] == "Haven uses Qdrant."

    def test_add_skips_empty_content(self) -> None:
        adapter = HavenAdapter.from_config({})
        result = adapter.add(_messages("", "Haven uses Claude."))
        assert len(result["results"]) == 1

    def test_add_stores_text_verbatim_when_infer_false(self) -> None:
        adapter = HavenAdapter.from_config({})
        text = "For my personal AI project I decided to build the Manager AI before GraphRAG."
        adapter.add(_messages(text), infer=False)
        stored = list(adapter._vault_dir.glob("*.md"))
        assert len(stored) == 1
        assert text in stored[0].read_text(encoding="utf-8")

    def test_add_writes_concept_files_for_detected_concepts(self) -> None:
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude for reasoning."))
        concept_files = list(adapter._concept_dir.glob("*.md"))
        # "Haven" and "Claude" are both capitalized-span concepts.
        assert len(concept_files) == 2

    def test_add_is_cumulative_across_calls(self) -> None:
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude."))
        adapter.add(_messages("Haven uses Qdrant."))
        assert len(list(adapter._vault_dir.glob("*.md"))) == 2


class TestSearch:
    def test_search_finds_added_memory_by_concept(self) -> None:
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude for reasoning."))
        result = adapter.search("What does Haven use?")
        answer = _answer(result)
        assert "Haven uses Claude for reasoning." in answer

    def test_search_returns_empty_results_for_unresolved_query(self) -> None:
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude for reasoning."))
        result = adapter.search("completely unrelated gibberish query xyz")
        assert result == {"results": []}

    def test_search_result_shape_matches_runner_expectations(self) -> None:
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude for reasoning."))
        result = adapter.search("Haven")
        assert "results" in result
        for mem in result["results"]:
            assert "memory" in mem
            assert isinstance(mem["memory"], str)

    def test_search_reflects_relationship_between_co_mentioned_concepts(self) -> None:
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude for reasoning."))
        # "Claude" was co-detected with "Haven" in the same KnowledgeObject,
        # so activation spreading should surface the same fact from either seed.
        result = adapter.search("Tell me about Claude")
        answer = _answer(result)
        assert "Haven uses Claude for reasoning." in answer

    def test_search_returns_one_result_entry_per_accepted_candidate(self) -> None:
        """Guards against the metadata-leak regression documented in
        benchmarks/BENCHMARK_AUDIT.md (Critical-1): search() must return
        one result per candidate MemoryEngine accepted, not a single
        joined block."""
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude for reasoning."))
        adapter.add(_messages("Haven uses Qdrant for vector storage."))
        result = adapter.search("What does Haven use?")
        assert len(result["results"]) == 2

    def test_search_results_carry_no_metadata_annotations(self) -> None:
        """Each ``memory`` entry must be raw ``canonical_fact`` text only --
        no ``type:``/``confidence:``/``importance:``/``confirmations:``/
        ``valid_from:``/``valid_until:`` annotations -- so Haven's answer
        is the same shape mem0 and the baselines return to the judge."""
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude for reasoning."))
        result = adapter.search("Haven")
        assert result["results"] == [
            {"id": mem["id"], "memory": "Haven uses Claude for reasoning."}
            for mem in result["results"]
        ]
        for mem in result["results"]:
            assert "confidence:" not in mem["memory"]
            assert "valid_from:" not in mem["memory"]

    def test_decision_benchmark_scenario(self) -> None:
        """Adapted from benchmarks/datasets/decisions/basic_001.json."""
        adapter = HavenAdapter.from_config({})
        adapter.add(
            _messages(
                "For my personal AI project I decided to build the Manager AI "
                "before GraphRAG."
            )
        )
        adapter.add(
            _messages(
                "The reason is that extraction quality appears to be a larger "
                "bottleneck than retrieval quality."
            )
        )

        result = adapter.search("What did I decide about Manager AI?")
        answer = _answer(result)

        assert "Manager AI" in answer
        assert "GraphRAG first" not in answer

    def test_full_benchmark_query_resolves_via_hybrid_retrieval(self) -> None:
        """Adapted from benchmarks/datasets/decisions/basic_001.json's
        actual query text.

        This used to document a gap: the query named no detected Concept,
        so the (then keyword-only) QueryResolver seeded nothing and
        returned empty results. Hybrid retrieval (see
        obsidian/memory_engine/hybrid_candidate_retriever.py) closed that
        gap — plain keyword overlap ("build", "project") now surfaces the
        memory even without a concept-name match.
        """
        adapter = HavenAdapter.from_config({})
        adapter.add(
            _messages(
                "For my personal AI project I decided to build the Manager AI "
                "before GraphRAG."
            )
        )
        result = adapter.search("What should I build next for the project?")
        answer = _answer(result)

        assert "Manager AI" in answer


class TestBuildContinuationContext:
    """Covers HavenAdapter's override of BaseAdapter.build_continuation_context
    (see docs/architecture/CONTINUATION_BENCHMARK_DESIGN.md §3): it must call
    MemoryEngine.query_structured(), not search()."""

    def test_returns_structured_prompt_not_flat_join(self) -> None:
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude for reasoning."))
        context = adapter.build_continuation_context("What does Haven use?")
        assert "Haven uses Claude for reasoning." in context
        # query_structured() renders an XML-delimited prompt; a flat
        # search()+join would just be the raw fact with no wrapping.
        assert context != "Haven uses Claude for reasoning."

    def test_calls_query_structured_not_query_with_trace(self) -> None:
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude for reasoning."))

        with patch(
            "benchmarks.adapters.haven_adapter.MemoryEngine"
        ) as mock_engine_cls:
            mock_engine_cls.return_value.query_structured.return_value = "<Prompt/>"
            result = adapter.build_continuation_context("What does Haven use?")

            mock_engine_cls.return_value.query_structured.assert_called_once_with(
                "What does Haven use?"
            )
            mock_engine_cls.return_value.query_with_trace.assert_not_called()
            assert result == "<Prompt/>"

    def test_empty_adapter_still_returns_a_string(self) -> None:
        adapter = HavenAdapter.from_config({})
        context = adapter.build_continuation_context("anything")
        assert isinstance(context, str)

    def test_passes_query_rewriter_through_like_search_does(self) -> None:
        rewriter = QueryRewriter()
        adapter = HavenAdapter(query_rewriter=rewriter)
        adapter.add(_messages("Haven uses Claude for reasoning."))

        with patch(
            "benchmarks.adapters.haven_adapter.MemoryEngine"
        ) as mock_engine_cls:
            mock_engine_cls.return_value.query_structured.return_value = "<Prompt/>"
            adapter.build_continuation_context("What does Haven use?")

            mock_engine_cls.assert_called_once()
            _, kwargs = mock_engine_cls.call_args
            assert kwargs["query_rewriter"] is rewriter


class TestDeleteAll:
    def test_delete_all_clears_vault_and_concept_files(self) -> None:
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude."))
        adapter.delete_all(filters={"user_id": "user123"})
        assert list(adapter._vault_dir.glob("*.md")) == []
        assert list(adapter._concept_dir.glob("*.md")) == []

    def test_delete_all_resets_search_results(self) -> None:
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude."))
        adapter.delete_all()
        result = adapter.search("Haven")
        assert result == {"results": []}

    def test_delete_all_on_empty_adapter_does_not_raise(self) -> None:
        adapter = HavenAdapter.from_config({})
        adapter.delete_all(filters={"user_id": "user123"})

    def test_add_after_delete_all_still_works(self) -> None:
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude."))
        adapter.delete_all()
        adapter.add(_messages("Haven uses Qdrant."))
        result = adapter.search("Haven")
        assert "Haven uses Qdrant." in _answer(result)
        assert "Haven uses Claude." not in _answer(result)


class TestFullBenchmarkFlow:
    """Replays run_benchmark()'s exact call sequence against one dataset file."""

    def test_runner_call_sequence_end_to_end(self) -> None:
        config = {
            "embedder": {"provider": "fastembed", "config": {"model": "x"}},
            "vector_store": {"provider": "qdrant", "config": {}},
            "llm": {"provider": "ollama", "config": {"model": "x"}},
        }

        adapter = HavenAdapter.from_config(config)

        try:
            adapter.delete_all(filters={"user_id": "user123"})
        except Exception:
            pass

        conversation = [
            {
                "speaker": "user",
                "text": "For my personal AI project I decided to build the "
                "Manager AI before GraphRAG.",
            },
            {
                "speaker": "user",
                "text": "The reason is that extraction quality appears to be a "
                "larger bottleneck than retrieval quality.",
            },
        ]
        for memory in conversation:
            adapter.add(
                messages=[{"role": "user", "content": memory["text"]}],
                user_id="user123",
                agent_id="agent456",
                infer=False,
            )

        result = adapter.search(
            query="What did I decide about Manager AI?",
            filters={"user_id": "user123"},
        )
        answer = " ".join(mem["memory"] for mem in result.get("results", []))

        assert "Manager AI" in answer
        assert "GraphRAG first" not in answer


class TestQueryRewriterIntegration:
    """Covers the optional ``query_rewriter`` dependency added to HavenAdapter.

    ``MemoryEngine`` itself already fully implements multi-query expansion
    (see obsidian/memory_engine/engine.py); these tests only cover the one
    thing HavenAdapter is responsible for: constructing it with the right
    ``query_rewriter`` argument.
    """

    def test_default_behavior_unchanged_with_no_rewriter(self) -> None:
        """No ``query_rewriter`` arg at all -> same as before this param existed."""
        adapter = HavenAdapter.from_config({})
        assert adapter._query_rewriter is None

        adapter.add(_messages("Haven uses Claude for reasoning."))
        result = adapter.search("What does Haven use?")
        assert "Haven uses Claude for reasoning." in _answer(result)

    def test_explicit_none_is_equivalent_to_default(self) -> None:
        """Disabled path (``query_rewriter=None`` passed explicitly) is unchanged."""
        adapter = HavenAdapter(query_rewriter=None)
        assert adapter._query_rewriter is None

        adapter.add(_messages("Haven uses Claude for reasoning."))
        result = adapter.search("What does Haven use?")
        assert "Haven uses Claude for reasoning." in _answer(result)

    def test_default_construction_never_calls_memory_engine_with_a_rewriter(
        self,
    ) -> None:
        """When unset, MemoryEngine must receive ``query_rewriter=None`` — the
        exact call it received before this parameter existed."""
        adapter = HavenAdapter.from_config({})
        adapter.add(_messages("Haven uses Claude for reasoning."))

        with patch(
            "benchmarks.adapters.haven_adapter.MemoryEngine"
        ) as mock_engine_cls:
            mock_engine_cls.return_value.query_with_trace.return_value = (
                "",
                SimpleNamespace(candidates=()),
            )
            adapter.search("What does Haven use?")

            mock_engine_cls.assert_called_once()
            _, kwargs = mock_engine_cls.call_args
            assert kwargs["query_rewriter"] is None

    def test_query_rewriter_instance_passed_through_to_memory_engine(self) -> None:
        """A supplied QueryRewriter reaches MemoryEngine's constructor unchanged."""
        rewriter = QueryRewriter()
        adapter = HavenAdapter(query_rewriter=rewriter)
        assert adapter._query_rewriter is rewriter

        adapter.add(_messages("Haven uses Claude for reasoning."))

        with patch(
            "benchmarks.adapters.haven_adapter.MemoryEngine"
        ) as mock_engine_cls:
            mock_engine_cls.return_value.query_with_trace.return_value = (
                "",
                SimpleNamespace(candidates=()),
            )
            adapter.search("What does Haven use?")

            mock_engine_cls.assert_called_once()
            _, kwargs = mock_engine_cls.call_args
            assert kwargs["query_rewriter"] is rewriter

    def test_enabled_rewriter_with_no_api_key_still_behaves_like_disabled(
        self,
    ) -> None:
        """QueryRewriter fails open with no API key configured, so search
        results are unaffected even when a (non-functional) rewriter is
        wired in — this documents current environment behavior, not a
        HavenAdapter guarantee."""
        adapter = HavenAdapter(query_rewriter=QueryRewriter())
        adapter.add(_messages("Haven uses Claude for reasoning."))
        result = adapter.search("What does Haven use?")
        assert "Haven uses Claude for reasoning." in _answer(result)
