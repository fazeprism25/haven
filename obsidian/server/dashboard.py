"""Haven's Memory Dashboard — read-only diagnostics API.

::

    Dashboard UI (obsidian/server/static/dashboard.html)
            |
            v
        FastAPI (this module)
            |
            v
    MemoryStore / ConceptGraph / AliasIndex / MemoryEngine  (unmodified)
            |
            v
    Real Haven Vault (Markdown files on disk)

Exposes Haven's internal state — memories grouped by type, vault/concept/
retrieval statistics, and the Retrieval Inspector — for debugging, demos,
and the Memory Dashboard UI. This module contributes no new retrieval, ranking, or
scoring logic of its own: every number it returns is either copied
directly off an existing ``KnowledgeObject``/``Concept``/``Relationship``/
``Attachment``, or produced by calling
:meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace` — the
same method :mod:`obsidian.server.main`'s ``POST /retrieve_context``
already calls for its own ``include_trace`` option. The Retrieval
Inspector endpoints below are a second, dashboard-scoped entry point onto
that same method, not a reimplementation of it. The Inspector additionally
attaches the rest of the deterministic pipeline — Slot Allocation (visible
via ``CandidateTrace``/``RetrievalPipelineStats``, already part of
``RetrievalTrace``), ``WorkingContextBuilder``, and
``StructuredPromptBuilder`` — via the unmodified
:meth:`~obsidian.memory_engine.engine.MemoryEngine.query_working_context`
and :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_structured`
(see :func:`_working_contexts_and_prompt`), degrading gracefully to the
``ContextBuilder``-only view above when those APIs are unavailable.

``_load_concepts`` here is a deliberate, content-identical duplicate of
:func:`obsidian.server.main._load_concepts` (itself already a duplicate of
``benchmarks.adapters.haven_adapter``'s helper of the same name) rather
than a cross-import from ``main`` — importing from ``main`` here would
make module import order load-bearing (``main`` imports this module's
``router``), and ``ConceptGraph`` has no enumeration method to call
instead (see that module's docstring). Re-parsing three lines of concept
files is cheaper than either risk.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response

from obsidian.core.enums import MemoryDomain, MemoryType
from obsidian.core.memory_domain import resolve_domain
from obsidian.manager_ai.models import KnowledgeObject, get_decision_metadata
from obsidian.memory_engine.engine import MemoryEngine
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.concept_parser import ConceptParser
from obsidian.ontology.models import Concept
from obsidian.ontology.retrieval_models import (
    ProjectStateTrace,
    RankedCandidate,
    RetrievalTrace,
    StateRefTrace,
    WorkingContext,
)
from obsidian.server.schemas import (
    ConceptStats,
    DashboardDecisionDetail,
    DashboardMemory,
    DashboardResponse,
    DomainSection,
    InspectorResponse,
    MemoryOntology,
    NextActionSummary,
    OntologyConceptDetail,
    OntologyRelationshipDetail,
    ProjectOverview,
    ProjectStateItem,
    RetrievalStats,
    TopicSummary,
    VaultStats,
    WorkingContextSummary,
    WriteTraceDetailResponse,
    WriteTraceListResponse,
    WriteTraceSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dashboard")

_DEFAULT_RECENT_MEMORIES_LIMIT = 20
_MAX_RECENT_MEMORIES_LIMIT = 200


def _load_concepts(concept_dir: Path) -> List[Concept]:
    """Return every Concept currently persisted in *concept_dir*.

    See the module docstring for why this duplicates
    :func:`obsidian.server.main._load_concepts` instead of importing it.
    """
    parser = ConceptParser()
    return [parser.read(path).concept for path in sorted(concept_dir.glob("*.md"))]


def _refresh_read_state(request: Request) -> None:
    """Reload the two collaborators whose authoritative state lives on disk.

    Mirrors exactly what ``obsidian.server.main.retrieve_context`` does
    before running the pipeline, so dashboard reads see the same
    just-written-to-disk state a retrieval call would.
    """
    app_state = request.app.state
    app_state.memory_store.load()
    app_state.alias_index.rebuild(_load_concepts(app_state.concept_dir))


def _to_decision_detail(ko: KnowledgeObject) -> Optional[DashboardDecisionDetail]:
    """Project *ko*'s ``DecisionMetadata`` for the dashboard, if it has one."""
    metadata = get_decision_metadata(ko)
    if metadata is None:
        return None
    return DashboardDecisionDetail(
        reason=metadata.reason,
        alternatives_considered=list(metadata.alternatives_considered),
        status=metadata.status.value,
        supersedes=str(metadata.supersedes) if metadata.supersedes is not None else None,
        superseded_by=str(metadata.superseded_by)
        if metadata.superseded_by is not None
        else None,
    )


