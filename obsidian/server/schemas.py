"""Request/response models for the Haven HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from obsidian.core.enums import MemoryDomain, MemoryType, Role, SourceType
from obsidian.ontology.enums import OntologyRelationshipType


class RetrieveContextRequest(BaseModel):
    query: str
    include_trace: bool = False


class RetrieveContextResponse(BaseModel):
    context: str
    # Populated only when the request set include_trace=True; omitted
    # from the serialised response otherwise (see main.py's
    # response_model_exclude_none) so existing callers see no change to
    # the response shape.
    trace: Optional[Dict[str, Any]] = None


class QueryRewritingSettingResponse(BaseModel):
    """Whether Query Rewriting (optional, experimental multi-query expansion —
    see ``obsidian.memory_engine.query_rewriter``) is enabled for this Haven
    server. ``enabled=False`` is the default: retrieval is then
    byte-for-byte identical to Haven's deterministic pipeline with no
    rewriter configured. See ``GET``/``PUT /api/v1/settings/query-rewriting``.
    """

    enabled: bool


class UpdateQueryRewritingSettingRequest(BaseModel):
    enabled: bool


class ConversationTurnRequest(BaseModel):
    """One turn of a full-conversation ``POST /memory`` payload.

    Mirrors the two fields of :class:`~obsidian.core.types.Event` the
    browser extension can actually supply from scraped DOM text -- role
    and content; ``timestamp``/``source``/``entities`` are filled in
    server-side when the ``Event`` is built (see
    ``obsidian.server.main.save_memory``).
    """

    role: Role
    content: str = Field(..., min_length=1)

    @field_validator("content")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped


class SaveMemoryRequest(BaseModel):
    """Either a legacy single-fact request or a full-conversation one.

    ``canonical_fact`` is the original "Remember" shape (the compose box's
    current text). ``conversation`` is the newer shape -- every visible
    ChatGPT turn, in chronological order -- and takes precedence when
    present, since it lets ``ManagerPipeline`` see the whole dialogue
    instead of one synthesized event (see
    ``obsidian.server.main.save_memory``). At least one of the two must be
    supplied; ``canonical_fact`` stays required-shaped (not just typed
    ``Optional``) for old callers that still send only it.

    ``external_key``/``source`` opt a request into conversation-level
    duplicate prevention (see ``obsidian.server.main.save_memory``'s
    docstring and :mod:`obsidian.checkpoint`). Both are optional and
    default to "not participating" (``None``/``SourceType.MANUAL``), so
    every existing caller that never sends them keeps today's exact
    behaviour -- no checkpoint is ever consulted or written for such a
    request.
    """

    canonical_fact: Optional[str] = None
    memory_type: MemoryType = MemoryType.FACT
    conversation: Optional[List[ConversationTurnRequest]] = None
    source: SourceType = SourceType.MANUAL
    external_key: Optional[str] = None
    # Optional free-form provenance stamped onto every KnowledgeObject this
    # request produces (under metadata["provenance"]). Used by the Obsidian
    # vault import to record source/source_file/imported_at/memory_space (see
    # obsidian.integrations.obsidian.importer.provenance). Additive and
    # gated: None for every existing caller, so nothing is stamped and
    # behaviour is byte-for-byte unchanged.
    provenance: Optional[Dict[str, Any]] = None

    @field_validator("canonical_fact")
    @classmethod
    def _reject_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("canonical_fact must not be blank")
        return stripped

    @model_validator(mode="after")
    def _require_fact_or_conversation(self) -> "SaveMemoryRequest":
        if not self.canonical_fact and not self.conversation:
            raise ValueError("Either canonical_fact or conversation must be provided")
        return self


class ReviewSummary(BaseModel):
    """Per-action counts for one Memory Review commit.

    Populated only by ``POST /api/v1/memory/commit`` (see
    ``obsidian.server.main.commit_memory``); every count is a plain tally
    of each reviewed item's ``review_action`` (see
    ``FactTrace.review_action`` in
    ``obsidian.ontology.write_trace_models``) -- nothing here is
    recomputed with different logic than what already built the write
    trace for the same commit.

    ``saved`` is the total number of ``KnowledgeObject``s persisted by this
    commit -- unchanged, edited, and added items all count toward it.
    ``edited``/``added`` are subsets of ``saved``. ``removed`` is the count
    of items present in the original preview but absent from the commit
    request (never part of ``saved``, since a removed item is never
    persisted).
    """

    saved: int
    edited: int
    added: int
    removed: int


class SaveMemoryResponse(BaseModel):
    """Result of a ``POST /memory`` call.

    ``status`` is ``"success"`` for every response shape that existed
    before conversation-level duplicate prevention (see
    ``obsidian.server.main.save_memory``) -- ``id``/``canonical_fact``/
    ``memory_type`` are always populated in that case, exactly as before.
    ``status="duplicate"`` is the new short-circuit case: the transcript
    exactly matched an already-processed checkpoint, so the pipeline never
    ran and there is no single object to report -- ``id``/
    ``canonical_fact``/``memory_type`` are ``None`` in that case. The
    existing HTTP 422 "nothing worth remembering" contract is unchanged
    and unrelated to this field (see that route's docstring for why the
    two are kept separate for now).
    """

    status: Literal["success", "duplicate"] = "success"
    id: Optional[str] = None
    canonical_fact: Optional[str] = None
    memory_type: Optional[MemoryType] = None
    # The write trace captured for this call (see
    # ``obsidian.server.main.save_memory``). Purely additive: every existing
    # caller (the browser extension) only reads ``status``, never this field
    # -- it exists so the dashboard's Quick Capture can offer an "open the
    # generated Write Trace" link. ``None`` only when trace capture itself
    # is not applicable (it never is today; a trace_id is always minted).
    trace_id: Optional[str] = None
    # Only set by POST /api/v1/memory/commit (see obsidian.server.main
    # .commit_memory); None for every other caller of this response model
    # (save_memory's normal path, Quick Capture, demo seeding), which never
    # went through Memory Review and so have nothing to summarize.
    review_summary: Optional[ReviewSummary] = None
    # Count of each KnowledgeDecision value produced by this call (e.g.
    # {"new": 2, "confirm": 1}), surfaced so a caller batching many commits
    # -- the Obsidian vault import -- can tally created/confirmed/superseded
    # totals for its completion screen. Only set by commit_memory (which
    # already computes this exact Counter); None for every other caller.
    decision_counts: Optional[Dict[str, int]] = None


class ReviewMemoryItem(BaseModel):
    """One extracted fact awaiting the user's review, from ``POST /memory/preview``.

    Deliberately narrow: only what the review dialog actually shows.
    ``text``/``memory_type`` are the two fields the user can edit;
    ``evidence`` is shown read-only ("Detected from: ..."). No confidence,
    classification confidence, importance score, or canonical-matching hint
    is exposed here -- none of those are editable or displayed, and
    canonical matching is recomputed authoritatively at commit time anyway
    (see ``obsidian.server.main.commit_memory``), so a provisional decision
    computed at preview time would only be misleading.
    """

    fact_index: int
    text: str
    memory_type: MemoryType
    evidence: str


class PreviewMemoryResponse(BaseModel):
    """Result of a ``POST /api/v1/memory/preview`` call.

    ``status="duplicate"`` mirrors ``SaveMemoryResponse``'s own duplicate
    short-circuit (see ``obsidian.server.main._checkpoint_lookup``) -- the
    transcript exactly matches an already-processed checkpoint, so
    extraction never ran; ``review_id``/``items`` are ``None``/empty in
    that case. Otherwise ``review_id`` identifies the server-side
    ``PendingReview`` this preview created (pass it to
    ``POST /memory/commit`` or ``POST /memory/cancel``), and ``items`` is
    one ``ReviewMemoryItem`` per fact the Extractor/Classifier/
    ImportanceScorer produced -- possibly empty, if nothing was worth
    remembering (still ``status="ok"``, not a 422 -- the review dialog can
    always offer to add a memory manually).
    """

    status: Literal["ok", "duplicate"]
    review_id: Optional[str] = None
    items: List[ReviewMemoryItem] = Field(default_factory=list)


class CommittedMemoryItem(BaseModel):
    """One reviewed memory as submitted to ``POST /api/v1/memory/commit``.

    ``fact_index`` ties this back to the ``ReviewMemoryItem`` it originated
    from; ``None`` means the user added this memory during review (it has
    no extracted origin). Exactly the two fields the review dialog lets
    the user touch -- ``text``/``memory_type`` -- nothing else: an added
    item's confidence/importance are fixed server-side defaults, never
    supplied by the client (see ``obsidian.server.main.commit_memory``).
    Any ``fact_index`` from the original preview that does not appear in
    the commit request's ``items`` is treated as deleted by the user.
    """

    fact_index: Optional[int] = None
    text: str = Field(..., min_length=1)
    memory_type: MemoryType

    @field_validator("text")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("text must not be blank")
        return stripped


class CommitMemoryRequest(BaseModel):
    """Request body for ``POST /api/v1/memory/commit``.

    ``review_id`` must match a ``PendingReview`` a prior
    ``POST /memory/preview`` call created (and that hasn't already been
    committed or cancelled) -- see ``obsidian.server.main.commit_memory``.
    """

    review_id: str
    items: List[CommittedMemoryItem] = Field(default_factory=list)


class CancelMemoryRequest(BaseModel):
    """Request body for ``POST /api/v1/memory/cancel``.

    Cancelling an unknown, already-committed, or already-cancelled
    ``review_id`` is a harmless no-op, not an error -- see
    ``obsidian.server.main.cancel_memory``.
    """

    review_id: str


class CaptureNoteRequest(BaseModel):
    """A free-form Markdown note written in the dashboard's Quick Capture.

    ``content`` is the note's Markdown body, saved verbatim into the vault
    and also fed through the existing save-memory flow (see
    ``obsidian.server.main.capture_note``) -- Quick Capture is just another
    input source for the one Manager Pipeline, alongside the browser
    extension, not a second extraction path. ``title``/``tags`` are optional
    Obsidian-facing metadata for the saved note file; they do not change how
    memories are extracted.
    """

    title: Optional[str] = None
    content: str = Field(..., min_length=1)
    tags: List[str] = Field(default_factory=list)

    @field_validator("title")
    @classmethod
    def _blank_title_to_none(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("content")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("content must not be blank")
        return stripped

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: List[str]) -> List[str]:
        return [t.strip() for t in value if t.strip()]


class CaptureNoteResponse(BaseModel):
    """Result of a ``POST /api/v1/capture`` call.

    ``note_path`` is the absolute path of the original Markdown note that
    was saved into the vault -- always populated, even when the pipeline
    found nothing worth remembering. The remaining fields mirror
    :class:`SaveMemoryResponse` (the note's Markdown was run through the
    exact same ``save_memory`` flow): ``status`` is ``"success"``/
    ``"duplicate"`` from that call, or ``"no_memories"`` when the note was
    saved but the Manager Pipeline extracted nothing (the ``POST /memory``
    422 case, which is not an error for a capture -- the note is still on
    disk). ``id``/``canonical_fact``/``memory_type`` describe the first
    ``KnowledgeObject`` created (``None`` for duplicate/no_memories), and
    ``trace_id`` is the generated Write Trace so the dashboard can link to
    it.
    """

    status: Literal["success", "duplicate", "no_memories"]
    note_path: str
    id: Optional[str] = None
    canonical_fact: Optional[str] = None
    memory_type: Optional[MemoryType] = None
    trace_id: Optional[str] = None


class WorkingContextPreviewRequest(BaseModel):
    query: str


# ---------------------------------------------------------------------------
# Vault selection
# ---------------------------------------------------------------------------


class VaultInfo(BaseModel):
    """Describes the vault Haven is currently reading/writing.

    ``configured`` is ``False`` only for the out-of-the-box default (no
    vault ever explicitly selected via ``POST /api/v1/vault``, and no
    ``HAVEN_VAULT_DIR``-style env var set) -- the dashboard uses this to
    show a first-run "select your vault" prompt instead of the normal
    layout. ``root``/``is_existing_obsidian_vault`` are ``None`` in that
    same case, since an env-var-configured deployment (the pre-existing
    way to run Haven) has no single "root" folder the four directories are
    nested under -- see ``obsidian.server.main._resolve_initial_vault_paths``.
    """

    configured: bool
    root: Optional[str] = None
    vault_dir: str
    concept_dir: str
    is_existing_obsidian_vault: Optional[bool] = None
    memory_count: int


class SelectVaultRequest(BaseModel):
    root: str = Field(..., min_length=1)


class SelectVaultResponse(VaultInfo):
    """``VaultInfo`` plus what changed by selecting this root just now."""

    created: bool
    initialized: bool


class SeedDemoResponse(BaseModel):
    """Result of ``POST /api/v1/dev/seed_demo`` or ``.../dev/reset_demo``."""

    bulk_facts: int
    conversation_calls: int


# ---------------------------------------------------------------------------
# Memory Spaces
#
# "Memory Space" is the only vault-shaped concept the dashboard exposes to
# users -- internally each space is still just a registered set of the same
# four directories ``_configure_vault_state`` has always rebuilt.
# ---------------------------------------------------------------------------


class SpaceInfo(BaseModel):
    """One registered Memory Space, as shown in the dashboard's space list."""

    id: str
    name: str
    root: Optional[str] = None
    env_managed: bool = False


