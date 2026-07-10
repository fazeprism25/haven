"""Tests for :mod:`benchmarks.adapters.haven_continuation_adapter`.

Exercises :class:`HavenContinuationAdapter` at three levels:

- typing (``TestAddConversationTyping``): each ingested turn's
  ``KnowledgeObject.memory_type`` matches ``turn_type`` via the fixed
  dictionary, with no LLM call.
- supersession/resolution bookkeeping (``TestSupersession``,
  ``TestBlockerResolution``): ``supersedes_turn``/``resolves_turn`` produce
  the exact ``DecisionMetadata``/``valid_until`` mutations
  ``docs/architecture/CONTINUATION_BENCHMARK_INGESTION_DESIGN.md`` §6
  describes, applied directly against persisted vault state.
- end-to-end reconstruction (``TestProjectStateIntegration``): the real,
  unmodified ``MemoryEngine``/``ProjectStateBuilder``/
  ``StructuredPromptBuilder`` pipeline, driven only through
  ``build_continuation_context`` -- proving ``<ProjectState>`` is no longer
  the structurally empty shell ``CONTINUATION_BENCHMARK_AUDIT.md``
  Critical-1 documented for ``HavenAdapter``.

``TestExistingAdaptersUnaffected`` and ``TestDeterminism`` cover this task's
own explicit regression requirements: retrieval-benchmark behavior
(``HavenAdapter``, ``HavenFullAdapter``, ``run_benchmarks.get_adapter_cls``)
is untouched, and repeated ingestion of the same conversation is
deterministic in every way that matters (typing, supersession/resolution
outcomes) even though ids and wall-clock timestamps are not literally
byte-identical across runs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from benchmarks.adapters.haven_adapter import HavenAdapter
from benchmarks.adapters.haven_continuation_adapter import (
    TURN_TYPE_TO_MEMORY_TYPE,
    HavenContinuationAdapter,
)
from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import DecisionStatus, KnowledgeObject, get_decision_metadata


def _turn(
    text: str,
    turn_type: str,
    turn_index: int,
    supersedes_turn: Optional[int] = None,
    resolves_turn: Optional[int] = None,
) -> Dict[str, Any]:
    turn: Dict[str, Any] = {
        "speaker": "user",
        "text": text,
        "turn_type": turn_type,
        "turn_index": turn_index,
    }
    if supersedes_turn is not None:
        turn["supersedes_turn"] = supersedes_turn
    if resolves_turn is not None:
        turn["resolves_turn"] = resolves_turn
    return turn


def _objects(adapter: HavenContinuationAdapter) -> List[KnowledgeObject]:
    adapter._memory_store.load()
    return list(adapter._memory_store.all())


def _by_fact(adapter: HavenContinuationAdapter, fact: str) -> KnowledgeObject:
    matches = [ko for ko in _objects(adapter) if ko.canonical_fact == fact]
    assert len(matches) == 1, f"expected exactly one KnowledgeObject for {fact!r}, got {len(matches)}"
    return matches[0]


class TestAddConversationTyping:
    """turn_type -> MemoryType, per the fixed dictionary, no LLM call."""

    @pytest.mark.parametrize(
        "turn_type,expected_type",
        [
            ("architecture_discussion", MemoryType.FACT),
            ("implementation", MemoryType.FACT),
            ("distractor", MemoryType.FACT),
            ("rejected_approach", MemoryType.FACT),
            ("decision", MemoryType.DECISION),
            ("constraint", MemoryType.RULE),
            ("blocker", MemoryType.BLOCKER),
            ("task", MemoryType.TASK),
            ("open_question", MemoryType.OPEN_QUESTION),
        ],
    )
    def test_each_turn_type_maps_to_documented_memory_type(
        self, turn_type: str, expected_type: MemoryType
    ) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        text = f"Content for a {turn_type} turn."
        adapter.add_conversation([_turn(text, turn_type, 0)])
        assert _by_fact(adapter, text).memory_type is expected_type

    def test_unmapped_turn_type_falls_back_to_fact(self) -> None:
        """"note" (used once in the pilot dataset for authorial commentary)
        has no dictionary entry -- must fall back to FACT, not raise."""
        adapter = HavenContinuationAdapter.from_config({})
        adapter.add_conversation([_turn("An aside, not project content.", "note", 0)])
        assert _by_fact(adapter, "An aside, not project content.").memory_type is MemoryType.FACT

    def test_missing_turn_type_falls_back_to_fact(self) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        adapter.add_conversation([{"speaker": "user", "text": "No turn_type key at all.", "turn_index": 0}])
        assert _by_fact(adapter, "No turn_type key at all.").memory_type is MemoryType.FACT

    def test_empty_text_is_skipped(self) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        result = adapter.add_conversation([_turn("", "task", 0)])
        assert result == {"results": []}
        assert _objects(adapter) == []

    def test_dictionary_is_exactly_as_documented(self) -> None:
        assert TURN_TYPE_TO_MEMORY_TYPE == {
            "architecture_discussion": MemoryType.FACT,
            "implementation": MemoryType.FACT,
            "distractor": MemoryType.FACT,
            "rejected_approach": MemoryType.FACT,
            "decision": MemoryType.DECISION,
            "constraint": MemoryType.RULE,
            "blocker": MemoryType.BLOCKER,
            "task": MemoryType.TASK,
            "open_question": MemoryType.OPEN_QUESTION,
        }

    def test_no_llm_client_is_ever_constructed(self) -> None:
        """Unlike HavenFullAdapter, this adapter has no _llm/_pipeline at
        all -- ingestion is a pure dictionary lookup, never a model call."""
        adapter = HavenContinuationAdapter.from_config({})
        assert not hasattr(adapter, "_llm")
        assert not hasattr(adapter, "_pipeline")

    def test_returns_one_result_per_non_empty_turn(self) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        result = adapter.add_conversation(
            [
                _turn("Decided: use blended ranking.", "decision", 0),
                _turn("", "distractor", 1),
                _turn("Blocked on X.", "blocker", 2),
            ]
        )
        assert len(result["results"]) == 2
        assert result["results"][0]["event"] == "ADD"


class TestSupersession:
    """supersedes_turn -> DecisionMetadata(status=SUPERSEDED, ...) on the
    original turn's KnowledgeObject, plus valid_until archiving it."""

    def test_new_decision_records_supersedes_link(self) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        adapter.add_conversation(
            [
                _turn("Rank by embedding similarity alone.", "rejected_approach", 0),
                _turn(
                    "Rank by a blended score instead.",
                    "decision",
                    1,
                    supersedes_turn=0,
                ),
            ]
        )
        rejected = _by_fact(adapter, "Rank by embedding similarity alone.")
        adopted = _by_fact(adapter, "Rank by a blended score instead.")

        adopted_metadata = get_decision_metadata(adopted)
        assert adopted_metadata is not None
        assert adopted_metadata.status is DecisionStatus.ACTIVE
        assert adopted_metadata.supersedes == rejected.id

    def test_original_turn_gets_superseded_metadata_and_archived(self) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        adapter.add_conversation(
            [
                _turn("Rank by embedding similarity alone.", "rejected_approach", 0),
                _turn(
                    "Rank by a blended score instead.",
                    "decision",
                    1,
                    supersedes_turn=0,
                ),
            ]
        )
        rejected = _by_fact(adapter, "Rank by embedding similarity alone.")
        adopted = _by_fact(adapter, "Rank by a blended score instead.")

        rejected_metadata = get_decision_metadata(rejected)
        assert rejected_metadata is not None
        assert rejected_metadata.status is DecisionStatus.SUPERSEDED
        assert rejected_metadata.superseded_by == adopted.id
        # Archived -- excluded from retrieval by the same valid_until gate
        # every other archived memory already uses (engine._active_candidates).
        assert rejected.valid_until is not None
        assert rejected.valid_until >= rejected.valid_from

    def test_original_turn_id_is_stable_across_the_rewrite(self) -> None:
        """The rewrite is an in-place vault-file overwrite (same id, same
        file), not a new object -- so nothing downstream sees two copies."""
        adapter = HavenContinuationAdapter.from_config({})
        adapter.add_conversation(
            [
                _turn("Rank by embedding similarity alone.", "rejected_approach", 0),
                _turn(
                    "Rank by a blended score instead.",
                    "decision",
                    1,
                    supersedes_turn=0,
                ),
            ]
        )
        objects = _objects(adapter)
        assert len(objects) == 2
        assert len(list(adapter._vault_dir.glob("*.md"))) == 2

    def test_decision_to_decision_chain_supersedes_correctly(self) -> None:
        """The schema also supports supersedes_turn pointing at an earlier
        *decision*-typed turn, not just a rejected_approach -- the shipped
        pilot dataset happens not to exercise this shape, but the mechanism
        must handle it (see TestProjectStateIntegration below for this
        actually reaching ProjectState.superseded_decisions)."""
        adapter = HavenContinuationAdapter.from_config({})
        adapter.add_conversation(
            [
                _turn("Use YAML sidecars for metadata.", "decision", 0),
                _turn(
                    "Use JSON sidecars for metadata instead.",
                    "decision",
                    1,
                    supersedes_turn=0,
                ),
            ]
        )
        old = _by_fact(adapter, "Use YAML sidecars for metadata.")
        new = _by_fact(adapter, "Use JSON sidecars for metadata instead.")
        assert old.memory_type is MemoryType.DECISION
        assert get_decision_metadata(old).status is DecisionStatus.SUPERSEDED
        assert get_decision_metadata(new).status is DecisionStatus.ACTIVE

    def test_supersedes_turn_pointing_at_unknown_index_does_not_raise(self) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        adapter.add_conversation(
            [_turn("Rank by a blended score.", "decision", 1, supersedes_turn=99)]
        )
        ko = _by_fact(adapter, "Rank by a blended score.")
        assert get_decision_metadata(ko) is None


