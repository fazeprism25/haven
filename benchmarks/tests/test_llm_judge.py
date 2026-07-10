"""Tests for :mod:`benchmarks.judges.llm_judge`.

Covers the Qwen Cloud API integration: model/base-url resolution,
missing-API-key handling, and the request/response plumbing around the
OpenAI-compatible client. The client itself is stubbed so these tests
never make a real network call.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from benchmarks.judges import llm_judge


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: List[Dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content: str) -> None:
        self.completions = _FakeCompletions(content)


class _FakeClient:
    def __init__(self, content: str = '{"passed": true, "score": 1.0, "reason": "ok", "failure_type": "NONE"}') -> None:
        self.chat = _FakeChat(content)


@pytest.fixture(autouse=True)
def _clear_qwen_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("QWEN_API_KEY", "QWEN_BASE_URL", "QWEN_JUDGE_MODEL"):
        monkeypatch.delenv(var, raising=False)


class TestResolveModel:
    def test_defaults_when_nothing_configured(self) -> None:
        assert llm_judge._resolve_model() == llm_judge.DEFAULT_QWEN_MODEL

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QWEN_JUDGE_MODEL", "qwen-max")
        assert llm_judge._resolve_model() == "qwen-max"

    def test_explicit_argument_overrides_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QWEN_JUDGE_MODEL", "qwen-max")
        assert llm_judge._resolve_model("qwen-turbo") == "qwen-turbo"


class TestGetClient:
    def test_raises_without_api_key(self) -> None:
        with pytest.raises(RuntimeError, match="QWEN_API_KEY"):
            llm_judge._get_client()

    def test_uses_default_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QWEN_API_KEY", "test-key")
        captured: Dict[str, Any] = {}

        class _RecordingOpenAI:
            def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
                captured["api_key"] = api_key
                captured["base_url"] = base_url

        monkeypatch.setattr(llm_judge, "OpenAI", _RecordingOpenAI)

        llm_judge._get_client()

        assert captured["api_key"] == "test-key"
        assert captured["base_url"] == llm_judge.DEFAULT_QWEN_BASE_URL

    def test_base_url_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QWEN_API_KEY", "test-key")
        monkeypatch.setenv("QWEN_BASE_URL", "https://example.invalid/v1")
        captured: Dict[str, Any] = {}

        class _RecordingOpenAI:
            def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
                captured["base_url"] = base_url

        monkeypatch.setattr(llm_judge, "OpenAI", _RecordingOpenAI)

        llm_judge._get_client()

        assert captured["base_url"] == "https://example.invalid/v1"


class TestJudgeAnswer:
    def test_raises_without_api_key(self) -> None:
        with pytest.raises(RuntimeError, match="QWEN_API_KEY"):
            llm_judge.judge_answer("q", {}, "a")

    def test_parses_valid_json_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QWEN_API_KEY", "test-key")
        fake_client = _FakeClient()
        monkeypatch.setattr(llm_judge, "_get_client", lambda: fake_client)

        result = llm_judge.judge_answer("What?", {"answer_contains": ["x"]}, "x")

        assert result == {
            "passed": True,
            "score": 1.0,
            "reason": "ok",
            "failure_type": "NONE",
        }

    def test_invalid_json_response_returns_judge_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QWEN_API_KEY", "test-key")
        fake_client = _FakeClient(content="not json")
        monkeypatch.setattr(llm_judge, "_get_client", lambda: fake_client)

        result = llm_judge.judge_answer("What?", {}, "answer")

        assert result["passed"] is False
        assert result["failure_type"] == "JUDGE_ERROR"
        assert result["raw_output"] == "not json"

    def test_passes_resolved_model_to_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QWEN_API_KEY", "test-key")
        fake_client = _FakeClient()
        monkeypatch.setattr(llm_judge, "_get_client", lambda: fake_client)

        llm_judge.judge_answer("q", {}, "a", model="qwen-turbo")

        assert fake_client.chat.completions.calls[0]["model"] == "qwen-turbo"

    def test_default_model_used_when_not_specified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QWEN_API_KEY", "test-key")
        fake_client = _FakeClient()
        monkeypatch.setattr(llm_judge, "_get_client", lambda: fake_client)

        llm_judge.judge_answer("q", {}, "a")

        assert fake_client.chat.completions.calls[0]["model"] == llm_judge.DEFAULT_QWEN_MODEL

    def test_uses_zero_temperature_for_reproducible_scoring(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pass/fail on borderline answers must not vary between runs of the
        same benchmark just because the judge sampled differently."""
        monkeypatch.setenv("QWEN_API_KEY", "test-key")
        fake_client = _FakeClient()
        monkeypatch.setattr(llm_judge, "_get_client", lambda: fake_client)

        llm_judge.judge_answer("q", {}, "a")

        assert fake_client.chat.completions.calls[0]["temperature"] == 0
