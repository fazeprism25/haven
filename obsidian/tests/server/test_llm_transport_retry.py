"""Regression tests for the shared LLM transport-retry helper.

Covers two layers:

* **Unit** -- :func:`generate_with_transport_retry` itself: it retries
  exactly once on a transient transport failure (network error, timeout),
  never retries a ``ValueError`` (that's each stage's own parse/validation
  repair retry to handle), never swallows a persistent failure into a
  default, and preserves the exception chain when the retry also fails.
* **Integration** -- ``Extractor``, ``Classifier``, and ``ImportanceScorer``
  each use the helper for every ``llm.generate`` call site (the first
  attempt and, separately, the repair-prompt attempt), so a transient
  transport error is invisible to a caller when the retry succeeds, and
  propagates as the *original* transport exception (never a
  ``ExtractionError``/``ClassificationError``/``ImportanceScoringError``,
  which are reserved for unusable-but-received responses) when it doesn't.
"""

from __future__ import annotations

import json

import pytest
from openai import APIConnectionError, APITimeoutError

from obsidian.core.enums import MemoryType, Role, SourceType
from obsidian.core.types import Conversation, Event
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.models import ClassificationResult, ExtractedFact
from obsidian.manager_ai.transport_retry import generate_with_transport_retry


def _classify_json(memory_type: str, confidence: float = 0.9, reason: str = "r") -> str:
    return json.dumps(
        {"memory_type": memory_type, "confidence": confidence, "reason": reason}
    )


def _importance_json(score: float = 0.6, reason: str = "r") -> str:
    return json.dumps({"score": score, "reason": reason})


def _extract_json(*texts: str) -> str:
    return json.dumps(
        [{"text": t, "evidence": "stated", "confidence": 0.9} for t in texts]
    )


def _fact(text: str = "The user likes AI.") -> ExtractedFact:
    return ExtractedFact(text=text, evidence="stated", confidence=0.9)


def _classification() -> ClassificationResult:
    return ClassificationResult(memory_type=MemoryType.FACT, confidence=0.9, reason="r")


def _conversation() -> Conversation:
    return Conversation(
        title="Remember",
        source=SourceType.MANUAL,
        events=[Event(role=Role.USER, content="irrelevant", source=SourceType.MANUAL)],
    )


class _FlakyLLM:
    """Consumes a FIFO queue of actions: an exception instance is raised, a
    string is returned. Records the number of ``generate`` invocations.
    """

    def __init__(self, actions) -> None:
        self._actions = list(actions)
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        action = self._actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        return action


# ---------------------------------------------------------------------------
# Unit tests: generate_with_transport_retry
# ---------------------------------------------------------------------------


def test_no_retry_on_success() -> None:
    llm = _FlakyLLM(["ok"])
    assert generate_with_transport_retry(llm, "prompt") == "ok"
    assert llm.calls == 1


@pytest.mark.parametrize(
    "transport_error",
    [
        ConnectionError("connection reset"),
        TimeoutError("timed out"),
        OSError("network unreachable"),
        APIConnectionError(request=None),  # type: ignore[arg-type]
        APITimeoutError(request=None),  # type: ignore[arg-type]
    ],
)
def test_retries_once_on_transport_error_then_succeeds(transport_error: BaseException) -> None:
    llm = _FlakyLLM([transport_error, "ok"])
    assert generate_with_transport_retry(llm, "prompt") == "ok"
    assert llm.calls == 2


def test_persistent_transport_error_propagates_after_exactly_one_retry() -> None:
    first = ConnectionError("first failure")
    second = ConnectionError("second failure")
    llm = _FlakyLLM([first, second])
    with pytest.raises(ConnectionError) as exc_info:
        generate_with_transport_retry(llm, "prompt")
    assert exc_info.value is second
    assert llm.calls == 2  # exactly one retry, never a third attempt
    # The exception chain is preserved: the second failure's __context__
    # is the first, since it was raised while handling it.
    assert exc_info.value.__context__ is first