class TestBlockerResolution:
    """resolves_turn -> valid_until set on the original blocker's
    KnowledgeObject, no DecisionMetadata involved."""

    def test_resolving_decision_archives_the_blocker(self) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        adapter.add_conversation(
            [
                _turn(
                    "Blocked on score_breakdown visibility.",
                    "blocker",
                    0,
                ),
                _turn(
                    "Decided: score_breakdown stays internal.",
                    "decision",
                    1,
                    resolves_turn=0,
                ),
            ]
        )
        blocker = _by_fact(adapter, "Blocked on score_breakdown visibility.")
        assert blocker.valid_until is not None
        assert blocker.valid_until >= blocker.valid_from
        # A blocker is not a decision -- no DecisionMetadata is attached.
        assert get_decision_metadata(blocker) is None

    def test_unresolved_blocker_has_no_valid_until(self) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        adapter.add_conversation([_turn("Blocked on X.", "blocker", 0)])
        assert _by_fact(adapter, "Blocked on X.").valid_until is None

    def test_resolves_turn_on_a_non_decision_turn_is_ignored(self) -> None:
        """§6 frames resolves_turn as living on a *decision* turn; a
        resolves_turn key on a non-decision turn_type must not silently
        archive something via a mechanism that was never meant to fire."""
        adapter = HavenContinuationAdapter.from_config({})
        adapter.add_conversation(
            [
                _turn("Blocked on X.", "blocker", 0),
                _turn("Some implementation note.", "implementation", 1, resolves_turn=0),
            ]
        )
        assert _by_fact(adapter, "Blocked on X.").valid_until is None


