"""Tests for the adapter-selection refactor in
:mod:`benchmarks.runners.run_benchmarks`.

Confirms the runner can drive an arbitrary
:class:`~benchmarks.adapters.base.BaseAdapter`-shaped class (not just
``mem0.Memory``), and that its default behavior — no ``adapter_cls``
argument supplied — is unchanged from before the refactor. Uses a fake
in-memory adapter and a stubbed judge so these tests need neither a
running Qdrant/Ollama nor real Haven vault I/O.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from mem0 import Memory

from benchmarks.adapters.base import BaseAdapter
from benchmarks.adapters.haven_adapter import HavenAdapter
from benchmarks.runners import run_benchmarks


class FakeAdapter(BaseAdapter):
    """Minimal in-memory adapter used only to exercise the runner's call sequence."""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self._store: List[str] = []

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None) -> "FakeAdapter":
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

    def search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        self.calls.append("search")
        return {"results": [{"memory": text} for text in self._store]}


_BENCHMARK = {
    "benchmark_id": "fake_001",
    "category": "unit_test",
    "conversation": [
        {"speaker": "user", "text": "Haven uses Claude."},
    ],
    "query": "What does Haven use?",
    "expected": {"answer_contains": ["Claude"], "must_not_contain": []},
}


def _stub_judge_pass(query, expected, answer):
    return {"passed": True, "score": 1.0, "reason": "stub", "failure_type": "NONE"}


class TestGetAdapterCls:
    def test_mem0_resolves_to_mem0_memory(self) -> None:
        assert run_benchmarks.get_adapter_cls("mem0") is Memory

    def test_haven_resolves_to_haven_adapter(self) -> None:
        assert run_benchmarks.get_adapter_cls("haven") is HavenAdapter

    def test_unknown_adapter_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            run_benchmarks.get_adapter_cls("does_not_exist")