def _to_dashboard_memory(ko: KnowledgeObject) -> DashboardMemory:
    return DashboardMemory(
        id=str(ko.id),
        canonical_fact=ko.canonical_fact,
        memory_type=ko.memory_type,
        confidence=ko.confidence,
        importance=ko.importance,
        confirmation_count=ko.confirmation_count,
        valid_from=ko.valid_from,
        valid_until=ko.valid_until,
        last_confirmed=ko.last_confirmed,
        decision=_to_decision_detail(ko),
        topics=[t.name for t in ko.topics],
    )


def _sorted_recent_first(memories: List[KnowledgeObject]) -> List[KnowledgeObject]:
    """Sort *memories* by descending ``valid_from``, ties broken by ``id``.

    The id tie-break keeps ordering deterministic for memories created in
    the same instant, the same convention used throughout the retrieval
    pipeline (e.g. ``RankedCandidate._sort_key``).
    """
    return sorted(memories, key=lambda ko: (ko.valid_from, str(ko.id)), reverse=True)


def _memories_of_type(
    memories: List[KnowledgeObject], memory_type: MemoryType
) -> List[DashboardMemory]:
    matches = [ko for ko in memories if ko.memory_type == memory_type]
    return [_to_dashboard_memory(ko) for ko in _sorted_recent_first(matches)]


def _domain_sections(memories: List[KnowledgeObject]) -> List[DomainSection]:
    """Group every memory by :class:`MemoryDomain`, then by :class:`MemoryType`.

    Replaces the old fixed 5-type ``_memories_of_type`` calls: every one of
    the 18 :class:`MemoryType` members is reachable here, not just
    ``project``/``decision``/``belief``/``preference``/``task``. A type
    with zero memories is omitted from its domain's ``by_type`` entirely
    (see :class:`~obsidian.server.schemas.DomainSection`'s docstring), and
    a domain with no memories at all still appears with an empty
    ``by_type`` so the dashboard can always render all three domain tabs.
    """
    sections: Dict[MemoryDomain, Dict[str, List[DashboardMemory]]] = {
        domain: {} for domain in MemoryDomain
    }
    for memory_type in MemoryType:
        matches = _memories_of_type(memories, memory_type)
        if matches:
            sections[resolve_domain(memory_type)][memory_type.value] = matches
    return [
        DomainSection(domain=domain.value, by_type=sections[domain])
        for domain in MemoryDomain
    ]


def _vault_stats(memories: List[KnowledgeObject]) -> VaultStats:
    total = len(memories)
    by_type: Dict[str, int] = {member.value: 0 for member in MemoryType}
    by_domain: Dict[str, int] = {domain.value: 0 for domain in MemoryDomain}
    for ko in memories:
        by_type[ko.memory_type.value] += 1
        by_domain[resolve_domain(ko.memory_type).value] += 1
    active = sum(1 for ko in memories if ko.valid_until is None)
    return VaultStats(
        total_memories=total,
        by_type=by_type,
        by_domain=by_domain,
        active_count=active,
        archived_count=total - active,
        average_confidence=(sum(ko.confidence for ko in memories) / total)
        if total
        else 0.0,
        average_importance=(sum(ko.importance for ko in memories) / total)
        if total
        else 0.0,
    )


