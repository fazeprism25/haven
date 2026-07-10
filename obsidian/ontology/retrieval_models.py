"""Retrieval data models for Haven Ontology-aware candidate retrieval.

This module implements ONLY the data model layer for the future
Concept-aware CandidateRetriever described in
``docs/architecture/ONTOLOGY_SPEC.md``.  No activation spreading, ranking,
or evidence-collection logic lives here — only the immutable value objects
those stages will pass to one another.

Model hierarchy
---------------
::

    ActivatedConcept – a Concept reached by activation spreading, with its
                        propagated score, hop distance, and originating seed.
    Candidate        – a KnowledgeObject proposed for retrieval, together
                        with the ActivatedConcepts that support it, if any
                        (empty for a keyword-only match with no ontology
                        evidence).
    RankedCandidate   – a Candidate wrapped with its final composite score
                        and a breakdown of the terms that produced it.
    RetrievalTrace    – an immutable record of one retrieval run, intended
                        only for debugging and benchmarking.  Never exposed
                        to the LLM.
    ContextPlanTrace  – a plain-primitive diagnostic projection of a
                        ``ContextPlan`` (Phase 1.5, observational only),
                        carried on ``RetrievalTrace.context_plan``.
    CoverageReportTrace – a plain-primitive diagnostic projection of a
                        ``CoverageReport`` (Phase 2, observational only),
                        carried on ``RetrievalTrace.coverage``.
    GapRecoveryTrace  – a plain-primitive diagnostic projection of a
                        ``GapRecoveryDecision`` (Phase 4, observational
                        only), carried on ``RetrievalTrace.gap_recovery``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Tuple
from uuid import UUID

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject


# ---------------------------------------------------------------------------
# ActivatedConcept
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivatedConcept:
    """A Concept reached by activation spreading from one or more seed Concepts.

    Parameters
    ----------
    concept_id : UUID
        The ``id`` of the :class:`~obsidian.ontology.models.Concept` that was
        activated.
    activation_score : float
        The propagated activation strength (0.0 – 1.0).  Seed concepts
        (depth 0) typically start at ``1.0``; activation decays with each
        hop of propagation.
    activation_depth : int
        Number of relationship hops from the originating seed concept.
        ``0`` means the concept was itself a seed (resolved directly from
        the query).
    source_seed : UUID
        The ``id`` of the seed :class:`~obsidian.ontology.models.Concept`
        from which this activation ultimately propagated.  Equal to
        ``concept_id`` when ``activation_depth`` is ``0``.

    Raises
    ------
    ValueError
        If *activation_score* is outside ``[0.0, 1.0]``.
    ValueError
        If *activation_depth* is negative.

    Examples
    --------
    >>> from uuid import uuid4
    >>> seed = uuid4()
    >>> ActivatedConcept(concept_id=seed, activation_score=1.0, activation_depth=0, source_seed=seed)
    ... # doctest: +SKIP
    """

    concept_id: UUID
    activation_score: float
    activation_depth: int
    source_seed: UUID

    def __post_init__(self) -> None:
        if not 0.0 <= self.activation_score <= 1.0:
            raise ValueError(
                f"activation_score must be in [0.0, 1.0]; got {self.activation_score}"
            )
        if self.activation_depth < 0:
            raise ValueError(
                f"activation_depth must be >= 0; got {self.activation_depth}"
            )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "concept_id": str(self.concept_id),
            "activation_score": self.activation_score,
            "activation_depth": self.activation_depth,
            "source_seed": str(self.source_seed),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ActivatedConcept":
        """Reconstruct an :class:`ActivatedConcept` from a serialised dictionary."""
        return cls(
            concept_id=UUID(data["concept_id"]),
            activation_score=data["activation_score"],
            activation_depth=data["activation_depth"],
            source_seed=UUID(data["source_seed"]),
        )


# ---------------------------------------------------------------------------
# Candidate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """A KnowledgeObject proposed for retrieval, with its supporting evidence.

    Ontology evidence is optional. A ``Candidate`` produced from a direct
    Concept attachment or activation spreading carries one or more
    ``ActivatedConcept`` entries in ``supporting_concepts``. A ``Candidate``
    produced from a keyword-only match (no resolved Concept, no attachment)
    has none — ``supporting_concepts=()``, ``attachment_relevance=0.0``,
    ``activation_score=0.0`` — which is the honest representation of "no
    ontology evidence was found", not a fabricated one. Use
    :attr:`has_ontology_evidence` to tell the two cases apart.

    Parameters
    ----------
    knowledge_object : KnowledgeObject
        The candidate's underlying evidence object.
    supporting_concepts : tuple[ActivatedConcept, ...]
        The ActivatedConcepts whose attachments or propagation produced
        this candidate. May be empty for a keyword-only candidate; see
        :attr:`has_ontology_evidence`.
    attachment_relevance : float
        Strength of the strongest direct attachment linking
        ``knowledge_object`` to a supporting concept (0.0 – 1.0). ``0.0``
        when ``supporting_concepts`` is empty.
    activation_score : float
        Aggregate activation strength contributed by ``supporting_concepts``
        (0.0 – 1.0). ``0.0`` when ``supporting_concepts`` is empty.
    retrieval_metadata : Mapping[str, Any]
        Arbitrary, read-only diagnostic key-value data attached during
        retrieval (e.g. which retrieval pass produced this candidate).
        Stored as an immutable :class:`~types.MappingProxyType`.
    keyword_overlap_score : float
        Strength of the keyword-path match between the query and
        ``knowledge_object.canonical_fact`` (0.0 – 1.0), computed by
        :class:`~obsidian.memory_engine.keyword_candidate_retriever.KeywordCandidateRetriever`.
        ``0.0`` when the keyword path found no overlap for this candidate
        (including candidates found only via the ontology path).
        Independent of ``attachment_relevance``/``activation_score`` the
        same way those two are independent of each other — it measures
        evidence from a different retrieval path entirely, never merged
        into either.

    Raises
    ------
    ValueError
        If *attachment_relevance*, *activation_score*, or
        *keyword_overlap_score* are outside ``[0.0, 1.0]``.
    """

    knowledge_object: KnowledgeObject
    supporting_concepts: Tuple[ActivatedConcept, ...]
    attachment_relevance: float
    activation_score: float
    retrieval_metadata: Mapping[str, Any] = field(default_factory=dict)
    keyword_overlap_score: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.attachment_relevance <= 1.0:
            raise ValueError(
                f"attachment_relevance must be in [0.0, 1.0]; got {self.attachment_relevance}"
            )
        if not 0.0 <= self.activation_score <= 1.0:
            raise ValueError(
                f"activation_score must be in [0.0, 1.0]; got {self.activation_score}"
            )
        if not 0.0 <= self.keyword_overlap_score <= 1.0:
            raise ValueError(
                f"keyword_overlap_score must be in [0.0, 1.0]; got {self.keyword_overlap_score}"
            )
        # Freeze mutable inputs so the dataclass is genuinely immutable.
        object.__setattr__(
            self, "supporting_concepts", tuple(self.supporting_concepts)
        )
        object.__setattr__(
            self, "retrieval_metadata", MappingProxyType(dict(self.retrieval_metadata))
        )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def has_ontology_evidence(self) -> bool:
        """Return ``True`` if at least one Concept supports this candidate.

        ``False`` for a keyword-only candidate — one whose
        ``knowledge_object`` was found only by token overlap with the
        query, with no resolved Concept or activation-spreading evidence
        behind it.
        """
        return bool(self.supporting_concepts)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "knowledge_object": self.knowledge_object.to_dict(),
            "supporting_concepts": [
                c.to_dict() for c in self.supporting_concepts
            ],
            "attachment_relevance": self.attachment_relevance,
            "activation_score": self.activation_score,
            "retrieval_metadata": dict(self.retrieval_metadata),
            "keyword_overlap_score": self.keyword_overlap_score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Candidate":
        """Reconstruct a :class:`Candidate` from a serialised dictionary."""
        return cls(
            knowledge_object=KnowledgeObject.from_dict(data["knowledge_object"]),
            supporting_concepts=tuple(
                ActivatedConcept.from_dict(c) for c in data["supporting_concepts"]
            ),
            attachment_relevance=data["attachment_relevance"],
            activation_score=data["activation_score"],
            retrieval_metadata=data.get("retrieval_metadata", {}),
            keyword_overlap_score=data.get("keyword_overlap_score", 0.0),
        )


# ---------------------------------------------------------------------------
# RankedCandidate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankedCandidate:
    """A :class:`Candidate` wrapped with its final composite ranking score.

    Instances sort deterministically by descending ``final_score``, with
    ties broken by the candidate's ``knowledge_object.id`` (ascending) so
    that ordering never depends on insertion order or dict/set iteration.

    Parameters
    ----------
    candidate : Candidate
        The underlying candidate being ranked.
    final_score : float
        The final composite ranking score (0.0 – 1.0).
    score_breakdown : Mapping[str, float]
        Named contributions that summed/combined into ``final_score``
        (e.g. ``{"activation": 0.4, "importance": 0.3, ...}``). Must not
        be empty. Stored as an immutable :class:`~types.MappingProxyType`.

    Raises
    ------
    ValueError
        If *final_score* is outside ``[0.0, 1.0]``.
    ValueError
        If *score_breakdown* is empty.

    Examples
    --------
    >>> ranked = sorted(ranked_candidates)  # doctest: +SKIP
    >>> ranked[0].final_score >= ranked[-1].final_score  # doctest: +SKIP
    True
    """

    candidate: Candidate
    final_score: float
    score_breakdown: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.final_score <= 1.0:
            raise ValueError(
                f"final_score must be in [0.0, 1.0]; got {self.final_score}"
            )
        if not self.score_breakdown:
            raise ValueError("score_breakdown must not be empty")
        object.__setattr__(
            self, "score_breakdown", MappingProxyType(dict(self.score_breakdown))
        )

    # ------------------------------------------------------------------
    # Deterministic ordering
    # ------------------------------------------------------------------

    @property
    def _sort_key(self) -> Tuple[float, str]:
        """Sort key: descending score, then ascending KnowledgeObject id."""
        return (-self.final_score, str(self.candidate.knowledge_object.id))

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RankedCandidate):
            return NotImplemented
        return self._sort_key < other._sort_key

    def __le__(self, other: object) -> bool:
        if not isinstance(other, RankedCandidate):
            return NotImplemented
        return self._sort_key <= other._sort_key

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, RankedCandidate):
            return NotImplemented
        return self._sort_key > other._sort_key

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, RankedCandidate):
            return NotImplemented
        return self._sort_key >= other._sort_key

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "candidate": self.candidate.to_dict(),
            "final_score": self.final_score,
            "score_breakdown": dict(self.score_breakdown),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RankedCandidate":
        """Reconstruct a :class:`RankedCandidate` from a serialised dictionary."""
        return cls(
            candidate=Candidate.from_dict(data["candidate"]),
            final_score=data["final_score"],
            score_breakdown=data.get("score_breakdown", {}),
        )


# ---------------------------------------------------------------------------
# CandidateTrace
# ---------------------------------------------------------------------------


#: Rejection reasons a :class:`CandidateTrace` may carry. A candidate is
#: rejected by one of the five ``AcceptanceStage`` stages
#: (``obsidian.memory_engine.acceptance_stage``) — absolute floor,
#: abstention, score-gap cut, relative floor, or acceptance cap — or,
#: having survived acceptance, because it fell outside
#: ``RetrievalConfig.max_results`` (the Slot Allocator's budget).
REJECTION_BELOW_MINIMUM_SCORE = "below_minimum_score"
REJECTION_BELOW_ABSTENTION_FLOOR = "below_abstention_floor"
REJECTION_SCORE_GAP_CUT = "score_gap_cut"
REJECTION_BELOW_RELATIVE_FLOOR = "below_relative_floor"
REJECTION_ACCEPTANCE_CAP_EXCEEDED = "acceptance_cap_exceeded"
REJECTION_SLOT_BUDGET_EXCEEDED = "slot_budget_exceeded"


@dataclass(frozen=True)
class CandidateTrace:
    """Diagnostic record of one memory considered during a single retrieval run.

    Every candidate that reached the Ranker is represented exactly once —
    whether or not it ultimately appeared in the returned context — so this
    is the "why was this (or wasn't this) retrieved" record for a single
    memory. It carries no field the ranking algorithm doesn't already
    compute; it only preserves values that :class:`DeterministicRanker`
    and :class:`DeterministicSlotAllocator` would otherwise discard once a
    candidate is filtered or falls outside the context budget.

    Parameters
    ----------
    knowledge_object_id : UUID
        Identity of the underlying ``KnowledgeObject``.
    canonical_fact : str
        The fact text, copied from the ``KnowledgeObject`` for a
        self-contained diagnostic record.
    memory_type : MemoryType
        The ``KnowledgeObject``'s semantic category.
    matched_by_keyword : bool
        Whether ``KeywordCandidateRetriever`` found this memory by token
        overlap with the query (or any query, when multi-query expansion
        is enabled). See ``keyword_overlap_score`` for the strength of
        that match.
    matched_by_ontology : bool
        Whether the ontology path (``QueryResolver`` /
        ``ActivationSpreader`` / ``CandidateAssembler``) found this
        memory. Equivalent to ``Candidate.has_ontology_evidence`` for the
        merged, possibly multi-query, retrieval.
    activation_score : float
        Propagated graph activation strength contributing to this
        candidate (0.0 if no ontology evidence was found).
    attachment_relevance : float
        Strength of the strongest direct Concept attachment (0.0 if no
        ontology evidence was found).
    keyword_overlap_score : float
        Strength of the keyword-path match (0.0 – 1.0; 0.0 when
        ``matched_by_keyword`` is ``False``), computed by
        :class:`~obsidian.memory_engine.keyword_candidate_retriever.KeywordCandidateRetriever`
        from overlapping-token count, token rarity, and exact-phrase
        containment. Copied straight from ``Candidate.keyword_overlap_score``.
    importance : float
        The ``KnowledgeObject``'s importance score.
    confidence : float
        The ``KnowledgeObject``'s confidence score.
    final_score : float
        The composite score used for acceptance, ranking, and allocation --
        i.e. ``base_score + category_preference_bonus``, clamped to
        ``[0.0, 1.0]`` (see those two fields). Equal to
        :class:`DeterministicRanker`'s own composite score whenever
        ``category_preference_bonus`` is ``0.0``, which is every candidate
        whose category the query's :class:`~obsidian.memory_engine.context_planner.ContextPlan`
        did not request (see
        :mod:`~obsidian.memory_engine.category_preference`'s module
        docstring, "Phase 3").
    accepted : bool
        Whether this candidate appears in the context that was actually
        returned (i.e. survived ``AcceptanceStage`` and the slot budget).
    rejection_reason : str, optional
        One of the ``REJECTION_*`` constants above when ``accepted`` is
        ``False``; ``None`` when ``accepted`` is ``True``.
    final_rank : int
        1-based position in the fully scored, descending-``final_score``
        ranking — assigned to every candidate, accepted or not, so a
        rejected candidate's "would-be rank" is still visible.
    threshold_used : float, optional
        The configured threshold value compared against to reject this
        candidate (e.g. ``minimum_candidate_score``, ``abstention_score``,
        ``min_gap``, the computed relative floor, ``acceptance_max_k``, or
        ``max_results``, depending on ``rejection_reason``). ``None`` when
        ``accepted`` is ``True`` — no threshold caused a rejection.
    score_gap : float, optional
        ``final_score`` minus the next candidate's ``final_score`` in the
        full descending ranking (the local drop immediately below this
        candidate). ``None`` for the lowest-ranked candidate, which has no
        next candidate to compare against.
    relative_score : float
        ``final_score`` divided by the top ``final_score`` across every
        candidate considered for this query (``0.0`` when every candidate
        scored ``0.0``). Always populated, accepted or not.
    abstained : bool
        ``True`` when this candidate was rejected because the entire
        query's best candidate failed to clear ``abstention_score`` (the
        whole query abstained); ``False`` otherwise, including when
        ``accepted`` is ``True``.
    score_breakdown : Mapping[str, float]
        The named ``final_score`` contributions computed by
        :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`
        for this candidate (keys: ``"activation"``, ``"attachment_relevance"``,
        ``"keyword_overlap"``, ``"importance"``, ``"confidence"``,
        ``"recency"``, ``"confirmation_count"`` — see that module's
        docstring). Copied straight from ``RankedCandidate.score_breakdown``;
        this field adds no ranking logic of its own, it only stops
        discarding a value the ranker already computed. Empty when not
        supplied (e.g. hand-built traces in tests).
    base_score : float, optional
        The composite score :class:`DeterministicRanker` computed for this
        candidate, *before* any category-preference bonus was added --
        i.e. what ``final_score`` would have been under Phase 1/1.5/2
        behavior. ``None`` only for traces built without running
        :class:`~obsidian.memory_engine.category_preference.CategoryPreferenceScorer`
        (e.g. hand-built traces in tests, or traces deserialised from a
        payload predating this field); every trace
        :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
        produces always populates it, whether or not a bonus was applied.
        When ``None``, callers should treat it as equal to ``final_score``.
    category_preference_bonus : float
        The bonus :class:`~obsidian.memory_engine.category_preference.CategoryPreferenceScorer`
        added toward ``final_score`` because this candidate's category was
        requested by the query's
        :class:`~obsidian.memory_engine.context_planner.ContextPlan``.
        ``0.0`` when no bonus applied -- either because this candidate's
        category was not requested, its ``memory_type`` has no category
        mapping, or (the default) no category-preference stage ran at all.
        Never negative; see
        :data:`~obsidian.memory_engine.category_preference.CATEGORY_PREFERENCE_BONUS`
        for its fixed maximum.

    Raises
    ------
    ValueError
        If any score is outside ``[0.0, 1.0]``, if ``final_rank`` is less
        than 1, or if ``accepted``/``rejection_reason`` are inconsistent
        with each other.
    """

    knowledge_object_id: UUID
    canonical_fact: str
    memory_type: MemoryType
    matched_by_keyword: bool
    matched_by_ontology: bool
    activation_score: float
    attachment_relevance: float
    keyword_overlap_score: float
    importance: float
    confidence: float
    final_score: float
    accepted: bool
    rejection_reason: Optional[str]
    final_rank: int
    threshold_used: Optional[float] = None
    score_gap: Optional[float] = None
    relative_score: float = 0.0
    abstained: bool = False
    score_breakdown: Mapping[str, float] = field(default_factory=dict)
    base_score: Optional[float] = None
    category_preference_bonus: float = 0.0

    def __post_init__(self) -> None:
        for field_name in (
            "activation_score",
            "attachment_relevance",
            "keyword_overlap_score",
            "importance",
            "confidence",
            "final_score",
            "relative_score",
        ):
            value = getattr(self, field_name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be in [0.0, 1.0]; got {value}")
        if self.final_rank < 1:
            raise ValueError(f"final_rank must be >= 1; got {self.final_rank}")
        if self.accepted and self.rejection_reason is not None:
            raise ValueError("accepted candidate must not carry a rejection_reason")
        if not self.accepted and self.rejection_reason is None:
            raise ValueError("rejected candidate must carry a rejection_reason")
        if self.score_gap is not None and self.score_gap < 0.0:
            raise ValueError(f"score_gap must be >= 0.0; got {self.score_gap}")
        if self.abstained and self.rejection_reason != REJECTION_BELOW_ABSTENTION_FLOOR:
            raise ValueError(
                "abstained candidate must carry REJECTION_BELOW_ABSTENTION_FLOOR"
            )
        if self.base_score is not None and not 0.0 <= self.base_score <= 1.0:
            raise ValueError(f"base_score must be in [0.0, 1.0]; got {self.base_score}")
        if not 0.0 <= self.category_preference_bonus <= 1.0:
            raise ValueError(
                "category_preference_bonus must be in [0.0, 1.0]; got "
                f"{self.category_preference_bonus}"
            )
        object.__setattr__(
            self, "score_breakdown", MappingProxyType(dict(self.score_breakdown))
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "knowledge_object_id": str(self.knowledge_object_id),
            "canonical_fact": self.canonical_fact,
            "memory_type": self.memory_type.value,
            "matched_by_keyword": self.matched_by_keyword,
            "matched_by_ontology": self.matched_by_ontology,
            "activation_score": self.activation_score,
            "attachment_relevance": self.attachment_relevance,
            "keyword_overlap_score": self.keyword_overlap_score,
            "importance": self.importance,
            "confidence": self.confidence,
            "final_score": self.final_score,
            "accepted": self.accepted,
            "rejection_reason": self.rejection_reason,
            "final_rank": self.final_rank,
            "threshold_used": self.threshold_used,
            "score_gap": self.score_gap,
            "relative_score": self.relative_score,
            "abstained": self.abstained,
            "score_breakdown": dict(self.score_breakdown),
            "base_score": self.base_score,
            "category_preference_bonus": self.category_preference_bonus,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CandidateTrace":
        """Reconstruct a :class:`CandidateTrace` from a serialised dictionary."""
        return cls(
            knowledge_object_id=UUID(data["knowledge_object_id"]),
            canonical_fact=data["canonical_fact"],
            memory_type=MemoryType(data["memory_type"]),
            matched_by_keyword=data["matched_by_keyword"],
            matched_by_ontology=data["matched_by_ontology"],
            activation_score=data["activation_score"],
            attachment_relevance=data["attachment_relevance"],
            keyword_overlap_score=data.get("keyword_overlap_score", 0.0),
            importance=data["importance"],
            confidence=data["confidence"],
            final_score=data["final_score"],
            accepted=data["accepted"],
            rejection_reason=data.get("rejection_reason"),
            final_rank=data["final_rank"],
            threshold_used=data.get("threshold_used"),
            score_gap=data.get("score_gap"),
            relative_score=data.get("relative_score", 0.0),
            abstained=data.get("abstained", False),
            score_breakdown=data.get("score_breakdown", {}),
            base_score=data.get("base_score"),
            category_preference_bonus=data.get("category_preference_bonus", 0.0),
        )


# ---------------------------------------------------------------------------
# RetrievalPipelineStats
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievalPipelineStats:
    """Aggregate counters and timing for a single retrieval run.

    Parameters
    ----------
    total_ontology_candidates : int
        Sum, across every query actually retrieved for (the original
        query, plus one per rewrite when multi-query expansion is
        enabled), of how many candidates the ontology path
        (``CandidateAssembler``) produced. A memory found via two
        different queries is counted twice here; see
        ``total_merged_candidates`` for the deduplicated count.
    total_keyword_candidates : int
        Same as ``total_ontology_candidates``, for the keyword path
        (``KeywordCandidateRetriever``).
    total_merged_candidates : int
        Number of unique ``KnowledgeObject`` candidates remaining after
        deduplication across paths and (when enabled) across queries —
        i.e. the number of candidates handed to the Ranker.
    total_accepted_candidates : int
        Number of candidates that appear in the returned context (cleared
        both the score cutoff and the slot budget).
    total_rejected_candidates : int
        ``total_merged_candidates - total_accepted_candidates``.
    final_context_size : int
        Character length of the context string actually returned.
    retrieval_latency_ms : float
        Wall-clock time spent in the retrieval pipeline (query rewriting
        through context building), in milliseconds.

    Raises
    ------
    ValueError
        If any count is negative or ``retrieval_latency_ms`` is negative.
    """

    total_ontology_candidates: int
    total_keyword_candidates: int
    total_merged_candidates: int
    total_accepted_candidates: int
    total_rejected_candidates: int
    final_context_size: int
    retrieval_latency_ms: float

    def __post_init__(self) -> None:
        for field_name in (
            "total_ontology_candidates",
            "total_keyword_candidates",
            "total_merged_candidates",
            "total_accepted_candidates",
            "total_rejected_candidates",
            "final_context_size",
        ):
            value = getattr(self, field_name)
            if value < 0:
                raise ValueError(f"{field_name} must be >= 0; got {value}")
        if self.retrieval_latency_ms < 0:
            raise ValueError(
                f"retrieval_latency_ms must be >= 0; got {self.retrieval_latency_ms}"
            )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "total_ontology_candidates": self.total_ontology_candidates,
            "total_keyword_candidates": self.total_keyword_candidates,
            "total_merged_candidates": self.total_merged_candidates,
            "total_accepted_candidates": self.total_accepted_candidates,
            "total_rejected_candidates": self.total_rejected_candidates,
            "final_context_size": self.final_context_size,
            "retrieval_latency_ms": self.retrieval_latency_ms,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RetrievalPipelineStats":
        """Reconstruct a :class:`RetrievalPipelineStats` from a serialised dictionary."""
        return cls(
            total_ontology_candidates=data["total_ontology_candidates"],
            total_keyword_candidates=data["total_keyword_candidates"],
            total_merged_candidates=data["total_merged_candidates"],
            total_accepted_candidates=data["total_accepted_candidates"],
            total_rejected_candidates=data["total_rejected_candidates"],
            final_context_size=data["final_context_size"],
            retrieval_latency_ms=data["retrieval_latency_ms"],
        )


# ---------------------------------------------------------------------------
# ContextPlanTrace (Phase 1.5 -- observational only)
# ---------------------------------------------------------------------------
#
# These two dataclasses are a plain-primitive diagnostic projection of
# obsidian.memory_engine.context_planner.ContextPlan/CategoryRequirement --
# not the same objects. RetrievalTrace lives in obsidian.ontology, a layer
# obsidian.memory_engine already depends on (see MemoryEngine's own imports
# from this module); importing ContextPlan's enum types here would invert
# that dependency. Storing plain str/int/float fields instead avoids the
# cycle and keeps this module's only coupling to the planner one-directional:
# MemoryEngine.query_with_trace converts a ContextPlan into a
# ContextPlanTrace when it builds a RetrievalTrace, exactly as it already
# converts ranker/acceptance-stage values into CandidateTrace fields.


@dataclass(frozen=True)
class ContextCategoryRequirementTrace:
    """Diagnostic snapshot of one ``CategoryRequirement`` from a ``ContextPlan``.

    Parameters
    ----------
    category : str
        The ``ContextCategory`` value (e.g. ``"decision"``, ``"task"``).
    necessity : str
        The ``Necessity`` value (``"required"`` or ``"optional"``).
    min_count : int
        Minimum accepted-item count needed to satisfy this requirement.
    max_count : int, optional
        Maximum item count this category should contribute; ``None`` means
        no plan-level cap.
    priority_tier : str
        The ``PriorityTier`` value (``"never_drop"``, ``"normal"``, or
        ``"drop_first"``).
    """

    category: str
    necessity: str
    min_count: int
    max_count: Optional[int]
    priority_tier: str

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "category": self.category,
            "necessity": self.necessity,
            "min_count": self.min_count,
            "max_count": self.max_count,
            "priority_tier": self.priority_tier,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ContextCategoryRequirementTrace":
        """Reconstruct a :class:`ContextCategoryRequirementTrace` from a serialised dictionary."""
        return cls(
            category=data["category"],
            necessity=data["necessity"],
            min_count=data["min_count"],
            max_count=data.get("max_count"),
            priority_tier=data["priority_tier"],
        )


@dataclass(frozen=True)
class ContextPlanTrace:
    """Diagnostic snapshot of the ``ContextPlan`` produced for a retrieval run.

    Populated by :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
    from the :class:`~obsidian.memory_engine.context_planner.ContextPlan` its
    :class:`~obsidian.memory_engine.context_planner.ContextPlanner` produces
    before retrieval begins. As of Phase 1.5, this is observational only:
    nothing in the pipeline reads this trace back to change retrieval,
    ranking, acceptance, allocation, or context/prompt construction.

    Parameters
    ----------
    task_mode : str
        The classified ``TaskMode`` value.
    planning_method : str
        The ``PlanningMethod`` value describing how ``task_mode`` was
        decided.
    scope_concept_id : UUID, optional
        The plan's ``scope_concept_id``, carried through verbatim. ``None``
        means project-wide / unresolved scope.
    confidence : float
        The plan's own classification confidence, in ``[0.0, 1.0]``.
    requirements : tuple[ContextCategoryRequirementTrace, ...]
        Diagnostic projection of the plan's ``requirements``, in the same
        order. Empty exactly when the plan's ``task_mode`` is the
        "no plan needed" sentinel.

    Raises
    ------
    ValueError
        If ``confidence`` is outside ``[0.0, 1.0]``.
    """

    task_mode: str
    planning_method: str
    scope_concept_id: Optional[UUID]
    confidence: float
    requirements: Tuple[ContextCategoryRequirementTrace, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirements", tuple(self.requirements))
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0]; got {self.confidence}")

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "task_mode": self.task_mode,
            "planning_method": self.planning_method,
            "scope_concept_id": (
                str(self.scope_concept_id) if self.scope_concept_id is not None else None
            ),
            "confidence": self.confidence,
            "requirements": [r.to_dict() for r in self.requirements],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ContextPlanTrace":
        """Reconstruct a :class:`ContextPlanTrace` from a serialised dictionary."""
        scope = data.get("scope_concept_id")
        return cls(
            task_mode=data["task_mode"],
            planning_method=data["planning_method"],
            scope_concept_id=UUID(scope) if scope is not None else None,
            confidence=data["confidence"],
            requirements=tuple(
                ContextCategoryRequirementTrace.from_dict(r)
                for r in data.get("requirements", [])
            ),
        )


# ---------------------------------------------------------------------------
# CoverageReportTrace (Phase 2 -- observational only)
# ---------------------------------------------------------------------------
#
# Same rationale as ContextPlanTrace above: obsidian.memory_engine.coverage_analyzer's
# CoverageReport/CategoryCoverage carry ContextCategory/Necessity enum types
# from obsidian.memory_engine.context_planner, and RetrievalTrace must not
# import those (one-directional dependency: obsidian.memory_engine already
# depends on obsidian.ontology, not the reverse). These two dataclasses are a
# plain-primitive diagnostic projection, built by MemoryEngine.query_with_trace
# exactly as it already builds ContextPlanTrace.


@dataclass(frozen=True)
class CategoryCoverageTrace:
    """Diagnostic snapshot of one ``CategoryCoverage`` entry from a ``CoverageReport``.

    Parameters
    ----------
    category : str
        The ``ContextCategory`` value this entry reports on.
    necessity : str
        The ``Necessity`` value (``"required"`` or ``"optional"``).
    required_minimum : int
        The originating requirement's ``min_count``.
    retrieved_count : int
        Number of accepted candidates that resolved to ``category``.
    satisfied : bool
        Whether ``retrieved_count >= required_minimum``.
    status : str
        The ``CoverageStatus`` value (``"full"``, ``"partial"``, or
        ``"missing"``).
    """

    category: str
    necessity: str
    required_minimum: int
    retrieved_count: int
    satisfied: bool
    status: str

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "category": self.category,
            "necessity": self.necessity,
            "required_minimum": self.required_minimum,
            "retrieved_count": self.retrieved_count,
            "satisfied": self.satisfied,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CategoryCoverageTrace":
        """Reconstruct a :class:`CategoryCoverageTrace` from a serialised dictionary."""
        return cls(
            category=data["category"],
            necessity=data["necessity"],
            required_minimum=data["required_minimum"],
            retrieved_count=data["retrieved_count"],
            satisfied=data["satisfied"],
            status=data["status"],
        )


@dataclass(frozen=True)
class CoverageReportTrace:
    """Diagnostic snapshot of the ``CoverageReport`` computed for a retrieval run.

    Populated by :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
    from the :class:`~obsidian.memory_engine.coverage_analyzer.CoverageReport`
    that :func:`~obsidian.memory_engine.coverage_analyzer.analyze_coverage`
    produces after acceptance and slot allocation finish. As of Phase 2, this
    is observational only: nothing in the pipeline reads this trace back to
    change retrieval, ranking, acceptance, allocation, or context/prompt
    construction. A future phase may use it to trigger a bounded gap-recovery
    retry; see ``docs/architecture/CONTEXT_PLAN_OBJECT.md`` §5.

    Parameters
    ----------
    entries : tuple[CategoryCoverageTrace, ...]
        Diagnostic projection of the report's ``entries``, in the same
        order. Empty exactly when the originating plan requested nothing
        (the ``TaskMode.POINTED_QA`` sentinel).
    overall_coverage_percentage : float
        Percentage of ``REQUIRED`` entries that were satisfied, in
        ``[0.0, 100.0]``. ``100.0`` when there are no ``REQUIRED`` entries.
    missing_required_categories : tuple[str, ...]
        ``ContextCategory`` values for unsatisfied ``REQUIRED`` entries, in
        plan declaration order.
    fully_satisfied : bool
        ``True`` iff ``missing_required_categories`` is empty.
    """

    entries: Tuple[CategoryCoverageTrace, ...] = ()
    overall_coverage_percentage: float = 100.0
    missing_required_categories: Tuple[str, ...] = ()
    fully_satisfied: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "entries", tuple(self.entries))
        object.__setattr__(
            self,
            "missing_required_categories",
            tuple(self.missing_required_categories),
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "entries": [e.to_dict() for e in self.entries],
            "overall_coverage_percentage": self.overall_coverage_percentage,
            "missing_required_categories": list(self.missing_required_categories),
            "fully_satisfied": self.fully_satisfied,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CoverageReportTrace":
        """Reconstruct a :class:`CoverageReportTrace` from a serialised dictionary."""
        return cls(
            entries=tuple(
                CategoryCoverageTrace.from_dict(e) for e in data.get("entries", [])
            ),
            overall_coverage_percentage=data.get("overall_coverage_percentage", 100.0),
            missing_required_categories=tuple(
                data.get("missing_required_categories", [])
            ),
            fully_satisfied=data.get("fully_satisfied", True),
        )


# ---------------------------------------------------------------------------
# GapRecoveryTrace (Phase 4 -- observational only)
# ---------------------------------------------------------------------------
#
# Same rationale as ContextPlanTrace/CoverageReportTrace above:
# obsidian.memory_engine.gap_recovery's GapRecoveryDecision carries
# RetryReason/RecoveryStrategy/ContextCategory enum types from
# obsidian.memory_engine.context_planner and obsidian.memory_engine.gap_recovery,
# and RetrievalTrace must not import those (one-directional dependency:
# obsidian.memory_engine already depends on obsidian.ontology, not the
# reverse). This is a plain-primitive diagnostic projection, built by
# MemoryEngine.query_with_trace exactly as it already builds
# ContextPlanTrace/CoverageReportTrace.


@dataclass(frozen=True)
class GapRecoveryTrace:
    """Diagnostic snapshot of the ``GapRecoveryDecision`` computed for a retrieval run.

    Populated by :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
    from the :class:`~obsidian.memory_engine.gap_recovery.GapRecoveryDecision`
    that :func:`~obsidian.memory_engine.gap_recovery.decide_gap_recovery`
    produces after coverage analysis finishes. As of Phase 4, this is
    observational only: nothing in the pipeline reads this trace back to
    change retrieval, ranking, acceptance, allocation, or context/prompt
    construction. A future phase may use it to actually trigger a bounded
    gap-recovery retry; see
    ``docs/architecture/CONTEXT_PLAN_OBJECT.md`` §5 and
    :mod:`obsidian.memory_engine.gap_recovery`'s module docstring.

    Parameters
    ----------
    should_retry : bool
        Whether another retrieval pass was recommended.
    missing_categories : tuple[str, ...]
        ``ContextCategory`` values this decision is about, in the
        originating ``CoverageReport``'s declaration order.
    retry_budget : int
        Number of retry attempts recommended. ``0`` when ``should_retry`` is
        ``False``.
    retry_reason : str
        The ``RetryReason`` value explaining this decision.
    confidence : float
        This decision's own confidence, in ``[0.0, 1.0]``.
    recovery_strategy : str
        The ``RecoveryStrategy`` value describing how a future retry would
        proceed. ``"none"`` when ``should_retry`` is ``False``.
    """

    should_retry: bool
    missing_categories: Tuple[str, ...] = ()
    retry_budget: int = 0
    retry_reason: str = "no_gap"
    confidence: float = 1.0
    recovery_strategy: str = "none"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "missing_categories", tuple(self.missing_categories)
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "should_retry": self.should_retry,
            "missing_categories": list(self.missing_categories),
            "retry_budget": self.retry_budget,
            "retry_reason": self.retry_reason,
            "confidence": self.confidence,
            "recovery_strategy": self.recovery_strategy,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GapRecoveryTrace":
        """Reconstruct a :class:`GapRecoveryTrace` from a serialised dictionary."""
        return cls(
            should_retry=data["should_retry"],
            missing_categories=tuple(data.get("missing_categories", [])),
            retry_budget=data.get("retry_budget", 0),
            retry_reason=data.get("retry_reason", "no_gap"),
            confidence=data.get("confidence", 1.0),
            recovery_strategy=data.get("recovery_strategy", "none"),
        )


# ---------------------------------------------------------------------------
# ProjectStateTrace (Phase A -- observational only)
# ---------------------------------------------------------------------------
#
# Same rationale as ContextPlanTrace/CoverageReportTrace/GapRecoveryTrace
# above: obsidian.memory_engine.project_state's ProjectState/StateRef/
# ProjectStateField carry no enum types from obsidian.memory_engine, but
# RetrievalTrace still must not import that module directly (one-directional
# dependency: obsidian.memory_engine already depends on obsidian.ontology,
# not the reverse -- see obsidian.memory_engine.project_state's own module
# docstring for the full Phase A design this projects). These three
# dataclasses are a plain-primitive diagnostic projection, built by
# MemoryEngine.query_with_trace exactly as it already builds
# ContextPlanTrace/CoverageReportTrace/GapRecoveryTrace.


@dataclass(frozen=True)
class StateRefTrace:
    """Diagnostic snapshot of one ``StateRef`` from a ``ProjectState``.

    Parameters
    ----------
    knowledge_object_id : UUID
        Identity of the underlying ``KnowledgeObject``.
    canonical_fact : str
        The fact text, copied verbatim.
    valid_from : datetime
        The ``KnowledgeObject``'s own ``valid_from``.
    confidence : float
        The ``KnowledgeObject``'s own ``confidence`` (0.0 - 1.0).
    importance : float
        The ``KnowledgeObject``'s own ``importance`` (0.0 - 1.0).
    """

    knowledge_object_id: UUID
    canonical_fact: str
    valid_from: datetime
    confidence: float
    importance: float

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "knowledge_object_id": str(self.knowledge_object_id),
            "canonical_fact": self.canonical_fact,
            "valid_from": self.valid_from.isoformat(),
            "confidence": self.confidence,
            "importance": self.importance,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "StateRefTrace":
        """Reconstruct a :class:`StateRefTrace` from a serialised dictionary."""
        return cls(
            knowledge_object_id=UUID(data["knowledge_object_id"]),
            canonical_fact=data["canonical_fact"],
            valid_from=datetime.fromisoformat(data["valid_from"]),
            confidence=data["confidence"],
            importance=data["importance"],
        )


@dataclass(frozen=True)
class ProjectStateFieldTrace:
    """Diagnostic snapshot of a single-value ``ProjectStateField``.

    Only ever populated for ``current_objective`` in Phase A -- see
    :class:`ProjectStateTrace`.

    Parameters
    ----------
    value : StateRefTrace
        The field's value, projected.
    derivation : str
        The ``FieldDerivation`` value (``"deterministic"``,
        ``"memory_direct"``, or ``"inferred"``). Always ``"memory_direct"``
        for every ``ProjectStateTrace`` a Phase A build produces.
    confidence : float
        The field's own confidence, in ``[0.0, 1.0]``.
    """

    value: StateRefTrace
    derivation: str
    confidence: float

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "value": self.value.to_dict(),
            "derivation": self.derivation,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectStateFieldTrace":
        """Reconstruct a :class:`ProjectStateFieldTrace` from a serialised dictionary."""
        return cls(
            value=StateRefTrace.from_dict(data["value"]),
            derivation=data["derivation"],
            confidence=data["confidence"],
        )


@dataclass(frozen=True)
class ProjectStateTrace:
    """Diagnostic snapshot of the ``ProjectState`` computed for a retrieval run.

    Populated by :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
    from the :class:`~obsidian.memory_engine.project_state.ProjectState` that
    :class:`~obsidian.memory_engine.project_state.ProjectStateBuilder` builds
    from this run's already-allocated candidates (Phase A). Purely
    observational, exactly like ``context_plan``/``coverage``/``gap_recovery``
    above: nothing in the pipeline reads this trace back to change
    retrieval, ranking, acceptance, allocation, or context/prompt
    construction -- see
    :mod:`obsidian.memory_engine.project_state`'s module docstring for the
    full Phase A design and scope.

    Parameters
    ----------
    current_objective : ProjectStateFieldTrace, optional
        Diagnostic projection of ``ProjectState.current_objective``. ``None``
        when no ``GOAL``-typed candidate was accepted this run.
    decisions, superseded_decisions, active_tasks, blockers, constraints,
    implementation_state, code_areas, open_questions : tuple[StateRefTrace, ...]
        Diagnostic projections of the correspondingly-named
        ``ProjectState`` fields.
    gaps : tuple[str, ...]
        Copied verbatim from ``ProjectState.gaps``.
    confidence : float
        Copied verbatim from ``ProjectState.confidence``.
    generated_at : datetime
        Copied verbatim from ``ProjectState.generated_at``.
    """

    current_objective: Optional[ProjectStateFieldTrace] = None
    decisions: Tuple[StateRefTrace, ...] = ()
    superseded_decisions: Tuple[StateRefTrace, ...] = ()
    active_tasks: Tuple[StateRefTrace, ...] = ()
    blockers: Tuple[StateRefTrace, ...] = ()
    constraints: Tuple[StateRefTrace, ...] = ()
    implementation_state: Tuple[StateRefTrace, ...] = ()
    code_areas: Tuple[StateRefTrace, ...] = ()
    open_questions: Tuple[StateRefTrace, ...] = ()
    gaps: Tuple[str, ...] = ()
    confidence: float = 1.0
    generated_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decisions", tuple(self.decisions))
        object.__setattr__(
            self, "superseded_decisions", tuple(self.superseded_decisions)
        )
        object.__setattr__(self, "active_tasks", tuple(self.active_tasks))
        object.__setattr__(self, "blockers", tuple(self.blockers))
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(
            self, "implementation_state", tuple(self.implementation_state)
        )
        object.__setattr__(self, "code_areas", tuple(self.code_areas))
        object.__setattr__(self, "open_questions", tuple(self.open_questions))
        object.__setattr__(self, "gaps", tuple(self.gaps))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "current_objective": (
                self.current_objective.to_dict()
                if self.current_objective is not None
                else None
            ),
            "decisions": [r.to_dict() for r in self.decisions],
            "superseded_decisions": [r.to_dict() for r in self.superseded_decisions],
            "active_tasks": [r.to_dict() for r in self.active_tasks],
            "blockers": [r.to_dict() for r in self.blockers],
            "constraints": [r.to_dict() for r in self.constraints],
            "implementation_state": [r.to_dict() for r in self.implementation_state],
            "code_areas": [r.to_dict() for r in self.code_areas],
            "open_questions": [r.to_dict() for r in self.open_questions],
            "gaps": list(self.gaps),
            "confidence": self.confidence,
            "generated_at": self.generated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectStateTrace":
        """Reconstruct a :class:`ProjectStateTrace` from a serialised dictionary."""
        current_objective_data = data.get("current_objective")
        return cls(
            current_objective=(
                ProjectStateFieldTrace.from_dict(current_objective_data)
                if current_objective_data is not None
                else None
            ),
            decisions=tuple(
                StateRefTrace.from_dict(r) for r in data.get("decisions", [])
            ),
            superseded_decisions=tuple(
                StateRefTrace.from_dict(r)
                for r in data.get("superseded_decisions", [])
            ),
            active_tasks=tuple(
                StateRefTrace.from_dict(r) for r in data.get("active_tasks", [])
            ),
            blockers=tuple(StateRefTrace.from_dict(r) for r in data.get("blockers", [])),
            constraints=tuple(
                StateRefTrace.from_dict(r) for r in data.get("constraints", [])
            ),
            implementation_state=tuple(
                StateRefTrace.from_dict(r)
                for r in data.get("implementation_state", [])
            ),
            code_areas=tuple(
                StateRefTrace.from_dict(r) for r in data.get("code_areas", [])
            ),
            open_questions=tuple(
                StateRefTrace.from_dict(r) for r in data.get("open_questions", [])
            ),
            gaps=tuple(data.get("gaps", [])),
            confidence=data.get("confidence", 1.0),
            generated_at=(
                datetime.fromisoformat(data["generated_at"])
                if "generated_at" in data
                else datetime.utcnow()
            ),
        )


# ---------------------------------------------------------------------------
# RetrievalTrace
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetrievalTrace:
    """Immutable, JSON-serialisable explanation of a single retrieval run.

    ``RetrievalTrace`` is Haven's canonical diagnostics/explanation
    object: for one query, it records every rewritten query issued and
    every memory considered, with enough detail to answer "why was this
    retrieved" or "why wasn't this retrieved" without re-running
    retrieval. It is built by :meth:`MemoryEngine.query_with_trace` from
    exactly the values the real pipeline already computed — it never
    recomputes a score or re-derives a ranking decision, so it cannot
    drift from what :meth:`MemoryEngine.query` actually returned.

    Intended consumers are debugging tools, benchmark analysis, a future
    dashboard, and a future browser extension's "why was this retrieved?"
    view. This object must NEVER be passed to, or serialised into, an LLM
    prompt or context window — it is a diagnostics artifact, not
    retrieval output.

    Parameters
    ----------
    query : str
        The raw user query that initiated this retrieval run. May be
        empty — :meth:`MemoryEngine.query_with_trace` builds one of these
        for every call it makes, including one made with an empty or
        whitespace-only query, so this is not restricted to well-formed
        input the way a hand-constructed debug trace might assume.
    rewritten_queries : tuple[str, ...]
        Alternate phrasings produced by ``QueryRewriter``, if one was
        configured and rewriting succeeded. Empty when no rewriter is
        configured, or when rewriting yielded no alternates (including
        its documented fail-open case).
    candidates : tuple[CandidateTrace, ...]
        One entry per unique memory considered, accepted or rejected,
        ordered by ascending ``final_rank``.
    pipeline_stats : RetrievalPipelineStats
        Aggregate counts and timing for this run.
    created_at : datetime
        UTC timestamp when this trace was captured.
    context_plan : ContextPlanTrace, optional
        Diagnostic snapshot of the :class:`~obsidian.memory_engine.context_planner.ContextPlan`
        produced for this query before retrieval began (Phase 1.5). ``None``
        only for traces built before this field existed and reconstructed
        via :meth:`from_dict` from serialised data that predates it --
        every trace :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
        builds today always populates this. Purely observational: no
        pipeline stage reads it back to change behavior.
    coverage : CoverageReportTrace, optional
        Diagnostic snapshot of the :class:`~obsidian.memory_engine.coverage_analyzer.CoverageReport`
        computed for this query, comparing ``context_plan``'s requirements
        against this run's accepted ``candidates`` (Phase 2). ``None`` only
        for traces built before this field existed and reconstructed via
        :meth:`from_dict` from serialised data that predates it -- every
        trace :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
        builds today always populates this. Purely observational: no
        pipeline stage reads it back to change behavior.
    gap_recovery : GapRecoveryTrace, optional
        Diagnostic snapshot of the :class:`~obsidian.memory_engine.gap_recovery.GapRecoveryDecision`
        computed for this query from ``context_plan`` and ``coverage``
        (Phase 4). ``None`` only for traces built before this field existed
        and reconstructed via :meth:`from_dict` from serialised data that
        predates it -- every trace
        :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
        builds today always populates this. Purely observational: no
        pipeline stage reads it back to change behavior.
    project_state : ProjectStateTrace, optional
        Diagnostic snapshot of the
        :class:`~obsidian.memory_engine.project_state.ProjectState`
        :class:`~obsidian.memory_engine.project_state.ProjectStateBuilder`
        derives from this run's allocated candidates (Phase A). ``None``
        only for traces built before this field existed and reconstructed
        via :meth:`from_dict` from serialised data that predates it -- every
        trace :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
        builds today always populates this. Purely observational: no
        pipeline stage reads it back to change behavior, and nothing it
        contains influences retrieval, ranking, acceptance, allocation,
        ``WorkingContext``, or any rendered prompt -- see
        :mod:`obsidian.memory_engine.project_state`'s module docstring.
    """

    query: str
    rewritten_queries: Tuple[str, ...]
    candidates: Tuple[CandidateTrace, ...]
    pipeline_stats: RetrievalPipelineStats
    created_at: datetime = field(default_factory=datetime.utcnow)
    context_plan: Optional[ContextPlanTrace] = None
    coverage: Optional[CoverageReportTrace] = None
    gap_recovery: Optional[GapRecoveryTrace] = None
    project_state: Optional[ProjectStateTrace] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "rewritten_queries", tuple(self.rewritten_queries)
        )
        object.__setattr__(self, "candidates", tuple(self.candidates))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "query": self.query,
            "rewritten_queries": list(self.rewritten_queries),
            "candidates": [c.to_dict() for c in self.candidates],
            "pipeline_stats": self.pipeline_stats.to_dict(),
            "created_at": self.created_at.isoformat(),
            "context_plan": (
                self.context_plan.to_dict() if self.context_plan is not None else None
            ),
            "coverage": (
                self.coverage.to_dict() if self.coverage is not None else None
            ),
            "gap_recovery": (
                self.gap_recovery.to_dict() if self.gap_recovery is not None else None
            ),
            "project_state": (
                self.project_state.to_dict() if self.project_state is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RetrievalTrace":
        """Reconstruct a :class:`RetrievalTrace` from a serialised dictionary."""
        context_plan_data = data.get("context_plan")
        coverage_data = data.get("coverage")
        gap_recovery_data = data.get("gap_recovery")
        project_state_data = data.get("project_state")
        return cls(
            query=data["query"],
            rewritten_queries=tuple(data.get("rewritten_queries", ())),
            candidates=tuple(
                CandidateTrace.from_dict(c) for c in data["candidates"]
            ),
            pipeline_stats=RetrievalPipelineStats.from_dict(data["pipeline_stats"]),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if "created_at" in data
                else datetime.utcnow()
            ),
            context_plan=(
                ContextPlanTrace.from_dict(context_plan_data)
                if context_plan_data is not None
                else None
            ),
            coverage=(
                CoverageReportTrace.from_dict(coverage_data)
                if coverage_data is not None
                else None
            ),
            gap_recovery=(
                GapRecoveryTrace.from_dict(gap_recovery_data)
                if gap_recovery_data is not None
                else None
            ),
            project_state=(
                ProjectStateTrace.from_dict(project_state_data)
                if project_state_data is not None
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Working Context prompt models
# ---------------------------------------------------------------------------
#
# These are the *plain data* value objects the prompt layer renders. They are
# produced downstream of the Slot Allocator (out of scope here) and consumed by
# :class:`~obsidian.memory_engine.structured_prompt_builder.StructuredPromptBuilder`,
# which is a pure renderer and computes none of the values below. Every model
# is a frozen dataclass reusing :class:`RankedCandidate` verbatim as its unit of
# membership, so nothing about ranking, acceptance, or allocation is duplicated
# or re-derived here.


class MemoryRole(str, Enum):
    """The role a memory plays inside a Working Context.

    Roles are a *presentation* grouping — "what kind of thing is this, for the
    purpose of resuming work" — distinct from
    :class:`~obsidian.core.enums.MemoryType` (the memory's semantic category).
    The mapping between the two is fixed and deterministic (see
    :func:`resolve_role`).

    Values
    ------
    DECISION : str
        A decision that was made.
    GOAL : str
        A goal being worked toward. Surfaces as a context's "current goal".
    TASK : str
        A concrete pending action.
    BELIEF : str
        A belief, opinion, or held rule/principle.
    RESEARCH : str
        A finding or piece of factual knowledge gathered while working.
    OPEN_QUESTION : str
        An explicitly unresolved question. Reached by
        ``MemoryType.OPEN_QUESTION`` directly, or, for any other type, via an
        explicit ``metadata["role"]`` override (see :func:`resolve_role`).
    REFERENCE : str
        Background reference material that does not fit another role.
    """

    DECISION = "decision"
    GOAL = "goal"
    TASK = "task"
    BELIEF = "belief"
    RESEARCH = "research"
    OPEN_QUESTION = "open_question"
    REFERENCE = "reference"


class ContextKind(str, Enum):
    """What a Working Context represents.

    Values
    ------
    PROJECT : str
        An endeavour anchored by a project/goal (e.g. "Project Haven").
    TOPIC : str
        A coherent topic cluster that is not an explicit project.
    GENERAL : str
        The catch-all context for memories with no resolvable anchor.
    """

    PROJECT = "project"
    TOPIC = "topic"
    GENERAL = "general"


class ContextStatus(str, Enum):
    """Deterministic lifecycle summary of a Working Context.

    Derived purely from which role buckets are populated (see
    :meth:`WorkingContextState.from_buckets`); no clock or threshold is read,
    so the same buckets always yield the same status.

    Values
    ------
    ACTIVE : str
        Has pending tasks or open questions — unfinished work to resume.
    DECIDED : str
        Has decisions but no open threads — settled, no next action.
    REFERENCE : str
        Only research/reference material — nothing actionable.
    """

    ACTIVE = "active"
    DECIDED = "decided"
    REFERENCE = "reference"


#: Fixed, total ``MemoryType`` -> ``MemoryRole`` mapping. Every ``MemoryType``
#: has exactly one entry so :func:`resolve_role` is deterministic and never
#: falls through for a known type.
#:
#: ``BLOCKER``, ``IMPLEMENTATION_STATE``, ``CODE_AREA``, and
#: ``OPEN_QUESTION`` map onto *existing* ``MemoryRole`` values rather than
#: introducing new ones -- see
#: ``docs/architecture/PROJECT_STATE_KNOWLEDGE_MODEL.md`` §3 for why: every
#: :class:`~obsidian.memory_engine.working_context_builder.WorkingContextBuilder`
#: call iterates ``for role in MemoryRole`` to build one bucket per role in
#: *every* :class:`WorkingContext`, always, even empty, so a new ``MemoryRole``
#: value would change the shape of every ``WorkingContext`` returned by every
#: query today. ``MemoryType.OPEN_QUESTION`` is the one new type that maps
#: directly onto the role of the same name -- previously that role was
#: reachable only via an explicit ``metadata["role"]`` override (still true
#: for every other type; the override still wins over this table, see
#: :func:`resolve_role`).
_MEMORY_TYPE_ROLE: Dict[MemoryType, "MemoryRole"] = {
    MemoryType.DECISION: MemoryRole.DECISION,
    MemoryType.GOAL: MemoryRole.GOAL,
    MemoryType.TASK: MemoryRole.TASK,
    MemoryType.BELIEF: MemoryRole.BELIEF,
    MemoryType.RULE: MemoryRole.BELIEF,
    MemoryType.FACT: MemoryRole.RESEARCH,
    MemoryType.SKILL: MemoryRole.REFERENCE,
    MemoryType.PREFERENCE: MemoryRole.REFERENCE,
    MemoryType.PERSON: MemoryRole.REFERENCE,
    MemoryType.EVENT: MemoryRole.REFERENCE,
    MemoryType.PROJECT: MemoryRole.REFERENCE,
    MemoryType.BLOCKER: MemoryRole.TASK,
    MemoryType.IMPLEMENTATION_STATE: MemoryRole.REFERENCE,
    MemoryType.CODE_AREA: MemoryRole.REFERENCE,
    MemoryType.OPEN_QUESTION: MemoryRole.OPEN_QUESTION,
}

#: How many members each list field of :class:`WorkingContextState` keeps.
STATE_TOP_K = 3


def resolve_role(knowledge_object: KnowledgeObject) -> MemoryRole:
    """Return the :class:`MemoryRole` for *knowledge_object*, deterministically.

    Resolution order:

    1. A ``metadata["role"]`` override, when present and a valid
       :class:`MemoryRole` value (the backwards-compatible, metadata-driven
       path that lets a memory declare e.g. ``OPEN_QUESTION`` explicitly). An
       unrecognised override is ignored, not an error.
    2. Otherwise the fixed :data:`_MEMORY_TYPE_ROLE` mapping for the memory's
       ``memory_type``, falling back to :attr:`MemoryRole.REFERENCE` for any
       type not in the table.

    This is a pure classification helper on the data model; it performs no
    retrieval, ranking, or grouping.
    """
    override = knowledge_object.metadata.get("role")
    if override is not None:
        try:
            return MemoryRole(override)
        except ValueError:
            pass
    return _MEMORY_TYPE_ROLE.get(knowledge_object.memory_type, MemoryRole.REFERENCE)


@dataclass(frozen=True)
class RoleBucket:
    """A role plus the ranked memories assigned to it, in the given order.

    ``members`` order is trusted and never re-sorted by the renderer — it is
    whatever order the (out-of-scope) assembly stage produced, typically
    descending ``final_score``.

    Parameters
    ----------
    role : MemoryRole
        The role every member of this bucket plays.
    members : tuple[RankedCandidate, ...]
        The memories in this bucket, in render order. May be empty.
    """

    role: MemoryRole
    members: Tuple[RankedCandidate, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "members", tuple(self.members))

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "role": self.role.value,
            "members": [m.to_dict() for m in self.members],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RoleBucket":
        """Reconstruct a :class:`RoleBucket` from a serialised dictionary."""
        return cls(
            role=MemoryRole(data["role"]),
            members=tuple(RankedCandidate.from_dict(m) for m in data.get("members", [])),
        )


@dataclass(frozen=True)
class WorkingContextState:
    """A deterministic executive summary of a Working Context's role buckets.

    Not a retrieval step: every field is a projection of the buckets a
    :class:`WorkingContext` already holds (see :meth:`from_buckets`). The
    renderer reads these fields verbatim and computes nothing.

    Parameters
    ----------
    status : ContextStatus
        The lifecycle summary (see :class:`ContextStatus`).
    current_goal : RankedCandidate, optional
        The single most salient goal, or ``None`` when there is none.
    recent_decisions : tuple[RankedCandidate, ...]
        The most recently valid decisions (newest ``valid_from`` first).
    pending_tasks : tuple[RankedCandidate, ...]
        The most salient pending tasks, in rank order.
    open_questions : tuple[RankedCandidate, ...]
        The most salient open questions, in rank order.
    """

    status: ContextStatus
    current_goal: Optional[RankedCandidate] = None
    recent_decisions: Tuple[RankedCandidate, ...] = ()
    pending_tasks: Tuple[RankedCandidate, ...] = ()
    open_questions: Tuple[RankedCandidate, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "recent_decisions", tuple(self.recent_decisions))
        object.__setattr__(self, "pending_tasks", tuple(self.pending_tasks))
        object.__setattr__(self, "open_questions", tuple(self.open_questions))

    @classmethod
    def from_buckets(
        cls, buckets: List["RoleBucket"], top_k: int = STATE_TOP_K
    ) -> "WorkingContextState":
        """Derive a state summary from *buckets*, deterministically.

        Pure reduction over the buckets — no clock, no retrieval. ``status`` is
        ``ACTIVE`` when there are pending tasks or open questions, else
        ``DECIDED`` when there are decisions, else ``REFERENCE``.
        ``current_goal`` is the top-ranked goal; ``recent_decisions`` are the
        newest-``valid_from`` decisions (rank breaks ties, via stable sort);
        ``pending_tasks``/``open_questions`` are the top-``top_k`` in the
        buckets' given rank order.
        """
        by_role: Dict[MemoryRole, Tuple[RankedCandidate, ...]] = {
            b.role: b.members for b in buckets
        }
        goals = by_role.get(MemoryRole.GOAL, ())
        decisions = by_role.get(MemoryRole.DECISION, ())
        tasks = by_role.get(MemoryRole.TASK, ())
        questions = by_role.get(MemoryRole.OPEN_QUESTION, ())

        by_recency = sorted(
            sorted(decisions),
            key=lambda rc: rc.candidate.knowledge_object.valid_from,
            reverse=True,
        )

        if tasks or questions:
            status = ContextStatus.ACTIVE
        elif decisions:
            status = ContextStatus.DECIDED
        else:
            status = ContextStatus.REFERENCE

        return cls(
            status=status,
            current_goal=goals[0] if goals else None,
            recent_decisions=tuple(by_recency[:top_k]),
            pending_tasks=tuple(tasks[:top_k]),
            open_questions=tuple(questions[:top_k]),
        )

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "status": self.status.value,
            "current_goal": self.current_goal.to_dict()
            if self.current_goal is not None
            else None,
            "recent_decisions": [m.to_dict() for m in self.recent_decisions],
            "pending_tasks": [m.to_dict() for m in self.pending_tasks],
            "open_questions": [m.to_dict() for m in self.open_questions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkingContextState":
        """Reconstruct a :class:`WorkingContextState` from a serialised dictionary."""
        goal = data.get("current_goal")
        return cls(
            status=ContextStatus(data["status"]),
            current_goal=RankedCandidate.from_dict(goal) if goal is not None else None,
            recent_decisions=tuple(
                RankedCandidate.from_dict(m) for m in data.get("recent_decisions", [])
            ),
            pending_tasks=tuple(
                RankedCandidate.from_dict(m) for m in data.get("pending_tasks", [])
            ),
            open_questions=tuple(
                RankedCandidate.from_dict(m) for m in data.get("open_questions", [])
            ),
        )


@dataclass(frozen=True)
class WorkingContext:
    """One endeavour the user is continuing, with its state and role buckets.

    A plain data structure. The renderer trusts ``buckets`` order and never
    re-groups or re-ranks; grouping into contexts and buckets is the job of the
    (out-of-scope) assembly stage.

    Parameters
    ----------
    key : str
        Deterministic identity of this context (e.g. ``"ctx:<uuid>"``).
    title : str
        Human-readable name (e.g. ``"Haven"``); the ``kind`` supplies any
        prefix at render time.
    kind : ContextKind
        What this context represents.
    state : WorkingContextState
        The executive summary rendered before the buckets.
    buckets : tuple[RoleBucket, ...]
        The role buckets, in render order.
    anchor_concept_id : UUID, optional
        The root concept this context is anchored on, if any (diagnostics).
    member_concept_ids : tuple[UUID, ...]
        Every concept folded into this context (diagnostics).
    """

    key: str
    title: str
    kind: ContextKind
    state: WorkingContextState
    buckets: Tuple[RoleBucket, ...] = ()
    anchor_concept_id: Optional[UUID] = None
    member_concept_ids: Tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "buckets", tuple(self.buckets))
        object.__setattr__(self, "member_concept_ids", tuple(self.member_concept_ids))

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "key": self.key,
            "title": self.title,
            "kind": self.kind.value,
            "state": self.state.to_dict(),
            "buckets": [b.to_dict() for b in self.buckets],
            "anchor_concept_id": str(self.anchor_concept_id)
            if self.anchor_concept_id is not None
            else None,
            "member_concept_ids": [str(cid) for cid in self.member_concept_ids],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WorkingContext":
        """Reconstruct a :class:`WorkingContext` from a serialised dictionary."""
        anchor = data.get("anchor_concept_id")
        return cls(
            key=data["key"],
            title=data["title"],
            kind=ContextKind(data["kind"]),
            state=WorkingContextState.from_dict(data["state"]),
            buckets=tuple(RoleBucket.from_dict(b) for b in data.get("buckets", [])),
            anchor_concept_id=UUID(anchor) if anchor is not None else None,
            member_concept_ids=tuple(
                UUID(cid) for cid in data.get("member_concept_ids", [])
            ),
        )
