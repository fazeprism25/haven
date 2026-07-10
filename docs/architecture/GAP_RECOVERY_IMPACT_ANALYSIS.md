# Gap Recovery Impact Analysis

Status: **Measurement only. No production code changed. No benchmark dataset changed.**
This is Step 0 of `docs/architecture/GAP_RECOVERY_EXECUTION_DESIGN.md`'s own "Implementation
roadmap" (§11): *"Run the existing, already-shipped `decide_gap_recovery` over the benchmark
corpus (no code change) and record how often `should_retry=True` fires, and for which
categories. This sizes §10's ceiling with real numbers before building anything."* That
document predicted the ceiling would be small; this document measures it.

**Method summary**: a throwaway harness script (not committed, deleted after this document was
written — same convention `PRE_BENCHMARK_FREEZE_AUDIT.md` §0 already establishes: *"two
throwaway harness scripts were written to measure the numbers below, run once, and deleted
immediately after"*) ran two passes over all 288 loadable cases in `benchmarks/datasets/`
(`discover_dataset_dirs()` / `load_benchmarks()`, unmodified, from
`benchmarks/runners/run_benchmarks.py`):

1. **`ContextPlanner().plan(query)`** for every one of the 288 cases — pure function of query
   text alone, no vault, no LLM, no retrieval (`context_planner.py`'s own "Determinism"
   section). Instant.
2. **The real write+read pipeline** (`HavenFullAdapter`: `Extractor` → `Classifier` →
   `ImportanceScorer` → `CanonicalMatcher` → `KnowledgeUpdater` → `VaultWriter` →
   `OntologyPipeline`, then `MemoryEngine.query_with_trace`, unmodified) for only the subset
   that classified into a non-`POINTED_QA` mode — the only subset where a `REQUIRED`-category
   gap is even possible, since `TaskMode.POINTED_QA` always produces `requirements=()`, which
   makes `CoverageReport.entries=()` and `decide_gap_recovery` trivially return
   `RetryReason.NO_GAP` (`gap_recovery.py`'s own rule 1, "including trivially for the
   `TaskMode.POINTED_QA` sentinel"). Running the heavy pipeline against the other 257 cases
   would have cost real LLM time to reconfirm a result already guaranteed by construction.

Pass/fail ground truth was **not re-judged** — it was read from the already-generated
`benchmarks/results/results_haven_full.json`, keyed by `benchmark_id`, whose own metadata
(`"git_commit": "042e16e66f5d28605ea3770b7c9080b55ec678ce"`) matches `HEAD` at the time this
measurement was run. Re-running the LLM judge (`judge_answer`, a live Qwen Cloud call) would
have added cost and a second source of run-to-run noise for a question (pass/fail) this
measurement doesn't change.

**One caveat stated plainly**: Manager AI's `Classifier` stage is a live LLM call, not a
deterministic function, so the second, real-pipeline run's `MemoryType` assignments can differ
in principle from whatever run originally produced `results_haven_full.json`'s pass/fail
column. Treat the coverage/gap-recovery numbers below as representative of current pipeline
behavior on this corpus at this commit, not a byte-identical replay of one frozen run.

---

## 1. Headline number

**31 of 288 cases (10.8%) even have a chance of triggering gap recovery** — everything else
classifies `TaskMode.POINTED_QA` and is categorically excluded before retrieval starts. **Of
those 31, all 31 (100%) actually produce `should_retry=True`** when run through the real
pipeline. So the bottleneck is not "does the decision fire when eligible" — it fires every
time it's eligible — the bottleneck is **how rarely it's eligible at all**, and, as §5 shows,
**how much of that 31 is itself a classification artifact rather than a real planning need.**

| | Count | % of 288 |
|---|---:|---:|
| `TaskMode.POINTED_QA` (gap recovery categorically inert) | 257 | 89.2% |
| `TaskMode.STRUCTURING` | 27 | 9.4% |
| `TaskMode.CODING_DEBUGGING` | 4 | 1.4% |
| `TaskMode.RESEARCH` | 0 | 0.0% |
| `TaskMode.CONTINUATION` | 0 | 0.0% |
| **`should_retry=True`** | **31** | **10.8%** |

---

## 2. Breakdown by benchmark category

| Category | Total cases | Non-`POINTED_QA` | `should_retry=True` |
|---|---:|---:|---:|
| concept_consolidation | 62 | 2 | 2 |
| supersession | 55 | 6 | 6 |
| refinements | 30 | 0 | 0 |
| decision_reconstruction | 26 | 2 | 2 |
| Beliefs | 15 | 6 | 6 |
| Decisions | 15 | 3 | 3 |
| Temporal | 15 | 1 | 1 |
| contradictions | 10 | 4 | 4 |
| goals | 10 | 0 | 0 |
| identity | 10 | 0 | 0 |
| preferences | 10 | 3 | 3 |
| temporal | 10 | 2 | 2 |
| belief_evolution | 5 | 1 | 1 |
| belief | 5 | 0 | 0 |
| decision_consistency | 5 | 1 | 1 |
| decision | 5 | 0 | 0 |

(Note: `Beliefs`/`belief`/`belief_evolution`, `Decisions`/`decision`/`decision_consistency`, and
`Temporal`/`temporal` are distinct, differently-cased `category` strings already present in the
dataset — not something this measurement introduced. Two datasets appear to have been authored
at different times with different naming conventions for near-duplicate content; out of scope
to fix here.)

Every `should_retry=True` case's `retry_budget=1` and `retry_reason='required_category_missing'`
— `RetryReason.LOW_PLAN_CONFIDENCE` never fired anywhere in the corpus, exactly as
`gap_recovery.py`'s own docstring predicts (`ContextPlanner` always emits `confidence=1.0`
today).

**Categories where retries never matter**: `refinements` (30 cases), `goals` (10), `identity`
(10), `belief`/`decision` (5 each) — every single case in these five categories classifies
`POINTED_QA`. Combined with the `people`/`projects`/`recurring` directories (0 gradeable files
each, per `benchmarks/RUNNER_SPEC.md`), roughly half the dataset directories contribute *zero*
cases where gap recovery could ever be reachable, independent of retrieval quality.

---

## 3. Breakdown by planner task mode

Already shown in §1's table. Two things worth stating explicitly:

- **`RESEARCH` and `CONTINUATION` — the two modes with the largest category-requirement
  tables, and the ones closest to Haven's actual "second brain" pitch (`CONTINUATION` requires
  *every* category at once) — have zero representation in the current 288-case corpus.** No
  query anywhere in `benchmarks/datasets/` matches `_MODE_PATTERNS`' `RESEARCH` or
  `CONTINUATION` triggers ("continue", "where were we", "resume", "keep going", "research",
  "investigate", "look into", "find out", "explore"). This directly corroborates
  `CONTEXT_PLANNER_DESIGN.md` §3 point 7's independent finding that `active_context`,
  `insights`, `memory_recall`, `mistake_prevention`, and `open_problems` are all 0-file
  directories — those are precisely the categories that would contain continuation/research-
  shaped queries.