def _concept_stats(
    concepts: List[Concept], graph: ConceptGraph, alias_index_size: int
) -> ConceptStats:
    """Count relationships/attachments via ``ConceptGraph``'s public, per-concept queries.

    ``ConceptGraph`` has no "list every relationship/attachment" method
    (only per-concept traversal — see that module's docstring), so totals
    are built by unioning each concept's own relationships/attachments,
    deduplicated by id since a relationship touches two concepts and would
    otherwise be counted twice.
    """
    relationship_ids = set()
    attachment_ids = set()
    for concept in concepts:
        for relationship in graph.relationships(concept.id):
            relationship_ids.add(relationship.id)
        for attachment in graph.attachments_for_concept(concept.id):
            attachment_ids.add(attachment.id)
    return ConceptStats(
        total_concepts=len(concepts),
        total_relationships=len(relationship_ids),
        total_attachments=len(attachment_ids),
        alias_index_size=alias_index_size,
    )


def _ontology_for_memory(graph: ConceptGraph, memory_id: UUID) -> MemoryOntology:
    """Every Concept/Relationship reachable from *memory_id*, via ``ConceptGraph`` alone.

    Reuses ``ConceptGraph.concepts_for_knowledge_object`` (the memory ->
    concept reverse lookup) and ``ConceptGraph.relationships`` (the same
    per-concept edge query :func:`_concept_stats` already uses for
    aggregate counts) — no new graph traversal logic, only a per-memory
    projection of two existing public methods.
    """
    concepts = graph.concepts_for_knowledge_object(memory_id)

    relationships: Dict[UUID, OntologyRelationshipDetail] = {}
    for concept in concepts:
        for relationship in graph.relationships(concept.id):
            if relationship.id in relationships:
                continue
            relationships[relationship.id] = OntologyRelationshipDetail(
                id=str(relationship.id),
                source_id=str(relationship.source_id),
                source_label=graph.get_concept(relationship.source_id).label,
                target_id=str(relationship.target_id),
                target_label=graph.get_concept(relationship.target_id).label,
                relationship_type=relationship.relationship_type,
                confidence=relationship.confidence,
            )

    return MemoryOntology(
        concepts=[
            OntologyConceptDetail(
                id=str(c.id),
                label=c.label,
                aliases=list(c.aliases),
                description=c.description,
            )
            for c in concepts
        ],
        relationships=sorted(relationships.values(), key=lambda r: r.id),
    )


def _build_engine(request: Request) -> MemoryEngine:
    """Build a ``MemoryEngine`` for this request, honoring the Query Rewriting
    setting (``obsidian.server.main``'s ``GET``/``PUT /api/v1/settings/query-rewriting``).

    Reads ``app_state.query_rewriting_enabled`` fresh on every call, exactly
    like every ``MemoryEngine`` construction site in ``obsidian.server.main``
    does via ``_active_query_rewriter`` -- so a toggle takes effect on the
    dashboard's Retrieval Inspector immediately, with no restart.
    """
    app_state = request.app.state
    return MemoryEngine(
        app_state.alias_index,
        app_state.concept_graph,
        app_state.memory_store,
        app_state.retrieval_config,
        query_rewriter=(
            app_state.query_rewriter if app_state.query_rewriting_enabled else None
        ),
    )


def _context_memory_count(context: WorkingContext) -> int:
    """Total memories across every role bucket in *context*."""
    return sum(len(bucket.members) for bucket in context.buckets)


def _context_title(context: WorkingContext, graph: ConceptGraph) -> str:
    """*context*'s title, with a TOPIC context's raw anchor id resolved to a label.

    ``WorkingContextBuilder`` names a TOPIC context after its anchor
    concept's raw UUID (see that module's docstring) since it has no
    access to ``ConceptGraph`` — this is a display-only enrichment done
    here, where the graph is already loaded, falling back to the raw
    title if the anchor concept can no longer be found.
    """
    if context.anchor_concept_id is not None and graph.has_concept(
        context.anchor_concept_id
    ):
        return graph.get_concept(context.anchor_concept_id).label
    return context.title


def _facts(members: Tuple[RankedCandidate, ...]) -> List[str]:
    return [m.candidate.knowledge_object.canonical_fact for m in members]


