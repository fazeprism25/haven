"""Tests for the ``POST /memory`` HTTP endpoint.

Mirrors ``test_retrieve_context.py``'s conventions: an isolated ``tmp_path``
vault/concept directory per test via ``HAVEN_VAULT_DIR``/``HAVEN_CONCEPT_DIR``,
driven through ``TestClient`` like a real HTTP client.

``save_memory`` no longer builds a ``KnowledgeObject`` directly from the
request -- it runs the request through the real ``ManagerPipeline``
(Extractor -> Classifier -> ImportanceScorer -> CanonicalMatcher ->
KnowledgeUpdater) before ``VaultWriter``/``OntologyPipeline`` (see
``obsidian/server/main.py``'s module docstring). These tests inject a
scripted fake ``LLMInterface`` into that same real pipeline -- exactly like
``test_query_rewriter.py`` injects a fake OpenAI client via
``_install_fake_client`` -- so Extractor/Classifier/ImportanceScorer/
CanonicalMatcher/KnowledgeUpdater all still run for real; only the LLM
they call is fake. Nothing here asserts that ``canonical_fact``/
``memory_type`` are copied verbatim from the request -- that was the old
manual-construction contract and is no longer true.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence
from uuid import UUID

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
# Scripted fake LLM -- injected into a real ManagerPipeline
# ---------------------------------------------------------------------------


class _ScriptedLLM:
    """Fake ``LLMInterface`` shared by Extractor/Classifier/ImportanceScorer.

    ``LLMInterface.generate`` takes only a prompt string -- it carries no
    "which stage is calling" signal -- so this fake distinguishes stages by
    a structural marker unique to each one's prompt: the Extractor's
    "Conversation:\\n" section, the Classifier's "Available memory types:"
    list, and the ImportanceScorer's "Classification:\\n" section (which
    only its prompt includes, since it's scoring an already-classified
    fact). All three markers predate and survive this session's prompt
    edits, so this isn't tied to today's exact wording.
    """

    def __init__(
        self,
        extract_response: str,
        classify_responses: Sequence[str] = (),
        importance_responses: Sequence[str] = (),
    ) -> None:
        self._extract_response = extract_response
        self._classify_responses = list(classify_responses)
        self._importance_responses = list(importance_responses)

    def generate(self, prompt: str) -> str:
        if "Conversation:\n" in prompt:
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
) -> None:
    """Replace the running app's ``manager_pipeline`` with one backed by a
    scripted fake LLM. Real ``Extractor``/``Classifier``/``ImportanceScorer``/
    ``CanonicalMatcher``/``KnowledgeUpdater`` instances still run -- only the
    LLM they call is fake -- so this exercises the actual production pipeline
    wiring, not a mock of it.
    """
    llm = _ScriptedLLM(extract_response, classify_responses, importance_responses)
    client.app.state.manager_pipeline = ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )


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


# ---------------------------------------------------------------------------
# Request validation (unchanged -- rejected before the pipeline ever runs)
# ---------------------------------------------------------------------------


def test_save_memory_rejects_invalid_memory_type(client: TestClient) -> None:
    response = client.post(
        "/api/v1/memory",
        json={"canonical_fact": "Some fact.", "memory_type": "not_a_real_type"},
    )
    assert response.status_code == 422


def test_save_memory_rejects_empty_canonical_fact(client: TestClient) -> None:
    response = client.post("/api/v1/memory", json={"canonical_fact": ""})
    assert response.status_code == 422


def test_save_memory_rejects_whitespace_only_canonical_fact(client: TestClient) -> None:
    response = client.post("/api/v1/memory", json={"canonical_fact": "   "})
    assert response.status_code == 422


def test_save_memory_requires_canonical_fact_field(client: TestClient) -> None:
    response = client.post("/api/v1/memory", json={})
    assert response.status_code == 422


def test_save_memory_rejects_empty_conversation_with_no_canonical_fact(
    client: TestClient,
) -> None:
    response = client.post("/api/v1/memory", json={"conversation": []})
    assert response.status_code == 422


def test_save_memory_rejects_conversation_turn_with_blank_content(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/memory",
        json={"conversation": [{"role": "user", "content": "   "}]},
    )
    assert response.status_code == 422


def test_save_memory_rejects_conversation_turn_with_invalid_role(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/memory",
        json={"conversation": [{"role": "moderator", "content": "hello"}]},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Successful extraction and save
# ---------------------------------------------------------------------------


def test_successful_extraction_and_save(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json(
            [("The user uses Terraform for infra.", "stated in conversation", 0.9)]
        ),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.6)],
    )
    response = client.post(
        "/api/v1/memory", json={"canonical_fact": "I use Terraform for infra."}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["canonical_fact"] == "The user uses Terraform for infra."
    assert body["memory_type"] == "fact"
    assert isinstance(body["id"], str) and body["id"]


# ---------------------------------------------------------------------------
# Nothing extracted -> 422
# ---------------------------------------------------------------------------


def test_nothing_extracted_returns_422(client: TestClient) -> None:
    _install_scripted_llm(client, extract_response="[]")
    response = client.post(
        "/api/v1/memory", json={"canonical_fact": "uh, hmm, nothing really"}
    )
    assert response.status_code == 422
    assert "nothing" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Multiple extracted memories
# ---------------------------------------------------------------------------


def test_multiple_extracted_memories_are_all_persisted(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json(
            [
                ("The user uses Obsidian.", "stated", 0.9),
                ("The user studies Mechanical Engineering at DTU.", "stated", 0.9),
            ]
        ),
        classify_responses=[_classify_json("fact"), _classify_json("fact")],
        importance_responses=[_importance_json(0.5), _importance_json(0.7)],
    )
    response = client.post(
        "/api/v1/memory",
        json={"canonical_fact": "I use Obsidian and study Mechanical Engineering at DTU."},
    )
    assert response.status_code == 200

    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    facts = {ko.canonical_fact for ko in store.all()}
    assert facts == {
        "The user uses Obsidian.",
        "The user studies Mechanical Engineering at DTU.",
    }


def test_multiple_separate_saves_each_get_a_distinct_id(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("First fact.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.5)],
    )
    first = client.post("/api/v1/memory", json={"canonical_fact": "First fact."})

    _install_scripted_llm(
        client,
        extract_response=_extract_json([("Second fact.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.5)],
    )
    second = client.post("/api/v1/memory", json={"canonical_fact": "Second fact."})

    assert first.json()["id"] != second.json()["id"]

    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    facts = {ko.canonical_fact for ko in store.all()}
    assert facts == {"First fact.", "Second fact."}


# ---------------------------------------------------------------------------
# CONFIRM path
# ---------------------------------------------------------------------------


def test_confirm_path_reuses_existing_knowledge_object(client: TestClient) -> None:
    extract_response = _extract_json([("The user lives in Muscat.", "stated", 0.9)])

    _install_scripted_llm(
        client,
        extract_response=extract_response,
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.7)],
    )
    first = client.post("/api/v1/memory", json={"canonical_fact": "I live in Muscat."})
    assert first.status_code == 200

    _install_scripted_llm(
        client,
        extract_response=extract_response,
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.7)],
    )
    second = client.post("/api/v1/memory", json={"canonical_fact": "I live in Muscat."})
    assert second.status_code == 200

    assert second.json()["id"] == first.json()["id"]

    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    ko = store.get(UUID(first.json()["id"]))
    assert ko.confirmation_count == 2


# ---------------------------------------------------------------------------
# Classified memory type preserved (not copied from the request)
# ---------------------------------------------------------------------------


def test_classified_memory_type_is_preserved_not_copied_from_request(
    client: TestClient,
) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user prefers dark mode.", "stated", 0.9)]),
        classify_responses=[_classify_json("preference")],
        importance_responses=[_importance_json(0.6)],
    )
    # The request sets memory_type to something the Classifier does NOT
    # return, proving the response reflects classification, not this field.
    response = client.post(
        "/api/v1/memory",
        json={"canonical_fact": "I prefer dark mode.", "memory_type": "goal"},
    )
    assert response.status_code == 200
    assert response.json()["memory_type"] == "preference"


# ---------------------------------------------------------------------------
# Importance preserved
# ---------------------------------------------------------------------------


def test_importance_is_persisted_from_importance_scorer(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json(
            [("The user is working on a project called Haven.", "stated", 0.9)]
        ),
        classify_responses=[_classify_json("project")],
        importance_responses=[_importance_json(0.95)],
    )
    response = client.post(
        "/api/v1/memory", json={"canonical_fact": "I'm building Haven."}
    )
    assert response.status_code == 200

    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    ko = store.get(UUID(response.json()["id"]))
    assert ko.importance == 0.95


# ---------------------------------------------------------------------------
# Confidence preserved
# ---------------------------------------------------------------------------


def test_confidence_is_persisted_from_extracted_fact(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json(
            [("The user is working on a project called Haven.", "stated", 0.87)]
        ),
        classify_responses=[_classify_json("project")],
        importance_responses=[_importance_json(0.9)],
    )
    response = client.post(
        "/api/v1/memory", json={"canonical_fact": "I'm building Haven."}
    )
    assert response.status_code == 200

    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    ko = store.get(UUID(response.json()["id"]))
    assert ko.confidence == 0.87


# ---------------------------------------------------------------------------
# Full conversation payload ("Remember Conversation")
# ---------------------------------------------------------------------------


def test_full_conversation_extracts_a_single_fact(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json(
            [("The user is planning a trip to Japan.", "stated", 0.9)]
        ),
        classify_responses=[_classify_json("goal")],
        importance_responses=[_importance_json(0.7)],
    )
    response = client.post(
        "/api/v1/memory",
        json={
            "conversation": [
                {"role": "user", "content": "I'm planning a trip to Japan next spring."},
                {"role": "assistant", "content": "That sounds exciting! Which cities?"},
                {"role": "user", "content": "Probably Tokyo and Kyoto."},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["canonical_fact"] == "The user is planning a trip to Japan."
    assert body["memory_type"] == "goal"


def test_full_conversation_extracts_multiple_memories(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json(
            [
                ("The user prefers tea over coffee.", "stated", 0.9),
                ("The user is learning Japanese.", "stated", 0.85),
            ]
        ),
        classify_responses=[_classify_json("preference"), _classify_json("goal")],
        importance_responses=[_importance_json(0.5), _importance_json(0.6)],
    )
    response = client.post(
        "/api/v1/memory",
        json={
            "conversation": [
                {"role": "user", "content": "I prefer tea over coffee."},
                {"role": "assistant", "content": "Got it, noted."},
                {"role": "user", "content": "Also I'm learning Japanese this year."},
            ]
        },
    )
    assert response.status_code == 200

    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    facts = {ko.canonical_fact for ko in store.all()}
    assert facts == {
        "The user prefers tea over coffee.",
        "The user is learning Japanese.",
    }


def test_assistant_only_conversation_extracts_nothing(client: TestClient) -> None:
    # No USER turn at all -- an assistant-only explanation should yield zero
    # extracted facts (see Extractor.build_prompt's "What to ignore"
    # section), which still surfaces as the existing 422 contract.
    _install_scripted_llm(client, extract_response="[]")
    response = client.post(
        "/api/v1/memory",
        json={
            "conversation": [
                {"role": "assistant", "content": "Here is how OAuth2 works: ..."},
            ]
        },
    )
    assert response.status_code == 422
    assert "nothing" in response.json()["detail"].lower()


def test_user_preference_extracted_correctly_from_conversation(
    client: TestClient,
) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user prefers dark mode.", "stated", 0.9)]),
        classify_responses=[_classify_json("preference")],
        importance_responses=[_importance_json(0.6)],
    )
    response = client.post(
        "/api/v1/memory",
        json={
            "conversation": [
                {"role": "user", "content": "I really prefer dark mode over light mode."},
                {"role": "assistant", "content": "Noted, dark mode it is."},
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["memory_type"] == "preference"


def test_conversation_with_single_turn_still_works(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user lives in Oslo.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.6)],
    )
    response = client.post(
        "/api/v1/memory",
        json={"conversation": [{"role": "user", "content": "I live in Oslo."}]},
    )
    assert response.status_code == 200
    assert response.json()["canonical_fact"] == "The user lives in Oslo."


def test_conversation_payload_preserves_roles_and_order_in_prompt(
    client: TestClient,
) -> None:
    """The Conversation built from ``request.conversation`` must carry every
    turn's role verbatim, in the order supplied -- not synthesized into a
    single USER event -- so ``Extractor.build_prompt`` sees the whole
    dialogue in chronological order (see obsidian/manager_ai/extractor.py's
    ``events_text`` join).
    """

    captured: dict = {}

    class _CapturingLLM:
        def generate(self, prompt: str) -> str:
            if "Conversation:\n" in prompt:
                captured["prompt"] = prompt
                return "[]"
            raise AssertionError(f"Unexpected prompt:\n{prompt}")

    llm = _CapturingLLM()
    client.app.state.manager_pipeline = ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )

    client.post(
        "/api/v1/memory",
        json={
            "conversation": [
                {"role": "user", "content": "First user message."},
                {"role": "assistant", "content": "First assistant reply."},
                {"role": "user", "content": "Second user message."},
            ]
        },
    )

    prompt = captured["prompt"]
    assert (
        prompt.index("[user] First user message.")
        < prompt.index("[assistant] First assistant reply.")
        < prompt.index("[user] Second user message.")
    )


# ---------------------------------------------------------------------------
# Backward compatibility -- legacy canonical_fact-only requests still work
# ---------------------------------------------------------------------------


def test_legacy_canonical_fact_request_without_conversation_field_still_works(
    client: TestClient,
) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json(
            [("The user uses Notion for planning.", "stated", 0.9)]
        ),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.5)],
    )
    response = client.post(
        "/api/v1/memory", json={"canonical_fact": "I use Notion for planning."}
    )
    assert response.status_code == 200
    assert response.json()["canonical_fact"] == "The user uses Notion for planning."


# ---------------------------------------------------------------------------
# Vault + ontology both invoked
# ---------------------------------------------------------------------------


def test_vault_and_ontology_are_both_invoked(client: TestClient) -> None:
    _install_scripted_llm(
        client,
        extract_response=_extract_json([("The user uses Terraform.", "stated", 0.9)]),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.6)],
    )
    client.post("/api/v1/memory", json={"canonical_fact": "I use Terraform."})

    store = MemoryStore(client.app.state.vault_dir)
    store.load()
    assert "The user uses Terraform." in [ko.canonical_fact for ko in store.all()]

    # OntologyPipeline.process runs on every save, so a proper-noun concept
    # in the fact should be detected and attached, making the fact
    # reachable through the ontology path too -- verified through the
    # trace's matched_by_ontology flag rather than re-deriving
    # concept-detection internals here.
    response = client.post(
        "/api/v1/retrieve_context",
        json={"query": "Terraform", "include_trace": True},
    )
    body = response.json()
    assert len(body["trace"]["candidates"]) == 1
    assert body["trace"]["candidates"][0]["matched_by_ontology"] is True


def test_saved_memory_is_immediately_retrievable_without_restart(
    client: TestClient,
) -> None:
    first = client.post("/api/v1/retrieve_context", json={"query": "Terraform"})
    assert first.json() == {"context": ""}

    _install_scripted_llm(
        client,
        extract_response=_extract_json(
            [("The user uses Terraform for infra.", "stated", 0.9)]
        ),
        classify_responses=[_classify_json("fact")],
        importance_responses=[_importance_json(0.6)],
    )
    client.post(
        "/api/v1/memory", json={"canonical_fact": "I use Terraform for infra."}
    )

    second = client.post("/api/v1/retrieve_context", json={"query": "Terraform"})
    assert "The user uses Terraform for infra." in second.json()["context"]
