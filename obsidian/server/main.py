"""Haven's real production HTTP server.

::

    Browser Extension
            |
            v
        FastAPI (this module)
            |
            v
        MemoryEngine
            |
            v
        Real Haven Vault (Markdown files on disk)

Backed by the same collaborators :mod:`benchmarks.adapters.haven_adapter`
already proves out (``VaultWriter``, ``OntologyPipeline``, ``ConceptGraph``,
``MemoryStore``, ``AliasIndex``, ``MemoryEngine``) — this module only points
them at a persistent vault/concept directory instead of a benchmark temp
directory, and exposes the read path over HTTP.

``concept_graph`` is loaded once at startup and held for the process
lifetime; ``POST /memory`` mutates it in place via ``ontology_pipeline``,
so every request after a write sees the same, already-updated instance.
``memory_store`` and ``alias_index`` are rebuilt from disk at the top of
every request, exactly like ``HavenAdapter.search()`` does, so edits made
directly to the vault on disk (e.g. hand-editing Markdown) are picked up
without a server restart.

``vault_writer``, ``ontology_pipeline``, and ``manager_pipeline`` are
constructed here and stored on ``app.state``. ``POST /memory`` is the
write endpoint: it turns the request into a ``Conversation`` -- a full
multi-turn one when the request carries ``conversation``, otherwise the
legacy single-event one built from ``canonical_fact`` -- runs it through
``manager_pipeline`` (Extractor -> Classifier -> ImportanceScorer ->
CanonicalMatcher -> KnowledgeUpdater), then persists every resulting
``KnowledgeObject`` via ``app.state.vault_writer.write`` followed by
``app.state.ontology_pipeline.process`` — see that route's own docstring
for the full contract.

``checkpoint_store``/``checkpoint_writer`` (see
:mod:`obsidian.checkpoint`) are the same "reload from disk every
request, then persist after success" shape as ``vault_writer``/
``memory_store`` above, but are only ever consulted when a request
carries ``external_key`` -- see ``POST /memory``'s own docstring for why
that keeps every existing caller's behaviour byte-for-byte unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
import time
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace as dataclasses_replace
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

import yaml
from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from obsidian.checkpoint.diff import TurnDiff, classify_turns
from obsidian.checkpoint.hashing import transcript_hash, turn_hash
from obsidian.checkpoint.identity import derive_conversation_id
from obsidian.checkpoint.models import CheckpointRun, ConversationCheckpoint
from obsidian.checkpoint.store import CheckpointStore
from obsidian.checkpoint.writer import CheckpointWriter
from obsidian.core.enums import MemoryType, Role, SourceType
from obsidian.core.errors import MemoryEngineError
from obsidian.core.types import Conversation, Event
from obsidian.integrations.obsidian import importer as obsidian_importer
from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import EXTRACTOR_PROMPT_VERSION, Extractor, ExtractionTrace
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.llm import ManagerAILLM
from obsidian.manager_ai.models import (
    ClassificationResult,
    ExtractedFact,
    ExtractionDecision,
    ImportanceResult,
    KnowledgeObject,
)
from obsidian.manager_ai.pipeline import PIPELINE_VERSION, ManagerPipeline
from obsidian.memory_engine.engine import MemoryEngine
from obsidian.memory_engine.memory_store import MemoryStore
from obsidian.memory_engine.query_rewriter import QueryRewriter
from obsidian.memory_engine.vault_writer import VaultWriter
from obsidian.ontology.alias_index import AliasIndex
from obsidian.ontology.concept_graph_loader import ConceptGraphLoader
from obsidian.ontology.concept_parser import ConceptParser
from obsidian.ontology.models import Concept
from obsidian.ontology.ontology_pipeline import OntologyPipeline
from obsidian.ontology.retrieval_config import RetrievalConfig
from obsidian.ontology.retrieval_models import WorkingContext
from obsidian.ontology.write_trace_models import (
    CURRENT_WRITE_TRACE_SCHEMA_VERSION,
    CheckpointStageTrace,
    ExtractorStageTrace,
    FactTrace,
    OntologyProposalTrace,
    OntologyStageTrace,
    WriteTrace,
)
from obsidian.ontology.write_trace_store import WriteTraceStore, WriteTraceWriter
from obsidian.server import demo_seed
from obsidian.server.benchmark_explorer import router as benchmark_explorer_router
from obsidian.server.dashboard import _to_working_context_summary
from obsidian.server.dashboard import router as dashboard_router
from obsidian.server.schemas import (
    ActivateSpaceRequest,
    CancelMemoryRequest,
    CaptureNoteRequest,
    CaptureNoteResponse,
    CommitMemoryRequest,
    CommittedMemoryItem,
    ConversationTurnRequest,
    CreateSpaceRequest,
    ImportPreviewRequest,
    ImportScanNote,
    ImportScanRequest,
    ImportScanResponse,
    PreviewMemoryResponse,
    QueryRewritingSettingResponse,
    RetrieveContextRequest,
    RetrieveContextResponse,
    ReviewMemoryItem,
    ReviewSummary,
    SaveMemoryRequest,
    SaveMemoryResponse,
    SeedDemoResponse,
    SelectVaultRequest,
    SelectVaultResponse,
    SpaceInfo,
    SpacesListResponse,
    UpdateQueryRewritingSettingRequest,
    UpdateSpaceRequest,
    VaultInfo,
    WorkingContextPreviewRequest,
    WorkingContextPreviewResponse,
)

logger = logging.getLogger(__name__)

#: Verbose per-request pipeline logging (decision list, write-trace
#: persistence failures) -- disabled by default. Set
#: ``HAVEN_DEBUG_PIPELINE_LOGGING=true`` to enable while diagnosing a
#: "Remember" issue; see the same flag in ``obsidian.manager_ai.extractor``.
_DEBUG_PIPELINE_LOGGING = os.environ.get(
    "HAVEN_DEBUG_PIPELINE_LOGGING", "false"
).strip().lower() in ("true", "1", "yes")

#: Serializes the read-existing / match / persist span of ``save_memory``
#: and ``commit_memory`` (see their thin ``_write_lock``-holding wrappers
#: below), *and* every read route that touches the shared, unsynchronized
#: ``app.state.concept_graph`` (``retrieve_context``,
#: ``retrieve_working_context``, and dashboard.py's read routes via
#: ``app.state.write_lock`` -- same object). FastAPI dispatches these sync
#: ``def`` routes onto a shared thread pool, so two genuinely concurrent
#: writes could otherwise both read the same pre-write vault snapshot, both
#: decide "NEW" via CanonicalMatcher, and both persist -- producing two
#: KnowledgeObjects for one fact; separately, a read iterating
#: ``ConceptGraph.relationships()`` (a plain ``defaultdict(set)``, no
#: internal locking) while a concurrent write mutates it via
#: ``OntologyPipeline`` can raise ``RuntimeError: Set changed size during
#: iteration``. A single process-wide lock covering both read and write
#: spans is sufficient here (single-process, single-user deployment -- see
#: ``README.md``'s "single-user, no auth" note) and intentionally coarse:
#: correctness of the vault beats throughput for a personal,
#: low-concurrency tool.
_write_lock = threading.Lock()


def _load_concepts(concept_dir: Path) -> List[Concept]:
    """Return every Concept currently persisted in *concept_dir*.

    Mirrors ``HavenAdapter._load_concepts`` exactly: re-parses concept
    Markdown files directly rather than asking ``ConceptGraph`` to enumerate
    its concepts, since it has no such method today.
    """
    parser = ConceptParser()
    return [parser.read(path).concept for path in sorted(concept_dir.glob("*.md"))]


#: Number of already-processed turns immediately preceding the new evidence
#: to fold into the Working Context retrieval query for an incremental
#: save (see ``_build_working_context_query`` below). Fixed and small on
#: purpose: cross-checkpoint-boundary coreference almost always resolves
#: against the last exchange, and a constant-size window keeps the query
#: (and therefore the retrieval cost) independent of conversation length.
_WORKING_CONTEXT_ANCHOR_TURNS = 3


def _build_working_context_query(
    turns: List[Tuple[Role, str]], new_turn_start_index: int
) -> str:
    """Render the Working Context retrieval query for an incremental save.

    Concatenates a small, fixed-size window of the most recent
    already-processed turns (the "anchor") with every new-evidence turn
    (``turns[new_turn_start_index:]``), so the query carries enough
    grounding to retrieve something relevant even when the newest message
    alone is a short, ambiguous follow-up (e.g. "yeah, that one" has no
    keyword signal by itself). Deterministic and lightweight: a fixed
    window size, no LLM call, and a query whose size never grows with the
    conversation's total length -- unlike using the entire prior transcript
    as the query, which would dilute keyword/ontology retrieval with
    distant, likely-irrelevant history.

    The anchor turns are drawn from *turns* (already in memory) purely to
    build this query string; they are never added to the evidence sent to
    the Extractor.
    """
    anchor_start = max(0, new_turn_start_index - _WORKING_CONTEXT_ANCHOR_TURNS)
    query_turns = turns[anchor_start:]
    return "\n".join(f"[{role.value}] {content}" for role, content in query_turns)


def _elapsed_ms(start: float) -> float:
    """Return milliseconds elapsed since *start* (a ``time.perf_counter()`` value)."""
    return (time.perf_counter() - start) * 1000.0


def _build_extractor_stage_trace(
    extraction_trace: ExtractionTrace, capture_llm_io: bool
) -> ExtractorStageTrace:
    """Project an :class:`ExtractionTrace` into a persistable stage trace.

    Applies the ``HAVEN_WRITE_TRACE_CAPTURE_LLM_IO`` redaction toggle --
    a server-layer concern the Extractor/pipeline modules have no
    knowledge of (see ``ExtractionTrace``'s own docstring).
    """
    return ExtractorStageTrace(
        prompt=extraction_trace.prompt if capture_llm_io else None,
        raw_response=extraction_trace.raw_response if capture_llm_io else None,
        fact_count=len(extraction_trace.facts),
    )


def _build_fact_traces(
    decisions: List[ExtractionDecision],
) -> Tuple[FactTrace, ...]:
    """Project every :class:`ExtractionDecision` into a :class:`FactTrace`.

    Pure re-reading of fields the pipeline already computed -- nothing is
    recomputed with different logic (see ``FactTrace``'s own docstring for
    why ``fact_index`` matters).
    """
    traces: List[FactTrace] = []
    for i, d in enumerate(decisions):
        traces.append(
            FactTrace(
                fact_index=i,
                fact_text=d.fact.text if d.fact is not None else "",
                evidence=d.fact.evidence if d.fact is not None else "",
                confidence=d.fact.confidence if d.fact is not None else 0.0,
                memory_type=(
                    d.classification.memory_type
                    if d.classification is not None
                    else None
                ),
                classification_confidence=(
                    d.classification.confidence
                    if d.classification is not None
                    else None
                ),
                classification_reason=(
                    d.classification.reason if d.classification is not None else None
                ),
                importance_score=(
                    d.importance.score if d.importance is not None else None
                ),
                importance_reason=(
                    d.importance.reason if d.importance is not None else None
                ),
                decision=d.decision,
                knowledge_object_id=(
                    d.knowledge.id if d.knowledge is not None else None
                ),
                supersession_operation=(
                    d.supersession.operation if d.supersession is not None else None
                ),
                supersession_matched_identity=(
                    d.supersession.matched_identity
                    if d.supersession is not None
                    else None
                ),
                supersession_reason=(
                    d.supersession.reason if d.supersession is not None else None
                ),
            )
        )
    return tuple(traces)


def _persist_write_trace_best_effort(
    *,
    trace_id: UUID,
    conversation_id: Optional[UUID],
    source: Optional[SourceType],
    external_key: Optional[str],
    mode: str,
    checkpoint: CheckpointStageTrace,
    working_contexts: Optional[List[Dict[str, Any]]],
    extractor: Optional[ExtractorStageTrace],
    facts: Tuple[FactTrace, ...],
    vault_paths: Tuple[str, ...],
    ontology: OntologyStageTrace,
    status: str,
    knowledge_object_ids: Tuple[UUID, ...],
    stage_timings_ms: Dict[str, float],
) -> None:
    """Build and persist a :class:`WriteTrace`, never raising past this call.

    Trace capture is diagnostic, not part of the write's real contract --
    a bug here must never turn a successful (or a cleanly-rejected) write
    into a failed HTTP response. Same fail-open idiom ``save_memory``
    already uses for the Working Context lookup.
    """
    try:
        trace = WriteTrace(
            schema_version=CURRENT_WRITE_TRACE_SCHEMA_VERSION,
            pipeline_version=PIPELINE_VERSION,
            extractor_prompt_version=EXTRACTOR_PROMPT_VERSION,
            trace_id=trace_id,
            conversation_id=conversation_id,
            source=source,
            external_key=external_key,
            mode=mode,
            checkpoint=checkpoint,
            working_contexts=working_contexts,
            extractor=extractor,
            facts=facts,
            vault_paths=vault_paths,
            ontology=ontology,
            status=status,
            knowledge_object_ids=knowledge_object_ids,
            stage_timings_ms=stage_timings_ms,
        )
        app.state.write_trace_writer.write(trace)
    except Exception as exc:
        logger.warning("Failed to persist write trace: %r", exc)


def _persist_duplicate_write_trace(
    *,
    trace_id: UUID,
    conversation_id: Optional[UUID],
    request_source: SourceType,
    request_external_key: Optional[str],
    turn_count: int,
    incoming_transcript_hash: str,
    stage_timings_ms: Dict[str, float],
) -> None:
    """Persist the "duplicate" short-circuit trace shared by ``save_memory``
    and ``preview_memory``.

    Both routes reach this exact same conclusion the exact same way
    (:func:`_checkpoint_lookup` reporting an unchanged transcript against an
    existing checkpoint) -- this is the one place that trace shape is built,
    rather than duplicating it at each call site.
    """
    _persist_write_trace_best_effort(
        trace_id=trace_id,
        conversation_id=conversation_id,
        source=request_source,
        external_key=request_external_key,
        mode="duplicate",
        checkpoint=CheckpointStageTrace(
            mode="duplicate",
            had_existing_checkpoint=True,
            turn_count=turn_count,
            new_turn_start_index=0,
            transcript_hash=incoming_transcript_hash,
        ),
        working_contexts=None,
        extractor=None,
        facts=(),
        vault_paths=(),
        ontology=OntologyStageTrace(),
        status="duplicate",
        knowledge_object_ids=(),
        stage_timings_ms=stage_timings_ms,
    )


# ---------------------------------------------------------------------------
# Checkpoint lookup / Working Context lookup / persistence helpers
#
# Extracted out of what used to be save_memory's single body so the Memory
# Review preview/commit routes (see the routes near the bottom of this
# module) can reuse the exact same checkpoint-identity, Working-Context, and
# persistence logic without a second implementation. save_memory itself
# calls these in the same order it always inlined them in, so its own
# behaviour is unchanged -- Quick Capture and demo seeding (both of which
# call save_memory directly, in-process) are unaffected byte-for-byte.
# ---------------------------------------------------------------------------


@dataclass
class CheckpointLookupResult:
    """Everything :func:`_checkpoint_lookup` determines about one request.

    ``is_duplicate=True`` means the transcript exactly matches
    ``existing_checkpoint`` -- the caller must stop immediately (no
    extraction, no pipeline) and report ``status="duplicate"``. In that
    case ``diff`` is meaningless and left at its default ("first_run");
    callers must check ``is_duplicate`` before consulting ``diff``.
    """

    conversation_id: Optional[UUID]
    existing_checkpoint: Optional[ConversationCheckpoint]
    had_existing_checkpoint: bool
    incoming_turn_hashes: List[str]
    incoming_transcript_hash: str
    diff: TurnDiff
    is_duplicate: bool


def _checkpoint_lookup(
    request: SaveMemoryRequest, turns: List[Tuple[Role, str]]
) -> CheckpointLookupResult:
    """Derive conversation identity and classify this request's turns.

    Mirrors exactly what ``save_memory`` used to inline at its top: when
    ``request.external_key`` is absent, checkpointing never engages at all
    (``conversation_id=None``, ``diff`` stays ``"first_run"``) -- see this
    module's docstring for why that keeps every non-opted-in caller's
    behaviour unchanged.
    """
    conversation_id: Optional[UUID] = None
    existing_checkpoint: Optional[ConversationCheckpoint] = None
    had_existing_checkpoint = False
    incoming_turn_hashes: List[str] = []
    incoming_transcript_hash = ""
    diff = TurnDiff(mode="first_run", new_turn_start_index=0)

    if request.external_key:
        conversation_id = derive_conversation_id(request.source, request.external_key)
        incoming_turn_hashes = [
            turn_hash(role.value, content) for role, content in turns
        ]
        incoming_transcript_hash = transcript_hash(incoming_turn_hashes)

        app.state.checkpoint_store.load()
        if app.state.checkpoint_store.has(conversation_id):
            existing_checkpoint = app.state.checkpoint_store.get(conversation_id)
            had_existing_checkpoint = True
            if existing_checkpoint.transcript_hash == incoming_transcript_hash:
                return CheckpointLookupResult(
                    conversation_id=conversation_id,
                    existing_checkpoint=existing_checkpoint,
                    had_existing_checkpoint=True,
                    incoming_turn_hashes=incoming_turn_hashes,
                    incoming_transcript_hash=incoming_transcript_hash,
                    diff=diff,
                    is_duplicate=True,
                )

        diff = classify_turns(existing_checkpoint, incoming_turn_hashes)

    return CheckpointLookupResult(
        conversation_id=conversation_id,
        existing_checkpoint=existing_checkpoint,
        had_existing_checkpoint=had_existing_checkpoint,
        incoming_turn_hashes=incoming_turn_hashes,
        incoming_transcript_hash=incoming_transcript_hash,
        diff=diff,
        is_duplicate=False,
    )


def _lookup_working_context(
    diff: TurnDiff, turns: List[Tuple[Role, str]]
) -> Tuple[Optional[List[WorkingContext]], Optional[List[Dict[str, Any]]]]:
    """Best-effort Working Context lookup for an ``"incremental"`` save.

    Mirrors exactly what ``save_memory`` used to inline -- see that route's
    docstring section "Incremental ingestion with Working Context". Returns
    ``(None, None)`` for any non-incremental ``diff.mode``, or if the lookup
    itself fails for any reason (never raises past this function).
    """
    existing_context: Optional[List[WorkingContext]] = None
    if diff.mode == "incremental":
        try:
            app.state.alias_index.rebuild(_load_concepts(app.state.concept_dir))
            engine = _build_memory_engine()
            raw_query = _build_working_context_query(turns, diff.new_turn_start_index)
            existing_context = engine.query_working_context(raw_query)
        except Exception as exc:
            logger.warning(
                "Working Context lookup failed, continuing without it: %r", exc
            )
            existing_context = None

    working_contexts_projected: Optional[List[Dict[str, Any]]] = None
    if existing_context is not None:
        try:
            working_contexts_projected = [
                _to_working_context_summary(
                    context, app.state.concept_graph
                ).model_dump()
                for context in existing_context
            ]
        except Exception:
            logger.debug("Working Context projection failed for review preview", exc_info=True)
            working_contexts_projected = None

    return existing_context, working_contexts_projected


def _stamp_provenance(
    knowledge_objects: List[KnowledgeObject],
    provenance: Optional[Dict[str, Any]],
) -> List[KnowledgeObject]:
    """Return *knowledge_objects* with *provenance* recorded on each.

    Attaches the provenance dict under ``metadata["provenance"]`` via
    ``dataclasses.replace`` (KnowledgeObject is frozen), preserving every
    other metadata key. A no-op returning the list unchanged when
    ``provenance is None`` -- which is every caller except the Obsidian vault
    import, so no existing write path is affected. ``KnowledgeObject.metadata``
    already round-trips through VaultWriter/MemoryStore, so nothing downstream
    needs to change to persist or read this back.
    """
    if not provenance:
        return knowledge_objects
    return [
        dataclasses_replace(
            k, metadata={**k.metadata, "provenance": dict(provenance)}
        )
        for k in knowledge_objects
    ]


def _persist_knowledge_objects(
    knowledge_objects: List[KnowledgeObject],
) -> Tuple[List[Path], List[Path], List[Any]]:
    """Write every knowledge object to the vault, then the concept graph.

    Mirrors exactly what ``save_memory`` used to inline: for each object,
    ``vault_writer.write`` then ``ontology_pipeline.process_with_trace``, in
    that order. Returns ``(vault_paths, ontology_concept_paths,
    ontology_validation_results)``.

    Raises
    ------
    HTTPException
        500 if the underlying filesystem write fails (e.g. the vault
        directory became unwritable/permission-denied, or was deleted out
        from under a live server) -- ``VaultWriter``/``OntologyPipeline``
        raise a raw ``OSError`` with no handling of their own, and this is
        the one place both ``save_memory`` and ``commit_memory`` funnel
        through, so catching here covers both without duplicating the
        guard at each call site.
    """
    vault_paths: List[Path] = []
    ontology_concept_paths: List[Path] = []
    ontology_validation_results: List[Any] = []
    try:
        for knowledge in knowledge_objects:
            vault_paths.append(app.state.vault_writer.write(knowledge))
            concept_paths, validation_results = (
                app.state.ontology_pipeline.process_with_trace(knowledge)
            )
            ontology_concept_paths.extend(concept_paths)
            ontology_validation_results.extend(validation_results)
    except OSError as exc:
        logger.exception("Failed to write knowledge object(s) to the vault")
        raise HTTPException(
            status_code=500, detail=f"Could not write to the vault: {exc}"
        ) from exc
    return vault_paths, ontology_concept_paths, ontology_validation_results


def _write_checkpoint(
    *,
    conversation_id: Optional[UUID],
    request_source: SourceType,
    request_external_key: Optional[str],
    turns: List[Tuple[Role, str]],
    diff: TurnDiff,
    incoming_turn_hashes: List[str],
    incoming_transcript_hash: str,
    existing_checkpoint: Optional[ConversationCheckpoint],
    knowledge_objects: List[KnowledgeObject],
    decision_counts: Dict[str, int],
) -> None:
    """Persist a ``ConversationCheckpoint``, exactly as ``save_memory`` used
    to inline it -- a no-op when ``conversation_id is None`` (the request
    never opted into checkpointing at all).

    Best-effort like ``_persist_write_trace_best_effort``: by the time this
    runs, the real ``KnowledgeObject``(s) are already durably written (see
    ``_persist_knowledge_objects``, called before this in both
    ``save_memory`` and ``commit_memory``), so a filesystem failure here
    must not turn an already-successful save into an error response --
    only checkpoint-based duplicate/incremental detection on a *future*
    request would degrade (to "first_run" behaviour), not this one.
    """
    if conversation_id is None:
        return

    now = datetime.utcnow()
    new_checkpoint = ConversationCheckpoint(
        conversation_id=conversation_id,
        source=request_source,
        external_key=request_external_key,
        turn_count=len(turns),
        last_processed_turn_index=len(turns) - 1,
        turn_hashes=incoming_turn_hashes,
        transcript_hash=incoming_transcript_hash,
        created_at=(
            existing_checkpoint.created_at
            if existing_checkpoint is not None
            else now
        ),
        last_processed_at=now,
        knowledge_object_ids=(
            (
                existing_checkpoint.knowledge_object_ids
                if existing_checkpoint is not None
                else []
            )
            + [knowledge.id for knowledge in knowledge_objects]
        ),
        processing_history=(
            (
                existing_checkpoint.processing_history
                if existing_checkpoint is not None
                else []
            )
            + [
                CheckpointRun(
                    processed_at=now,
                    turn_range=(diff.new_turn_start_index, len(turns)),
                    knowledge_object_ids=[
                        knowledge.id for knowledge in knowledge_objects
                    ],
                    decision_counts=dict(decision_counts),
                    mode=diff.mode,
                )
            ]
        ),
    )
    try:
        app.state.checkpoint_writer.write(new_checkpoint)
    except OSError as exc:
        logger.warning("Failed to persist checkpoint, continuing: %r", exc)


# ---------------------------------------------------------------------------
# Memory Review: PendingReview (preview -> commit/cancel)
#
# A PendingReview holds only what commit cannot regenerate without either
# re-running the LLM (scored_facts) or re-deriving checkpoint identity from
# scratch (everything else here). Nothing UI-derived is stored -- the
# review dialog's own display data (ReviewMemoryItem) is built fresh from
# scored_facts every time, never cached separately.
# ---------------------------------------------------------------------------


@dataclass
class PendingReview:
    conversation_id: Optional[UUID]
    source: SourceType
    external_key: Optional[str]
    turns: List[Tuple[Role, str]]
    existing_checkpoint: Optional[ConversationCheckpoint]
    had_existing_checkpoint: bool
    incoming_turn_hashes: List[str]
    incoming_transcript_hash: str
    diff: TurnDiff
    extractor_stage_trace: ExtractorStageTrace
    working_contexts_projected: Optional[List[Dict[str, Any]]]
    scored_facts: List[Tuple[ExtractedFact, ClassificationResult, ImportanceResult]]
    # Optional provenance to stamp onto every committed KnowledgeObject (see
    # _stamp_provenance). Carried from the preview request so commit can
    # apply it without the client resupplying it. None for every caller
    # except the Obsidian vault import.
    provenance: Optional[Dict[str, Any]] = None


#: Bound on how many pending reviews can accumulate in memory at once.
#: Cancel (POST /memory/cancel) removes an entry immediately when the user
#: dismisses the review dialog; this eviction is only a backstop for
#: reviews abandoned without an explicit Cancel (browser crash, tab
#: closed), mirroring WriteTraceWriter's own oldest-first pruning.
_PENDING_REVIEW_MAX_COUNT = 50


def _evict_pending_reviews() -> None:
    reviews: Dict[UUID, PendingReview] = app.state.pending_reviews
    excess = len(reviews) - _PENDING_REVIEW_MAX_COUNT
    if excess <= 0:
        return
    for key in list(reviews.keys())[:excess]:
        reviews.pop(key, None)


# ---------------------------------------------------------------------------
# Vault selection
#
# Three tiers, checked in order, so every existing caller (tests,
# benchmarks, scripts/seed_demo.py -- all of which set HAVEN_VAULT_DIR and
# friends directly) keeps today's exact behaviour unchanged:
#
# 1. Explicit HAVEN_VAULT_DIR/HAVEN_CONCEPT_DIR/HAVEN_CHECKPOINT_DIR/
#    HAVEN_WRITE_TRACE_DIR env vars -- unchanged from before this feature
#    existed.
# 2. A vault root previously chosen via POST /api/v1/vault, persisted to
#    _VAULT_CONFIG_PATH so it survives a server restart.
# 3. Today's unconfigured default (haven_data/*).
#
# Tiers 2 sees a single "vault root" the user opens in Obsidian, nesting
# Haven's own directories inside it: vault/ and concepts/ are visible in
# Obsidian (memory notes carry real [[wikilinks]] to concept notes, so both
# must live inside the same opened vault for those links to resolve);
# .haven/ is hidden bookkeeping (checkpoints, write traces) no one is
# meant to browse as notes -- mirroring Obsidian's own .obsidian/
# convention for "this folder is metadata, not content".
# ---------------------------------------------------------------------------

_VAULT_CONFIG_PATH = Path("config/vault_selection.json")
_ENV_VAULT_VARS = (
    "HAVEN_VAULT_DIR",
    "HAVEN_CONCEPT_DIR",
    "HAVEN_CHECKPOINT_DIR",
    "HAVEN_WRITE_TRACE_DIR",
)


def _paths_for_root(root: Path) -> Tuple[Path, Path, Path, Path]:
    """The four vault-scoped directories Haven needs, nested under *root*."""
    return (
        root / "vault",
        root / "concepts",
        root / ".haven" / "checkpoints",
        root / ".haven" / "write_traces",
    )


def _load_persisted_vault_root() -> Optional[Path]:
    if not _VAULT_CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(_VAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    root = data.get("vault_root")
    return Path(root) if root else None


def _save_persisted_vault_root(root: Path) -> None:
    _VAULT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _VAULT_CONFIG_PATH.write_text(
        json.dumps({"vault_root": str(root)}), encoding="utf-8"
    )


def _resolve_initial_vault_paths() -> Tuple[Optional[Path], Path, Path, Path, Path]:
    """Resolve the four vault-scoped directories at server startup.

    Returns ``(root, vault_dir, concept_dir, checkpoint_dir,
    write_trace_dir)`` -- *root* is ``None`` for tiers 1 and 3 (there is no
    single enclosing folder to report/open in Obsidian; see
    ``VaultInfo.root``'s docstring), and the persisted root for tier 2.
    """
    if any(os.environ.get(var) for var in _ENV_VAULT_VARS):
        return (
            None,
            Path(os.environ.get("HAVEN_VAULT_DIR", "haven_data/vault")),
            Path(os.environ.get("HAVEN_CONCEPT_DIR", "haven_data/concepts")),
            Path(os.environ.get("HAVEN_CHECKPOINT_DIR", "haven_data/checkpoints")),
            Path(os.environ.get("HAVEN_WRITE_TRACE_DIR", "haven_data/write_traces")),
        )
    persisted_root = _load_persisted_vault_root()
    if persisted_root is not None:
        return (persisted_root, *_paths_for_root(persisted_root))
    return (
        None,
        Path("haven_data/vault"),
        Path("haven_data/concepts"),
        Path("haven_data/checkpoints"),
        Path("haven_data/write_traces"),
    )


def _remove_with_retry(
    func: Any, path: str, attempts: int, delay: float
) -> None:
    """Call ``func(path)`` (``os.remove``/``os.rmdir``), retrying transient failures.

    Shared retry primitive for :func:`_robust_rmtree` and :func:`_rename_aside`
    -- see their docstrings for *why* a single remove/rmdir/rename call needs
    this on Windows. Each retry also clears the path's read-only attribute
    first (the other common Windows delete failure, e.g. files checked out
    or synced with it set -- harmless to attempt even when the real cause
    was a lock, not a permission bit).
    """
    last_exc: Optional[OSError] = None
    for attempt in range(attempts):
        try:
            func(path)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last_exc = exc
            try:
                os.chmod(path, stat.S_IWRITE)
            except OSError:
                pass
            time.sleep(delay * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _robust_rmtree(directory: Path, *, attempts: int = 6, delay: float = 0.05) -> None:
    """Delete *directory* and everything under it, tolerating transient Windows locks.

    Plain ``shutil.rmtree`` raises the instant any single file under
    *directory* is briefly locked by another process holding an open
    handle without ``FILE_SHARE_DELETE`` -- e.g. OneDrive syncing a
    just-written demo file, Windows Search indexing it, antivirus scanning
    it, or (for a real local vault -- see this function's caller and the
    ``notes_dir`` comment below: ``vault_dir`` is meant to be browsable in a
    live Obsidian window) the Obsidian desktop app's own file watcher. All
    of these release the lock again within milliseconds; POSIX has no
    equivalent restriction (unlinking an open file just detaches the
    directory entry), which is why this class of failure is specific to
    Windows and to real local vaults rather than showing up in CI's ``tmp``
    dirs. Walks the tree manually (rather than relying on
    ``shutil.rmtree``'s ``onexc``/``onerror`` callback, whose signature
    differs across the Python versions this package supports) so each
    individual remove/rmdir call gets its own short retry-with-backoff
    before the whole operation gives up and re-raises the last error.
    """
    if not directory.exists():
        return

    for root, dirs, files in os.walk(directory, topdown=False):
        for name in files:
            _remove_with_retry(os.remove, os.path.join(root, name), attempts, delay)
        for name in dirs:
            _remove_with_retry(os.rmdir, os.path.join(root, name), attempts, delay)
    _remove_with_retry(os.rmdir, str(directory), attempts, delay)


def _rename_aside(
    directory: Path, suffix: str, *, attempts: int = 6, delay: float = 0.05
) -> Optional[Path]:
    """Rename *directory* to a same-parent sibling, or return ``None`` if it
    doesn't exist.

    Unlike deletion, a rename is a metadata-only operation -- it does not
    require every file inside to be lock-free first -- so this is what
    actually makes :func:`reset_demo_data` atomic-ish: the instant this call
    returns, *directory*'s original name is free for a fresh, empty
    directory to be created in its place, whether or not the old contents
    (now under the returned backup path) have finished settling. Still
    retried via :func:`_remove_with_retry`'s sibling logic, since the rename
    itself briefly touches the directory's own metadata and can hit the same
    transient-lock window described in :func:`_robust_rmtree`.
    """
    if not directory.exists():
        return None
    backup = directory.with_name(directory.name + suffix)
    last_exc: Optional[OSError] = None
    for attempt in range(attempts):
        try:
            directory.rename(backup)
            return backup
        except OSError as exc:
            last_exc = exc
            time.sleep(delay * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _configure_vault_state(
    vault_dir: Path, concept_dir: Path, checkpoint_dir: Path, write_trace_dir: Path
) -> None:
    """(Re)build every vault-scoped collaborator on ``app.state``.

    Called at startup and whenever the active vault changes
    (``POST /api/v1/vault``) or is cleared (``POST /api/v1/dev/reset_demo``).
    Never touches ``app.state.manager_pipeline`` -- Manager AI's LLM
    configuration is independent of which vault is active.
    """
    vault_dir.mkdir(parents=True, exist_ok=True)
    concept_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    write_trace_dir.mkdir(parents=True, exist_ok=True)

    # Quick Capture saves the *original* Markdown note here (see
    # ``capture_note``). A sibling of ``vault_dir`` on purpose: it must be
    # browsable in the same opened Obsidian vault, yet must NOT sit under
    # ``vault_dir`` -- ``MemoryStore``/``VaultIndex`` recursively glob
    # ``vault_dir`` for ``*.md`` and would try (and fail) to parse a raw
    # note as a KnowledgeObject. Never cleared by ``POST /dev/reset_demo``:
    # captured notes are user content, not demo data.
    notes_dir = vault_dir.parent / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    concept_graph = ConceptGraphLoader().load(concept_dir)

    app.state.vault_dir = vault_dir
    app.state.notes_dir = notes_dir
    app.state.concept_dir = concept_dir
    app.state.checkpoint_dir = checkpoint_dir
    app.state.write_trace_dir = write_trace_dir
    # Same "reload from disk every request" contract as memory_store below
    # (see module docstring); checkpoint_writer mirrors vault_writer's
    # "construct once, write() persists immediately" shape.
    app.state.checkpoint_store = CheckpointStore(checkpoint_dir)
    app.state.checkpoint_writer = CheckpointWriter(checkpoint_dir)
    # Write Inspector persistence -- see obsidian/ontology/write_trace_store.py.
    # A trace is captured for every POST /memory call (see save_memory), not
    # gated on external_key like checkpoints are, so even legacy callers get
    # debuggability. HAVEN_WRITE_TRACE_MAX_COUNT<=0 disables pruning.
    write_trace_max_count = int(os.environ.get("HAVEN_WRITE_TRACE_MAX_COUNT", "500"))
    app.state.write_trace_store = WriteTraceStore(write_trace_dir)
    app.state.write_trace_writer = WriteTraceWriter(
        write_trace_dir, max_count=write_trace_max_count
    )
    app.state.write_trace_capture_llm_io = (
        os.environ.get("HAVEN_WRITE_TRACE_CAPTURE_LLM_IO", "true").strip().lower()
        not in ("false", "0", "no")
    )
    # concept_graph is passed through so VaultWriter can render Obsidian
    # wiki-links for whatever Concepts are already attached at write time
    # (see obsidian/memory_engine/vault_writer.py's module docstring) —
    # purely additive; the documented vault_writer.write() then
    # ontology_pipeline.process() call order below is unchanged.
    app.state.vault_writer = VaultWriter(vault_dir, concept_graph=concept_graph)
    app.state.concept_graph = concept_graph
    app.state.ontology_pipeline = OntologyPipeline(concept_graph, concept_dir)
    app.state.memory_store = MemoryStore(vault_dir)
    app.state.alias_index = AliasIndex()
    app.state.retrieval_config = RetrievalConfig()
    # Memory Review pending state (see PendingReview above) -- tied to
    # whichever vault is active, so switching vaults (POST /api/v1/vault)
    # or resetting demo data discards any in-flight review rather than
    # letting it commit against the wrong vault.
    app.state.pending_reviews = {}


def _vault_info(root: Optional[Path], configured: bool) -> VaultInfo:
    app.state.memory_store.load()
    return VaultInfo(
        configured=configured,
        root=str(root) if root is not None else None,
        vault_dir=str(app.state.vault_dir),
        concept_dir=str(app.state.concept_dir),
        is_existing_obsidian_vault=(
            (root / ".obsidian").is_dir() if root is not None else None
        ),
        memory_count=app.state.memory_store.count(),
    )


# ---------------------------------------------------------------------------
# Memory Spaces
#
# A Memory Space is the only vault-shaped concept the dashboard exposes to
# the user; internally it's just a registered set of the same four
# directories ``_configure_vault_state`` already knows how to (re)build.
#
# ``_SPACES_CONFIG_PATH`` is the long-term source of truth going forward --
# ``_VAULT_CONFIG_PATH``/``_load_persisted_vault_root`` above are read
# exactly once, during one-time migration, and never written to by any of
# the code below.
#
# A space's four directories are computed via ``_paths_for_root`` exactly
# once -- at creation time, or during migration copied verbatim from
# whatever tier ``_resolve_initial_vault_paths`` already resolves -- and
# then stored, never recomputed from ``root`` again on activation.
# Re-deriving them from ``root`` on every activation would silently move an
# unconfigured (tier-3) deployment's *actual* flat ``haven_data/checkpoints``/
# ``haven_data/write_traces`` layout to ``_paths_for_root``'s nested
# ``.haven/checkpoints`` convention, orphaning existing checkpoints/write
# traces (``vault_dir``/``concept_dir`` happen to coincide either way, which
# is exactly why that bug would slip past casual testing).
# ---------------------------------------------------------------------------

_SPACES_CONFIG_PATH = Path("config/spaces.json")


def _spaces_env_managed() -> bool:
    """Whether this deployment is currently pinned by HAVEN_VAULT_DIR-style
    env vars -- while true, there's no single "root" to register spaces
    against, so space create/edit/delete/activate all reject (see each
    route's docstring)."""
    return any(os.environ.get(var) for var in _ENV_VAULT_VARS)


#: Hostnames the dashboard itself treats as "running locally" -- see the
#: matching ``['localhost', '127.0.0.1'].includes(location.hostname)`` check
#: in dashboard.html, which hides the folder-selector UI on any other host.
_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1"}


def _is_local_request(http_request: Request) -> bool:
    """Whether *http_request* reached Haven via localhost/127.0.0.1.

    A pasted-in absolute filesystem path (a Memory Space root, an Obsidian
    import folder) only resolves to something real when the browser and the
    Haven process share a filesystem -- true for the Quick Start's local
    ``uvicorn`` but not for a remote deployment (e.g. the Alibaba Cloud
    backend), where "C:\\Users\\you\\Vault" or "/home/you/Vault" refers to
    the *browser's* machine, not the server's. The dashboard already hides
    its folder-selector UI once ``location.hostname`` isn't local; this is
    the server-side half of that contract, so a request that reaches a route
    directly (bypassing the hidden UI) is rejected the same way rather than
    silently touching the wrong filesystem.
    """
    return http_request.url.hostname in _LOCAL_HOSTNAMES


_LOCAL_PATH_REMOTE_ERROR = (
    "Local filesystem paths cannot be used on remote deployments -- "
    "'root' refers to a path on your browser's machine, not the server's. "
    "Run Haven locally (accessed via localhost/127.0.0.1) to select a "
    "folder on this machine."
)


#: Fixed id for the single synthetic space an env-managed deployment always
#: gets. Deterministic (not ``uuid4()``) because that space is never
#: persisted (see ``_load_spaces_registry``/``_save_spaces_registry`` below)
#: -- every request re-synthesizes it fresh, and a stable id keeps
#: ``app.state.active_space_id`` consistent with whatever a later request
#: re-derives, exactly mirroring how tier 1 already never persists anything
#: to ``config/vault_selection.json`` either.
_ENV_MANAGED_SPACE_ID = "env-managed"


def _load_spaces_registry() -> Optional[dict]:
    # Tier 1 (env vars) never reads or writes any config file on disk --
    # same "every existing caller keeps today's exact behaviour unchanged"
    # contract _resolve_initial_vault_paths documents for
    # config/vault_selection.json above. Without this, every test/script/
    # CI run that sets HAVEN_VAULT_DIR (i.e. effectively all of them) would
    # read and write the real repo's config/spaces.json on every run.
    if _spaces_env_managed():
        return None
    if not _SPACES_CONFIG_PATH.exists():
        return None
    try:
        return json.loads(_SPACES_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save_spaces_registry(data: dict) -> None:
    if _spaces_env_managed():
        return
    _SPACES_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SPACES_CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _find_space(registry: dict, space_id: str) -> Optional[dict]:
    for space in registry["spaces"]:
        if space["id"] == space_id:
            return space
    return None


def _normalized_root(root: Optional[str]) -> Optional[Path]:
    return Path(root).resolve() if root else None


def _check_root_overlap(
    registry: dict, root: Path, *, ignore_space_id: Optional[str] = None
) -> None:
    """Raise 409 if *root* equals, contains, or sits inside any other
    registered space's root -- prevents ``_paths_for_root`` nesting one
    space's vault/concepts/.haven inside another's directory tree, which
    would let the two spaces silently see each other's files."""
    candidate = root.resolve()
    for space in registry["spaces"]:
        if space["id"] == ignore_space_id:
            continue
        existing = _normalized_root(space.get("root"))
        if existing is None:
            continue
        if (
            candidate == existing
            or candidate in existing.parents
            or existing in candidate.parents
        ):
            raise HTTPException(
                status_code=409,
                detail=f"{root} overlaps with the root already registered to "
                f"Memory Space '{space['name']}' ({existing}).",
            )


def _synthesize_spaces_registry() -> dict:
    """Migrate a pre-Memory-Spaces deployment into a single space.

    Copies whatever ``_resolve_initial_vault_paths`` already resolves for
    whichever of the 3 vault tiers is currently active, verbatim -- no
    recomputation via ``_paths_for_root`` -- so an existing deployment's
    real on-disk directory layout is preserved exactly. Named after the
    resolved root's folder (e.g. a vault at ``C:\\Users\\you\\MyVault``
    becomes a space named ``"MyVault"``) when there is one; falls back to
    ``"Default"`` for tiers 1 (env vars) and 3 (unconfigured), neither of
    which has a folder to name it after.
    """
    root, vault_dir, concept_dir, checkpoint_dir, write_trace_dir = (
        _resolve_initial_vault_paths()
    )
    env_managed = _spaces_env_managed()
    name = root.name if root is not None and root.name else "Default"
    # Deterministic id while env-managed (nothing is persisted in that mode,
    # so every re-synthesis must agree with app.state); a real random id for
    # tiers 2/3, which are persisted once and reused after.
    space_id = _ENV_MANAGED_SPACE_ID if env_managed else str(uuid4())
    registry = {
        "active_space_id": space_id,
        "spaces": [
            {
                "id": space_id,
                "name": name,
                "root": str(root) if root is not None else None,
                "vault_dir": str(vault_dir),
                "concept_dir": str(concept_dir),
                "checkpoint_dir": str(checkpoint_dir),
                "write_trace_dir": str(write_trace_dir),
                "env_managed": env_managed,
            }
        ],
    }
    _save_spaces_registry(registry)
    return registry


def _activate_space(registry: dict, space_id: str) -> None:
    """(Re)build ``app.state`` for *space_id* by delegating to the existing,
    completely unmodified ``_configure_vault_state`` -- switching Memory
    Spaces is nothing more than "look up this space's stored four
    directories, then rebuild app.state exactly like selecting a vault
    already does," which is also why every collaborator downstream of
    ``app.state`` (MemoryEngine, dashboard routes, Quick Capture, ...)
    automatically becomes space-consistent with zero further changes.
    """
    space = _find_space(registry, space_id)
    if space is None:
        raise HTTPException(
            status_code=404, detail=f"No Memory Space with id {space_id!r}."
        )
    _configure_vault_state(
        Path(space["vault_dir"]),
        Path(space["concept_dir"]),
        Path(space["checkpoint_dir"]),
        Path(space["write_trace_dir"]),
    )
    root = space.get("root")
    app.state.vault_root = Path(root) if root else None
    app.state.vault_configured = root is not None or space.get("env_managed", False)
    app.state.active_space_id = space_id
    # Recorded so the Obsidian import can stamp the active space's name into
    # each imported memory's provenance without re-reading the registry.
    app.state.active_space_name = space.get("name", "")
    registry["active_space_id"] = space_id
    _save_spaces_registry(registry)


# ---------------------------------------------------------------------------
# Hosted demo initialization (Alibaba Cloud deployment)
#
# A judge visiting the public hosted demo should see a fully populated
# dashboard on the very first request -- no folder to choose, no "Import
# Demo Data" click required (see deploy/alibaba-cloud/README.md and this
# module's ``_LOCAL_HOSTNAMES``/``IS_LOCAL_DEPLOYMENT`` local-vs-remote
# contract, which this reuses unchanged on the dashboard side).
#
# Gated on HAVEN_HOSTED_DEMO (set only in that deployment's haven.service,
# never in local dev, CI, or the test suite), so every other tier's startup
# behavior is completely unchanged -- this is one centralized bootstrap path,
# not a scattering of `if hosted` checks through the rest of the module.
#
# Runs at most once per deployment lifetime, from lifespan() below, exactly
# like _synthesize_spaces_registry's own migration path: the very first
# startup with no config/spaces.json yet builds and seeds the two bundled
# demo Memory Spaces once, persists the registry, and every later startup
# (or process restart) finds it already on disk and skips straight past this
# section entirely -- idempotent, no reseeding, no duplicate demo memories.
# ---------------------------------------------------------------------------

_HOSTED_DEMO_ENV_VAR = "HAVEN_HOSTED_DEMO"

#: (display name, directory slug) for each bundled demo Memory Space, in
#: activation order -- the first is always the one active by default after
#: bootstrap. Slugs (not names) name the on-disk folders under
#: haven_data/demo_spaces/, so renaming a space later (dashboard "Rename")
#: never orphans its own directory.
_HOSTED_DEMO_SPACES: Tuple[Tuple[str, str], ...] = (
    ("Haven Development", "haven_development"),
    ("Personal AI Research", "personal_ai_research"),
)


def _is_hosted_demo_deployment() -> bool:
    """Whether this process should bootstrap the bundled demo Memory Spaces.

    True only when HAVEN_HOSTED_DEMO is set truthy (the Alibaba Cloud
    ``haven.service`` unit) *and* this deployment isn't already env-managed
    via HAVEN_VAULT_DIR-style vars -- the two tiers are mutually exclusive,
    and an env-managed deployment never registers spaces at all (see
    ``_spaces_env_managed``).
    """
    return (
        os.environ.get(_HOSTED_DEMO_ENV_VAR, "").strip().lower() in ("1", "true", "yes")
        and not _spaces_env_managed()
    )


def _seed_hosted_demo_space(space: dict) -> None:
    """(Re)build ``app.state`` for *space* and write its bundled demo dataset
    into it in place -- the same bulk-facts-then-scripted-conversations
    sequence ``POST /api/v1/dev/seed_demo`` already uses (see
    ``demo_seed.dataset_for_space``), just called directly during startup
    instead of from a dashboard button click.
    """
    _configure_vault_state(
        Path(space["vault_dir"]),
        Path(space["concept_dir"]),
        Path(space["checkpoint_dir"]),
        Path(space["write_trace_dir"]),
    )
    app.state.active_space_name = space["name"]
    memories_file, _ = demo_seed.dataset_for_space(space["name"])
    demo_seed.seed_bulk_facts(
        app.state.vault_writer, app.state.ontology_pipeline, memories_file
    )
    _replay_demo_conversations()


def _bootstrap_hosted_demo_registry() -> dict:
    """Build and seed the bundled demo Memory Spaces (see
    ``_HOSTED_DEMO_SPACES``), persist the registry, and return it.

    Only ever called once per deployment, from ``lifespan()``, when
    ``_is_hosted_demo_deployment()`` is true and no ``config/spaces.json``
    exists yet -- see this section's module docstring for the idempotency
    contract that guarantees it never runs again afterward.
    """
    demo_root = Path("haven_data/demo_spaces")
    spaces = []
    for name, slug in _HOSTED_DEMO_SPACES:
        vault_dir, concept_dir, checkpoint_dir, write_trace_dir = _paths_for_root(
            demo_root / slug
        )
        spaces.append(
            {
                "id": str(uuid4()),
                "name": name,
                "root": str(demo_root / slug),
                "vault_dir": str(vault_dir),
                "concept_dir": str(concept_dir),
                "checkpoint_dir": str(checkpoint_dir),
                "write_trace_dir": str(write_trace_dir),
                "env_managed": False,
            }
        )

    registry = {"active_space_id": spaces[0]["id"], "spaces": spaces}
    for space in spaces:
        _seed_hosted_demo_space(space)
    _save_spaces_registry(registry)
    return registry


# ---------------------------------------------------------------------------
# Query Rewriting (optional, experimental multi-query expansion)
#
# A dashboard-facing on/off switch for the already-implemented
# obsidian.memory_engine.query_rewriter.QueryRewriter -- see that module's
# and obsidian.memory_engine.engine's docstrings for the (unmodified)
# rewrite/retrieve/merge pipeline this activates. Off (the default)
# reproduces retrieval's pre-existing, deterministic behaviour
# byte-for-byte: every MemoryEngine construction site below passes
# query_rewriter=None, exactly as it did before this setting existed.
#
# Server-level, not vault-scoped: set once in lifespan() (not
# _configure_vault_state, which reruns on every vault/space switch), so
# switching Memory Spaces never resets this toggle. A single shared
# QueryRewriter instance is always constructed -- building one does nothing
# but set up an empty cache (see that class's docstring); the outbound LLM
# call only happens if/when a request is actually served with the setting
# on -- so toggling app.state.query_rewriting_enabled at runtime (see
# update_query_rewriting_setting below) takes effect on the very next
# request, with no server restart and no MemoryEngine reconstruction.
#
# Persisted the same way _SPACES_CONFIG_PATH is: never read or written by a
# tier-1 (env-managed) deployment, so the test suite (which sets
# HAVEN_VAULT_DIR almost universally) never touches the real repo's config/
# directory. In that mode the toggle still works for the current process --
# only the "survives an actual restart" guarantee is unavailable, mirroring
# the same tier-1 limitation _load_spaces_registry already documents.
# ---------------------------------------------------------------------------

_QUERY_REWRITING_CONFIG_PATH = Path("config/query_rewriting_setting.json")


def _load_query_rewriting_enabled() -> bool:
    if _spaces_env_managed():
        return False
    if not _QUERY_REWRITING_CONFIG_PATH.exists():
        return False
    try:
        data = json.loads(_QUERY_REWRITING_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(data.get("enabled", False))


def _save_query_rewriting_enabled(enabled: bool) -> None:
    if _spaces_env_managed():
        return
    _QUERY_REWRITING_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _QUERY_REWRITING_CONFIG_PATH.write_text(
        json.dumps({"enabled": enabled}), encoding="utf-8"
    )


def _active_query_rewriter() -> Optional[QueryRewriter]:
    """The shared ``QueryRewriter`` when Query Rewriting is enabled, else ``None``.

    Read fresh at every ``MemoryEngine`` construction site (this module and
    ``obsidian.server.dashboard``) so a toggle takes effect on the very next
    call -- no restart, no engine rebuild.
    """
    return app.state.query_rewriter if app.state.query_rewriting_enabled else None


def _build_memory_engine() -> MemoryEngine:
    """Construct a ``MemoryEngine`` from the current ``app.state`` collaborators.

    Shared by every route in this module that needs a fresh engine (state
    like ``query_rewriting_enabled`` can change between requests, so engines
    aren't cached on ``app.state``).
    """
    return MemoryEngine(
        app.state.alias_index,
        app.state.concept_graph,
        app.state.memory_store,
        app.state.retrieval_config,
        query_rewriter=_active_query_rewriter(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Exposed on app.state so dashboard.py's read routes can also hold it
    # (see _write_lock's own docstring, and this module's MemoryEngineError
    # handler docstring) -- same Lock object either way, just reachable from
    # a router that can't import a main.py module-level name without a
    # circular import.
    app.state.write_lock = _write_lock

    # See "Query Rewriting" section above -- server-level, off by default,
    # untouched by vault/space switches below.
    app.state.query_rewriter = QueryRewriter()
    app.state.query_rewriting_enabled = _load_query_rewriting_enabled()

    # Single shared LLM instance for every Manager AI stage (see
    # obsidian/manager_ai/llm.py's module docstring) -- Extractor,
    # Classifier, and ImportanceScorer only ever call llm.generate(prompt),
    # so one client/model/timeout configuration serves all three. Built
    # before the spaces registry below: a hosted-demo bootstrap's scripted
    # conversation replay (_replay_demo_conversations) swaps this out and
    # back in, so it must already exist on app.state by that point.
    manager_llm = ManagerAILLM()
    app.state.manager_pipeline = ManagerPipeline(
        extractor=Extractor(llm=manager_llm),
        classifier=Classifier(llm=manager_llm),
        importance_scorer=ImportanceScorer(llm=manager_llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )

    registry = _load_spaces_registry()
    if registry is None:
        registry = (
            _bootstrap_hosted_demo_registry()
            if _is_hosted_demo_deployment()
            else _synthesize_spaces_registry()
        )
    _activate_space(registry, registry["active_space_id"])

    yield


app = FastAPI(title="Haven", lifespan=lifespan)


@app.exception_handler(MemoryEngineError)
async def _memory_engine_error_handler(
    request: Request, exc: MemoryEngineError
) -> JSONResponse:
    """Turn a vault-load failure into a clear 422 naming the offending path.

    ``MemoryStore.load()`` raises this for a vault file that fails to
    parse, is missing its ``id`` frontmatter, or collides on id -- most
    plausibly a stray non-Haven ``.md`` note a user drops directly into the
    opened Obsidian vault folder. Every read/write route that reloads the
    vault (``retrieve_context``, ``retrieve_working_context``,
    ``save_memory``, ``preview_memory``, ``commit_memory``, and the
    dashboard routes) calls ``memory_store.load()`` with no local
    try/except, so without this handler the request falls through to the
    generic 500 below -- opaque to the caller, with the actual offending
    filename only reaching the server log. Registered ahead of the
    catch-all ``Exception`` handler so Starlette's most-specific-handler
    lookup picks this one for ``MemoryEngineError`` instances.
    """
    logger.warning("Vault load failed on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Turn any exception no route explicitly caught into the same
    ``{"detail": ...}`` JSON shape every ``HTTPException`` already returns.

    Without this, FastAPI/Starlette's default behaviour for an uncaught
    exception is a plain-text 500 response -- every other error path in
    this API (see ``HTTPException`` usage throughout this module) returns
    JSON, so a caller that always parses the body as JSON (the extension's
    ``HavenClient``, the dashboard's ``fetch`` helpers) would otherwise hit
    a parse error on top of the original failure. This does not change
    *which* requests fail, only how a failure not already converted to a
    clean ``HTTPException`` is reported. Logged at ERROR with the full
    traceback first, mirroring the ``logger.exception(...)`` guards
    already used around risky calls in this module (e.g. ``save_memory``'s
    pipeline try/except).
    """
    logger.exception(
        "Unhandled exception on %s %s", request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )


router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/settings/query-rewriting", response_model=QueryRewritingSettingResponse)
def get_query_rewriting_setting() -> QueryRewritingSettingResponse:
    """Whether Query Rewriting (optional, experimental multi-query expansion)
    is enabled for this Haven server. Off by default."""
    return QueryRewritingSettingResponse(enabled=app.state.query_rewriting_enabled)


@router.put("/settings/query-rewriting", response_model=QueryRewritingSettingResponse)
def update_query_rewriting_setting(
    request: UpdateQueryRewritingSettingRequest,
) -> QueryRewritingSettingResponse:
    """Enable/disable Query Rewriting for this Haven server.

    Takes effect immediately -- no restart, and no ``MemoryEngine``
    reconstruction, is required (see ``_active_query_rewriter``, read fresh
    at every construction site). Persisted to ``_QUERY_REWRITING_CONFIG_PATH``
    so a server restart resumes at the same setting (see the "Query
    Rewriting" section above for the one exception: a tier-1, env-managed
    deployment, which never persists this to disk).
    """
    app.state.query_rewriting_enabled = request.enabled
    _save_query_rewriting_enabled(request.enabled)
    return QueryRewritingSettingResponse(enabled=request.enabled)


@router.get("/vault", response_model=VaultInfo)
def get_vault() -> VaultInfo:
    """Report the vault Haven is currently reading/writing.

    ``configured=False`` means no vault has ever been explicitly selected
    (the out-of-the-box default) -- the dashboard shows a first-run
    "select your vault" prompt in that case rather than the normal layout.
    """
    return _vault_info(app.state.vault_root, app.state.vault_configured)


@router.post("/vault", response_model=SelectVaultResponse)
def select_vault(
    request: SelectVaultRequest, http_request: Request
) -> SelectVaultResponse:
    """Select (and initialize, if necessary) an Obsidian vault root.

    *request.root* becomes the enclosing folder for Haven's four
    directories (see ``_paths_for_root``) -- the same folder the user is
    expected to open directly in Obsidian ("Open folder as vault"), so
    memory notes and concept notes (both browsable, cross-linked via
    ``[[wikilinks]]``) resolve correctly there.

    Non-destructive either way: an existing folder's contents (any
    pre-existing Obsidian vault, or a previously-initialized Haven vault)
    are never touched here beyond creating the ``vault/``/``concepts/``/
    ``.haven/`` subfolders if they don't already exist. Persists the choice
    to ``_VAULT_CONFIG_PATH`` so a server restart resumes at the same
    vault, then rebuilds every vault-scoped collaborator on ``app.state``
    in place -- no process restart needed for the change to take effect.

    Raises
    ------
    HTTPException
        400 if this deployment isn't being accessed locally (see
        :func:`_is_local_request`), *root* is not an absolute path, or it
        exists but is not a directory (e.g. it's a file).
    """
    if not _is_local_request(http_request):
        raise HTTPException(status_code=400, detail=_LOCAL_PATH_REMOTE_ERROR)
    root = Path(request.root)
    if not root.is_absolute():
        raise HTTPException(
            status_code=400,
            detail="Vault path must be absolute (e.g. C:\\Users\\you\\MyVault "
            "or /home/you/MyVault).",
        )
    if root.exists() and not root.is_dir():
        raise HTTPException(
            status_code=400, detail=f"{root} exists but is not a directory."
        )

    created = not root.exists()
    vault_dir, concept_dir, checkpoint_dir, write_trace_dir = _paths_for_root(root)
    initialized = not (vault_dir.exists() or concept_dir.exists())

    try:
        _configure_vault_state(vault_dir, concept_dir, checkpoint_dir, write_trace_dir)
    except OSError as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not initialize {root}: {exc}"
        ) from exc

    _save_persisted_vault_root(root)
    app.state.vault_root = root
    app.state.vault_configured = True

    info = _vault_info(root, True)
    return SelectVaultResponse(created=created, initialized=initialized, **info.model_dump())


def _space_info(space: dict) -> SpaceInfo:
    return SpaceInfo(
        id=space["id"],
        name=space["name"],
        root=space.get("root"),
        env_managed=space.get("env_managed", False),
    )


@router.get("/spaces", response_model=SpacesListResponse)
def list_spaces() -> SpacesListResponse:
    """List every registered Memory Space and report which one is active."""
    registry = _load_spaces_registry() or _synthesize_spaces_registry()
    return SpacesListResponse(
        active_space_id=registry["active_space_id"],
        env_managed=_spaces_env_managed(),
        spaces=[_space_info(s) for s in registry["spaces"]],
    )


@router.post("/spaces", response_model=SpaceInfo, status_code=201)
def create_space(request: CreateSpaceRequest, http_request: Request) -> SpaceInfo:
    """Register a new Memory Space. Does not activate it -- creating and
    switching are separate dashboard actions."""
    if _spaces_env_managed():
        raise HTTPException(
            status_code=409,
            detail="Cannot create a Memory Space while HAVEN_VAULT_DIR-style "
            "env vars are set for this deployment.",
        )
    if not _is_local_request(http_request):
        raise HTTPException(status_code=400, detail=_LOCAL_PATH_REMOTE_ERROR)
    root = Path(request.root)
    if not root.is_absolute():
        raise HTTPException(status_code=400, detail="root must be an absolute path.")
    if root.exists() and not root.is_dir():
        raise HTTPException(
            status_code=400, detail=f"{root} exists but is not a directory."
        )

    registry = _load_spaces_registry() or _synthesize_spaces_registry()
    _check_root_overlap(registry, root)

    vault_dir, concept_dir, checkpoint_dir, write_trace_dir = _paths_for_root(root)
    try:
        vault_dir.mkdir(parents=True, exist_ok=True)
        concept_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        write_trace_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not initialize {root}: {exc}"
        ) from exc

    space = {
        "id": str(uuid4()),
        "name": request.name,
        "root": str(root),
        "vault_dir": str(vault_dir),
        "concept_dir": str(concept_dir),
        "checkpoint_dir": str(checkpoint_dir),
        "write_trace_dir": str(write_trace_dir),
        "env_managed": False,
    }
    registry["spaces"].append(space)
    _save_spaces_registry(registry)
    return _space_info(space)


@router.patch("/spaces/{space_id}", response_model=SpaceInfo)
def update_space(
    space_id: str, request: UpdateSpaceRequest, http_request: Request
) -> SpaceInfo:
    """Rename a Memory Space and/or re-point its root -- the only way to
    "move" a space, since ``root`` is otherwise fixed at creation.

    If *space_id* is the currently active space and ``root`` changes, also
    rebuilds ``app.state`` immediately via ``_configure_vault_state`` -- the
    edit takes effect without a separate activate call.
    """
    if request.name is None and request.root is None:
        raise HTTPException(
            status_code=400, detail="Provide at least one of name/root."
        )
    registry = _load_spaces_registry() or _synthesize_spaces_registry()
    space = _find_space(registry, space_id)
    if space is None:
        raise HTTPException(
            status_code=404, detail=f"No Memory Space with id {space_id!r}."
        )
    if space.get("env_managed") or _spaces_env_managed():
        raise HTTPException(
            status_code=409,
            detail="Cannot edit a Memory Space while this deployment is env-managed.",
        )

    if request.name is not None:
        space["name"] = request.name

    if request.root is not None:
        if not _is_local_request(http_request):
            raise HTTPException(status_code=400, detail=_LOCAL_PATH_REMOTE_ERROR)
        root = Path(request.root)
        if not root.is_absolute():
            raise HTTPException(
                status_code=400, detail="root must be an absolute path."
            )
        if root.exists() and not root.is_dir():
            raise HTTPException(
                status_code=400, detail=f"{root} exists but is not a directory."
            )
        _check_root_overlap(registry, root, ignore_space_id=space_id)

        vault_dir, concept_dir, checkpoint_dir, write_trace_dir = _paths_for_root(root)
        try:
            vault_dir.mkdir(parents=True, exist_ok=True)
            concept_dir.mkdir(parents=True, exist_ok=True)
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            write_trace_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HTTPException(
                status_code=400, detail=f"Could not initialize {root}: {exc}"
            ) from exc

        space["root"] = str(root)
        space["vault_dir"] = str(vault_dir)
        space["concept_dir"] = str(concept_dir)
        space["checkpoint_dir"] = str(checkpoint_dir)
        space["write_trace_dir"] = str(write_trace_dir)

        if space_id == registry["active_space_id"]:
            _configure_vault_state(vault_dir, concept_dir, checkpoint_dir, write_trace_dir)
            app.state.vault_root = root
            app.state.vault_configured = True

    _save_spaces_registry(registry)
    return _space_info(space)


@router.delete("/spaces/{space_id}", status_code=204)
def delete_space(space_id: str) -> Response:
    """Remove a Memory Space from the registry. Never deletes files on disk
    -- a space's root may be an irreplaceable real Obsidian vault, and
    there's no confirmation flow strong enough to gate an ``rmtree`` of an
    arbitrary user-chosen path safely. To get the files back, re-create a
    space pointing at the same root.
    """
    registry = _load_spaces_registry() or _synthesize_spaces_registry()
    space = _find_space(registry, space_id)
    if space is None:
        raise HTTPException(
            status_code=404, detail=f"No Memory Space with id {space_id!r}."
        )
    if space.get("env_managed") or _spaces_env_managed():
        raise HTTPException(
            status_code=409, detail="Cannot delete an env-managed Memory Space."
        )
    if space_id == registry["active_space_id"]:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete the active Memory Space -- switch to another one first.",
        )
    if len(registry["spaces"]) <= 1:
        raise HTTPException(
            status_code=409, detail="Cannot delete the last remaining Memory Space."
        )
    registry["spaces"] = [s for s in registry["spaces"] if s["id"] != space_id]
    _save_spaces_registry(registry)
    return Response(status_code=204)


@router.post("/spaces/{space_id}/activate", response_model=SpaceInfo)
def activate_space(space_id: str, request: ActivateSpaceRequest) -> SpaceInfo:
    """Switch the active Memory Space -- no server restart required.

    If a Memory Review is currently pending (``app.state.pending_reviews``),
    switching would silently discard it (``_configure_vault_state`` always
    resets it to ``{}``), so this requires ``confirm=True`` whenever one is
    pending; otherwise it returns 409 with the pending count instead of
    switching, and the dashboard prompts before retrying with confirmation.

    Raises
    ------
    HTTPException
        400 if the target space's directories can't be (re)created (e.g.
        its root was deleted or is now permission-denied) -- the same
        ``OSError``-to-400 contract ``select_vault`` already uses for the
        identical ``_configure_vault_state`` call, so a bad target space
        can't crash the switch with a raw 500 mid-request (which would
        leave ``app.state`` pointed at a half-reconfigured space).
    """
    registry = _load_spaces_registry() or _synthesize_spaces_registry()
    space = _find_space(registry, space_id)
    if space is None:
        raise HTTPException(
            status_code=404, detail=f"No Memory Space with id {space_id!r}."
        )
    if _spaces_env_managed():
        raise HTTPException(
            status_code=409,
            detail="Cannot switch Memory Spaces while this deployment is env-managed.",
        )
    if app.state.pending_reviews and not request.confirm:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "A Memory Review is pending in the current space "
                "and will be discarded by switching.",
                "pending_review_count": len(app.state.pending_reviews),
            },
        )
    try:
        _activate_space(registry, space_id)
    except OSError as exc:
        raise HTTPException(
            status_code=400, detail=f"Could not activate Memory Space: {exc}"
        ) from exc
    return _space_info(space)


@router.post("/dev/seed_demo", response_model=SeedDemoResponse)
def seed_demo_data() -> SeedDemoResponse:
    """Import the bundled demo dataset into the currently active vault.

    Reuses the exact parsing/replay logic ``scripts/seed_demo.py`` already
    validates (``obsidian.server.demo_seed``), but targets whatever vault
    ``app.state`` currently points at -- the default ``haven_data/`` paths,
    or one selected via ``POST /api/v1/vault`` -- instead of a fixed
    directory. Additive: does not clear existing content first (see
    ``POST /dev/reset_demo`` for that), so calling this against a vault
    that already has the demo data seeds a second, ``CONFIRM``ed copy of
    every fact via the same real ``CanonicalMatcher`` any duplicate save
    goes through -- harmless, not an error.

    Which bundled dataset gets seeded depends on the active Memory Space's
    own name (``app.state.active_space_name``, set by ``_activate_space``)
    -- see ``demo_seed.dataset_for_space`` -- so a space named e.g. "Personal
    AI Research" gets a genuinely different demo story instead of a
    duplicate of Haven's own.
    """
    memories_file, _ = demo_seed.dataset_for_space(
        getattr(app.state, "active_space_name", None)
    )
    bulk_count = demo_seed.seed_bulk_facts(
        app.state.vault_writer, app.state.ontology_pipeline, memories_file
    )
    call_count = _replay_demo_conversations()
    return SeedDemoResponse(bulk_facts=bulk_count, conversation_calls=call_count)


@router.post("/dev/reset_demo", response_model=SeedDemoResponse)
def reset_demo_data() -> SeedDemoResponse:
    """Atomically clear the currently active vault, then re-import demo data.

    Only ever touches the four directories ``app.state`` already has on
    record (whatever startup or the last ``POST /api/v1/vault`` call
    configured) -- never an arbitrary path.

    Clears by *renaming* each directory aside (:func:`_rename_aside`) rather
    than deleting it in place first. The previous implementation called
    ``shutil.rmtree`` directly: on Windows, that raises the instant any
    single file underneath is transiently locked by another process --
    OneDrive syncing it, Windows Search indexing it, antivirus scanning it,
    or (for a real local vault) the Obsidian desktop app's own file watcher
    -- turning an ordinary Reset Demo click into an unhandled 500 that left
    the vault directory half-deleted. Renaming is a metadata-only operation
    that doesn't require the old contents to be lock-free first, so the
    fresh directory Haven reseeds into is created and fully populated
    *before* the old one is ever touched by a delete (:func:`_robust_rmtree`,
    used only for the old/broken directories, never for the live one).

    If reseeding then fails for any reason, the fresh (half-seeded)
    directories are discarded and the pre-reset ones are restored from
    their backups before a 500 is raised -- a failed reset always leaves
    the space exactly as it was beforehand, never emptied or half-seeded.
    On success the backups are discarded (best-effort: a leftover backup
    directory is a harmless disk leak, not a correctness problem, so a
    failure to discard one is logged rather than turned into an error for
    what was otherwise a successful reset).

    Repeated calls are idempotent -- each call's backups are discarded (or,
    on failure, restored) before the response is returned, so no backup
    directories accumulate across resets.

    Beyond the failure-rollback window above there is no undo; the
    dashboard is expected to confirm with the user before calling this.
    """
    directories = (
        app.state.vault_dir,
        app.state.concept_dir,
        app.state.checkpoint_dir,
        app.state.write_trace_dir,
    )
    suffix = f".reset-bak-{uuid4().hex}"

    backups: List[Optional[Path]] = []
    try:
        for directory in directories:
            backups.append(_rename_aside(directory, suffix))
    except OSError as exc:
        # Undo whatever was already renamed aside before this one failed --
        # nothing has been deleted yet, so a plain rename-back is enough.
        for directory, backup in zip(directories, backups):
            if backup is not None:
                backup.rename(directory)
        raise HTTPException(
            status_code=500,
            detail=(
                "Could not reset demo data: failed to clear an existing "
                f"directory ({exc}). No changes were made."
            ),
        ) from exc

    try:
        _configure_vault_state(*directories)
        memories_file, _ = demo_seed.dataset_for_space(
            getattr(app.state, "active_space_name", None)
        )
        bulk_count = demo_seed.seed_bulk_facts(
            app.state.vault_writer, app.state.ontology_pipeline, memories_file
        )
        call_count = _replay_demo_conversations()
    except Exception as exc:
        logger.exception("reset_demo: reseed failed, rolling back to pre-reset state")
        for directory in directories:
            if directory.exists():
                _robust_rmtree(directory)
        for directory, backup in zip(directories, backups):
            if backup is not None:
                backup.rename(directory)
        _configure_vault_state(*directories)
        raise HTTPException(
            status_code=500,
            detail=(
                "Reset demo data failed and was rolled back to the "
                f"pre-reset state: {exc}"
            ),
        ) from exc

    for backup in backups:
        if backup is not None:
            try:
                _robust_rmtree(backup)
            except OSError:
                logger.warning(
                    "reset_demo: reset succeeded but failed to discard backup "
                    "directory %s (harmless, left on disk)",
                    backup,
                    exc_info=True,
                )

    return SeedDemoResponse(bulk_facts=bulk_count, conversation_calls=call_count)


def _replay_demo_conversations() -> int:
    """Replay the active Memory Space's demo conversations file by calling the
    real ``save_memory`` route function directly (a plain Python call, no
    HTTP/ASGI round trip needed since we're already inside the server
    process) -- temporarily swapping ``app.state.manager_pipeline`` for a
    scripted, no-API-key pipeline (see ``demo_seed.build_scripted_pipeline``)
    and always restoring the original afterward, so a live "Remember" click
    right after seeding still uses the real Manager AI LLM, not the demo one.

    Which file is replayed depends on the active Memory Space's name, via the
    same ``demo_seed.dataset_for_space`` lookup ``seed_demo_data``/
    ``reset_demo_data`` already used for the bulk-facts pass, so both passes
    always agree on which story this space is getting.
    """
    _, conversations_file = demo_seed.dataset_for_space(
        getattr(app.state, "active_space_name", None)
    )
    calls = demo_seed.parse_conversations(
        conversations_file.read_text(encoding="utf-8")
    )
    original_pipeline = app.state.manager_pipeline
    app.state.manager_pipeline = demo_seed.build_scripted_pipeline()
    try:
        for call in calls:
            request = SaveMemoryRequest(
                conversation=[
                    {"role": role, "content": content} for role, content in call["turns"]
                ],
                external_key=call["external_key"],
            )
            save_memory(request)
    finally:
        app.state.manager_pipeline = original_pipeline
    return len(calls)


@router.post(
    "/retrieve_context",
    response_model=RetrieveContextResponse,
    response_model_exclude_none=True,
)
def retrieve_context(request: RetrieveContextRequest) -> RetrieveContextResponse:
    """Resolve a raw query into Haven's deterministic LLM context string.

    Reloads ``memory_store`` and ``alias_index`` from disk (the two
    collaborators whose authoritative state lives there — see the module
    docstring), then runs the unmodified ``MemoryEngine`` retrieval
    pipeline, exactly like ``HavenAdapter.search()`` does.

    When ``request.include_trace`` is true, the response also carries a
    ``trace`` field — a serialised
    :class:`~obsidian.ontology.retrieval_models.RetrievalTrace` explaining
    every memory considered for this query. ``query_with_trace`` always
    runs the same pipeline ``query`` does (no extra retrieval work), so
    this flag only controls whether the diagnostics are attached to the
    response, never what context is returned. ``response_model_exclude_none``
    drops ``trace`` from the JSON body entirely when it is ``None``, so
    existing callers that never set ``include_trace`` see the exact same
    ``{"context": ...}`` shape as before this field existed.
    """
    with _write_lock:
        app.state.memory_store.load()
        app.state.alias_index.rebuild(_load_concepts(app.state.concept_dir))

        engine = _build_memory_engine()
        context, trace = engine.query_with_trace(request.query)
    return RetrieveContextResponse(
        context=context,
        trace=trace.to_dict() if request.include_trace else None,
    )


@router.post("/retrieve_working_context", response_model=WorkingContextPreviewResponse)
def retrieve_working_context(
    request: WorkingContextPreviewRequest,
) -> WorkingContextPreviewResponse:
    """Working Context + Structured Prompt preview for the browser extension.

    Reloads ``memory_store``/``alias_index`` from disk, exactly like
    ``retrieve_context`` above, then calls the unmodified
    :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_working_context`
    and :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_structured`
    for ``request.query``. Purely additive: never touches, and cannot
    affect, ``POST /retrieve_context`` or its ``ContextBuilder``-based
    output.

    ``available=False`` — never a raised exception — when
    ``query_working_context`` is missing (an older engine) or either call
    fails for any reason, so the extension can fall back to
    ``POST /retrieve_context`` without ever seeing a broken preview. This
    mirrors the same best-effort, fail-open contract
    ``obsidian.server.dashboard._working_context_summaries`` already uses
    for the dashboard's "Resume Work" panel.
    """
    with _write_lock:
        app.state.memory_store.load()
        app.state.alias_index.rebuild(_load_concepts(app.state.concept_dir))

        engine = _build_memory_engine()
        if not hasattr(engine, "query_working_context"):
            return WorkingContextPreviewResponse(available=False)
        try:
            contexts = engine.query_working_context(request.query)
            structured_prompt = engine.query_structured(request.query)
        except Exception:
            logger.debug("Working Context preview unavailable", exc_info=True)
            return WorkingContextPreviewResponse(available=False)

        return WorkingContextPreviewResponse(
            available=True,
            structured_prompt=structured_prompt,
            contexts=[
                _to_working_context_summary(context, app.state.concept_graph)
                for context in contexts
            ],
        )


@router.post("/memory", response_model=SaveMemoryResponse)
def save_memory(request: SaveMemoryRequest) -> SaveMemoryResponse:
    """Persist new facts into the real Haven vault via the Manager AI pipeline.

    *request* becomes a :class:`~obsidian.core.types.Conversation`. When
    ``request.conversation`` is present (the "Remember Conversation" shape:
    every visible ChatGPT turn, in chronological order), it becomes a
    multi-event ``Conversation`` with one ``Event`` per turn, its ``role``
    taken from each turn verbatim -- so ``ManagerPipeline`` sees the whole
    dialogue instead of a single synthesized message. Otherwise (the legacy
    shape) it becomes the original single-event ``Conversation`` -- one
    ``Role.USER`` event carrying ``canonical_fact`` verbatim.

    Either way, that ``Conversation`` is run through
    ``app.state.manager_pipeline.process()`` (Extractor -> Classifier ->
    ImportanceScorer -> CanonicalMatcher -> KnowledgeUpdater), matched
    against every ``KnowledgeObject`` already in the vault so ``CONFIRM``
    decisions work, not just ``NEW`` ones.

    The pipeline can produce zero, one, or several ``KnowledgeObject``s
    (zero when the Extractor decides there's nothing worth remembering --
    e.g. a conversation containing only assistant explanations the user
    never adopted). Each one is persisted through exactly the two
    collaborators the module docstring describes:
    ``app.state.vault_writer.write`` followed by
    ``app.state.ontology_pipeline.process``. ``SaveMemoryResponse`` still
    only has room for one result (the extension only checks
    ``response.ok``, never reads these fields — see
    ``extension/content/controller.js``'s ``onRememberClick``), so the
    first produced object's fields are returned as that single result when
    more than one was created.

    Conversation-level duplicate prevention
    ----------------------------------------
    When *request* carries ``external_key`` (and, optionally, ``source``),
    a deterministic ``conversation_id`` is derived via
    :func:`obsidian.checkpoint.identity.derive_conversation_id` and looked
    up in ``app.state.checkpoint_store``. If a checkpoint already exists
    for that id **and** its stored ``transcript_hash`` matches this
    request's transcript exactly, the entire pipeline is skipped -- no
    Extractor/Classifier/ImportanceScorer/CanonicalMatcher call, no
    ``vault_writer``/``ontology_pipeline`` call -- and
    ``SaveMemoryResponse(status="duplicate")`` is returned immediately.
    This is what makes re-clicking "Remember" on an unchanged conversation
    a cheap no-op instead of a full LLM reprocessing (see
    ``obsidian/checkpoint/__init__.py``'s module docstring for the wider
    design).

    When the transcript is new or has changed (including "``external_key``
    was not supplied at all"), what gets sent to the pipeline depends on
    how it changed -- see "Incremental ingestion with Working Context"
    below. After a successful run (at least one ``KnowledgeObject``
    persisted), the checkpoint is created or updated via
    ``app.state.checkpoint_writer.write()``. Requests that never supply
    ``external_key`` never touch ``checkpoint_store``/``checkpoint_writer``
    at all -- every new code path below is gated on
    ``conversation_id is not None`` -- so this feature is entirely inert,
    byte-for-byte, for any caller that doesn't opt in.

    Incremental ingestion with Working Context
    -------------------------------------------
    :func:`~obsidian.checkpoint.diff.classify_turns` compares this
    request's turn hashes against the existing checkpoint (if any) and
    returns one of three classifications:

    * ``"first_run"`` -- no checkpoint exists yet. Every turn is evidence;
      no Working Context is fetched (there is nothing in the vault yet to
      contextualise).
    * ``"incremental"`` -- the checkpoint's turn hashes are an exact,
      in-order prefix of this request's, which are strictly longer (a pure
      append). Only the turns after that prefix are sent to the Extractor
      as evidence. :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_working_context`
      is called with a query built by :func:`_build_working_context_query`
      (a small fixed window of the most recent already-processed turns
      plus the new evidence -- see that function's docstring), and the
      result is passed to ``manager_pipeline.process`` as
      ``existing_context``: background only, never itself extracted as a
      new fact (see :meth:`~obsidian.manager_ai.extractor.Extractor.build_prompt`).
      The Working Context lookup is best-effort -- any failure degrades to
      ``existing_context=None`` rather than failing the request.
    * ``"fallback"`` -- the checkpoint exists but this request's turns are
      *not* a clean append (an earlier turn was edited, deleted,
      reordered, or the transcript was truncated). Every turn is
      reprocessed as evidence, with no Working Context, exactly like
      today's ("first_run"-shaped) full-conversation behaviour -- the
      safest response to "something earlier changed and there is no
      reliable new-turns-only slice to take."

    Regardless of classification, the persisted checkpoint always records
    the *entire* current turn list (hashes, count, last processed index) --
    never just the evidence slice -- so a later request can still be
    classified correctly; only ``CheckpointRun.turn_range`` and ``mode``
    describe which slice was actually sent to the Extractor for this
    particular run.

    Raises
    ------
    HTTPException
        422, if the pipeline extracted no facts worth remembering —
        surfaced by the extension the same way as its existing "Nothing to
        remember yet." blank-text check. This is distinct from the
        "duplicate" case above: a genuinely empty extraction from a *new*
        transcript still raises 422 (unchanged from before this feature
        existed) and does **not** write a checkpoint, so an unchanged
        resend of that same empty-ish conversation runs the pipeline again
        rather than short-circuiting -- see this route's docstring section
        above for why collapsing that case into "duplicate" is left for a
        later PR.
    HTTPException
        500, if ``manager_pipeline.process_with_trace`` itself raises
        (a programmer bug, not a recognised failure mode any stage
        already handles) -- the full traceback is logged server-side via
        ``logger.exception`` before this clean 500 is returned, instead of
        letting the raw exception surface as FastAPI's default error page.
    """
    with _write_lock:
        trace_id = uuid4()
        write_start = time.perf_counter()
        stage_timings_ms: Dict[str, float] = {}

        app.state.memory_store.load()

        if request.conversation:
            turns: List[Tuple[Role, str]] = [
                (turn.role, turn.content) for turn in request.conversation
            ]
        else:
            turns = [(Role.USER, request.canonical_fact)]

        checkpoint_lookup_start = time.perf_counter()
        lookup = _checkpoint_lookup(request, turns)
        stage_timings_ms["checkpoint_lookup"] = _elapsed_ms(checkpoint_lookup_start)

        if lookup.is_duplicate:
            stage_timings_ms["total"] = _elapsed_ms(write_start)
            _persist_duplicate_write_trace(
                trace_id=trace_id,
                conversation_id=lookup.conversation_id,
                request_source=request.source,
                request_external_key=request.external_key,
                turn_count=len(turns),
                incoming_transcript_hash=lookup.incoming_transcript_hash,
                stage_timings_ms=stage_timings_ms,
            )
            return SaveMemoryResponse(status="duplicate", trace_id=str(trace_id))

        diff = lookup.diff
        conversation_id = lookup.conversation_id
        existing_checkpoint = lookup.existing_checkpoint
        had_existing_checkpoint = lookup.had_existing_checkpoint
        incoming_turn_hashes = lookup.incoming_turn_hashes
        incoming_transcript_hash = lookup.incoming_transcript_hash

        evidence_turns = turns[diff.new_turn_start_index :]

        working_context_start = time.perf_counter()
        existing_context, working_contexts_projected = _lookup_working_context(
            diff, turns
        )
        stage_timings_ms["working_context"] = _elapsed_ms(working_context_start)

        conversation = Conversation(
            id=conversation_id if conversation_id is not None else uuid4(),
            title="Remember",
            source=SourceType.MANUAL,
            events=[
                Event(role=role, content=content, source=SourceType.MANUAL)
                for role, content in evidence_turns
            ],
        )
        pipeline_start = time.perf_counter()
        try:
            decisions, extraction_trace = app.state.manager_pipeline.process_with_trace(
                conversation,
                app.state.memory_store.all(),
                existing_context=existing_context,
            )
        except Exception as exc:
            logger.exception(
                "Manager AI pipeline failed while processing /memory "
                "(trace_id=%s)",
                trace_id,
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    "Manager AI failed while processing this request "
                    "— check your LLM provider configuration and try again."
                ),
            ) from exc
        stage_timings_ms["extract_classify_match_apply"] = _elapsed_ms(pipeline_start)
        knowledge_objects = [d.knowledge for d in decisions if d.knowledge is not None]
        extractor_stage_trace = _build_extractor_stage_trace(
            extraction_trace, app.state.write_trace_capture_llm_io
        )
        fact_traces = _build_fact_traces(decisions)

        if _DEBUG_PIPELINE_LOGGING:
            logger.debug(
                "5. Pipeline produced %d decision(s), %d with a knowledge object",
                len(decisions),
                len(knowledge_objects),
            )
            for i, d in enumerate(decisions):
                fact_text = d.fact.text if d.fact else None
                memory_type = (
                    d.classification.memory_type.value if d.classification else None
                )
                importance_score = d.importance.score if d.importance else None
                logger.debug(
                    "5.   decision[%d]: fact=%r classification=%s importance=%s "
                    "knowledge=%s",
                    i,
                    fact_text,
                    memory_type,
                    importance_score,
                    "<created>" if d.knowledge is not None else None,
                )

            if not knowledge_objects:
                if not decisions:
                    logger.debug(
                        "5. Concluding 'Nothing worth remembering': the "
                        "Extractor produced ZERO candidate facts for this "
                        "conversation (see extractor.py's [1]/[2]/[3] debug "
                        "logs for the conversation text and raw LLM output)."
                    )
                else:
                    logger.debug(
                        "5. Concluding 'Nothing worth remembering': facts WERE "
                        "extracted, but none produced a knowledge object -- see "
                        "the decision[] entries above for which stage dropped "
                        "them."
                    )

        if not knowledge_objects:
            stage_timings_ms["total"] = _elapsed_ms(write_start)
            _persist_write_trace_best_effort(
                trace_id=trace_id,
                conversation_id=conversation_id,
                source=request.source,
                external_key=request.external_key,
                mode=diff.mode,
                checkpoint=CheckpointStageTrace(
                    mode=diff.mode,
                    had_existing_checkpoint=had_existing_checkpoint,
                    turn_count=len(turns),
                    new_turn_start_index=diff.new_turn_start_index,
                    transcript_hash=incoming_transcript_hash,
                ),
                working_contexts=working_contexts_projected,
                extractor=extractor_stage_trace,
                facts=fact_traces,
                vault_paths=(),
                ontology=OntologyStageTrace(),
                status="no_facts_extracted",
                knowledge_object_ids=(),
                stage_timings_ms=stage_timings_ms,
            )
            raise HTTPException(
                status_code=422,
                detail="Nothing worth remembering was found in that text.",
            )

        knowledge_objects = _stamp_provenance(knowledge_objects, request.provenance)

        vault_and_ontology_start = time.perf_counter()
        vault_paths, ontology_concept_paths, ontology_validation_results = (
            _persist_knowledge_objects(knowledge_objects)
        )
        stage_timings_ms["vault_and_ontology_write"] = _elapsed_ms(
            vault_and_ontology_start
        )

        checkpoint_persist_start = time.perf_counter()
        decision_counts = Counter(
            d.decision.value for d in decisions if d.decision is not None
        )
        _write_checkpoint(
            conversation_id=conversation_id,
            request_source=request.source,
            request_external_key=request.external_key,
            turns=turns,
            diff=diff,
            incoming_turn_hashes=incoming_turn_hashes,
            incoming_transcript_hash=incoming_transcript_hash,
            existing_checkpoint=existing_checkpoint,
            knowledge_objects=knowledge_objects,
            decision_counts=dict(decision_counts),
        )
        stage_timings_ms["checkpoint_persist"] = _elapsed_ms(checkpoint_persist_start)
        stage_timings_ms["total"] = _elapsed_ms(write_start)

        _persist_write_trace_best_effort(
            trace_id=trace_id,
            conversation_id=conversation_id,
            source=request.source,
            external_key=request.external_key,
            mode=diff.mode,
            checkpoint=CheckpointStageTrace(
                mode=diff.mode,
                had_existing_checkpoint=had_existing_checkpoint,
                turn_count=len(turns),
                new_turn_start_index=diff.new_turn_start_index,
                transcript_hash=incoming_transcript_hash,
                decision_counts=dict(decision_counts),
            ),
            working_contexts=working_contexts_projected,
            extractor=extractor_stage_trace,
            facts=fact_traces,
            vault_paths=tuple(str(p) for p in vault_paths),
            ontology=OntologyStageTrace(
                validation_results=tuple(
                    OntologyProposalTrace(
                        proposal_type=r.proposal.proposal_type.value,
                        accepted=r.accepted,
                        rejection_reason=r.rejection_reason,
                    )
                    for r in ontology_validation_results
                ),
                concept_paths=tuple(str(p) for p in ontology_concept_paths),
            ),
            status="success",
            knowledge_object_ids=tuple(k.id for k in knowledge_objects),
            stage_timings_ms=stage_timings_ms,
        )

        primary = knowledge_objects[0]
        return SaveMemoryResponse(
            status="success",
            id=str(primary.id),
            canonical_fact=primary.canonical_fact,
            memory_type=primary.memory_type,
            trace_id=str(trace_id),
        )


@router.post("/memory/preview", response_model=PreviewMemoryResponse)
def preview_memory(request: SaveMemoryRequest) -> PreviewMemoryResponse:
    """Memory Review, step 1: run the Manager AI LLM stages, then stop.

    Runs the same checkpoint lookup, turn classification, and Working
    Context lookup as ``save_memory`` (via the shared helpers both routes
    call -- see ``_checkpoint_lookup``/``_lookup_working_context``), then
    ``app.state.manager_pipeline.extract_classify_score`` -- the Extractor/
    Classifier/ImportanceScorer stages, and *only* those. No
    ``CanonicalMatcher``/``KnowledgeUpdater`` runs here, and nothing is
    written to the vault, concept graph, or checkpoint store.

    Mirrors ``save_memory``'s own conversation-level duplicate short-circuit
    exactly: an unchanged transcript against an existing checkpoint returns
    ``status="duplicate"`` immediately, with no extraction at all.

    Otherwise, the result is held server-side as a ``PendingReview`` keyed
    by a fresh ``review_id``, for a later ``POST /memory/commit`` (or
    ``POST /memory/cancel``) to consume. Returns one ``ReviewMemoryItem``
    per extracted fact -- possibly an empty list, if nothing was worth
    remembering; still ``status="ok"``, not a 422, since the review dialog
    always lets the user add a memory manually regardless.

    Raises
    ------
    HTTPException
        500, if ``manager_pipeline.extract_classify_score`` itself raises
        (a programmer bug, not a recognised failure mode any stage
        already handles) -- the full traceback is logged server-side via
        ``logger.exception`` before this clean 500 is returned, instead of
        letting the raw exception surface as FastAPI's default error page.
    """
    trace_id = uuid4()
    stage_timings_ms: Dict[str, float] = {}

    app.state.memory_store.load()

    if request.conversation:
        turns: List[Tuple[Role, str]] = [
            (turn.role, turn.content) for turn in request.conversation
        ]
    else:
        turns = [(Role.USER, request.canonical_fact)]

    checkpoint_lookup_start = time.perf_counter()
    lookup = _checkpoint_lookup(request, turns)
    stage_timings_ms["checkpoint_lookup"] = _elapsed_ms(checkpoint_lookup_start)

    if lookup.is_duplicate:
        stage_timings_ms["total"] = _elapsed_ms(checkpoint_lookup_start)
        _persist_duplicate_write_trace(
            trace_id=trace_id,
            conversation_id=lookup.conversation_id,
            request_source=request.source,
            request_external_key=request.external_key,
            turn_count=len(turns),
            incoming_transcript_hash=lookup.incoming_transcript_hash,
            stage_timings_ms=stage_timings_ms,
        )
        return PreviewMemoryResponse(status="duplicate")

    diff = lookup.diff
    evidence_turns = turns[diff.new_turn_start_index :]

    working_context_start = time.perf_counter()
    existing_context, working_contexts_projected = _lookup_working_context(
        diff, turns
    )
    stage_timings_ms["working_context"] = _elapsed_ms(working_context_start)

    conversation = Conversation(
        id=lookup.conversation_id if lookup.conversation_id is not None else uuid4(),
        title="Remember",
        source=SourceType.MANUAL,
        events=[
            Event(role=role, content=content, source=SourceType.MANUAL)
            for role, content in evidence_turns
        ],
    )

    extraction_start = time.perf_counter()
    try:
        scored_facts, extractor_trace = (
            app.state.manager_pipeline.extract_classify_score(
                conversation, existing_context=existing_context
            )
        )
    except Exception as exc:
        logger.exception(
            "Manager AI pipeline failed while processing /memory/preview "
            "(trace_id=%s)",
            trace_id,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Manager AI failed while processing this request "
                "— check your LLM provider configuration and try again."
            ),
        ) from exc
    stage_timings_ms["extract_classify_score"] = _elapsed_ms(extraction_start)

    extractor_stage_trace = _build_extractor_stage_trace(
        extractor_trace, app.state.write_trace_capture_llm_io
    )

    review_id = uuid4()
    app.state.pending_reviews[review_id] = PendingReview(
        conversation_id=lookup.conversation_id,
        source=request.source,
        external_key=request.external_key,
        turns=turns,
        existing_checkpoint=lookup.existing_checkpoint,
        had_existing_checkpoint=lookup.had_existing_checkpoint,
        incoming_turn_hashes=lookup.incoming_turn_hashes,
        incoming_transcript_hash=lookup.incoming_transcript_hash,
        diff=diff,
        extractor_stage_trace=extractor_stage_trace,
        working_contexts_projected=working_contexts_projected,
        scored_facts=scored_facts,
        provenance=request.provenance,
    )
    _evict_pending_reviews()

    items = [
        ReviewMemoryItem(
            fact_index=i,
            text=fact.text,
            memory_type=classification.memory_type,
            evidence=fact.evidence,
        )
        for i, (fact, classification, _importance) in enumerate(scored_facts)
    ]
    return PreviewMemoryResponse(status="ok", review_id=str(review_id), items=items)


@router.post("/memory/commit", response_model=SaveMemoryResponse)
def commit_memory(request: CommitMemoryRequest) -> SaveMemoryResponse:
    """Memory Review, step 2: apply the user's edits, then persist.

    Consumes the ``PendingReview`` a prior ``POST /memory/preview`` call
    created (identified by ``request.review_id``) -- one-shot: it is popped
    out of ``app.state.pending_reviews`` immediately, so a second commit
    with the same ``review_id`` 404s. The Extractor/Classifier/
    ImportanceScorer never run again here: every fact's text/evidence/
    confidence/importance is either carried over from the preview
    unchanged, or overridden only in the two fields the review dialog
    exposes (``text``/``memory_type``) -- never recomputed by the LLM. A
    ``fact_index`` from the preview that is absent from ``request.items``
    is treated as deleted; a ``CommittedMemoryItem`` with no ``fact_index``
    is a memory the user added during review.

    ``app.state.manager_pipeline.match_and_apply`` -- the deterministic,
    non-LLM ``CanonicalMatcher``/``KnowledgeUpdater`` pair -- runs exactly
    once here, against ``app.state.memory_store`` reloaded fresh from disk,
    over every kept (unchanged or edited) and added item. This is what lets
    an edited fact that now duplicates an existing memory correctly
    ``CONFIRM`` instead of becoming a spurious ``NEW`` (see this module's
    design notes) without ever touching the LLM.

    Persists through the same collaborators ``save_memory`` uses
    (``_persist_knowledge_objects``, ``_write_checkpoint``,
    ``_persist_write_trace_best_effort``), so the vault, concept graph,
    checkpoint, and Write Trace end up in exactly the shape they would if
    this had been a single, unreviewed ``save_memory`` call -- plus a
    per-fact ``review_action``/original-value annotation on the trace (see
    ``FactTrace``) and a ``review_summary`` count breakdown on the response.

    Raises
    ------
    HTTPException
        404 if ``review_id`` doesn't match a pending review (unknown,
        already committed, already cancelled, or the server restarted since
        the preview -- the caller should just click Remember again, which
        re-runs extraction from scratch exactly once). 422 if the user
        deleted every extracted fact and added nothing -- mirrors
        ``save_memory``'s own "nothing worth remembering" contract exactly.
    """
    with _write_lock:
        try:
            review_uuid = UUID(request.review_id)
        except ValueError:
            raise HTTPException(status_code=404, detail="Unknown or expired review_id.")

        pending: Optional[PendingReview] = app.state.pending_reviews.pop(
            review_uuid, None
        )
        if pending is None:
            raise HTTPException(
                status_code=404,
                detail="Unknown or expired review_id. Click Remember again.",
            )

        trace_id = uuid4()
        write_start = time.perf_counter()
        stage_timings_ms: Dict[str, float] = {}

        app.state.memory_store.load()
        existing: List[KnowledgeObject] = app.state.memory_store.all()

        submitted_by_index: Dict[int, CommittedMemoryItem] = {
            item.fact_index: item for item in request.items if item.fact_index is not None
        }
        # A fact_index outside the preview's own range (stale/tampered client
        # state -- e.g. the review dialog held a review_id from a previous
        # preview with more facts) would otherwise be silently dropped below:
        # the loop over pending.scored_facts only ever looks up indices it
        # itself enumerates, so an extra out-of-range key in submitted_by_index
        # is never read, never persisted, and never reported. Reject it
        # explicitly instead of losing it quietly.
        invalid_indices = sorted(
            idx for idx in submitted_by_index if not (0 <= idx < len(pending.scored_facts))
        )
        if invalid_indices:
            raise HTTPException(
                status_code=400,
                detail=f"fact_index out of range for this review: {invalid_indices}. "
                "The review may be stale -- click Remember again.",
            )
        added_items = [item for item in request.items if item.fact_index is None]

        all_scored_facts: List[
            Tuple[ExtractedFact, ClassificationResult, ImportanceResult]
        ] = []
        review_actions: List[str] = []
        original_texts: List[Optional[str]] = []
        original_types: List[Optional[MemoryType]] = []
        fact_index_by_position: List[int] = []
        deleted_fact_indices: List[int] = []

        for idx, (fact, classification, importance) in enumerate(pending.scored_facts):
            item = submitted_by_index.get(idx)
            if item is None:
                deleted_fact_indices.append(idx)
                continue

            text_changed = item.text != fact.text
            type_changed = item.memory_type != classification.memory_type
            if text_changed or type_changed:
                new_fact = (
                    dataclasses_replace(fact, text=item.text) if text_changed else fact
                )
                new_classification = (
                    dataclasses_replace(classification, memory_type=item.memory_type)
                    if type_changed
                    else classification
                )
                all_scored_facts.append((new_fact, new_classification, importance))
                review_actions.append("edited")
                original_texts.append(fact.text)
                original_types.append(classification.memory_type)
            else:
                all_scored_facts.append((fact, classification, importance))
                review_actions.append("unchanged")
                original_texts.append(None)
                original_types.append(None)
            fact_index_by_position.append(idx)

        next_fact_index = len(pending.scored_facts)
        for item in added_items:
            new_fact = ExtractedFact(
                text=item.text, evidence="Added by user during review", confidence=1.0
            )
            new_classification = ClassificationResult(
                memory_type=item.memory_type,
                confidence=1.0,
                reason="User-specified during review",
            )
            new_importance = ImportanceResult(
                score=0.5, reason="Default importance for user-added memory"
            )
            all_scored_facts.append((new_fact, new_classification, new_importance))
            review_actions.append("added")
            original_texts.append(None)
            original_types.append(None)
            fact_index_by_position.append(next_fact_index)
            next_fact_index += 1

        match_start = time.perf_counter()
        decisions = app.state.manager_pipeline.match_and_apply(all_scored_facts, existing)
        stage_timings_ms["match_and_apply"] = _elapsed_ms(match_start)

        knowledge_objects = [d.knowledge for d in decisions if d.knowledge is not None]
        knowledge_objects = _stamp_provenance(knowledge_objects, pending.provenance)

        base_traces = _build_fact_traces(decisions)
        fact_traces: List[FactTrace] = [
            dataclasses_replace(
                ft,
                fact_index=fact_index_by_position[i],
                review_action=review_actions[i],
                original_fact_text=original_texts[i],
                original_memory_type=original_types[i],
            )
            for i, ft in enumerate(base_traces)
        ]
        for idx in deleted_fact_indices:
            fact, classification, importance = pending.scored_facts[idx]
            fact_traces.append(
                FactTrace(
                    fact_index=idx,
                    fact_text=fact.text,
                    evidence=fact.evidence,
                    confidence=fact.confidence,
                    memory_type=classification.memory_type,
                    classification_confidence=classification.confidence,
                    classification_reason=classification.reason,
                    importance_score=importance.score,
                    importance_reason=importance.reason,
                    decision=None,
                    knowledge_object_id=None,
                    review_action="deleted",
                    original_fact_text=fact.text,
                    original_memory_type=classification.memory_type,
                )
            )
        fact_traces.sort(key=lambda ft: ft.fact_index)

        if not knowledge_objects:
            stage_timings_ms["total"] = _elapsed_ms(write_start)
            _persist_write_trace_best_effort(
                trace_id=trace_id,
                conversation_id=pending.conversation_id,
                source=pending.source,
                external_key=pending.external_key,
                mode=pending.diff.mode,
                checkpoint=CheckpointStageTrace(
                    mode=pending.diff.mode,
                    had_existing_checkpoint=pending.had_existing_checkpoint,
                    turn_count=len(pending.turns),
                    new_turn_start_index=pending.diff.new_turn_start_index,
                    transcript_hash=pending.incoming_transcript_hash,
                ),
                working_contexts=pending.working_contexts_projected,
                extractor=pending.extractor_stage_trace,
                facts=tuple(fact_traces),
                vault_paths=(),
                ontology=OntologyStageTrace(),
                status="no_facts_extracted",
                knowledge_object_ids=(),
                stage_timings_ms=stage_timings_ms,
            )
            raise HTTPException(
                status_code=422,
                detail="Nothing worth remembering was found in that text.",
            )

        vault_and_ontology_start = time.perf_counter()
        vault_paths, ontology_concept_paths, ontology_validation_results = (
            _persist_knowledge_objects(knowledge_objects)
        )
        stage_timings_ms["vault_and_ontology_write"] = _elapsed_ms(
            vault_and_ontology_start
        )

        checkpoint_persist_start = time.perf_counter()
        decision_counts = Counter(
            d.decision.value for d in decisions if d.decision is not None
        )
        _write_checkpoint(
            conversation_id=pending.conversation_id,
            request_source=pending.source,
            request_external_key=pending.external_key,
            turns=pending.turns,
            diff=pending.diff,
            incoming_turn_hashes=pending.incoming_turn_hashes,
            incoming_transcript_hash=pending.incoming_transcript_hash,
            existing_checkpoint=pending.existing_checkpoint,
            knowledge_objects=knowledge_objects,
            decision_counts=dict(decision_counts),
        )
        stage_timings_ms["checkpoint_persist"] = _elapsed_ms(checkpoint_persist_start)
        stage_timings_ms["total"] = _elapsed_ms(write_start)

        _persist_write_trace_best_effort(
            trace_id=trace_id,
            conversation_id=pending.conversation_id,
            source=pending.source,
            external_key=pending.external_key,
            mode=pending.diff.mode,
            checkpoint=CheckpointStageTrace(
                mode=pending.diff.mode,
                had_existing_checkpoint=pending.had_existing_checkpoint,
                turn_count=len(pending.turns),
                new_turn_start_index=pending.diff.new_turn_start_index,
                transcript_hash=pending.incoming_transcript_hash,
                decision_counts=dict(decision_counts),
            ),
            working_contexts=pending.working_contexts_projected,
            extractor=pending.extractor_stage_trace,
            facts=tuple(fact_traces),
            vault_paths=tuple(str(p) for p in vault_paths),
            ontology=OntologyStageTrace(
                validation_results=tuple(
                    OntologyProposalTrace(
                        proposal_type=r.proposal.proposal_type.value,
                        accepted=r.accepted,
                        rejection_reason=r.rejection_reason,
                    )
                    for r in ontology_validation_results
                ),
                concept_paths=tuple(str(p) for p in ontology_concept_paths),
            ),
            status="success",
            knowledge_object_ids=tuple(k.id for k in knowledge_objects),
            stage_timings_ms=stage_timings_ms,
        )

        review_summary = ReviewSummary(
            saved=len(knowledge_objects),
            edited=sum(1 for action in review_actions if action == "edited"),
            added=sum(1 for action in review_actions if action == "added"),
            removed=len(deleted_fact_indices),
        )

        primary = knowledge_objects[0]
        return SaveMemoryResponse(
            status="success",
            id=str(primary.id),
            canonical_fact=primary.canonical_fact,
            memory_type=primary.memory_type,
            trace_id=str(trace_id),
            review_summary=review_summary,
            decision_counts=dict(decision_counts),
        )


@router.post("/memory/cancel")
def cancel_memory(request: CancelMemoryRequest) -> Dict[str, str]:
    """Discard a pending Memory Review immediately.

    Called when the user dismisses the review dialog without saving
    (Cancel button, clicking outside the dialog, or Escape). Idempotent and
    side-effect-free: an unknown, already-committed, or already-cancelled
    ``review_id`` is a harmless no-op, not an error -- this is best-effort
    tidy-up (see ``_evict_pending_reviews`` for the backstop that handles
    reviews abandoned without ever calling this route).
    """
    try:
        review_uuid = UUID(request.review_id)
    except ValueError:
        return {"status": "ok"}
    app.state.pending_reviews.pop(review_uuid, None)
    return {"status": "ok"}


def _slugify(text: str, max_len: int = 48) -> str:
    """Turn arbitrary text into a filesystem-safe, lowercase filename slug.

    Collapses every run of non-alphanumerics to a single hyphen and trims
    to *max_len*, so a note title/body becomes a readable filename stem.
    Falls back to ``"note"`` when nothing survives (e.g. a note whose title
    is only punctuation or non-Latin text).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-") or "note"


def _write_capture_note(request: CaptureNoteRequest, created: datetime) -> Path:
    """Persist the *original* Quick Capture Markdown verbatim into ``notes/``.

    Writes a self-contained Obsidian note: YAML frontmatter (title, creation
    time, optional tags, and a ``source: quick-capture`` marker) followed by
    the user's Markdown exactly as typed -- with the optional title prepended
    as an ``# H1`` heading only when one was supplied. The filename is
    ``<utc-timestamp>-<slug>-<hash>.md``; the random hash suffix guarantees
    uniqueness without a collision check, even for two captures in the same
    second. Lives in ``app.state.notes_dir`` (a sibling of the memory vault
    -- see ``_configure_vault_state``), never under ``vault_dir``.
    """
    notes_dir: Path = app.state.notes_dir
    notes_dir.mkdir(parents=True, exist_ok=True)

    slug_source = request.title or request.content
    filename = (
        f"{created.strftime('%Y%m%d-%H%M%S')}-"
        f"{_slugify(slug_source)}-{uuid4().hex[:8]}.md"
    )
    note_path = notes_dir / filename

    first_line = request.content.strip().splitlines()[0].strip()
    frontmatter: Dict[str, Any] = {
        "title": request.title or first_line[:80],
        "created": created.isoformat(),
        "source": "quick-capture",
    }
    if request.tags:
        frontmatter["tags"] = list(request.tags)

    yaml_str = yaml.dump(
        frontmatter, default_flow_style=False, sort_keys=False, allow_unicode=True
    )
    body = (
        f"# {request.title}\n\n{request.content}"
        if request.title
        else request.content
    )
    note_path.write_text(f"---\n{yaml_str}---\n\n{body}\n", encoding="utf-8")
    return note_path


@router.post("/capture", response_model=CaptureNoteResponse)
def capture_note(request: CaptureNoteRequest) -> CaptureNoteResponse:
    """Quick Capture: save a free-form Markdown note and remember it.

    Two steps, no new business logic:

    1. The *original* Markdown is written verbatim into the vault's
       ``notes/`` folder (see :func:`_write_capture_note`) so it is
       browsable in Obsidian alongside the machine-generated memory notes.
    2. That same Markdown is run through the **existing** ``save_memory``
       flow -- as a single ``Role.USER`` turn with ``source=MANUAL`` and an
       ``external_key`` derived from the note's filename -- so
       ``KnowledgeObject``s are extracted through the one Manager Pipeline
       exactly as a ChatGPT conversation is, producing a normal Write Trace
       (and consulting/updating Working Context) with zero duplication.
       ``save_memory`` is invoked as a plain in-process call, the same
       pattern :func:`_replay_demo_conversations` already uses.

    A capture never fails just because the pipeline found nothing worth
    remembering: the original note is already saved, so the ``POST /memory``
    422 ("nothing worth remembering") case is caught and reported as
    ``status="no_memories"`` rather than propagated as an error.
    """
    created = datetime.utcnow()
    try:
        note_path = _write_capture_note(request, created)
    except OSError as exc:
        logger.exception("Failed to write Quick Capture note to disk")
        raise HTTPException(
            status_code=500, detail=f"Could not save the note: {exc}"
        ) from exc

    body = (
        f"{request.title}\n\n{request.content}" if request.title else request.content
    )
    save_request = SaveMemoryRequest(
        conversation=[ConversationTurnRequest(role=Role.USER, content=body)],
        source=SourceType.MANUAL,
        external_key=note_path.stem,
    )
    try:
        result = save_memory(save_request)
    except HTTPException as exc:
        if exc.status_code == 422:
            return CaptureNoteResponse(
                status="no_memories", note_path=str(note_path)
            )
        raise

    return CaptureNoteResponse(
        status=result.status,
        note_path=str(note_path),
        id=result.id,
        canonical_fact=result.canonical_fact,
        memory_type=result.memory_type,
        trace_id=result.trace_id,
    )


def _resolve_import_root(root_arg: Optional[str], http_request: Request) -> Path:
    """Resolve the vault folder an import scan/preview should read.

    Uses *root_arg* when given, otherwise the active Memory Space's root.
    An explicit *root_arg* is a path on the browser's machine, so it's only
    honored for requests reaching Haven locally (see :func:`_is_local_request`)
    -- falling back to the active space's already-configured root is always
    fine, since that path was validated (and belongs to the server) when the
    space was created or last re-pointed.

    Raises
    ------
    HTTPException
        400 if *root_arg* is given but this deployment isn't being accessed
        locally, if there is no root to fall back to, or the resolved path is
        not a directory.
    """
    if root_arg:
        if not _is_local_request(http_request):
            raise HTTPException(status_code=400, detail=_LOCAL_PATH_REMOTE_ERROR)
        root = Path(root_arg)
    elif app.state.vault_root is not None:
        root = Path(app.state.vault_root)
    else:
        raise HTTPException(
            status_code=400,
            detail="No vault root to scan. Select a Memory Space with a root, "
            "or pass an explicit 'root'.",
        )
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"{root} is not a directory.")
    return root


@router.post("/import/obsidian/scan", response_model=ImportScanResponse)
def scan_obsidian_vault(
    request: ImportScanRequest, http_request: Request
) -> ImportScanResponse:
    """Obsidian import, phase 1: classify each note as skipped or needs_review.

    Pure filesystem walk + checkpoint hashing -- **no LLM, no pipeline, and
    nothing is written**. For each ``*.md`` note under the resolved root, the
    note's vault-relative path is its ``external_key``; the note text is hashed
    with the exact same ``turn_hash``/``transcript_hash`` the save path uses
    (never a second hashing scheme), and compared against any existing
    ``ConversationCheckpoint`` (``derive_conversation_id`` + ``checkpoint_store``).
    A note whose current content hashes identically to its stored checkpoint is
    ``"skipped"``; a new or changed note is ``"needs_review"``.

    The review/import phases that follow reuse the unchanged
    ``/memory/preview`` and ``/memory/commit`` routes, one call per
    ``needs_review`` note -- so an imported note goes through exactly the same
    Manager AI pipeline as a ChatGPT conversation, not a second one.
    ``review_mode`` tells the dashboard whether to lay the review out flat
    (<= 20 changed) or grouped by source file (> 20).

    Haven's own vault-scoped directories (``vault/``, ``concepts/``,
    ``notes/``, and the ``.haven/`` checkpoint/write-trace dirs) are always
    excluded from the walk, so generated memory/concept notes are never
    re-ingested even when the import source is the active space's own root.

    Raises
    ------
    HTTPException
        400 if an explicit ``root`` is given but this deployment isn't being
        accessed locally, if no root is given and the active deployment has
        no single vault root to default to, or if the resolved root is not a
        directory.
    """
    root = _resolve_import_root(request.root, http_request)

    exclude_dirs = [
        app.state.vault_dir,
        app.state.concept_dir,
        app.state.notes_dir,
        app.state.checkpoint_dir,
        app.state.write_trace_dir,
    ]

    app.state.checkpoint_store.load()

    notes: List[ImportScanNote] = []
    skipped = 0
    changed = 0
    for relative_path, absolute_path in obsidian_importer.iter_notes(
        root, exclude_dirs
    ):
        conversation_id = derive_conversation_id(SourceType.OBSIDIAN, relative_path)
        try:
            turns = obsidian_importer.note_to_turns(relative_path, absolute_path)
            incoming_turn_hashes = [
                turn_hash(role.value, content) for role, content in turns
            ]
            incoming_transcript_hash = transcript_hash(incoming_turn_hashes)
            is_skipped = (
                app.state.checkpoint_store.has(conversation_id)
                and app.state.checkpoint_store.get(conversation_id).transcript_hash
                == incoming_transcript_hash
            )
        except (OSError, UnicodeDecodeError):
            # Unreadable or non-UTF-8/binary note: surface it for review
            # rather than crashing the whole scan (UnicodeDecodeError is a
            # ValueError subclass, not an OSError, so it needs its own arm
            # here -- a binary file dropped into an Obsidian vault is a
            # realistic case ``note_to_turns``'s ``read_text`` doesn't guard).
            is_skipped = False

        if is_skipped:
            skipped += 1
            status = "skipped"
        else:
            changed += 1
            status = "needs_review"
        notes.append(ImportScanNote(source_file=relative_path, status=status))

    return ImportScanResponse(
        root=str(root),
        scanned=len(notes),
        skipped=skipped,
        changed=changed,
        review_mode="grouped" if changed > 20 else "flat",
        notes=notes,
    )


@router.post("/import/obsidian/preview", response_model=PreviewMemoryResponse)
def preview_obsidian_note(
    request: ImportPreviewRequest, http_request: Request
) -> PreviewMemoryResponse:
    """Obsidian import, phase 3: preview one changed note for review.

    Reads *request.source_file* (a vault-relative path from a prior scan)
    server-side -- the browser can't read the local disk -- turns it into a
    single ``Role.USER`` turn via
    :func:`obsidian.integrations.obsidian.importer.note_to_turns`, and runs it
    through the **unchanged** ``preview_memory`` route function in-process (the
    same in-process-call pattern ``capture_note`` uses for ``save_memory``).
    Provenance (source/source_file/imported_at/memory_space) is stamped here
    from the active space name and carried on the ``PendingReview`` so the
    later ``/memory/commit`` applies it -- the browser never has to know it.

    Returns a normal :class:`PreviewMemoryResponse` (``review_id`` + items),
    consumed by the standard ``POST /memory/commit`` at import time. The note
    file is only read, never modified.

    Raises
    ------
    HTTPException
        400 for a bad/missing root (see :func:`_resolve_import_root`) or a
        ``source_file`` that escapes the root; 404 if the note does not
        exist or can't be read as text (e.g. a binary/non-UTF-8 file, or a
        permission error) -- the scan route (``/import/obsidian/scan``)
        already surfaces unreadable notes for review rather than crashing,
        so this mirrors that with a clean error instead of a raw 500.
    """
    root = _resolve_import_root(request.root, http_request)
    absolute = (root / request.source_file).resolve()
    try:
        absolute.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(
            status_code=400, detail="source_file must be inside the vault root."
        )
    if not absolute.is_file():
        raise HTTPException(
            status_code=404, detail=f"Note not found: {request.source_file}"
        )

    try:
        turns = obsidian_importer.note_to_turns(request.source_file, absolute)
    except (OSError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Could not read note as text: {request.source_file} ({exc})",
        ) from exc
    prov = obsidian_importer.provenance(
        request.source_file, getattr(app.state, "active_space_name", None)
    )
    save_request = SaveMemoryRequest(
        conversation=[
            ConversationTurnRequest(role=role, content=content)
            for role, content in turns
        ],
        source=SourceType.OBSIDIAN,
        external_key=request.source_file,
        provenance=prov,
    )
    return preview_memory(save_request)


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard_ui() -> str:
    """Serve the Memory Dashboard's static, single-file UI.

    A plain HTML/CSS/JS page (no build step) that consumes
    ``obsidian.server.dashboard``'s JSON endpoints via ``fetch`` — this
    route contributes no retrieval, ranking, or aggregation logic of its
    own, only the page shell. Read from disk on every request, the same
    "always reflects the current file" convention the rest of this module
    uses for the vault/concept state, so editing the page during local
    development needs no server restart.
    """
    html_path = Path(__file__).parent / "static" / "dashboard.html"
    return html_path.read_text(encoding="utf-8")


@app.get("/format-utc.js", include_in_schema=False)
def format_utc_js() -> Response:
    """Serve the dashboard's presentation-only UTC -> local timestamp helper.

    Same "read from disk on every request, no build step" convention as
    :func:`dashboard_ui` above — see that route's docstring. A dedicated
    module (rather than inlined in ``dashboard.html``'s script) so it can
    be unit-tested directly (``obsidian/server/static/format-utc.test.js``).
    """
    js_path = Path(__file__).parent / "static" / "format-utc.js"
    return Response(
        content=js_path.read_text(encoding="utf-8"),
        media_type="application/javascript",
    )


app.include_router(router)
app.include_router(dashboard_router)
app.include_router(benchmark_explorer_router)
