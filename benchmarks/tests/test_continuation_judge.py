"""Tests for :mod:`benchmarks.judges.continuation_judge`.

Covers the deterministic §6 weighting/hard-fail-ceiling formula in
isolation (:class:`TestWeightedScore`) and the Qwen Cloud request/response
plumbing (:class:`TestJudgeContinuation`), using the same client-stubbing
pattern ``benchmarks/tests/test_llm_judge.py`` already uses so no test here
ever makes a real network call.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from benchmarks.judges import continuation_judge as cj


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
    def __init__(self, content: str) -> None:
        self.chat = _FakeChat(content)


_CLEAN_JUDGE_JSON = json.dumps(
    {
        "must_state_score": 1.0,
        "must_not_state_violations": [],
        "forbidden_action_violations": [],
        "prioritization_correct": True,
        "coherence_score": 1.0,
        "reason": "Covers all required facts, avoids stale content, prioritizes correctly.",
        "failure_type": "NONE",
    }
)


class TestWeightedScore:
    """§6's table: must_state 40%, violations 35% (hard-fail gated to a 0.2
    ceiling), must_prioritize 15%, coherence 10%."""

    def test_perfect_result_scores_one(self) -> None:
        result = {
            "must_state_score": 1.0,
            "must_not_state_violations": [],
            "forbidden_action_violations": [],
            "prioritization_correct": True,
            "coherence_score": 1.0,
        }
        assert cj._weighted_score(result) == 1.0

    def test_all_other_axes_zero_still_earns_violation_free_credit(self) -> None:
        """No must_not_state/forbidden_action violations earns the full 35%
        on that axis even when every other axis scores zero -- "no
        violations" is itself a real, positive signal, not a null one."""
        result = {
            "must_state_score": 0.0,
            "must_not_state_violations": [],
            "forbidden_action_violations": [],
            "prioritization_correct": False,
            "coherence_score": 0.0,
        }
        assert cj._weighted_score(result) == cj.VIOLATIONS_WEIGHT

    def test_must_not_state_violation_caps_score_at_ceiling(self) -> None:
        """A response that scores perfectly on must_state/prioritization/
        coherence but states one stale fact must still be capped -- §6:
        'a continuation that recommends a rejected approach is actively
        harmful, not merely incomplete, and must not be averaged away by
        otherwise-good recall.'"""
        result = {
            "must_state_score": 1.0,
            "must_not_state_violations": ["revived the rejected approach"],
            "forbidden_action_violations": [],
            "prioritization_correct": True,
            "coherence_score": 1.0,
        }
        assert cj._weighted_score(result) <= cj.HARD_FAIL_CEILING

    def test_forbidden_action_violation_also_caps_score(self) -> None:
        result = {
            "must_state_score": 1.0,
            "must_not_state_violations": [],
            "forbidden_action_violations": ["recommended the forbidden action"],
            "prioritization_correct": True,
            "coherence_score": 1.0,
        }
        assert cj._weighted_score(result) <= cj.HARD_FAIL_CEILING

    def test_missing_fields_default_to_zero_not_crash(self) -> None:
        """An empty dict has no violations lists either, so it still earns
        the violations-axis credit (same reasoning as the test above) --
        this test's job is only to confirm no KeyError/TypeError, not to
        assert a specific score."""
        assert cj._weighted_score({}) == cj.VIOLATIONS_WEIGHT

    def test_mid_scores_weighted_correctly(self) -> None:
        result = {
            "must_state_score": 0.5,
            "must_not_state_violations": [],
            "forbidden_action_violations": [],
            "prioritization_correct": False,
            "coherence_score": 0.5,
        }
        expected = 0.40 * 0.5 + 0.35 * 1.0 + 0.15 * 0.0 + 0.10 * 0.5
        assert cj._weighted_score(result) == round(expected, 4)


@pytest.fixture(autouse=True)
def _clear_qwen_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("QWEN_API_KEY", "QWEN_BASE_URL", "QWEN_JUDGE_MODEL"):
        monkeypatch.delenv(var, raising=False)


class TestJudgeContinuation:
    def test_raises_without_api_key(self) -> None:
        with pytest.raises(RuntimeError, match="QWEN_API_KEY"):
            cj.judge_continuation("q", {}, {}, "response")

    def test_parses_valid_json_and_computes_score(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QWEN_API_KEY", "test-key")
        fake_client = _FakeClient(_CLEAN_JUDGE_JSON)
        monkeypatch.setattr(cj, "_get_client", lambda: fake_client)

        result = cj.judge_continuation("q", {"current_objective": "x"}, {}, "response")

        assert result["score"] == 1.0
        assert result["passed"] is True
        assert result["failure_type"] == "NONE"

    def test_hard_fail_violation_marks_not_passed_regardless_of_score(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QWEN_API_KEY", "test-key")
        content = json.dumps(
            {
                "must_state_score": 1.0,
                "must_not_state_violations": ["stale blocker presented as open"],
                "forbidden_action_violations": [],
                "prioritization_correct": True,
                "coherence_score": 1.0,
                "reason": "Stated a resolved blocker as still open.",
                "failure_type": "STALE_STATE_SURFACED",
            }
        )
        fake_client = _FakeClient(content)
        monkeypatch.setattr(cj, "_get_client", lambda: fake_client)

        result = cj.judge_continuation("q", {}, {}, "response")

        assert result["passed"] is False
        assert result["score"] <= cj.HARD_FAIL_CEILING

    def test_invalid_json_response_returns_judge_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QWEN_API_KEY", "test-key")
        fake_client = _FakeClient("not json")
        monkeypatch.setattr(cj, "_get_client", lambda: fake_client)

        result = cj.judge_continuation("q", {}, {}, "response")

        assert result["passed"] is False
        assert result["score"] == 0.0
        assert result["failure_type"] == "JUDGE_ERROR"
        assert result["raw_output"] == "not json"

    def test_uses_zero_temperature(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QWEN_API_KEY", "test-key")
        fake_client = _FakeClient(_CLEAN_JUDGE_JSON)
        monkeypatch.setattr(cj, "_get_client", lambda: fake_client)

        cj.judge_continuation("q", {}, {}, "response")

        assert fake_client.chat.completions.calls[0]["temperature"] == 0

    def test_low_score_without_violations_fails_pass_threshold(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QWEN_API_KEY", "test-key")
        content = json.dumps(
            {
                "must_state_score": 0.1,
                "must_not_state_violations": [],
                "forbidden_action_violations": [],
                "prioritization_correct": False,
                "coherence_score": 0.1,
                "reason": "Mostly incomplete.",
                "failure_type": "INCOMPLETE",
            }
        )
        fake_client = _FakeClient(content)
        monkeypatch.setattr(cj, "_get_client", lambda: fake_client)

        result = cj.judge_continuation("q", {}, {}, "response")

        assert result["score"] < cj.PASS_THRESHOLD
        assert result["passed"] is False
