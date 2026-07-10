"""Regression tests for ``ManagerPipeline.process``'s concurrent classify/
importance stage.

``pipeline.py`` now runs ``Classifier.classify``/``ImportanceScorer.score``
for every extracted fact in a ``ThreadPoolExecutor`` instead of one fact at a
time, since each fact's classify -> score chain has no data dependency on any
other fact. These tests prove that change is behaviourally invisible from
the caller's side:

- results still line up with the correct fact even when the fake LLM's
  ``generate`` calls arrive out of extraction order (a real risk with
  concurrent execution, unlike the sequential code path);
- the final ``decisions`` list is still in extraction order;
- the CanonicalMatcher/KnowledgeUpdater dependency on ``existing`` (a NEW
  fact must be visible to a later, identical fact in the same conversation
  so it CONFIRMs instead of duplicating) still holds, since that part of
  ``process`` was deliberately left sequential;
- the stage actually runs concurrently (wall time roughly one fact's delay,
  not N of them), not just "still correct by accident".
"""

from __future__ import annotations

import json
import threading
import time
from typing import Dict, List

from obsidian.core.enums import Role, SourceType
from obsidian.core.types import Conversation, Event
from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.pipeline import ManagerPipeline


def _conversation(*facts_text: str) -> Conversation:
    # One synthesized USER event carrying all the facts we want the fake
    # Extractor to return; its content is never parsed by the fake LLM
    # (only the pipeline stage markers are), so its literal text doesn't
    # matter here.
    return Conversation(
        title="Remember",
        source=SourceType.MANUAL,
        events=[Event(role=Role.USER, content="irrelevant", source=SourceType.MANUAL)],
    )


class _KeyedConcurrentLLM:
    """Fake LLM keyed by fact text embedded in the prompt, not call order.

    Unlike a FIFO-queue fake (safe only for strictly sequential callers),
    this looks up each fact's canned classify/importance response by the
    ``fact.text``/``evidence`` substring the real prompts embed -- see
    ``Classifier.build_prompt``/``ImportanceScorer.build_prompt`` -- so it
    gives the right answer to whichever thread calls it, in whatever order
    threads happen to race in.

    Also records, per fact, the concrete time window each classify/score
    call was in flight, and injects a small artificial delay per call, so
    tests can assert the calls actually overlapped in wall time rather than
    merely asserting the (order-independent) final result.
    """

    def __init__(
        self,
        extract_response: str,
        classify_by_text: Dict[str, str],
        importance_by_text: Dict[str, str],
        delay_seconds: float = 0.2,
    ) -> None:
        self._extract_response = extract_response
        self._classify_by_text = classify_by_text
        self._importance_by_text = importance_by_text
        self._delay = delay_seconds
        self.call_windows: List[tuple] = []
        self._lock = threading.Lock()

    def generate(self, prompt: str) -> str:
        if "Conversation:\n" in prompt:
            return self._extract_response

        start = time.perf_counter()
        time.sleep(self._delay)

        if "Available memory types:" in prompt:
            for text, response in self._classify_by_text.items():
                if f"Text: {text}\n" in prompt:
                    with self._lock:
                        self.call_windows.append((start, time.perf_counter()))
                    return response
            raise AssertionError(f"No classify response registered for prompt:\n{prompt}")

        if "Classification:\n" in prompt:
            for text, response in self._importance_by_text.items():
                if f"Text: {text}\n" in prompt:
                    with self._lock:
                        self.call_windows.append((start, time.perf_counter()))
                    return response
            raise AssertionError(f"No importance response registered for prompt:\n{prompt}")

        raise AssertionError(f"Unrecognised prompt shape:\n{prompt}")


def _extract_json(*facts: tuple) -> str:
    return json.dumps(
        [
            {"text": text, "evidence": evidence, "confidence": confidence}
            for text, evidence, confidence in facts
        ]
    )


def _classify_json(memory_type: str, confidence: float = 0.9, reason: str = "stated") -> str:
    return json.dumps({"memory_type": memory_type, "confidence": confidence, "reason": reason})


def _importance_json(score: float, reason: str = "scored") -> str:
    return json.dumps({"score": score, "reason": reason})


def _build_pipeline(llm: _KeyedConcurrentLLM) -> ManagerPipeline:
    return ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )


