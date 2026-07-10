# ProjectState — Architectural Evaluation

Status: **Evaluation only. No implementation.** Grounded entirely against source
on disk as of 2026-07-10:
`obsidian/memory_engine/project_state.py`,
`obsidian/memory_engine/structured_prompt_builder.py`,
`obsidian/memory_engine/engine.py`,
`obsidian/memory_engine/context_planner.py`,
`obsidian/memory_engine/coverage_analyzer.py`,
`obsidian/memory_engine/deterministic_slot_allocator.py`,
`obsidian/memory_engine/category_preference.py`,
`obsidian/memory_engine/working_context_builder.py`,
`obsidian/ontology/retrieval_models.py`,
`obsidian/tests/test_project_state.py`,
`obsidian/tests/test_structured_prompt_builder.py`,
`obsidian/tests/test_engine.py`. Every claim below cites the file it comes
from. Where a design document (`PROJECT_STATE_DESIGN.md`,
`PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md`) describes something not
present in code, that is called out explicitly — this document evaluates
**what ships**, not what was proposed.

## 0. What actually ships, in one paragraph

`ProjectStateBuilder.build()` (`project_state.py:511`) takes one query's
already-ranked, already-budget-truncated `allocated: List[RankedCandidate]`
— the exact list `DeterministicSlotAllocator` selected under
`RetrievalConfig.max_results` (default 50, `retrieval_config.py:97`) — and
buckets it into 8 tracked fields (`current_objective`, `decisions`,
`active_tasks`, `blockers`, `constraints`, `implementation_state`,
`code_areas`, `open_questions`) plus one untracked convenience field
(`superseded_decisions`), by a fixed `MemoryType → ContextCategory` table
reused verbatim from `coverage_analyzer.resolve_category`
(`project_state.py:487-494`). It is reachable from exactly one prompt-serving
path — `MemoryEngine.query_structured()` — and only when
`ContextPlanner` lexically classifies the query as `TaskMode.CONTINUATION`
(`engine.py:1206-1208`). Nothing is persisted; nothing is inferred; nothing
incremental exists. This is a faithful, narrower-than-designed slice of
`PROJECT_STATE_DESIGN.md`'s Phase A, and the design documents' own status
headers already say so accurately. The rest of this document evaluates
whether that slice, as it actually behaves today, is good at the one job
it's reachable to do: helping a downstream model resume "continue
implementing Haven."

---

## 1. Does ProjectState actually reconstruct what an AI needs to resume work?

**Partially, and only for one narrow trigger condition.** Three structural
facts limit it more than the design documents' prose suggests:

**a) It answers "what did this one retrieval pass surface," not "what is
true about the project."** `ProjectState` is built strictly downstream of
`DeterministicSlotAllocator`, which selects a single flat top-`max_results`
slice ranked by `final_score` across *all* categories combined
(`deterministic_slot_allocator.py:37-50` — "Selection, not truncation," no
per-category reservation of any kind). A category can be entirely absent
from `ProjectState` — and therefore appear in `gaps` — not because the vault
contains no such memory, but because other categories' candidates outscored
it for the 50 available slots this run. `ProjectState.gaps` cannot
distinguish these two cases; the module docstring is honest about this
("this run's reconstruction," not "the vault's") but nothing downstream —
not the XML render, not `ContextPlan`, not a consuming model — is told which
kind of empty it's looking at.

