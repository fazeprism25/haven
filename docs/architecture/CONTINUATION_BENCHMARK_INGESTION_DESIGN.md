# Continuation Benchmark — Ingestion Design

Status: **implemented**, exactly as recommended in §10. See "Implementation
status" at the end of this document for what shipped, the tests added, and
one thing worth knowing before trusting a `resume_coding` number this
pipeline produces (a retrieval-seeding limitation the pilot dataset's own
two stock queries run into, independent of this design). Everything above
that section is the frozen design this implementation was built against,
unchanged.

Answers the ten questions
posed after `docs/architecture/CONTINUATION_BENCHMARK_AUDIT.md`'s
Critical-1 finding: the continuation benchmark ingests every conversation
turn through `HavenAdapter.add()`, which hardcodes `MemoryType.FACT`
(`benchmarks/adapters/haven_adapter.py:242-247`), so `ProjectStateBuilder`
never receives a `GOAL`/`DECISION`/`TASK`/`BLOCKER`/`RULE`-typed candidate
and every `<ProjectState>` the benchmark observes is a structurally empty
shell. Grounded against the actual working tree: `benchmarks/adapters/
{base,haven_adapter,haven_full_adapter,baselines}.py`,
`benchmarks/runners/{run_benchmarks,run_continuation_benchmarks}.py`,
`obsidian/manager_ai/{pipeline,classifier,canonical_matcher,
knowledge_updater,models}.py`, `obsidian/memory_engine/{project_state,
coverage_analyzer,engine}.py`, `obsidian/docs/TECH_DEBT.md`,
`obsidian/tests/test_project_state.py`. Nothing below was taken on the
audit's or design doc's word alone — every claim was re-checked directly.

---

## 0. A second defect the audit didn't surface, and why it matters here

The audit's fix-(a) option — route continuation ingestion through Haven's
real write pipeline (`HavenFullAdapter` / `ManagerPipeline`, which already
exists and is already wired into `run_benchmarks.get_adapter_cls` as
`"haven_full"`) — reads as the "honest" fix: real `Extractor` →
`Classifier` → ... → `KnowledgeUpdater`, no shortcuts. It is not sufficient
on its own, for a reason the audit didn't check:
`CanonicalMatcher.match_with_target` **never returns `SUPERSEDE`**
(`obsidian/manager_ai/canonical_matcher.py:50`, docstring: "`SUPERSEDE` is
intentionally never returned here — driving true write-time supersession is
deferred work", confirmed against `obsidian/docs/TECH_DEBT.md:31-36`,
listed as unstarted "Stage 3"). `DecisionMetadata.status` only ever becomes
`SUPERSEDED` via `KnowledgeUpdater.supersede_decision()`
(`obsidian/manager_ai/knowledge_updater.py:284-349`), a method nothing in
`ManagerPipeline.process`/`match_and_apply` calls automatically — it exists
for an explicit, separately-invoked API path, not conversational ingestion.

So even a `HavenFullAdapter`-based continuation ingestion path, with a real
LLM correctly classifying turn 1 as a rejected `DECISION` and turn 2 as its
replacement, would produce **two `MemoryType.DECISION` objects, both with
`DecisionMetadata.status == ACTIVE`** (or, for decisions written before this
feature, no `DecisionMetadata` at all — `project_state.py`'s own docstring:
"no `DecisionMetadata` renders like any other decision"). `ProjectState
Builder.build` (`project_state.py:564-570`) would route both into
`decisions`, never into `superseded_decisions`. The exact mechanism §4/§8 of
the design doc calls out as the worked example — "whether `<Decisions>`
correctly shows only the blended-score decision... and not the rejected
embedding-only one" — would **still** not be observable, for a different
reason than Critical-1: not because nothing is typed `DECISION`, but because
nothing is ever typed `SUPERSEDED`. This is decisive for §7/§10 below: it
rules out "just use the real pipeline" as a complete fix by itself,
independent of the cost/latency/nondeterminism concerns the audit already
named.

---

## 1. What is the correct ingestion path for a continuation benchmark?

