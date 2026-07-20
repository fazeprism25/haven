# Haven Architecture

Haven is a deterministic long-term memory system: a **write pipeline** (Manager
AI) turns raw conversations into canonical knowledge, an **ontology** layer
indexes that knowledge into a concept graph, and a **read pipeline** (Memory
Engine) turns a query into an LLM-ready context string. A local FastAPI
server, a diagnostics dashboard, and a browser extension sit on top of all
three. Every stage is plain, testable Python — no stage's correctness depends
on an LLM behaving a particular way at runtime (see Decision 002 in
[DECISIONS.md](DECISIONS.md)).

```
Conversation
    │
    ▼
Manager AI (write pipeline)  ──▶  KnowledgeObject  ──▶  VaultWriter (Markdown on disk)
    │                                    │
    │                                    ▼
    │                          OntologyPipeline ──▶ ConceptGraph + concept Markdown files
    ▼
Memory Engine (read pipeline)  ◀── query ── FastAPI server ◀── browser extension / dashboard
    │
    ▼
context string (or structured prompt) ──▶ downstream LLM
```

## Write pipeline (Manager AI)

Orchestrated by `ManagerPipeline` (`obsidian/manager_ai/pipeline.py`):

```
Conversation Importer → Extractor → Classifier → ImportanceScorer → CanonicalMatcher → KnowledgeUpdater → VaultWriter
```

1. **Conversation Importer** (`obsidian/integrations/`) normalises a
   conversation from a source into a `Conversation` object. Two live paths
   feed this today: the browser extension calls `POST /memory` directly
   with the captured ChatGPT compose-box exchange (no separate importer
   module — see "Browser extension flow" below), and
   `obsidian/integrations/obsidian/importer.py` bulk-imports an existing
   Obsidian vault's own notes (`POST /import/obsidian/scan` and
   `/preview` — see `obsidian/server/README.md`). An earlier
   file-export-based ChatGPT/`obsidian/importers/` importer was removed as
   orphaned dead code (nothing called it; superseded by the two paths
   above). `integrations/claude/` and `integrations/gemini/` are empty
   package stubs today.
2. **Extractor** analyses each event and extracts atomic facts as
   `ExtractedFact` (text, source event id, evidence string, confidence).
3. **Classifier** assigns a `MemoryType` (see [MEMORY_TYPES.md](MEMORY_TYPES.md))
   with a confidence score and a reason.
4. **ImportanceScorer** scores each classified fact (see [IMPORTANCE.md](IMPORTANCE.md)).
5. **CanonicalMatcher** compares the fact against existing knowledge and
   returns a `KnowledgeDecision`: `NEW`, `CONFIRM`, `UPDATE`, or `SUPERSEDE`.
6. **KnowledgeUpdater** applies the decision to produce/modify a
   `KnowledgeObject`:
   - **NEW** — creates a `KnowledgeObject`, seeds its evidence chain, sets
     `valid_from`.
   - **CONFIRM** — increments `confirmation_count`, updates
     `last_confirmed`, appends an `EvidenceEntry`, nudges `confidence` up
     (clamped to 1.0).
   - **UPDATE** — preserves `id`, replaces `canonical_fact`, appends
     evidence, updates `confidence`/`last_confirmed`/`confirmation_count`.
   - **SUPERSEDE** — archives the old object (`valid_until` set) and
     creates a new one, cross-linked via `metadata["supersedes"]` /
     `metadata["superseded_by"]`.

   All four operations are implemented in `KnowledgeUpdater`. `ManagerPipeline
   .match_and_apply()`, the orchestrator that automatically runs
   Extractor→...→KnowledgeUpdater end-to-end, now drives `NEW`, `CONFIRM`,
   **and** `UPDATE` — the latter via `CanonicalMatcher`'s conservative,
   deterministic "whole-word prefix extension" rule. **What's not yet wired
   up:** `SUPERSEDE` — `CanonicalMatcher` never returns it, and reaching it
   for a real conversation still requires calling `KnowledgeUpdater`
   directly — which is exactly what Decision Memory's
   `KnowledgeUpdater.supersede_decision()` does (see
   [DECISION_MEMORY.md](DECISION_MEMORY.md)).
