# Continuation Benchmark — Independent Audit

Scope: does the Phase 1 pilot (`docs/architecture/CONTINUATION_BENCHMARK_DESIGN.md`
§11) actually measure what it claims to measure — project-continuation
*reconstruction*, not retrieval, not verbosity, not lexical luck? This is an
audit of the benchmark, not of Haven's score on it. Every claim below was
verified directly against the current working tree — file paths and line
numbers are given so each can be re-checked. Nothing was modified.

Read: `docs/architecture/CONTINUATION_BENCHMARK_DESIGN.md`,
`benchmarks/runners/run_continuation_benchmarks.py`,
`benchmarks/judges/continuation_judge.py`, `benchmarks/adapters/base.py`,
`benchmarks/adapters/haven_adapter.py`, all 10
`benchmarks/datasets_continuation/resume_coding/*.json` cases, and — because
the design doc's own claims about Haven's behavior needed checking against
the actual pipeline, not taken on faith —
`obsidian/memory_engine/{context_planner,project_state,engine,
working_context_builder,structured_prompt_builder,coverage_analyzer}.py`,
`obsidian/ontology/retrieval_models.py`, `benchmarks/tests/
{test_run_continuation_benchmarks,test_haven_adapter}.py`.

## Bottom line

**No — the pilot's numbers, if run today, would not demonstrate what the
design document claims.** Not because Haven scores badly, but because the
one mechanism the whole benchmark exists to test — Haven's decision/blocker/
task reconstruction — is never invoked. This is a benchmark-harness defect,
not a Haven capability gap, and it is fixable without touching Haven's
algorithms. Three further problems compound it: the ten pilot cases are one
structural template wearing ten different vocabularies, not ten independent
samples; a fixed keyword-prefix convention in the dataset text leaks the
exact category signal the benchmark claims only an ontology-aware system can
extract; and the `must_prioritize` axis cannot currently be distinguished
from "always pick whatever was mentioned last."

---

## Critical findings

### Critical-1 — Haven's own decision/blocker/task reconstruction never fires in this benchmark

**Where:** `benchmarks/adapters/haven_adapter.py:242-247` (`HavenAdapter.add`
hardcodes `memory_type=MemoryType.FACT` for every ingested turn, `infer`
accepted but not enforced) → `obsidian/ontology/retrieval_models.py:1614-1628`
(`_MEMORY_TYPE_ROLE`: `MemoryType.FACT → MemoryRole.RESEARCH`) →
`obsidian/memory_engine/coverage_analyzer.py:113-121` (`resolve_category`:
`MemoryType.FACT → ContextCategory.RESEARCH`) →
`obsidian/memory_engine/project_state.py:487-494` (`_CATEGORY_TO_FIELD` has
no `RESEARCH` entry) and `:560-575` (`ProjectStateBuilder.build` only
populates `current_objective`/`decisions`/`superseded_decisions` from
`MemoryType.GOAL`/`MemoryType.DECISION` candidates) →
`obsidian/memory_engine/structured_prompt_builder.py:495-519`
(`_decision_attrs` returns `""` for any non-`MemoryType.DECISION` object,
i.e. the `status`/`supersedes`/`superseded_by` XML attributes never render).

