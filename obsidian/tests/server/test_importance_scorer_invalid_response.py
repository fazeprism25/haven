"""Regression tests for the ImportanceScorer's malformed-response handling.

Mirrors ``test_classifier_invalid_memory_type.py``'s contract, applied to the
third pipeline stage: the ImportanceScorer LLM sometimes returns malformed
JSON, a response missing a required key, or an out-of-range/non-numeric
``score`` -- any of which used to raise an uncaught ``ValueError`` all the
way out of ``POST /import/obsidian/preview`` as a 500.

The contract now proven here:

* **Retry path** -- malformed JSON, a missing field, or an invalid ``score``
  triggers exactly one repair retry; if the retry is valid, scoring succeeds
  normally.
* **Skip path** -- if the retry is *also* invalid,
  ``ImportanceScorer.score`` raises :class:`ImportanceScoringError` (never a
  bare ``ValueError``), and the pipeline skips that one fact.
* **Remaining facts still import** -- the other facts in the same note are
  classified, scored, and returned as usual.
* **Preview never 500s** -- the import preview endpoint returns ``200`` with
  the surviving facts (partial success), never an uncaught ``ValueError``.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, List, Sequence

import pytest
from fastapi.testclient import TestClient

from obsidian.core.enums import MemoryType, Role, SourceType
from obsidian.core.types import Conversation, Event
from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer, ImportanceScoringError
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.models import ClassificationResult, ExtractedFact
from obsidian.manager_ai.pipeline import ManagerPipeline


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


def _classification() -> ClassificationResult:
    return ClassificationResult(
        memory_type=MemoryType.FACT, confidence=0.9, reason="r"
    )


# ---------------------------------------------------------------------------
# Unit tests: ImportanceScorer.score retry / skip
# ---------------------------------------------------------------------------


class _QueueLLM:
    """Returns canned responses in FIFO order, recording the call count.

    ``ImportanceScorer.score`` calls ``generate`` once, then once more only
    if the first response is unusable -- so a two-element queue exercises
    the repair retry directly, with no prompt routing needed.
    """

    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return self._responses.pop(0)


def test_valid_first_response_does_not_retry() -> None:
    llm = _QueueLLM([_importance_json(0.7)])
    result = ImportanceScorer(llm=llm).score(
        ExtractedFact(text="The user lives in Muscat.", evidence="stated", confidence=0.9),
        _classification(),
    )
    assert result.score == 0.7
    assert llm.calls == 1  # no repair prompt needed


def test_malformed_json_retries_once_then_succeeds() -> None:
    llm = _QueueLLM(["not valid json{{{", _importance_json(0.5)])
    result = ImportanceScorer(llm=llm).score(
        ExtractedFact(text="The user likes AI.", evidence="stated", confidence=0.9),
        _classification(),
    )
    assert result.score == 0.5
    assert llm.calls == 2  # exactly one repair retry


def test_persistent_malformed_json_raises_importance_scoring_error() -> None:
    llm = _QueueLLM(["not valid json{{{", "still not json}}}"])
    with pytest.raises(ImportanceScoringError):
        ImportanceScorer(llm=llm).score(
            ExtractedFact(text="The user likes AI.", evidence="stated", confidence=0.9),
            _classification(),
        )
    assert llm.calls == 2  # one attempt + exactly one repair, then stop


def test_missing_field_retries_once_then_succeeds() -> None:
    # First response is missing "reason"; the repair retry returns a
    # complete object, so scoring succeeds without raising.
    llm = _QueueLLM([json.dumps({"score": 0.6}), _importance_json(0.6)])
    result = ImportanceScorer(llm=llm).score(
        ExtractedFact(text="The user uses Obsidian.", evidence="stated", confidence=0.9),
        _classification(),
    )
    assert result.score == 0.6
    assert llm.calls == 2


def test_persistent_missing_field_raises_importance_scoring_error() -> None:
    llm = _QueueLLM([json.dumps({"score": 0.6}), json.dumps({"reason": "r"})])
    with pytest.raises(ImportanceScoringError):
        ImportanceScorer(llm=llm).score(
            ExtractedFact(text="The user uses Obsidian.", evidence="stated", confidence=0.9),
            _classification(),
        )
    assert llm.calls == 2


def test_invalid_score_value_retries_once_then_succeeds() -> None:
    # First response has an out-of-range score; the one repair retry
    # returns a valid score, so scoring succeeds without raising.
    llm = _QueueLLM([_importance_json(1.5), _importance_json(0.8)])
    result = ImportanceScorer(llm=llm).score(
        ExtractedFact(text="The user works at Acme Corp.", evidence="stated", confidence=0.9),
        _classification(),
    )
    assert result.score == 0.8
    assert llm.calls == 2


def test_persistent_invalid_score_value_raises_importance_scoring_error() -> None:
    # Both the first attempt and the single repair retry carry an invalid
    # score: give up on this one fact with a typed ImportanceScoringError
    # (never a raw ValueError), and never retry more than once.
    llm = _QueueLLM([_importance_json(1.5), json.dumps({"score": "high", "reason": "r"})])
    with pytest.raises(ImportanceScoringError):
        ImportanceScorer(llm=llm).score(
            ExtractedFact(text="The user works at Acme Corp.", evidence="stated", confidence=0.9),
            _classification(),
        )
    assert llm.calls == 2  # one attempt + exactly one repair, then stop


def test_repair_prompt_includes_invalid_response_error_and_schema() -> None:
    fact = ExtractedFact(text="The user likes AI.", evidence="stated", confidence=0.9)
    previous_response = _importance_json(1.5)
    error = ValueError("'score' must be between 0 and 1, got 1.5")
    repair = ImportanceScorer(llm=_QueueLLM([])).build_repair_prompt(
        fact, _classification(), previous_response, error
    )
    assert previous_response in repair
    assert str(error) in repair
    assert '"score"' in repair
    assert '"reason"' in repair


# ---------------------------------------------------------------------------
# Pipeline test: one unscorable fact is skipped, the rest survive
# ---------------------------------------------------------------------------


class _KeyedScriptedLLM:
    """Fake LLM keyed by the fact text embedded in each stage's prompt.

    Routes by stage marker (extract/importance) and otherwise treats a
    prompt as a classify-or-repair call, serving that fact's next queued
    classify response. Importance responses are looked up the same way,
    keyed by fact text, with a single-element queue returned for every
    attempt (so a fact scripted to always fail returns the same invalid
    response on both its attempts) and a multi-element queue consumed one
    per attempt (retry-then-succeed).
    """

    def __init__(
        self,
        extract_response: str,
        classify_by_text: Dict[str, List[str]],
        importance_by_text: Dict[str, List[str]],
    ) -> None:
        self._extract_response = extract_response
        self._classify_by_text = {k: list(v) for k, v in classify_by_text.items()}
        self._importance_by_text = {k: list(v) for k, v in importance_by_text.items()}
        self._lock = threading.Lock()

    def _match(self, prompt: str, keys) -> str:
        for text in keys:
            if f"Text: {text}\n" in prompt:
                return text
        raise AssertionError(f"No canned response for prompt:\n{prompt}")

    def generate(self, prompt: str) -> str:
        if "Conversation:\n" in prompt:
            return self._extract_response
        if "Classification:\n" in prompt:  # importance stage (score or repair)
            text = self._match(prompt, self._importance_by_text)
            with self._lock:
                queue = self._importance_by_text[text]
                return queue.pop(0) if len(queue) > 1 else queue[0]
        # classify (first attempt) or repair (retry) -- same response source
        text = self._match(prompt, self._classify_by_text)
        with self._lock:
            queue = self._classify_by_text[text]
            return queue.pop(0) if len(queue) > 1 else queue[0]


def _build_pipeline(llm) -> ManagerPipeline:
    return ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )


def _conversation() -> Conversation:
    return Conversation(
        title="Remember",
        source=SourceType.MANUAL,
        events=[Event(role=Role.USER, content="irrelevant", source=SourceType.MANUAL)],
    )


def test_pipeline_skips_unscorable_fact_and_keeps_the_rest() -> None:
    good_a = "The user lives in Muscat."
    bad = "The user is working on Haven."
    good_c = "The user uses Obsidian."
    llm = _KeyedScriptedLLM(
        extract_response=_extract_json(good_a, bad, good_c),
        classify_by_text={
            good_a: [_classify_json("fact")],
            bad: [_classify_json("project")],
            good_c: [_classify_json("fact")],
        },
        importance_by_text={
            good_a: [_importance_json(0.7)],
            bad: [_importance_json(1.5)],  # invalid on both attempts
            good_c: [_importance_json(0.5)],
        },
    )
    pipeline = _build_pipeline(llm)

    scored_facts, trace = pipeline.extract_classify_score(_conversation())

    # The extractor trace still lists all three facts...
    assert [f.text for f in trace.facts] == [good_a, bad, good_c]
    # ...but only the two scorable ones survive, in extraction order.
    assert [f.text for (f, _c, _i) in scored_facts] == [good_a, good_c]
    scores = {f.text: i.score for (f, _c, i) in scored_facts}
    assert scores[good_a] == 0.7
    assert scores[good_c] == 0.5


def test_pipeline_recovers_when_the_repair_retry_succeeds() -> None:
    text = "The user is into AI."
    llm = _KeyedScriptedLLM(
        extract_response=_extract_json(text),
        classify_by_text={text: [_classify_json("preference")]},
        importance_by_text={
            # First attempt invalid, repair attempt valid -> fact survives.
            text: [_importance_json(1.5), _importance_json(0.4)],
        },
    )
    scored_facts, _trace = _build_pipeline(llm).extract_classify_score(_conversation())

    assert len(scored_facts) == 1
    assert scored_facts[0][2].score == 0.4


# ---------------------------------------------------------------------------
# Server test: import preview returns partial success, never HTTP 500
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))
    monkeypatch.setenv("HAVEN_WRITE_TRACE_DIR", str(tmp_path / "write_traces"))
    monkeypatch.setenv("HAVEN_CHECKPOINT_DIR", str(tmp_path / "checkpoints"))

    from obsidian.server.main import app

    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client


def test_import_preview_partial_success_when_one_fact_is_unscorable(
    client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "external_vault"
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.md").write_text(
        "# Notes\n\n- Lives in Muscat\n- Working on Haven\n- Uses Obsidian\n",
        encoding="utf-8",
    )

    good_a = "The user lives in Muscat."
    bad = "The user is working on Haven."
    good_c = "The user uses Obsidian."
    client.app.state.manager_pipeline = _build_pipeline(
        _KeyedScriptedLLM(
            extract_response=_extract_json(good_a, bad, good_c),
            classify_by_text={
                good_a: [_classify_json("fact")],
                bad: [_classify_json("project")],
                good_c: [_classify_json("fact")],
            },
            importance_by_text={
                good_a: [_importance_json(0.7)],
                bad: [_importance_json(1.5)],  # invalid on both attempts
                good_c: [_importance_json(0.5)],
            },
        )
    )

    response = client.post(
        "/api/v1/import/obsidian/preview",
        json={"root": str(root), "source_file": "notes.md"},
    )

    # Never a 500: the unscorable fact is dropped, not fatal.
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    texts = {item["text"] for item in body["items"]}
    assert texts == {good_a, good_c}  # the unscorable fact was skipped