- Every `should_retry=True` case is `STRUCTURING` (27) or `CODING_DEBUGGING` (4) — the two
  modes with the *smallest* requirement tables (3 and 5 categories respectively, vs.
  `CONTINUATION`'s 6).

---

## 4. Which missing categories dominate

Counted across the 31 `should_retry=True` cases (a case can be missing more than one category):

| Missing category | Count | % of the 31 |
|---|---:|---:|
| `constraint` | 31 | **100%** |
| `research` | 25 | 80.6% |
| `decision` | 15 | 48.4% |
| `implementation_state` | 4 | 12.9% |
| `code_area` | 4 | 12.9% |
| `open_question` | 4 | 12.9% |

**`constraint` is missing in every single triggering case, with no exception.** Per
`coverage_analyzer.py`'s `MEMORY_TYPE_CATEGORY` table, `ContextCategory.CONSTRAINT` is reached
by exactly one `MemoryType`: `RULE`. These benchmark conversations are short, single-topic,
synthetic exchanges about one belief, one decision, or one preference — the kind of content
Manager AI's `Classifier` essentially never has a reason to tag `RULE`. This is not a retrieval
failure a retry can fix: `gap_recovery.py`'s own module docstring already names this exact
failure mode ("a plan can never 'conjure' a candidate that isn't there"), and
`GAP_RECOVERY_EXECUTION_DESIGN.md` §9 names it too ("`categories_recovered` can be zero even
when `retry_executed` is `True`... the retry correctly finds nothing, and the category
legitimately remains `MISSING`. This is expected behavior, not a bug"). A retry cannot surface a
`RULE`-typed memory that was never written.

`implementation_state`/`code_area`/`open_question` only ever co-occur — they come exclusively
from the 4 `CODING_DEBUGGING` cases (`TaskMode.CODING_DEBUGGING`'s requirement table is the only
one containing all three).

---

## 5. Planner mistakes causing unnecessary retry recommendations

**Yes — a concrete, quantifiable bug, not a judgment call.** `ContextPlanner._classify`
(`context_planner.py:608-611`) matches mode patterns with plain, unanchored substring
containment (`pattern in normalized`), not word-boundary/token matching. Checked every one of
the 31 triggering queries against its matched pattern(s):

| Query (truncated) | Matched pattern | Contained inside | benchmark_id |
|---|---|---|---|
| "...monolithic **architectures**?" | `architecture` | `architectures` | beliefs_015 |
| "...ideal **infrastructure** deployment..." | `structure` | `infrastructure` | beliefs_018 |
| "...**roadmapping** and **planning** horizons?" | `roadmap`, `plan` | `roadmapping`, `planning` | beliefs_020 |
| "...**infrastructure** and operations..." | `structure` | `infrastructure` | beliefs_024 |
| "What are the user's **plans** for the Japan trip?" | `plan` | `plans` | concept_consolidation_basic_045 |
| "What are the user's **plans** for Seoul?" | `plan` | `plans` | concept_consolidation_basic_061 |
| "...before **implementation**?" | `implement` | `implementation` | contradictions_basic_004 |
| "What should be **implemented** first?" | `implement` | `implemented` | contradictions_basic_008 |
| "...currently **planned**?" (embedding) | `plan` | `planned` | supersession_basic_006 |
| "...currently **planned**?" (architecture, ×2) | `plan` | `planned` | supersession_basic_010, _018 |
| "...currently **planned**?" (embedding system) | `plan` | `planned` | supersession_basic_011 |
| "...API **redesign** project?" | `design` | `redesign` | supersession_basic_037 |
| "...storing **unstructured** data?" | `structure` | `unstructured` | temporal_022 |

**12 of the 31 (38.7%)** are unambiguous false positives: the query is a plain `POINTED_QA`
single-fact lookup ("what are the user's plans for Seoul" is a factual recall question, not a
planning task; "unstructured data" describes a database property, not a request to reconstruct
project structure) that only misclassified because a substring like `plan`, `design`,
`structure`, `architecture`, or `implement` happens to appear inside an unrelated word.

A further 2 of the 31 (`supersession_basic_010`, `_018`, both "What architecture is currently
planned?") are **mixed**: one real `architecture` token match plus one more `planned`-inside-
`plan` false match — the case is arguably still `STRUCTURING`-shaped on the strength of the
first match alone, but the second contributed nothing correct.

Even most of the remaining 17 "clean" pattern matches are, on inspection, ordinary single-fact
decision/preference lookups that happen to use a trigger word as a plain noun ("What
**architecture** does the user currently prefer?", "Which **design** tool did the team decide
to use?") rather than genuine multi-category planning/reconstruction requests — the same
overfitting risk `CONTEXT_PLANNER_DESIGN.md` §1 already flagged in the abstract ("a planner that
defaults every query into a task-mode bucket with a nonempty required-category list will
silently regress [pointed queries]"). This measurement shows it isn't hypothetical: it is
already happening on 12/31, and arguably more.

**Practical consequence**: fixing `_classify` to use word-boundary matching (e.g. `\bplan\b`
instead of `"plan" in text`) would, on this corpus alone, cut `should_retry=True` firings from
31 to roughly 17-19 with no change to `gap_recovery.py`, `coverage_analyzer.py`, or any
retry-execution code — a near-zero-risk, purely-planner-side fix that improves the *signal*
any future retry would act on, independent of whether retry execution ever ships.

---

## 6. How many `should_retry=True` cases are already passing anyway

**23 of 31 (74.2%)** already `PASS` today, despite the planner recording a "gap." The category
the planner flagged as missing (overwhelmingly `constraint`/`research`, see §4) is simply not
something the benchmark's `expected.answer_contains` needs — the query gets answered correctly
without it. This is consistent with §5: many of these 31 aren't real reconstruction tasks in the
first place, so there was never a real gap for the answer to suffer from.

## 7. How many current failures are theoretically reachable by one retry

Of the corpus's **48 total failures** (240/288 = 83.3% overall pass rate), only **8** fall
within the `should_retry=True` set:

| benchmark_id | category | failure_type | missing_categories |
|---|---|---|---|
| beliefs_019 | Beliefs | INCOMPLETE | research, constraint |
| beliefs_024 | Beliefs | INCORRECT | decision, research, constraint |
| contradictions_basic_003 | contradictions | TEMPORAL | decision, research, constraint |
| contradictions_basic_004 | contradictions | INCOMPLETE | decision, constraint, implementation_state, code_area, open_question |
| contradictions_basic_008 | contradictions | INCORRECT | decision, constraint, implementation_state, code_area, open_question |
| decision_basic_002 | decision_consistency | INCOMPLETE | decision, research, constraint |
| supersession_basic_037 | supersession | INCOMPLETE | decision, research, constraint |
| temporal_022 | Temporal | INCORRECT | research, constraint |

**8/48 = 16.7% of all failures, or 8/288 = 2.8% of the entire corpus, is the absolute
theoretical ceiling** — and it is an optimistic one. Failure-type breakdown of just these 8:
`INCOMPLETE` 4, `INCORRECT` 3, `TEMPORAL` 1 — **zero `RETRIEVAL`-type failures**, matching the
corpus-wide failure-type distribution (`INCOMPLETE` 25, `INCORRECT` 13, `SUPERSESSION` 9,
`TEMPORAL` 1 — also zero `RETRIEVAL` anywhere in all 288 cases). `INCORRECT` and `TEMPORAL`
failures are reasoning/ordering problems, not "the fact wasn't retrieved" problems — pulling in
*more* candidates via a retry is at best irrelevant to them and at worst adds noise. Only the
4 `INCOMPLETE` cases are plausibly the right shape for a retry to help, and even those require
the vault to actually contain a `constraint`/`research`/`decision`-mapped memory for the retry
to find — which, per §4, is exactly the category this corpus's narrow synthetic conversations
mostly never produce.

**Realistic ceiling: at most 4 of 288 cases (1.4%), likely fewer once "does the vault even
contain the missing category" is accounted for.**

## 8. What would the realistic post-implementation impact look like

Given §6 and §7: even in the best case where the full `GAP_RECOVERY_EXECUTION_DESIGN.md`
retry-execution machinery (Steps 1-4) is built and works exactly as designed, the observable
benchmark delta is **bounded above by +4 PASS out of 288 (+1.4 percentage points)** — and the
31-case population it draws from already contains ~12-14 misclassified cases (§5) whose
"gap" isn't real, so building retry execution now would spend a full behavior-changing pipeline
change (a second rank→accept→allocate pass, doubling latency on every triggering query, per
`GAP_RECOVERY_EXECUTION_DESIGN.md` §9's own named risk) mostly on cases where there is nothing
to recover.

---

## 9. Qualitative analysis

**Does the benchmark corpus underrepresent the continuation conversations Haven is actually
designed for?** Emphatically yes, and this measurement adds a new, concrete data point beyond
the previously-documented "5 empty dataset directories" finding: **zero of the 288 loadable
cases classify as `CONTINUATION` or `RESEARCH`** — not "few," zero. Every benchmark
conversation in this corpus is short (a handful of turns) and narrowly about one topic (one
belief, one decision, one preference), queried with a single pointed question. That shape
structurally cannot exercise gap recovery's actual design target: a query like "where were we"
or "what am I blocked on" over a vault with real topic diversity, where the query's own words
carry almost no retrievable content and the planner's category-requirement table is what tells
retrieval what to go looking for. The current corpus tests "did retrieval find the one fact this
question is about," which is precisely the case `TaskMode.POINTED_QA` already handles today,
un-augmented.

**Would gap recovery become much more valuable on real 300-500 turn project conversations than
on today's benchmarks?** Plausibly, yes — and for a specific mechanical reason, not just
intuition. `GAP_RECOVERY_EXECUTION_DESIGN.md` §5's whole retry mechanism is a category-keyword
query augmentation (`raw_query + " " + keywords for each missing category`) specifically meant
to widen `KeywordCandidateRetriever`'s exact-token matching beyond whatever the original
phrasing happened to use. That mechanism has nothing to bite on in a two-turn synthetic
conversation where the one relevant fact almost certainly already shares vocabulary with the
query (these are the kinds of pairs `benchmarks/datasets/` authors by construction). It has a
lot to bite on in a real, long, multi-topic vault where a `CONTINUATION`-mode "keep going" query
carries zero content-bearing keywords of its own and depends entirely on activation spreading
plus category-targeted widening to reconstruct scattered state. **This means the current
benchmark numbers are likely a lower bound on gap recovery's real value, not an upper bound** —
the opposite of the usual benchmark-overfitting failure mode, but equally a reason not to trust
this benchmark corpus's near-zero ceiling as the final word on whether to build it.

**Should the hackathon optimize for benchmark gain or long-term product value here?** Given
§8's hard ceiling (≤4/288, and likely 0-2 once §4's "vault doesn't contain the category" and
§5's "the trigger was a lexical bug" are subtracted out), optimizing *this* investment for
benchmark gain during a time-boxed hackathon is close to a null bet on the *current* dataset.
The long-term product value case is real but currently unmeasurable — nothing in the corpus
exercises it. Which points at the actual highest-leverage move not being "build the retry" or
"skip it," but the two much cheaper items in §10 below.

---

## 10. Recommendation

**B — skip gap-recovery retry execution (`GAP_RECOVERY_EXECUTION_DESIGN.md` Steps 1-4) until
after the hackathon**, on the strength of §7/§8's numbers: the addressable ceiling on the
current corpus is ≤4 cases (1.4%), the mechanism this corpus could validate isn't the shape of
gap recovery's real design target (§9), and Step 3 is explicitly a behavior-changing,
latency-doubling pipeline change (`GAP_RECOVERY_EXECUTION_DESIGN.md` §9) — not something to ship
on a benchmark signal this thin.

This is **not** "do nothing with this finding." Two adjacent, much cheaper, much higher-signal
actions fall directly out of this measurement and are worth doing regardless of the retry-
execution decision:

1. **Fix `ContextPlanner._classify`'s word-boundary bug (§5).** Pure planner-side, zero
   retrieval/ranking/acceptance risk, no new pipeline stage, immediately improves the accuracy
   of `RetrievalTrace.context_plan`/`.coverage`/`.gap_recovery` as a diagnostic today — those
   traces are already shipped and already read by the Retrieval Inspector regardless of whether
   a retry ever consumes `GapRecoveryDecision`. This is worth doing on its own merits, not
   contingent on the gap-recovery retry decision at all.
2. **Treat filling the 5 empty continuation-shaped dataset directories
   (`active_context`/`insights`/`memory_recall`/`mistake_prevention`/`open_problems`) as the
   real prerequisite**, not a nice-to-have — per §9, this benchmark corpus cannot currently
   produce a single `CONTINUATION` or `RESEARCH`-mode case to measure gap recovery's actual
   target scenario against. Building retry execution before this exists means shipping a
   feature this repository's own benchmark suite is structurally incapable of validating either
   way.

If time pressure forces a choice between "build gap recovery retry execution" and "author
continuation-shaped benchmark data," the data authoring is the better hackathon investment: it
is the only one of the two that changes what can be *measured*, and per §9, what's currently
measurable is not where this feature's real value is expected to live.