def _current_focus(context: WorkingContext) -> Optional[str]:
    """The single highest-``final_score`` memory across every role bucket.

    "What's most salient right now" for this context — distinct from
    ``current_goal``, which is specifically the top GOAL-role memory.
    """
    members = [member for bucket in context.buckets for member in bucket.members]
    if not members:
        return None
    return min(members).candidate.knowledge_object.canonical_fact


def _to_working_context_summary(
    context: WorkingContext, graph: ConceptGraph
) -> WorkingContextSummary:
    state = context.state
    return WorkingContextSummary(
        key=context.key,
        title=_context_title(context, graph),
        kind=context.kind.value,
        status=state.status.value,
        memory_count=_context_memory_count(context),
        current_goal=state.current_goal.candidate.knowledge_object.canonical_fact
        if state.current_goal is not None
        else None,
        current_focus=_current_focus(context),
        recent_decisions=_facts(state.recent_decisions),
        pending_tasks=_facts(state.pending_tasks),
        open_questions=_facts(state.open_questions),
    )


def _working_contexts_and_prompt(
    engine: MemoryEngine, query: str
) -> Tuple[Optional[List[dict]], Optional[str]]:
    """Best-effort ``WorkingContext``/``StructuredPrompt`` detail for the Inspector.

    Extends the Retrieval Inspector past ``ContextBuilder`` by calling the
    unmodified :meth:`MemoryEngine.query_working_context` and
    :meth:`MemoryEngine.query_structured`, exactly the pair
    :func:`obsidian.server.main.retrieve_working_context` already calls for
    the browser extension's preview. No new assembly logic: every
    ``WorkingContext`` is serialised via its own ``to_dict()``.

    Returns ``(None, None)`` — never raises — when ``query_working_context``
    is missing (an older engine) or either call fails for any reason, the
    same fail-open contract :func:`_working_context_summaries` below and
    ``obsidian.server.main.retrieve_working_context`` already use, so a
    Working Context problem degrades the Inspector to its pre-WorkingContext
    behavior instead of breaking it.
    """
    if not hasattr(engine, "query_working_context"):
        return None, None
    try:
        contexts = engine.query_working_context(query)
        structured_prompt = engine.query_structured(query)
    except Exception:
        logger.debug("Working Context unavailable for Inspector query", exc_info=True)
        return None, None
    return [context.to_dict() for context in contexts], structured_prompt


def _working_context_summaries(
    request: Request, memories: List[KnowledgeObject]
) -> Optional[List[WorkingContextSummary]]:
    """Best-effort Working Context summaries for the "Resume Work" panel.

    Purely additive on top of the unmodified
    :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_working_context`
    — seeded with a query built from every memory's own fact text (the same
    "no separate algorithm, reuse the memory's own text as the query"
    convention :func:`inspect_memory` already uses), so the pipeline's own
    ranking/acceptance/allocation naturally surfaces the most salient
    memories into Working Contexts, exactly as any other query would.

    Returns ``None`` — never raises — if ``query_working_context`` is
    missing (an older engine) or fails for any reason, so a Working Context
    problem can never take down the rest of the dashboard. This mirrors the
    fail-open contract :mod:`obsidian.memory_engine.query_rewriter` already
    uses for its own best-effort enhancement.
    """
    engine = _build_engine(request)
    if not hasattr(engine, "query_working_context"):
        return None
    try:
        digest_query = " ".join(ko.canonical_fact for ko in memories)
        contexts = engine.query_working_context(digest_query)
    except Exception:
        logger.debug("Working Context unavailable for Resume Work panel", exc_info=True)
        return None
    graph = request.app.state.concept_graph
    return [_to_working_context_summary(context, graph) for context in contexts]


def _state_ref_to_item(ref: StateRefTrace) -> ProjectStateItem:
    """Project one ``StateRefTrace`` verbatim into a ``ProjectStateItem``.

    No synthesis: every field is copied straight off *ref*, which is itself
    already a verbatim copy of the ``KnowledgeObject`` ``ProjectStateBuilder``
    accepted (see :mod:`obsidian.memory_engine.project_state`).
    """
    return ProjectStateItem(
        id=str(ref.knowledge_object_id),
        fact=ref.canonical_fact,
        confidence=ref.confidence,
        importance=ref.importance,
        valid_from=ref.valid_from,
    )