def test_value_error_is_never_retried() -> None:
    # A ValueError is a parse/validation failure, not a transport failure --
    # this helper must never touch it (that retry belongs to the stage).
    llm = _FlakyLLM([ValueError("bad json"), "ok"])
    with pytest.raises(ValueError):
        generate_with_transport_retry(llm, "prompt")
    assert llm.calls == 1  # never retried, never silently replaced


def test_never_returns_a_default_on_persistent_failure() -> None:
    llm = _FlakyLLM([TimeoutError("t1"), TimeoutError("t2")])
    with pytest.raises(TimeoutError):
        generate_with_transport_retry(llm, "prompt")


# ---------------------------------------------------------------------------
# Integration: Classifier
# ---------------------------------------------------------------------------


def test_classifier_recovers_from_transient_transport_error() -> None:
    llm = _FlakyLLM([ConnectionError("boom"), _classify_json("preference")])
    result = Classifier(llm=llm).classify(_fact())
    assert result.memory_type is MemoryType.PREFERENCE
    assert llm.calls == 2  # transport retry, not a repair retry


def test_classifier_persistent_transport_error_propagates_untouched() -> None:
    llm = _FlakyLLM([ConnectionError("boom"), ConnectionError("boom again")])
    with pytest.raises(ConnectionError):
        Classifier(llm=llm).classify(_fact())
    assert llm.calls == 2  # transport helper's own retry, no repair attempted


def test_classifier_repair_retry_survives_transient_transport_error() -> None:
    # First attempt returns an invalid memory_type (triggers the repair
    # retry); the repair attempt itself hits a transient transport error,
    # which is retried once and then succeeds. "hobby" stands in for a
    # plausible-but-fake category -- "interest" is a real MemoryType as of
    # the V2 ontology (see obsidian/core/enums.py), so it no longer works
    # as an invalid-value example.
    llm = _FlakyLLM(
        [_classify_json("hobby"), ConnectionError("boom"), _classify_json("preference")]
    )
    result = Classifier(llm=llm).classify(_fact())
    assert result.memory_type is MemoryType.PREFERENCE
    assert llm.calls == 3


# ---------------------------------------------------------------------------
# Integration: ImportanceScorer
# ---------------------------------------------------------------------------


def test_importance_scorer_recovers_from_transient_transport_error() -> None:
    llm = _FlakyLLM([TimeoutError("boom"), _importance_json(0.7)])
    result = ImportanceScorer(llm=llm).score(_fact(), _classification())
    assert result.score == 0.7
    assert llm.calls == 2


def test_importance_scorer_persistent_transport_error_propagates_untouched() -> None:
    llm = _FlakyLLM([TimeoutError("boom"), TimeoutError("boom again")])
    with pytest.raises(TimeoutError):
        ImportanceScorer(llm=llm).score(_fact(), _classification())
    assert llm.calls == 2


def test_importance_scorer_repair_retry_survives_transient_transport_error() -> None:
    llm = _FlakyLLM(
        [_importance_json(1.5), ConnectionError("boom"), _importance_json(0.4)]
    )
    result = ImportanceScorer(llm=llm).score(_fact(), _classification())
    assert result.score == 0.4
    assert llm.calls == 3


# ---------------------------------------------------------------------------
# Integration: Extractor
# ---------------------------------------------------------------------------


def test_extractor_recovers_from_transient_transport_error() -> None:
    text = "The user lives in Muscat."
    llm = _FlakyLLM([ConnectionError("boom"), _extract_json(text)])
    facts = Extractor(llm=llm).extract(_conversation())
    assert [f.text for f in facts] == [text]
    assert llm.calls == 2


def test_extractor_persistent_transport_error_propagates_untouched() -> None:
    llm = _FlakyLLM([ConnectionError("boom"), ConnectionError("boom again")])
    with pytest.raises(ConnectionError):
        Extractor(llm=llm).extract(_conversation())
    assert llm.calls == 2


def test_extractor_repair_retry_survives_transient_transport_error() -> None:
    text = "The user lives in Muscat."
    llm = _FlakyLLM(["not valid json{{{", ConnectionError("boom"), _extract_json(text)])
    facts = Extractor(llm=llm).extract(_conversation())
    assert [f.text for f in facts] == [text]
    assert llm.calls == 3