class SpacesListResponse(BaseModel):
    active_space_id: str
    env_managed: bool
    spaces: List[SpaceInfo]


class CreateSpaceRequest(BaseModel):
    name: str = Field(..., min_length=1)
    root: str = Field(..., min_length=1)


class UpdateSpaceRequest(BaseModel):
    """Rename and/or re-point a space's root. At least one field must be set."""

    name: Optional[str] = None
    root: Optional[str] = None


class ActivateSpaceRequest(BaseModel):
    """``confirm=True`` acknowledges that any pending Memory Review in the
    space being switched away from will be discarded."""

    confirm: bool = False


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


class DashboardDecisionDetail(BaseModel):
    """Decision-specific fields for a ``MemoryType.DECISION`` memory.

    A direct projection of
    :class:`~obsidian.manager_ai.models.DecisionMetadata`. Only present on
    ``DashboardMemory.decision`` when the underlying ``KnowledgeObject``
    actually has one attached — see
    :func:`~obsidian.manager_ai.models.get_decision_metadata` for why a
    decision may have none (e.g. it predates Decision Memory).
    """

    reason: str
    alternatives_considered: List[str]
    status: str
    supersedes: Optional[str] = None
    superseded_by: Optional[str] = None


class DashboardMemory(BaseModel):
    """One ``KnowledgeObject``, projected to the fields the dashboard needs.

    A direct field-for-field projection of
    :class:`~obsidian.manager_ai.models.KnowledgeObject` — no derived or
    recomputed values. ``evidence_chain`` and ``metadata`` are intentionally
    omitted; ``decision`` is the one structured exception, carrying
    :class:`DashboardDecisionDetail` when the memory has one (see that
    class's docstring). ``topics`` is a plain list of canonicalized topic
    names (confidence is not exposed here — see
    :class:`TopicSummary`/``InspectorResponse.topics`` for the confidence-
    carrying projection used by the Why? inspector).
    """

    id: str
    canonical_fact: str
    memory_type: MemoryType
    confidence: float
    importance: float
    confirmation_count: int
    valid_from: datetime
    valid_until: Optional[datetime] = None
    last_confirmed: Optional[datetime] = None
    decision: Optional[DashboardDecisionDetail] = None
    topics: List[str] = Field(default_factory=list)