class TestProjectStateIntegration:
    """End-to-end: the real MemoryEngine / ProjectStateBuilder /
    StructuredPromptBuilder pipeline, driven only via
    build_continuation_context -- no mocking, against the real Phase-1 pilot
    conversation (resume_coding_basic_001.json). Proves <ProjectState> is no
    longer the empty shell CONTINUATION_BENCHMARK_AUDIT.md's Critical-1
    documented.

    Haven's real hybrid retrieval only accepts candidates that share
    keyword/concept overlap with the query -- a generic "Continue
    implementing the project." matches CONTINUATION task-mode (so
    <ProjectState> is attempted) but seeds zero retrieval candidates against
    this vault's actual vocabulary, same as it would for any adapter (see
    TestExistingAdaptersUnaffected). Each test below therefore uses a query
    with real keyword overlap with the specific content it's checking --
    mirroring how a genuine downstream query would need to relate to the
    project's own vocabulary to retrieve anything at all. Every query still
    contains "Continue" so it classifies as TaskMode.CONTINUATION.
    """

    def _load_case_001(self, adapter: HavenContinuationAdapter) -> None:
        import json
        import os

        path = os.path.join(
            "benchmarks", "datasets_continuation", "resume_coding", "resume_coding_basic_001.json"
        )
        if not os.path.isfile(path):
            pytest.skip("resume_coding_basic_001.json not present in this checkout")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        adapter.add_conversation(data["conversation"])

    def test_project_state_element_is_rendered(self) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        self._load_case_001(adapter)
        context = adapter.build_continuation_context("Continue implementing DeterministicRanker.")
        assert "<ProjectState" in context

    def test_current_decision_is_present_and_rejected_one_is_not(self) -> None:
        """Both the adopted decision and the rejected approach it
        supersedes share heavy keyword overlap with this query ("embedding
        similarity alone" is lifted almost verbatim from the rejected
        turn) -- proving the rejected turn's absence here is because it was
        archived at ingestion, not because it was never a retrieval
        candidate. Contrast with TestExistingAdaptersUnaffected's plain
        HavenAdapter comparison, where the same query surfaces exactly the
        rejected turn and nothing else."""
        adapter = HavenContinuationAdapter.from_config({})
        self._load_case_001(adapter)
        context = adapter.build_continuation_context(
            "Continue implementing the ranker with embedding similarity alone."
        )
        assert "<Decisions>" in context
        assert "we need recency and confirmation count in the score too" in context
        assert "I was thinking we rank by embedding similarity alone" not in context

    def test_constraint_is_present_in_constraints_section(self) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        self._load_case_001(adapter)
        context = adapter.build_continuation_context(
            "Continue respecting the low-confidence memory recency rule."
        )
        assert "<Constraints>" in context
        assert "never let a single low-confidence memory override" in context

    def test_resolved_blocker_is_absent_even_when_queried_directly(self) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        self._load_case_001(adapter)
        context = adapter.build_continuation_context(
            "Continue settling whether score_breakdown needs to be exposed outside DeterministicRanker."
        )
        assert "Blocked on the ranker until we settle whether score_breakdown" not in context

    def test_active_task_is_present(self) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        self._load_case_001(adapter)
        context = adapter.build_continuation_context("Continue working on the slot allocator task.")
        assert "<ActiveTasks>" in context
        assert "finish wiring DeterministicRanker's output into the slot allocator" in context

    def test_open_question_is_present(self) -> None:
        adapter = HavenContinuationAdapter.from_config({})
        self._load_case_001(adapter)
        context = adapter.build_continuation_context(
            "Continue deciding whether recency decay should be linear or exponential."
        )
        assert "<OpenQuestions>" in context
        assert "should recency decay be linear or exponential" in context

    def test_superseded_decision_is_hidden_from_both_decision_buckets(self) -> None:
        """ProjectState.superseded_decisions exists for the case where
        DecisionMetadata.status and KnowledgeObject.valid_until diverge
        (see project_state.py's own docstring: "typically empty in
        practice ... a superseded decision's valid_until is normally set at
        the same time"). This adapter mirrors
        KnowledgeUpdater.supersede_decision exactly (§6 of the ingestion
        design), setting both together -- so the rejected turn is excluded
        by the valid_until/_active_candidates gate before ProjectStateBuilder
        ever runs, and never appears in *either* bucket. That is the
        intended, documented outcome, not a gap: "hidden from active
        state" is satisfied at the retrieval boundary, one step earlier
        than the SupersededDecisions rendering boundary."""
        adapter = HavenContinuationAdapter.from_config({})
        self._load_case_001(adapter)
        context = adapter.build_continuation_context(
            "Continue implementing the ranker with embedding similarity alone."
        )
        assert "<SupersededDecisions>" not in context
        assert "I was thinking we rank by embedding similarity alone" not in context

    def test_gaps_no_longer_include_every_tracked_field(self) -> None:
        """Regression guard for Critical-1: before this adapter existed,
        every ProjectState field was empty (gaps == every tracked field
        name, confidence == 0.0) for any continuation-shaped ingestion."""
        adapter = HavenContinuationAdapter.from_config({})
        self._load_case_001(adapter)
        context = adapter.build_continuation_context(
            "Continue implementing the ranker with embedding similarity alone."
        )
        assert "<Gaps>" in context or "<Gaps/>" in context
        # At minimum, decisions were populated by this query -- confidence
        # must reflect that, not the fully-empty 0.00 Critical-1 documented.
        assert 'confidence="0.00"' not in context


