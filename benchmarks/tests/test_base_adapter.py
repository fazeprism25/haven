"""Tests for :mod:`benchmarks.adapters.base`.

Verifies the abstract contract itself (can't instantiate without
implementing every method) and that :class:`HavenAdapter` actually
conforms to it — the two things that matter for future adapters
(Mem0, GraphRAG, Memobase, Zep, ...) to be pluggable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from benchmarks.adapters.base import BaseAdapter
from benchmarks.adapters.haven_adapter import HavenAdapter


class TestBaseAdapterIsAbstract:
    def test_cannot_instantiate_directly(self) -> None:
        with pytest.raises(TypeError):
            BaseAdapter()  # type: ignore[abstract]

    def test_subclass_missing_methods_cannot_be_instantiated(self) -> None:
        class Incomplete(BaseAdapter):
            @classmethod
            def from_config(cls, config=None):
                return cls()

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_subclass_implementing_all_methods_is_instantiable(self) -> None:
        class Complete(BaseAdapter):
            @classmethod
            def from_config(cls, config: Optional[Dict[str, Any]] = None) -> "Complete":
                return cls()

            def delete_all(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
                return {}

            def add(
                self,
                messages: List[Dict[str, str]],
                user_id: Optional[str] = None,
                agent_id: Optional[str] = None,
                infer: bool = False,
                **kwargs: Any,
            ) -> Dict[str, Any]:
                return {"results": []}

            def search(
                self,
                query: str,
                filters: Optional[Dict[str, Any]] = None,
                **kwargs: Any,
            ) -> Dict[str, Any]:
                return {"results": []}

        adapter = Complete.from_config({})
        assert isinstance(adapter, BaseAdapter)


class TestAddConversationDefault:
    """The default add_conversation() must reproduce the runner's original
    one-add-call-per-entry loop exactly, so every adapter that doesn't
    override it keeps producing identical results."""

    def _recording_adapter(self):
        calls: List[Dict[str, Any]] = []

        class RecordingAdapter(BaseAdapter):
            @classmethod
            def from_config(cls, config=None):
                return cls()

            def delete_all(self, filters=None):
                return {}

            def add(self, messages, user_id=None, agent_id=None, infer=False, **kwargs):
                calls.append(
                    {
                        "messages": messages,
                        "user_id": user_id,
                        "agent_id": agent_id,
                        "infer": infer,
                    }
                )
                return {
                    "results": [
                        {"id": str(i), "memory": m["content"], "event": "ADD"}
                        for i, m in enumerate(messages)
                    ]
                }

            def search(self, query, filters=None, **kwargs):
                return {"results": []}

        return RecordingAdapter(), calls

    def test_calls_add_once_per_conversation_entry(self) -> None:
        adapter, calls = self._recording_adapter()
        adapter.add_conversation(
            [
                {"speaker": "user", "text": "Haven uses Claude."},
                {"speaker": "user", "text": "Haven uses Qdrant."},
            ],
            user_id="user123",
            agent_id="agent456",
        )
        assert len(calls) == 2
        assert calls[0]["messages"] == [{"role": "user", "content": "Haven uses Claude."}]
        assert calls[1]["messages"] == [{"role": "user", "content": "Haven uses Qdrant."}]

    def test_forwards_user_id_agent_id_and_infer_false(self) -> None:
        adapter, calls = self._recording_adapter()
        adapter.add_conversation(
            [{"speaker": "user", "text": "x"}], user_id="u", agent_id="a"
        )
        assert calls[0]["user_id"] == "u"
        assert calls[0]["agent_id"] == "a"
        assert calls[0]["infer"] is False

    def test_aggregates_results_across_entries(self) -> None:
        adapter, _calls = self._recording_adapter()
        result = adapter.add_conversation(
            [
                {"speaker": "user", "text": "Haven uses Claude."},
                {"speaker": "user", "text": "Haven uses Qdrant."},
            ]
        )
        assert [r["memory"] for r in result["results"]] == [
            "Haven uses Claude.",
            "Haven uses Qdrant.",
        ]

    def test_empty_conversation_yields_empty_results(self) -> None:
        adapter, calls = self._recording_adapter()
        result = adapter.add_conversation([])
        assert result == {"results": []}
        assert calls == []


class TestBuildContinuationContextDefault:
    """The default build_continuation_context() must fall back to search()
    plus the same flat join run_benchmark() already does -- the "flat
    retrieval" baseline condition
    docs/architecture/CONTINUATION_BENCHMARK_DESIGN.md §3 describes."""

    def _searching_adapter(self, memories):
        class SearchingAdapter(BaseAdapter):
            @classmethod
            def from_config(cls, config=None):
                return cls()

            def delete_all(self, filters=None):
                return {}

            def add(self, messages, user_id=None, agent_id=None, infer=False, **kwargs):
                return {"results": []}

            def search(self, query, filters=None, **kwargs):
                return {"results": [{"memory": m} for m in memories]}

        return SearchingAdapter()

    def test_falls_back_to_search_plus_join(self) -> None:
        adapter = self._searching_adapter(["Haven uses Claude.", "Haven uses Qdrant."])
        context = adapter.build_continuation_context("What does Haven use?")
        assert context == "Haven uses Claude. Haven uses Qdrant."

    def test_empty_search_results_yield_empty_context(self) -> None:
        adapter = self._searching_adapter([])
        assert adapter.build_continuation_context("anything") == ""

    def test_uses_query_argument_positionally_compatible_with_search(self) -> None:
        calls = []

        class RecordingAdapter(BaseAdapter):
            @classmethod
            def from_config(cls, config=None):
                return cls()

            def delete_all(self, filters=None):
                return {}

            def add(self, messages, user_id=None, agent_id=None, infer=False, **kwargs):
                return {"results": []}

            def search(self, query, filters=None, **kwargs):
                calls.append(query)
                return {"results": []}

        adapter = RecordingAdapter()
        adapter.build_continuation_context("Continue implementing the project.")
        assert calls == ["Continue implementing the project."]

    def test_default_implementation_is_not_abstract(self) -> None:
        """A subclass implementing only the four required methods must
        still be instantiable and expose a working
        build_continuation_context -- confirms this method is additive,
        not a new requirement on existing adapters."""
        adapter = self._searching_adapter(["x"])
        assert isinstance(adapter, BaseAdapter)
        assert adapter.build_continuation_context("q") == "x"


class TestHavenAdapterConformsToBaseAdapter:
    def test_is_a_base_adapter_subclass(self) -> None:
        assert issubclass(HavenAdapter, BaseAdapter)

    def test_from_config_returns_base_adapter_instance(self) -> None:
        adapter = HavenAdapter.from_config({})
        assert isinstance(adapter, BaseAdapter)

    def test_conforming_instance_satisfies_full_runner_call_sequence(self) -> None:
        adapter: BaseAdapter = HavenAdapter.from_config({})
        adapter.delete_all(filters={"user_id": "user123"})
        adapter.add(
            messages=[{"role": "user", "content": "Haven uses Claude."}],
            user_id="user123",
            agent_id="agent456",
            infer=False,
        )
        result = adapter.search("Haven", filters={"user_id": "user123"})
        assert "results" in result
