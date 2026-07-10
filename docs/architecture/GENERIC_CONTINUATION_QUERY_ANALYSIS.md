# Generic Continuation Queries — Root-Cause Analysis

Status: **Analysis only. No implementation.** Grounded entirely against source
on disk: `obsidian/memory_engine/{context_planner,query_rewriter,
hybrid_candidate_retriever,keyword_candidate_retriever,engine,
deterministic_ranker,acceptance_stage,project_state}.py`,
`obsidian/ontology/{query_resolver,retrieval_config}.py`,
`obsidian/server/{main,dashboard}.py`, and the five prerequisite documents
(`CONTINUATION_BENCHMARK_DESIGN.md`, `CONTINUATION_BENCHMARK_AUDIT.md`,
`CONTINUATION_BENCHMARK_INGESTION_DESIGN.md`, `PROJECT_STATE_EVALUATION.md`,
`PROMPT_CONTINUATION_EVALUATION.md`). Every claim below cites the line it
comes from; the empirical retrieval trace in §2 was independently re-derived
from the tokenizers/matchers themselves, not taken from the ingestion
design doc's report (whose own Stage-A run already confirmed the same
finding for one of the four example queries).

---

## 1. The one-sentence answer

**Candidate generation — not classification, not ranking, not acceptance,
not `ProjectState` — is where these queries die.** Both of Haven's two
candidate-generation paths (`QueryResolver`'s ontology path,
`KeywordCandidateRetriever`'s lexical path) require the query to share
literal vocabulary with the vault: an alias-index hit or a token
intersection. A generic continuation query ("Continue.", "Continue
implementing the project.") carries almost no vault-specific vocabulary by
construction — that is what makes it generic — so both paths correctly,
deterministically, by-design return nothing. `ContextPlanner` classifies
these queries exactly right (`TaskMode.CONTINUATION`, requiring every
tracked category) but that classification is never used to compensate for
the retrieval failure — it is carried as inert metadata all the way to
`ProjectStateBuilder`, which has nothing in its input to build from.
Everything downstream of candidate generation (`ActivationSpreader`,
`AcceptanceStage`, `DeterministicSlotAllocator`, `ProjectStateBuilder`,
`StructuredPromptBuilder`) is operating correctly on an empty or near-empty
input — they are casualties of the failure, not causes of it.

---

## 2. Tracing all four example queries through the real pipeline

`MemoryEngine._run_retrieval` (`engine.py:708-790`) is the single shared
prefix every public query method uses: `ContextPlanner.plan` →
(optional `QueryRewriter`) → `HybridCandidateRetriever.retrieve_with_diagnostics`
per query string → merge → `_active_candidates` validity filter →
`DeterministicRanker.score_all`. Traced individually below.

### "Continue implementing the project."

- **`ContextPlanner`** (`context_planner.py:484-497,608-611`): substring
  `"continue"` matches first → `TaskMode.CONTINUATION`, `requirements` =
  all six of that mode's categories (Decision, Task, Constraint†, Blocker†,
  Research, OpenQuestion). **Classification is correct.**
- **`QueryResolver.resolve`** (`query_resolver.py:66-102`): whole-phrase
  alias lookup on `"continue implementing the project."` — no concept in
  any real vault is aliased to that sentence, so this fails. Token pass
  (`tokenize_query`, stop words removed) tries `continue`/`implementing`/
  `project` individually against the alias index — none of these are
  themselves concept labels/aliases (concepts are named entities like
  `"Haven"`, `"DeterministicRanker"`, not generic verbs/nouns). **Zero
  concepts resolved → zero seeds → `ActivationSpreader` has nothing to
  spread from → zero ontology candidates.**
- **`KeywordCandidateRetriever.retrieve_with_scores`**
  (`keyword_candidate_retriever.py:791-835`): query keywords after
  stop-word filtering = `{continue, implementing, project}` (`"the"` is
  filtered). A candidate is returned only if its `canonical_fact` token set
  intersects this set (`:828`, exact token equality, no fuzzy/substring
  matching by explicit design — see the module's own "Design decisions").
  Vault facts are specific technical statements ("the ranker uses a
  blended score...", "score_breakdown stays internal..."); they do not
  generally contain the literal words "continue", "implementing", or the
  bare word "project". **Zero or near-zero keyword matches.** This is not
  a hypothetical — the ingestion design doc's own Stage-A validation ran
  this exact query against real ingested cases and confirmed **zero
  retrieved candidates in 9 of 10 cases**
  (`CONTINUATION_BENCHMARK_INGESTION_DESIGN.md`, "Implementation status,"
  finding 2).
- **Result:** `HybridCandidateRetriever` merges two empty sets → empty.
  `DeterministicRanker.score_all([])` → `[]`. `AcceptanceStage` has nothing
  to accept or reject. `DeterministicSlotAllocator` allocates zero slots.
  `ProjectStateBuilder.build([])` → every field empty, `gaps` = all 8 names,
  `confidence=0.0` (`project_state.py:487-600`, per the audit's Critical-1
  trace). Classification was right; there was simply nothing to classify
  *into*.

### "Continue."

- **`ContextPlanner`**: `"continue"` matches → `CONTINUATION`. Correct.
- **Retrieval**: query keywords = `{continue}`, a single token. No stop
  words to remove beyond that (it isn't one). This is the cleanest,
  least-ambiguous case in the whole set: for the ontology path to find
  anything, some concept would have to be aliased to the literal word
  "continue" (never true in practice); for the keyword path to find
  anything, some fact would have to literally contain the word "continue"
  (essentially never true for facts that are about technical/project
  content). **Zero candidates is not a likely outcome here — it is a
  structural guarantee**, independent of vault size, content, or recency.
  No amount of downstream ranking/allocation/reconstruction sophistication
  can recover from an empty candidate list; there is no signal in a
  single content-free word for an exact-match pipeline to act on.

### "Continue yesterday's work."

- **`ContextPlanner`**: `"continue"` matches → `CONTINUATION`. Correct.
- **Retrieval**: `_strip_clitic` (`keyword_candidate_retriever.py:547-563`)
  reduces `"yesterday's"` to `"yesterday"`; `"work"` is in the `work`
  variant group (`work`/`works`/`working`/`worked`,
  `keyword_candidate_retriever.py:366`). Query keywords = `{continue,
  yesterday, work}`. Unlike the two queries above, this one has a
  non-trivial chance of a partial keyword-path hit if some fact happens to
  contain "work"/"working"/"worked" literally (plausible in
  implementation-narration text) — but even a hit provides no traction on
  "yesterday": nothing in the rendered prompt carries an as-of timestamp
  at all (`PROMPT_CONTINUATION_EVALUATION.md` §4/§7 — `generated_at` is
  computed but deliberately never rendered), so even a perfect retrieval
  result cannot answer what "yesterday" is relative to. This query's
  failure mode is **partial retrieval with no temporal grounding**, a
  different, narrower symptom than the other three's near-total retrieval
  failure.

### "What should we work on next?"

- **`ContextPlanner`**: checked against every pattern in
  `_MODE_PATTERNS` (`context_planner.py:484-536`) — none of
  `CONTINUATION`'s eight patterns, `CODING_DEBUGGING`'s eleven,
  `STRUCTURING`'s six, or `RESEARCH`'s five match this exact phrasing.
  Falls through to `TaskMode.POINTED_QA`, `requirements=()`
  (`_classify`, `:598-611`). **This is a second, independent failure**:
  the query is arguably the single most orientation-seeking phrasing in
  the whole set, and it is the one phrasing the classifier gets wrong.
  `PROMPT_CONTINUATION_EVALUATION.md` §7 already verified this exact gap.
- **Retrieval**: query keywords after stop-word filtering = `{work, next}`
  (`what`/`should`/`we`/`on` are all in `_STOP_WORDS`,
  `keyword_candidate_retriever.py:253-343`). This is enough surface area
  to plausibly retrieve *something* real — the ingestion design doc's own
  validation confirms it: "`'What should we work on next?'` does retrieve
  real candidates (e.g. the active task)"
  (`CONTINUATION_BENCHMARK_INGESTION_DESIGN.md`, finding 2). But because
  `task_mode is TaskMode.POINTED_QA`, `query_structured` never builds a
  `ProjectState` at all (`engine.py:1260-1262`: the `if` guard only fires
  for `CONTINUATION`) — the candidates that were found still reach
  `WorkingContext`, but the orientation layer that would organize them into
  `Decisions`/`Blockers`/`ActiveTasks` never renders. **This query fails
  differently from the other three: retrieval half-works, but the
  classifier routes it away from the component that would make the result
  usable as a continuation answer.**

† `Constraint`/`Blocker` are `PriorityTier.NEVER_DROP` in `CONTINUATION`'s
requirement table (`context_planner.py:448-449`) — a detail that matters
for §4's recommendation, not for this trace.

---

## 3. Root-cause summary

| Component named in the task | Verdict | Why |
|---|---|---|
| **`ContextPlanner`** | **Correct for 3 of 4 queries; a real, separate bug for the 4th.** | Classifies "continue"/"continue yesterday's work"/"continue implementing the project" correctly as `CONTINUATION`. Misclassifies "what should we work on next?" as `POINTED_QA` because no lexical pattern covers that phrasing (`context_planner.py:484-536`). This is a genuine, independent defect — but fixing it alone does not fix the other three queries, which already classify correctly and still fail. |
| **`QueryRewriter`** | **Not a cause — because it never runs in production.** Every real `MemoryEngine` construction site (`server/main.py:470,1410,1448`, `server/dashboard.py:243`) omits the `query_rewriter=` argument, so it defaults to `None` (`engine.py:682`) and `_run_retrieval` takes the "disabled" branch (`engine.py:745-747,769-770`) on every live request. The module exists, is wired into `MemoryEngine` as an *optional* constructor parameter, and is fully tested — it is simply never instantiated by anything that runs. See §4 for whether wiring it in would help. |
| **`KeywordCandidateRetriever`** | **Primary root cause.** Exact, stop-word-filtered token-set intersection only (`keyword_candidate_retriever.py:791-835`) — explicitly, by design, "no embeddings, no fuzzy matching" (module docstring, "Explicitly out of scope"). A query with no vault-specific vocabulary has no tokens to intersect against specific facts. This is not a bug in the module — it is doing exactly what its own extensive design-decision notes say it should do — but it is the component with no fallback for "the query carries no topical signal," which is exactly the query shape this investigation is about. |
| **`QueryResolver` / ontology lookup** | **Co-equal root cause.** Whole-phrase and per-token alias-index lookups only (`query_resolver.py:66-102`) — no ranking, no fuzzy matching, no "closest concept" fallback (module docstring, "Design constraints"). Generic phrasing essentially never matches a concept label/alias, so this path contributes zero seeds for these queries, structurally and reliably. |
| **Activation (`ActivationSpreader`)** | **Not a cause — a downstream casualty.** It only spreads from seeds `HybridCandidateRetriever` constructs from resolved concepts (`hybrid_candidate_retriever.py:323-335`). Zero resolved concepts means zero seeds means nothing to spread, correctly. |
| **Acceptance (`AcceptanceStage`)** | **Not a cause.** Operates on `ranked_all`, which is `DeterministicRanker.score_all(candidates, ...)` — `score_all([])` is `[]` (`deterministic_ranker.py:211+`). Nothing to abstain from, gap-cut, or floor when the input is already empty. Separately confirmed feasible for a *non-empty* recall-only candidate: `AcceptanceConfig.abstention_score` defaults to `0.25` (`acceptance_stage.py:124`) and `RetrievalConfig`'s default weights give `importance`+`confidence`+`recency`+`confirmation_count` a combined `0.65` of `1.40` total weight (`retrieval_config.py:107-113`) — a candidate with zero ontology/keyword evidence but ordinary importance/confidence/recency values clears `0.25` comfortably (worked example in §4). This matters because it means a *fallback* candidate-generation path (§4) would not need any change to `AcceptanceStage`'s thresholds to survive acceptance. |
| **Slot allocation (`DeterministicSlotAllocator`)** | **Not a cause.** Allocates from whatever `ranked_all` contains; allocating zero slots from zero candidates is correct behavior, not a defect. `PROJECT_STATE_EVALUATION.md` §1(a)/§7 already documents this allocator's real, separate weakness (flat top-K, no per-category reservation) — relevant to *content quality at scale*, not to this investigation's "near-zero content" symptom. |
| **`ProjectStateBuilder` / `ProjectState`** | **Not a cause — the component every one of these queries is actually trying to reach, and correctly reports its own emptiness.** `PROJECT_STATE_EVALUATION.md` already established this component is `DETERMINISTIC`/never-fabricating; an empty `allocated` list correctly produces `gaps` = all 8 fields, `confidence=0.0` (`project_state.py:487-600`). It has nothing to reconstruct because nothing reached it — it is not miscategorizing or dropping content that exists in its input. |

**Combination, not single point of failure — but not an even one either.**
The four queries fail for two distinct reasons in different proportions:
three of four (`"Continue."`, `"Continue implementing the project."`,
`"Continue yesterday's work."`) fail because **candidate generation itself
returns nothing** (`QueryResolver` + `KeywordCandidateRetriever` both
require lexical/alias overlap that a content-free query cannot supply);
one of four (`"What should we work on next?"`) fails for the **unrelated,
independent** reason that `ContextPlanner`'s lexical table misses that
phrasing, so even the candidates that *were* found never reach
`ProjectStateBuilder`. Any fix that addresses only one of these two causes
will look successful on the query it targets and leave the other three (or
one) exactly as broken as before.

---

## 4. Candidate fixes, ranked

Ranked by expected improvement on the four example queries specifically,
implementation complexity, and regression risk to behavior that already
works. All are deterministic; none require an inference/persistence change
beyond what's noted.

### 1. Plan-driven category-fallback retrieval for `CONTINUATION` queries — **recommended, see §5**

Add a retrieval path that, for queries `ContextPlanner` classifies as
`CONTINUATION`, additionally pulls the top-ranked still-valid
`KnowledgeObject`s per required `ContextCategory` (Decision, Task,
Constraint, Blocker, Research, OpenQuestion) **independent of lexical
overlap with the query** — ranked by the same non-lexical signals
`DeterministicRanker` already computes (importance, confidence, recency,
confirmation count), reusing `coverage_analyzer.resolve_category`'s
existing `MemoryType → ContextCategory` table to know which objects belong
to which category. Merged additively into the existing hybrid-retrieval
candidate pool before ranking — never replacing it.

- **Expected improvement:** **High.** This is the only candidate fix that
  addresses the actual, verified mechanism (§2, §3): candidate generation
  returning nothing because the query carries no lexical signal. It fixes
  exactly the case where the query has *zero* topical content — the case
  every other fix on this list either doesn't reach or reaches only
  weakly.
- **Complexity:** Medium. No new scoring system — reuses
  `DeterministicRanker`'s existing weighted formula and
  `coverage_analyzer`'s existing category-resolution table (already
  proven at `project_state.py:487-494`). The new piece is: iterate
  `MemoryStore.all()`, group by resolved `ContextCategory`, take top-N per
  category by `final_score` restricted to the non-lexical weight terms (or
  simply run the existing ranker over the *whole* store once and slice by
  category — no new ranking math). Needs its own tests for category
  grouping and the "additive, never replaces" merge contract.
- **Regression risk:** Medium. Must be scoped carefully:
  - **Must be additive, never a replacement**, so `CONTINUATION` queries
    that already retrieve well today (e.g. the ingestion design doc's own
    validated case, a query sharing real keyword overlap with the vault)
    are not diluted or re-ordered by injecting recency-only filler
    alongside genuinely relevant, lexically-matched content.
  - **Must respect the existing allocation budget**
    (`DeterministicSlotAllocator`'s `max_results`), or a vault with a large
    `Decision`/`Task` history could crowd out the very lexical hits that
    made a *good* `CONTINUATION` query work well before this change —
    exactly the flat-top-K crowding risk `PROJECT_STATE_EVALUATION.md` §7
    already flags as a distinct, pre-existing concern.
  - **Changes `query_structured`/`query_working_context` output for every
    `CONTINUATION`-classified query**, not just empty-signal ones — this
    is a real, observable behavior change to an existing task mode, not a
    purely additive new code path nothing else calls (unlike, e.g., the
    ingestion design's `HavenContinuationAdapter`, which was additive
    specifically because nothing else called it). It needs its own
    dedicated review pass, not a bundle with cheaper items on this list.

### 2. Extend `ContextPlanner`'s lexical pattern table to catch "what should I/we do next?"-style phrasings

- **Expected improvement:** Medium, and narrow — fixes exactly one of the
  four example queries (`"What should we work on next?"`), and only the
  "doesn't render `ProjectState`" half of that query's failure; it does
  nothing for the near-total retrieval failure the other three queries
  share.
- **Complexity:** Low. Add a handful of substrings (`"what should we"`,
  `"what should i"`, `"what's next"`, `"what next"`) to
  `_MODE_PATTERNS`'s `CONTINUATION` tuple (`context_planner.py:486-497`) —
  the same closed, hand-curated, first-match-wins table already used for
  the other seven patterns.
- **Regression risk:** Low. Purely additive to an existing lexical table
  with well-established precedent; the only care needed is checking new
  patterns don't false-positive against `CODING_DEBUGGING`'s patterns
  (checked here: none of `CODING_DEBUGGING`'s eleven patterns share a
  substring with the proposed additions, and `CONTINUATION` is already
  checked first in table order — `context_planner.py:478-483`'s own
  comment explains why that ordering exists).
- **Verdict:** Cheap and worth doing, but insufficient alone — necessary
  only for the one query this investigation's four examples that fails
  for a *different* reason than the other three.

### 3. Wire `QueryRewriter` into the production `MemoryEngine` construction sites

- **Expected improvement:** Low for these four queries specifically, and
  it's important to be precise about why: `QueryRewriter`'s system prompt
  (`query_rewriter.py:107-130`) only ever sees the raw query string — it
  has no access to vault content, concept labels, or what the project
  actually is. Asked to rewrite `"Continue."`, a capable LLM can only
  produce equally generic alternates ("Resume work", "Keep going") with no
  more vault-specific vocabulary than the original — because it has no
  way to know what vault-specific vocabulary would even help. This fix
  would plausibly help a *different*, narrower class of query: one with
  real but differently-worded topical content (e.g., "the retrieval
  pipeline" when the vault's own vocabulary is "HybridCandidateRetriever")
  — not the content-free orientation-seeking queries this investigation is
  about.
- **Complexity:** Low. The constructor parameter and merge logic already
  exist and are tested (`engine.py:682,745-751,769-772`); this is
  purely an operational change (construct a real `QueryRewriter` instance
  with `QUERY_REWRITER_API_KEY` configured, pass it at the three call
  sites in `server/main.py`/`server/dashboard.py`).
- **Regression risk:** Medium. Adds an outbound LLM call (latency, cost,
  and a new external-dependency failure mode) to every retrieval request
  in production — fails open per the module's own contract, so it's not
  *unsafe*, but it is a real infrastructure/product decision (added tail
  latency on every query) that deserves its own sign-off, separate from
  whether it fixes this specific symptom (it mostly doesn't).

### 4. Make `QueryRewriter` vault-aware (seed its prompt with active concept labels / a project summary)

- **Expected improvement:** Medium — could plausibly turn "Continue
  implementing the project" into something with real vault vocabulary if
  the rewriter is told what's currently active. Does not fully solve
  `"Continue."` alone (still needs *something* to seed from), and notably
  presupposes roughly the same "what's currently active" signal recommendation
  #1 would need to produce anyway — this option and #1 solve overlapping
  problems by different means.
- **Complexity:** High. Requires new plumbing to supply vault
  state into a module whose own docstring currently states "No ontology
  changes... nothing here reads or writes `obsidian.ontology`" as an
  explicit boundary (`query_rewriter.py:26-27`) — reversing that is a
  real architectural decision, not an incremental change.
- **Regression risk:** Medium-high. New coupling between a previously
  vault-blind module and live vault content; new nondeterminism; widens
  the LLM-prompt surface to include vault content for the first time in
  this module specifically.

### 5. Add embedding/semantic-similarity candidate generation as a third path

- **Expected improvement:** Potentially high for lexically-distant but
  topically-related queries — but explicitly does **not** solve the
  zero-content case ("Continue." has nothing to embed against
  meaningfully, whatever the corpus contains).
- **Complexity:** High. New dependency, new index, new infrastructure.
- **Regression risk:** High. Cuts directly across an explicit, repeated
  design boundary — "no embeddings, no fuzzy matching" appears as a stated
  constraint in `hybrid_candidate_retriever.py`, `keyword_candidate_retriever.py`,
  and `query_resolver.py`'s own docstrings, independently, in three
  different modules. Not a small change to any one of them; a genuine
  architectural addition.
- **Verdict:** Out of proportion to the problem. Not recommended for this
  symptom.

### 6. Wire `CategoryPreferenceScorer` into ranking for `CONTINUATION` queries (`PROJECT_STATE_EVALUATION.md` §9 recommendation #7)

- **Expected improvement:** Low **for this specific symptom.** This
  recommendation reweights candidates that already exist in the pool
  toward the categories `ContextPlanner`'s plan requires — but reweighting
  has nothing to act on when the pool is empty, which is exactly this
  investigation's finding for three of the four example queries. This is
  a good fix for a *different*, related problem (category imbalance
  *among* candidates that were found), already correctly scoped and
  ranked by that document as its own highest-leverage, highest-risk item.
- **Complexity / regression risk:** As already assessed in
  `PROJECT_STATE_EVALUATION.md` §9 (High complexity; changes ranking
  output for an existing call path deliberately left plan-blind today).
- **Verdict:** Not a fix for this investigation's symptom; do not conflate
  the two problems.

### 7. Loosen `KeywordCandidateRetriever` to fuzzy/substring/partial matching

- **Expected improvement:** Low-medium, and only for near-miss lexical
  cases — still does nothing for genuinely content-free queries.
- **Complexity:** Low-medium mechanically, but explicitly rejected by the
  module's own extensively-documented design decisions (three separate
  "Design decisions" entries explain exactly why fuzzy/substring matching
  was ruled out — the `"atlas"` → `"atla"` corruption risk named
  specifically, `keyword_candidate_retriever.py:144-156,180-184`).
- **Regression risk:** Medium-high — reintroduces a failure mode this
  module was deliberately built to avoid.
- **Verdict:** Not recommended.

---

## 5. Recommended approach

**Ship #1 (plan-driven category-fallback retrieval) as the primary fix,
paired with #2 (extend `ContextPlanner`'s pattern table) as a cheap,
independent companion — not a substitute for #1.**

Why this pairing and not a single item alone:

- **#1 is the only fix that addresses the verified mechanism.** §2's trace
  shows, concretely, per query, that `QueryResolver` and
  `KeywordCandidateRetriever` structurally cannot produce candidates for a
  query with no vault-specific vocabulary — not as a probabilistic risk,
  but as the guaranteed behavior of an exact-match-only pipeline against a
  content-free string. Every other fix on the list either doesn't reach
  this mechanism (#2, #6), reaches it only weakly (#3, #4), or reaches it
  at disproportionate cost/risk (#5, #7).
- **#2 must ship alongside it, not instead of it, because it fixes an
  orthogonal failure.** Even with #1 in place, `"What should we work on
  next?"` would still misclassify to `POINTED_QA` and never reach
  `ProjectStateBuilder` at all — #1's fallback only fires for
  `CONTINUATION`-classified queries. Conversely, #2 alone does nothing for
  the other three queries, which already classify correctly today and
  still fail. Neither is a substitute for the other; both are cheap enough
  and independent enough to ship together without one blocking the other.
- **#1's regression risk is real and specifically scoped, not open-ended.**
  It must be additive to existing hybrid-retrieval results (never a
  replacement), budget-aware (respecting the existing
  `DeterministicSlotAllocator` cap so it cannot crowd out genuinely
  lexically-matched content in a query that already worked), and reuses
  `DeterministicRanker`'s already-tested non-lexical scoring terms rather
  than inventing a second scoring system — §3's table already confirms
  those terms alone (importance/confidence/recency/confirmation, combined
  weight 0.65 of 1.40) clear `AcceptanceStage`'s default abstention floor
  for an ordinary candidate, so no threshold changes are needed downstream
  for the fallback candidates to survive acceptance.
- **Everything else on the list is either solving a different problem
  (#4, #6), disproportionate to this one (#5, #7), or real but secondary
  (#3 — an operational decision with low expected payoff for exactly the
  query shape this investigation is about).**

This document makes no claim about exact implementation shape (e.g.,
whether the fallback lives inside `HybridCandidateRetriever`, as a new
sibling stage in `_run_retrieval`, or elsewhere) — per this task's scope,
that is a follow-on design decision, not part of this root-cause analysis.