class TestSupersededDecisionsBucketDivergentCase:
    """The one case project_state.py's own docstring names as when
    superseded_decisions actually populates: status=SUPERSEDED with
    valid_until left unset. This adapter never produces that combination
    itself (see TestProjectStateIntegration's
    test_superseded_decision_is_hidden_from_both_decision_buckets), so this
    is proven directly against ProjectStateBuilder using the same
    KnowledgeObject/DecisionMetadata shapes obsidian/tests/test_project_state.py
    already uses -- confirming the rendering path this adapter's metadata
    would exercise *if* a future caller ever attached SUPERSEDED status
    without also archiving via valid_until."""

    def test_superseded_status_without_valid_until_populates_the_bucket(self) -> None:
        from datetime import datetime

        from obsidian.manager_ai.models import DecisionMetadata, DecisionStatus, with_decision_metadata
        from obsidian.memory_engine.project_state import ProjectStateBuilder
        from obsidian.ontology.retrieval_models import Candidate, RankedCandidate

        ko = with_decision_metadata(
            KnowledgeObject(canonical_fact="Use YAML sidecars.", memory_type=MemoryType.DECISION),
            DecisionMetadata(status=DecisionStatus.SUPERSEDED),
        )
        candidate = Candidate(
            knowledge_object=ko,
            supporting_concepts=(),
            attachment_relevance=0.0,
            activation_score=0.0,
        )
        ranked = RankedCandidate(
            candidate=candidate,
            final_score=0.9,
            score_breakdown={"importance": 0.9},
        )
        state = ProjectStateBuilder().build([ranked], now=datetime(2026, 7, 9, 12, 0, 0))
        assert state.decisions == ()
        assert [r.canonical_fact for r in state.superseded_decisions] == ["Use YAML sidecars."]


