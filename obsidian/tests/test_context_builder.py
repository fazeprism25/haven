"""Unit tests for obsidian.memory_engine.context_builder.ContextBuilder.

Test groups
-----------
TestFieldExposure               — canonical_fact/memory_type/confidence/
                                    importance/confirmation_count/validity
                                    dates all appear in the rendered block.
TestNoRetrievalTraceExposure     — RetrievalTrace/score_breakdown/final_score/
                                    supporting_concepts/activation_score/
                                    attachment_relevance never appear in
                                    output or the module namespace.
TestOrderingPreserved            — input order reproduced exactly, even
                                    when final_score would sort otherwise.
TestNumberingLabels               — 1-based position labels match input order.
TestFloatFormatting               — confidence/importance rendered with
                                    fixed 2-decimal precision.
TestDateFormatting                — valid_from/valid_until use isoformat();
                                    None valid_until renders as "none".
TestEmptyInput                    — build([]) == "".
TestSingleCandidate                — no separator artifacts for one block.
TestDeterminism                    — repeated calls on the same input are
                                    byte-identical.
TestNoMutation                     — input list/candidates/KnowledgeObjects
                                    untouched.
TestStatelessReuse                 — one instance, many calls, no leakage.
TestNoOutOfScopeImports            — module never imports retrieval/ranking/
                                    allocation code or RetrievalTrace.
TestDecisionFields                 — DecisionMetadata fields render for
                                    MemoryType.DECISION candidates that have
                                    them, and stay absent otherwise (backward
                                    compatibility with non-decision types and
                                    pre-existing decisions with no metadata).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import (
    DecisionMetadata,
    DecisionStatus,
    KnowledgeObject,
    with_decision_metadata,
)
from obsidian.memory_engine.context_builder import ContextBuilder
from obsidian.ontology.retrieval_models import ActivatedConcept, Candidate, RankedCandidate

NOW = datetime(2026, 7, 2, 12, 0, 0)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------


def make_ko(
    fact: str = "Haven uses Claude",
    ko_id: Optional[UUID] = None,
    memory_type: MemoryType = MemoryType.FACT,
    confidence: float = 0.5,
    importance: float = 0.5,
    confirmation_count: int = 0,
    valid_from: datetime = NOW,
    valid_until: Optional[datetime] = None,
) -> KnowledgeObject:
    return KnowledgeObject(
        id=ko_id if ko_id is not None else uuid4(),
        canonical_fact=fact,
        memory_type=memory_type,
        confidence=confidence,
        importance=importance,
        confirmation_count=confirmation_count,
        valid_from=valid_from,
        valid_until=valid_until,
    )


def make_activated_concept() -> ActivatedConcept:
    cid = uuid4()
    return ActivatedConcept(
        concept_id=cid, activation_score=1.0, activation_depth=0, source_seed=cid
    )


def make_candidate(
    ko: Optional[KnowledgeObject] = None,
    attachment_relevance: float = 0.5,
    activation_score: float = 0.5,
) -> Candidate:
    return Candidate(
        knowledge_object=ko if ko is not None else make_ko(),
        supporting_concepts=(make_activated_concept(),),
        attachment_relevance=attachment_relevance,
        activation_score=activation_score,
    )


def make_ranked(
    ko: Optional[KnowledgeObject] = None,
    final_score: float = 0.5,
) -> RankedCandidate:
    return RankedCandidate(
        candidate=make_candidate(ko=ko),
        final_score=final_score,
        score_breakdown={"activation": final_score},
    )


# ---------------------------------------------------------------------------
# TestFieldExposure
# ---------------------------------------------------------------------------


class TestFieldExposure:
    def test_canonical_fact_present(self) -> None:
        ko = make_ko(fact="Haven is a personal second brain")
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert "Haven is a personal second brain" in context

    def test_canonical_fact_embedded_verbatim_including_newlines(self) -> None:
        # canonical_fact is treated as opaque (see "No markdown parsing" in
        # the module docstring) -- embedded newlines are preserved exactly,
        # not stripped, escaped, or rewritten.
        ko = make_ko(fact="line one\nline two")
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert "line one\nline two" in context

    def test_memory_type_present(self) -> None:
        ko = make_ko(memory_type=MemoryType.PREFERENCE)
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert "preference" in context

    def test_confidence_present(self) -> None:
        ko = make_ko(confidence=0.73)
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert "0.73" in context

    def test_importance_present(self) -> None:
        ko = make_ko(importance=0.42)
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert "0.42" in context

    def test_confirmation_count_present(self) -> None:
        # A bare "7" in context would also match the "07" in NOW's
        # isoformat() date string, so assert the labelled substring to
        # actually pin down confirmation_count's rendering.
        ko = make_ko(confirmation_count=7)
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert "confirmations: 7" in context

    def test_valid_from_present(self) -> None:
        ko = make_ko(valid_from=NOW)
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert NOW.isoformat() in context

    def test_valid_until_present_when_set(self) -> None:
        until = datetime(2026, 12, 31, 0, 0, 0)
        ko = make_ko(valid_until=until)
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert until.isoformat() in context

    def test_valid_until_none_renders_placeholder(self) -> None:
        ko = make_ko(valid_until=None)
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert "valid_until: none" in context


# ---------------------------------------------------------------------------
# TestNoRetrievalTraceExposure
# ---------------------------------------------------------------------------


class TestNoRetrievalTraceExposure:
    def test_final_score_not_rendered(self) -> None:
        ko = make_ko()
        context = ContextBuilder().build([make_ranked(ko=ko, final_score=0.123456)])
        assert "0.123456" not in context
        assert "final_score" not in context

    def test_score_breakdown_not_rendered(self) -> None:
        ko = make_ko()
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert "score_breakdown" not in context
        assert "activation" not in context

    def test_supporting_concepts_not_rendered(self) -> None:
        ko = make_ko()
        rc = make_ranked(ko=ko)
        concept_id = str(rc.candidate.supporting_concepts[0].concept_id)
        context = ContextBuilder().build([rc])
        assert concept_id not in context
        assert "supporting_concepts" not in context

    def test_attachment_relevance_and_activation_score_not_rendered(self) -> None:
        ko = make_ko()
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert "attachment_relevance" not in context
        assert "activation_score" not in context

    def test_knowledge_object_id_not_rendered(self) -> None:
        ko_id = uuid4()
        ko = make_ko(ko_id=ko_id)
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert str(ko_id) not in context

    def test_module_does_not_bind_retrieval_trace(self) -> None:
        import obsidian.memory_engine.context_builder as module

        assert "RetrievalTrace" not in dir(module)


# ---------------------------------------------------------------------------
# TestOrderingPreserved
# ---------------------------------------------------------------------------


class TestOrderingPreserved:
    def test_output_order_matches_input_order_not_score(self) -> None:
        low = make_ranked(ko=make_ko(fact="low score fact"), final_score=0.1)
        high = make_ranked(ko=make_ko(fact="high score fact"), final_score=0.9)

        # Deliberately pass the lower-scored candidate first.
        context = ContextBuilder().build([low, high])

        assert context.index("low score fact") < context.index("high score fact")

    def test_reversed_input_reverses_output(self) -> None:
        a = make_ranked(ko=make_ko(fact="fact A"))
        b = make_ranked(ko=make_ko(fact="fact B"))

        forward = ContextBuilder().build([a, b])
        backward = ContextBuilder().build([b, a])

        assert forward.index("fact A") < forward.index("fact B")
        assert backward.index("fact B") < backward.index("fact A")


# ---------------------------------------------------------------------------
# TestNumberingLabels
# ---------------------------------------------------------------------------


class TestNumberingLabels:
    def test_labels_are_one_based_and_sequential(self) -> None:
        candidates = [make_ranked(ko=make_ko(fact=f"fact {i}")) for i in range(3)]
        context = ContextBuilder().build(candidates)
        assert context.startswith("[1] fact 0")
        assert "[2] fact 1" in context
        assert "[3] fact 2" in context


# ---------------------------------------------------------------------------
# TestFloatFormatting
# ---------------------------------------------------------------------------


class TestFloatFormatting:
    def test_confidence_and_importance_use_two_decimal_places(self) -> None:
        ko = make_ko(confidence=1.0 / 3.0, importance=2.0 / 3.0)
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert "confidence: 0.33" in context
        assert "importance: 0.67" in context

    def test_exact_values_still_have_two_decimals(self) -> None:
        ko = make_ko(confidence=1.0, importance=0.0)
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert "confidence: 1.00" in context
        assert "importance: 0.00" in context


# ---------------------------------------------------------------------------
# TestDateFormatting
# ---------------------------------------------------------------------------


class TestDateFormatting:
    def test_valid_from_uses_isoformat(self) -> None:
        valid_from = datetime(2025, 3, 14, 9, 26, 53)
        ko = make_ko(valid_from=valid_from)
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert f"valid_from: {valid_from.isoformat()}" in context

    def test_valid_until_uses_isoformat_when_present(self) -> None:
        valid_until = datetime(2025, 4, 1, 0, 0, 0)
        ko = make_ko(valid_until=valid_until)
        context = ContextBuilder().build([make_ranked(ko=ko)])
        assert f"valid_until: {valid_until.isoformat()}" in context


# ---------------------------------------------------------------------------
# TestEmptyInput
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_build_empty_list_returns_empty_string(self) -> None:
        assert ContextBuilder().build([]) == ""


# ---------------------------------------------------------------------------
# TestSingleCandidate
# ---------------------------------------------------------------------------


class TestSingleCandidate:
    def test_no_leading_or_trailing_separator(self) -> None:
        context = ContextBuilder().build([make_ranked(ko=make_ko(fact="solo fact"))])
        assert not context.startswith("\n")
        assert not context.endswith("\n")
        assert "\n\n" not in context


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_produces_byte_identical_output(self) -> None:
        candidates = [make_ranked(ko=make_ko(fact=f"fact {i}")) for i in range(5)]
        builder = ContextBuilder()
        first = builder.build(candidates)
        second = builder.build(candidates)
        assert first == second

    def test_two_block_separator_is_single_blank_line(self) -> None:
        a = make_ranked(ko=make_ko(fact="fact A"))
        b = make_ranked(ko=make_ko(fact="fact B"))
        context = ContextBuilder().build([a, b])
        assert "\n\n" in context
        assert "\n\n\n" not in context


# ---------------------------------------------------------------------------
# TestNoMutation
# ---------------------------------------------------------------------------


class TestNoMutation:
    def test_input_list_and_candidates_untouched(self) -> None:
        candidates = [make_ranked(ko=make_ko(fact=f"fact {i}")) for i in range(3)]
        # RankedCandidate/Candidate/KnowledgeObject are frozen dataclasses
        # (with a MappingProxyType field), so a shallow copy of the list is
        # enough to detect either replacement or mutation of an entry —
        # matching the convention used by the sibling ranker/allocator tests.
        snapshot = list(candidates)

        ContextBuilder().build(candidates)

        assert candidates == snapshot

    def test_knowledge_object_fields_unchanged_after_build(self) -> None:
        ko = make_ko(fact="immutable fact", confidence=0.6, importance=0.4)
        rc = make_ranked(ko=ko)

        ContextBuilder().build([rc])

        assert rc.candidate.knowledge_object.canonical_fact == "immutable fact"
        assert rc.candidate.knowledge_object.confidence == 0.6
        assert rc.candidate.knowledge_object.importance == 0.4


# ---------------------------------------------------------------------------
# TestStatelessReuse
# ---------------------------------------------------------------------------


class TestStatelessReuse:
    def test_same_instance_reused_across_calls(self) -> None:
        builder = ContextBuilder()
        first = builder.build([make_ranked(ko=make_ko(fact="alpha"))])
        second = builder.build([make_ranked(ko=make_ko(fact="beta"))])
        assert "alpha" in first
        assert "alpha" not in second
        assert "beta" in second


# ---------------------------------------------------------------------------
# TestNoOutOfScopeImports
# ---------------------------------------------------------------------------


class TestNoOutOfScopeImports:
    def test_module_does_not_bind_retrieval_ranking_or_allocation_names(self) -> None:
        """The module's docstring *discusses* DeterministicRanker/
        DeterministicSlotAllocator/MemoryStore/ConceptGraph/RetrievalTrace as
        out-of-scope context, but must never actually import or bind them —
        checked against the module namespace rather than raw source text so
        prose mentions don't produce false positives."""
        import obsidian.memory_engine.context_builder as module

        forbidden = {
            "DeterministicRanker",
            "DeterministicSlotAllocator",
            "MemoryStore",
            "ConceptGraph",
            "RetrievalTrace",
            "ActivationSpreader",
            "CandidateAssembler",
        }
        assert forbidden.isdisjoint(dir(module))