7. **VaultWriter** (`obsidian/memory_engine/vault_writer.py`) persists the
   `KnowledgeObject` as a Markdown file with YAML frontmatter.

Immediately after `VaultWriter`, `OntologyPipeline`
(`obsidian/ontology/ontology_pipeline.py`) runs the same `KnowledgeObject`
through the ontology write path:

```
KnowledgeObject → OntologyManager (propose) → OntologyValidator (accept/reject) → ConceptGraph (mutate) → ConceptWriter (persist concept Markdown)
```

`OntologyPipeline` is the only component that mutates `ConceptGraph`; this
keeps a single, deterministic writer for the in-memory graph shared with the
read pipeline.

## Read pipeline (Memory Engine)

Orchestrated by `MemoryEngine.query()` / `query_with_trace()`
(`obsidian/memory_engine/engine.py`):

```
raw query
    │  (ContextPlanner classifies task_mode + category requirements — Phase 1.5,
    │   runs first, before rewriting; see "Context Planner" below)
    │  (optional: QueryRewriter → multi-query expansion)
    ▼
HybridCandidateRetriever   (keyword path + ontology/concept-activation path, merged)
    │
    ▼
DeterministicRanker        (final_score + score_breakdown per candidate)
    │
    ├──────────────────────────────────┬─────────────────────────────────────────┐
    ▼                                   ▼
CategoryPreferenceScorer            (no category-preference step — WorkingContext
(bonus for plan-requested            path stays planner-agnostic for scoring;
categories; ContextBuilder           see "Category-Aware Retrieval" below)
path only — Phase 3)
    │                                   │
    ▼                                   ▼
AcceptanceStage                     AcceptanceStage
(one global pass — abstention /     (topic-diversified — one pass per anchor
score-gap cut / relative floor /    concept, plus a ContextPlan-driven category
hard cap)                           fallback for CONTINUATION queries)
    │                                   │
    ▼                                   ▼
DeterministicSlotAllocator          DeterministicSlotAllocator
(context-budget cap; rarely         (same class/instance, invoked via
binds after AcceptanceStage)        `_accept_and_allocate`)
    │                                   │
    ▼                                   ▼
ContextBuilder                      WorkingContextBuilder
(flat text — legacy)                (groups allocated candidates by anchor concept)
    │                                   │
    ▼                                   ▼
context string                      StructuredPromptBuilder
(POST /retrieve_context)            (+ ProjectState, for CONTINUATION-mode queries)
                                         │
                                         ▼
                                   <HavenContext> XML prompt
                                   (POST /retrieve_working_context — live: what the
                                    extension's Use Haven button inserts, and what
                                    the dashboard's Working Context preview shows)
```