**b) Plan-awareness, which exists in the codebase, never reaches the path
that actually renders a prompt.** `ContextPlanner` classifies "continue
implementing Haven" as `TaskMode.CONTINUATION` and produces a
`CategoryRequirement` table biasing retrieval toward `DECISION`, `TASK`,
`CONSTRAINT`, `BLOCKER`, `RESEARCH`, `OPEN_QUESTION`
(`context_planner.py:445-452`). But `CategoryPreferenceScorer`, the stage
that actually applies that bias to ranking, only runs inside
`query_with_trace()` — a diagnostics-only method nothing renders a prompt
from. `query_structured()` (the only method that reaches
`StructuredPromptBuilder`) calls `_allocate()`, whose own docstring states
plainly: *"Deliberately not planner-aware... never runs
CategoryPreferenceScorer... sees DeterministicRanker's raw scores,
unmodified"* (`engine.py:1103-1114`). So the one `ContextPlan` signal that
reaches the rendered prompt is a single boolean — "was this classified
CONTINUATION, yes/no" (`engine.py:1207`) — not the category weighting the
plan actually specifies. `ProjectState`'s content for a continuation query is
therefore whatever a plan-blind top-50 ranking happened to include, not a
plan-guided reconstruction.

**c) Two of the eight tracked fields answer "what is this project" and "what
phase are we in" — and neither exists.** `identity` and `phase`, the two
fields a human asks first when resuming (see §5), are `INFERRED`-only in the
design and are not implemented at all in this module — omitted, not
present-but-empty (`project_state.py:68-76`). `current_objective` is the
closest substitute, but it is sourced from `MemoryRole.GOAL` via a role, not
a `ContextCategory` — `GOAL` has no entry in `MEMORY_TYPE_CATEGORY` at all
(`coverage_analyzer.py:112-122`), meaning it is also outside
`ContextPlanner`'s requirement table entirely. A `GOAL`-typed memory
reaching `ProjectState.current_objective` on a `CONTINUATION` query is
incidental to the flat top-50 ranking, not the product of any goal-directed
retrieval.

**Net assessment:** `ProjectState` reliably reconstructs a small, honestly-
labeled slice of *recently high-scoring facts in six-to-eight categories*,
correctly refuses to fabricate anything for categories it didn't see, and is
byte-verified to never contaminate any other query path
(`test_engine.py`'s `TestProjectStateOmitted`,
`test_structured_prompt_builder.py:604-624`). It does not yet reconstruct
"where things stand" in the sense a returning engineer means the phrase —
it has no notion of project identity, phase, milestone, or history beyond
whatever fits in one ranked slice.

---

## 2. Which fields provide the highest value?

Ranked by (i) how safe the derivation is (never-fabricated, verbatim) and
(ii) how directly useful the content is for resuming work:

| Rank | Field | Why |
|---|---|---|
| 1 | `constraints` | `MemoryType.RULE`-sourced, `MEMORY_DIRECT`, verbatim user rules. The single field where a wrong answer (a fabricated rule) would be actively harmful, and it structurally cannot be fabricated in this phase. |
| 2 | `blockers` | Same safety profile as `constraints`; directly actionable ("what's stopping me right now" is usually the first thing a resuming engineer wants). |
| 3 | `decisions` / `superseded_decisions` split | The status split is genuinely `DETERMINISTIC` — a pure projection of `DecisionMetadata.status`, not a judgment call (`project_state.py:566-570`) — so this field is trustworthy in a way most of the others can only aspire to. |
| 4 | `open_questions` | Cheap, safe, and the field most likely to short-circuit a resuming AI re-litigating something already flagged as unresolved. |
| 5 | `active_tasks` / `implementation_state` / `code_areas` | Directly relevant to "what's built, what's left," but weaker in practice — see §3 for why these are the fields most exposed to write-side thinness. |
| 6 | `current_objective` | High value *when present*, but per §1(c) its presence is incidental to a flat ranking rather than goal-directed retrieval — treat its absence as uninformative, not as "no goal exists." |

**Common thread:** the fields the design correctly marks "never inferred"
(`constraints`, `blockers`, the decision-status split) are exactly the ones
that survive scrutiny best, because their safety property and their
usefulness property are the same property — a verbatim pointer to a real
memory is both the safest thing to render and the most trustworthy thing for
a downstream model to act on.

---

## 3. Which fields are weak, redundant, or hard to derive deterministically?

**Weak (thin by construction, not by bug):**