**What's happening:** the continuation runner ingests every conversation
turn through `HavenAdapter.add()`, which — per the design doc's own §3
("Reusing `run_benchmarks.py`... `add()`... is not what's different about
this benchmark") and the adapter's own module docstring ("no extraction,
classification, or supersession logic runs") — stores *every* turn as
`KnowledgeObject(canonical_fact=content, memory_type=MemoryType.FACT)`,
unconditionally. This is correct and intentional for the base suite's
`infer=False`-means-verbatim-storage contract. But `ProjectStateBuilder` —
the exact component §2 Q6/Q7, §7's worked example, and §8's step 2 all name
as *the* thing under test — only recognizes `MemoryType.GOAL` and
`MemoryType.DECISION` objects. Since nothing ingested through this benchmark
is ever typed `DECISION` (or `GOAL`, `TASK`, `RULE`/constraint, `BLOCKER`),
`ProjectStateBuilder.build()` traces every allocated candidate to
`ContextCategory.RESEARCH`, which `_CATEGORY_TO_FIELD` does not track at
all. The result, for **every one of the 10 pilot cases, on every query**:
`current_objective=None`, `decisions=()`, `superseded_decisions=()`,
`active_tasks=()`, `blockers=()`, `constraints=()`, `gaps` = all 8 field
names, `confidence=0.0`. Even when a query correctly classifies as
`TaskMode.CONTINUATION` (§4's `"Continue implementing the project."` does —
verified against `context_planner.py`'s `_MODE_PATTERNS`, "continue" matches
first), the `<ProjectState>` element `query_structured()` attaches
(`engine.py:1260-1262`) is a structurally empty shell. Separately, the
`<Memory status="..." supersedes="..." superseded_by="...">` attributes
`StructuredPromptBuilder._decision_attrs` would add to a genuine decision
(the mechanism `benchmarks/BENCHMARK_AUDIT.md`'s Critical-1 fix, case 006's
own dataset content, was built around) also never appears, for the same
reason — no ingested object is ever `MemoryType.DECISION`.

**Why it matters:** §1 states the benchmark's whole premise is "retrieval
mostly works... does *reconstruction* work." §8's worked example walks
through exactly this scenario — "whether that block's `<Decisions>`
correctly shows only the blended-score decision... and not the rejected
embedding-only one" — as the thing Phase 3 would observe. It cannot observe
it: there is no `<Decisions>` content to be correct or incorrect about, for
Haven or for any adapter, because the write path this benchmark uses never
produces a typed decision. Whatever Stage B receives from Haven today is
WorkingContext's ontology-concept-grouped bucket of undifferentiated
"research" facts, ordered by `DeterministicRanker`'s blended score (recency
+ confirmation + embedding similarity) — i.e., Haven is running this
benchmark as a *flat-retrieval-with-good-ranking* system, structurally
identical in kind (not necessarily in quality) to the `recency`/`embedding`
baselines the design doc says this benchmark exists to beat. Any score
difference observed between `haven` and a baseline on this pilot today
reflects ranking/grouping quality, not the staleness-resolution mechanism
the design's every worked example, every mechanism in §10, and the entire
"why this needs a second pipeline" argument in §3 is built around.

**Severity:** Critical. **Likelihood this already affects any run:** Certain
— this is not a probabilistic risk, it is the current, unconditional
behavior of the code path a `haven` run takes. **Why untested:**
`benchmarks/tests/test_run_continuation_benchmarks.py` exercises the runner
only against `FakeContinuationAdapter` (an in-memory stand-in, never real
`HavenAdapter`) — see that file's own docstring ("stubbed Stage B... Stage
C... so these tests need neither a running Qdrant/Ollama nor a real Qwen
Cloud API key"). No test in the added suite constructs a real `HavenAdapter`
and inspects its `<ProjectState>` output for a continuation case, so this
would not have been caught by the test suite as it stands.

**Effort to fix:** Medium, and genuinely ambiguous which fix is "correct" —
this is a real design tension, not just an oversight:
- **(a)** Give the continuation runner (only — not the base suite, to avoid
  disturbing its adapter-parity contract) an ingestion path that runs
  Haven's real `Classifier`/decision-supersession logic (i.e., something
  closer to `infer=True`/`ManagerPipeline`, which `haven_full_adapter.py`
  already wires up for the base suite) instead of `infer=False` verbatim
  storage. This is the only option that actually exercises the mechanism
  the design document is about, but it reintroduces LLM classification
  latency/cost/nondeterminism into every case and — critically — means
  Haven and every baseline are no longer on the same write-path footing
  unless baselines get an equivalent real-extraction path too (mem0's own
  `infer=True` exists and would be the natural counterpart).