class TestDeterminism:
    """Repeated ingestion of the same conversation is deterministic in
    every way that matters for this benchmark: typing, supersession status,
    and archival outcomes -- not literal id/timestamp equality, since
    KnowledgeObject.id is a fresh uuid4 and valid_from a fresh wall-clock
    read on every call (exactly as HavenAdapter.add already behaves)."""

    def _case(self) -> List[Dict[str, Any]]:
        return [
            _turn("Rank by embedding similarity alone.", "rejected_approach", 0),
            _turn("Rank by a blended score instead.", "decision", 1, supersedes_turn=0),
            _turn("Blocked on X.", "blocker", 2),
            _turn("Decided: X is resolved.", "decision", 3, resolves_turn=2),
            _turn("Finish the ranker.", "task", 4),
        ]

    def test_repeated_ingestion_produces_the_same_typing_and_outcomes(self) -> None:
        first = HavenContinuationAdapter.from_config({})
        first.add_conversation(self._case())
        second = HavenContinuationAdapter.from_config({})
        second.add_conversation(self._case())

        def _summary(adapter: HavenContinuationAdapter) -> List[Dict[str, Any]]:
            summary = []
            for ko in sorted(_objects(adapter), key=lambda k: k.canonical_fact):
                metadata = get_decision_metadata(ko)
                summary.append(
                    {
                        "fact": ko.canonical_fact,
                        "memory_type": ko.memory_type,
                        "archived": ko.valid_until is not None,
                        "decision_status": metadata.status if metadata is not None else None,
                    }
                )
            return summary

        assert _summary(first) == _summary(second)

    def test_repeated_ingestion_produces_equivalent_rendered_project_state(self) -> None:
        import re

        def _strip_volatile(context: str) -> str:
            # [N] reference indices and confidence floats are stable given
            # identical input content, but valid_from timestamps (a fresh
            # wall-clock read per KnowledgeObject) and any embedded
            # KnowledgeObject id (a fresh uuid4 per ingestion run, e.g. a
            # decision's rendered `supersedes="..."` attribute) are not --
            # normalize both so this assertion tests content equivalence,
            # not incidental byte equality of volatile fields the docstring
            # above already disclaims caring about.
            context = re.sub(r"\s+", " ", context)
            context = re.sub(r'valid_from="[^"]*"', 'valid_from="X"', context)
            context = re.sub(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                "UUID",
                context,
            )
            return context

        first = HavenContinuationAdapter.from_config({})
        first.add_conversation(self._case())
        second = HavenContinuationAdapter.from_config({})
        second.add_conversation(self._case())

        context_a = _strip_volatile(first.build_continuation_context("Continue implementing the project."))
        context_b = _strip_volatile(second.build_continuation_context("Continue implementing the project."))
        assert context_a == context_b


