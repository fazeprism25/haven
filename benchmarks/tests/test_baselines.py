"""Tests for :mod:`benchmarks.adapters.baselines`.

Exercises each baseline purely through the mem0-shaped interface the
runner drives (``from_config`` / ``delete_all`` / ``add`` / ``search``)
and the runner's answer-assembly convention, so a drift from what
``run_benchmarks.py`` actually calls fails here. No LLM judge or API key
is involved.
"""

from __future__ import annotations

import pytest

from benchmarks.adapters.baselines import (
    BM25Adapter,
    EmbeddingAdapter,
    RecencyAdapter,
    ReturnAllAdapter,
)


def _messages(*texts: str) -> list[dict]:
    return [{"role": "user", "content": text} for text in texts]


def _answer(result: dict) -> str:
    return " ".join(mem["memory"] for mem in result.get("results", []))


class TestReturnAll:
    def test_returns_every_memory_in_insertion_order(self) -> None:
        adapter = ReturnAllAdapter.from_config({})
        adapter.add(_messages("first fact", "second fact", "third fact"))
        assert _answer(adapter.search("anything")) == "first fact second fact third fact"

    def test_empty_store_returns_no_results(self) -> None:
        assert ReturnAllAdapter.from_config({}).search("q") == {"results": []}

    def test_keeps_superseded_memory_by_design(self) -> None:
        # The whole point of the floor baseline: it never drops the old fact.
        adapter = ReturnAllAdapter.from_config({})
        adapter.add(_messages("I use OpenAI embeddings.", "I now use FastEmbed."))
        answer = _answer(adapter.search("which embeddings?"))
        assert "OpenAI" in answer and "FastEmbed" in answer


class TestRecency:
    def test_returns_only_the_single_most_recent_memory(self) -> None:
        adapter = RecencyAdapter.from_config({})
        adapter.add(_messages("I use OpenAI embeddings.", "I now use FastEmbed."))
        answer = _answer(adapter.search("which embeddings?"))
        assert answer == "I now use FastEmbed."
        assert "OpenAI" not in answer

    def test_empty_store_returns_no_results(self) -> None:
        assert RecencyAdapter.from_config({}).search("q") == {"results": []}


class TestBM25:
    def test_ranks_lexically_overlapping_memory_first(self) -> None:
        adapter = BM25Adapter.from_config({})
        adapter.add(_messages(
            "The user studies mechanical engineering.",
            "The user enjoys hiking on weekends.",
        ))
        result = adapter.search("What is the user's field of study in engineering?")
        assert result["results"][0]["memory"] == "The user studies mechanical engineering."

    def test_no_lexical_overlap_returns_nothing(self) -> None:
        adapter = BM25Adapter.from_config({})
        adapter.add(_messages("The user studies mechanical engineering."))
        assert adapter.search("photosynthesis chlorophyll xylophone") == {"results": []}

    def test_empty_query_returns_nothing(self) -> None:
        adapter = BM25Adapter.from_config({})
        adapter.add(_messages("The user studies mechanical engineering."))
        assert adapter.search("!!! ??? ...") == {"results": []}

    def test_is_deterministic_across_repeated_searches(self) -> None:
        adapter = BM25Adapter.from_config({})
        adapter.add(_messages("alpha beta gamma", "beta gamma delta", "gamma delta epsilon"))
        first = adapter.search("beta gamma")
        second = adapter.search("beta gamma")
        assert first == second

    def test_caps_results_at_top_k(self) -> None:
        adapter = BM25Adapter.from_config({})
        # Ten memories all containing the query term; top-k must bound output.
        adapter.add(_messages(*[f"shared token unique{i}" for i in range(10)]))
        result = adapter.search("shared")
        assert len(result["results"]) <= 5


class TestEmbedding:
    def test_ranks_semantically_closest_memory_first(self) -> None:
        try:
            adapter = EmbeddingAdapter.from_config({})
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"embedding model unavailable: {exc}")
        adapter.add(_messages(
            "The user studies mechanical engineering.",
            "The weather was sunny during the picnic.",
        ))
        try:
            result = adapter.search("What does the user study at university?")
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"embedding model unavailable: {exc}")
        assert result["results"][0]["memory"] == "The user studies mechanical engineering."

    def test_empty_store_returns_no_results(self) -> None:
        assert EmbeddingAdapter.from_config({}).search("q") == {"results": []}


class TestDeleteAllAndConfig:
    @pytest.mark.parametrize("cls", [ReturnAllAdapter, RecencyAdapter, BM25Adapter, EmbeddingAdapter])
    def test_delete_all_resets_store(self, cls) -> None:
        adapter = cls.from_config({})
        adapter.add(_messages("some memory"))
        adapter.delete_all(filters={"user_id": "user123"})
        assert adapter.search("some") == {"results": []}

    @pytest.mark.parametrize("cls", [ReturnAllAdapter, RecencyAdapter, BM25Adapter, EmbeddingAdapter])
    def test_add_skips_empty_content(self, cls) -> None:
        adapter = cls.from_config({})
        result = adapter.add(_messages("", "real"))
        assert len(result["results"]) == 1
