# ProjectState × WorkingContext — Integration Architecture

Status: **§7 Step 1 fully implemented (2026-07-09), including the
`<ProjectState>` renderer this document's own §7 originally scoped into
Step 1 but which shipped late (see "Step 1's deferred renderer" note
below). §7's actual Step 2 (freshness-check + bounded gap-fill routing)
and Steps 3–4 remain architecture only.** Grounded against source as of
2026-07-09: `project_state.py`, `working_context_builder.py`,
`context_builder.py`, `structured_prompt_builder.py`, `engine.py`,
`retrieval_models.py` (`WorkingContext`, `WorkingContextState`,
`RoleBucket`, `MemoryRole`, `ContextKind`), `PROJECT_STATE_DESIGN.md`,
`WORKING_CONTEXT_2_DESIGN.md`.

## Step 1's deferred renderer — now implemented (2026-07-09)

A naming note before the detail, because this document's own §7 and
`ARCHITECTURE.md` have described this piece two different ways: §7 below
scoped "`StructuredPromptBuilder`'s `<ProjectState>` rendering (§3.3),
defaulted off" as *part of* Step 1, but what actually shipped first
(the "Step 1 implementation note" immediately below this one) deferred it,
and `ARCHITECTURE.md` subsequently labeled that deferred remainder
"Step 2+" informally, in prose, not as a renumbering of §7's own roadmap.
**This section is that deferred remainder, now built — it is not §7's own
Step 2** (freshness-check + bounded gap-fill routing against
`ContextPlan.required_categories`, §3.4), which remains entirely
unimplemented. `WorkingContextBuilder.from_project_state` and
`ContextKind.PROJECT` activation (§3.4, §5) are also still not built. Only
the rendering half of Step 1 changed.

What shipped, exactly: §3.3 designed as written, with one resolved
ambiguity worth recording. §3.3's own example self-closes an empty
`<Blockers/>` *and* lists `blockers` under `<Gaps>` — redundant, since both
say the same thing. The shipped renderer picks one authoritative
representation of
"this tracked field is empty": `<Gaps>` alone. Every other `<ProjectState>`
child element (`<CurrentObjective>`, `<Decisions>`, `<SupersededDecisions>`,
`<ActiveTasks>`, `<Blockers>`, `<Constraints>`, `<ImplementationState>`,
`<CodeAreas>`, `<OpenQuestions>`) is **omitted entirely** when its
underlying field is empty/`None`, rather than self-closing — there is
nothing to gain from a second, per-field empty marker once `<Gaps>` already
says so. `<Gaps>` itself is the one exception per §2 point 5: always
rendered, self-closing to `<Gaps/>` only when `project_state.gaps` is
itself empty (i.e. nothing tracked was missing this run). Concretely, what
shipped in `StructuredPromptBuilder`
(`obsidian/memory_engine/structured_prompt_builder.py`):

* `_render_project_state` renders one `<ProjectState confidence="...">`
  element as the first child of `<HavenContext>`, immediately after
  `<Guidance>` and before any `<WorkingContext>` element — exactly the
  placement §3.3/§4 specify (orientation layer first, per-concept
  deep-dive below it).
* Every list-valued field (`decisions`, `superseded_decisions`,
  `active_tasks`, `blockers`, `constraints`, `implementation_state`,
  `code_areas`, `open_questions`) renders as `<Tag><Item>[N] fact</Item>
  ...</Tag>` in `ProjectState`'s own dataclass declaration order — a fixed,
  explicit `_PROJECT_STATE_LIST_SECTIONS` table, not a re-derivation of
  `_CATEGORY_TO_FIELD`. `current_objective` (single-valued) renders as
  `<CurrentObjective>[N] fact</CurrentObjective>` when present.
* Item references (`_ref_state`) reuse the exact same `[N]` index the
  renderer already assigns to `WorkingContext` bucket members
  (`_assign_indices`, unchanged) — a `StateRef` and the same memory's
  `<Memory>` element in a `WorkingContext` bucket always share one index,
  never a separately-numbered copy, satisfying §4's never-duplicate-content
  table. A `StateRef` whose `knowledge_object_id` is absent from the given
  `index` (not expected in Phase A, since `ProjectState` and the rendered
  `WorkingContext`s are always built from the same `allocated` list, but
  possible for a hand-built `ProjectState` passed independently) falls back
  to unindexed plain text, mirroring `_ref`'s own fallback discipline
  exactly.
* `superseded_decisions` — not one of the 8 fields `ProjectState.gaps`
  tracks — renders as its own `<SupersededDecisions>` section when
  non-empty, and is simply omitted (never a gap) when empty, consistent
  with it being real, deterministic `ProjectState` content the design
  doesn't ask to be hidden, just not gap-tracked.
