"""Regression tests for the Classifier's invalid-``memory_type`` handling.

Reproduces and pins the fix for the import HTTP 500 whose root cause was a
note about "interests": the Classifier LLM sometimes returns a plausible but
non-existent ``memory_type`` (originally ``"interest"``), which used to raise
an uncaught ``ValueError`` all the way out of ``POST /import/obsidian/preview``
as a 500.

The V2 ontology (see ``obsidian/core/enums.py``) added ``INTEREST`` as a real
``MemoryType`` member, so ``"interest"`` is no longer an example of an invalid
value -- these tests now use ``"hobby"`` as the stand-in plausible-but-fake
category instead. The scenario name (an "interests.md" source note) and the
underlying contract this file pins are otherwise unchanged: any invented
category, not specifically ``"interest"``, must trigger the same
retry-then-skip behavior.

The contract now proven here:

* **Retry path** -- an invalid ``memory_type`` triggers exactly one repair
  retry; if the retry is valid, classification succeeds normally.
* **Skip path** -- if the retry is *also* invalid, ``Classifier.classify``
  raises :class:`ClassificationError` (never a bare ``ValueError``), and the
  pipeline skips that one fact.
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
from obsidian.manager_ai.classifier import Classifier, ClassificationError
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.models import ExtractedFact
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


# ---------------------------------------------------------------------------
# Unit tests: Classifier.classify retry / skip
# ---------------------------------------------------------------------------


class _QueueLLM:
    """Returns canned responses in FIFO order, recording the call count.

    ``Classifier.classify`` calls ``generate`` once, then once more only if
    the first response is unusable -- so a two-element queue exercises the
    repair retry directly, with no prompt routing needed.
    """

    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return self._responses.pop(0)


def test_valid_first_response_does_not_retry() -> None:
    llm = _QueueLLM([_classify_json("preference")])
    result = Classifier(llm=llm).classify(
        ExtractedFact(text="The user likes AI.", evidence="stated", confidence=0.9)
    )
    assert result.memory_type is MemoryType.PREFERENCE
    assert llm.calls == 1  # no repair prompt needed


def test_invalid_memory_type_retries_once_then_succeeds() -> None:
    # First response is the invalid "hobby"; the one repair retry returns a
    # valid type, so classification succeeds without raising.
    llm = _QueueLLM([_classify_json("hobby"), _classify_json("preference")])
    result = Classifier(llm=llm).classify(
        ExtractedFact(text="The user is into AI.", evidence="stated", confidence=0.9)
    )
    assert result.memory_type is MemoryType.PREFERENCE
    assert llm.calls == 2  # exactly one repair retry


def test_persistent_invalid_memory_type_raises_classification_error() -> None:
    # Both the first attempt and the single repair retry are invalid: give up
    # on this one fact with a typed ClassificationError (never a raw
    # ValueError), and never retry more than once.
    llm = _QueueLLM([_classify_json("hobby"), _classify_json("hobby")])
    with pytest.raises(ClassificationError):
        Classifier(llm=llm).classify(
            ExtractedFact(text="The user is into AI.", evidence="stated", confidence=0.9)
        )
    assert llm.calls == 2  # one attempt + exactly one repair, then stop


def test_repair_prompt_lists_every_valid_memory_type() -> None:
    fact = ExtractedFact(text="The user is into AI.", evidence="stated", confidence=0.9)
    repair = Classifier(llm=_QueueLLM([])).build_repair_prompt(
        fact, _classify_json("hobby"), ValueError("Invalid memory_type value: hobby")
    )
    for memory_type in MemoryType:
        assert memory_type.value in repair


# ---------------------------------------------------------------------------
# Pipeline test: one unclassifiable fact is skipped, the rest survive
# ---------------------------------------------------------------------------


class _KeyedScriptedLLM:
    """Fake LLM keyed by the fact text embedded in each stage's prompt.

    Routes by stage marker (extract/importance) and otherwise treats a prompt
    as a classify-or-repair call, serving that fact's next queued classify
    response. A single-element queue is returned for every attempt (so a fact
    scripted to always fail returns "hobby" on both its attempts); a
    multi-element queue is consumed one per attempt (retry-then-succeed).
    """

    def __init__(
        self,
        extract_response: str,
        classify_by_text: Dict[str, List[str]],
        importance_by_text: Dict[str, str],
    ) -> None:
        self._extract_response = extract_response
        self._classify_by_text = {k: list(v) for k, v in classify_by_text.items()}
        self._importance_by_text = dict(importance_by_text)
        self._lock = threading.Lock()

    def _match(self, prompt: str, keys) -> str:
        for text in keys:
            if f"Text: {text}\n" in prompt:
                return text
        raise AssertionError(f"No canned response for prompt:\n{prompt}")

    def generate(self, prompt: str) -> str:
        if "Conversation:\n" in prompt:
            return self._extract_response
        if "Classification:\n" in prompt:  # importance stage
            return self._importance_by_text[self._match(prompt, self._importance_by_text)]
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


def test_pipeline_skips_unclassifiable_fact_and_keeps_the_rest() -> None:
    good_a = "The user lives in Muscat."
    bad = "The user is into AI memory systems."
    good_c = "The user is working on Haven."
    llm = _KeyedScriptedLLM(
        extract_response=_extract_json(good_a, bad, good_c),
        classify_by_text={
            good_a: [_classify_json("fact")],
            bad: [_classify_json("hobby")],  # invalid on both attempts
            good_c: [_classify_json("project")],
        },
        importance_by_text={
            good_a: _importance_json(0.7),
            good_c: _importance_json(0.9),
        },
    )
    pipeline = _build_pipeline(llm)

    scored_facts, trace = pipeline.extract_classify_score(_conversation())

    # The extractor trace still lists all three facts...
    assert [f.text for f in trace.facts] == [good_a, bad, good_c]
    # ...but only the two classifiable ones survive, in extraction order.
    assert [f.text for (f, _c, _i) in scored_facts] == [good_a, good_c]
    types = {f.text: c.memory_type for (f, c, _i) in scored_facts}
    assert types[good_a] is MemoryType.FACT
    assert types[good_c] is MemoryType.PROJECT


def test_pipeline_recovers_when_the_repair_retry_succeeds() -> None:
    bad_then_good = "The user is into AI."
    llm = _KeyedScriptedLLM(
        extract_response=_extract_json(bad_then_good),
        classify_by_text={
            # First attempt invalid, repair attempt valid -> the fact survives.
            bad_then_good: [_classify_json("hobby"), _classify_json("preference")],
        },
        importance_by_text={bad_then_good: _importance_json(0.4)},
    )
    scored_facts, _trace = _build_pipeline(llm).extract_classify_score(_conversation())

    assert len(scored_facts) == 1
    assert scored_facts[0][1].memory_type is MemoryType.PREFERENCE


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

    with TestClient(app) as test_client:
        yield test_client


def test_import_preview_partial_success_when_one_fact_is_unclassifiable(
    client: TestClient, tmp_path: Path
) -> None:
    root = tmp_path / "external_vault"
    root.mkdir(parents=True, exist_ok=True)
    (root / "interests.md").write_text(
        "# Current Interests\n\n- Personal AI systems\n- AI memory\n",
        encoding="utf-8",
    )

    good_a = "The user lives in Muscat."
    bad = "The user is into AI memory systems."
    good_c = "The user is working on Haven."
    client.app.state.manager_pipeline = _build_pipeline(
        _KeyedScriptedLLM(
            extract_response=_extract_json(good_a, bad, good_c),
            classify_by_text={
                good_a: [_classify_json("fact")],
                bad: [_classify_json("hobby")],  # invalid on both attempts
                good_c: [_classify_json("project")],
            },
            importance_by_text={
                good_a: _importance_json(0.7),
                good_c: _importance_json(0.9),
            },
        )
    )

    response = client.post(
        "/api/v1/import/obsidian/preview",
        json={"root": str(root), "source_file": "interests.md"},
    )

    # Never a 500: the unclassifiable fact is dropped, not fatal.
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    texts = {item["text"] for item in body["items"]}
    assert texts == {good_a, good_c}  # the "hobby" fact was skipped