class VaultStats(BaseModel):
    total_memories: int
    by_type: Dict[str, int]
    by_domain: Dict[str, int]
    active_count: int
    archived_count: int
    average_confidence: float
    average_importance: float


class ConceptStats(BaseModel):
    total_concepts: int
    total_relationships: int
    total_attachments: int
    alias_index_size: int


class RetrievalStats(BaseModel):
    """Snapshot of the state and configuration that governs retrieval.

    Not a log of past queries — Haven does not persist query history —
    but the live sizes of the indices ``MemoryEngine`` reads plus the
    ``RetrievalConfig`` weights/thresholds currently in effect, which is
    what "why did retrieval behave this way" debugging actually needs.
    """

    alias_index_size: int
    concept_count: int
    vault_memory_count: int
    config: Dict[str, Any]


class WorkingContextSummary(BaseModel):
    """One :class:`~obsidian.ontology.retrieval_models.WorkingContext`, projected for the "Resume Work" panel.

    Every field is copied or derived — never recomputed — from the
    WorkingContext objects :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_working_context`
    already built. ``title`` is the one enrichment: a TOPIC context's raw
    anchor-concept-id title is resolved to that concept's human-readable
    label, falling back to the raw title if the concept can't be found.
    ``current_focus`` is the single highest-``final_score`` memory across
    every role bucket in the context — "what's most salient right now",
    distinct from ``current_goal`` (specifically the top GOAL-role memory).
    """

    key: str
    title: str
    kind: str
    status: str
    memory_count: int
    current_goal: Optional[str] = None
    current_focus: Optional[str] = None
    recent_decisions: List[str] = Field(default_factory=list)
    pending_tasks: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)


