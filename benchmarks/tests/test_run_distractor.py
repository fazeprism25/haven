"""Tests for :mod:`benchmarks.runners.run_distractor`.

Covers the deterministic, judge-free pieces: distractor generation,
non-mutating injection, aggregation, and that ``run_sweep`` drives
``run_benchmark`` once per (count × benchmark) with the distractors
prepended. ``run_benchmark`` itself is stubbed so no LLM judge or API key
is needed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from benchmarks.runners import run_distractor


class TestGenerateDistractors:
    def test_count_matches_request(self) -> None:
        assert len(run_distractor.generate_distractors(0)) == 0
        assert len(run_distractor.generate_distractors(37)) == 37

    def test_is_deterministic_for_same_seed(self) -> None:
        assert run_distractor.generate_distractors(20, seed=1) == run_distractor.generate_distractors(20, seed=1)

    def test_different_seeds_differ(self) -> None:
        assert run_distractor.generate_distractors(20, seed=1) != run_distractor.generate_distractors(20, seed=2)

    def test_entries_have_dataset_shape(self) -> None:
        for entry in run_distractor.generate_distractors(5):
            assert set(entry) == {"speaker", "text"}
            assert entry["speaker"] == "user"
            assert entry["text"]

    def test_entries_are_distinct(self) -> None:
        texts = [e["text"] for e in run_distractor.generate_distractors(50)]
        assert len(set(texts)) == len(texts)

    def test_negative_count_raises(self) -> None:
        with pytest.raises(ValueError):
            run_distractor.generate_distractors(-1)


class TestInjectDistractors:
    def test_prepends_and_preserves_real_conversation(self) -> None:
        benchmark = {"conversation": [{"speaker": "user", "text": "real fact"}]}
        distractors = [{"speaker": "user", "text": "noise"}]
        injected = run_distractor.inject_distractors(benchmark, distractors)
        assert injected["conversation"] == [
            {"speaker": "user", "text": "noise"},
            {"speaker": "user", "text": "real fact"},
        ]

    def test_does_not_mutate_original(self) -> None:
        benchmark = {"conversation": [{"speaker": "user", "text": "real fact"}]}
        original = list(benchmark["conversation"])
        run_distractor.inject_distractors(benchmark, [{"speaker": "user", "text": "noise"}])
        assert benchmark["conversation"] == original

    def test_real_memories_stay_most_recent(self) -> None:
        # Recency-based adapters rely on the real turns being last.
        benchmark = {"conversation": [{"speaker": "user", "text": "latest real fact"}]}
        distractors = run_distractor.generate_distractors(10)
        injected = run_distractor.inject_distractors(benchmark, distractors)
        assert injected["conversation"][-1]["text"] == "latest real fact"


class TestAggregate:
    def test_counts_and_pass_rate(self) -> None:
        results = [
            {"passed": True, "category": "goals"},
            {"passed": False, "category": "goals"},
            {"passed": True, "category": "identity"},
        ]
        agg = run_distractor._aggregate(results)
        assert agg["total"] == 3
        assert agg["passed"] == 2
        assert agg["pass_rate"] == pytest.approx(2 / 3)
        assert agg["by_category"]["goals"] == {"passed": 1, "total": 2}
        assert agg["by_category"]["identity"] == {"passed": 1, "total": 1}


class TestRunSweep:
    def test_drives_run_benchmark_per_count_and_prepends_distractors(self) -> None:
        fake_benchmarks = [
            {"benchmark_id": "a", "category": "goals", "conversation": [{"speaker": "user", "text": "fact a"}]},
            {"benchmark_id": "b", "category": "goals", "conversation": [{"speaker": "user", "text": "fact b"}]},
        ]

        seen_lengths = []

        def fake_run_benchmark(benchmark, adapter_cls=None, query_rewriter=None):
            seen_lengths.append(len(benchmark["conversation"]))
            return {"benchmark_id": benchmark["benchmark_id"], "category": "goals", "passed": True}

        with patch.object(run_distractor, "discover_dataset_dirs", return_value=["d"]), \
             patch.object(run_distractor, "load_benchmarks", return_value=fake_benchmarks), \
             patch.object(run_distractor, "get_adapter_cls", return_value=object), \
             patch.object(run_distractor, "run_benchmark", side_effect=fake_run_benchmark):
            data = run_distractor.run_sweep("return_all", counts=[0, 5])

        # 2 benchmarks x 2 counts = 4 run_benchmark calls.
        assert seen_lengths == [1, 1, 6, 6]  # n=0 -> 1 turn; n=5 -> 5 distractors + 1
        assert [row["distractors"] for row in data["sweep"]] == [0, 5]
        assert all(row["pass_rate"] == 1.0 for row in data["sweep"])
        assert data["metadata"]["adapter"] == "return_all"

    def test_category_filter_and_limit(self) -> None:
        fake_benchmarks = [
            {"benchmark_id": "a", "category": "goals", "conversation": [{"speaker": "user", "text": "x"}]},
            {"benchmark_id": "b", "category": "identity", "conversation": [{"speaker": "user", "text": "y"}]},
            {"benchmark_id": "c", "category": "goals", "conversation": [{"speaker": "user", "text": "z"}]},
        ]
        with patch.object(run_distractor, "discover_dataset_dirs", return_value=["d"]), \
             patch.object(run_distractor, "load_benchmarks", return_value=fake_benchmarks), \
             patch.object(run_distractor, "get_adapter_cls", return_value=object), \
             patch.object(run_distractor, "run_benchmark",
                          side_effect=lambda b, adapter_cls=None, query_rewriter=None:
                          {"category": b["category"], "passed": True}):
            data = run_distractor.run_sweep("recency", counts=[0], categories=["goals"], limit=1)

        assert data["metadata"]["total_benchmarks"] == 1
        assert data["sweep"][0]["total"] == 1


class TestRegistryResolution:
    @pytest.mark.parametrize(
        "name",
        ["return_all", "recency", "bm25", "embedding",
         "haven_no_ontology", "haven_no_keyword", "haven_no_recency"],
    )
    def test_new_adapter_names_resolve(self, name) -> None:
        from benchmarks.runners.run_benchmarks import get_adapter_cls

        assert get_adapter_cls(name) is not None
