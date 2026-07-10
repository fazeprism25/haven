"""Tests for :mod:`benchmarks.adapters.haven_full_adapter`.

Exercises HavenFullAdapter through the same mem0-shaped interface
``benchmarks/tests/test_haven_adapter.py`` uses for HavenAdapter, plus the
new ``add_conversation`` entry point the runner prefers. A scripted fake LLM
(``_DynamicScriptedLLM``) stands in for ``ManagerAILLM`` so these tests are
deterministic and need no network/API key -- it extracts each conversation
turn's content verbatim as its own fact and returns fixed classification/
importance responses, isolating exactly what this adapter is responsible
for: wiring Conversation -> ManagerPipeline -> VaultWriter/OntologyPipeline,
not language understanding (the same trade-off
``benchmarks/incremental_ingestion/fake_llm.py`` documents for its own
marker-based fake).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from benchmarks.adapters.haven_adapter import HavenAdapter
from benchmarks.adapters.haven_full_adapter import HavenFullAdapter


class _DynamicScriptedLLM:
    """Extracts each conversation turn's content verbatim as its own fact."""

    def __init__(self) -> None:
        self.extract_prompts: List[str] = []

    def generate(self, prompt: str) -> str:
        if "Conversation:\n" in prompt:
            self.extract_prompts.append(prompt)
            _preamble, evidence = prompt.split("Conversation:\n", 1)
            events_block = evidence.split("\n\n", 1)[0]
            facts = []
            for line in events_block.splitlines():
                if not line.strip():
                    continue
                text = line.split("] ", 1)[1] if "] " in line else line
                facts.append(
                    {"text": text, "evidence": "scripted", "confidence": 0.9}
                )
            return json.dumps(facts)
        if "Available memory types:" in prompt:
            return json.dumps(
                {"memory_type": "fact", "confidence": 0.9, "reason": "scripted"}
            )
        if "Classification:\n" in prompt:
            return json.dumps({"score": 0.7, "reason": "scripted"})
        raise AssertionError(f"Unrecognised prompt shape:\n{prompt[:200]}")


def _turns(*texts: str) -> List[Dict[str, str]]:
    return [{"speaker": "user", "text": text} for text in texts]


def _messages(*texts: str) -> List[Dict[str, str]]:
    return [{"role": "user", "content": text} for text in texts]


def _answer(result: dict) -> str:
    return " ".join(mem["memory"] for mem in result.get("results", []))


class TestFromConfig:
    def test_returns_haven_full_adapter_instance(self) -> None:
        adapter = HavenFullAdapter.from_config({})
        assert isinstance(adapter, HavenFullAdapter)
        assert isinstance(adapter, HavenAdapter)

    def test_each_instance_gets_isolated_storage(self) -> None:
        a = HavenFullAdapter(llm=_DynamicScriptedLLM())
        b = HavenFullAdapter(llm=_DynamicScriptedLLM())
        assert a._vault_dir != b._vault_dir
        assert a._concept_dir != b._concept_dir

    def test_fresh_instance_has_no_prior_data(self) -> None:
        adapter = HavenFullAdapter(llm=_DynamicScriptedLLM())
        assert adapter.search("anything") == {"results": []}

    def test_default_llm_is_manager_ai_llm_when_not_injected(self) -> None:
        from obsidian.manager_ai.llm import ManagerAILLM

        adapter = HavenFullAdapter()
        assert isinstance(adapter._llm, ManagerAILLM)