**Deterministic, metadata-driven typed ingestion — not the real
`ManagerPipeline`, and not `HavenAdapter.add`'s current verbatim-`FACT`
storage.** Concretely: a continuation-only ingestion path that reads each
dataset turn's already-authored `turn_type` (and `supersedes_turn` /
`resolves_turn`) and constructs the `KnowledgeObject` directly —
`memory_type` set from `turn_type`, `DecisionMetadata` set from
`supersedes_turn`, `valid_until` set on a resolved blocker's original
object from `resolves_turn` — with **no LLM call**. This is audit
fix-(b), and per §0 above it is not merely the cheaper of two valid options;
it is the only one of the two that actually produces a `SUPERSEDED`
decision or a closed-out blocker at all, since the real pipeline's write-time
supersession is a deferred no-op.

This does mean revisiting the current field note in
`CONTINUATION_BENCHMARK_DESIGN.md` §4 — "`turn_type` is authored metadata
... it is never given to the adapter" — explicitly, as the audit already
flagged. §2 below explains why that reversal is the right call rather than
a compromise.

## 2. Should continuation benchmarks intentionally use the full write pipeline?

**No.** Three independent reasons, any one of which would be sufficient
alone:

- **It doesn't work for the one thing this benchmark is built around.**
  §0: write-time supersession is a deferred no-op, so the real pipeline
  cannot produce a `SUPERSEDED` decision or a closed blocker from ordinary
  conversational ingestion regardless of how well the LLM classifies.
