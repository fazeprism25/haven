"""Tests for the Memory Review workflow: ``POST /memory/preview``,
``POST /memory/commit``, and ``POST /memory/cancel``.

Mirrors ``test_save_memory.py``'s conventions: an isolated ``tmp_path``
vault/concept directory per test via ``HAVEN_VAULT_DIR``/``HAVEN_CONCEPT_DIR``,
driven through ``TestClient``, with a scripted fake ``LLMInterface`` injected
into the real ``ManagerPipeline`` so Extractor/Classifier/ImportanceScorer/
CanonicalMatcher/KnowledgeUpdater all still run for real -- only the LLM they
call is fake.

The core property under test throughout: the LLM (Extractor/Classifier/
ImportanceScorer) is scripted to answer exactly once per conversation --
``preview`` consumes that single scripted response; ``commit`` must never
need another one, even when items are edited, deleted, or added.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import pytest
from fastapi.testclient import TestClient

from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.pipeline import ManagerPipeline
from obsidian.memory_engine.memory_store import MemoryStore


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))
    monkeypatch.setenv("HAVEN_WRITE_TRACE_DIR", str(tmp_path / "write_traces"))

    from obsidian.server.main import app

    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Scripted fake LLM -- injected into a real ManagerPipeline. Each call to
# generate() for a given stage pops the next scripted response, so a test
# that only scripts ONE extractor/classifier/importance response per fact
# proves the LLM was called exactly once even across a preview+commit pair.
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    def __init__(
        self,
        extract_response: str,
        classify_responses: Sequence[str] = (),
        importance_responses: Sequence[str] = (),
    ) -> None:
        self._extract_response = extract_response
        self._extract_calls = 0
        self._classify_responses = list(classify_responses)
        self._importance_responses = list(importance_responses)

    def generate(self, prompt: str) -> str:
        if "Conversation:\n" in prompt:
            self._extract_calls += 1
            if self._extract_calls > 1:
                raise AssertionError(
                    "Extractor called more than once for the same review -- "
                    "editing/deleting/adding memories must never re-run the LLM."
                )
            return self._extract_response
        if "Available memory types:" in prompt:
            return self._classify_responses.pop(0)
        if "Classification:\n" in prompt:
            return self._importance_responses.pop(0)
        raise AssertionError(f"Unrecognised prompt shape:\n{prompt}")


def _install_scripted_llm(
    client: TestClient,
    extract_response: str,
    classify_responses: Sequence[str] = (),
    importance_responses: Sequence[str] = (),
) -> _ScriptedLLM:
    llm = _ScriptedLLM(extract_response, classify_responses, importance_responses)
    client.app.state.manager_pipeline = ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )
    return llm


def _extract_json(facts: Sequence[tuple]) -> str:
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


def _preview(client: TestClient, **payload) -> dict:
    response = client.post("/api/v1/memory/preview", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Preview: stops before writing anything
# ---------------------------------------------------------------------------


def test_preview_extracts_but_does_not_write_to_vault(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user uses Terraform.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.6)],
    )
    body = _preview(client, canonical_fact="I use Terraform.")

    assert body["status"] == "ok"
    assert body["review_id"]
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["text"] == "The user uses Terraform."
    assert item["memory_type"] == "fact"
    assert item["evidence"] == "stated"
    assert item["fact_index"] == 0

    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    assert store.count() == 0


def test_preview_with_nothing_extracted_returns_ok_not_422(client: TestClient) -> None:
    _install_scripted_llm(client, extract_response="[]")
    body = _preview(client, canonical_fact="uh, hmm, nothing really")
    assert body["status"] == "ok"
    assert body["items"] == []
    assert body["review_id"]


# ---------------------------------------------------------------------------
# Duplicate short-circuit happens at preview, before extraction
# ---------------------------------------------------------------------------


def test_preview_duplicate_short_circuits_before_extraction(client: TestClient) -> None:
    class _ExplodingLLM:
        def generate(self, prompt: str) -> str:
            raise AssertionError("LLM must not be called for a duplicate transcript")

    turns = [{"role": "user", "content": "I use Terraform."}]

    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user uses Terraform.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.6)],
    )
    first = _preview(client, conversation=turns, external_key="conv-1")
    client.post(
        "/api/v1/memory/commit",
        json={"review_id": first["review_id"], "items": first["items"]},
    )

    client.app.state.manager_pipeline = ManagerPipeline(
        extractor=Extractor(llm=_ExplodingLLM()),
        classifier=Classifier(llm=_ExplodingLLM()),
        importance_scorer=ImportanceScorer(llm=_ExplodingLLM()),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )
    second = _preview(client, conversation=turns, external_key="conv-1")
    assert second["status"] == "duplicate"
    assert second["review_id"] is None
    assert second["items"] == []


# ---------------------------------------------------------------------------
# Commit: unchanged items persist exactly as extracted
# ---------------------------------------------------------------------------


def test_commit_unchanged_items_persists_as_extracted(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user lives in Muscat.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.7)],
    )
    body = _preview(client, canonical_fact="I live in Muscat.")

    response = client.post(
        "/api/v1/memory/commit",
        json={"review_id": body["review_id"], "items": body["items"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "success"
    assert result["canonical_fact"] == "The user lives in Muscat."
    assert result["review_summary"] == {
        "saved": 1,
        "edited": 0,
        "added": 0,
        "removed": 0,
    }

    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    assert store.count() == 1


# ---------------------------------------------------------------------------
# Edit: text/type overridden without a second LLM call
# ---------------------------------------------------------------------------


def test_commit_edited_text_and_type_persist_the_edit_not_the_original(
    client: TestClient,
) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user uses Notion.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.6)],
    )
    body = _preview(client, canonical_fact="I use Notion.")
    review_id = body["review_id"]
    item = body["items"][0]

    edited_items = [
        {**item, "text": "The user uses Notion for planning.", "memory_type": "project"}
    ]
    response = client.post(
        "/api/v1/memory/commit", json={"review_id": review_id, "items": edited_items}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["canonical_fact"] == "The user uses Notion for planning."
    assert result["memory_type"] == "project"
    assert result["review_summary"] == {"saved": 1, "edited": 1, "added": 0, "removed": 0}

    trace = client.get(f"/api/v1/dashboard/write-traces/{result['trace_id']}").json()["trace"]
    fact_trace = trace["facts"][0]
    assert fact_trace["review_action"] == "edited"
    assert fact_trace["original_fact_text"] == "The user uses Notion."
    assert fact_trace["original_memory_type"] == "fact"
    assert fact_trace["fact_text"] == "The user uses Notion for planning."


# ---------------------------------------------------------------------------
# Delete: omitted fact_index never reaches the vault
# ---------------------------------------------------------------------------


def test_commit_omitting_a_fact_index_deletes_it(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json(
            [
                ("The user uses Obsidian.", "stated", 0.9),
                ("The user studies at DTU.", "stated", 0.9),
            ]
        ),
        classify_responses=[_classify_json("fact"), _classify_json("fact")],
        importance_responses=[_importance_json(0.5), _importance_json(0.6)],
    )
    body = _preview(client, canonical_fact="I use Obsidian and study at DTU.")
    kept_item = next(i for i in body["items"] if "Obsidian" in i["text"])

    response = client.post(
        "/api/v1/memory/commit",
        json={"review_id": body["review_id"], "items": [kept_item]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["review_summary"] == {
        "saved": 1,
        "edited": 0,
        "added": 0,
        "removed": 1,
    }

    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    facts = {ko.canonical_fact for ko in store.all()}
    assert facts == {"The user uses Obsidian."}

    trace = client.get(
        f"/api/v1/dashboard/write-traces/{response.json()['trace_id']}"
    ).json()["trace"]
    deleted = [f for f in trace["facts"] if f["review_action"] == "deleted"]
    assert len(deleted) == 1
    assert deleted[0]["fact_text"] == "The user studies at DTU."
    assert deleted[0]["knowledge_object_id"] is None


# ---------------------------------------------------------------------------
# Add: a memory with no fact_index, never sent through the LLM
# ---------------------------------------------------------------------------


def test_commit_added_item_persists_with_fixed_defaults(client: TestClient) -> None:
    _install_scripted_llm(client, extract_response="[]")
    body = _preview(client, canonical_fact="just chatting, nothing to extract")
    assert body["items"] == []

    added_items = [
        {"fact_index": None, "text": "The user's favourite color is teal.", "memory_type": "preference"}
    ]
    response = client.post(
        "/api/v1/memory/commit",
        json={"review_id": body["review_id"], "items": added_items},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["canonical_fact"] == "The user's favourite color is teal."
    assert result["memory_type"] == "preference"
    assert result["review_summary"] == {"saved": 1, "edited": 0, "added": 1, "removed": 0}

    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    ko = store.all()[0]
    assert ko.confidence == 1.0
    assert ko.importance == 0.5


# ---------------------------------------------------------------------------
# Re-matching on edit: an edit that now duplicates an existing memory CONFIRMs
# ---------------------------------------------------------------------------


def test_editing_a_fact_to_duplicate_an_existing_memory_confirms_not_new(
    client: TestClient,
) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user lives in Oslo.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.6)],
    )
    first = _preview(client, canonical_fact="I live in Oslo.")
    first_commit = client.post(
        "/api/v1/memory/commit",
        json={"review_id": first["review_id"], "items": first["items"]},
    ).json()

    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user lives in Bergen.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.6)],
    )
    second = _preview(client, canonical_fact="I live in Bergen.")
    edited_items = [{**second["items"][0], "text": "The user lives in Oslo."}]
    second_commit = client.post(
        "/api/v1/memory/commit",
        json={"review_id": second["review_id"], "items": edited_items},
    ).json()

    assert second_commit["id"] == first_commit["id"]

    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    ko = store.get(__import__("uuid").UUID(first_commit["id"]))
    assert ko.confirmation_count == 2


# ---------------------------------------------------------------------------
# Everything deleted, nothing added -> 422, mirroring save_memory's contract
# ---------------------------------------------------------------------------


def test_commit_with_everything_deleted_and_nothing_added_returns_422(
    client: TestClient,
) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user uses Vim.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.5)],
    )
    body = _preview(client, canonical_fact="I use Vim.")

    response = client.post(
        "/api/v1/memory/commit", json={"review_id": body["review_id"], "items": []}
    )
    assert response.status_code == 422
    assert "nothing" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# review_id lifecycle: unknown, one-shot, cancelled
# ---------------------------------------------------------------------------


def test_commit_with_unknown_review_id_returns_404(client: TestClient) -> None:
    response = client.post(
        "/api/v1/memory/commit",
        json={"review_id": "00000000-0000-0000-0000-000000000000", "items": []},
    )
    assert response.status_code == 404


def test_commit_is_one_shot_second_commit_404s(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user uses Rust.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.5)],
    )
    body = _preview(client, canonical_fact="I use Rust.")
    payload = {"review_id": body["review_id"], "items": body["items"]}

    first = client.post("/api/v1/memory/commit", json=payload)
    assert first.status_code == 200

    second = client.post("/api/v1/memory/commit", json=payload)
    assert second.status_code == 404


def test_cancel_then_commit_404s(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user uses Go.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.5)],
    )
    body = _preview(client, canonical_fact="I use Go.")

    cancel_response = client.post(
        "/api/v1/memory/cancel", json={"review_id": body["review_id"]}
    )
    assert cancel_response.status_code == 200

    commit_response = client.post(
        "/api/v1/memory/commit",
        json={"review_id": body["review_id"], "items": body["items"]},
    )
    assert commit_response.status_code == 404


def test_cancel_with_unknown_review_id_is_a_harmless_no_op(client: TestClient) -> None:
    response = client.post(
        "/api/v1/memory/cancel",
        json={"review_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Robustness audit: stale/tampered fact_index in a commit payload
#
# Before this fix, a CommittedMemoryItem whose fact_index didn't correspond
# to any index in the original preview was accepted by Pydantic, placed in
# commit_memory's submitted_by_index dict, and then silently never read --
# not applied, not reported, no error. This proves it is now rejected
# cleanly instead of being lost.
# ---------------------------------------------------------------------------


def test_commit_with_out_of_range_fact_index_is_rejected_not_silently_dropped(
    client: TestClient,
) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user uses Rust.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.6)],
    )
    body = _preview(client, canonical_fact="I use Rust.")
    assert len(body["items"]) == 1

    tampered_item = dict(body["items"][0])
    tampered_item["fact_index"] = 999  # out of range: preview only has index 0

    response = client.post(
        "/api/v1/memory/commit",
        json={"review_id": body["review_id"], "items": [tampered_item]},
    )
    assert response.status_code == 400, response.text
    assert "fact_index" in response.json()["detail"]

    # The review was consumed (one-shot, same as any other commit attempt)
    # but nothing was written to the vault.
    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    assert store.count() == 0


# ---------------------------------------------------------------------------
# Robustness audit: concurrent commits of the identical fact must not race
#
# Two independent Memory Reviews that both extracted the same fact text,
# committed from two genuinely concurrent threads, must not produce two
# KnowledgeObjects. The interleaving is forced deterministically (rather
# than relying on GIL-scheduling luck) by pausing thread "A" mid-critical-
# section via a monkeypatched _persist_knowledge_objects and recording
# entry/exit order: without the write lock this would reproduce the race
# (both threads decide NEW before either persists); with it, thread B's
# commit cannot even begin its own critical section until thread A's
# finishes.
# ---------------------------------------------------------------------------


def test_concurrent_commits_of_the_same_fact_are_serialized_not_duplicated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time

    import obsidian.server.main as main_module

    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user uses Terraform.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.6)],
    )
    review_a = _preview(client, canonical_fact="I use Terraform. (session A)")

    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user uses Terraform.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.6)],
    )
    review_b = _preview(client, canonical_fact="I use Terraform. (session B)")

    # Correlate calls by arrival order, not by ``threading.current_thread()``
    # -- FastAPI's ``TestClient`` dispatches each request through its own
    # internal ASGI worker-thread pool, so the calling test thread's name
    # ("A"/"B") is never visible inside the route.
    order: list = []
    order_lock = threading.Lock()
    call_count = 0
    first_call_paused = threading.Event()
    release_first = threading.Event()
    original = main_module._persist_knowledge_objects

    def wrapper(knowledge_objects):
        nonlocal call_count
        with order_lock:
            call_count += 1
            this_call = call_count
            order.append(f"enter-{this_call}")
        if this_call == 1:
            first_call_paused.set()
            release_first.wait(timeout=5)
        result = original(knowledge_objects)
        with order_lock:
            order.append(f"exit-{this_call}")
        return result

    monkeypatch.setattr(main_module, "_persist_knowledge_objects", wrapper)

    results: dict = {}

    def commit(name: str, review: dict) -> None:
        results[name] = client.post(
            "/api/v1/memory/commit",
            json={"review_id": review["review_id"], "items": review["items"]},
        )

    thread_a = threading.Thread(target=commit, args=("A", review_a), name="A")
    thread_b = threading.Thread(target=commit, args=("B", review_b), name="B")

    thread_a.start()
    assert first_call_paused.wait(timeout=5), (
        "thread A never reached _persist_knowledge_objects"
    )

    thread_b.start()
    time.sleep(0.2)
    with order_lock:
        snapshot = list(order)
    assert snapshot == ["enter-1"], (
        "a second commit made progress into its own persist call while the "
        "first still held the write lock -- commits are not being serialized"
    )

    release_first.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert order == ["enter-1", "exit-1", "enter-2", "exit-2"]
    assert results["A"].status_code == 200, results["A"].text
    assert results["B"].status_code == 200, results["B"].text

    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    assert store.count() == 1, (
        "two concurrent commits of the identical fact produced more than "
        "one KnowledgeObject -- the duplicate-write race was not prevented"
    )