def _top_project_memory(memories: List[KnowledgeObject]) -> Optional[ProjectStateItem]:
    """The single most-recently-touched active ``MemoryType.PROJECT`` memory, if any.

    Stands in for "current milestone": ``ProjectState`` (Phase A) tracks no
    field for ``MemoryType.PROJECT`` at all (``PROJECT`` has no
    ``ContextCategory`` mapping — see
    :mod:`obsidian.memory_engine.coverage_analyzer`'s module docstring), so
    this is derived directly from the same already-loaded *memories* list
    :func:`get_dashboard`'s ``projects``/``recent_memories`` sections use,
    reusing :func:`_sorted_recent_first`'s exact ordering rather than a new
    sort or any retrieval call.
    """
    active_projects = [
        ko
        for ko in memories
        if ko.memory_type is MemoryType.PROJECT and ko.valid_until is None
    ]
    if not active_projects:
        return None
    top = _sorted_recent_first(active_projects)[0]
    return ProjectStateItem(
        id=str(top.id),
        fact=top.canonical_fact,
        confidence=top.confidence,
        importance=top.importance,
        valid_from=top.valid_from,
    )


def _overview_current_focus(trace: RetrievalTrace) -> Optional[str]:
    """The accepted candidate with the single highest ``final_score`` this run.

    Same "what's most salient right now" concept :func:`_current_focus`
    above already uses for a ``WorkingContext``'s role buckets, applied here
    to :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`'s
    own candidate list instead — avoids a second engine call just to build a
    ``WorkingContext`` for this one field.
    """
    accepted = [c for c in trace.candidates if c.accepted]
    if not accepted:
        return None
    return max(accepted, key=lambda c: c.final_score).canonical_fact


def _recommended_next_action(state: ProjectStateTrace) -> Optional[NextActionSummary]:
    """Deterministic priority pick over ``ProjectState``'s own tracked fields.

    Not a synthesized recommendation: the surfaced fact is rendered
    verbatim, exactly like every other never-inferred ``ProjectState`` field
    (see ``docs/architecture/PROJECT_STATE_DESIGN.md`` §3). Only the *choice
    of which* tracked field to surface first is new, fixed logic, in
    priority order blockers > active tasks > open questions > current
    objective — an unresolved blocker is the most urgent thing standing
    between "now" and forward progress; failing that, continuing an active
    task is more directly actionable than an open question; the current
    objective is the fallback orientation when nothing more specific is
    tracked. Returns ``None`` when none of the four are populated.
    """
    if state.blockers:
        return NextActionSummary(reason="blocker", item=_state_ref_to_item(state.blockers[0]))
    if state.active_tasks:
        return NextActionSummary(
            reason="active_task", item=_state_ref_to_item(state.active_tasks[0])
        )
    if state.open_questions:
        return NextActionSummary(
            reason="open_question", item=_state_ref_to_item(state.open_questions[0])
        )
    if state.current_objective is not None:
        return NextActionSummary(
            reason="current_objective",
            item=_state_ref_to_item(state.current_objective.value),
        )
    return None


