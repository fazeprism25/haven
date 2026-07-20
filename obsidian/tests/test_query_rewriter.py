"""Tests for :mod:`obsidian.memory_engine.query_rewriter`.

Covers the fail-open contract (missing API key, timeout, malformed JSON,
API errors), the "original unchanged" + "at most two rewrites" output
contract, and the per-instance cache keyed by normalised query. The
OpenAI-compatible client is stubbed throughout so these tests never make a
real network call.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest
from openai import APIConnectionError, APITimeoutError

from obsidian.memory_engine import query_rewriter
from obsidian.memory_engine.query_rewriter import (
    QueryRewriter,
    RewriteResult,
    RewriteSuggester,
    SuggestionResult,
)

VALID_CONTENT = json.dumps({"rewrites": ["alternate phrase one", "alternate phrase two"]})
VALID_SUGGESTION_YES = json.dumps(
    {"needs_rewrite": True, "rewrite": "explicit retrieval phrasing"}
)
VALID_SUGGESTION_NO = json.dumps({"needs_rewrite": False, "rewrite": None})


# ---------------------------------------------------------------------------
# Fakes (mirrors benchmarks/tests/test_llm_judge.py's fake client shape)
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content: Optional[str]) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: Optional[str]) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: Optional[str], *, empty_choices: bool = False) -> None:
        self.choices = [] if empty_choices else [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(
        self,
        content: Optional[str] = VALID_CONTENT,
        *,
        raise_exc: Optional[BaseException] = None,
        empty_choices: bool = False,
    ) -> None:
        self._content = content
        self._raise_exc = raise_exc
        self._empty_choices = empty_choices
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeResponse(self._content, empty_choices=self._empty_choices)


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: Optional[_FakeCompletions] = None) -> None:
        self.chat = _FakeChat(completions or _FakeCompletions())


def _install_fake_client(
    monkeypatch: pytest.MonkeyPatch,
    completions: Optional[_FakeCompletions] = None,
) -> _FakeClient:
    """Point ``_get_client`` at a fake client and ensure the API key check passes."""
    monkeypatch.setenv("QUERY_REWRITER_API_KEY", "test-key")
    fake_client = _FakeClient(completions)
    monkeypatch.setattr(query_rewriter, "_get_client", lambda: fake_client)
    return fake_client


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "QUERY_REWRITER_API_KEY",
        "QUERY_REWRITER_BASE_URL",
        "QUERY_REWRITER_MODEL",
        "QUERY_REWRITER_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# RewriteResult
# ---------------------------------------------------------------------------


class TestRewriteResult:
    def test_queries_property_prepends_original(self) -> None:
        result = RewriteResult(original="haven", rewrites=("second brain",))
        assert result.queries == ("haven", "second brain")

    def test_queries_property_with_no_rewrites(self) -> None:
        result = RewriteResult(original="haven")
        assert result.queries == ("haven",)

    def test_more_than_two_rewrites_rejected(self) -> None:
        with pytest.raises(ValueError, match="at most 2"):
            RewriteResult(original="haven", rewrites=("a", "b", "c"))

    def test_exactly_two_rewrites_accepted(self) -> None:
        result = RewriteResult(original="haven", rewrites=("a", "b"))
        assert result.rewrites == ("a", "b")


# ---------------------------------------------------------------------------
# _resolve_model / _resolve_timeout / _get_client
# ---------------------------------------------------------------------------


class TestResolveModel:
    def test_defaults_when_nothing_configured(self) -> None:
        assert query_rewriter._resolve_model() == query_rewriter.DEFAULT_MODEL

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUERY_REWRITER_MODEL", "qwen-max")
        assert query_rewriter._resolve_model() == "qwen-max"

    def test_explicit_argument_overrides_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUERY_REWRITER_MODEL", "qwen-max")
        assert query_rewriter._resolve_model("qwen-turbo") == "qwen-turbo"


class TestResolveTimeout:
    def test_defaults_when_unset(self) -> None:
        assert query_rewriter._resolve_timeout() == query_rewriter.DEFAULT_TIMEOUT_SECONDS

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUERY_REWRITER_TIMEOUT_SECONDS", "2.5")
        assert query_rewriter._resolve_timeout() == 2.5

    def test_malformed_env_var_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUERY_REWRITER_TIMEOUT_SECONDS", "not-a-number")
        assert query_rewriter._resolve_timeout() == query_rewriter.DEFAULT_TIMEOUT_SECONDS


class TestGetClient:
    def test_raises_without_api_key(self) -> None:
        with pytest.raises(RuntimeError, match="QUERY_REWRITER_API_KEY"):
            query_rewriter._get_client()

    def test_uses_default_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUERY_REWRITER_API_KEY", "test-key")
        captured: Dict[str, Any] = {}

        class _RecordingOpenAI:
            def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
                captured["api_key"] = api_key
                captured["base_url"] = base_url

        monkeypatch.setattr(query_rewriter, "OpenAI", _RecordingOpenAI)

        query_rewriter._get_client()

        assert captured["api_key"] == "test-key"
        assert captured["base_url"] == query_rewriter.DEFAULT_BASE_URL

    def test_base_url_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUERY_REWRITER_API_KEY", "test-key")
        monkeypatch.setenv("QUERY_REWRITER_BASE_URL", "https://example.invalid/v1")
        captured: Dict[str, Any] = {}

        class _RecordingOpenAI:
            def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
                captured["base_url"] = base_url

        monkeypatch.setattr(query_rewriter, "OpenAI", _RecordingOpenAI)

        query_rewriter._get_client()

        assert captured["base_url"] == "https://example.invalid/v1"


# ---------------------------------------------------------------------------
# _parse_rewrites
# ---------------------------------------------------------------------------


class TestParseRewrites:
    def test_parses_valid_two_rewrites(self) -> None:
        text = json.dumps({"rewrites": ["phrase one", "phrase two"]})
        assert query_rewriter._parse_rewrites(text, "original") == (
            "phrase one",
            "phrase two",
        )

    def test_truncates_more_than_two(self) -> None:
        text = json.dumps({"rewrites": ["a", "b", "c", "d"]})
        assert query_rewriter._parse_rewrites(text, "original") == ("a", "b")

    def test_drops_empty_and_whitespace_only_entries(self) -> None:
        text = json.dumps({"rewrites": ["", "   ", "real phrase"]})
        assert query_rewriter._parse_rewrites(text, "original") == ("real phrase",)

    def test_strips_surrounding_whitespace(self) -> None:
        text = json.dumps({"rewrites": ["  padded phrase  "]})
        assert query_rewriter._parse_rewrites(text, "original") == ("padded phrase",)

    def test_drops_duplicate_of_original_case_insensitive(self) -> None:
        text = json.dumps({"rewrites": ["Haven", "second brain"]})
        assert query_rewriter._parse_rewrites(text, "haven") == ("second brain",)

    def test_drops_internal_duplicate_rewrites(self) -> None:
        text = json.dumps({"rewrites": ["same phrase", "Same Phrase", "third phrase"]})
        assert query_rewriter._parse_rewrites(text, "original") == (
            "same phrase",
            "third phrase",
        )

    def test_empty_rewrites_list_yields_empty_tuple(self) -> None:
        text = json.dumps({"rewrites": []})
        assert query_rewriter._parse_rewrites(text, "original") == ()

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            query_rewriter._parse_rewrites("not json", "original")

    def test_missing_rewrites_key_raises(self) -> None:
        with pytest.raises(KeyError):
            query_rewriter._parse_rewrites(json.dumps({}), "original")

    def test_non_list_rewrites_raises(self) -> None:
        with pytest.raises(TypeError):
            query_rewriter._parse_rewrites(json.dumps({"rewrites": "not a list"}), "original")

    def test_non_string_entry_raises(self) -> None:
        with pytest.raises(TypeError):
            query_rewriter._parse_rewrites(json.dumps({"rewrites": [1, 2]}), "original")


# ---------------------------------------------------------------------------
# QueryRewriter.rewrite — success path
# ---------------------------------------------------------------------------


class TestRewriteSuccess:
    def test_returns_original_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_client(monkeypatch)
        rewriter = QueryRewriter()

        result = rewriter.rewrite("What database did I pick?")

        assert result.original == "What database did I pick?"

    def test_returns_up_to_two_rewrites(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_client(monkeypatch)
        rewriter = QueryRewriter()

        result = rewriter.rewrite("What database did I pick?")

        assert result.rewrites == ("alternate phrase one", "alternate phrase two")

    def test_queries_property_includes_original_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_client(monkeypatch)
        rewriter = QueryRewriter()

        result = rewriter.rewrite("query text")

        assert result.queries[0] == "query text"
        assert len(result.queries) == 3

    def test_passes_resolved_model_to_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions()
        fake_client = _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter(model="qwen-turbo")

        rewriter.rewrite("query")

        assert fake_client.chat.completions.calls[0]["model"] == "qwen-turbo"

    def test_default_model_used_when_not_specified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions()
        fake_client = _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        rewriter.rewrite("query")

        assert fake_client.chat.completions.calls[0]["model"] == query_rewriter.DEFAULT_MODEL

    def test_passes_timeout_to_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QUERY_REWRITER_TIMEOUT_SECONDS", "3.0")
        completions = _FakeCompletions()
        fake_client = _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        rewriter.rewrite("query")

        assert fake_client.chat.completions.calls[0]["timeout"] == 3.0

    def test_query_sent_as_user_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions()
        fake_client = _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        rewriter.rewrite("find my decisions about databases")

        messages = fake_client.chat.completions.calls[0]["messages"]
        assert messages[-1] == {
            "role": "user",
            "content": "find my decisions about databases",
        }


# ---------------------------------------------------------------------------
# QueryRewriter.rewrite — fail-open paths
# ---------------------------------------------------------------------------


class TestRewriteFailOpen:
    def test_missing_api_key_returns_only_original(self) -> None:
        rewriter = QueryRewriter()

        result = rewriter.rewrite("some query")

        assert result == RewriteResult(original="some query", rewrites=())

    def test_timeout_returns_only_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(
            raise_exc=APITimeoutError(request=None)  # type: ignore[arg-type]
        )
        _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        result = rewriter.rewrite("some query")

        assert result == RewriteResult(original="some query", rewrites=())

    def test_connection_error_returns_only_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(
            raise_exc=APIConnectionError(request=None)  # type: ignore[arg-type]
        )
        _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        result = rewriter.rewrite("some query")

        assert result == RewriteResult(original="some query", rewrites=())

    def test_generic_api_error_returns_only_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(raise_exc=RuntimeError("boom"))
        _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        result = rewriter.rewrite("some query")

        assert result == RewriteResult(original="some query", rewrites=())

    def test_malformed_json_returns_only_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(content="this is not json")
        _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        result = rewriter.rewrite("some query")

        assert result == RewriteResult(original="some query", rewrites=())

    def test_unexpected_json_shape_returns_only_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(content=json.dumps({"unexpected": "shape"}))
        _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        result = rewriter.rewrite("some query")

        assert result == RewriteResult(original="some query", rewrites=())

    def test_empty_choices_returns_only_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(empty_choices=True)
        _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        result = rewriter.rewrite("some query")

        assert result == RewriteResult(original="some query", rewrites=())

    def test_none_content_returns_only_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(content=None)
        _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        result = rewriter.rewrite("some query")

        assert result == RewriteResult(original="some query", rewrites=())

    def test_never_raises_regardless_of_failure_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for exc in (
            RuntimeError("network down"),
            ValueError("bad value"),
            KeyError("missing"),
        ):
            completions = _FakeCompletions(raise_exc=exc)
            _install_fake_client(monkeypatch, completions)
            rewriter = QueryRewriter()
            # Must not raise.
            rewriter.rewrite("some query")


# ---------------------------------------------------------------------------
# QueryRewriter.rewrite — empty/whitespace query short-circuit
# ---------------------------------------------------------------------------


class TestRewriteEmptyQuery:
    def test_empty_string_returns_only_original_without_api_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        completions = _FakeCompletions()
        fake_client = _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        result = rewriter.rewrite("")

        assert result == RewriteResult(original="", rewrites=())
        assert fake_client.chat.completions.calls == []

    def test_whitespace_only_returns_only_original_without_api_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        completions = _FakeCompletions()
        fake_client = _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        result = rewriter.rewrite("   ")

        assert result == RewriteResult(original="   ", rewrites=())
        assert fake_client.chat.completions.calls == []


# ---------------------------------------------------------------------------
# QueryRewriter.rewrite — caching / determinism
# ---------------------------------------------------------------------------


class TestRewriteCaching:
    def test_repeated_identical_query_hits_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions()
        fake_client = _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        first = rewriter.rewrite("repeated query")
        second = rewriter.rewrite("repeated query")

        assert first == second
        assert len(fake_client.chat.completions.calls) == 1

    def test_cache_keyed_by_normalized_query_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions()
        fake_client = _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        rewriter.rewrite("Repeated Query")
        rewriter.rewrite("repeated query")

        assert len(fake_client.chat.completions.calls) == 1

    def test_cache_keyed_by_normalized_query_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions()
        fake_client = _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        rewriter.rewrite("repeated query")
        rewriter.rewrite("  repeated query  ")

        assert len(fake_client.chat.completions.calls) == 1

    def test_cache_hit_preserves_this_calls_exact_original(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        completions = _FakeCompletions()
        _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        rewriter.rewrite("Repeated Query")
        second = rewriter.rewrite("REPEATED QUERY")

        # The cache stores only the rewrites, not the whole result, so a
        # cache hit must still echo back *this* call's exact input string.
        assert second.original == "REPEATED QUERY"
        assert second.rewrites == ("alternate phrase one", "alternate phrase two")

    def test_different_queries_each_call_the_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions()
        fake_client = _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        rewriter.rewrite("first query")
        rewriter.rewrite("second query")

        assert len(fake_client.chat.completions.calls) == 2

    def test_failed_result_is_also_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(raise_exc=RuntimeError("boom"))
        fake_client = _install_fake_client(monkeypatch, completions)
        rewriter = QueryRewriter()

        rewriter.rewrite("repeated query")
        rewriter.rewrite("repeated query")

        assert len(fake_client.chat.completions.calls) == 1

    def test_separate_instances_do_not_share_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions()
        fake_client = _install_fake_client(monkeypatch, completions)

        QueryRewriter().rewrite("shared query text")
        QueryRewriter().rewrite("shared query text")

        assert len(fake_client.chat.completions.calls) == 2

    def test_deterministic_output_across_repeated_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_client(monkeypatch)
        rewriter = QueryRewriter()

        results = [rewriter.rewrite("stable query") for _ in range(5)]

        assert all(r == results[0] for r in results)


# ---------------------------------------------------------------------------
# SuggestionResult
# ---------------------------------------------------------------------------


class TestSuggestionResult:
    def test_changed_true_when_suggestion_present(self) -> None:
        result = SuggestionResult(original="continue", suggestion="explicit phrasing")
        assert result.changed is True
        assert result.rewritten == "explicit phrasing"

    def test_changed_false_when_no_suggestion(self) -> None:
        result = SuggestionResult(original="What is Python?", suggestion=None)
        assert result.changed is False
        assert result.rewritten == "What is Python?"


# ---------------------------------------------------------------------------
# _parse_suggestion
# ---------------------------------------------------------------------------


class TestParseSuggestion:
    def test_parses_yes_with_rewrite(self) -> None:
        assert (
            query_rewriter._parse_suggestion(VALID_SUGGESTION_YES, "original")
            == "explicit retrieval phrasing"
        )

    def test_parses_no_as_none(self) -> None:
        assert query_rewriter._parse_suggestion(VALID_SUGGESTION_NO, "original") is None

    def test_no_ignores_present_rewrite_field(self) -> None:
        text = json.dumps({"needs_rewrite": False, "rewrite": "ignored anyway"})
        assert query_rewriter._parse_suggestion(text, "original") is None

    def test_strips_surrounding_whitespace(self) -> None:
        text = json.dumps({"needs_rewrite": True, "rewrite": "  padded phrase  "})
        assert query_rewriter._parse_suggestion(text, "original") == "padded phrase"

    def test_blank_rewrite_treated_as_no_suggestion(self) -> None:
        text = json.dumps({"needs_rewrite": True, "rewrite": "   "})
        assert query_rewriter._parse_suggestion(text, "original") is None

    def test_rewrite_identical_to_original_treated_as_no_suggestion(self) -> None:
        text = json.dumps({"needs_rewrite": True, "rewrite": "Original"})
        assert query_rewriter._parse_suggestion(text, "original") is None

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            query_rewriter._parse_suggestion("not json", "original")

    def test_missing_needs_rewrite_key_raises(self) -> None:
        with pytest.raises(KeyError):
            query_rewriter._parse_suggestion(json.dumps({}), "original")

    def test_non_bool_needs_rewrite_raises(self) -> None:
        text = json.dumps({"needs_rewrite": "yes", "rewrite": "phrase"})
        with pytest.raises(TypeError):
            query_rewriter._parse_suggestion(text, "original")

    def test_non_string_rewrite_when_needed_raises(self) -> None:
        text = json.dumps({"needs_rewrite": True, "rewrite": 5})
        with pytest.raises(TypeError):
            query_rewriter._parse_suggestion(text, "original")


# ---------------------------------------------------------------------------
# RewriteSuggester.suggest — success path
# ---------------------------------------------------------------------------


class TestSuggestSuccess:
    def test_returns_original_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_client(monkeypatch, _FakeCompletions(VALID_SUGGESTION_YES))
        suggester = RewriteSuggester()

        result = suggester.suggest("continue working on it")

        assert result.original == "continue working on it"

    def test_returns_suggestion_when_needed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_client(monkeypatch, _FakeCompletions(VALID_SUGGESTION_YES))
        suggester = RewriteSuggester()

        result = suggester.suggest("continue working on it")

        assert result.changed is True
        assert result.suggestion == "explicit retrieval phrasing"

    def test_returns_no_suggestion_when_not_needed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_fake_client(monkeypatch, _FakeCompletions(VALID_SUGGESTION_NO))
        suggester = RewriteSuggester()

        result = suggester.suggest("What is Python?")

        assert result.changed is False
        assert result.rewritten == "What is Python?"

    def test_passes_resolved_model_to_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(VALID_SUGGESTION_YES)
        fake_client = _install_fake_client(monkeypatch, completions)
        suggester = RewriteSuggester(model="qwen-turbo")

        suggester.suggest("query")

        assert fake_client.chat.completions.calls[0]["model"] == "qwen-turbo"

    def test_query_sent_as_user_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(VALID_SUGGESTION_YES)
        fake_client = _install_fake_client(monkeypatch, completions)
        suggester = RewriteSuggester()

        suggester.suggest("catch me up on the project")

        messages = fake_client.chat.completions.calls[0]["messages"]
        assert messages[-1] == {
            "role": "user",
            "content": "catch me up on the project",
        }
        assert messages[0]["content"] == query_rewriter.SUGGESTION_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# RewriteSuggester.suggest — fail-open paths
# ---------------------------------------------------------------------------


class TestSuggestFailOpen:
    def test_missing_api_key_returns_no_suggestion(self) -> None:
        suggester = RewriteSuggester()

        result = suggester.suggest("some query")

        assert result == SuggestionResult(original="some query", suggestion=None)

    def test_timeout_returns_no_suggestion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(
            raise_exc=APITimeoutError(request=None)  # type: ignore[arg-type]
        )
        _install_fake_client(monkeypatch, completions)
        suggester = RewriteSuggester()

        result = suggester.suggest("some query")

        assert result == SuggestionResult(original="some query", suggestion=None)

    def test_generic_api_error_returns_no_suggestion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(raise_exc=RuntimeError("boom"))
        _install_fake_client(monkeypatch, completions)
        suggester = RewriteSuggester()

        result = suggester.suggest("some query")

        assert result == SuggestionResult(original="some query", suggestion=None)

    def test_malformed_json_returns_no_suggestion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(content="this is not json")
        _install_fake_client(monkeypatch, completions)
        suggester = RewriteSuggester()

        result = suggester.suggest("some query")

        assert result == SuggestionResult(original="some query", suggestion=None)

    def test_empty_choices_returns_no_suggestion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(empty_choices=True)
        _install_fake_client(monkeypatch, completions)
        suggester = RewriteSuggester()

        result = suggester.suggest("some query")

        assert result == SuggestionResult(original="some query", suggestion=None)

    def test_never_raises_regardless_of_failure_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for exc in (
            RuntimeError("network down"),
            ValueError("bad value"),
            KeyError("missing"),
        ):
            completions = _FakeCompletions(raise_exc=exc)
            _install_fake_client(monkeypatch, completions)
            suggester = RewriteSuggester()
            # Must not raise.
            suggester.suggest("some query")


# ---------------------------------------------------------------------------
# RewriteSuggester.suggest — empty/whitespace query short-circuit
# ---------------------------------------------------------------------------


class TestSuggestEmptyQuery:
    def test_empty_string_returns_no_suggestion_without_api_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        completions = _FakeCompletions(VALID_SUGGESTION_YES)
        fake_client = _install_fake_client(monkeypatch, completions)
        suggester = RewriteSuggester()

        result = suggester.suggest("")

        assert result == SuggestionResult(original="", suggestion=None)
        assert fake_client.chat.completions.calls == []


# ---------------------------------------------------------------------------
# RewriteSuggester.suggest — caching / determinism
# ---------------------------------------------------------------------------


class TestSuggestCaching:
    def test_repeated_identical_query_hits_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(VALID_SUGGESTION_YES)
        fake_client = _install_fake_client(monkeypatch, completions)
        suggester = RewriteSuggester()

        first = suggester.suggest("repeated query")
        second = suggester.suggest("repeated query")

        assert first == second
        assert len(fake_client.chat.completions.calls) == 1

    def test_different_queries_each_call_the_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(VALID_SUGGESTION_YES)
        fake_client = _install_fake_client(monkeypatch, completions)
        suggester = RewriteSuggester()

        suggester.suggest("first query")
        suggester.suggest("second query")

        assert len(fake_client.chat.completions.calls) == 2

    def test_separate_instances_do_not_share_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        completions = _FakeCompletions(VALID_SUGGESTION_YES)
        fake_client = _install_fake_client(monkeypatch, completions)

        RewriteSuggester().suggest("shared query text")
        RewriteSuggester().suggest("shared query text")

        assert len(fake_client.chat.completions.calls) == 2