class TestAddConversation:
    def test_single_turn_persists_one_knowledge_object(self) -> None:
        adapter = HavenFullAdapter(llm=_DynamicScriptedLLM())
        result = adapter.add_conversation(_turns("Haven uses Claude for reasoning."))

        assert len(result["results"]) == 1
        assert result["results"][0]["memory"] == "Haven uses Claude for reasoning."
        assert result["results"][0]["event"] == "NEW"
        assert len(list(adapter._vault_dir.glob("*.md"))) == 1

    def test_whole_conversation_reaches_extractor_in_one_call(self) -> None:
        """The Extractor must see every turn together -- not once per
        message -- since ManagerPipeline.process expects a single
        Conversation (see obsidian/manager_ai/pipeline.py)."""
        llm = _DynamicScriptedLLM()
        adapter = HavenFullAdapter(llm=llm)

        adapter.add_conversation(
            _turns(
                "For my personal AI project I decided to build the Manager "
                "AI before GraphRAG.",
                "The reason is that extraction quality appears to be a "
                "larger bottleneck than retrieval quality.",
            )
        )

        assert len(llm.extract_prompts) == 1
        assert "Manager AI before GraphRAG" in llm.extract_prompts[0]
        assert "larger bottleneck" in llm.extract_prompts[0]

    def test_identical_fact_confirms_rather_than_duplicates(self) -> None:
        adapter = HavenFullAdapter(llm=_DynamicScriptedLLM())
        adapter.add_conversation(_turns("Haven uses Claude for reasoning."))
        result = adapter.add_conversation(_turns("Haven uses Claude for reasoning."))

        assert result["results"][0]["event"] == "CONFIRM"
        assert len(list(adapter._vault_dir.glob("*.md"))) == 1

    def test_refinement_updates_in_place(self) -> None:
        """Adapted from the Stage 2 UPDATE contract: a strict whole-word
        prefix extension refines the existing memory instead of creating
        a second one."""
        adapter = HavenFullAdapter(llm=_DynamicScriptedLLM())
        adapter.add_conversation(_turns("The user works at Google"))
        result = adapter.add_conversation(
            _turns("The user works at Google as a Staff Engineer")
        )

        assert result["results"][0]["event"] == "UPDATE"
        assert len(list(adapter._vault_dir.glob("*.md"))) == 1

        search_result = adapter.search("Where does the user work?")
        answer = _answer(search_result)
        assert "Staff Engineer" in answer
        assert answer.count("The user works at Google") == 1

    def test_refinement_updates_in_place_with_trailing_period(self) -> None:
        """Extractor-produced canonical facts routinely end with a period
        (unlike the hand-written fact in
        ``test_refinement_updates_in_place`` above); the UPDATE prefix rule
        must still fire in that realistic shape (see CanonicalMatcher)."""
        adapter = HavenFullAdapter(llm=_DynamicScriptedLLM())
        adapter.add_conversation(_turns("The user works at Google."))
        result = adapter.add_conversation(
            _turns("The user works at Google as a Staff Engineer.")
        )

        assert result["results"][0]["event"] == "UPDATE"
        assert len(list(adapter._vault_dir.glob("*.md"))) == 1

        search_result = adapter.search("Where does the user work?")
        answer = _answer(search_result)
        assert "Staff Engineer" in answer
        assert answer.count("The user works at Google") == 1

    def test_writes_concept_files_via_ontology_pipeline(self) -> None:
        adapter = HavenFullAdapter(llm=_DynamicScriptedLLM())
        adapter.add_conversation(_turns("Haven uses Claude for reasoning."))
        concept_files = list(adapter._concept_dir.glob("*.md"))
        assert len(concept_files) >= 1

    def test_empty_conversation_persists_nothing(self) -> None:
        adapter = HavenFullAdapter(llm=_DynamicScriptedLLM())
        result = adapter.add_conversation([])
        assert result == {"results": []}
        assert list(adapter._vault_dir.glob("*.md")) == []


class TestAdd:
    """add() must still work standalone, delegating to add_conversation."""

    def test_add_wraps_single_message_as_one_turn_conversation(self) -> None:
        adapter = HavenFullAdapter(llm=_DynamicScriptedLLM())
        result = adapter.add(_messages("Haven uses Claude for reasoning."))
        assert result["results"][0]["memory"] == "Haven uses Claude for reasoning."

    def test_add_skips_empty_content(self) -> None:
        adapter = HavenFullAdapter(llm=_DynamicScriptedLLM())
        result = adapter.add(_messages(""))
        assert result == {"results": []}


class TestSearchIsIdenticalToHavenRetrieval:
    """The read path MUST behave identically to Haven Retrieval -- proven
    here by literally being the same inherited method, not a re-implementation."""

    def test_search_method_is_inherited_unchanged(self) -> None:
        assert HavenFullAdapter.search is HavenAdapter.search
        assert HavenFullAdapter.delete_all is HavenAdapter.delete_all

    def test_search_finds_added_memory(self) -> None:
        adapter = HavenFullAdapter(llm=_DynamicScriptedLLM())
        adapter.add_conversation(_turns("Haven uses Claude for reasoning."))
        result = adapter.search("What does Haven use?")
        assert "Haven uses Claude for reasoning." in _answer(result)


class TestDeleteAll:
    def test_delete_all_clears_vault_and_concept_files(self) -> None:
        adapter = HavenFullAdapter(llm=_DynamicScriptedLLM())
        adapter.add_conversation(_turns("Haven uses Claude."))
        adapter.delete_all(filters={"user_id": "user123"})
        assert list(adapter._vault_dir.glob("*.md")) == []
        assert list(adapter._concept_dir.glob("*.md")) == []

    def test_add_after_delete_all_still_works(self) -> None:
        adapter = HavenFullAdapter(llm=_DynamicScriptedLLM())
        adapter.add_conversation(_turns("Haven uses Claude."))
        adapter.delete_all()
        adapter.add_conversation(_turns("Haven uses Qdrant."))
        result = adapter.search("Haven")
        answer = _answer(result)
        assert "Haven uses Qdrant." in answer
        assert "Haven uses Claude." not in answer


class TestFullBenchmarkFlow:
    """Replays run_benchmark()'s exact call sequence (add_conversation then
    search) against one dataset-shaped conversation."""

    def test_runner_call_sequence_end_to_end(self) -> None:
        adapter = HavenFullAdapter.from_config({})
        adapter._llm = _DynamicScriptedLLM()
        adapter._pipeline.extractor.llm = adapter._llm
        adapter._pipeline.classifier.llm = adapter._llm
        adapter._pipeline.importance_scorer.llm = adapter._llm

        try:
            adapter.delete_all(filters={"user_id": "user123"})
        except Exception:
            pass

        conversation = _turns(
            "For my personal AI project I decided to build the Manager AI "
            "before GraphRAG.",
            "The reason is that extraction quality appears to be a larger "
            "bottleneck than retrieval quality.",
        )
        adapter.add_conversation(conversation, user_id="user123", agent_id="agent456")

        result = adapter.search(
            query="What did I decide about Manager AI?",
            filters={"user_id": "user123"},
        )
        answer = " ".join(mem["memory"] for mem in result.get("results", []))

        assert "Manager AI" in answer