class WorkingContextPreviewResponse(BaseModel):
    """Preview payload for the browser extension's Working Context dialog.

    ``available=False`` when Working Context assembly can't run at all — an
    older engine missing ``query_working_context``, or the best-effort call
    raising for any reason (see
    ``obsidian.server.main.retrieve_working_context``) — in which case
    ``structured_prompt`` is ``None`` and ``contexts`` is empty; the
    extension falls back to its pre-existing ``POST /retrieve_context`` flow
    in that case. ``structured_prompt`` is the exact XML-delimited prompt
    the extension's Insert action places into the compose box;
    ``contexts`` is the same read-only projection the dashboard's "Resume
    Work" panel uses (:class:`WorkingContextSummary`), reused verbatim so
    the two surfaces never drift.
    """

    available: bool
    structured_prompt: Optional[str] = None
    contexts: List[WorkingContextSummary] = Field(default_factory=list)


class ProjectStateItem(BaseModel):
    """One memory surfaced through ``ProjectState``, projected for the Project Overview page.

    A verbatim projection of a
    :class:`~obsidian.ontology.retrieval_models.StateRefTrace` — never
    synthesized or paraphrased text. ``memory_type`` is deliberately not
    included: every list this appears in on
    :class:`ProjectOverview` already names its own category (active tasks,
    blockers, ...), so the type is implied by placement, not repeated here.
    """

    id: str
    fact: str
    confidence: float
    importance: float
    valid_from: datetime