- **(b)** Keep `infer=False` verbatim storage (cheap, deterministic, matches
  the base suite's convention) but have a continuation-benchmark-only
  variant of `HavenAdapter.add()` map `turn_type` → the corresponding
  `MemoryType` (`decision→DECISION` with `DecisionMetadata.supersedes`/
  `status` populated from `supersedes_turn`/`resolves_turn`, `constraint→
  RULE`, `blocker→BLOCKER`, `task→TASK`) deterministically, no LLM call.
  This directly conflicts with §4's own field note — "`turn_type` is
  authored metadata... it is never given to the adapter" — and would need
  that design decision revisited, explicitly, with the tradeoff named: it
  makes the write path deterministic-cheat rather than a simulation of
  real extraction, but it is the only way to test `ProjectStateBuilder`
  without paying for real classification on every run.
- Either way, this must be decided and fixed **before** any Haven-vs-adapter
  number from this benchmark is reported, because right now the number
  cannot mean what §1 says it means.

---

### Critical-2 — `must_prioritize` is confounded with recency-of-mention in every pilot case

**Where:** all 10 files under `benchmarks/datasets_continuation/resume_coding/`.

**What's happening:** every pilot case's `conversation` ends with exactly
one `task_type: "task"` turn (always second-to-last or third-to-last, always
the *only* task turn in the whole conversation, always immediately preceded
by the `open_question` turn and followed only by trailing distractors), and
`ground_truth.active_tasks`/`top_priority_next_action` is always that single
turn, verbatim. There is never a case with two competing active tasks, a
task mentioned early but still highest-priority, or a priority order that
contradicts recency of mention.

**Why it matters:** §10(d) states `must_prioritize` exists specifically to
catch "a flat dump [whose] response's ordering will track recency-of-mention
or verbosity, not project priority." In the pilot dataset as authored,
recency-of-mention and correct priority are *the same signal* in all 10
cases — a system with zero prioritization judgment that simply parrots "do
whatever was mentioned most recently" scores perfectly on this axis every
time. The mechanism §10(d) describes cannot be observed to be working,
because nothing in the pilot dataset would fail a recency-only heuristic.
This is the same failure shape Critical-1 has: a real design mechanism
(§6's 15%-weighted `must_prioritize` check) exists in the scoring code and
is applied every run, but the dataset never gives it a case where it could
tell a prioritizing system apart from a recency-parroting one.

**Severity:** High (not Critical — this degrades one 15%-weighted
sub-component, not the whole pipeline's premise like Critical-1).
**Likelihood:** Certain, by construction — verified across all 10 files.
**Effort to fix:** Low. Add cases (even 2-3 of the next authoring batch)
where the highest-priority next action is *not* the most recently mentioned
task — e.g. an early-mentioned blocker-unblocked task that's more urgent
than a later, lower-stakes open item — and confirm scores actually separate
a recency-parroting response from a correctly-prioritizing one on those
cases specifically.

---

### Critical-3 (methodological, not a defect) — the pilot's 10 cases are one template, not ten samples

**Where:** all 10 files under `benchmarks/datasets_continuation/resume_coding/`.

**What's verified:** every case follows the identical `turn_type` skeleton —
2 distractors, `architecture_discussion`, distractor, `rejected_approach`,
distractor, `decision` (`supersedes_turn`), 2 distractors, `constraint`,
distractor, `blocker`, distractor(s), `decision` (`resolves_turn`),
distractor, `implementation` ×2, distractor(s), `open_question`, distractor,
`task`, distractor — at a near-constant length (25 turns in 7 of 10 cases;
27–28 in the rest). Every case's `ground_truth` has exactly 2
`active_decisions`, exactly 1 `superseded_or_rejected`, exactly 1
`constraints`, 0 `active_blockers`, exactly 1 `resolved_blockers`, exactly 1
`active_tasks`, exactly 1 `open_questions`. Only the domain vocabulary
changes (ranker/query-rewriter/slot-allocator/alias-index/vault-writer/
adapter-refactor/project-state-builder/context-planner/hybrid-retriever/
base-adapter-interface). The distractor pool (~25-30 bland personal-life
sentences — "My neighbor's dog barked...", "The gym was way more crowded
than usual today...") is reused verbatim, reshuffled, across every file —
not independently authored per case.

**Why it matters:** a pass rate over these 10 cases has an effective sample
size much closer to "1 structural pattern, 10 vocabulary substitutions" than
"10 independent draws from the category's real difficulty distribution." No
case has: more than one rejected approach, more than one active (still-open)
blocker, competing/conflicting constraints, a decision that *partially* but
not fully supersedes an earlier one, or multiple active tasks needing
relative prioritization. Distractors are uniformly easy (off-topic personal
chatter, trivially separable by any lexical or embedding method — see
High-2) — there is no case testing robustness to *topically adjacent* noise
(e.g., a different feature's decisions mixed into the same conversation).
None of the three tiers' upper range is exercised (§2 Q2 specifies
40–180 turns; the pilot runs 25–28 — itself an explicitly disclosed,
deliberate deviation per §11, so not a new finding, but worth restating
here because it compounds directly with this one: the pilot cannot yet
speak to the `long`-tier staleness-under-volume claim central to §9's
distractor-sweep argument).

**Severity:** High for interpretation of any headline number ("Haven passed
X/10 on resume_coding" says much less than it sounds like it says); not a
defect in code, a limitation of pilot scope the design doc itself flags in
general terms (§11's "compressed... at a fraction of the authoring cost")
but does not spell out this specific consequence for `must_prioritize`
(Critical-2) or for effective sample diversity.
**Likelihood:** Certain — structural, verified across all 10 files.
**Effort to fix:** Medium — this is exactly Phase 4's job (§10), not a
pilot-scope bug; flagging here so Phase 4's authoring explicitly targets
structural diversity (varying which fields are populated/empty, varying
turn-type order, varying which signal wins under prioritization conflict)
rather than only varying domain vocabulary the way the pilot did.

---

## High-severity findings

### High-1 — Every category-signaling turn carries a fixed, near-identical lexical prefix

**Where:** all 10 dataset files — e.g. every `constraint` turn contains the
literal word "Constraint:", every `blocker` turn "Blocked:", every
resolving `decision` turn "Decided:... Unblocks", every `task` turn "Next:",
every `open_question` turn "Open question:" (case 007 even literally spells
out "Open question (explicitly out of scope for now):").

**Why it matters:** §4's field notes state `turn_type` metadata is "never
given to the adapter" — true at the schema level (only `speaker`/`text`
reach `adapter.add()`, verified in `base.py:129-135`) — but the *surface
text itself* leaks the identical signal via a near-constant keyword prefix,
uniformly across all 10 cases. This has two consequences: (1) it
undermines the strength of §9/§10's claim that flat retrieval "has no way"
to separate categories — a trivial regex layered on top of any baseline's
flat join (`"Constraint:" → constraint`, `"Blocked:" → blocker`, etc.) would
recover much of the same category signal Haven's ontology pipeline is
credited for extracting, at zero retrieval sophistication. This is exactly
the kind of accidental "architectural bias" the audit brief asked to hunt
for, except here it cuts the *other* way — it is a bias that could make a
naive baseline look artificially more capable than it would be on real
conversational text, which never self-labels its own rhetorical role this
consistently. (2) It reduces linguistic diversity in a way that makes the
conversations read more like structured meeting-note bullet points than
natural dialogue — see High-3.

**Severity:** High. **Likelihood:** Certain, by construction — grep any
pilot file for "Constraint:"/"Blocked:"/"Decided:"/"Next:"/"Open question:"
and every instance of that `turn_type` matches. **Effort to fix:** Low-
Medium — rewrite future cases' turn text to convey the same role
conversationally rather than with a fixed keyword header (e.g. "we can't
move forward on the ranker until we settle X" instead of "Blocked: ..."),
and consider re-authoring the 10 pilot cases the same way before trusting
comparative numbers from them, since this is cheap per-case text editing,
not a schema or pipeline change.

---

### High-2 — Distractors are uniformly easy; the benchmark has no topically-adjacent noise condition

**Where:** the shared distractor pool reused across all 10 files (e.g.
"My phone battery has been draining faster than usual lately.", "There's a
new bakery opening down the street next month.").

**Why it matters:** every distractor is generic personal-life chatter with
zero lexical or semantic overlap with the software-project content around
it. This is exactly the shape `benchmarks/BENCHMARK_AUDIT.md` (the base
suite's own audit, §7) already praised `decision_reconstruction` for
avoiding — that category mixes distractors that are at least topically
plausible. Here, any embedding-similarity or BM25 baseline separates
signal from noise as easily as Haven does; the "similarity-trap" mechanism
this benchmark actually relies on (§10c) is carried entirely by the
rejected/superseded-vs-current pair, never reinforced by the distractor
pool itself. A more adversarial distractor set — a *different, unrelated*
technical decision/task from the same fictional project, not just
off-topic personal life — would be a meaningfully harder and more realistic
test of whether Haven's ontology-concept grouping earns its keep over
plain embedding similarity.

**Severity:** High for the benchmark's discriminative power between "system
filters obviously irrelevant chatter" (easy, everyone passes) and "system
filters relevant-but-wrong project content" (hard, actually diagnostic).
**Likelihood:** Certain — structural. **Effort to fix:** Medium — requires
authoring a second distractor pool of plausible-but-wrong same-project
content (e.g., decisions/tasks from a sibling component never actually
referenced by the query), not just editing existing text.

---

### High-3 — Dataset realism: several pilot cases are self-referential meta-commentary on this very benchmark, not representative software-project dialogue

**Where:** `resume_coding_basic_006` (about `HavenAdapter.search()`'s own
metadata-leak fix — the exact issue `benchmarks/BENCHMARK_AUDIT.md`
Critical-1 documents), `_007` (about `ProjectStateBuilder` itself),
`_008` (about `ContextPlanner`'s classifier, whose `ground_truth.
active_blockers` is *verbatim* the "what should I do next... falls through
to POINTED_QA" gap this very audit's Critical-1/`context_planner.py` module
docstring already names), `_010` (about `BaseAdapter.add_conversation`'s own
refactor).

**Why it matters:** 4 of the 10 pilot cases (40%) are not generic "resuming
a coding project" scenarios — they are near-verbatim narrations of this
repository's own recent commits and architecture-decision documents,
phrased as if they were conversation turns ("Filter to `MemoryType.DECISION`
objects before projecting status; beliefs go through WorkingContext instead"
is not how an engineer talks out loud; it is documentation prose). This
matters for two reasons: (1) **realism** (the audit brief's Q7) — these
read as commit-message/PR-description text, not spoken dialogue, which is a
different and easier distribution than what Haven would actually see from a
real user; a benchmark that wants to claim generality over "any coding
project" should not draw 40% of its cases from its own meta-development.
(2) **Circularity** — using the benchmark's own architecture gaps as
benchmark content (case 008 literally is the POINTED_QA-fallback gap this
audit's Critical-1 chain depends on) is not leakage in the classic train/
test-contamination sense (there's no model training happening), but it does
mean these specific cases were authored with full, first-hand knowledge of
exactly which Haven mechanism they'd stress and how it currently behaves —
raising the question of whether they were chosen because they're
representative, or because they were the easiest content to write
correctly-labeled `ground_truth` for, having just been read out of the
architecture docs.

**Severity:** High for the "is this benchmark realistic and generalizable"
question the audit brief asks directly; not a scoring-mechanics defect.
**Likelihood:** Certain — 4/10 files confirmed by content inspection.
**Effort to fix:** Low-Medium — replace or rebalance these 4 cases (or
clearly mark them as a "meta/self-hosting" sub-category, if keeping them is
intentional) when Phase 4 authors the remaining ~140-190 cases, and draw
`resume_coding`'s remaining variety from domains that aren't this repository
describing itself.

---

## Medium-severity findings

### Medium-1 — `coherence_score`'s instruction plausibly rewards prose over correct terseness

**Where:** `benchmarks/judges/continuation_judge.py:56-91` (`SYSTEM_PROMPT`),
scored field `coherence_score` — "does this read like a competent
engineer's own resumption, **not a fact list**."

**Why it matters:** this is the only rubric component that reads response
*style*, and its own instruction explicitly disfavors a fact-list-shaped
answer, even a fully correct and appropriately terse one. The audit brief
asks directly whether the benchmark over-rewards verbosity or under-rewards
concise-but-correct answers — this is the one place in the rubric that
could do either, and its wording leans toward rewarding narrative prose
over a tight, correct bulleted summary, which for a *resumption* response
(where an engineer plausibly wants "constraint X, blocker resolved, next
task Y" as fast as possible) is an odd thing to penalize. The weight is
capped at 10% (§6), which limits the damage, but the instruction itself
should be reworded to explicitly credit correct terseness rather than
implicitly asking for narrative style.

**Severity:** Medium (capped blast radius by its 10% weight).
**Likelihood:** Plausible — depends on judge-model behavior under this
exact wording; not verified empirically here (would require running real
cases and comparing a terse-correct response against a verbose-correct one
head to head).
**Effort to fix:** Low — reword the coherence instruction to something like
"holistically, is this response usable as-is by a returning engineer,
independent of length — a short, correct, well-ordered answer should score
at least as well as a longer one with the same content."

### Medium-2 — Hard-fail ceiling does not distinguish "mentioned in passing" from "recommended as the primary action"

**Where:** `benchmarks/judges/continuation_judge.py:94-111`
(`_weighted_score`) — any non-empty `must_not_state_violations` **or**
`forbidden_action_violations` caps the score at `HARD_FAIL_CEILING = 0.2`,
with no distinction between the two violation types or their severity.

**Why it matters:** §6 itself frames this as intentional ("a continuation
that recommends a rejected approach is actively harmful... must not be
averaged away") for the `forbidden_actions` case, which is a strong,
defensible design choice. But `must_not_state` is a broader, softer
category ("the response states X as current/still-open") that plausibly
admits borderline cases — e.g., a response that briefly acknowledges "we'd
earlier considered embedding-only ranking" while correctly recommending the
blended approach as current could trip a lenient judge's
`must_not_state_violations` even though it never *recommends* the stale
path. Today that response is scored identically (ceiling 0.2) to a response
that actively recommends the rejected approach as the way forward. This
conflates two qualitatively different failure severities into one ceiling.
**Severity:** Medium. **Likelihood:** Plausible, judge-dependent — not
verified against a real borderline transcript here. **Effort to fix:**
Low-Medium — either split the ceiling (e.g., `forbidden_actions` → hard cap
near 0, `must_not_state` → a softer but still punitive cap, e.g. 0.4), or
keep one ceiling but tighten the judge's `must_not_state_violations`
instruction to only fire when the stale item is presented as current/
actionable, not merely referenced historically.

### Medium-3 — Same model family generates the continuation response and judges it

**Where:** `run_continuation_benchmarks.py:161-181` (Stage B) and
`continuation_judge.py:145-153` (Stage C) both call
`benchmarks.judges.llm_judge._get_client()`/`_resolve_model()` — the same
Qwen Cloud client/model resolution, per design (§5: "for infrastructure
consistency").

**Why it matters:** using one model family as both the generator being
scored indirectly (Stage B, whose output the judge grades) and the judge
itself carries the well-documented LLM-judge self-preference risk — a model
family may rate outputs in its own house style more favorably, independent
of correctness. This affects the *absolute* calibration of every adapter's
score roughly equally (all adapters' Stage-B responses come from the same
Stage-B model, so it's not obviously a differential Haven-vs-baseline bias)
but it does mean the resulting numbers may not reproduce if Stage C were
swapped to a different judge family, and that risk is currently undisclosed
and unmeasured.
**Severity:** Medium. **Likelihood:** Plausible, general LLM-judge
literature risk, not specific to this codebase. **Effort to fix:** Low to
disclose (state the shared-model-family caveat next to any reported
number, the same way `RUNNER_SPEC.md` already discloses judge-variance
near pass-rate boundaries); Medium to actually test (run Stage C with a
second, independent judge model on a sample and diff).

### Medium-4 — `retrieval_coverage` diagnostic is unimplemented, so Critical-1's failure mode is currently invisible in the output

**Where:** §6 names `retrieval_coverage` as diagnostic-only and explicitly
optional; §11 confirms "not implemented" for the pilot.

**Why it matters:** this is disclosed, not hidden — but its absence is what
let Critical-1 go unnoticed: had a stage-A-vs-`ground_truth` coverage check
existed, a Haven run would have shown 0% coverage on `active_decisions`/
`superseded_or_rejected`/`active_blockers`/`constraints` for every single
case, which would have surfaced Critical-1 immediately from the numbers
alone rather than requiring a source read. Worth prioritizing above other
Phase 5 work specifically because of what it would have caught here.
**Severity:** Medium (process/observability gap, not a scoring defect).
**Likelihood:** N/A (already true). **Effort to fix:** Low-Medium — compare
stage-A's raw context string against `ground_truth`'s fields via the
existing judge infrastructure (a cheaper, non-LLM substring/semantic check
would even do as a first pass, purely diagnostic).

---

## Low-severity / process findings

- **Low** — `_DEFAULT_ADAPTER_CONFIG` (`run_continuation_benchmarks.py:58-71`)
  configures an `ollama`/`qwen3:8b` `llm` block that no adapter in this
  pipeline invokes (`HavenAdapter` ignores `config` entirely per its own
  `from_config` docstring; mem0-shaped adapters would only use it if
  `infer=True`, which this runner never sets). Dead configuration, mirrors
  the base suite's own already-flagged Low finding
  (`benchmarks/BENCHMARK_AUDIT.md`, final Low bullet) — harmless but worth
  cleaning up alongside it.
- **Low** — `run_continuation_case` swallows any per-query exception into a
  `JUDGE_ERROR` result with `score=0.0`/`passed=False`
  (`run_continuation_benchmarks.py:237-250`). Reasonable fail-safe, but a
  Stage-A exception (e.g., a real Haven pipeline error) is indistinguishable
  in the output from a Stage-C judge parsing failure — both surface as the
  same `failure_type`. Worth a distinct `failure_type` or at least a logged
  stage tag, so a future reader of `results_continuation_haven.json` isn't
  misled into thinking every `JUDGE_ERROR` is a judge problem.
- **Low** — `PASS_THRESHOLD = 0.6` (`continuation_judge.py:54`) is
  unmotivated in-code (a bare comment says "not specified by the design").
  Not wrong, just worth deciding deliberately once real score distributions
  exist rather than leaving a placeholder threshold in place indefinitely.

---

## Answers to the ten investigation questions

1. **Does it genuinely reward project continuation?** Partially, and less
   than intended — see Critical-1. What it currently rewards is
   ontology-grouped ranking quality of undifferentiated facts, not the
   decision/blocker/task reconstruction the design centers on.
2. **Can a simple retrieval system accidentally score highly?** Yes, more
   easily than the design intends, precisely because Critical-1 means
   Haven itself is currently *also* running as "simple retrieval with good
   ranking" on this benchmark — so a baseline with comparably good ranking
   has a real, not merely theoretical, chance to score close to Haven today.
   Once Critical-1 is fixed, this should become harder, which is the
   point — but that hasn't been demonstrated yet.
3. **Does it over-reward verbosity?** Modest risk, capped at 10% weight —
   see Medium-1. The `coherence_score` instruction leans toward narrative
   prose over terse correctness.
4. **Does it under-reward concise but correct answers?** Same finding as
   #3 — the coherence instruction's "not a fact list" framing is the one
   place this could happen.
5. **Is the rubric balanced?** The four-way weighting (40/35/15/10) is
   reasonable on paper, but `must_prioritize` (Critical-2) and `must_state`
   coverage are both currently under-discriminating given the pilot
   dataset's uniformity (Critical-3) — the weights are fine; what they're
   computed over isn't varied enough yet to prove the weights matter.
6. **Is the hard-fail ceiling appropriate?** The concept is sound and well-
   justified for `forbidden_actions`; conflating it with the softer
   `must_not_state` category at one flat 0.2 (Medium-2) is the one part
   worth revisiting.
7. **Are the cases sufficiently realistic?** No, for two independent
   reasons: the fixed keyword-prefix convention (High-1) makes turns read
   as self-labeled bullet points rather than dialogue, and 40% of pilot
   cases are meta-commentary on this repository's own recent development
   rather than representative project content (High-3).
8. **Hidden assumptions that unfairly favor Haven?** The opposite of the
   usual failure mode was found: Critical-1 currently makes the benchmark
   *unfairly disadvantage nobody* by disabling the one mechanism that would
   have most favored Haven specifically. If Critical-1 is fixed without a
   matching real-extraction path for baselines (per Critical-1's fix-(a)
   caveat), *that* would become a genuine, differential Haven-favoring
   hidden assumption — flagging it now so the fix doesn't introduce it.
9. **Would mem0/Zep/Letta/Graphiti have a reasonable chance to compete?**
   Today, yes — arguably more than intended, because of Critical-1. This
   is not evidence the benchmark is fair; it's evidence the benchmark isn't
   yet testing what it says it tests.
10. **Reviewing for publication, what would I say?** Reject and resubmit:
    fix Critical-1 first (nothing else can be validly interpreted until the
    mechanism under test actually runs), then re-author the pilot set with
    Critical-2/3 and High-1/2/3 in mind before trusting any comparative
    number this pipeline produces.

---

## Priority order for fixes

1. **Critical-1** — must be resolved before any result from this benchmark
   is reported or cited, in either direction (for or against Haven).
2. **Critical-2** — cheap to fix, blocks trusting the `must_prioritize` axis.
3. **High-1** — cheap per-case text edits; also improves High-3's realism.
4. **Medium-2** — cheap judge-prompt/ceiling tweak, worth doing alongside
   Critical-1's re-validation run since it changes what "hard fail" means.
5. **High-2, High-3, Critical-3, Medium-1, Medium-3, Medium-4** — properly
   Phase 4/5 scope; sequence into the next authoring pass rather than
   patching the existing 10 pilot cases piecemeal.
