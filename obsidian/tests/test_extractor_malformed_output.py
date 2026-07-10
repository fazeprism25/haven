"""Regression tests for the Extractor's handling of malformed LLM output.

Mirrors ``test_classifier_invalid_memory_type.py``'s shape for the sibling
failure mode: the Extractor's own LLM call can return unparseable JSON, a
non-array, a truncated response, or entries missing required keys. Before
this fix, any of those raised a bare ``ValueError`` straight out of
``Extractor.extract_with_trace``, uncaught anywhere in ``ManagerPipeline``,
which would reach ``POST /memory/preview`` (and ``/import/obsidian/preview``,
which calls it in-process) as an uncaught exception -- an HTTP 500.

The contract now proven here:

* **Retry path** -- a malformed/truncated/invalid-shape response triggers
  exactly one repair retry; if the retry is usable, extraction succeeds
  normally.
* **Give-up path** -- if the repair retry is *also* unusable,
  ``Extractor.extract_with_trace`` raises
  :class:`~obsidian.core.errors.ExtractionError` (never a bare
  ``ValueError``), and never fabricates facts.
* **Pipeline never aborts** -- ``ManagerPipeline.extract_classify_score``
  catches ``ExtractionError`` and treats the conversation as having produced
  zero facts, exactly like a legitimate empty extraction.
* **Preview never 500s** -- ``POST /memory/preview`` returns ``200`` with an
  empty review (``status="ok"``, ``items=[]``) when extraction fails, never
  an uncaught exception.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Sequence

import pytest
from fastapi.testclient import TestClient

from obsidian.core.enums import Role, SourceType
from obsidian.core.errors import ExtractionError
from obsidian.core.types import Conversation, Event
from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.pipeline import ManagerPipeline


def _extract_json(*texts: str) -> str:
    return json.dumps(
        [{"text": t, "evidence": "stated", "confidence": 0.9} for t in texts]
    )


def _conversation() -> Conversation:
    return Conversation(
        title="Remember",
        source=SourceType.MANUAL,
        events=[
            Event(role=Role.USER, content="I use Terraform.", source=SourceType.MANUAL)
        ],
    )


# ---------------------------------------------------------------------------
# Unit tests: Extractor.extract_with_trace retry / give-up
# ---------------------------------------------------------------------------


class _QueueLLM:
    """Returns canned responses in FIFO order, recording the call count.

    ``Extractor.extract_with_trace`` calls ``generate`` once, then once more
    only if the first response is unusable -- so a two-element queue
    exercises the repair retry directly, with no prompt routing needed.
    """

    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return self._responses.pop(0)


def test_empty_extraction_returns_no_facts_without_retry() -> None:
    # A valid, empty JSON array is a legitimate "nothing worth remembering"
    # result -- not malformed output, so no retry should happen.
    llm = _QueueLLM(["[]"])
    trace = Extractor(llm=llm).extract_with_trace(_conversation())
    assert trace.facts == []
    assert llm.calls == 1


def test_malformed_json_retries_once_then_succeeds() -> None:
    llm = _QueueLLM(["not json at all {{{", _extract_json("The user uses Terraform.")])
    trace = Extractor(llm=llm).extract_with_trace(_conversation())
    assert [f.text for f in trace.facts] == ["The user uses Terraform."]
    assert llm.calls == 2  # exactly one repair retry
    assert trace.raw_response == _extract_json("The user uses Terraform.")


def test_truncated_response_retries_once_then_succeeds() -> None:
    truncated = '[{"text": "The user uses Terraform.", "evidence": "stated", "conf'
    llm = _QueueLLM([truncated, _extract_json("The user uses Terraform.")])
    trace = Extractor(llm=llm).extract_with_trace(_conversation())
    assert [f.text for f in trace.facts] == ["The user uses Terraform."]
    assert llm.calls == 2


def test_invalid_shape_retries_once_then_succeeds() -> None:
    # Valid JSON, but missing the required "confidence" key.
    missing_key = json.dumps(
        [{"text": "The user uses Terraform.", "evidence": "stated"}]
    )
    llm = _QueueLLM([missing_key, _extract_json("The user uses Terraform.")])
    trace = Extractor(llm=llm).extract_with_trace(_conversation())
    assert [f.text for f in trace.facts] == ["The user uses Terraform."]
    assert llm.calls == 2


def test_persistent_malformed_json_raises_extraction_error() -> None:
    # Both the first attempt and the single repair retry are unparseable:
    # give up with a typed ExtractionError (never a bare ValueError), and
    # never retry more than once.
    llm = _QueueLLM(["not json {{{", "still not json ]]]"])
    with pytest.raises(ExtractionError):
        Extractor(llm=llm).extract_with_trace(_conversation())
    assert llm.calls == 2  # one attempt + exactly one repair, then stop


def test_persistent_truncated_response_raises_extraction_error() -> None:
    truncated = '[{"text": "The user uses Terraform.", "evidence": "stated", "conf'
    llm = _QueueLLM([truncated, truncated])
    with pytest.raises(ExtractionError):
        Extractor(llm=llm).extract_with_trace(_conversation())
    assert llm.calls == 2


def test_repair_prompt_quotes_previous_response_and_reason() -> None:
    repair = Extractor(llm=_QueueLLM([])).build_repair_prompt(
        "not json {{{",
        ValueError("LLM response could not be parsed as JSON: bad token"),
    )
    assert "not json {{{" in repair
    assert "Reason:" in repair


# ---------------------------------------------------------------------------
# Pipeline test: a whole-conversation extraction failure is zero facts,
# never an uncaught exception.
# ---------------------------------------------------------------------------


class _AlwaysBadLLM:
    """Always returns unparseable output, regardless of prompt.

    Used to prove a whole-conversation extraction failure never reaches the
    Classifier/ImportanceScorer stages and never raises out of the pipeline
    or the server.
    """

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str) -> str:
        self.calls += 1
        return "not json at all {{{"


def _build_pipeline(llm) -> ManagerPipeline:
    return ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )


def test_pipeline_treats_extraction_failure_as_zero_facts() -> None:
    llm = _AlwaysBadLLM()
    scored_facts, trace = _build_pipeline(llm).extract_classify_score(_conversation())

    assert scored_facts == []
    assert trace.facts == []
    assert llm.calls == 2  # one attempt + one repair retry, then give up


def test_pipeline_extraction_failure_does_not_raise() -> None:
    # process_with_trace/process go through the same code path; confirm
    # neither one lets ExtractionError escape.
    llm = _AlwaysBadLLM()
    decisions, trace = _build_pipeline(llm).process_with_trace(_conversation())

    assert decisions == []
    assert trace.facts == []


# ---------------------------------------------------------------------------
# Server test: preview never 500s on an extraction failure
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))
    monkeypatch.setenv("HAVEN_WRITE_TRACE_DIR", str(tmp_path / "write_traces"))

    from obsidian.server.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_preview_returns_ok_with_empty_items_when_extraction_fails(
    client: TestClient,
) -> None:
    llm = _AlwaysBadLLM()
    client.app.state.manager_pipeline = _build_pipeline(llm)

    response = client.post(
        "/api/v1/memory/preview", json={"canonical_fact": "I use Terraform."}
    )

    # Never a 500: extraction failure is an empty review, not a crash.
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["items"] == []
    assert body["review_id"]