class NextActionSummary(BaseModel):
    """A deterministic priority pick over ``ProjectState``'s own tracked fields.

    Not a synthesized recommendation — ``item`` is a real, existing memory
    rendered verbatim, exactly like every other ``ProjectState`` field.
    ``reason`` names which category won a fixed priority order (blockers >
    active tasks > open questions > current objective — see
    ``obsidian.server.dashboard._recommended_next_action`` for why), so the
    dashboard can label *why* this item was surfaced rather than presenting
    it as an opaque suggestion.
    """

    reason: Literal["blocker", "active_task", "open_question", "current_objective"]
    item: ProjectStateItem


class ProjectOverview(BaseModel):
    """Mission-control snapshot of "what's happening in this project."

    Built entirely from :class:`~obsidian.memory_engine.project_state.ProjectState`
    (Phase A) plus memories already loaded for the rest of the dashboard
    response — see ``obsidian.server.dashboard._project_overview``. No new
    retrieval, ranking, or LLM inference of any kind: every field here is
    either a verbatim ``StateRef`` projection, a plain sort/filter over
    already-loaded ``KnowledgeObject``s, or a fixed, documented priority
    rule — never a generated summary.

    ``gaps`` and ``field_coverage`` are copied verbatim from ``ProjectState``
    (the latter renamed from that object's own ``confidence`` field to avoid
    it being read as a per-fact certainty score, which is what ``confidence``
    means everywhere else in this API — see
    ``docs/architecture/PROJECT_STATE_EVALUATION.md`` §3/§6). A name in
    ``gaps`` means this run's reconstruction didn't surface anything for that
    category — not that the vault has no such fact at all (see
    :mod:`obsidian.memory_engine.project_state`'s module docstring).
    """

    current_objective: Optional[ProjectStateItem] = None
    current_milestone: Optional[ProjectStateItem] = None
    current_focus: Optional[str] = None
    active_tasks: List[ProjectStateItem] = Field(default_factory=list)
    active_blockers: List[ProjectStateItem] = Field(default_factory=list)
    open_questions: List[ProjectStateItem] = Field(default_factory=list)
    recent_decisions: List[ProjectStateItem] = Field(default_factory=list)
    recommended_next_action: Optional[NextActionSummary] = None
    gaps: List[str] = Field(default_factory=list)
    field_coverage: float = 0.0
    generated_at: Optional[datetime] = None


