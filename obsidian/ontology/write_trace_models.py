"""Write Trace data models -- the write-path equivalent of ``RetrievalTrace``.

A :class:`WriteTrace` is Haven's canonical diagnostics/explanation object
for the write path: for one ``POST /api/v1/memory`` call, it records the
checkpoint decision, the Working Context supplied to the Extractor, the
Extractor's prompt/raw LLM output, every extracted fact's classification/
importance/canonical-matching outcome, the vault paths written, and the
ontology proposals validated (accepted or rejected) -- with per-stage
timing. Like ``RetrievalTrace``, it is assembled from values the real
pipeline already computed; nothing here recomputes a decision with
different logic.

Unlike ``RetrievalTrace``, a ``WriteTrace`` cannot be recomputed on demand
-- a write has real side effects (LLM calls, vault files, ontology
mutations, a checkpoint) that cannot be safely repeated just to explain
them after the fact. So a ``WriteTrace`` is captured once, at write time,
and persisted (see :mod:`obsidian.ontology.write_trace_store`) rather than
assembled fresh on every read, the way ``RetrievalTrace`` is.

This module has no dependency on ``obsidian.server`` -- ``working_contexts``
is stored as plain, already-projected dicts (title/goal/decisions/tasks/
questions as strings) rather than raw ``WorkingContext`` objects, because
turning a ``WorkingContext`` into a human-readable title needs a live
``ConceptGraph``, which is a server-layer concern
(``obsidian.server.dashboard._to_working_context_summary``). The caller
(``obsidian/server/main.py``) does that projection once and hands this
module plain data.

Intended consumers are the Write Inspector dashboard and, potentially, a
future provenance-tracking feature -- see ``WriteTrace``'s own docstring
for why it does not yet serve that second purpose, only leaves room for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple
from uuid import UUID

from obsidian.core.enums import MemoryType, SourceType
from obsidian.manager_ai.models import KnowledgeDecision, SupersessionOperation

#: The write-trace schema version this module reads and writes. Bumped only
#: for a breaking change to *trace field shape* -- purely additive fields
#: never require a bump (mirrors
#: ``obsidian.checkpoint.models.CURRENT_SCHEMA_VERSION``).
CURRENT_WRITE_TRACE_SCHEMA_VERSION = 1

CheckpointMode = Literal["first_run", "duplicate", "incremental", "fallback"]
WriteStatus = Literal["success", "duplicate", "no_facts_extracted"]


# ---------------------------------------------------------------------------
# CheckpointStageTrace
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckpointStageTrace:
    """What the checkpoint subsystem decided for this write.

    Parameters
    ----------
    mode : "first_run" | "duplicate" | "incremental" | "fallback"
        The checkpoint decision for this call. ``"duplicate"`` is not a
        member of :data:`obsidian.checkpoint.diff.TurnDiffMode` -- it is
        the early-return case ``main.py`` checks *before* calling
        :func:`~obsidian.checkpoint.diff.classify_turns` (an exact
        ``transcript_hash`` match against an existing checkpoint).
    had_existing_checkpoint : bool
        Whether a checkpoint already existed for this conversation before
        this call.
    turn_count : int
        Total turns in the incoming request.
    new_turn_start_index : int
        Index of the first turn treated as new evidence (0 for
        ``first_run``/``fallback``, which reprocess everything).
    transcript_hash : str
        The incoming transcript's hash. Empty when the request carried no
        ``external_key`` (checkpointing is not in play at all).
    decision_counts : dict[str, int]
        Count of each :class:`~obsidian.manager_ai.models.KnowledgeDecision`
        value produced by this run (e.g. ``{"new": 2, "confirm": 1}``).
        Empty for ``"duplicate"`` (the pipeline never ran).
    """

    mode: CheckpointMode
    had_existing_checkpoint: bool
    turn_count: int
    new_turn_start_index: int
    transcript_hash: str
    decision_counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "had_existing_checkpoint": self.had_existing_checkpoint,
            "turn_count": self.turn_count,
            "new_turn_start_index": self.new_turn_start_index,
            "transcript_hash": self.transcript_hash,
            "decision_counts": dict(self.decision_counts),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckpointStageTrace":
        return cls(
            mode=data.get("mode", "first_run"),
            had_existing_checkpoint=data.get("had_existing_checkpoint", False),
            turn_count=data.get("turn_count", 0),
            new_turn_start_index=data.get("new_turn_start_index", 0),
            transcript_hash=data.get("transcript_hash", ""),
            decision_counts=dict(data.get("decision_counts", {})),
        )


# ---------------------------------------------------------------------------
# ExtractorStageTrace
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractorStageTrace:
    """The Extractor's prompt, raw LLM output, and resulting fact count.

    Parameters
    ----------
    prompt : str, optional
        The exact prompt sent to the LLM. ``None`` when
        ``HAVEN_WRITE_TRACE_CAPTURE_LLM_IO=false`` -- see this module's
        docstring section in ``obsidian/server/main.py`` for why this is
        opt-out rather than always-on.
    raw_response : str, optional
        The LLM's raw text response, before parsing/validation/dedup.
        ``None`` under the same condition as ``prompt``.
    fact_count : int
        Number of facts extracted after validation and deduplication.
        Always populated regardless of the capture flag -- it costs
        nothing and isn't sensitive.
    """

    prompt: Optional[str]
    raw_response: Optional[str]
    fact_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prompt": self.prompt,
            "raw_response": self.raw_response,
            "fact_count": self.fact_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExtractorStageTrace":
        return cls(
            prompt=data.get("prompt"),
            raw_response=data.get("raw_response"),
            fact_count=data.get("fact_count", 0),
        )


# ---------------------------------------------------------------------------
# FactTrace
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FactTrace:
    """One extracted fact's journey through classification, importance,
    canonical matching, and knowledge-object creation.

    Mirrors :class:`~obsidian.ontology.retrieval_models.CandidateTrace` on
    the read side: one entry per candidate (here, per extracted fact),
    carrying every stage's outcome for that candidate.

    Parameters
    ----------
    fact_index : int
        This fact's position (0-based) within the trace's ``facts``
        tuple -- a stable, addressable reference a future feature could
        point at (e.g. ``(trace_id, fact_index)``) instead of duplicating
        this fact's classification/importance/canonical reasoning
        elsewhere. See :class:`WriteTrace`'s docstring for the fuller
        provenance-forward-compatibility rationale.
    fact_text : str
        The extracted fact's normalized text.
    evidence : str
        Why the Extractor believed this was a fact.
    confidence : float
        The Extractor's confidence in this fact (0.0-1.0).
    memory_type : MemoryType, optional
        The Classifier's assigned type.
    classification_confidence : float, optional
        The Classifier's confidence.
    classification_reason : str, optional
        The Classifier's reasoning.
    importance_score : float, optional
        The ImportanceScorer's score.
    importance_reason : str, optional
        The ImportanceScorer's reasoning.
    decision : KnowledgeDecision, optional
        The CanonicalMatcher's raw decision (NEW/CONFIRM/UPDATE/SUPERSEDE).
    knowledge_object_id : UUID, optional
        The resulting (or, for CONFIRM, matched-and-confirmed)
        :class:`~obsidian.manager_ai.models.KnowledgeObject`'s id.
        ``None`` when the decision produced no object (e.g. today's
        pipeline leaves UPDATE/SUPERSEDE unhandled).
    review_action : "unchanged" | "edited" | "deleted" | "added", optional
        Only populated for a fact that went through the Memory Review
        preview/commit flow (``obsidian/server/main.py``'s
        ``/memory/preview`` + ``/memory/commit``) -- ``None`` for every
        trace produced by the direct one-shot ``save_memory`` path (Quick
        Capture, demo seeding, and any caller that never uses Review).
        ``"unchanged"``/``"edited"`` describe a fact the Extractor produced
        that the user kept or modified during review; ``"deleted"`` a fact
        the Extractor produced that the user removed before commit (such a
        row always has ``decision=None``/``knowledge_object_id=None``, since
        it never reached the matcher); ``"added"`` a fact the user typed in
        during review that the Extractor never produced at all.
    original_fact_text : str, optional
        The Extractor's original ``fact_text`` before the user's edit.
        Populated only when ``review_action in ("edited", "deleted")`` --
        the text this row would have had if the user hadn't changed or
        removed it. ``None`` otherwise (including ``"added"``, which has no
        "original" text to compare against).
    original_memory_type : MemoryType, optional
        The Classifier's original ``memory_type`` before the user's edit.
        Same populated-only-for-``"edited"``/``"deleted"`` contract as
        ``original_fact_text``.
    supersession_operation : SupersessionOperation, optional
        Mirrors :class:`~obsidian.manager_ai.models.SupersessionResult.operation`
        for this fact, when the pipeline recorded a supersession-family
        outcome (``ExtractionDecision.supersession``). Today only an in-place
        ``UPDATE`` populates it (``SupersessionOperation.UPDATE``); ``None``
        for every ``NEW``/``CONFIRM`` fact, which is the vast majority. This
        is what makes an in-place refinement fully explainable from the trace
        alone -- the ``decision`` field already says ``UPDATE``, and these
        fields say *which* prior memory was refined and *why*.
    supersession_matched_identity : UUID, optional
        Mirrors ``SupersessionResult.matched_identity`` -- the ``id`` of the
        memory this fact refined. For an ``UPDATE`` this equals
        ``knowledge_object_id`` (the id is preserved in place); the field is
        kept distinct so a future ``SUPERSEDE`` (which mints a new id) can
        record the *old* id here without ambiguity. ``None`` when
        ``supersession_operation`` is ``None``.
    supersession_reason : str, optional
        Mirrors ``SupersessionResult.reason`` -- a human-readable explanation
        of the refinement, including the previous ``canonical_fact`` text that
        was overwritten. ``None`` when ``supersession_operation`` is ``None``.
    """

    fact_index: int
    fact_text: str
    evidence: str
    confidence: float
    memory_type: Optional[MemoryType] = None
    classification_confidence: Optional[float] = None
    classification_reason: Optional[str] = None
    importance_score: Optional[float] = None
    importance_reason: Optional[str] = None
    decision: Optional[KnowledgeDecision] = None
    knowledge_object_id: Optional[UUID] = None
    review_action: Optional[Literal["unchanged", "edited", "deleted", "added"]] = None
    original_fact_text: Optional[str] = None
    original_memory_type: Optional[MemoryType] = None
    supersession_operation: Optional[SupersessionOperation] = None
    supersession_matched_identity: Optional[UUID] = None
    supersession_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fact_index": self.fact_index,
            "fact_text": self.fact_text,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "memory_type": self.memory_type.value if self.memory_type else None,
            "classification_confidence": self.classification_confidence,
            "classification_reason": self.classification_reason,
            "importance_score": self.importance_score,
            "importance_reason": self.importance_reason,
            "decision": self.decision.value if self.decision else None,
            "knowledge_object_id": (
                str(self.knowledge_object_id)
                if self.knowledge_object_id is not None
                else None
            ),
            "review_action": self.review_action,
            "original_fact_text": self.original_fact_text,
            "original_memory_type": (
                self.original_memory_type.value
                if self.original_memory_type is not None
                else None
            ),
            "supersession_operation": (
                self.supersession_operation.value
                if self.supersession_operation is not None
                else None
            ),
            "supersession_matched_identity": (
                str(self.supersession_matched_identity)
                if self.supersession_matched_identity is not None
                else None
            ),
            "supersession_reason": self.supersession_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FactTrace":
        return cls(
            fact_index=data.get("fact_index", 0),
            fact_text=data.get("fact_text", ""),
            evidence=data.get("evidence", ""),
            confidence=data.get("confidence", 0.0),
            memory_type=(
                MemoryType(data["memory_type"])
                if data.get("memory_type") is not None
                else None
            ),
            classification_confidence=data.get("classification_confidence"),
            classification_reason=data.get("classification_reason"),
            importance_score=data.get("importance_score"),
            importance_reason=data.get("importance_reason"),
            decision=(
                KnowledgeDecision(data["decision"])
                if data.get("decision") is not None
                else None
            ),
            knowledge_object_id=(
                UUID(data["knowledge_object_id"])
                if data.get("knowledge_object_id") is not None
                else None
            ),
            review_action=data.get("review_action"),
            original_fact_text=data.get("original_fact_text"),
            original_memory_type=(
                MemoryType(data["original_memory_type"])
                if data.get("original_memory_type") is not None
                else None
            ),
            supersession_operation=(
                SupersessionOperation(data["supersession_operation"])
                if data.get("supersession_operation") is not None
                else None
            ),
            supersession_matched_identity=(
                UUID(data["supersession_matched_identity"])
                if data.get("supersession_matched_identity") is not None
                else None
            ),
            supersession_reason=data.get("supersession_reason"),
        )


# ---------------------------------------------------------------------------
# OntologyStageTrace
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OntologyProposalTrace:
    """One ontology proposal's validation outcome.

    Mirrors :class:`~obsidian.ontology.ontology_validator.ValidationResult`
    verbatim (proposal type + accepted + rejection_reason), projected to
    plain JSON-safe fields -- the first place rejected proposals become
    visible anywhere in Haven (``OntologyPipeline.process()`` discards
    them today).
    """

    proposal_type: str
    accepted: bool
    rejection_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_type": self.proposal_type,
            "accepted": self.accepted,
            "rejection_reason": self.rejection_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OntologyProposalTrace":
        return cls(
            proposal_type=data.get("proposal_type", ""),
            accepted=data.get("accepted", False),
            rejection_reason=data.get("rejection_reason", ""),
        )


@dataclass(frozen=True)
class OntologyStageTrace:
    """Ontology update outcome for every :class:`KnowledgeObject` in this write.

    Parameters
    ----------
    validation_results : tuple[OntologyProposalTrace, ...]
        Every proposal considered across every knowledge object processed
        in this write, accepted or rejected, in the order produced.
    concept_paths : tuple[str, ...]
        Concept file paths written or updated (as strings), deduplicated
        across every knowledge object processed in this write.
    """

    validation_results: Tuple[OntologyProposalTrace, ...] = field(
        default_factory=tuple
    )
    concept_paths: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "validation_results", tuple(self.validation_results)
        )
        object.__setattr__(self, "concept_paths", tuple(self.concept_paths))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "validation_results": [
                r.to_dict() for r in self.validation_results
            ],
            "concept_paths": list(self.concept_paths),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OntologyStageTrace":
        return cls(
            validation_results=tuple(
                OntologyProposalTrace.from_dict(r)
                for r in data.get("validation_results", [])
            ),
            concept_paths=tuple(data.get("concept_paths", [])),
        )


# ---------------------------------------------------------------------------
# WriteTrace
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteTrace:
    """Immutable, JSON-serialisable explanation of a single write (``POST
    /api/v1/memory``) call.

    ``WriteTrace`` is the write-path counterpart to
    :class:`~obsidian.ontology.retrieval_models.RetrievalTrace`: for one
    call, it records the checkpoint decision, the Working Context supplied
    to the Extractor, the Extractor's prompt/raw output, every extracted
    fact's classification/importance/canonical-matching/knowledge-object
    outcome, the vault paths written, the ontology proposals validated,
    and per-stage timing -- built from values the real pipeline already
    computed, never recomputed with different logic.

    Unlike ``RetrievalTrace``, this cannot be assembled on demand (see this
    module's docstring) -- it is captured once, at write time, in
    ``obsidian/server/main.py``'s ``save_memory``, and persisted via
    :class:`~obsidian.ontology.write_trace_store.WriteTraceWriter`. A trace
    is produced for **every** call, including the ``"duplicate"``
    short-circuit and the "no facts extracted" 422 case -- especially
    those, since explaining *why nothing happened* is exactly what a
    debugging tool needs to do.

    Provenance-forward-compatibility note: a later feature that needs to
    record *why* a ``KnowledgeObject`` was updated/superseded (e.g. a
    future PR building on the currently-unhandled UPDATE/SUPERSEDE
    decisions) could plausibly reference an existing ``WriteTrace`` by
    ``(trace_id, fact_index)`` instead of duplicating that reasoning onto
    the ``KnowledgeObject`` itself -- that is why :class:`FactTrace` carries
    a stable ``fact_index``. This module does not implement any such
    linkage itself: no field here points at or is pointed at by a
    ``KnowledgeObject``, and ``KnowledgeUpdater``/``CanonicalMatcher`` are
    untouched. See also the separate, permanent
    ``KnowledgeObject.evidence_chain``/``EvidenceEntry``
    (``obsidian/manager_ai/models.py``) -- that is a different, already-
    existing concept (a fact's own permanent audit trail, growing across
    every future confirmation) that this trace deliberately does not
    duplicate or replace: a ``WriteTrace`` is an ephemeral, per-request
    diagnostic log spanning the *entire* pipeline for one call (including
    calls that produce zero knowledge objects), not a fact's lifetime
    evidence record.

    Parameters
    ----------
    schema_version : int
        This trace's own field-shape version.
    pipeline_version : int
        :data:`obsidian.manager_ai.pipeline.PIPELINE_VERSION` at capture
        time -- which pipeline *behaviour* produced this trace.
    extractor_prompt_version : int
        :data:`obsidian.manager_ai.extractor.EXTRACTOR_PROMPT_VERSION` at
        capture time -- whether a later prompt-wording change makes this
        trace's ``raw_response`` non-reproducible against today's prompt.
    trace_id : UUID
        Unique id for this trace; also its filename (see
        :class:`~obsidian.ontology.write_trace_store.WriteTraceWriter`).
    conversation_id : UUID, optional
        ``None`` when the request carried no ``external_key`` (legacy
        callers still get a trace, just with no conversation identity).
    source : SourceType, optional
    external_key : str, optional
    mode : "first_run" | "duplicate" | "incremental" | "fallback"
        Top-level convenience mirror of ``checkpoint.mode``.
    checkpoint : CheckpointStageTrace
    working_contexts : list[dict], optional
        Plain, already-projected Working Context summaries (see this
        module's docstring for why raw ``WorkingContext`` objects aren't
        stored here). ``None`` when this wasn't an incremental save.
    extractor : ExtractorStageTrace, optional
        ``None`` only for the ``"duplicate"`` short-circuit, where the
        Extractor never ran.
    facts : tuple[FactTrace, ...]
    vault_paths : tuple[str, ...]
        Vault Markdown file paths written, one per knowledge object.
    ontology : OntologyStageTrace
    status : "success" | "duplicate" | "no_facts_extracted"
    knowledge_object_ids : tuple[UUID, ...]
    stage_timings_ms : dict[str, float]
        Wall-clock milliseconds per named phase (e.g. ``"checkpoint_lookup"``,
        ``"working_context"``, ``"extract_classify_match_apply"``,
        ``"vault_and_ontology_write"``, ``"checkpoint_persist"``,
        ``"total"``).
    created_at : datetime
    """

    schema_version: int
    pipeline_version: int
    extractor_prompt_version: int
    trace_id: UUID
    conversation_id: Optional[UUID]
    source: Optional[SourceType]
    external_key: Optional[str]
    mode: CheckpointMode
    checkpoint: CheckpointStageTrace
    working_contexts: Optional[List[Dict[str, Any]]]
    extractor: Optional[ExtractorStageTrace]
    facts: Tuple[FactTrace, ...]
    vault_paths: Tuple[str, ...]
    ontology: OntologyStageTrace
    status: WriteStatus
    knowledge_object_ids: Tuple[UUID, ...]
    stage_timings_ms: Dict[str, float] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", tuple(self.facts))
        object.__setattr__(self, "vault_paths", tuple(self.vault_paths))
        object.__setattr__(
            self, "knowledge_object_ids", tuple(self.knowledge_object_ids)
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "pipeline_version": self.pipeline_version,
            "extractor_prompt_version": self.extractor_prompt_version,
            "trace_id": str(self.trace_id),
            "conversation_id": (
                str(self.conversation_id)
                if self.conversation_id is not None
                else None
            ),
            "source": self.source.value if self.source is not None else None,
            "external_key": self.external_key,
            "mode": self.mode,
            "checkpoint": self.checkpoint.to_dict(),
            "working_contexts": self.working_contexts,
            "extractor": self.extractor.to_dict() if self.extractor else None,
            "facts": [f.to_dict() for f in self.facts],
            "vault_paths": list(self.vault_paths),
            "ontology": self.ontology.to_dict(),
            "status": self.status,
            "knowledge_object_ids": [str(i) for i in self.knowledge_object_ids],
            "stage_timings_ms": dict(self.stage_timings_ms),
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WriteTrace":
        return cls(
            schema_version=data.get(
                "schema_version", CURRENT_WRITE_TRACE_SCHEMA_VERSION
            ),
            pipeline_version=data.get("pipeline_version", 0),
            extractor_prompt_version=data.get("extractor_prompt_version", 0),
            trace_id=UUID(data["trace_id"]),
            conversation_id=(
                UUID(data["conversation_id"])
                if data.get("conversation_id") is not None
                else None
            ),
            source=(
                SourceType(data["source"]) if data.get("source") is not None else None
            ),
            external_key=data.get("external_key"),
            mode=data.get("mode", "first_run"),
            checkpoint=CheckpointStageTrace.from_dict(data.get("checkpoint", {})),
            working_contexts=data.get("working_contexts"),
            extractor=(
                ExtractorStageTrace.from_dict(data["extractor"])
                if data.get("extractor") is not None
                else None
            ),
            facts=tuple(FactTrace.from_dict(f) for f in data.get("facts", [])),
            vault_paths=tuple(data.get("vault_paths", [])),
            ontology=OntologyStageTrace.from_dict(data.get("ontology", {})),
            status=data.get("status", "success"),
            knowledge_object_ids=tuple(
                UUID(i) for i in data.get("knowledge_object_ids", [])
            ),
            stage_timings_ms=dict(data.get("stage_timings_ms", {})),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if "created_at" in data
                else datetime.utcnow()
            ),
        )