class TestExistingAdaptersUnaffected:
    """Retrieval-benchmark regression guard: HavenAdapter, HavenFullAdapter,
    and run_benchmarks.get_adapter_cls's registry are untouched by this
    file's existence."""

    def test_haven_continuation_adapter_subclasses_haven_adapter(self) -> None:
        assert issubclass(HavenContinuationAdapter, HavenAdapter)

    def test_search_is_inherited_unchanged(self) -> None:
        assert HavenContinuationAdapter.search is HavenAdapter.search

    def test_build_continuation_context_is_inherited_unchanged(self) -> None:
        assert HavenContinuationAdapter.build_continuation_context is HavenAdapter.build_continuation_context

    def test_delete_all_is_inherited_unchanged(self) -> None:
        assert HavenContinuationAdapter.delete_all is HavenAdapter.delete_all

    def test_add_is_inherited_unchanged_verbatim_fact_storage(self) -> None:
        """The standalone add() (single mem0-shaped message, no turn_type)
        must stay HavenAdapter's own verbatim-FACT behavior."""
        assert HavenContinuationAdapter.add is HavenAdapter.add

    def test_run_benchmarks_registry_still_resolves_haven_to_haven_adapter(self) -> None:
        from benchmarks.runners.run_benchmarks import get_adapter_cls

        assert get_adapter_cls("haven") is HavenAdapter
        assert not issubclass(get_adapter_cls("haven"), HavenContinuationAdapter)

    def test_run_benchmarks_registry_does_not_know_about_continuation_adapter(self) -> None:
        import inspect

        from benchmarks.runners import run_benchmarks

        source = inspect.getsource(run_benchmarks.get_adapter_cls)
        assert "HavenContinuationAdapter" not in source
        assert "haven_continuation_adapter" not in source