class DomainSection(BaseModel):
    """One :class:`~obsidian.core.enums.MemoryDomain`'s memories, grouped by type.

    Replaces the old fixed 5-field split (``projects``/``decisions``/
    ``beliefs``/``preferences``/``tasks``) with a scalable, two-level
    grouping: a domain (``"personal"``/``"work"``/``"knowledge"``), each
    broken into ``by_type`` — every :class:`~obsidian.core.enums.MemoryType`
    resolving to this domain (see
    :func:`obsidian.core.memory_domain.resolve_domain`) that has at least
    one memory. A type with zero memories is omitted from ``by_type``
    entirely rather than included as an empty list, so the dashboard only
    renders sub-sections that have something to show.
    """

    domain: str
    by_type: Dict[str, List[DashboardMemory]]


class DashboardResponse(BaseModel):
    domains: List[DomainSection]
    recent_memories: List[DashboardMemory]
    vault_stats: VaultStats
    concept_stats: ConceptStats
    retrieval_stats: RetrievalStats
    # None when Working Context assembly is unavailable or fails for any
    # reason (see obsidian.server.dashboard._working_context_summaries) —
    # the dashboard UI falls back to its pre-existing layout in that case.
    # Otherwise never empty: MemoryEngine.query_working_context always
    # returns at least the catch-all GENERAL context.
    working_contexts: Optional[List[WorkingContextSummary]] = None
    # None when ProjectState assembly is unavailable or fails for any reason
    # (see obsidian.server.dashboard._project_overview) — the Project
    # Overview page falls back to an "unavailable" state in that case, the
    # same fail-open contract working_contexts already uses. Otherwise
    # always present, though every field inside it may be empty/None for a
    # cold vault (reflected honestly in its own gaps list, never hidden).
    project_overview: Optional[ProjectOverview] = None


class OntologyConceptDetail(BaseModel):
    """A ``Concept`` attached to a memory, projected for the Memory Inspector.

    A direct field-for-field projection of
    :class:`~obsidian.ontology.models.Concept` — ``aliases`` is that
    Concept's own ``aliases`` tuple, not a separate ``AliasIndex`` lookup
    (``AliasIndex`` only resolves text -> concept id, never the reverse).
    """

    id: str
    label: str
    aliases: List[str]
    description: str