- **`implementation_state` / `code_areas`.** Real `MemoryType`s exist and
  resolve correctly (`coverage_analyzer.py:119-121`), but neither category
  appears in `TaskMode.CONTINUATION`'s requirement table
  (`context_planner.py:445-452` lists `DECISION, TASK, CONSTRAINT, BLOCKER,
  RESEARCH, OPEN_QUESTION` — no `IMPLEMENTATION_STATE`, no `CODE_AREA`).
  Even if plan-awareness *did* reach the rendering path (it doesn't, per
  §1(b)), these two fields would still receive no bias toward being
  retrieved for a continuation query. They are present in `ProjectState`'s
  schema but structurally the least likely to be populated by the one task
  mode that actually builds a `ProjectState`.
- **`current_objective`.** Already covered in §1(c) — its presence is
  incidental, not goal-directed, since `GOAL` has no `ContextCategory`.

**Redundant — a concrete, verified triplication, not a hypothetical:**

A `GOAL`-typed memory that becomes `ProjectState.current_objective` is, in
the same rendered prompt, *also*:
1. `WorkingContextState.current_goal` — built by the identical rule ("top-
   ranked `MemoryRole.GOAL` candidate," `retrieval_models.py:1748,1768`) —
   rendered as `<CurrentGoal>[N] fact</CurrentGoal>`
   (`structured_prompt_builder.py:419-424`), **and**
2. A full `<Memory>` element inside a `<Goals>` `RoleBucket`, since
   `WorkingContextBuilder._build_context` buckets *every* role including
   `GOAL` with no exclusion (`working_context_builder.py:201-207`,
   `_ROLE_TAG[MemoryRole.GOAL] = "Goals"`, `structured_prompt_builder.py:123`).

The same triplication pattern applies to `decisions` (→
`WorkingContextState.recent_decisions` + `<Decisions>` bucket),
`active_tasks` (→ `pending_tasks` + `<Tasks>` bucket), and `open_questions`
(→ `state.open_questions` + `<OpenQuestions>` bucket). The `[N]` index is
correctly shared across all three renderings (`_ref_state`/`_ref` both key
off the same `Dict[UUID, int]`, `structured_prompt_builder.py:287-305`), so
there is no risk of two different confidence/fact-text snapshots diverging —
but the same fact's full content still appears up to three times in one
prompt: once as a one-line reference under `<ProjectState>`, once as a
one-line reference under `<WorkingContextState>`, and once as the full
`<Memory>` element with all its attributes under `<RoleBuckets>`. For a
`CONTINUATION` query with a healthy set of decisions/tasks/blockers, this is
a real, non-trivial chunk of duplicated prompt content — directly relevant
to §7's scaling question, since duplication scales with content, not with
query count.

**Hard to derive deterministically, as currently labeled:**

- **`gaps`.** The value itself is a pure, deterministic projection
  (`project_state.py:587-597`) — but its *meaning* is not what its name
  suggests to a reader who hasn't read the module docstring. "Gap" reads as
  "this category has no data"; the actual guarantee is narrower ("this
  category had no representative in this run's post-ranking, post-budget
  top-50") per §1(a). The field is honestly documented in the module
  docstring (`project_state.py:78-90`) but that honesty does not survive
  into the XML render — `<Gaps><Item>blockers</Item></Gaps>` gives a
  downstream model no signal that "blockers" might simply have lost a
  ranking contest, not that none exist.
- **`confidence`.** Computed as `(8 - len(gaps)) / 8`
  (`project_state.py:598-600`) — a completeness fraction over exactly the 8
  names in `PROJECT_STATE_FIELD_NAMES`, nothing more. It is rendered as a
  bare attribute, `<ProjectState confidence="0.75">`
  (`structured_prompt_builder.py:325`), with no attribute name or nearby
  text distinguishing it from a per-fact confidence score (every `<Memory>`
  element elsewhere in the same prompt also carries a `confidence`
  attribute, but that one means "how sure Haven is this fact is true"
  — `structured_prompt_builder.py:479-480`). Same word, two different
  meanings, in the same document.

---

## 4. What important project state is still missing (deterministically derivable only)?

Per the task's constraint, this section only lists additions derivable from
**existing memories with no write-side change**. Two candidates qualify;
everything else in the example list either requires persistence (Phase B,
out of scope for "add a field") or requires new write-side extraction
(explicitly not deterministic from what exists today):

**Qualifies — derivable today, no write-side change:**

- **`recent_discoveries` (the `RESEARCH` category).** `MemoryType.FACT`
  already resolves to `ContextCategory.RESEARCH`
  (`coverage_analyzer.py:116`), and `TaskMode.CONTINUATION`'s own
  requirement table lists `RESEARCH` as required
  (`context_planner.py:450`) — it is the *only* plan-required category with
  no corresponding `ProjectState` field today. Adding it would be the same
  `MEMORY_DIRECT` bucketing every other field already uses, closing the one
  clean mismatch between what `ContextPlanner` asks for and what
  `ProjectState` tracks (see §6's table for the rest of that mismatch).
  This is exactly what the original design named and the implementation
  simply hadn't reached yet (`project_state.py:74-76`).
- **A `gaps`-adjacent distinction between "empty because absent from the
  vault" and "empty because this run's top-50 excluded it."** Not a new
  field sourced from memories — a re-derivation of information
  `ProjectState` already discards. `ProjectStateBuilder.build` has no
  visibility into anything the ranker/allocator dropped before `allocated`
  reached it (§1(a)), so this cannot be produced from `allocated` alone —
  it would need the pre-allocation `ranked_all` list `_run_retrieval`
  already computes (`engine.py`, `_RetrievalPrefix.ranked_all`) as an
  additional input. That input already exists on the call path
  (`query_structured` has `prefix.ranked_all` in scope,
  `engine.py:1202-1203`) — this is a plumbing change, not new inference,
  but it is a real code change to `ProjectStateBuilder.build`'s signature,
  not a pure field addition. Flagging it here because it is the single
  highest-leverage fix to `gaps`' honesty, deterministic throughout, and
  worth scoping separately from "add a field."

**Does not qualify — listed for completeness, explicitly not recommended
under this document's constraints:**

- **`current_milestone` / `phase`.** The design's own analysis is correct:
  no `KnowledgeObject` field is "the project's phase" by construction; any
  answer requires synthesis, i.e. `INFERRED`, i.e. out of scope for a
  deterministic-only recommendation (`PROJECT_STATE_DESIGN.md` §3).
- **`pending_validation`.** No `MemoryType`/`ContextCategory` produces this
  concept today; would require Extractor prompt changes (write-side), the
  same class of gap already flagged for `rejected_approaches`/`do_not_do`
  (`project_state.py:68-72`).
- **`next_recommended_action`.** Inherently a judgment call about what to do
  next — the same category of thing `PROJECT_STATE_DESIGN.md` §3's
  never-inferred list exists to keep out of this object. `blockers` +
  `open_questions` + `active_tasks`, rendered verbatim, already give a
  downstream model the raw material to derive this itself; having Haven
  pre-compute it would duplicate what the consuming model is for.
- **"Recent changes."** Requires a notion of *recent relative to what came
  before* — meaningless without a persisted watermark (`version`,
  `last_incorporated_event_id`), which does not exist in Phase A
  (`project_state.py:44-48`). Not derivable from one query's `allocated`
  list; a Phase B dependency, not a field addition.

---

## 5. Comparison to how humans resume complex projects after weeks away

A returning engineer typically reconstructs, in this order: (1) *what is
this thing* (one-line identity/purpose), (2) *what state is it in* (phase —
prototype vs. hardening vs. shipped), (3) *what was I about to do*
(objective/next action), (4) *what's blocking or constraining me*, (5) *what
did I already decide and why* (so as not to re-litigate it), (6) *what's
half-done* (implementation state), and only then (7) *the detailed history*.

| Human resumption step | `ProjectState` coverage today |
|---|---|
| 1. What is this project | **Missing.** `identity` is `INFERRED`-only, not implemented (§1c). |
| 2. What phase is it in | **Missing.** `phase` is `INFERRED`-only, not implemented. |
| 3. What was I about to do | **Weak.** `current_objective` exists but is incidentally sourced, not goal-directed (§1c, §3). |
| 4. What's blocking/constraining me | **Present and trustworthy.** `blockers`/`constraints` are `MEMORY_DIRECT`, never-inferred (§2). |
| 5. What did I already decide | **Present and trustworthy.** `decisions`/`superseded_decisions` split is `DETERMINISTIC` (§2). |
| 6. What's half-done | **Present but thin.** `implementation_state`/`code_areas` exist but are outside the `CONTINUATION` plan's requirement table (§3). |
| 7. Detailed history | **Correctly out of scope.** `ProjectState` is explicitly a "now" snapshot, not a log (`PROJECT_STATE_DESIGN.md` §7) — appropriate; humans don't want a full log first either. |

The honest summary: **Haven's `ProjectState` is strong exactly where humans
resume *last* (blockers, constraints, decision history) and missing or weak
exactly where humans resume *first* (identity, phase, a goal-directed
objective).** This is not a coincidence of what got built — it is a direct
consequence of the never-inferred design principle (§3, §8 of the design
doc) correctly prioritizing safety for the fields it built, while the two
riskiest-to-infer fields (identity, phase) were deliberately deferred rather
than built unsafely. That was the right call to make first; it does mean
the object as it stands today under-serves the *opening* move of a
continuation conversation more than the design's own framing ("answers
'where do things stand'") suggests.

---

## 6. Evaluating the XML structure

**Current shape** (`structured_prompt_builder.py:264-274, 331`):
`<Guidance>` → `<ProjectState>` (`CurrentObjective`, `Decisions`,
`SupersededDecisions`, `ActiveTasks`, `Blockers`, `Constraints`,
`ImplementationState`, `CodeAreas`, `OpenQuestions`, `Gaps`) →
`<WorkingContext>` elements (each with its own `WorkingContextState` and
`RoleBuckets`).

**Should the ordering within `<ProjectState>` change? Yes, on one specific
point.** The current list order is `ProjectState`'s dataclass declaration
order (`_PROJECT_STATE_LIST_SECTIONS`, `structured_prompt_builder.py:139-148`),
which is a reasonable default but puts historical content
(`Decisions`, `SupersededDecisions`) ahead of the two `NEVER_DROP`-tier,
most-action-relevant fields (`Blockers`, `Constraints`). Per §5's ordering
("what's blocking me" before "what did I decide two weeks ago"), and per
`WORKING_CONTEXT_2_DESIGN.md`'s own orientation-before-detail principle that
motivated `ProjectState`'s placement in the first place
(`PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md` §4), `Blockers` and
`Constraints` reading *immediately* after `CurrentObjective` — before
`Decisions` — would match both how humans prioritize and the design's own
stated tiering intent. This is a pure reorder of one fixed tuple; zero
derivation risk. `SupersededDecisions`, being explicitly not-gap-tracked
convenience content (`PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md`, "Step
1's deferred renderer" note), reads more naturally last, immediately before
`Gaps`, than second.

**Should any sections move between `ProjectState` and `WorkingContext`?**
Not by moving content — the redundancy identified in §3 argues for
*removing* duplication, not relocating it. Concretely: when a `ProjectState`
is present (`CONTINUATION` queries only), `WorkingContextState`'s
`current_goal`/`recent_decisions`/`pending_tasks`/`open_questions`
(`structured_prompt_builder.py:411-428`) are already a strict subset of what
`<ProjectState>` just rendered one XML level up, using the identical
selection rule in the `current_goal`/`decisions`/`active_tasks`/
`open_questions` cases (§3's triplication). The `RoleBuckets` still carry
information `ProjectState` doesn't (the full `<Memory>` attributes:
`confidence`, `importance`, `confirmations`, `valid_from`/`valid_until`,
decision metadata) — those should stay. But `WorkingContextState`'s own
summary fields are, for a `CONTINUATION` query specifically, pure
restatement of what `<ProjectState>` already said in a terser form one
level up. Suppressing `WorkingContextState`'s summary fields specifically
when a `ProjectState` is already present in the same rendered document
would remove real duplication without losing any content — a rendering
change, not a new derivation, and additive in the sense that it only
changes output for the one task mode (`CONTINUATION`) that already gets a
`ProjectState`.

**Should any information render differently?** Two points from §3 carry
directly into rendering:
- `<Gaps>` should distinguish "not in the vault" from "not in this run's
  top-50" once (and only once) the input described in §4's second
  recommendation is available — until then, the honest fix is a rendering
  clarification (e.g. renaming the attribute or adding a short fixed caveat
  line) rather than a data change, since the underlying data genuinely
  cannot support the stronger claim yet.
- `confidence` on `<ProjectState>` reads as the same concept as a
  `<Memory>` element's `confidence` attribute but measures something
  different (§3). A more specific attribute name (something like
  `field_coverage` in the rendered XML, without touching the Python field
  name) would remove the ambiguity for a downstream model reading both
  attributes in the same document — a label-only change.

---

## 7. Will this scale to hundreds of conversations / thousands of memories?

**No, not as currently wired — and the reason is structural, not a matter of
tuning constants.**

- **Every `ProjectState` is a full recompute over the current top-50
  ranked slice, on every call.** There is no persistence
  (`project_state.py:41-43`), so cost is bounded per-call by
  `max_results`, not by project size — this part is fine and will not blow
  up per se. The actual scaling failure is qualitative, not computational:
  as a vault accumulates thousands of memories, a flat top-50 ranking
  across nine-plus categories increasingly reflects *recency and raw
  score*, not *category coverage*. §1(a)/§1(b) already established that
  nothing biases the ranking toward category balance on the path that
  builds `ProjectState`. In a young vault with a handful of memories,
  every category is likely representated in the top 50 by default; in a
  mature vault, high-volume categories (e.g. many small `TASK` or `FACT`
  memories) will systematically crowd out low-volume, high-value ones
  (`BLOCKER`, `RULE`) purely on frequency, unless those low-volume
  categories' recency/importance scores happen to compensate. `gaps` will
  grow noisier over time for exactly the projects where a stable,
  always-populated `blockers`/`constraints` view matters most.
- **No incremental materialization means no monotonic notion of "current."**
  Two conversations five minutes apart, if their retrieval scores land
  differently, can produce two different `current_objective`s with no
  contradiction ever surfaced — there is no `version`/watermark to detect
  that the "objective" changed between calls that weren't really about a
  new objective at all, just a different ranking outcome. `PROJECT_STATE_DESIGN.md`
  §8's `STATE_DRIFT` failure mode was scoped for the *persisted*, Phase B
  design and correctly noted as not applicable to Phase A "since there is
  no persisted prior state for anything to drift from"
  (`project_state.py:78-90`) — but the flip side of that honest disclaimer
  is that Phase A has no drift detection *at all*, persisted or not; a
  project growing from 5 to 500 conversations gets progressively noisier
  `ProjectState` output with no instrumentation surfacing that it's
  happening.
- **The triplication identified in §3 scales with content, not with query
  volume**, but it compounds the noise problem above: a mature project with
  a healthy `decisions`/`active_tasks` history produces a
  `CONTINUATION`-mode prompt where a meaningful fraction of the token
  budget is spent re-stating the same handful of facts three times, leaving
  less of the flat 50-candidate budget for anything else — a second-order
  effect where the redundancy itself accelerates the crowding-out problem
  above.

**Conclusion:** Phase A is safe and cheap in isolation (it adds no I/O, no
LLM calls, and is verified byte-identical when absent), but its *content
quality* — not its cost — degrades as project size grows, for reasons
`PROJECT_STATE_DESIGN.md` already anticipated needing Phase B (incremental
materialization with per-category top-K, §4 of that document) to fix. That
diagnosis holds up against the actual code: nothing in Phase A's own scope
mitigates it.

---

## 8. Is ProjectState sufficiently explainable? Can every field trace to supporting memories?

**Yes, for provenance — every populated field traces cleanly.** Every
`StateRef` carries `knowledge_object_id` (`project_state.py:187`), and
`current_objective`'s `ProjectStateField.source_ids` explicitly names the
backing `KnowledgeObject` (`project_state.py:635`). The renderer reuses one
shared `[N]` index across `ProjectState` and every `WorkingContext` bucket
(`structured_prompt_builder.py:287-305`), so a downstream model — or a
human auditor — can always resolve `<ProjectState>`'s claims back to a
concrete, fully-attributed `<Memory>` element elsewhere in the same prompt.
Determinism is independently verified (`TestProjectStateBuilderDeterminism`,
`test_project_state.py:543-577`; shuffled-input-order equality). This part
of the design is sound and the tests back it up.

**No, for the *absence* signal and for two rendering-level claims:**

- **`gaps` cannot explain itself.** As established in §1(a)/§3/§7, an empty
  field traces back to "not present in `allocated`," but nothing tells a
  reader *why* it's absent — no vault data, or crowded out by ranking. The
  field is deterministic and reproducible (the same input always gives the
  same `gaps`), but reproducibility is not the same as explainability: a
  reader cannot distinguish "Haven has no blocker on record" from "Haven
  has a blocker but it scored 51st."
- **`confidence` explains a number but not what it means**, per §3/§6 — it
  is traceable to its formula, but a downstream model has no way to
  discover, from the rendered XML alone, that this `confidence` differs in
  kind from every `<Memory>` element's `confidence` attribute two lines
  below it.
- **One genuine documentation/implementation mismatch worth flagging
  directly, since it affects explainability of the system to future
  maintainers, not just to a downstream LLM:** `query_structured`'s own
  docstring states *"`StructuredPromptBuilder.render` accepts the parameter
  but does not yet render it (no XML shape change in this phase)"*
  (`engine.py:1185-1189`). This is stale — `StructuredPromptBuilder.render`
  **does** render `<ProjectState>` today
  (`structured_prompt_builder.py:270-271, 311-334`), confirmed by
  `TestProjectStateRendering` actually asserting `<ProjectState` appears in
  `query_structured`'s output (`test_engine.py:2650-2652`). The
  `PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md` design doc's own header
  already flags this exact drift at the document level ("Status:" note at
  the top of that file) — but `engine.py`'s in-code docstring was not
  updated to match, which is precisely the kind of drift that erodes
  explainability for the next person reading source instead of design docs.
  A one-line fix, called out here because it directly undermines this
  section's premise ("can this be traced") for anyone reading code as the
  source of truth, which this document's own instructions required.

---

## 9. Recommended improvements, ranked

Ranked by expected improvement to continuation quality (↑), implementation
complexity (low/med/high), and architectural risk (low/med/high). All are
deterministic; none require inference, persistence, or write-side
(Extractor) changes unless noted.

| # | Recommendation | Continuation-quality impact | Complexity | Risk |
|---|---|---|---|---|
| 1 | Reorder `<ProjectState>`'s list sections: `Blockers`/`Constraints` immediately after `CurrentObjective`, `SupersededDecisions` moved to just before `Gaps` (§6) | High — matches how humans actually prioritize on resume | Low (reorder one fixed tuple) | None |
| 2 | Add `recent_discoveries` (`RESEARCH` category) as a tracked field (§4) | Medium-high — closes the one clean `ContextPlan`/`ProjectState` category mismatch | Low (mirrors every existing `MEMORY_DIRECT` field) | Low |
| 3 | Fix the stale `query_structured` docstring claiming `<ProjectState>` isn't rendered (§8) | None to the model; high for maintainer trust in source-as-truth | Trivial | None |
| 4 | Suppress `WorkingContextState`'s summary fields when a `ProjectState` is already rendered in the same prompt (§3, §6) | Medium — frees token budget, removes ambiguity about which copy is authoritative | Low-medium (conditional render path, additive, gated on existing `CONTINUATION` check) | Low |
| 5 | Rename/annotate `<ProjectState confidence="...">` to something like `field_coverage` to disambiguate from per-`Memory` `confidence` (§3, §6) | Low-medium — mostly a clarity fix for the consuming model | Trivial | None |
| 6 | Thread `ranked_all` (already computed by `_run_retrieval`) into `ProjectStateBuilder.build` so `gaps` can distinguish "absent from vault" from "excluded by budget" (§4, §7) | High — this is the single fix that makes `gaps` trustworthy at scale | Medium (new parameter, new comparison logic, needs its own tests) | Low-medium (touches a currently-pure, well-tested function's signature) |
| 7 | Wire `CategoryPreferenceScorer` into `_allocate()`/`query_structured` for `CONTINUATION`-mode queries specifically, so the plan's own category requirements actually influence what `ProjectState` sees (§1b) | Highest — this is the fix with the most leverage on §1's core finding | High (the "Deliberately not planner-aware" scoping in `engine.py:1103-1114` was an explicit prior decision; reversing it for one task mode needs its own review, and changes ranking output for `query_working_context`/`query_structured` on `CONTINUATION` queries, which is a real behavior change, not purely additive) | Medium — changes ranking output for an existing call path, which the current design deliberately avoided |

Recommendations 1–5 are essentially free and should ship together as one
pass. 6 is the highest-value fix that stays inside Phase A's own boundaries
(no persistence, no inference). 7 is the highest-value fix overall but is
explicitly the one item here that is not purely additive — it reopens a
decision the codebase already made deliberately, and should be scoped and
reviewed as its own change, not bundled with the others.

---

## 10. Roadmap

**Quick Wins (low complexity, low/no risk, ship together):**
- Reorder `<ProjectState>`'s rendered sections (blockers/constraints first,
  superseded-decisions last).
- Fix the stale `query_structured` docstring.
- Disambiguate `confidence`'s XML attribute name/label from per-`Memory`
  `confidence`.
- Add `recent_discoveries` as a tracked, `MEMORY_DIRECT` field, closing the
  `RESEARCH`/`ContextPlan` mismatch.

**Medium Improvements (real code change, still additive, still no
persistence/inference):**
- Suppress `WorkingContextState`'s summary fields when `ProjectState` is
  already present in the same rendered prompt, removing the verified
  triplication (§3) without losing content.
- Thread `ranked_all` into `ProjectStateBuilder.build` so `gaps` can
  honestly distinguish "no such memory in the vault" from "excluded by this
  run's ranking budget" — and update the rendered `<Gaps>` element (or its
  documentation) to reflect the distinction once available.

**Long-Term Ideas (require crossing into persistence/inference/plan-
awareness — each is a separately-scoped decision, not a next sprint):**
- Reconsider whether `CategoryPreferenceScorer` should apply to
  `CONTINUATION`-mode `query_structured` calls specifically (recommendation
  7) — the highest-leverage fix to §1's core finding, deliberately not
  bundled with anything additive above because it changes existing ranking
  output.
- `PROJECT_STATE_DESIGN.md` Phase B (incremental materialization,
  persistence, per-category top-K at write time) — the prerequisite for
  §7's scaling concerns to actually resolve, since Phase A's flat top-50
  recompute-per-call model cannot fix category-crowding through rendering
  or field changes alone.
- `PROJECT_STATE_DESIGN.md` Phase D (`identity`, `phase`,
  `current_objective` tie-break) — explicitly gated, correctly, on Phase A/B
  being solid first; §5's finding that these are exactly the fields humans
  reach for *first* when resuming makes this valuable, but it is inference
  and therefore out of this document's deterministic-only scope by design.