- **It conflates two independently fallible systems.** This benchmark's own
  stated philosophy (`CONTINUATION_BENCHMARK_DESIGN.md` §1: "starts from the
  assumption that retrieval mostly works and asks the harder question...
  does *reconstruction* work") already draws this line for retrieval; it
  should draw the same line for classification. If a case fails, a real
  `Classifier` call in the loop leaves it ambiguous whether
  `ProjectStateBuilder`/`StructuredPromptBuilder` reconstructed badly, or
  the `Classifier` mis-typed a fact as `FACT` instead of `DECISION` in the
  first place (a real, LLM-dependent, nondeterministic failure mode
  unrelated to reconstruction quality). Deterministic typing removes that
  confound entirely — a continuation-benchmark failure can only mean the
  reconstruction/rendering layer got it wrong, never that the classifier did.
- **Cost, latency, nondeterminism.** Already named in the audit
  (Critical-1, fix-(a) bullet): re-running LLM classification on every
  conversation turn, for every case, on every benchmark run, in addition to
  Stage B's continuation-model call and Stage C's judge call, is expensive
  and reintroduces run-to-run variance into a pipeline whose Stage B/C are
  already deliberately pinned to `temperature=0` for reproducibility.

## 3. Should retrieval benchmarks continue using verbatim `FACT` storage?

**Yes, unchanged.** `benchmarks/adapters/haven_adapter.py`'s `add()` and the
base suite's `decision_reconstruction`/`supersession`/`contradictions`
categories are not in scope for this fix and should not be touched. Per
`CONTINUATION_BENCHMARK_DESIGN.md` §1, those categories ask "did the system
surface the right fact(s)" over a flat joined string — a legitimate,
already-working question that verbatim `FACT` storage answers correctly
today, and the existing `benchmarks/tests/` suite (195 tests per
`CONTINUATION_BENCHMARK_DESIGN.md` §11) already depends on
`run_benchmarks.py`'s adapter-parity contract not moving. `haven_full` also
already exists as a separate, opt-in registry entry
(`run_benchmarks.py`'s `get_adapter_cls`) for anyone who wants to benchmark
the base suite against the real write pipeline instead — that path is
untouched by anything here.

## 4. Should there be two Haven adapters?

**Yes — a third one, following the exact pattern `haven`/`haven_full`
already establish.** `HavenFullAdapter` already subclasses `HavenAdapter`
to override only the write path while inheriting the read path verbatim
(`haven_full_adapter.py`'s own docstring: "The read path... MUST behave
identically to Haven Retrieval, so it is inherited unchanged"). A new
`HavenContinuationAdapter(HavenAdapter)` should do the same: override
`add_conversation` (not `add` — see §5) to perform deterministic typed
ingestion, inherit `search`/`delete_all`/`from_config`/
`build_continuation_context` unchanged. This keeps the change additive and
mirrors an already-reviewed precedent instead of inventing a new shape.

Critically, this new adapter must **not** be added to
`run_benchmarks.get_adapter_cls`'s registry — it has no meaning for the
base suite (it requires `turn_type`/`supersedes_turn`/`resolves_turn`
metadata the base suite's dataset schema doesn't carry) and adding it there
would risk exactly the "disturbing the base suite's adapter-parity
contract" the audit's fix-(a) bullet warned against. It should be resolved
only inside `run_continuation_benchmarks.py`, e.g. a small local registry
mapping `"haven"` (the continuation runner's own default) to
`HavenContinuationAdapter`, distinct from and not reusing
`run_benchmarks.get_adapter_cls` for Haven specifically — `main()`'s current
`from benchmarks.runners.run_benchmarks import get_adapter_cls` (line 305)
would need to special-case Haven rather than delegating for every adapter
name. (Non-Haven names — `mem0`, `return_all`, `recency`, `bm25`,
`embedding` — still resolve exactly as today; see §5's last paragraph for
why they need no equivalent change.)

## 5. Should there instead be two ingestion modes?

**No — this is the same question as §4 phrased differently, and the answer
converges on "one adapter subclass per mode," not "one adapter with a mode
flag."** A boolean/enum flag on `HavenAdapter.add_conversation` (e.g.
`typed_ingestion: bool = False`) would work mechanically but breaks the
established convention this codebase already uses to express "same read
path, different write path" — `HavenFullAdapter` — and would make
`HavenAdapter` responsible for a concern (mapping `turn_type` →
`MemoryType`) that has nothing to do with what `HavenAdapter` exists to do
(mirror `mem0.Memory`'s verbatim-storage contract, per its own module
docstring). Keeping it a separate subclass, resolved only by the
continuation runner's own adapter selection, means `HavenAdapter` and
`HavenFullAdapter` — and every base-suite test that constructs and asserts
against them — are provably untouched: nothing about their behavior
changes, because no line inside either class changes.

## 6. Where should inference happen?

**Nowhere, for this benchmark's ingestion — inference is replaced by
dataset-authored ground truth, deterministically applied.** This is the
load-bearing design choice, so it's worth stating precisely what "no
inference" means here and what it doesn't:

- `turn_type` → `MemoryType`: a fixed dictionary lookup
  (`architecture_discussion`/`implementation`/`distractor` → `FACT`;
  `decision` → `DECISION`; `constraint` → `RULE`; `blocker` → `BLOCKER`;
  `task` → `TASK`; `rejected_approach` → `FACT` — see §9 for why rejected
  approaches stay `FACT`, not a new type). No LLM, no judgment call.
- `supersedes_turn` → `DecisionMetadata(status=SUPERSEDED, ...)` written
  onto the **original** turn's `KnowledgeObject` (looked up by turn index,
  already tracked during ingestion) — mirroring exactly what
  `KnowledgeUpdater.supersede_decision` would do if write-time supersession
  existed, minus the LLM call to decide *that* it should happen (the
  dataset already encodes that decision at authoring time, which is the
  entire point of `supersedes_turn` existing in the schema per
  `CONTINUATION_BENCHMARK_DESIGN.md` §4's field notes).
- `resolves_turn` (on a `blocker`-resolving `decision` turn) → sets
  `valid_until = <this turn's timestamp>` on the **original blocker's**
  `KnowledgeObject`, so `engine._active_candidates` (`engine.py:535-562`)
  excludes it from acceptance exactly as it already does for any
  `valid_until`-expired memory — no new exclusion mechanism, reusing an
  already-existing, already-tested gate.
- This is real inference the dataset author already did by hand when they
  wrote `ground_truth`/`expected` (`CONTINUATION_BENCHMARK_DESIGN.md` §4:
  "`ground_truth` is the single source of truth; `expected`'s rubric fields
  are mechanically derivable from it"). Applying `turn_type`/
  `supersedes_turn`/`resolves_turn` deterministically at ingestion is
  exposing metadata that already exists in the case file, not inventing new
  judgment Haven itself never made.

**What stays exactly as-is:** Stage B (continuation generation) and Stage C
(judging) are both untouched by this — they already don't see `turn_type`
(only Stage A's rendered `<ProjectState>`/`<WorkingContext>` XML reaches
Stage B, and only Stage B's response reaches Stage C), so nothing about
*those* stages leaks ground truth. The only thing this design changes is
what `MemoryType` a turn is stored as — a fact about the write path, never
visible to the generation or judging stages.

## 7. What changes minimize benchmark contamination?

"Contamination" here means: does giving Haven deterministically-correct
typing let it pass for a reason that has nothing to do with the
reconstruction mechanism under test? Two angles:

- **Within Haven's own run:** no — `ProjectStateBuilder`,
  `StructuredPromptBuilder`, `DeterministicRanker`, and
  `DeterministicSlotAllocator` are all still real, unmodified, and still
  make every actual reconstruction/tiering/staleness-resolution decision the
  benchmark exists to test. Deterministic typing only supplies the *input*
  those components already expected to receive from a working write
  pipeline (per `CONTINUATION_BENCHMARK_DESIGN.md`'s own framing: "assume
  retrieval mostly works," extended here to "assume classification mostly
  works," per §2). It does not touch `_build_current_objective`, the
  `DecisionStatus.SUPERSEDED` branch, `_CATEGORY_TO_FIELD` routing, or any
  rendering logic in `structured_prompt_builder.py`.
- **Relative to baselines:** also no, for a reason worth stating explicitly
  since it's the audit's own Critical-1 answer to question 8 (flagging this
  exact risk). `BaseAdapter.build_continuation_context`'s default fallback
  (`base.py:143-165`) is a flat `search()` + join for every adapter that
  doesn't override it — `mem0`, `return_all`, `recency`, `bm25`,
  `embedding` all fall back to this, and **none of them consume
  `MemoryType` or any typed structure at all**, deterministically-injected
  or otherwise. A flat-join baseline gains nothing from Haven's turns being
  typed correctly, because nothing in its own path reads the type. The
  comparison this benchmark draws — "does Haven's *structured* rendering
  beat a *flat* one" — is not affected by how Haven's structure was
  populated, only by whether it exists and whether it's correct. Giving
  Haven deterministic typing is not a differential advantage; it is the
  precondition for Haven's differentiator (structure) to be exercised at
  all. (This is exactly the audit's Critical-1 answer-to-Q8 concern,
  addressed: "if Critical-1 is fixed without a matching real-extraction path
  for baselines, that would become a genuine hidden assumption" — the
  answer here is that baselines were never going to use typed information
  regardless, so no matching path is owed.)

The one discipline this design must hold to avoid contamination: the
deterministic mapping must be driven **only** by `turn_type`/
`supersedes_turn`/`resolves_turn` — fields already in `conversation`, never
by peeking at `ground_truth`/`expected` directly. `ground_truth` remains the
judge's answer key, untouched by ingestion; `turn_type` is dataset-author
metadata about the conversation, the same status quo distinction
`CONTINUATION_BENCHMARK_DESIGN.md` §4 already draws for every other field.

## 8. Which design best reflects real Haven usage?

Neither option is a faithful simulation of real usage in every respect —
worth being honest about the tradeoff rather than picking the option that
sounds more "real" by name:

- The real pipeline (§2's rejected option) reflects real usage in *stage
  sequence* (Extractor → Classifier → ... → write) but **misreflects** real
  usage in *outcome*, because real production supersession is also
  incomplete (§0) — so "real pipeline" ingestion would actually simulate a
  Haven that never correctly resolves a stale decision in production
  either, which is true today but is a production gap, not a
  reconstruction-quality question this benchmark is built to isolate.
- Deterministic typed ingestion (the recommended option) reflects real
  usage in *outcome* — it produces exactly the `KnowledgeObject`/
  `DecisionMetadata` shape a correctly-classified, correctly-superseded
  write path *would* produce, once that pipeline gap is eventually closed —
  but not in *mechanism* (no LLM call performs the typing).

Given `CONTINUATION_BENCHMARK_DESIGN.md` §1's own framing — this benchmark
tests reconstruction, explicitly *not* retrieval or (by the extension this
design proposes) classification — outcome-fidelity is the correct axis to
optimize, not mechanism-fidelity. The benchmark's entire value proposition
is testing what `ProjectStateBuilder`/`StructuredPromptBuilder` do with
correctly-typed input; simulating a step that (a) isn't the thing under
test and (b) is independently known-broken in a way unrelated to
reconstruction quality would only add noise, not realism.

## 9. Which design minimizes implementation complexity?

Deterministic typed ingestion, decisively:

- **No LLM client wiring for Stage A.** The real-pipeline option needs
  `ManagerAILLM`/`Extractor`/`Classifier` construction in the continuation
  runner path (mirroring `HavenFullAdapter.__init__`), plus retry/error
  handling for classification failures mid-benchmark-run
  (`ClassificationError` — `classifier.py:20-32` — already a real,
  documented per-fact skip path in production; a continuation case losing a
  fact to a classification retry failure would silently degrade its own
  `ground_truth` coverage in a way that's invisible without deep
  inspection).
- **No new supersession-drive logic.** §0 already establishes the real
  pipeline can't produce `SUPERSEDED` at all without *also* writing new code
  to call `KnowledgeUpdater.supersede_decision()` from somewhere in the
  ingestion path — which itself only exists as a manually-invoked API
  method today, not a pipeline stage. Building that wiring is genuinely new
  work (effectively re-opening `obsidian/docs/TECH_DEBT.md`'s deferred
  Stage 3), well outside a benchmark-ingestion-only fix's scope. Deterministic
  typing needs none of this — it sets `DecisionMetadata`/`valid_until`
  directly, the same way `obsidian/tests/test_project_state.py`'s own
  `make_ko`/`DecisionMetadata(status=DecisionStatus.SUPERSEDED)` fixtures
  already do (`test_project_state.py:452-479`) for unit-testing
  `ProjectStateBuilder` — this design generalizes an already-proven test
  pattern into the benchmark's ingestion path, not a new technique.
- **Small, local surface.** One new adapter subclass
  (`HavenContinuationAdapter(HavenAdapter)`) overriding one method
  (`add_conversation`), plus a `turn_type` → `MemoryType` dict and a
  same-conversation lookup of the referenced turn index for
  `supersedes_turn`/`resolves_turn`. No change to `BaseAdapter`,
  `HavenAdapter`, `HavenFullAdapter`, `ProjectStateBuilder`,
  `StructuredPromptBuilder`, `run_benchmarks.py`, or any base-suite dataset
  or test.

## 10. Recommended architecture

**Add `HavenContinuationAdapter(HavenAdapter)` in
`benchmarks/adapters/haven_continuation_adapter.py`, overriding only
`add_conversation` to perform deterministic, dataset-metadata-driven typed
ingestion (§6) with no LLM call. Wire it into
`run_continuation_benchmarks.py` only — not into
`run_benchmarks.get_adapter_cls` — as the continuation runner's `"haven"`
resolution. Leave `HavenAdapter`, `HavenFullAdapter`, `BaseAdapter`,
`ProjectStateBuilder`, `StructuredPromptBuilder`, the base suite, and every
existing dataset/test byte-for-byte unchanged.**

Why this is the one architecture, not a compromise between two live
options:

1. It is the only option that actually produces a `SUPERSEDED` decision or
   a closed-out blocker (§0) — the real pipeline cannot, today, regardless
   of implementation effort spent on it, without first closing a deferred
   production gap that is out of scope for a benchmark fix.
2. It cleanly separates what this benchmark tests (reconstruction) from
   what it doesn't (retrieval, already excluded per the design doc's own
   §1; classification, newly excluded here per §2) — matching the
   benchmark's own stated philosophy rather than fighting it.
3. It follows an already-established, already-reviewed codebase pattern
   (`HavenAdapter` → `HavenFullAdapter` as sibling write-path overrides of a
   shared read path) instead of introducing a new shape, and reuses an
   already-proven test technique (`test_project_state.py`'s hand-built
   typed `KnowledgeObject`/`DecisionMetadata` fixtures) instead of a novel
   one.
4. It does not require, and specifically must not acquire, a matching
   change to any baseline adapter (§7) — baselines' flat-join fallback
   already ignores `MemoryType` entirely, so the comparison this benchmark
   draws stays apples-to-apples without extra work on that side.
5. It costs one new small file and one runner-level adapter-resolution
   change — no LLM wiring, no new supersession-drive logic, no touch to any
   component the base suite or its 195 existing tests depend on.

**Explicitly not recommended, and why:** giving the continuation runner a
flag to switch `HavenAdapter` itself between verbatim and typed storage
(§5) — conflates two adapters' responsibilities into one class against the
codebase's own established convention. Routing continuation ingestion
through `HavenFullAdapter`/`ManagerPipeline` (audit fix-(a), §2/§8/§9
above) — doesn't produce a correct result for the central mechanism under
test, conflates two fallible systems, costs real LLM latency/nondeterminism
per run, and would require materially new (and out-of-scope) work to even
attempt closing the supersession gap first.

**What this design does not address**, because it's out of scope for an
ingestion-path fix and was already correctly scoped out by the audit:
Critical-2 (`must_prioritize` confounded with recency-of-mention),
Critical-3 (ten cases as one template), High-1/2/3 (keyword-prefix leakage,
easy distractors, self-referential pilot content), and the Medium/Low
findings. Those are dataset-authoring and judge-prompt concerns for Phase 4
of `CONTINUATION_BENCHMARK_DESIGN.md`'s roadmap, independent of whichever
ingestion path is chosen here — fixing ingestion is a precondition for
those numbers to mean anything, not a substitute for re-authoring the pilot
set.

---

## Implementation status

§10's recommended architecture, implemented exactly as specified — no
architectural deviation from the frozen design above.

**Files added:**
- `benchmarks/adapters/haven_continuation_adapter.py` — new.
  `HavenContinuationAdapter(HavenAdapter)`, overriding only
  `add_conversation`. A fixed `turn_type -> MemoryType` dictionary (§6) with
  one addition beyond §6's own illustrative list: `open_question ->
  MemoryType.OPEN_QUESTION`. §6 enumerated six turn_types drawn from
  `CONTINUATION_BENCHMARK_DESIGN.md` §4's worked example, which happens not
  to include an `open_question` turn; the shipped pilot dataset uses one in
  every case, with the same direct, unambiguous type correspondence every
  other entry has, so it was added rather than left to fall through to the
  `FACT` default (which is what any turn_type genuinely absent from the
  table, e.g. `"note"`, still gets). No LLM client is constructed anywhere
  in this file.
- `benchmarks/tests/test_haven_continuation_adapter.py` — new, 41 tests
  across typing, supersession/resolution bookkeeping, end-to-end
  `ProjectState` reconstruction (against the real `MemoryEngine` pipeline,
  no mocking), determinism, and a regression guard proving `HavenAdapter`/
  `HavenFullAdapter`/`run_benchmarks.get_adapter_cls` are untouched.

**Files changed:**
- `benchmarks/runners/run_continuation_benchmarks.py` — added
  `get_continuation_adapter_cls`, a local resolver used only by this
  module's `main()`: `"haven"` now resolves to `HavenContinuationAdapter`;
  every other name still delegates to `run_benchmarks.get_adapter_cls`
  unchanged. Per §4/§10, `HavenContinuationAdapter` is deliberately **not**
  added to `run_benchmarks.get_adapter_cls`'s own registry — confirmed by a
  test that greps that function's source for both the class name and the
  module name and asserts neither appears.
- `benchmarks/tests/test_run_continuation_benchmarks.py` — added
  `TestGetContinuationAdapterCls` (4 tests) covering the new resolver.

**Files explicitly not touched:** `benchmarks/adapters/{base,haven_adapter,
haven_full_adapter,baselines,ablations}.py`, `benchmarks/runners/
run_benchmarks.py`, `obsidian/memory_engine/{project_state,
structured_prompt_builder,engine,coverage_analyzer}.py`,
`obsidian/manager_ai/*`, every existing dataset file. The full existing
suite (`benchmarks/tests/` — 240 tests — plus `obsidian/tests/` — 2346
tests) passes unchanged after this implementation.

**One documented, expected consequence of following §6 literally:**
`supersedes_turn` sets **both** `DecisionMetadata(status=SUPERSEDED, ...)`
*and* `valid_until` on the original turn's `KnowledgeObject` — "mirroring
exactly what `KnowledgeUpdater.supersede_decision` would do" per §6's own
wording, and `supersede_decision` sets both too. Consequence: the
superseded object is archived by `engine._active_candidates` *before*
`ProjectStateBuilder` ever sees it, so it never reaches either
`decisions` or `superseded_decisions` — it is simply absent, the same
outcome `project_state.py`'s own docstring already names as the normal
case ("a superseded decision's `valid_until` is normally set at the same
time... this field exists for the case where status and validity diverge,
not as a field expected to be routinely populated"). `<SupersededDecisions>`
therefore stays empty for every shipped `resume_coding` case — "hidden from
active state" is satisfied one step earlier (at the retrieval boundary)
than "hidden by rendering into a separate bucket," which is a stronger,
not weaker, form of the same guarantee. `benchmarks/tests/
test_haven_continuation_adapter.py::TestSupersededDecisionsBucketDivergentCase`
proves the bucket itself still renders correctly for the one case where it
would populate (status=SUPERSEDED without a matching `valid_until`),
directly against `ProjectStateBuilder`, independent of this adapter.

**Validation performed (Stage A only — no `QWEN_API_KEY` in this
environment, so Stage B/C of the full three-stage pilot runner could not be
exercised end-to-end):**

Ran ingestion + `build_continuation_context` across all 10 shipped
`resume_coding` cases directly. Two findings:

1. **The mechanism works.** Against `resume_coding_basic_001.json`, the
   query `"Continue implementing the ranker with embedding similarity
   alone."` — chosen to share heavy keyword overlap with *both* the
   rejected turn and the decision that superseded it — retrieves, before
   this change (plain `HavenAdapter`):

   ```
   <ProjectState confidence="0.00">
     <Gaps> ...all 8 field names... </Gaps>
   </ProjectState>
   ```

   (in fact `search()` alone returns exactly one result for that query: the
   rejected turn itself, "I was thinking we rank by embedding similarity
   alone, simplest thing that could work." — the single worst possible
   answer, and the exact failure mode `CONTINUATION_BENCHMARK_DESIGN.md`
   §8's worked example is built around). After this change
   (`HavenContinuationAdapter`), the same query against the same
   conversation:

   ```
   <ProjectState confidence="0.12">
     <Decisions>
       <Item>[1] Decided: score_breakdown stays internal to DeterministicRanker, ...</Item>
       <Item>[2] Embedding-only ranking won't separate a stale fact from a fresh one ...</Item>
     </Decisions>
     <Gaps> current_objective, active_tasks, blockers, constraints,
            implementation_state, code_areas, open_questions </Gaps>
   </ProjectState>
   ```

   The rejected turn ("I was thinking we rank by embedding similarity
   alone...") does not appear anywhere in the output, despite sharing more
   literal keyword overlap with the query than either surviving decision —
   it was archived at ingestion, not merely out-ranked.

2. **A separate, pre-existing limitation remains, undisturbed by this
   change.** The pilot dataset's own two shipped query phrasings
   (`"Continue implementing the project."` / `"What should we work on
   next?"`) mostly do not exercise this mechanism at all:
   `"Continue implementing the project."` retrieves zero candidates (not
   zero *typed* candidates — zero candidates, full stop) for 9 of the 10
   cases, because Haven's hybrid retrieval requires keyword/concept overlap
   with the query and "the project" is too generic to overlap with any
   case's specific technical vocabulary — this was already true of plain
   `HavenAdapter` before this change (confirmed directly: `search()` with
   that exact query against `resume_coding_basic_001.json` also returns
   zero results on `HavenAdapter`). `"What should we work on next?"` does
   retrieve real candidates (e.g. the active task) but never renders
   `<ProjectState>` at all, because it doesn't classify as
   `TaskMode.CONTINUATION` — the same `ContextPlanner` fallback-to-
   `POINTED_QA` gap `CONTINUATION_BENCHMARK_AUDIT.md` names directly (its
   own case 008 is built around this exact gap). Both are retrieval/
   classification concerns entirely outside this design's scope (§1: "the
   correct ingestion path... — not retrieval"); fixing them is Phase 4/5
   dataset-authoring work (better query phrasing, more literal keyword
   overlap with case vocabulary) or a separate `ContextPlanner` fix, not an
   ingestion change.

**Remaining future work:**
- Phase 4/5 of `CONTINUATION_BENCHMARK_DESIGN.md`'s own roadmap (unchanged
  by this task): the other seven categories, baseline/ablation wiring,
  `retrieval_coverage` diagnostics.
- Re-author (or add alongside) the pilot's two stock query phrasings with
  more literal vault-vocabulary overlap, per finding 2 above — otherwise a
  `resume_coding` pass-rate number will mostly measure retrieval seeding,
  not reconstruction, defeating the point this design's typed ingestion was
  built to unblock.
- The `ContextPlanner` `POINTED_QA`-fallback gap for orientation-seeking
  phrasings ("What should I do next?" and close variants) — a real,
  separately-scoped `ContextPlanner` fix, not an ingestion concern.
- Running the full three-stage pilot (Stage B/C) with a real `QWEN_API_KEY`
  to get actual pass-rate numbers was not possible in this environment; the
  above is Stage-A-only validation.