def _project_overview(
    request: Request, memories: List[KnowledgeObject]
) -> Optional[ProjectOverview]:
    """Best-effort Project Overview — the Mission Control panel's data source.

    Built entirely from ``ProjectState`` (Phase A) plus *memories* already
    loaded for the rest of this response — no new retrieval, ranking, or LLM
    inference. Reuses the exact "digest query built from every memory's own
    fact text, fed through an unmodified ``MemoryEngine`` method" pattern
    :func:`_working_context_summaries` already established, calling
    :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
    instead of ``query_working_context`` since ``ProjectState`` is only
    reachable off ``RetrievalTrace.project_state`` today (see
    :mod:`obsidian.memory_engine.project_state`'s module docstring).

    Returns ``None`` — never raises — if ``query_with_trace`` fails for any
    reason, the same fail-open contract :func:`_working_context_summaries`
    already uses, so a Project Overview problem can never take down the rest
    of the dashboard.
    """
    engine = _build_engine(request)
    try:
        digest_query = " ".join(ko.canonical_fact for ko in memories)
        _, trace = engine.query_with_trace(digest_query)
    except Exception:
        logger.debug("Project Overview unavailable", exc_info=True)
        return None

    state = trace.project_state
    current_objective = (
        _state_ref_to_item(state.current_objective.value)
        if state.current_objective is not None
        else None
    )
    return ProjectOverview(
        current_objective=current_objective,
        current_milestone=_top_project_memory(memories),
        current_focus=_overview_current_focus(trace),
        active_tasks=[_state_ref_to_item(r) for r in state.active_tasks],
        active_blockers=[_state_ref_to_item(r) for r in state.blockers],
        open_questions=[_state_ref_to_item(r) for r in state.open_questions],
        recent_decisions=[_state_ref_to_item(r) for r in state.decisions],
        recommended_next_action=_recommended_next_action(state),
        gaps=list(state.gaps),
        field_coverage=state.confidence,
        generated_at=state.generated_at,
    )


@router.get("", response_model=DashboardResponse)
def get_dashboard(
    request: Request,
    response: Response,
    recent_limit: int = Query(
        _DEFAULT_RECENT_MEMORIES_LIMIT, ge=1, le=_MAX_RECENT_MEMORIES_LIMIT
    ),
) -> DashboardResponse:
    """Return Haven's full internal state in one call.

    Reloads ``memory_store``/``alias_index`` from disk first (the vault and
    concept files may have changed since the last request — see
    :func:`_refresh_read_state`), then projects the loaded state into the
    sections a debugging/demo dashboard needs. Runs no ranking, retrieval,
    or scoring of its own.

    Sets ``Cache-Control: no-store`` — this reflects state that changes on
    every write (a new memory should be visible in ``recent_memories`` the
    very next time this is fetched), so a stale cached response — from the
    browser's HTTP cache or an intermediate proxy, since this response
    otherwise carries no cache-defeating header at all — must never be
    served in its place.
    """
    response.headers["Cache-Control"] = "no-store"
    app_state = request.app.state
    with app_state.write_lock:
        _refresh_read_state(request)

        memories = app_state.memory_store.all()
        concepts = _load_concepts(app_state.concept_dir)

        return DashboardResponse(
            domains=_domain_sections(memories),
            recent_memories=[
                _to_dashboard_memory(ko)
                for ko in _sorted_recent_first(memories)[:recent_limit]
            ],
            vault_stats=_vault_stats(memories),
            concept_stats=_concept_stats(
                concepts, app_state.concept_graph, app_state.alias_index.size()
            ),
            retrieval_stats=RetrievalStats(
                alias_index_size=app_state.alias_index.size(),
                concept_count=len(concepts),
                vault_memory_count=len(memories),
                config=asdict(app_state.retrieval_config),
            ),
            working_contexts=_working_context_summaries(request, memories),
            project_overview=_project_overview(request, memories),
        )


@router.get("/inspect", response_model=InspectorResponse)
def inspect_query(request: Request, query: str) -> InspectorResponse:
    """Retrieval Inspector for an arbitrary query string.

    Runs the exact same :meth:`MemoryEngine.query_with_trace` call
    ``POST /retrieve_context`` makes with ``include_trace=True`` — this
    endpoint exists only to give the dashboard a discoverable, GET-able
    route for it, not a second implementation. Also attaches best-effort
    ``working_contexts``/``structured_prompt`` (see
    :func:`_working_contexts_and_prompt`) so the Inspector can show the rest
    of the deterministic pipeline past ``ContextBuilder``.
    """
    with request.app.state.write_lock:
        _refresh_read_state(request)
        engine = _build_engine(request)
        context, trace = engine.query_with_trace(query)
        working_contexts, structured_prompt = _working_contexts_and_prompt(engine, query)
    return InspectorResponse(
        context=context,
        trace=trace.to_dict(),
        working_contexts=working_contexts,
        structured_prompt=structured_prompt,
    )