# ---------------------------------------------------------------------------
# TestDecisionFields
# ---------------------------------------------------------------------------


class TestDecisionFields:
    def test_decision_fields_render_when_present(self) -> None:
        ko = with_decision_metadata(
            make_ko(fact="Use Qdrant.", memory_type=MemoryType.DECISION),
            DecisionMetadata(
                reason="Better filtering support.",
                alternatives_considered=["Chroma", "Pinecone"],
                status=DecisionStatus.ACTIVE,
            ),
        )
        context = ContextBuilder().build([make_ranked(ko=ko)])

        assert "status: active" in context
        assert "reason: Better filtering support." in context
        assert "alternatives_considered: Chroma, Pinecone" in context

    def test_supersedes_and_superseded_by_render_when_set(self) -> None:
        old_id, new_id = uuid4(), uuid4()
        ko = with_decision_metadata(
            make_ko(memory_type=MemoryType.DECISION),
            DecisionMetadata(
                status=DecisionStatus.SUPERSEDED,
                supersedes=old_id,
                superseded_by=new_id,
            ),
        )
        context = ContextBuilder().build([make_ranked(ko=ko)])

        assert f"supersedes: {old_id}" in context
        assert f"superseded_by: {new_id}" in context

    def test_no_decision_metadata_renders_unchanged_block(self) -> None:
        # A MemoryType.DECISION candidate with no DecisionMetadata attached
        # (e.g. written before Decision Memory existed) must render
        # byte-identical to the plain six-field block.
        ko = make_ko(fact="Build Manager AI first.", memory_type=MemoryType.DECISION)
        with_metadata = ContextBuilder().build([make_ranked(ko=ko)])

        plain_ko = make_ko(fact="Build Manager AI first.", memory_type=MemoryType.DECISION)
        without_metadata = ContextBuilder().build([make_ranked(ko=plain_ko)])

        assert with_metadata == without_metadata
        assert "status:" not in with_metadata
        assert "reason:" not in with_metadata

    def test_non_decision_type_never_renders_decision_fields(self) -> None:
        # DecisionMetadata is meaningless on a non-DECISION KnowledgeObject;
        # even if metadata["decision"] were somehow present, it must not
        # leak into a FACT/PREFERENCE/etc. block.
        ko = with_decision_metadata(
            make_ko(fact="Plain fact.", memory_type=MemoryType.FACT),
            DecisionMetadata(reason="Should not render."),
        )
        context = ContextBuilder().build([make_ranked(ko=ko)])

        assert "reason:" not in context
        assert "Should not render." not in context

    def test_empty_reason_and_alternatives_are_omitted(self) -> None:
        ko = with_decision_metadata(
            make_ko(memory_type=MemoryType.DECISION),
            DecisionMetadata(status=DecisionStatus.ACTIVE),
        )
        context = ContextBuilder().build([make_ranked(ko=ko)])

        assert "status: active" in context
        assert "reason:" not in context
        assert "alternatives_considered:" not in context
        assert "supersedes:" not in context
        assert "superseded_by:" not in context
