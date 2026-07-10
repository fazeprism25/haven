"""Tests for :mod:`benchmarks.runners.run_continuation_benchmarks`.

Uses a fake in-memory :class:`~benchmarks.adapters.base.BaseAdapter` plus
stubbed Stage B (:func:`generate_continuation`) and Stage C
(:func:`judge_continuation`) calls, so these tests need neither a running
Qdrant/Ollama nor a real Qwen Cloud API key -- mirrors the pattern
``benchmarks/tests/test_run_benchmarks_adapter.py`` already uses for the
existing runner.

Also confirms (:class:`TestExistingSuiteUnaffected`) that this module's
dataset root is disjoint from ``benchmarks/datasets/`` -- the thing that
makes this a genuinely additive pipeline rather than one that could ever
perturb an existing category's run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from benchmarks.adapters.base import BaseAdapter
from benchmarks.runners import run_benchmarks
from benchmarks.runners import run_continuation_benchmarks as rcb


class FakeContinuationAdapter(BaseAdapter):
    """Minimal in-memory BaseAdapter used only to exercise the runner's
    call sequence. build_continuation_context uses BaseAdapter's own
    default (search() + join) unless a subclass overrides it."""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self._store: List[str] = []

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None) -> "FakeContinuationAdapter":
        return cls()

    def delete_all(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.calls.append("delete_all")
        self._store.clear()
        return {"message": "ok"}

    def add(
        self,
        messages: List[Dict[str, str]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        infer: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self.calls.append("add")
        results = []
        for message in messages:
            self._store.append(message["content"])
            results.append({"id": "1", "memory": message["content"], "event": "ADD"})
        return {"results": results}

    def search(self, query: str, filters: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append("search")
        return {"results": [{"memory": text} for text in self._store]}

    def build_continuation_context(self, query: str) -> str:
        self.calls.append("build_continuation_context")
        return super().build_continuation_context(query)


_CASE = {
    "benchmark_id": "resume_coding_fake_001",
    "category": "resume_coding",
    "tier": "short",
    "domain": "unit_test",
    "conversation": [{"speaker": "user", "text": "Decided: use blended ranking."}],
    "ground_truth": {"current_objective": "x", "top_priority_next_action": "y"},
    "queries": [
        {"text": "Continue implementing the project.", "task_mode_hint": "continuation"},
        {"text": "What should we work on next?", "task_mode_hint": "ambiguous"},
    ],
    "expected": {"must_state": [], "must_not_state": [], "forbidden_actions": [], "must_prioritize": ["y"]},
}


def _stub_generate_continuation(context, query, model=None):
    return f"response to: {query}"


def _stub_judge_pass(query, ground_truth, expected, response, model=None):
    return {
        "must_state_score": 1.0,
        "must_not_state_violations": [],
        "forbidden_action_violations": [],
        "prioritization_correct": True,
        "coherence_score": 1.0,
        "reason": "ok",
        "failure_type": "NONE",
        "score": 1.0,
        "passed": True,
    }


def _stub_judge_hard_fail(query, ground_truth, expected, response, model=None):
    return {
        "must_state_score": 1.0,
        "must_not_state_violations": ["revived a rejected approach"],
        "forbidden_action_violations": [],
        "prioritization_correct": True,
        "coherence_score": 1.0,
        "reason": "stale content surfaced",
        "failure_type": "STALE_STATE_SURFACED",
        "score": 0.2,
        "passed": False,
    }


class TestLoadContinuationBenchmarks:
    def test_valid_case_is_loaded(self, tmp_path: Path) -> None:
        (tmp_path / "basic_001.json").write_text(json.dumps(_CASE), encoding="utf-8")
        loaded = rcb.load_continuation_benchmarks(str(tmp_path))
        assert loaded == [_CASE]

    def test_case_missing_queries_is_skipped(self, tmp_path: Path) -> None:
        """A continuation case has 'queries' (list), not 'query' (str) --
        this is exactly why placing one under benchmarks/datasets/ would be
        silently skipped by the *existing* loader rather than crash it."""
        broken = dict(_CASE)
        del broken["queries"]
        (tmp_path / "basic_001.json").write_text(json.dumps(broken), encoding="utf-8")
        assert rcb.load_continuation_benchmarks(str(tmp_path)) == []

    def test_case_missing_ground_truth_is_skipped(self, tmp_path: Path) -> None:
        broken = dict(_CASE)
        del broken["ground_truth"]
        (tmp_path / "basic_001.json").write_text(json.dumps(broken), encoding="utf-8")
        assert rcb.load_continuation_benchmarks(str(tmp_path)) == []

    def test_malformed_json_is_skipped_not_raised(self, tmp_path: Path) -> None:
        (tmp_path / "basic_001.json").write_text("{not valid json", encoding="utf-8")
        assert rcb.load_continuation_benchmarks(str(tmp_path)) == []

    def test_skipped_list_collects_paths(self, tmp_path: Path) -> None:
        (tmp_path / "basic_001.json").write_text("{not valid json", encoding="utf-8")
        skipped: List[str] = []
        rcb.load_continuation_benchmarks(str(tmp_path), skipped=skipped)
        assert len(skipped) == 1

    def test_ordinary_run_benchmarks_style_case_is_skipped_here(self, tmp_path: Path) -> None:
        """The existing suite's shape (single 'query' string, no
        'ground_truth'/'queries') must not be picked up by this loader
        either -- the two schemas are disjoint in both directions."""
        old_style = {
            "benchmark_id": "x",
            "category": "decisions",
            "conversation": [{"speaker": "user", "text": "hi"}],
            "query": "q",
            "expected": {"answer_contains": []},
        }
        (tmp_path / "basic_001.json").write_text(json.dumps(old_style), encoding="utf-8")
        assert rcb.load_continuation_benchmarks(str(tmp_path)) == []


class TestDiscoverContinuationDatasetDirs:
    def test_returns_every_subdirectory(self, tmp_path: Path) -> None:
        (tmp_path / "resume_coding").mkdir()
        (tmp_path / "resume_debugging").mkdir()
        (tmp_path / "not_a_dir.json").write_text("{}", encoding="utf-8")

        dirs = rcb.discover_continuation_dataset_dirs(str(tmp_path))

        assert sorted(os.path.basename(d) for d in dirs) == ["resume_coding", "resume_debugging"]

    def test_missing_root_returns_empty_list(self, tmp_path: Path) -> None:
        assert rcb.discover_continuation_dataset_dirs(str(tmp_path / "nope")) == []


class TestRunContinuationCase:
    def test_calls_adapter_methods_in_expected_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rcb, "generate_continuation", _stub_generate_continuation)
        monkeypatch.setattr(rcb, "judge_continuation", _stub_judge_pass)

        adapter_holder: Dict[str, FakeContinuationAdapter] = {}

        class TrackingAdapter(FakeContinuationAdapter):
            @classmethod
            def from_config(cls, config=None):
                instance = cls()
                adapter_holder["adapter"] = instance
                return instance

        rcb.run_continuation_case(_CASE, adapter_cls=TrackingAdapter)

        calls = adapter_holder["adapter"].calls
        assert calls[0] == "delete_all"
        assert calls[1] == "add"
        assert calls.count("build_continuation_context") == len(_CASE["queries"])

    def test_case_score_is_mean_of_per_query_scores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rcb, "generate_continuation", _stub_generate_continuation)
        monkeypatch.setattr(rcb, "judge_continuation", _stub_judge_pass)

        result = rcb.run_continuation_case(_CASE, adapter_cls=FakeContinuationAdapter)

        assert result["case_score"] == 1.0
        assert result["passed"] is True
        assert result["hard_fail"] is False
        assert len(result["queries"]) == 2

    def test_hard_fail_query_is_flagged_and_fails_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rcb, "generate_continuation", _stub_generate_continuation)
        monkeypatch.setattr(rcb, "judge_continuation", _stub_judge_hard_fail)

        result = rcb.run_continuation_case(_CASE, adapter_cls=FakeContinuationAdapter)

        assert result["hard_fail"] is True
        assert result["passed"] is False

    def test_judge_exception_produces_judge_error_not_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(*args, **kwargs):
            raise RuntimeError("no API key")

        monkeypatch.setattr(rcb, "generate_continuation", _stub_generate_continuation)
        monkeypatch.setattr(rcb, "judge_continuation", _raise)

        result = rcb.run_continuation_case(_CASE, adapter_cls=FakeContinuationAdapter)

        assert result["passed"] is False
        for q in result["queries"]:
            assert q["failure_type"] == "JUDGE_ERROR"

    def test_per_query_results_carry_query_text_and_hint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(rcb, "generate_continuation", _stub_generate_continuation)
        monkeypatch.setattr(rcb, "judge_continuation", _stub_judge_pass)

        result = rcb.run_continuation_case(_CASE, adapter_cls=FakeContinuationAdapter)

        assert result["queries"][0]["query"] == "Continue implementing the project."
        assert result["queries"][0]["task_mode_hint"] == "continuation"
        assert result["queries"][1]["query"] == "What should we work on next?"


class TestContinuationContextFallback:
    """_continuation_context must work even against an adapter that predates
    BaseAdapter entirely (no build_continuation_context attribute at all),
    e.g. raw mem0.Memory."""

    def test_falls_back_to_flat_search_join_when_method_absent(self) -> None:
        class LegacyAdapter:
            def search(self, query, filters=None, **kwargs):
                return {"results": [{"memory": "a"}, {"memory": "b"}]}

        context = rcb._continuation_context(LegacyAdapter(), "q")
        assert context == "a b"

    def test_uses_build_continuation_context_when_present(self) -> None:
        class ModernAdapter:
            def build_continuation_context(self, query):
                return "structured context"

            def search(self, query, filters=None, **kwargs):
                raise AssertionError("search() should not be called when build_continuation_context exists")

        context = rcb._continuation_context(ModernAdapter(), "q")
        assert context == "structured context"


class TestExistingSuiteUnaffected:
    """The continuation dataset root is disjoint from benchmarks/datasets/,
    so the existing runner's discovery/loading is untouched by anything in
    this module."""

    def test_continuation_datasets_root_is_not_under_existing_datasets_root(self) -> None:
        assert not rcb.discover_continuation_dataset_dirs.__defaults__[0].startswith(
            "benchmarks/datasets/"
        )

    def test_existing_discover_dataset_dirs_does_not_see_continuation_root(self) -> None:
        existing_dirs = run_benchmarks.discover_dataset_dirs()
        assert all("datasets_continuation" not in d for d in existing_dirs)

    def test_existing_load_benchmarks_skips_a_real_resume_coding_case(self) -> None:
        """A real continuation-shaped case file, if it were ever pointed at
        by the *old* loader, is skipped (missing 'query'), not crashed on --
        belt-and-suspenders on top of the two dataset roots never
        overlapping in practice."""
        continuation_dir = "benchmarks/datasets_continuation/resume_coding"
        if not os.path.isdir(continuation_dir):
            pytest.skip("resume_coding dataset not present in this checkout")
        loaded = run_benchmarks.load_benchmarks(continuation_dir)
        assert loaded == []


class TestGetContinuationAdapterCls:
    """See docs/architecture/CONTINUATION_BENCHMARK_INGESTION_DESIGN.md §4/§10:
    this runner's own "haven" resolution must diverge from
    run_benchmarks.get_adapter_cls's "haven" entry, without touching that
    registry."""

    def test_haven_resolves_to_haven_continuation_adapter(self) -> None:
        from benchmarks.adapters.haven_continuation_adapter import (
            HavenContinuationAdapter,
        )

        assert rcb.get_continuation_adapter_cls("haven") is HavenContinuationAdapter

    def test_haven_full_still_delegates_to_run_benchmarks_registry(self) -> None:
        from benchmarks.adapters.haven_full_adapter import HavenFullAdapter

        assert rcb.get_continuation_adapter_cls("haven_full") is HavenFullAdapter

    def test_mem0_still_delegates_to_run_benchmarks_registry(self) -> None:
        from mem0 import Memory

        assert rcb.get_continuation_adapter_cls("mem0") is Memory

    def test_baselines_still_delegate_to_run_benchmarks_registry(self) -> None:
        from benchmarks.adapters.baselines import RecencyAdapter

        assert rcb.get_continuation_adapter_cls("recency") is RecencyAdapter


class TestGeneratedResumeCodingDataset:
    """Sanity checks on the Phase 1 pilot dataset itself, loaded through
    this module's own loader."""

    def _cases(self):
        continuation_dir = "benchmarks/datasets_continuation/resume_coding"
        if not os.path.isdir(continuation_dir):
            pytest.skip("resume_coding dataset not present in this checkout")
        return rcb.load_continuation_benchmarks(continuation_dir)

    def test_approximately_ten_cases_exist(self) -> None:
        cases = self._cases()
        assert 8 <= len(cases) <= 12

    def test_every_case_has_two_queries(self) -> None:
        for case in self._cases():
            assert len(case["queries"]) >= 1

    def test_every_supersedes_and_resolves_pointer_is_valid(self) -> None:
        for case in self._cases():
            conversation = case["conversation"]
            indices = {turn["turn_index"] for turn in conversation}
            for turn in conversation:
                if "supersedes_turn" in turn:
                    assert turn["supersedes_turn"] in indices
                if "resolves_turn" in turn:
                    assert turn["resolves_turn"] in indices

    def test_expected_rubric_fields_present(self) -> None:
        for case in self._cases():
            for key in ("must_state", "must_not_state", "forbidden_actions", "must_prioritize"):
                assert key in case["expected"]