@router.get("/inspect/memory/{memory_id}", response_model=InspectorResponse)
def inspect_memory(request: Request, memory_id: str) -> InspectorResponse:
    """Retrieval Inspector for an existing memory, by id.

    There is no separate "explain this stored memory" algorithm — a
    memory's own ``canonical_fact`` is used as the query text for
    :meth:`MemoryEngine.query_with_trace`, showing where that memory (and
    whatever else matches its own text) would rank if it were searched
    for right now.

    Raises
    ------
    HTTPException
        404 if *memory_id* is not a valid UUID or is not in the vault.
    """
    try:
        parsed_id = UUID(memory_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Memory not found")

    app_state = request.app.state
    with app_state.write_lock:
        _refresh_read_state(request)
        if not app_state.memory_store.has(parsed_id):
            raise HTTPException(status_code=404, detail="Memory not found")

        knowledge = app_state.memory_store.get(parsed_id)
        engine = _build_engine(request)
        context, trace = engine.query_with_trace(knowledge.canonical_fact)
        working_contexts, structured_prompt = _working_contexts_and_prompt(
            engine, knowledge.canonical_fact
        )
        return InspectorResponse(
            context=context,
            trace=trace.to_dict(),
            source_memory_id=memory_id,
            ontology=_ontology_for_memory(app_state.concept_graph, parsed_id),
            working_contexts=working_contexts,
            structured_prompt=structured_prompt,
            provenance=knowledge.metadata.get("provenance"),
            classification_reason=knowledge.metadata.get("classification_reason"),
            domain=resolve_domain(knowledge.memory_type),
            topics=[
                TopicSummary(name=t.name, confidence=t.confidence)
                for t in knowledge.topics
            ],
        )


@router.get("/write-traces", response_model=WriteTraceListResponse)
def list_write_traces(
    request: Request,
    limit: int = Query(
        _DEFAULT_RECENT_MEMORIES_LIMIT, ge=1, le=_MAX_RECENT_MEMORIES_LIMIT
    ),
) -> WriteTraceListResponse:
    """Write Inspector: list recent persisted write traces, newest first.

    Unlike the Retrieval Inspector, a write trace cannot be recomputed on
    demand — it is captured once, at write time, by
    ``obsidian.server.main.save_memory`` (every ``POST /memory`` call,
    including the "duplicate" and "no facts extracted" cases) and
    persisted via
    :class:`~obsidian.ontology.write_trace_store.WriteTraceWriter`. This
    route only reads what is already on disk; it runs no pipeline of its
    own. Reuses the same ``limit``/pagination shape as ``GET /dashboard``'s
    ``recent_limit``.
    """
    app_state = request.app.state
    app_state.write_trace_store.load()
    traces = list(reversed(app_state.write_trace_store.all()))[:limit]
    return WriteTraceListResponse(
        traces=[
            WriteTraceSummary(
                trace_id=str(t.trace_id),
                conversation_id=(
                    str(t.conversation_id) if t.conversation_id is not None else None
                ),
                mode=t.mode,
                status=t.status,
                fact_count=len(t.facts),
                knowledge_object_count=len(t.knowledge_object_ids),
                total_duration_ms=t.stage_timings_ms.get("total"),
                created_at=t.created_at,
            )
            for t in traces
        ]
    )


@router.get(
    "/write-traces/{trace_id}", response_model=WriteTraceDetailResponse
)
def get_write_trace(request: Request, trace_id: str) -> WriteTraceDetailResponse:
    """Write Inspector: full detail for one persisted write trace.

    Raises
    ------
    HTTPException
        404 if *trace_id* is not a valid UUID or has no persisted trace.
    """
    try:
        parsed_id = UUID(trace_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Write trace not found")

    app_state = request.app.state
    app_state.write_trace_store.load()
    trace = app_state.write_trace_store.get(parsed_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Write trace not found")

    return WriteTraceDetailResponse(trace=trace.to_dict())