def test_results_line_up_with_the_correct_fact_under_concurrency() -> None:
    facts = [
        ("The user lives in Muscat.", "stated", 0.9),
        ("The user prefers dark mode.", "stated", 0.8),
        ("The user is working on a project called Haven.", "stated", 0.95),
    ]
    llm = _KeyedConcurrentLLM(
        extract_response=_extract_json(*facts),
        classify_by_text={
            "The user lives in Muscat.": _classify_json("fact"),
            "The user prefers dark mode.": _classify_json("preference"),
            "The user is working on a project called Haven.": _classify_json("project"),
        },
        importance_by_text={
            "The user lives in Muscat.": _importance_json(0.7),
            "The user prefers dark mode.": _importance_json(0.3),
            "The user is working on a project called Haven.": _importance_json(0.95),
        },
    )
    pipeline = _build_pipeline(llm)

    decisions = pipeline.process(_conversation())

    assert [d.fact.text for d in decisions] == [f[0] for f in facts]
    by_text = {d.fact.text: d for d in decisions}
    assert by_text["The user lives in Muscat."].classification.memory_type.value == "fact"
    assert by_text["The user lives in Muscat."].importance.score == 0.7
    assert by_text["The user prefers dark mode."].classification.memory_type.value == "preference"
    assert by_text["The user prefers dark mode."].importance.score == 0.3
    assert (
        by_text["The user is working on a project called Haven."].classification.memory_type.value
        == "project"
    )
    assert by_text["The user is working on a project called Haven."].importance.score == 0.95


def test_decisions_preserve_extraction_order() -> None:
    facts = [
        ("Fact A.", "stated", 0.9),
        ("Fact B.", "stated", 0.9),
        ("Fact C.", "stated", 0.9),
        ("Fact D.", "stated", 0.9),
    ]
    llm = _KeyedConcurrentLLM(
        extract_response=_extract_json(*facts),
        classify_by_text={text: _classify_json("fact") for text, _, _ in facts},
        importance_by_text={text: _importance_json(0.5) for text, _, _ in facts},
    )
    pipeline = _build_pipeline(llm)

    decisions = pipeline.process(_conversation())

    assert [d.fact.text for d in decisions] == ["Fact A.", "Fact B.", "Fact C.", "Fact D."]


def test_identical_fact_twice_in_one_conversation_still_confirms_not_duplicates() -> None:
    # CanonicalMatcher/KnowledgeUpdater must stay sequential: the first
    # occurrence creates a NEW KnowledgeObject and appends it to `existing`
    # *before* the second, textually-different-but-CanonicalMatcher-equal
    # fact is matched -- proving that dependency survived parallelizing the
    # classify/importance stage. The two facts differ only in case (not
    # exact text) so Extractor.deduplicate (keyed on exact
    # (text, source_event_id) equality) does not itself collapse them
    # before classify/importance ever runs -- CanonicalMatcher's
    # normalised-lowercase comparison is what should treat them as the same
    # underlying fact.
    facts = [
        ("The user lives in Muscat.", "stated", 0.9),
        ("the user lives in muscat.", "stated again", 0.9),
    ]
    llm = _KeyedConcurrentLLM(
        extract_response=_extract_json(*facts),
        classify_by_text={
            "The user lives in Muscat.": _classify_json("fact"),
            "the user lives in muscat.": _classify_json("fact"),
        },
        importance_by_text={
            "The user lives in Muscat.": _importance_json(0.7),
            "the user lives in muscat.": _importance_json(0.7),
        },
    )
    pipeline = _build_pipeline(llm)

    decisions = pipeline.process(_conversation())

    assert len(decisions) == 2
    assert decisions[0].knowledge is not None
    assert decisions[1].knowledge is not None
    # Same underlying KnowledgeObject, confirmed rather than duplicated.
    assert decisions[0].knowledge.id == decisions[1].knowledge.id
    assert decisions[1].knowledge.confirmation_count == 2


def test_classify_and_importance_calls_actually_overlap_in_wall_time() -> None:
    # Proves the concurrency is real, not just behaviourally harmless: with
    # N facts run sequentially at `delay` seconds per LLM call (2 calls per
    # fact: classify + score), total wall time would be >= 2*N*delay. Under
    # the ThreadPoolExecutor, all N classify->score chains run in parallel
    # threads, so wall time should stay close to a single chain's 2*delay
    # regardless of N.
    delay = 0.2
    n_facts = 5
    facts = [(f"Fact {i}.", "stated", 0.9) for i in range(n_facts)]
    llm = _KeyedConcurrentLLM(
        extract_response=_extract_json(*facts),
        classify_by_text={text: _classify_json("fact") for text, _, _ in facts},
        importance_by_text={text: _importance_json(0.5) for text, _, _ in facts},
        delay_seconds=delay,
    )
    pipeline = _build_pipeline(llm)

    start = time.perf_counter()
    pipeline.process(_conversation())
    wall_time = time.perf_counter() - start

    sequential_floor = 2 * n_facts * delay
    assert wall_time < sequential_floor * 0.6, (
        f"wall_time={wall_time:.3f}s was not meaningfully faster than the "
        f"sequential floor of {sequential_floor:.3f}s -- classify/importance "
        "calls do not appear to be running concurrently"
    )

    # Sanity: multiple call windows genuinely overlap (start of one call
    # before the end of another), not just "fast because it was cached".
    windows = llm.call_windows
    assert len(windows) == 2 * n_facts
    overlapping = any(
        a_start < b_end and b_start < a_end
        for i, (a_start, a_end) in enumerate(windows)
        for j, (b_start, b_end) in enumerate(windows)
        if i != j
    )
    assert overlapping, "no two LLM calls overlapped in time"