class OntologyRelationshipDetail(BaseModel):
    """A ``Relationship`` involving one of a memory's attached concepts.

    A direct field-for-field projection of
    :class:`~obsidian.ontology.models.Relationship`, plus ``source_label``/
    ``target_label`` resolved via ``ConceptGraph.get_concept`` so the
    dashboard doesn't need a second round trip to render human-readable
    edges.
    """

    id: str
    source_id: str
    source_label: str
    target_id: str
    target_label: str
    relationship_type: OntologyRelationshipType
    confidence: float


class TopicSummary(BaseModel):
    """One canonicalized topic tag, projected for the Why? inspector.

    A direct field-for-field projection of
    :class:`~obsidian.core.value_objects.TopicTag`. ``confidence`` is
    carried here (unlike :attr:`DashboardMemory.topics`, which is names
    only) per the V2 ontology's requirement to store topic confidence
    internally even though the dashboard UI doesn't render it yet.
    """

    name: str
    confidence: float


class MemoryOntology(BaseModel):
    """Every Concept and Relationship reachable from one memory.

    ``concepts`` is every Concept the memory is attached to
    (``ConceptGraph.concepts_for_knowledge_object``); ``relationships`` is
    the union of ``ConceptGraph.relationships(concept.id)`` across those
    concepts, deduplicated by relationship id (a relationship touching two
    of the memory's own concepts would otherwise appear twice).
    """

    concepts: List[OntologyConceptDetail]
    relationships: List[OntologyRelationshipDetail]


class InspectorResponse(BaseModel):
    """Retrieval Inspector payload for one query or one memory.

    ``trace`` is always a serialised
    :class:`~obsidian.ontology.retrieval_models.RetrievalTrace` produced by
    the unmodified :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
    — never recomputed or approximated here. ``ontology`` is populated only
    by the by-memory-id inspector route (``GET
    /api/v1/dashboard/inspect/memory/{memory_id}``), since only that route
    has a concrete memory to look up concepts for; the by-query route
    leaves it ``None``.

    ``working_contexts``/``structured_prompt`` extend the Inspector past
    ``ContextBuilder`` into the rest of the deterministic pipeline
    (Slot Allocation feeding ``WorkingContextBuilder``, then
    ``StructuredPromptBuilder``). ``working_contexts`` is a list of
    serialised :class:`~obsidian.ontology.retrieval_models.WorkingContext`
    (via that class's own ``to_dict()`` — the same JSON projection it
    already exposes, not a second schema mirroring its fields) produced by
    the unmodified :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_working_context`;
    ``structured_prompt`` is the unmodified
    :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_structured`
    output. Both are ``None`` — never a validation error or 500 — when
    Working Context assembly is unavailable (an older engine missing
    ``query_working_context``) or raises for any reason, mirroring the same
    fail-open contract already used by
    ``obsidian.server.dashboard._working_context_summaries`` and
    ``obsidian.server.main.retrieve_working_context``, so the Inspector
    degrades to its pre-WorkingContext behaviour instead of breaking.
    """

    context: str
    trace: Dict[str, Any]
    source_memory_id: Optional[str] = None
    ontology: Optional[MemoryOntology] = None
    working_contexts: Optional[List[Dict[str, Any]]] = None
    structured_prompt: Optional[str] = None
    # The memory's provenance metadata (KnowledgeObject.metadata["provenance"]),
    # populated only by the by-memory-id inspector route. For a memory imported
    # from an Obsidian vault this carries source/source_file/imported_at/
    # memory_space, so the inspector can show the original source file path.
    # None for memories with no provenance (everything not imported this way)
    # and for the by-query inspector route, which has no single memory.
    provenance: Optional[Dict[str, Any]] = None
    # The Classifier's own explanation of why this memory received its
    # memory_type and topics (KnowledgeObject.metadata["classification_reason"],
    # see obsidian.manager_ai.knowledge_updater._apply_new). None when the
    # memory predates this field, or for the by-query inspector route.
    classification_reason: Optional[str] = None
    # The memory's MemoryDomain (obsidian.core.memory_domain.resolve_domain),
    # populated only by the by-memory-id inspector route.
    domain: Optional[MemoryDomain] = None
    # The memory's canonicalized topics, confidence included (unlike
    # DashboardMemory.topics) per the V2 ontology's "store confidence
    # internally, UI doesn't need to render it yet" requirement.
    topics: List[TopicSummary] = Field(default_factory=list)