Both branches share one retrieval → rank prefix (`MemoryEngine._run_retrieval`);
the fork happens immediately after `DeterministicRanker`, not just at the
renderer. `query_with_trace()` (backing `POST /retrieve_context`) applies
`CategoryPreferenceScorer` then a single global `AcceptanceStage` pass;
`query_working_context()`/`query_structured()` (backing
`POST /retrieve_working_context`) skip category preference entirely and instead
run `AcceptanceStage` once per topic via `_accept_and_allocate` (see
"Category-Aware Retrieval" below for why the two paths diverge here).
`query_with_trace()` also runs `CoverageAnalyzer` and `GapRecoveryDecision`
after allocation — observational only, attached to `RetrievalTrace`, never read
back into ranking/acceptance/allocation (see "Coverage Analysis" and "Gap
Recovery Decision" below). `POST /retrieve_context` is otherwise unaffected by
anything in the right-hand branch.

- **HybridCandidateRetriever** resolves query text to seed Concepts via
  `AliasIndex`, spreads activation across `ConceptGraph` with
  `ActivationSpreader`, and separately matches on keyword overlap. Both
  paths return the same `Candidate` type; a `KnowledgeObject` found by both
  paths keeps its ontology evidence.
- **DeterministicRanker** scores every candidate (activation, attachment
  relevance, keyword overlap, importance, confidence, recency, confirmation
  count) with no filtering.
- **AcceptanceStage** decides which prefix of the ranked list is trustworthy
  enough to return — including returning nothing at all for a low-confidence
  match. Design rationale and tuned constants are in
  [`docs/architecture/ACCEPTANCE_STAGE_DESIGN.md`](../../docs/architecture/ACCEPTANCE_STAGE_DESIGN.md).
- **ContextBuilder** renders accepted candidates into the flat
  `[N] fact\n    type: ... | confidence: ...` string that backs
  `MemoryEngine.query()`, the benchmark harness (when present), and today's
  `POST /retrieve_context` response.

A second, now-live rendering path assembles the same allocated candidates
into a structured prompt instead of a flat string:

- **WorkingContextBuilder** (`obsidian/memory_engine/working_context_builder.py`)
  groups them by anchor concept into one or more `WorkingContext` objects —
  the goal, recent decisions, pending tasks, and open questions per topic
  (`MemoryEngine.query_working_context()`).
- **StructuredPromptBuilder** (`obsidian/memory_engine/structured_prompt_builder.py`)
  renders those `WorkingContext` objects into the `<HavenContext>` XML block
  (`MemoryEngine.query_structured()`) — a `<Guidance>` preamble, an optional
  `<ProjectState>` (see "Project State" below), then one `<WorkingContext>`
  per topic.

Both are wired end to end into `POST /retrieve_working_context`, which is
what the browser extension's **Use Haven** button and the dashboard's
Working Context preview actually call — this is live product behavior, not
a design exercise awaiting integration. `POST /retrieve_context` is
unaffected either way: it still only calls `ContextBuilder`, unchanged. See
[`obsidian/server/README.md`](../server/README.md#prompt-assembly) for the
full renderer output shape and design rationale.

`query_with_trace()` returns the same context string alongside a
`RetrievalTrace` describing every candidate considered (accepted or
rejected) — this is what powers the Retrieval Inspector in the dashboard.

### Context Planner (Phase 1.5 — observational only)

`ContextPlanner` (`obsidian/memory_engine/context_planner.py`) classifies a
query into a `ContextPlan` — a task mode (pointed Q&A, coding/debugging,
structuring, research, continuation) plus the named context categories
(decisions, tasks, constraints, blockers, ...) that task mode typically
needs. `query_with_trace()` runs it once per call, before rewriting or
retrieval begins, and attaches the result to `RetrievalTrace.context_plan`
(task mode, planning method, scope, confidence, category requirements).

As of this phase, `ContextPlan` is **purely observational** for retrieval
(`HybridCandidateRetriever`) and everything downstream of ranking except one
narrow, Phase 3 exception — see "Category-Aware Retrieval (Phase 3)" below,
which is the one place `context_plan.requirements` is actually read back.
`WorkingContext`/prompt construction (`WorkingContextBuilder`,
`StructuredPromptBuilder`) remain fully unaffected, as does retrieval itself
— see [`docs/architecture/CONTEXT_PLAN_OBJECT.md`](../../docs/architecture/CONTEXT_PLAN_OBJECT.md)
§8 for the larger, still not-yet-implemented per-category retrieval design
that section describes.

### Category-Aware Retrieval (Phase 3 — behavior-changing)

`CategoryPreferenceScorer` (`obsidian/memory_engine/category_preference.py`)
is the first phase where `ContextPlan` actually changes what retrieval
returns. `query_with_trace()` runs it once per call, right after
`DeterministicRanker` scores every candidate and before `AcceptanceStage`
runs: any candidate whose `MemoryType` resolves (via the same
`MEMORY_TYPE_CATEGORY` table `CoverageAnalyzer` uses) to a category present
in `context_plan.requirements` gets a small, fixed, deterministic score
bonus (`CATEGORY_PREFERENCE_BONUS`, 0.05) added to its `final_score`, before
`AcceptanceStage`'s absolute floor, abstention check, score-gap cut, and
relative floor all run against the adjusted scores.

This is a **soft preference, not a hard filter**: no candidate is ever
dropped, reclassified, or made ineligible because of the plan — the bonus
only ever reorders candidates that were already eligible, closing small
score gaps (or nudging a borderline candidate over an acceptance threshold)
without ever overriding a real evidence-based score difference. An
unrequested-category candidate that scores meaningfully higher on its own
evidence still wins. Every `CandidateTrace` on the returned trace now
exposes `base_score` (the pre-bonus composite score), `category_preference_bonus`,
and `final_score` (post-bonus, the value actually used for acceptance and
ranking), so this influence is fully inspectable per candidate. The
`TaskMode.POINTED_QA` sentinel (empty `requirements`) applies a zero bonus
to every candidate, so retrieval for that common case is unchanged from
before this phase. See that module's own docstring for the full design
rationale, expected benefits, failure modes, and interaction with a future
gap-recovery retry.

### Coverage Analysis (Phase 2 — observational only)

`analyze_coverage()` (`obsidian/memory_engine/coverage_analyzer.py`) compares
a `ContextPlan`'s category requirements against the `CandidateTrace` entries
a retrieval run actually accepted, and produces a `CoverageReport`: per
requested category, how many accepted candidates counted toward it, whether
that met the requirement's minimum, and an overall FULL/PARTIAL/MISSING
status. It also rolls up an overall coverage percentage (over `REQUIRED`
categories only), the list of unsatisfied `REQUIRED` categories, and a
`fully_satisfied` boolean. `query_with_trace()` runs it once per call, after
acceptance and slot allocation finish, and attaches the result to
`RetrievalTrace.coverage`.

Category membership is resolved from each candidate's `MemoryType` through a
fixed, deliberately partial table. `CONSTRAINT`, `BLOCKER`, `OPEN_QUESTION`,
`IMPLEMENTATION_STATE`, and `CODE_AREA` all have a corresponding `MemoryType`
(`RULE` for `CONSTRAINT`; `BLOCKER`, `OPEN_QUESTION`, `IMPLEMENTATION_STATE`,
and `CODE_AREA` were added as new `MemoryType` members for this purpose — see
[`docs/architecture/PROJECT_STATE_KNOWLEDGE_MODEL.md`](../../docs/architecture/PROJECT_STATE_KNOWLEDGE_MODEL.md)),
so a requirement for one of those categories can now be marked satisfied —
nothing in the write pipeline populates these types from real conversations
yet (the Extractor's prompt doesn't ask for this content), so in practice
they still won't appear until that changes. `GOAL`, `PROJECT`, `PERSON`,
`EVENT`, `SKILL`, and `PREFERENCE` remain unmapped — no `ContextCategory`
corresponds to them at all.

As of this phase, `CoverageReport` is **purely observational**: nothing reads
it back. Retrieval, ranking, acceptance, allocation, and prompt construction
are all unaffected — `query()` and `query_with_trace()`'s returned context
string are byte-identical to before this integration. The report is visible
only through the trace, for the Retrieval Inspector. A future phase may use
an unsatisfied `REQUIRED` category to trigger a bounded gap-recovery retry of
retrieval; see
[`docs/architecture/CONTEXT_PLAN_OBJECT.md`](../../docs/architecture/CONTEXT_PLAN_OBJECT.md)
§5 for that larger, not-yet-implemented design.

### Gap Recovery Decision (Phase 4 — observational)

`decide_gap_recovery()` (`obsidian/memory_engine/gap_recovery.py`) answers
exactly one question — "should Haven attempt another retrieval?" — as a
`GapRecoveryDecision`, derived *only* from a `ContextPlan` and the
`CoverageReport` `CoverageAnalyzer` just produced for the same query. It has
no access to the retriever, ranker, any LLM, or the ontology; it cannot
retrieve, rank, or resolve anything itself. `query_with_trace()` runs it once
per call, immediately after coverage analysis finishes, and attaches the
result to `RetrievalTrace.gap_recovery` (`should_retry`, `missing_categories`,
`retry_budget`, `retry_reason`, `confidence`, `recovery_strategy`).

The decision defaults to **no retry** and only recommends one under a narrow,
deterministic rule: `should_retry` is `True` exactly when
`CoverageReport.missing_required_categories` is non-empty (a real,
unsatisfied `REQUIRED` category exists) *and* the originating `ContextPlan`'s
own classification `confidence` is at or above a fixed threshold
(`MIN_PLAN_CONFIDENCE_FOR_RETRY`, 0.5) — a low-confidence plan's requirements
aren't trusted enough to retry against, since the categories a retry would
target might themselves be wrong. Every `ContextPlan` `ContextPlanner`
produces today has `confidence=1.0` (Phase 1 is deterministic-only), so the
low-confidence path is reserved for a future LLM-fallback planning phase, not
reachable against real traffic yet. When no `REQUIRED` category is missing —
including trivially for the `TaskMode.POINTED_QA` sentinel, whose
`CoverageReport` is always empty — the decision is `should_retry=False` with
`retry_reason=NO_GAP` and an empty `missing_categories`.

**No retrieval retries exist yet.** `GapRecoveryDecision` is a recommendation
attached only to the trace; nothing in the pipeline reads it back. Even a
`should_retry=True` decision issues no second retrieval pass, changes no
acceptance or allocation outcome, and does not alter `WorkingContext`,
`ContextBuilder`, or `StructuredPromptBuilder` output — `query()` and
`query_with_trace()`'s returned context string remain byte-identical to
before this integration, exactly as Coverage Analysis (Phase 2) established.
This phase is infrastructure only: it establishes the `ContextPlan` +
`CoverageReport` → decision shape a future phase can act on, without yet
building the retry loop, planner changes, or gap-filling logic that would
consume it. See
[`docs/architecture/CONTEXT_PLAN_OBJECT.md`](../../docs/architecture/CONTEXT_PLAN_OBJECT.md)
§5 for that larger, not-yet-implemented design.

### Project State (Phase A — observational only)

`ProjectStateBuilder` (`obsidian/memory_engine/project_state.py`) derives a
`ProjectState` — a deterministic, category-bucketed snapshot of "what this
run's accepted candidates say about where the project currently stands" —
from `allocated`, the exact same slot-allocated candidate list
`ContextBuilder` already renders into the returned context string.
`query_with_trace()` runs it once per call, immediately after slot
allocation finishes, and attaches the result to `RetrievalTrace.project_state`
(`current_objective`, `decisions`, `superseded_decisions`, `active_tasks`,
`blockers`, `constraints`, `implementation_state`, `code_areas`,
`open_questions`, `gaps`, `confidence`, `generated_at`).

**This is Phase A of a five-phase design**
([`docs/architecture/PROJECT_STATE_DESIGN.md`](../../docs/architecture/PROJECT_STATE_DESIGN.md))
and implements only the first phase, narrower even than that document's own
Phase A description: `ProjectState` is recomputed entirely from **one
query's** already-accepted candidates, not a full-vault aggregation. There
is no persistence (no JSON sidecar, no `ProjectStateStore`), no incremental
materialization (no `ManagerPipeline` write-time hook, no `version`/
`last_incorporated_event_id` watermark), and no inference (no LLM-synthesized
`identity`/`phase`, no tie-break judgment among competing goals —
`current_objective` is always the single highest-ranked accepted `GOAL`
candidate, deterministically). Every field is either a pure bookkeeping
projection or a verbatim pointer (`StateRef`) to an accepted
`KnowledgeObject` — see that module's own docstring for the full scope and
rationale, including why `rejected_approaches`, `do_not_do`,
`recent_discoveries`, `identity`, and `phase` are omitted entirely rather
than included as permanently-empty fields.

Category membership reuses `CoverageAnalyzer`'s `MEMORY_TYPE_CATEGORY` table
verbatim (the same single source of truth `CategoryPreferenceScorer`
already reuses), except for `current_objective`, which is sourced directly
from `MemoryType.GOAL` since `GOAL` has no `ContextCategory` entry at all.
`decisions`/`superseded_decisions` are split deterministically from each
accepted `DECISION` candidate's `DecisionMetadata.status`, when present.
`gaps` lists which of the 8 tracked fields are empty *for this run* — this
is a strictly weaker signal than a full-vault or incrementally-materialized
`gaps` would be (there is no persisted prior state for anything to drift
from), and `confidence` is a deterministic completeness fraction over those
same 8 fields, not a per-fact confidence.

As of this phase, `ProjectState` is **purely observational and read-only**:
it is built strictly *after* `ContextBuilder` has already rendered `context`
from the same `allocated` list, so it structurally cannot influence
retrieval, ranking, acceptance, allocation, `WorkingContext`, or any
rendered prompt — `query()` and `query_with_trace()`'s returned context
string are byte-identical to before this integration. Nothing is written to
disk. See
[`docs/architecture/PROJECT_STATE_DESIGN.md`](../../docs/architecture/PROJECT_STATE_DESIGN.md)
§10 for Phases B (incremental materialization), C (`WorkingContext`
wiring), D (inferred fields), and E (hardening) — none of which are
implemented yet.

### ProjectState × WorkingContext integration — Step 1 (complete, incl. rendering)

[`docs/architecture/PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md`](../../docs/architecture/PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md)
§7's Step 1 is fully implemented, in two parts landed on the same day:
plumbing (making `ProjectState` reachable from `query_structured()`), then
the `<ProjectState>` XML renderer this section used to describe as
deferred.

1. **One retrieval prefix, not two.** `MemoryEngine._run_retrieval` is now
   the single implementation of rewrite → retrieve → merge →
   validity-filter → rank (including the `ContextPlanner` call), returned as
   a private `_RetrievalPrefix`. `query_with_trace()` and `_allocate()`
   (used by `query_working_context()` and, transitively,
   `query_structured()`) both call it instead of each independently
   re-implementing the same seed-construction/stage-wiring logic — the
   duplication `PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md` §0 identified
   as the thing actually blocking `ProjectState` from reaching
   `query_structured()` (there was no single place to obtain both
   `allocated` and a `ContextPlan`). `_allocate()` still deliberately skips
   `CategoryPreferenceScorer` — see `_RetrievalPrefix`'s docstring — so
   ranking/acceptance/allocation output for `query_working_context()` and
   `query_structured()` is unchanged from before this refactor.
2. **`ProjectState` is now available to, and rendered by,
   `query_structured()`.** When a query's `ContextPlan.task_mode` is
   `TaskMode.CONTINUATION`, `query_structured()` builds a `ProjectState`
   from the same `allocated` list (via `ProjectStateBuilder.build`, exactly
   as `query_with_trace()` already does) and passes it to
   `StructuredPromptBuilder.render()`'s optional `project_state` parameter
   (default `None`). Every other task mode — including `POINTED_QA`, the
   common case — leaves it `None`, and `render()` renders no
   `<ProjectState>` element at all in that case: the prompt string stays
   byte-identical to before this integration for every non-`CONTINUATION`
   query.

   When `project_state` is supplied, `render()` now emits one
   `<ProjectState confidence="0.NN">` element — the first child of
   `<HavenContext>`, immediately after `<Guidance>` and before any
   `<WorkingContext>` element (the orientation layer leading, the
   per-concept deep-dive following, per
   [`WORKING_CONTEXT_2_DESIGN.md`](../../docs/architecture/WORKING_CONTEXT_2_DESIGN.md)
   §1/§4's human-reconstruction ordering). Its children —
   `<CurrentObjective>`, `<Decisions>`, `<SupersededDecisions>`,
   `<ActiveTasks>`, `<Blockers>`, `<Constraints>`, `<ImplementationState>`,
   `<CodeAreas>`, `<OpenQuestions>` — each render as `[N] <fact>` item
   references reusing the *same* `[N]` index already assigned to that
   memory in a `WorkingContext` bucket (a memory tracked by both `ProjectState`
   and a rendered `WorkingContext` therefore never gets a second, separately
   numbered copy). A field that is empty is omitted entirely, not
   self-closed — `<Gaps>` is the single authoritative "this was empty"
   signal, always rendered, self-closing to `<Gaps/>` only when nothing was
   missing. See
   [`docs/architecture/PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md`](../../docs/architecture/PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md)'s
   "Step 1's deferred renderer" note for the full rationale, including why
   this departs slightly from §3.3's own illustrative example.

   `WorkingContextBuilder.from_project_state` and `ContextKind.PROJECT`
   activation (§3.4/§5) remain **not built** — this step renders
   `ProjectState` directly, independent of any `WorkingContext`, rather than
   routing its content through one. §7's own Step 2 (the freshness-check +
   bounded gap-fill fallback so a `CONTINUATION` query stops paying full
   retrieval cost) is also still unimplemented — every `CONTINUATION` query
   still runs the complete `_allocate` pipeline every call.

### Prompt Continuation Quick Wins (2026-07-10)

[`docs/architecture/PROMPT_CONTINUATION_EVALUATION.md`](../../docs/architecture/PROMPT_CONTINUATION_EVALUATION.md)
evaluated the rendered prompt end to end and named four Quick Win candidates;
two shipped, two were rejected (see that document's "Implementation status"
section for the full reasoning):

1. **`<Guidance>` now explains the `[N]` convention.** One additional bullet
   in `StructuredPromptBuilder`'s `_GUIDANCE_LINES` states that the same
   `[N]` denotes the same underlying memory everywhere it appears in the
   document — previously left for a model to infer from noticing the
   numbers repeat.
2. **`TOPIC`-kind `WorkingContext` titles resolve to `Concept.label`.**
   `MemoryEngine._resolve_topic_titles` replaces a `TOPIC` context's
   `str(anchor_concept_id)` title with that concept's label via one
   read-only `ConceptGraph.get_concept` lookup per context — no retrieval,
   no new candidate/ranking pass. Applied identically inside
   `query_working_context()` and `query_structured()` so the two never
   diverge. Deliberately implemented in `MemoryEngine` rather than by
   threading `ConceptGraph` into `WorkingContextBuilder`, which stays exactly
   as isolated from `ConceptGraph` as its own docstring and
   `TestNoOutOfScopeImports` require. A context whose anchor concept is
   absent from the graph keeps its UUID title, unchanged from before.
   `GENERAL` contexts (already `"General"`) and the still-unbuilt `PROJECT`
   kind are untouched.
3. **Rejected: an explicit "as of" timestamp.** `ProjectState.generated_at`
   stays unrendered — `PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md` already
   documents this as a deliberate judgment call ("a diagnostic timestamp
   with no orientation value to a downstream model"), not an oversight, so
   this pass left it in place rather than reversing a documented decision
   without its own scoped review.
4. **Rejected: making `<ProjectState>` a deterministic floor.** Doing so
   requires no longer gating it on `TaskMode.CONTINUATION`, which
   unavoidably changes `POINTED_QA`'s rendered output (verified
   byte-identical to omitting `project_state` today). Left as future work,
   same as `PROMPT_CONTINUATION_EVALUATION.md` §10's Long-Term Ideas #1
   already scoped it.

## Ontology / Concept Graph

The ontology indexes `KnowledgeObject`s by what they're *about*, without
becoming the source of truth (`KnowledgeObject` remains that). Full design:
[`docs/architecture/ONTOLOGY_SPEC.md`](../../docs/architecture/ONTOLOGY_SPEC.md).

- **Concept** — a stable semantic entity (e.g. "Haven", "Claude").
- **Relationship** — a typed, directed edge between two Concepts. Allowed
  types (`obsidian.ontology.enums.OntologyRelationshipType`): `IS_A`,
  `PART_OF`, `USES`, `DEPENDS_ON`, `CREATED_BY`, `LOCATED_IN`,
  `RELATED_TO`, `SUPPORTS`. (See the note in
  [RELATIONSHIPS.md](RELATIONSHIPS.md) about an older, unimplemented type
  list that used to live in this doc.)
- **Attachment** — evidence linking a `KnowledgeObject` to one or more
  Concepts.
- **OntologyProposal** / **OntologyValidator** — proposals (create
  concept/relationship, attach knowledge object) are generated by
  `OntologyManager` and must pass `OntologyValidator` (duplicate detection,
  alias resolution, deterministic IDs) before they can mutate the graph.
- **ActivationSpreader** — propagates activation from seed Concepts to
  neighbors across `Relationship` edges, decaying with distance, for
  concept-aware retrieval.

All of the above is implemented and covered by tests (`obsidian/tests/test_concept_graph.py`,
`test_activation_spreader.py`, `test_ontology_models.py`,
`test_concept_parser.py`, `test_concept_writer.py`, `test_alias_index.py`,
`test_query_resolver.py`, `test_candidate_assembler.py`,
`test_hybrid_candidate_retriever.py`) — this used to be future work; it now
backs every retrieval call. See [PROGRESS.md](PROGRESS.md) for the
phase-by-phase status.

## Server, dashboard, and extension

A local, single-user FastAPI server (`obsidian/server/`) wraps `MemoryEngine`
and `OntologyPipeline` for a real on-disk vault — endpoints, the diagnostics
dashboard, and the Retrieval Inspector are documented in full in
[`obsidian/server/README.md`](../server/README.md). The browser extension
(`extension/`) is the primary client: it calls `/retrieve_context` and
`/memory` from ChatGPT's compose box.

## Deployment architecture

Everything above runs unmodified whether it's started locally via `uvicorn`
(Quick Start) or as the production deployment on Alibaba Cloud — deployment
only adds process management and a reverse proxy in front of the same app,
nothing in `obsidian/` changes:

```
Browser
  │
  ▼
Alibaba Cloud ECS (Simple Application Server, Ubuntu 22.04)
  │
  ▼
nginx (reverse proxy + HTTP Basic Auth, except GET /api/v1/health)
  │
  ▼
FastAPI (obsidian.server.main:app, under systemd — haven.service, 127.0.0.1:8765 only)
  │
  ▼
Memory Engine / Manager AI / OntologyPipeline
  │
  ▼
Vault (Markdown + YAML on local disk)

LLM calls (Manager AI, Query Rewriter, benchmark judge)
  │
  ▼
Alibaba Cloud DashScope (Qwen Cloud)
```

Full provisioning steps and rationale:
[`deploy/alibaba-cloud/README.md`](../../deploy/alibaba-cloud/README.md).

## Benchmarking

`benchmarks/` at the repo root (a sibling of `obsidian/`, not inside it)
drives the same benchmark runner against either `mem0.Memory` directly or
a `HavenAdapter` that exercises Haven's real `VaultWriter` +
`OntologyPipeline` + `MemoryEngine` — no pipeline stage is bypassed or
reimplemented for benchmarking purposes. An LLM judge scores each run's
answers against per-case expectations. See
[`benchmarks/results/final_report.md`](../../benchmarks/results/final_report.md)
for the full write-up and measured mem0-vs-Haven numbers.

## Memory lifecycle

`KnowledgeObject` tracks validity with `valid_from` / `valid_until` /
`last_confirmed` rather than an explicit state machine — "archived" means
`valid_until is not None` (see the Dashboard's `archived_count` stat). This
is simpler than, and does not implement, the five-state
`NEW → ACTIVE → DORMANT → ARCHIVED → DELETED` lifecycle described in
[MEMORY_STATES.md](MEMORY_STATES.md); see that doc's note for detail.

## Key design principles

- **Canonical knowledge objects** — each piece of knowledge exists once;
  duplicates are merged via `CONFIRM`/`UPDATE`, not stored separately.
- **Provenance** — every knowledge object tracks which conversation events
  contributed to it via its evidence chain.
- **The LLM proposes, Python validates** — every LLM output is validated
  before entering the pipeline (Decision 002).
- **Determinism downstream of extraction/classification** — ranking,
  acceptance, allocation, and rendering have no LLM calls and no
  randomness, so the same vault + query always produces the same context
  (the optional `QueryRewriter` multi-query expansion is the one stage that
  calls an LLM, and fails open to single-query behavior on any error).
- **Pluggable LLM** — pipeline stages depend on a `BaseLLM` interface, not a
  specific provider.