class TestRunBenchmarkAdapterParameter:
    def test_default_adapter_cls_is_mem0_memory(self) -> None:
        import inspect

        signature = inspect.signature(run_benchmarks.run_benchmark)
        assert signature.parameters["adapter_cls"].default is Memory

    def test_run_benchmark_drives_arbitrary_adapter_through_full_call_sequence(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(run_benchmarks, "judge_answer", _stub_judge_pass)

        result = run_benchmarks.run_benchmark(_BENCHMARK, adapter_cls=FakeAdapter)

        assert result["passed"] is True
        assert result["answer_score"] == 1.0
        assert result["answer"] == "Haven uses Claude."
        assert result["benchmark_id"] == "fake_001"

    def test_run_benchmark_calls_adapter_methods_in_runner_spec_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(run_benchmarks, "judge_answer", _stub_judge_pass)

        captured: Dict[str, FakeAdapter] = {}

        class TrackingFakeAdapter(FakeAdapter):
            @classmethod
            def from_config(cls, config=None):
                instance = cls()
                captured["adapter"] = instance
                return instance

        run_benchmarks.run_benchmark(_BENCHMARK, adapter_cls=TrackingFakeAdapter)

        assert captured["adapter"].calls == ["delete_all", "add", "search"]


class TestMainAdapterSelection:
    def test_main_rejects_unknown_adapter_before_running_anything(self) -> None:
        with pytest.raises(ValueError):
            run_benchmarks.main(adapter_name="does_not_exist")


class TestQueryRewriterWiring:
    """Covers the optional ``query_rewriter`` construction path added to the
    runner: ``run_benchmark``/``main`` can construct a
    :class:`~obsidian.memory_engine.query_rewriter.QueryRewriter` and hand
    it to an adapter's constructor instead of the ``from_config(config)``
    path, but only when explicitly requested — disabled by default.
    """

    def test_run_benchmark_query_rewriter_defaults_to_none(self) -> None:
        import inspect

        signature = inspect.signature(run_benchmarks.run_benchmark)
        assert signature.parameters["query_rewriter"].default is None

    def test_main_enable_query_rewriter_defaults_to_false(self) -> None:
        import inspect

        signature = inspect.signature(run_benchmarks.main)
        assert signature.parameters["enable_query_rewriter"].default is False

    def test_default_behavior_still_constructs_via_from_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ``query_rewriter`` argument at all -> unchanged construction path."""
        monkeypatch.setattr(run_benchmarks, "judge_answer", _stub_judge_pass)

        captured: Dict[str, str] = {}

        class TrackingFakeAdapter(FakeAdapter):
            @classmethod
            def from_config(cls, config=None):
                captured["constructed_via"] = "from_config"
                return cls()

        run_benchmarks.run_benchmark(_BENCHMARK, adapter_cls=TrackingFakeAdapter)

        assert captured["constructed_via"] == "from_config"

    def test_explicit_query_rewriter_bypasses_from_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Enabled path: adapter_cls is constructed directly with
        ``query_rewriter=...`` instead of through ``from_config``."""
        monkeypatch.setattr(run_benchmarks, "judge_answer", _stub_judge_pass)

        sentinel = object()
        captured: Dict[str, Any] = {}

        class RewriterAwareFakeAdapter(FakeAdapter):
            def __init__(self, query_rewriter: Any = None) -> None:
                super().__init__()
                captured["query_rewriter"] = query_rewriter

            @classmethod
            def from_config(cls, config=None):
                captured["constructed_via"] = "from_config"
                return cls()

        run_benchmarks.run_benchmark(
            _BENCHMARK,
            adapter_cls=RewriterAwareFakeAdapter,
            query_rewriter=sentinel,
        )

        assert captured["query_rewriter"] is sentinel
        assert "constructed_via" not in captured

    def test_main_rejects_query_rewriter_for_non_haven_adapter(self) -> None:
        with pytest.raises(ValueError):
            run_benchmarks.main(adapter_name="mem0", enable_query_rewriter=True)

    def test_main_passes_none_to_run_benchmark_when_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: Dict[str, Any] = {}

        def fake_run_benchmark(benchmark, adapter_cls=Memory, query_rewriter=None):
            captured["query_rewriter"] = query_rewriter
            return {"benchmark_id": benchmark["benchmark_id"], "passed": True}

        monkeypatch.setattr(run_benchmarks, "run_benchmark", fake_run_benchmark)
        monkeypatch.setattr(
            run_benchmarks,
            "load_benchmarks",
            lambda dataset_dir, skipped=None: [_BENCHMARK] if "decisions" in dataset_dir else [],
        )
        monkeypatch.setattr(run_benchmarks, "save_results", lambda data, path: None)

        run_benchmarks.main(adapter_name="haven")

        assert captured["query_rewriter"] is None

    def test_main_constructs_query_rewriter_for_haven_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from obsidian.memory_engine.query_rewriter import QueryRewriter

        captured: Dict[str, Any] = {}

        def fake_run_benchmark(benchmark, adapter_cls=Memory, query_rewriter=None):
            captured["query_rewriter"] = query_rewriter
            return {"benchmark_id": benchmark["benchmark_id"], "passed": True}

        monkeypatch.setattr(run_benchmarks, "run_benchmark", fake_run_benchmark)
        monkeypatch.setattr(
            run_benchmarks,
            "load_benchmarks",
            lambda dataset_dir, skipped=None: [_BENCHMARK] if "decisions" in dataset_dir else [],
        )
        monkeypatch.setattr(run_benchmarks, "save_results", lambda data, path: None)

        run_benchmarks.main(adapter_name="haven", enable_query_rewriter=True)

        assert isinstance(captured["query_rewriter"], QueryRewriter)

    def test_query_rewriter_instance_reaches_memory_engine_through_haven_adapter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: a QueryRewriter passed into run_benchmark() for the
        haven adapter reaches HavenAdapter's constructor and, from there,
        MemoryEngine — the same guarantee
        benchmarks/tests/test_haven_adapter.py proves for HavenAdapter in
        isolation, exercised here through the actual runner call path.
        """
        from obsidian.memory_engine.query_rewriter import QueryRewriter

        monkeypatch.setattr(run_benchmarks, "judge_answer", _stub_judge_pass)
        rewriter = QueryRewriter()

        with patch("benchmarks.adapters.haven_adapter.MemoryEngine") as mock_engine_cls:
            mock_engine_cls.return_value.query_with_trace.return_value = (
                "Haven uses Claude.",
                SimpleNamespace(candidates=()),
            )

            result = run_benchmarks.run_benchmark(
                _BENCHMARK, adapter_cls=HavenAdapter, query_rewriter=rewriter
            )

        mock_engine_cls.assert_called_once()
        _, kwargs = mock_engine_cls.call_args
        assert kwargs["query_rewriter"] is rewriter
        assert result["passed"] is True


_VALID_BENCHMARK = {
    "benchmark_id": "x",
    "category": "unit_test",
    "conversation": [{"speaker": "user", "text": "hi"}],
    "query": "q",
    "expected": {"answer_contains": []},
}


class TestLoadBenchmarksSkipsInvalidFiles:
    """Some dataset directories (see benchmarks/README.md: "Dataset creation
    in progress") contain empty placeholder ``.json`` files. A single such
    file used to crash ``load_benchmarks`` with an unhandled
    ``JSONDecodeError``, taking down the entire run over one incomplete
    dataset entry.
    """

    def test_valid_files_are_loaded(self, tmp_path: Path) -> None:
        (tmp_path / "basic_001.json").write_text(
            json.dumps(_VALID_BENCHMARK), encoding="utf-8"
        )

        loaded = run_benchmarks.load_benchmarks(str(tmp_path))

        assert loaded == [_VALID_BENCHMARK]

    def test_empty_file_is_skipped_not_raised(self, tmp_path: Path) -> None:
        (tmp_path / "basic_001.json").write_text(
            json.dumps(_VALID_BENCHMARK), encoding="utf-8"
        )
        (tmp_path / "basic_002.json").write_text("", encoding="utf-8")

        loaded = run_benchmarks.load_benchmarks(str(tmp_path))

        assert loaded == [_VALID_BENCHMARK]

    def test_malformed_json_is_skipped_not_raised(self, tmp_path: Path) -> None:
        (tmp_path / "basic_001.json").write_text("{not valid json", encoding="utf-8")

        loaded = run_benchmarks.load_benchmarks(str(tmp_path))

        assert loaded == []

    def test_missing_required_field_is_skipped_not_raised(self, tmp_path: Path) -> None:
        """A file that parses as JSON but is missing a field the runner
        indexes unconditionally (e.g. ``category``, read via
        ``benchmark["category"]`` in ``run_benchmark``) must be skipped
        the same way malformed JSON is, not crash the run with a
        ``KeyError`` partway through.
        """
        (tmp_path / "basic_001.json").write_text(
            json.dumps(_VALID_BENCHMARK), encoding="utf-8"
        )
        (tmp_path / "basic_002.json").write_text(
            json.dumps({"benchmark_id": "no_category"}), encoding="utf-8"
        )

        loaded = run_benchmarks.load_benchmarks(str(tmp_path))

        assert loaded == [_VALID_BENCHMARK]

    def test_skipped_list_collects_paths_of_skipped_files(self, tmp_path: Path) -> None:
        (tmp_path / "basic_001.json").write_text("{not valid json", encoding="utf-8")
        (tmp_path / "basic_002.json").write_text(
            json.dumps({"benchmark_id": "no_category"}), encoding="utf-8"
        )

        skipped: List[str] = []
        run_benchmarks.load_benchmarks(str(tmp_path), skipped=skipped)

        assert len(skipped) == 2

    def test_skipped_defaults_to_none_and_is_optional(self, tmp_path: Path) -> None:
        (tmp_path / "basic_001.json").write_text(
            json.dumps(_VALID_BENCHMARK), encoding="utf-8"
        )

        loaded = run_benchmarks.load_benchmarks(str(tmp_path))

        assert loaded == [_VALID_BENCHMARK]


class TestDiscoverDatasetDirs:
    def test_returns_every_subdirectory(self, tmp_path: Path) -> None:
        (tmp_path / "decisions").mkdir()
        (tmp_path / "beliefs").mkdir()
        (tmp_path / "not_a_dir.json").write_text("{}", encoding="utf-8")

        dirs = run_benchmarks.discover_dataset_dirs(str(tmp_path))

        assert sorted(os.path.basename(d) for d in dirs) == ["beliefs", "decisions"]

    def test_empty_root_returns_empty_list(self, tmp_path: Path) -> None:
        assert run_benchmarks.discover_dataset_dirs(str(tmp_path)) == []