class WriteTraceSummary(BaseModel):
    """One row of the Write Inspector's trace list.

    A thin projection of a persisted
    :class:`~obsidian.ontology.write_trace_models.WriteTrace` -- every
    field is copied, never recomputed, from the trace's own data. See
    ``GET /api/v1/dashboard/write-traces``.
    """

    trace_id: str
    conversation_id: Optional[str] = None
    mode: str
    status: str
    fact_count: int
    knowledge_object_count: int
    total_duration_ms: Optional[float] = None
    created_at: datetime


class WriteTraceListResponse(BaseModel):
    """List payload for ``GET /api/v1/dashboard/write-traces``."""

    traces: List[WriteTraceSummary]


class WriteTraceDetailResponse(BaseModel):
    """Full-detail payload for ``GET /api/v1/dashboard/write-traces/{trace_id}``.

    ``trace`` is always a serialised
    :class:`~obsidian.ontology.write_trace_models.WriteTrace` (via that
    class's own ``to_dict()``) -- the same loose-``Dict[str, Any]`` idiom
    :class:`InspectorResponse.trace` already uses, since Pydantic does not
    need to re-validate a shape the dataclass itself already owns.
    """

    trace: Dict[str, Any]


# ---------------------------------------------------------------------------
# Obsidian vault import
#
# The import is only a Markdown -> Conversation adapter feeding the existing
# preview -> commit pipeline (see obsidian.integrations.obsidian.importer and
# the /import/obsidian/scan route in obsidian.server.main). The scan step is
# pure filesystem + checkpoint hashing -- it never invokes the LLM; the
# review/commit phases reuse the unchanged /memory/preview and /memory/commit
# routes, one call per changed note.
# ---------------------------------------------------------------------------


class ImportScanRequest(BaseModel):
    """Request body for ``POST /api/v1/import/obsidian/scan``.

    ``root`` is the vault folder to walk; when omitted, the currently active
    Memory Space's root is used. No LLM runs during a scan.
    """

    root: Optional[str] = None


class ImportPreviewRequest(BaseModel):
    """Request body for ``POST /api/v1/import/obsidian/preview``.

    ``source_file`` is one note's vault-relative path (a ``source_file`` from
    a prior scan); ``root`` resolves it the same way the scan did (defaults to
    the active Memory Space root). The route reads that note server-side and
    runs it through the unchanged ``/memory/preview`` logic, returning a
    normal :class:`PreviewMemoryResponse` -- the note content never has to
    round-trip through the browser (which cannot read the local disk).
    """

    source_file: str = Field(..., min_length=1)
    root: Optional[str] = None


class ImportScanNote(BaseModel):
    """One note's scan result.

    ``source_file`` is the note's vault-relative path (also its
    ``external_key``). ``status`` is ``"skipped"`` when an existing checkpoint
    already covers this note's exact current content (nothing to do), or
    ``"needs_review"`` when it is new or changed since last import.
    """

    source_file: str
    status: Literal["skipped", "needs_review"]


class ImportScanResponse(BaseModel):
    """Result of an Obsidian vault scan.

    ``scanned`` is the total note count; ``skipped`` and ``changed`` partition
    it. ``review_mode`` is ``"grouped"`` when ``changed > 20`` (the dashboard
    groups review items by source file) or ``"flat"`` otherwise. ``root`` is
    the absolute folder that was actually walked. No memory has been created
    yet -- a scan writes nothing.
    """

    root: str
    scanned: int
    skipped: int
    changed: int
    review_mode: Literal["flat", "grouped"]
    notes: List[ImportScanNote] = Field(default_factory=list)