* `generated_at` is not rendered — a diagnostic timestamp with no
  orientation value to a downstream model, consistent with §4's field
  table (it lists no row for it).
* `project_state=None` (the default, and every non-`CONTINUATION` task
  mode's actual value per Step 1) renders no `<ProjectState>` element at
  all and is confirmed byte-identical to rendering with the parameter
  omitted entirely — the same "strict floor, never a regression" property
  Step 1 already established, now re-verified with real rendering behind
  it (see `obsidian/tests/test_structured_prompt_builder.py`'s
  `TestProjectStateOmitted` and `test_engine.py`'s
  `test_pointed_qa_prompt_is_byte_identical_to_rendering_without_project_state`).
* `WorkingContextBuilder.from_project_state` (§3.4, §5, Step 3) was **not**
  built — `ContextKind.PROJECT` remains dead code with no constructor. This
  step renders `ProjectState` directly from `StateRef`s, independently of
  whatever `WorkingContext`s the same call also renders; it does not route
  `ProjectState`'s content *through* a `WorkingContext` at all. The
  freshness-check/gap-fill routing in §3.4 is likewise still unbuilt — a
  `CONTINUATION` query still runs the full `_allocate` retrieval every
  call, exactly as Step 1 left it; Step 2 is rendering-only.

## Step 1 implementation note

Step 1 (§7) shipped narrower than a literal reading of that section, in one
respect worth calling out: rather than adding a new
`WorkingContextBuilder.from_project_state` construction path, this
implementation stopped at making `ProjectState` *reachable* from
`query_structured()` at all — the more fundamental gap §0 point 1
identified (`ProjectStateBuilder` was never even instantiated on the
prompt-serving path). Concretely, what shipped:

* `MemoryEngine._run_retrieval` unifies the rewrite/retrieve/merge/
  validity-filter/rank prefix `query_with_trace()` and `_allocate()`
  (`query_working_context()`/`query_structured()`) previously each
  implemented independently, returning it as a private `_RetrievalPrefix`
  that also carries the `ContextPlan` every caller now gets access to
  without a second `ContextPlanner` call.
* `query_structured()`, when `ContextPlan.task_mode is TaskMode.CONTINUATION`,
  builds a `ProjectState` from `allocated` (the same
  `ProjectStateBuilder.build` call `query_with_trace()` already makes) and
  passes it to `StructuredPromptBuilder.render()`'s new, optional
  `project_state` parameter.
* `StructuredPromptBuilder.render()` accepts `project_state` but does not
  render it — no `<ProjectState>` element, no XML shape change. §3.3's
  `_render_project_state` design (including the `<Gaps>` invariant) is
  **not** implemented; that, `WorkingContextBuilder.from_project_state`,
  and the freshness-check/gap-fill routing in §3.4 remain Steps 2–4, not yet
  built. `ContextKind.PROJECT` is still dead — no code path constructs a
  `kind=ContextKind.PROJECT` `WorkingContext` yet.
* `_allocate()` deliberately still never runs
  `CategoryPreferenceScorer` — folding it into the shared prefix would have
  changed `query_working_context()`/`query_structured()`'s ranking output,
  which the implementation task scoped as an explicit non-goal ("Do NOT
  change ranking"). `_RetrievalPrefix` carries `ContextPlan` for callers to
  *read*; nothing feeds it into scoring on that path.

See `obsidian/docs/ARCHITECTURE.md`'s "ProjectState × WorkingContext
integration — Step 1 (complete)" section for the same summary at the
integration point, and `obsidian/memory_engine/engine.py`'s
`_RetrievalPrefix`/`_run_retrieval`/`query_structured` docstrings for the
full detail.

---

## 0. What actually exists today (not what the design docs describe)

Both prior design docs describe ambitious, multi-phase visions. Only a
narrow slice of each shipped, and the two slices that shipped don't talk to
each other at all:

| | `ProjectState` (Phase A, shipped) | `WorkingContext` (shipped) |
|---|---|---|
| Built from | one query's `allocated` list | one query's `allocated` list |
| Built by | `ProjectStateBuilder.build()` | `WorkingContextBuilder.build()` |
| Grouping vocabulary | `ContextCategory` (9 values, via `resolve_category`) | `MemoryRole` (7 values, via `resolve_role`) |
| Reachable via | `RetrievalTrace.project_state` (inside `query_with_trace` only) | `query_working_context()` / `query_structured()` (a **separate** call path with its own duplicated retrieval prefix, `_allocate`) |
| Consumed by | **nothing** | `StructuredPromptBuilder.render()` |
| Persisted | no | no |
| Plan-aware (`ContextPlan`/`CategoryPreferenceScorer`) | yes, inherits it from `query_with_trace`'s pipeline | **no** — `_allocate` is explicitly documented as "deliberately not planner-aware" |

Three consequences worth being precise about, because they change the shape
of the recommendation below:

1. **`ProjectState` is not merely "generated but never consumed" in the
   abstract — it is architecturally unreachable from the one path that
   renders a prompt.** `query_structured()` (the only method that produces
   an actual LLM-facing prompt via `StructuredPromptBuilder`) calls
   `_allocate()`, not `query_with_trace()`. `ProjectStateBuilder` is never
   even instantiated on that path. Wiring `ProjectState` into
   `WorkingContext` therefore means wiring it into `_allocate`/
   `query_working_context`/`query_structured`, not adding a read step to
   `query_with_trace`.
2. **The two objects already use two different, overlapping-but-distinct
   category taxonomies.** `ContextCategory` (`ProjectState`, `ContextPlanner`,
   `CoverageAnalyzer`) has `BLOCKER`, `CONSTRAINT`, `IMPLEMENTATION_STATE`,
   `CODE_AREA`, `OPEN_QUESTION` as first-class values. `MemoryRole`
   (`WorkingContext`) does not — `_MEMORY_TYPE_ROLE` maps `BLOCKER` onto
   `MemoryRole.TASK`, `CONSTRAINT`-shaped `RULE`s onto `MemoryRole.BELIEF`,
   and `IMPLEMENTATION_STATE`/`CODE_AREA` onto `MemoryRole.REFERENCE`
   (`retrieval_models.py:1613-1629`, comment explicitly says this was a
   deliberate choice to avoid changing `WorkingContext`'s shape). A blocker
   and a task are indistinguishable once resolved into a `WorkingContext`
   today. This is real, pre-existing information loss, not something this
   integration introduces — but any integration must not paper over it by
   quietly re-deriving a *third* category scheme.
3. **`WorkingContextBuilder.from_project_state`, as `PROJECT_STATE_DESIGN.md`
   §5 proposes it, does not type-check as written.** That section says to
   convert `ProjectState`'s `StateRef` tuples into `RoleBucket`s "reusing
   `resolve_role`/`MemoryRole` unchanged." But `resolve_role` takes a
   `KnowledgeObject` and returns a role — that part is fine, if `StateRef`
   carried a `KnowledgeObject` (it doesn't; it carries only
   `knowledge_object_id`/`canonical_fact`/`valid_from`/`confidence`/
   `importance`, deliberately, per its own docstring: *"must never embed a
   transient ranking score or an in-memory-only object"*). The real problem
   is downstream: `RoleBucket.members: Tuple[RankedCandidate, ...]` —
   `RankedCandidate` carries `final_score` and `score_breakdown`, fields a
   persisted, cross-query `StateRef` structurally cannot have (there is no
   "this run's rank" for something that was never ranked this run).
   Producing a `RoleBucket` from `StateRef`s means either fabricating a
   `RankedCandidate` with an invented score (contaminates the one field
   `RetrievalTrace`/diagnostics tooling already trust as "this query's real
   ranking signal"), or loosening `RoleBucket`'s type (a real, non-trivial
   change to a type every existing renderer depends on). §5's own reasoning
   for *why* to reuse `RoleBucket` — "every downstream consumer sees the
   identical object shape it already handles" — is the right goal; the
   specific mechanism it names doesn't achieve it for free. See §3 below for
   the resolution this document recommends.

---

## 1. Answering the ten questions

**Q1 — Should `WorkingContext` embed `ProjectState`?**
Not by literal composition (a `ProjectState` field bolted onto the
`WorkingContext` dataclass) — but yes in the sense that mattered originally:
for project-wide queries, `WorkingContext` should be *constructed from*
`ProjectState`, so `ProjectState` becomes the upstream source of truth and
`WorkingContext` remains the thing that gets rendered. This is `PROJECT_STATE_DESIGN.md`
§5's own conclusion, and it's correct — it's just the *mechanism* (§0 point 3)
that needs revision, not the direction.

**Q2 — Should `ProjectState` embed `WorkingContext`?**
No. `ProjectState` is defined by being persisted, query-independent, and
small (§7's top-K/tier-aware truncation). A `WorkingContext` is inherently
query-scoped (`TOPIC`/`GENERAL` grouping is *about* what one query's
retrieval activated). Embedding a `WorkingContext` inside `ProjectState`
would either force `ProjectState` to be recomputed per query (defeating the
entire point of persistence) or force a stale, query-shaped artifact to sit
inside a durable object it has no business being in.

**Q3 — Should both remain fully independent?**
No, but not fully coupled either — the right shape is **directional,
optional dependency**: `WorkingContext` construction may consult
`ProjectState` (one new code path, §3), but `ProjectState` must never import
from, construct, or reason about `WorkingContext`/`RoleBucket`/`MemoryRole`
at all. For narrow, single-concept, `POINTED_QA` queries, `WorkingContext`
should remain **exactly** as decoupled as it is today — built straight from
`allocated` candidates, no `ProjectState` read, zero behavior change. This
mirrors the "strict floor, never a regression" property `CONTEXT_PLAN_OBJECT.md`
already established elsewhere in this codebase.

**Q4 — Which architecture scales best to 500+ conversation projects?**
`ProjectState`-as-backbone, decisively. `_allocate()` (the prefix
`query_working_context`/`query_structured` both run) re-executes hybrid
retrieval → ranking → acceptance → allocation from scratch on every call,
over whatever the vault has grown to. `ProjectState`'s eventual Phase B
(incremental materialization, `O(1)` load + bounded gap-fill) is the only
one of the two whose cost is independent of vault size. A 500-conversation
project asking "continue implementing Haven" today pays full retrieval cost
whether it goes through `query_with_trace` or `query_structured`; neither
path is cheap yet, but only `ProjectState`'s design has a route to being
cheap.

**Q5 — Which produces the best prompt for AI assistants?**
Neither alone — the combination does. §4 gives the concrete shape:
`ProjectState`'s never-drop, durable fields (identity, phase, current
objective, constraints, do-not-do, open blockers, explicit gaps) belong at
the *top* of the prompt as an orientation layer; `WorkingContext`'s
per-concept `TOPIC` grouping belongs *below* it as the selective, query-
specific deep-dive. This is exactly the two-phase structure
`WORKING_CONTEXT_2_DESIGN.md` §1 already derived from how humans
reconstruct context (orientation/gist/timeline, cheap and always-on, vs.
selective deep-dive, targeted and query-specific) — that document named the
split correctly but had no durable object to put in the first tier.
`ProjectState` is that object.

**Q6 — Which minimizes recomputation?**
`ProjectState`-as-backbone. Concretely: today, asking two different
questions about the same project ("what's blocking us" and "what's the
current goal") independently re-runs retrieval+ranking+acceptance twice,
each time discarding the other's work. Once `ProjectState` persists
(Phase B), both questions read the same already-resolved `blockers`/
`current_objective` fields with no retrieval at all; only a genuine gap
triggers a bounded, single-category retrieval.

**Q7 — Which minimizes coupling?**
The directional dependency in Q3: one new construction path
(`WorkingContextBuilder.from_project_state`, or equivalent, added — not the
existing `.build()` changed) depends on `ProjectState`; nothing in
`obsidian/memory_engine/project_state.py` depends on `working_context_builder.py`,
`retrieval_models.WorkingContext`, `RoleBucket`, or `MemoryRole`. This keeps
`ProjectState` importable and testable in total isolation from the
rendering/grouping layer, which matters because `ProjectState` is the
persisted artifact — the thing most likely to be read by future,
unanticipated consumers (an API endpoint, a CLI, a different renderer) that
have no reason to know `WorkingContext` exists at all.

**Q8 — Which is easiest to evolve?**
Additive-only. Every change this document recommends is a new method or a
new optional parameter, never a signature change to `WorkingContextBuilder.build`,
`ContextBuilder.build`, or `StructuredPromptBuilder.render`. This matches
`PROJECT_STATE_DESIGN.md`'s own phasing discipline (§10, each phase strictly
additive) and this codebase's demonstrated bias throughout `engine.py` (every
one of Phases 1.5–4/A is a new attachment to `RetrievalTrace`, never a
change to what `query()` returns).

**Q9 — Which best supports future persistence?**
Only `ProjectState` should ever be persisted. `WorkingContext` must stay
permanently ephemeral/request-scoped — persisting a `WorkingContext` would
just be reinventing `ProjectState` with a query-shaped bias (`TOPIC`/
`GENERAL` grouping keyed to one query's activation, not to the project),
and would create exactly the two-sources-of-truth problem this whole
integration exists to avoid.

**Q10 — Which best supports continuation-style conversations?**
`ProjectState` answers the *opening* move of a continuation
("where do things stand") in `O(1)`; `WorkingContext`'s per-concept
grouping remains the right tool for the *follow-up* moves within that same
conversation ("okay, now show me what's built in the retrieval pipeline
specifically") once the user has picked a thread to pull on. Neither
replaces the other inside one continuation conversation — they answer
different turns of it.

---

## 2. Duplication analysis — what must never be duplicated

1. **Category vocabulary.** `ContextCategory` and `MemoryRole` must not
   remain two independently-maintained tables mapping the same
   `MemoryType`s to different buckets. Recommendation: this integration
   does **not** need to unify them immediately (that's a larger, separate
   refactor — `MemoryRole` is deeply embedded in `StructuredPromptBuilder`'s
   fixed `_ROLE_TAG` and every existing `WorkingContext`). But
   `from_project_state` must resolve a `ProjectState` field to a `MemoryRole`
   via **one fixed, explicit table** (`ProjectState` field name →
   `MemoryRole`), reviewed once, not by re-deriving it ad hoc per field. Do
   not let a third mapping table grow next to `_CATEGORY_TO_FIELD` and
   `_MEMORY_TYPE_ROLE`.
2. **Retrieval mechanics.** Gap-fill for a `ProjectState`-backed
   `WorkingContext` must call the exact same `HybridCandidateRetriever` →
   `DeterministicRanker` → `AcceptanceStage` stages `_allocate` already
   calls — never a second, parallel retrieval implementation.
3. **Provenance objects.** A `StateRef` and the `KnowledgeObject`/
   `RankedCandidate` it was derived from must never independently drift.
   Concretely: `ProjectState`'s never-drop fields (`constraints`, `blockers`)
   must be the single source of "what the never-drop tier contains" — do
   not let `DeterministicSlotAllocator`'s existing tiering logic and
   `ProjectState`'s field-priority tiering (`PROJECT_STATE_DESIGN.md` §6)
   become two independently-tuned priority schemes for conceptually the
   same tier.
4. **Rendering.** `StructuredPromptBuilder` stays the single XML renderer.
   Do not add a second renderer for the `ProjectState`-backed case — extend
   `render()` additively (§3).
5. **The "gaps must never be inferred away" invariant.** This is the one
   piece of `ProjectState`'s design (`PROJECT_STATE_DESIGN.md` §3, §8) that
   is easy to silently lose in an integration: if `from_project_state` folds
   an empty `ProjectState` field into an empty `RoleBucket`, the bucket
   self-closes in the rendered XML exactly like "nothing was relevant" —
   indistinguishable from "this category was checked and is genuinely
   empty." `WorkingContext`/`StructuredPromptBuilder` today have **no**
   concept of an explicit gap at all (`WORKING_CONTEXT_2_DESIGN.md` §4's
   proposed "Unknown / Not Found" section was never built either). Any
   `ProjectState` integration that routes through the existing renderer
   without addressing this reintroduces the exact confabulation-adjacent
   failure mode (silence read as absence-of-fact rather than
   absence-of-data) `ProjectState`'s own design was written to prevent.

---

## 3. Recommended object relationships and data flow

### 3.1 Dependency direction

```
ProjectState / ProjectStateBuilder / StateRef        (no dependents below; importable standalone)
        │
        │  (new, additive: ProjectStateBuilder or a small adapter converts
        │   StateRef -> the minimal shape WorkingContext rendering needs)
        ▼
WorkingContextBuilder.from_project_state(...)         (new method, .build() untouched)
        │
        ▼
WorkingContext (kind=ContextKind.PROJECT)              (finally activates the dead enum value)
        │
        ▼
StructuredPromptBuilder.render(...)                    (extended additively, §3.3)
```

`ProjectState` never imports `retrieval_models.WorkingContext`,
`RoleBucket`, or `MemoryRole`. The dependency runs one way, matching Q7.

### 3.2 Resolving the `RankedCandidate` mismatch (§0 point 3)

Two options, both additive; pick one deliberately rather than discovering
the mismatch mid-implementation:

- **(a) Rehydrate.** Give `ProjectStateBuilder`/its caller access to the
  original `KnowledgeObject`s (already true today — `allocated` carries
  full `RankedCandidate`s at build time) and have `from_project_state`
  accept the *pre-conversion* `RankedCandidate`s directly for a live query,
  falling back to a `StateRef`-only path (option b) only once `ProjectState`
  is actually persisted and re-loaded cold (Phase B), where no live
  `RankedCandidate` exists anymore. This keeps `RoleBucket`'s contract
  completely untouched for the "just built this run" case, and only faces
  the real mismatch once persistence exists.
- **(b) Give `ProjectState`-backed contexts their own bucket shape.** Once
  `ProjectState` is loaded cold from disk (Phase B) with no live
  `RankedCandidate` behind it, don't force a `RoleBucket`. Render
  `ProjectState` through a dedicated, additive path in
  `StructuredPromptBuilder` (§3.3) that reads `StateRef` directly. This
  avoids fabricating a `final_score`/`score_breakdown` for something that
  was never ranked this run — which would otherwise contaminate the one
  field `RetrievalTrace`'s diagnostics already treat as ground truth.

Recommendation: **(a) now, (b) once Phase B (persistence) lands.** Phase A's
`ProjectState` is built from the same `allocated` list a query's
`RankedCandidate`s already came from, so no rehydration problem exists yet —
`from_project_state` can trivially take `allocated` directly (or a filtered
subset of it) rather than `ProjectState`'s `StateRef`s, for now. The
`StateRef`-only rendering path only becomes necessary once cold, persisted
`ProjectState` (no accompanying `RankedCandidate`s) is a real input — at
which point (b) is worth the second, minimal rendering path specifically
*because* pretending a persisted fact has "this run's rank" would be wrong,
not just inconvenient.

### 3.3 `StructuredPromptBuilder` extension

Add an optional `project_state: Optional[ProjectStateRenderModel] = None`
parameter to `render()` (default `None` — byte-identical output when
omitted, matching this codebase's established "new parameter defaults to
off" convention, e.g. `MemoryEngine.__init__`'s `query_rewriter`). When
supplied, render one new top-level child of `<HavenContext>`, **before**
the `<WorkingContext>` elements, with an explicit gaps section:

```
<HavenContext version="1">
  <Guidance>...</Guidance>
  <ProjectState confidence="0.75">
    <CurrentObjective>[1] Ship Phase A of ProjectState</CurrentObjective>
    <Constraints>
      <Item>[2] Never silently drop a user-stated rule</Item>
    </Constraints>
    <Blockers/>
    <Gaps>
      <Item>blockers</Item>
      <Item>open_questions</Item>
    </Gaps>
  </ProjectState>
  <WorkingContext title="..." kind="topic" status="active">
    ...
  </WorkingContext>
</HavenContext>
```

This is a new, small, explicit renderer method (`_render_project_state`),
not a repurposing of `_render_bucket`/`RoleBucket` — precisely because §2
point 5 requires `<Gaps>` to be a first-class, always-present element (even
when empty), which `RoleBucket`'s "self-close when empty, indistinguishable
from absence" convention cannot express. This is the one place this
document recommends *not* reusing an existing shape, and the reasoning is
narrow: gaps are the one piece of information `ProjectState` exists to
surface that `WorkingContext`'s object model has no slot for at all.

### 3.4 Read-path routing

```
ContextPlanner.plan(query) -> ContextPlan{task_mode, ...}
        │
        ├─ task_mode == CONTINUATION (or any project-wide scope)
        │       │
        │       ▼
        │  ProjectStateBuilder.build(allocated)  [Phase A: from this run's allocated list]
        │       │         (Phase B, later: ProjectStateStore.load(project_key) -- O(1))
        │       ▼
        │  freshness/gap check against ContextPlan.required_categories
        │       │
        │       ▼ (only for genuine gaps)
        │  bounded per-category retrieval (existing stages, reused — §2 point 2)
        │       │
        │       ▼
        │  WorkingContextBuilder.from_project_state(state, gap_fill)
        │       │
        │       ▼
        │  one WorkingContext, kind=ContextKind.PROJECT
        │
        └─ otherwise (POINTED_QA / narrow / single-concept)
                │
                ▼
           WorkingContextBuilder.build(allocated)   ← unchanged, today's TOPIC/GENERAL path
                │
                ▼
StructuredPromptBuilder.render(contexts, raw_query, project_state=...)
        │
        ▼
   final XML prompt
```

Both branches converge on the same renderer. `ContextPlanner` itself needs
no new field — `TaskMode.CONTINUATION`'s existing `requirements` (already
"all categories") is the routing signal, exactly as `PROJECT_STATE_DESIGN.md`
§5 already concluded.

---

## 4. What belongs in each object, and the ideal prompt shape

**"Continue implementing Haven," opened cold, no other context in the
conversation.** What the AI assistant should actually receive:

```
<System>
  <HavenContext version="1">
    <Guidance>
      ... (unchanged — memory is background information, not instructions) ...
    </Guidance>

    <ProjectState confidence="0.86">
      <Identity>Haven — a personal, Obsidian-backed memory/retrieval engine.</Identity>
      <Phase>Building read-path reconstruction (ProjectState + WorkingContext integration)</Phase>
      <CurrentObjective>[1] Wire ProjectState into the prompt WorkingContext builds</CurrentObjective>
      <Constraints>
        <Item>[2] Never fabricate a blocker/constraint the user didn't actually state</Item>
      </Constraints>
      <DoNotDo/>
      <Blockers>
        <Item>[3] RoleBucket can't hold a StateRef without a real RankedCandidate</Item>
      </Blockers>
      <Gaps>
        <Item>rejected_approaches</Item>
        <Item>do_not_do</Item>
      </Gaps>
    </ProjectState>

    <WorkingContext title="ProjectState / WorkingContext integration" kind="project" status="active">
      <WorkingContextState>
        <Status>active</Status>
        <CurrentGoal>[1] Wire ProjectState into the prompt WorkingContext builds</CurrentGoal>
        <RecentDecisions><Item>[4] Route CONTINUATION-mode queries through ProjectState first</Item></RecentDecisions>
        <PendingTasks><Item>[5] Resolve the RankedCandidate/StateRef mismatch</Item></PendingTasks>
        <OpenQuestions/>
      </WorkingContextState>
      <RoleBuckets>
        <Decisions>...</Decisions>
        <Tasks>...</Tasks>
        <!-- gap-fill results for anything ProjectState didn't already cover -->
      </RoleBuckets>
    </WorkingContext>
  </HavenContext>
  <UserRequest>
    Continue implementing Haven.
  </UserRequest>
</System>
```

**What belongs where, and why nothing here is duplicated:**

| Content | Lives in | Never in the other, because |
|---|---|---|
| Identity, phase, current objective | `ProjectState` only | These are project-wide and sticky across queries; a `WorkingContext` rebuilt per query has no business re-deciding "what phase is this project in." |
| Constraints, do-not-do, durable blockers | `ProjectState` only, never-drop tier | `WorkingContext`'s per-query slot allocator already has its own budget pressure (`DeterministicSlotAllocator`); duplicating the never-drop list into its tiering would risk two independently-tuned notions of "never drop this." |
| Explicit gaps | `ProjectState`'s `<Gaps>` only | This is precisely the information `RoleBucket`'s self-closing-when-empty convention cannot represent — it must have exactly one home, or a future querier can't tell which "empty" is authoritative. |
| Per-concept detail (recent decisions on a specific subsystem, pending tasks for a specific file) | `WorkingContext` only | This is inherently query-scoped — `ProjectState`'s few, bounded, top-K fields are not the place for arbitrary per-concept depth; that would turn `ProjectState` back into an ever-growing blob (`PROJECT_STATE_DESIGN.md` §8, "field-growth regression"). |
| The actual memory content (`canonical_fact`, `confidence`, provenance) | Rendered once, wherever the item appears | A memory referenced by `ProjectState.current_objective` and also present in a `WorkingContext` bucket must render as the **same** `[N]` index / fact text, never two separately-fetched copies with potentially different `confidence` snapshots. |

---

## 5. Trade-offs

- **Cost of the `<ProjectState>` renderer addition (§3.3):** one new,
  small, additive method and one new optional parameter — genuinely low
  cost, and it's the only way to preserve the gaps invariant (§2 point 5)
  without a larger `RoleBucket` redesign.
- **Cost of deferring the `StateRef`/`RankedCandidate` reconciliation
  (§3.2) to Phase B:** real, but honest — Phase A has no cold, disconnected
  `StateRef` to worry about yet (its `ProjectState` is always built from the
  same `allocated` list a live `RankedCandidate` came from), so solving the
  cold-load case now would be solving a problem that doesn't exist until
  persistence does.
- **Risk of the category-vocabulary non-unification (§2 point 1):** the
  single largest deferred risk in this document. Two tables
  (`_CATEGORY_TO_FIELD`/`resolve_category` vs. `_MEMORY_TYPE_ROLE`/
  `resolve_role`) mapping the same `MemoryType`s to different taxonomies is
  already a latent inconsistency (a `BLOCKER` is a `ContextCategory.BLOCKER`
  but a `MemoryRole.TASK`) that this integration does not fix, only works
  around with one more fixed table (§2 point 1). Unifying `MemoryRole` and
  `ContextCategory` properly is a separate, larger refactor this document
  deliberately does not scope in, since it touches every existing
  `WorkingContext` shape, not just the new path.
- **Risk of scope creep into Phase B (persistence) prematurely:** this
  document's read-path routing (§3.4) is written to work today, with
  `ProjectState` built fresh per call exactly as Phase A already does — it
  does not require `ProjectStateStore`/incremental materialization to exist
  first. Building the `WorkingContext` integration does not have to wait on
  persistence; it can ship against Phase A's recompute-per-call
  `ProjectState` first, exactly as `PROJECT_STATE_DESIGN.md` §12 already
  argues for `ProjectState` in isolation ("independently useful... even
  before incremental materialization exists").

---

## 6. Scalability

Unchanged from `PROJECT_STATE_DESIGN.md` §7's analysis — this integration
adds no new scaling concern of its own, since it consumes `ProjectState`
exactly as already designed and routes narrow queries around it entirely.
The one new scaling-relevant fact this document adds: `query_structured`
(the only path that reaches `StructuredPromptBuilder` today) currently pays
the **full** `_allocate` retrieval cost on *every* call, including
`CONTINUATION`-mode ones — so wiring `ProjectState` into this specific path
is the highest-leverage place to capture Phase B's eventual `O(1)`-load win,
more so than `query_with_trace`, which is diagnostics-only and not on the
prompt-serving path at all.

---

## 7. Roadmap

**Step 1 (no new persistence, no ContextPlanner change). IMPLEMENTED IN TWO
PARTS: plumbing (2026-07-09), renderer (2026-07-09) — see "Step 1's deferred
renderer" and "Step 1 implementation note" at the top of this document for
exactly what shipped, when, and why the renderer landed after the plumbing.**
Add `WorkingContextBuilder.from_project_state(state, gap_fill)` per §3.2
option (a) — builds a `ContextKind.PROJECT` `WorkingContext` from the same
`allocated` list a live query already has, using a fixed `ProjectState`
field → `MemoryRole` table (§2 point 1). Add `StructuredPromptBuilder`'s
`<ProjectState>` rendering (§3.3), defaulted off. Route only inside
`_allocate`/`query_structured`, gated on `ContextPlan.task_mode ==
CONTINUATION`. Fully additive; every existing call to `query`,
`query_with_trace`, `query_working_context` (unrouted), and `query_structured`
for non-`CONTINUATION` queries is byte-identical to today.
*What actually shipped, in order:* first, the retrieval-prefix unification
(`MemoryEngine._run_retrieval`/`_RetrievalPrefix`) that makes a `ContextPlan`
reachable from `query_structured()` at all, plus `ProjectState` availability
via a new `project_state` parameter on `StructuredPromptBuilder.render()`
(accepted, not yet rendered) — gated on `TaskMode.CONTINUATION`, exactly as
described. Then, the `<ProjectState>` XML renderer itself (`_render_project_state`
and helpers in `structured_prompt_builder.py`) — see "Step 1's deferred
renderer" above for its exact shape. **`WorkingContextBuilder.from_project_state`
was not built and `ContextKind.PROJECT` remains dead** — those, per §3.2's
recommendation, wait on §3.4's routing (§7's own Step 2) rather than being
pulled forward with the renderer. Every existing call to `query`,
`query_with_trace`, `query_working_context`, and `query_structured` for
non-`CONTINUATION` queries is confirmed byte-identical to before this
integration, both before and after the renderer landed (see
`obsidian/tests/test_engine.py::TestSharedRetrievalPrefixIntegration`).

**Step 2 (depends on `CONTEXT_PLAN_OBJECT.md`'s `query_context_plan`,
already-designed elsewhere, not new scope here).**
Wire the freshness-check + bounded gap-fill fallback (§3.4) so a
`CONTINUATION` query no longer runs six parallel category retrievals
unconditionally — it consults `ProjectState` first and only fills genuine
gaps.

**Step 3 (Phase B dependency — persistence).**
Once `ProjectStateStore` exists (`PROJECT_STATE_DESIGN.md` Phase B),
implement §3.2 option (b) — the `StateRef`-only rendering path for cold,
persisted `ProjectState` with no accompanying live `RankedCandidate`s.

**Step 4.**
Revisit the `MemoryRole`/`ContextCategory` non-unification (§5) as its own,
separately-scoped design once both consumers of the categories have been in
production long enough to know which of the two taxonomies (or a merged
third) actually earns its keep.

---

## 8. Recommendation

Build Step 1 now. It requires no persistence work, resolves the concrete
`RankedCandidate` type mismatch §0 identified in the existing design
honestly (rather than inheriting a proposal that doesn't type-check), and
is the first point at which `ProjectState` — currently inert, attached only
to a diagnostics-only trace on a call path (`query_with_trace`) that isn't
even the one serving prompts — becomes reachable from the actual prompt a
downstream AI assistant sees. Do not attempt Step 3 before Phase B
(persistence) exists; building a `StateRef`-only render path against a
`ProjectState` that is still always freshly recomputed from a live
`allocated` list would be solving a problem — "what if there's no
`RankedCandidate` behind this `StateRef`" — that cannot occur yet. Treat §5's
category-vocabulary non-unification as a known, explicitly deferred risk,
not an oversight: fixing it now would expand this integration into a
rewrite of `WorkingContext`'s existing shape, which is a larger and
separately-reviewable decision.
