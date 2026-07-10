# Why Candidate-Generation Improvements Are Becoming Ranking Failures

Status: Analysis only. No production code, benchmark dataset, ontology
file, or scoring logic was modified to produce this document. All numbers
were measured by running Haven's real, unmodified pipeline
(`OntologyPipeline` -> `HybridCandidateRetriever` -> `DeterministicRanker`
-> `AcceptanceStage` -> `MemoryEngine.query_with_trace`) against the
current working tree, using a throwaway, uncommitted harness script
(`scripts/ranking_failure_audit.py`-equivalent, written for this
investigation and deleted after use — same convention
`ENTITY_CAT_INVESTIGATION.md` and `LEXICAL_NORMALIZATION_COMPLETE.md`
already established for their own audits).
Version: 1.0

---

# 0. Method

Same harness shape as the three prior audits in this directory: for each
of the 288 benchmark files under `benchmarks/datasets/*/*.json`, every
`conversation` entry is written verbatim as its own `KnowledgeObject`
(`infer=False`, matching `HavenAdapter.add`), run through the real
`OntologyPipeline.process`, then `MemoryEngine.query_with_trace(query)` is
called and the returned `RetrievalTrace` is read — nothing is
reimplemented.

**One correction to the prior audits' own method, found and fixed before
trusting any number below.** The prior harnesses picked "the target" as
*the first* conversation entry containing every string in
`expected.answer_contains`. When a conversation contains more than one
qualifying entry (e.g. two turns both mention "jazz," or an earlier
options-listing turn happens to repeat a word from the answer), that
picks the wrong ground truth and can misreport a fine outcome as a
"ranking failure." This audit instead collects *every* qualifying entry
and passes the case if *any* of them is accepted — always well-defined,
since `AcceptanceStage`'s five stages only ever shrink a score-sorted
prefix, so if the best-ranked qualifying entry isn't accepted, no
worse-ranked one is either. This fix flipped 2 cases
(`concept_consolidation_basic_015/033`, two facts that both assert "I
listen to jazz," previously miscounted as a ranking failure) from
false-positive `RANKING_FAILURE` to correct `PASS`, and 1 case
(`decisions/basic_017`, where an early options-listing turn also contains
the literal word "search") similarly resolved correctly. Reported because
it changes the denominator this document's counts are built on.

**Coverage.** Same as prior audits: 288 total cases, 38 excluded as
ungradeable (`concept_consolidation`/`beliefs` synthesis cases whose
answer isn't verbatim in one turn), **250 gradeable cases**.

**Before/after comparison.** `obsidian/ontology/category_taxonomy.py` and
the `OntologyManager.propose()` IS_A-bridge logic
(`ENTITY_CAT_INVESTIGATION.md`'s recommended, narrowly-scoped Phase 2) are
present and enabled in the current working tree — this is the
"candidate-generation improvement" the task brief refers to. To isolate
its effect, the harness was run twice: once against the current tree, and
once with `CATEGORY_TAXONOMY` monkeypatched empty (no file changed) to
reconstruct the pre-bridge baseline. Both runs are the real pipeline;
nothing about `DeterministicRanker`, `AcceptanceStage`, or the benchmark
files themselves differs between them.

---

# 1. Top-level results (Task 1, part 1)

| Outcome | Before (bridge disabled) | After (current tree) | Δ |
|---|---|---|---|
| PASS | 186 | 192 | +6 |
| Candidate-generation failure | 41 | 32 | −9 |
| **Ranking failure** | 23 | **26** | **+3** |
| (Ungradeable) | 38 | 38 | — |

Confirmed by full before/after case-ID diff (not just aggregate counts):

| Transition | Count | Cases |
|---|---|---|
| CANDIDATE_GEN_FAILURE -> PASS | 6 | `decision_reconstruction_016` (Mediterranean), `decisions/basic_020` (Celery), `supersession_003` (GPT/Qwen), `supersession_033` (PostgreSQL), `supersession_034` (Svelte), `supersession_054` (Linear/Jira) |
| **CANDIDATE_GEN_FAILURE -> RANKING_FAILURE** | **3** | `decision_reconstruction_009` (ThinkPad), `decision_reconstruction_018` (GitHub Actions), `decision_reconstruction_019` (Obsidian) |
| RANKING_FAILURE -> RANKING_FAILURE (unaffected) | 23 | every other current ranking failure |
| any PASS -> anything else (regression) | **0** | none — confirms the bridge is additive, as designed |

This directly answers the task's framing: **3 of the 26 current ranking
failures are literally candidates that the IS_A bridge newly created** —
before the bridge, these 3 queries' correct answer was never a candidate
at all (the exact `ENTITY_CAT` shape `ENTITY_CAT_INVESTIGATION.md`
documented); now it reaches the ranker, but loses there instead. The other
23 ranking failures are pre-existing and untouched by this specific
change — investigated below because the task asks for the full root-cause
picture, not just the 3.

---

# 2. Root-cause classification of all 26 ranking failures (Task 2, Task 3)

**Headline fact, verified directly from every trace, not assumed:** all
26 are rejected by exactly one `AcceptanceStage` stage.

| Rejection mechanism (`CandidateTrace.rejection_reason`) | Count |
|---|---|
| `score_gap_cut` (stage 3) | **26 / 26 (100%)** |
| `below_minimum_score`, `below_abstention_floor`, `below_relative_floor`, `acceptance_cap_exceeded` (stages 1, 2, 4, 5) | 0 |
| `slot_budget_exceeded` (Slot Allocator, downstream of acceptance) | 0 |

Zero candidates in this corpus are rejected by abstention, the relative
floor, the hard cap, or the context-budget slot allocator — every single
ranking failure is the score-gap cut specifically. That narrows the
investigation to one question: **why does a gap `>= 0.04` open up between
the correct answer and a wrong candidate ranked above it?**

To answer that, every one of the 26 was diffed component-by-component
(`RankedCandidate.score_breakdown`, seven named contributions) between the
correct answer and the candidate that beat it. The result is unambiguous:

| Finding (verified across all 26, not sampled) | |
|---|---|
| `importance`, `confidence`, `recency`, `confirmation_count` contributions | **Identical between the correct answer and its beater in all 26 cases** (delta = 0.0 exactly, to floating-point noise) |
| The entire score gap, every time | Explained by `keyword_overlap` alone, `activation` alone, or both together |

The `importance`/`confidence` tie is structural: `HavenAdapter.add` writes
every turn with the same default values (`infer=False` — no LLM
classification runs). The `recency`/`confirmation_count` tie is a harness
artifact: every conversation's turns are inserted within milliseconds of
each other in this benchmark runner, so `age_days` is ~0 for every turn
and `RECENCY_SCALE_DAYS=7.0` cannot differentiate them; `confirmation_count`
starts at 0 and nothing in this write path increments it. **In a real
vault, where turns are written hours or days apart, `weight_recency`
would very likely resolve several of the cases below on its own — this
benchmark's near-instantaneous insertion timing structurally silences the
one component built to detect "this is the newer statement."** That's a
benchmark-fidelity gap, not a ranking-algorithm defect, and it isn't
something this analysis-only task can fix (no benchmark changes) — but it
bounds how much weight to put on any recommendation below that touches
`weight_recency`.

## 2.1 Which single component decided each case

| Driving component | Count | % of 26 |
|---|---|---|
| `keyword_overlap` alone | 21 | 81% |
| `activation` (+ `attachment_relevance`) alone | 5 | 19% |

### The `keyword_overlap`-driven group (21) splits into two narrative shapes

**Shape A — "compare N options, then decide" (12 of the 21).** The
conversation's *options-listing* turn repeats the query's own category
noun ("laptop," "CI/CD," "cloud provider," "message queue," "design
tool," "major," "car," "task scheduling system" — see
`keyword_candidate_retriever.py`'s scoring: `score = (IDF-weighted
overlap) / (IDF-weighted query keywords)`, i.e. *what fraction of the
query's own vocabulary appears in this fact*). The actual *decision* turn
("Went with the ThinkPad," "Going with GitHub Actions," "Going with
Saatva") almost never repeats that category noun — it names the winner
instead — so it scores far lower on this axis even though it is the
answer.

Cases: `decision_reconstruction_003/004/009/012/013/017/018/019/022`,
`decisions/basic_017`, `temporal_017/018`.

Three of these nine `decision_reconstruction` cases (`009` ThinkPad,
`018` GitHub Actions, `019` Obsidian) are the exact 3 IS_A-bridge-created
candidates from §1 — for these three specifically, `activation` and
`attachment_relevance` are **numerically tied** between the options turn
and the decision turn (both attach to the same curated instance concept
with identical relevance), so raising `weight_activation` would not help;
the entire gap is `keyword_overlap`, because the options turn additionally
repeats the category word ("laptop," "CI/CD," "note-taking app") that the
decision turn doesn't.

**Shape B — "superseded belief/preference restated more plainly" (8 of
the 21).** No options-list structure; a plain earlier statement ("I
currently believe GraphRAG is the most important component," "I go to the
gym in the mornings," "For this trip I want a window seat") is
subsequently revised ("After further research, I now believe Manager AI
is more important...," "I've switched to evening gym sessions,"
"Actually, I'd rather have an aisle seat"). Both are keyword-path-only
(`matched_by_ontology=False` on both sides — no capitalized entity for
`ConceptDetector` to attach to). The revision statement's own wording
("after further research," "I've changed my mind," "actually") is *new*
vocabulary relative to the query, so it doesn't help the overlap ratio,
while the original statement more directly echoes the query's own phrasing.
Spot-checked against the raw text: this is a genuine **paraphrase gap**
(e.g. `supersession_043`'s "I'd rather have an aisle seat" vs. the query's
own verb "want," present verbatim only in the superseded "I want a window
seat"), the same PARAPHRASE shape `LEXICAL_NORMALIZATION_COMPLETE.md`
already concluded is out of reach for a closed inflectional-variant table
(§4 of that document: "these need semantic matching, not lexical
matching").

Cases: `belief_basic_001`, `beliefs_016`, `contradictions_basic_009`,
`supersession_011/012/014/043/050`.

**One more, unrelated shape (1 of the 21, not a near-miss at all):
`refinements_basic_025`.** "What does the user own at work?" is answered
by "I own the deployment pipeline and the CI configuration," but "We
adopted a new design system at work" — an unrelated fact — outranks it
purely because it shares the generic token "work." A false lexical
collision, not a competing answer.

### The `activation`-driven group (5)

`decision_reconstruction_005` (car), `decision_reconstruction_008`
(venue), `decision_reconstruction_025` (mattress), `decisions/basic_012`
(REST/GraphQL), `temporal_023` (API gateway). Here `keyword_overlap` is
either tied or not the deciding term; the options-listing turn instead
gets a materially higher `activation` contribution than the decision turn,
despite both turns attaching to the same named entities. Two of the five
were traced to a specific, already-documented cause: `venue` and
`mattress`
(`decision_reconstruction_008`/`025`) — both beater facts begin their
sentence with the category word itself ("**Venue** hunting for the
wedding...," "**Mattress** shopping..."), which is exactly the
sentence-initial-capitalization noise-concept artifact
`ENTITY_CAT_INVESTIGATION.md`'s verification addendum already flagged as
graph noise ("`Confirmed`, `Need`, `Tried`, `Still`... none of which are
real entities") — except here it isn't just noise sitting unused in the
graph, it actively **wins a query seed**: the query itself contains
"venue"/"mattress," `QueryResolver`'s token pass matches the noise concept
`Venue`/`Mattress`, and that turn — being the noise concept's *own*
`KnowledgeObject` — gets counted as a direct, depth-0 attachment, out-
activating the real decision turn's weaker, propagated evidence for the
same named entities. This is a previously undocumented, code-traceable
mechanism distinct from ordinary keyword competition.

---

# 3. Mapping to the requested taxonomy (Task 2, exact counts)

| Root cause | Count | % of 26 |
|---|---|---|
| **Competing near-miss** (a genuinely topic-adjacent turn — the options-listing/setup turn — outranks the true answer; covers both the keyword-driven Shape A above and the activation-driven group) | **17** | 65% |
| **Keyword dominance** (a superseded/contradicted prior statement outranks the current one purely because it echoes the query's vocabulary more directly; no ontology evidence on either side) | **8** | 31% |
| **Other** (false lexical collision on an unrelated generic word) | **1** | 4% |
| `score_gap_cut` as a *distinct* cause (i.e., a case with no more specific driver — both candidates equally relevant, threshold just too tight) | 0 | — |
| Recency dominance | 0 | — |
| Ontology under-weighted | 0 | — |
| Acceptance threshold (stages 1/2/4/5, independent of the gap cut) | 0 | — |
| Duplicate candidates | 0 | — |
| Incorrect ordering (sort/tie-break itself wrong, given the scores) | 0 | — |
| Context budget cutoff (Slot Allocator) | 0 | — |

Why the zero rows are genuinely zero, not just unobserved:

- **`score_gap_cut`-as-sole-cause / acceptance threshold**: every one of
  the 26 traces back to a specific, nameable scoring asymmetry (§2), never
  to "the threshold itself is simply too aggressive on two equally good
  candidates." The mechanism that *rejects* every case is the gap cut, but
  the *cause* is always upstream, in what produced the gap.
- **Recency dominance / ontology under-weighted**: numerically excluded
  above — recency is tied to the float in all 26 (harness timing, §2),
  and for the bridge-created cases, ontology contributions are *tied*, not
  insufficient, so no amount of reweighting `weight_activation` moves
  them.
- **Duplicate candidates**: the closest near-miss in this corpus
  (`concept_consolidation_basic_015/033`, two non-contradictory "I listen
  to jazz" turns) is not a failure at all once graded correctly (§0) —
  both are accepted, and either would satisfy a real judge.
- **Incorrect ordering / context budget cutoff**: `RankedCandidate`'s sort
  is a straightforward descending-score order, which is doing exactly what
  the (flawed) scores tell it to; and the largest candidate pool observed
  across all 26 traces is 5 — nowhere near `max_results=50`, so the Slot
  Allocator never binds in this benchmark (consistent with
  `ACCEPTANCE_STAGE_DESIGN.md` §2.4's own observation that the small
  benchmark/demo vaults understate real-vault-scale effects).

---

# 4. Which of these are fixable without embeddings or LLM retrieval (Task 4)

All 26 have real, scored evidence for the correct answer already flowing
through the deterministic pipeline — none require semantic similarity to
*find* the answer, only to *rank* it correctly once found. That said,
they differ sharply in how cheaply a deterministic fix reaches them:

| Tier | Cases | Deterministic lever | Confidence |
|---|---|---|---|
| **1 — already has ontology evidence on both sides** | 9 (`decision_reconstruction_009/018/019`, `temporal_018`, the 5 activation-driven cases) | `AcceptanceStage` already has the signal it needs (`Candidate.supporting_concepts` overlap between the top pick and the next candidate) — no new detector, no new table | High |
| **2 — no ontology evidence today, but the missing category noun is a well-known, curatable term** | 8 (`decision_reconstruction_003/004/012/013/017/022`, `decisions/basic_017`†, `temporal_017`) | Extend `category_taxonomy.py` with more curated instance->category pairs, same closed-table discipline already shipped, *then* Tier 1's fix applies | Medium — see the alias-brittleness caveat below |
| **3 — genuine paraphrase / revision-marker gap, no shared entity at all** | 8 (the belief-supersession group + `refinements_basic_025`) | A closed, hand-reviewed "revision marker" phrase list (`"actually"`, `"I've changed my mind"`, `"after further research"`, `"I've switched to"` — same review discipline as `_VARIANT_GROUPS`) recovers most, not all | Lower — 1 of the 8 (`contradictions_basic_009`) has no such marker at all |

† `decisions/basic_017`'s missing category ("service") is a lowercase
common noun in the source text, not a proper-noun instance —
`ConceptDetector`'s capitalized-span heuristic would never promote it
regardless of taxonomy size; this specific case actually belongs to the
already-documented `ONTOLOGY_COV` bucket, explicitly out of scope for the
category-taxonomy mechanism (`ENTITY_CAT_INVESTIGATION.md` Task 4, finding
B).

**A caveat that materially affects Tier 2's ceiling, found while
checking it, not assumed:** two of these eight already have their
instance curated in `category_taxonomy.py` today (`Linear`/`Jira` for
`decision_reconstruction_012`, `Celery` for `temporal_017`) and **still
show zero ontology evidence**, because the query's own words ("project
management tool," "task scheduling system") don't match the curated
alias ("tracking," "background"). `QueryResolver` only does whole-phrase
or single-token exact `AliasIndex` lookups — extending the taxonomy helps
only the queries that happen to phrase the category the way the curator
guessed; it does not helps queries that describe the category
differently, and guessing every real-world phrasing is the same
open-ended maintenance burden `CANDIDATE_GENERATION_DECISION.md` already
flagged. This is why Tier 1 (which needs no vocabulary guess at all — it
only needs *some* shared concept, regardless of which words the query
used to get there) is the higher-confidence lever.

---

# 5. Theoretical ceiling from deterministic ranking improvements only (Task 5)

| Program | Recovers | Cumulative | % of 26 | % of 250 gradeable |
|---|---|---|---|---|
| Tier 1 only (`AcceptanceStage` shared-evidence exemption) | 9 | 9 | 35% | 3.6% |
| Tier 1 + Tier 2 (+ curated taxonomy expansion) | up to 8 more (alias-brittleness discounted, see §4) | up to 17 | up to 65% | up to 6.8% |
| Tier 1 + 2 + 3 (+ closed revision-marker list) | up to 7 more (excludes `contradictions_basic_009`) | up to 24 | up to 92% | up to 9.6% |

**Absolute ceiling: 24–25 of 26 (92–96%), i.e. at most ~9.6% additional
absolute pass-rate gain on the 250 gradeable cases, if every tier above is
built.** `contradictions_basic_009` is the one case with no lexical,
temporal, or ontology signal distinguishing the correct answer from its
contradiction at all in this write path — recoverable only by real
contradiction-resolution logic (well beyond "ranking improvement") or by
an LLM judge tolerant of the ambiguity, which is exactly how the real
benchmark (not this deterministic proxy) already grades it.

This is a **ceiling for the ranking layer specifically**, on top of
whatever the still-open 32 candidate-generation failures (unaffected by
this analysis) could separately contribute — the two are independent
budgets, not additive without double-checking overlap.

---

# 6. Recommendation: smallest fix set for the highest gain (Task 6)

**Ship Tier 1 alone first: add a shared-supporting-concept exemption to
`AcceptanceStage`'s stage 3 (score-gap cut).**

Concretely (design only, not implemented here): when the candidate about
to be cut shares at least one `ActivatedConcept.concept_id` with the
candidate immediately above it in the sorted list, skip the gap cut for
that pair (fall through to stage 4's relative-floor check instead, which
is anchored to the query's own top score rather than a fixed absolute
gap). This is the same posture `ACCEPTANCE_STAGE_DESIGN.md` §4.7 already
committed to for single-path keyword ties ("keep both... let the LLM
disambiguate from the fuller context — not to guess") — this is that same
principle, extended to a case the original design didn't have evidence
for yet: two candidates that are demonstrably *about the same thing* by
the pipeline's own already-computed graph evidence, not merely two
generically similar keyword hits.

Why this over the alternatives, using the same comparison discipline
`CANDIDATE_GENERATION_DECISION.md` used for its own recommendation:

| Approach | Cases recovered | New surface area | Overfitting risk | Architectural fit |
|---|---|---|---|---|
| **Tier 1: shared-evidence gap-cut exemption** | 9/26 (35%) | None — reuses `Candidate.supporting_concepts`, already computed | Low — the signal is "these two facts are graph-connected to the same concept," true regardless of query phrasing | Perfect — a same-module refinement to a stage whose own design doc already reasons this way |
| Tier 2: expand `category_taxonomy.py` | up to 8 more, but 2 already-curated instances in this very corpus show the alias-phrasing brittleness that caps its real yield | A new curated table entry per category, forever | Medium — same taxonomy-staleness cost `ENTITY_CAT_INVESTIGATION.md` already priced in, now with a demonstrated alias-mismatch failure mode on top | Good, but strictly the more expensive, less certain of the two ontology-side levers |
| Tier 3: revision-marker phrase list | up to 7 more | A new closed table + new comparison logic in the ranker or acceptance stage | Medium-high — reweights a *reason for supersession*, not a topic/entity match; only as good as the marker list's coverage of real phrasing, and doesn't touch `contradictions_basic_009` at all | Weakest fit of the three — first genuinely new heuristic, not a reuse of existing evidence |
| Reweighting `weight_keyword_overlap`/`weight_activation` globally | Unclear, likely net-negative | None | High — moves every other passing case's score too; the bridge-created 3 cases are *tied* on activation, so this wouldn't even help them | Touches a globally-shared config value used by all 250 cases, the definition of a large blast radius for a "smallest fix" ask |

**Do not reweight `RetrievalConfig`'s scoring weights as the first move.**
It was checked directly (§2) and would not resolve the three cases this
task specifically asks about (the bridge-created ones) because their
`activation`/`attachment_relevance` contributions are already numerically
tied — the lever that's actually broken there is the score-gap *cut*, not
the score *itself*.

**Do not chase Tier 3 next.** Its ceiling (up to 7) is smaller than Tier
1's confirmed 9, for a materially newer piece of logic (a phrase-marker
comparison the acceptance/ranking stages don't do anything like today),
and it still leaves the one case (`contradictions_basic_009`) with no
marker to key off — the same "smaller ceiling, higher complexity" shape
`CANDIDATE_GENERATION_DECISION.md` already used to reject the original
`IS_A` bridge in favor of lexical normalization.

**If more gain is wanted after Tier 1 ships:** revisit Tier 2, but budget
for the alias-brittleness finding in §4 — `Linear`/`Jira`/`Celery` are
already curated and still miss two of this corpus's own queries, so
expanding the table further will under-deliver relative to its raw
case count unless paired with a broader query-to-category matching
strategy (out of this document's "no scoring changes" scope to design).

---

# 7. Explicit non-goals

- **Not re-litigating the 32 remaining candidate-generation failures.**
  Confirmed unaffected by the bridge (§1's diff) and already exhaustively
  classified in `ENTITY_CAT_INVESTIGATION.md`; out of this document's
  ranking-focused scope.
- **Not proposing a `DeterministicRanker` scoring-formula change.** Every
  finding above stays inside `AcceptanceStage` (Tier 1) or existing
  curated-table patterns (Tiers 2–3) specifically because the task
  constrains this pass to "no scoring changes" — a scoring-formula
  redesign (e.g., making `keyword_overlap` and ontology evidence interact
  instead of summing independently) might have a higher ceiling than
  92-96%, but is a materially larger, riskier change this analysis
  deliberately does not scope.
- **Not fixing the benchmark harness's flat insertion timing.** Noted in
  §2 as the reason `recency`/`confirmation_count` never differentiate any
  of these 26 cases, and as a likely source of real-vault-scale
  improvement this benchmark structurally cannot measure — out of scope
  under "no benchmark changes."
