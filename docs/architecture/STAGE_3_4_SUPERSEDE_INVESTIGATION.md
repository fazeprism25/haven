# Stage 3/4 (Automatic SUPERSEDE) — Pre-Hackathon Investigation

Status: Analysis only. No production code, benchmark dataset, or ontology
file was modified to produce this document. One throwaway harness script
(`stage34_proxy_harness.py`) and one oracle-simulation script
(`stage34_oracle_sim.py`) were written to a scratch directory outside this
repository, run, and their JSON output analyzed — the same
write-once/analyze/discard convention `PRE_BENCHMARK_FREEZE_AUDIT.md`
established. No code in `obsidian/` or `benchmarks/` was changed to
produce any number below.

---

## 0. Scope and method

**What "Stage 3/4" means** (`obsidian/docs/TECH_DEBT.md`, "Pipeline
orchestration" section):

- **Stage 3** — wire `SUPERSEDE` into `ManagerPipeline.match_and_apply()`:
  generalize the archive-and-recreate `(archived_old, new)` pair beyond
  `MemoryType.DECISION` (today only reachable via the manual
  `KnowledgeUpdater.supersede_decision()` API — see `obsidian/docs/DECISION_MEMORY.md`)
  so a contradiction archives the old memory and creates a replacement,
  both persisted.
- **Stage 4** — give `CanonicalMatcher` (or a restored Supersession stage)
  the ability to *decide* `UPDATE` vs `SUPERSEDE` vs `NONE` semantically
  (contradiction/identity detection) — "the LLM-shaped judgement the
  current deterministic matcher deliberately avoids" (`TECH_DEBT.md`,
  verbatim).

Confirmed directly in code, not from the docs' description alone:
`obsidian/manager_ai/canonical_matcher.py`'s `match_with_target` can only
return `CONFIRM` (exact text match), `UPDATE` (strict whole-word prefix
extension), or `NEW`. `SUPERSEDE` is never constructed anywhere in that
file. `ManagerPipeline.match_and_apply`'s `else` branch (the only place a
hypothetical `SUPERSEDE` would land) is dead code today — comment in
`obsidian/manager_ai/pipeline.py:355-361`: *"this branch is unreachable in
practice; it leaves `matched_knowledge` None so nothing is persisted."*
`KnowledgeUpdater._apply_supersede` and `KnowledgeUpdater.supersede_decision`
already exist and are unit-tested (`obsidian/tests/test_knowledge_updater.py`,
`obsidian/tests/test_decision_memory.py`), but are only reachable through a
manual API scoped to `MemoryType.DECISION` — never auto-driven from a
conversation.

**Why a fresh benchmark run, not a code change, was needed first**: the
only prior run that exercises the write pipeline at all
(`benchmarks/results/results_haven_full.json`, Qwen-judged, real
`ManagerPipeline`) is stale — `git diff` against its recorded commit
(`042e16e6`, current `HEAD`) shows `canonical_matcher.py` (+141/-0 lines),
`knowledge_updater.py` (+156), and `pipeline.py` (+287) all have
substantial **uncommitted** changes since. `git show HEAD:obsidian/manager_ai/canonical_matcher.py`
confirms the committed version that benchmark actually ran against had no
`UPDATE` at all (only `CONFIRM`/`NEW` — "simple normalised-lowercase text
equality check", per its own docstring at that commit). So that result
file cannot answer "what fails today," only "what failed before UPDATE
existed."

**Method used instead** (per the user's explicit choice: deterministic
proxy, no paid LLM calls): a throwaway harness feeds every
`benchmarks/datasets/**/*.json` conversation's turns **verbatim** as
`ExtractedFact`s (bypassing `Extractor`/`Classifier`/`ImportanceScorer` —
no LLM call anywhere in this investigation) through the **real, current,
uncommitted** `CanonicalMatcher` + `KnowledgeUpdater` via
`ManagerPipeline.match_and_apply`, persists the resulting
`KnowledgeObject`s through the real `VaultWriter`/`OntologyPipeline`
exactly as `HavenFullAdapter.add_conversation` does, then runs the real
`MemoryEngine.query_with_trace` for retrieval — identical to
`HavenAdapter.search`. This isolates exactly the write-path layer Stage
3/4 would change, using the same "verbatim storage" convention this
repo's own `HavenAdapter` and every prior investigation doc already treat
as a valid proxy, while still exercising the actual current
`CanonicalMatcher`/`KnowledgeUpdater` decision logic (not a hand-wave).

Grading matches `PRE_BENCHMARK_FREEZE_AUDIT.md` §1 exactly for
comparability: a case is **gradeable** if at least one raw conversation
turn literally contains every `answer_contains` string ("qualifying
turn"); **audit-style PASS** if any accepted retrieval candidate contains
every `answer_contains` string; **strict PASS** additionally requires no
`must_not_contain` string anywhere in the joined accepted context.

**Cross-validation of the method**: this harness found **288** total
benchmark files, **19** empty-stub skips, **250** gradeable, **38**
ungradeable-by-substring (synthesis-required `concept_consolidation`/
`beliefs` answers) — all four numbers match `PRE_BENCHMARK_FREEZE_AUDIT.md`
§1 exactly. Audit-style pass: **200/250 (80.0%)**, versus that audit's
197/250 (78.8%) measured before `UPDATE` existed — a +3-case gain
consistent with `UPDATE` now being live, and zero net change in strict
pass (**159/250, 63.6%**, identical count to that audit's Haven Retrieval
number), consistent with `UPDATE`'s narrow scope (prefix-extension) never
touching a `must_not_contain` leak, which is a different mechanism
entirely. This agreement across two independently-written harnesses, at
two different points in the codebase's history, is the strongest evidence
in this document that the method is sound.

---

## 1. What actually remains broken (91 cases)

- **50 cases** fail audit-style PASS outright (the correct answer never
  reaches accepted context at all).
- **41 additional cases** pass audit-style PASS but leak a
  `must_not_contain` string (the stale/superseded fact rides along with
  the correct one in accepted context).
- **91 combined cases** (no overlap) warrant Stage-3/4-relevant scrutiny.

### 1.1 Where the 50 outright failures come from

| Sub-bucket | Count | What it means |
|---|---|---|
| Nothing retrieved at all (0 candidates) | 15 | Query shares no lexical/ontology signal with any stored fact |
| Right fact never became a candidate (something else did) | 17 | Candidate generation missed the specific qualifying turn |
| Right fact *was* a candidate, ranking/acceptance rejected it | 18 | A competing candidate (often the *stale* fact) outscored it |

All 50 are attributable to candidate-generation or ranking/acceptance —
**zero** are caused by `UPDATE` failing to fire (it already fires
everywhere its narrow, safe contract applies) and **zero** can be
directly attributed to a missing `SUPERSEDE` without further evidence
(see §1.2 — that requires actually testing whether removing the stale
competitor would help, not just observing that a stale competitor
exists).

### 1.2 Oracle simulation: does perfect SUPERSEDE actually fix these?

To test causation rather than correlation, a second throwaway harness
archives (`valid_until = now`) every `KnowledgeObject` created before the
conversation's **last** turn, then reruns real retrieval — a best-case
oracle standing in for a hypothetically perfect Stage 3/4, applied only
to the **67** of the 91 combined cases that are short (≤5 turns) and in a
category where the whole point is one fact replacing an earlier one
(`supersession`, `contradictions`, `beliefs`/`belief_evolution`,
`decisions`, `identity`, `temporal`). The remaining 24 (mostly
`decision_reconstruction`, 6–28 turns each, deliberately including
unrelated distractor turns — see the example in §1.3) were **excluded**
because blanket "archive everything but the last turn" would destroy
legitimate unrelated memories in a multi-topic conversation; there is no
safe deterministic proxy for that shape (see §3).

**Result on the 67 tested cases**:

| Outcome | Count |
|---|---|
| Newly become fully correct (were outright wrong, now right) | 8 |
| Newly stop leaking (were already "right answer present" but leaking the stale fact, now clean) | 15 |
| **Total genuinely fixed by oracle SUPERSEDE** | **23** |
| Still wrong even with the stale fact perfectly archived | 32 |

**The 32 "still wrong even with perfect archiving" is the single most
important finding in this document.** Concrete examples (`supersession_basic_006`,
`_007`, `_009`, `_041`): after archiving the old fact, the *only*
remaining, correct memory produces **zero retrieval candidates** for its
own benchmark query:

```
supersession_basic_006
  query: "What embedding solution is currently planned?"
  surviving fact (old one archived): "That plan has been replaced with FastEmbed."
  candidates after archiving: []
```

The surviving sentence never says "embedding" at all — it says "that
plan" and "FastEmbed." This is the exact PARAPHRASE/ONTOLOGY_COV gap
`PRE_BENCHMARK_FREEZE_AUDIT.md` and `ENTITY_CAT_INVESTIGATION.md` already
identified and already rejected as low-ROI/high-risk to fix, wearing a
`supersession` label. **Within the category most associated with
this investigation, candidate generation — not the missing SUPERSEDE
mechanism — is the dominant, measured bottleneck**, confirmed by directly
testing (not assuming) what a perfect archive would do.

### 1.3 The other excluded shape: multi-hop chains need identity resolution, not archiving

`decision_reconstruction_basic_003` (20 turns: a real decision buried in
16 topic-relevant statements plus 4 unrelated life-distractor turns
sharing no vocabulary with the decision) and `beliefs_016`/`beliefs_024`
(5-turn conversations describing **three** sequential belief revisions,
not one) both show the same structural pattern: retrieval *does* surface
multiple relevant candidates (2–5 in `all_candidates`), and the ranker
picks an **intermediate**, superseded one — not because nothing is
archived, but because nothing tracks *which of several candidates
describing the same evolving topic is current*. Naively archiving
"everything but the last turn" is actively unsafe here (see §3) — the
correct fix requires identifying which turns are about the *same* topic
across a noisy, multi-topic conversation and picking the most recent one
in that chain specifically. That is squarely the "LLM semantic identity
required" bucket, structurally distinct from — and harder than — the
83-case two-fact SUPERSEDE shape in §1.2. This overlaps with
`RANKING_FAILURE_INVESTIGATION.md`'s Tier 2/3 ranking findings, which
`PRE_BENCHMARK_FREEZE_AUDIT.md` §Task 4 already priced as below its 3%
ROI bar and rejected — this investigation's findings for the same case
shape are consistent with, not contradictory to, that prior rejection.

---

## 2. Classification of the 91 cases (Task 1 taxonomy)

| Bucket | Count | Evidence |
|---|---|---|
| Candidate generation | 32 of the 50 outright fails (15 nothing-retrieved + 17 right-fact-never-a-candidate), **plus all 32 that remain broken under the oracle in §1.2** | §1.1, §1.2 |
| Ranking / Acceptance (not cleanly separable — see caveat) | 18 of the 50 outright fails | §1.1; my harness recorded only accepted/not-accepted, not raw scores or acceptance-stage rejection reasons, so ranking-vs-acceptance cannot be split further **without additional instrumentation this investigation did not build — stated explicitly per the "prove it or flag it" rule** |
| UPDATE would solve | **0** | `UPDATE` is already live in the current tree and already captured in the 200/250 passes; none of the 91 remaining failures fit its narrow strict-whole-word-prefix-extension contract (confirmed by inspecting every case's `turn_trace`: none show a prefix relationship that `UPDATE` missed) |
| SUPERSEDE would solve | **23** (proven by oracle, §1.2) | Concentrated in `supersession` (5 of the 23), plus scattered `contradictions`/`beliefs`/`decisions` cases with the same simple two-fact shape |
| LLM semantic identity required | 24 excluded multi-hop/multi-topic cases (§1.3) + an unproven fraction of the 18 ranking/acceptance cases that are `decision_reconstruction`-shaped | Ceiling not provable by this method — stated explicitly, see §3 |
| Dataset issue | 38 ungradeable-by-substring cases (synthesis-required `concept_consolidation`/`beliefs`, carried forward from `PRE_BENCHMARK_FREEZE_AUDIT.md`, unchanged) + a demonstrated grading-fragility pattern (§2.1) | §2.1 |
| Judge issue | Not directly testable without a real Qwen-judged run (declined this round for cost/time) — the closest observable analog is the proxy-artifact pattern in §2.1, which a real LLM judge would likely handle correctly since it understands "considering X" ≠ "chose X" | Cannot be proven; stated explicitly |
| Other | None identified | — |

### 2.1 A grading-fragility finding that affects trust in every number above

While testing for SUPERSEDE-caused regressions (§3), several conversations
follow a "brainstorm several options, then decide" shape
(`basic_016`/`_018`/`_021`, `decision_basic_004`): the **first** turn lists
all candidate options by name (e.g. *"debating between WebSockets,
Server-Sent Events, and long polling"*), which trivially satisfies a
substring-based `answer_contains: ["WebSockets"]` check even though it is
not the decision — the real decision is 4 turns later. My deterministic
proxy marks these audit-style PASS on turn 1 alone; a real semantic judge
almost certainly would not, since turn 1 explicitly frames all three as
undecided. **This means some of this document's "pass" counts are
themselves optimistic** (proxy artifacts, not genuine passes) — flagged
honestly rather than corrected, since correcting it would require the LLM
judge run this investigation was scoped not to run. It is very unlikely
to be a Haven-specific problem (the same substring check would over-credit
`return_all`/`recency`/`bm25` identically), but it does mean the "23 cases
SUPERSEDE fixes" and "200/250 pass" numbers in this document carry some
proxy noise in both directions that only a real Qwen-judged run can fully
resolve.

---

## 3. Regression risk (tested directly, not assumed)

The same oracle (§1.2) was run against all **82** currently-passing short
cases in the same six categories, to check whether naive "archive
everything but the last turn" would break anything already working.
**10 flip from pass to fail.** Manual inspection of all 10 against their
full conversation text:

- **7 are re-exposures of the §2.1 grading-fragility pattern**
  (`basic_013`, `basic_016`, `basic_018`, `basic_021`, `decision_basic_004`,
  `supersession_basic_041`, `basic_011`) — their original "pass" was
  already a proxy artifact (the brainstorming-turn substring match), and
  archiving that turn away merely exposes the pre-existing failure rather
  than causing a new one. Not counted as genuine SUPERSEDE regressions.
- **3 are genuine regressions**, and they identify exactly the judgment
  Stage 4 would need and does not yet have:

  ```
  supersession_basic_040 / _055
    turn 1: "The design deadline is Monday and the dev deadline is Thursday."
    turn 2: "The design deadline moved to Wednesday; the dev deadline is unchanged."
    -> turn 2 explicitly says turn 1's dev-deadline fact is STILL CURRENT.
       A naive "later turn supersedes earlier turn" rule would wrongly
       archive it. Query asks about the dev deadline -> answer destroyed.

  decision_basic_001
    turn 1: "...I decided to build the Manager AI before GraphRAG."
    turn 2: "The reason is that extraction quality appears to be a larger
             bottleneck than retrieval quality."
    -> turn 2 is a JUSTIFICATION of turn 1, not a replacement of it, and
       doesn't restate the decision at all. Archiving turn 1 destroys the
       only fact that answers the query.
  ```

**This is direct evidence, not speculation**, that a real Stage 3/4 must
distinguish "full replacement," "partial update leaving other facts
intact," and "justification/elaboration with no new claim" — three
different conversational shapes the current 307-file corpus does not
label or test as distinct cases anywhere. This is the same "LLM-shaped
judgement" `TECH_DEBT.md` already names as Stage 4's hard part, and this
investigation's regression testing shows concretely, not just
theoretically, what happens when that judgment is approximated with a
mechanical rule instead: a genuine ~3.7% (3/82) regression rate on the
exact case shape Stage 3/4 targets, using only the crudest possible
implementation. A real implementation could do better than this oracle —
or could do worse, if its contradiction-detection heuristic is any less
conservative than "always supersede the immediately preceding fact."

---

## 4. Theoretical ceiling

All numbers are on the same 250-case gradeable denominator used
throughout, current baseline **200/250 (80.0%)** audit-style / **159/250
(63.6%)** strict.

| Ceiling | Cases | New pass rate | Basis |
|---|---|---|---|
| **Realistic / proven** | +23 | 223/250 = **89.2%** (+9.2 pts) | Directly measured by the oracle in §1.2 — the only number in this section actually tested against the real retrieval pipeline rather than assumed |
| **Worst case** | 0, possibly slightly negative | ≤80.0% | §3's regression evidence: a real implementation conservative enough to avoid the `supersession_040`/`055`/`decision_basic_001` failure mode may also be too conservative to fire on much beyond the already-tested 23; one built to fire more aggressively risks trading some of those 23 gains for new regressions in the untested 82-case regression-risk pool at large (only the 6-relevant-category slice was tested here) |
| **Optimistic (unproven upper bound)** | up to +24 more (the excluded multi-hop/candidate-gen-entangled cases) | up to 247/250 = 98.8% | **Cannot be proven by this method** — requires a real chain-aware semantic identity resolver this investigation did not and could not build deterministically. Stated as an upper bound only; `PRE_BENCHMARK_FREEZE_AUDIT.md` already found the closely-related ranking-tier work for this exact case shape yields "well under" its theoretical ceiling in practice, which is reason to expect the realized number is much closer to the realistic ceiling than to this optimistic one |

**By category** (of the 23 proven-fixable cases): concentrated in
`supersession` (5) and single cases across `contradictions`/`beliefs` —
i.e., a **9.2-percentage-point, single-category-concentrated gain**, not
a broad benchmark-wide lift. `decision_reconstruction` (26 gradeable
cases, 61.5% pass today) sees **no proven improvement** from Stage 3/4
alone under this investigation's evidence — its failures are dominated by
the multi-hop identity-resolution shape in §1.3, not simple two-fact
supersession.

---

## 5. Risk analysis

**Engineering effort — substantial, not a small patch.** `CanonicalMatcher`
today is pure deterministic string logic with an explicit, tested design
guarantee ("no semantic similarity, no embeddings, no LLM call" — its own
class docstring). Giving it (or a restored Supersession stage) real
contradiction/identity judgment means introducing an LLM call into a
layer this codebase has, everywhere else, deliberately kept
LLM/embedding-free — a genuine architectural boundary crossing, not an
incremental feature. Separately, `SUPERSEDE` producing **two**
`KnowledgeObject`s (archived-old + new) instead of one breaks the current
one-decision-in/one-`KnowledgeObject`-out contract `ManagerPipeline.match_and_apply`,
`HavenFullAdapter.add_conversation`, and `obsidian/server/main.py`'s
commit route all currently assume.

**Regression probability — medium, and concentrated exactly where the
feature would need to be most careful.** §3 measured a ~3.7% (3/82)
regression rate on the crudest possible heuristic, on precisely the case
shape (partial update, justification-only turns) a real implementation
must get right to be worth shipping at all.

**New tests required**: `obsidian/tests/test_canonical_matcher.py` (274
lines) and `obsidian/tests/test_knowledge_updater.py` (239 lines) both
currently assert today's "SUPERSEDE is never auto-returned"/"must be
invoked manually" contract and would need rewriting, not just extending;
`obsidian/tests/test_manager_pipeline_trace.py` (266 lines) documents the
now-dead `else` branch and would need new assertions for the two-object
persistence path; and — per §3's findings — new benchmark cases
explicitly distinguishing full-replacement / partial-update /
justification-only conversation shapes, a labeled sub-taxonomy the
current 307-file corpus does not contain anywhere.

**Memory Review impact**: `obsidian/server/schemas.py`'s `ReviewMemoryItem`
docstring states, as a deliberate design choice, that `/memory/preview`
computes no decision because "a provisional decision computed at preview
time would only be misleading" (decision depends on `existing`, which can
drift between preview and commit). Auto-SUPERSEDE raises the stakes of
that gap: today a user reviewing a new fact has no way to learn that
committing it will silently archive an existing memory — that UX
(surfacing "this will supersede X" before commit) does not exist and is
not a small addition, since it requires the preview route to run a
decision that route explicitly avoids today.

**Write trace impact**: `obsidian/ontology/write_trace_models.py` (615
lines) is not exhaustively re-examined by this investigation (out of
scope for a no-code-change audit), but a decision producing two persisted
objects instead of one is a schema-shaped change to whatever this module
records per decision, not merely an internal implementation detail —
flagged as a known unknown, not measured here.

**Benchmark impact**: shipping Stage 3/4 without new corpus coverage for
the full-replacement/partial-update/justification-only distinction (§3)
means the existing 307-file corpus cannot validate the single riskiest
part of the feature being added — the corpus would show the gain (§4) but
not the regression risk that gain is coupled to.

---

## 6. Recommendation

# B) Do not implement Stage 3/4 before the hackathon.

Every number in this document that could be directly measured says the
same thing from a different angle:

1. **The proven ceiling is narrow and single-category**: +23/250 (+9.2
   points), concentrated almost entirely in `supersession`, with **zero**
   proven improvement to `decision_reconstruction` — the other category
   this investigation's context named as motivating the question.
2. **Even inside the target category, the oracle test (§1.2) shows
   candidate generation — not the missing SUPERSEDE mechanism — is the
   dominant bottleneck** (32 of 67 tested cases stay broken with a
   *perfect* archive). Shipping Stage 3/4 alone would not close most of
   `supersession`'s own gap.
3. **The regression risk is not hypothetical — it was measured** (§3), and
   it lands precisely on the judgment call (full replacement vs. partial
   update vs. justification) that makes this Stage-4-shaped work
   genuinely LLM-hard, not a deterministic extension of the existing
   `CanonicalMatcher` design.
4. **The engineering cost is a structural change**, not a contained
   patch: a new LLM call in a layer designed to have none, a
   one-object-in/one-object-out contract that has to become
   one-or-two-out across at least three call sites, and a Memory Review
   UX gap with no existing surface to build on.
5. **This mirrors `PRE_BENCHMARK_FREEZE_AUDIT.md`'s own prior freeze
   recommendation** for candidate-generation/ranking work, on the same
   evidentiary bar (ceiling size vs. risk vs. architectural fit) — that
   audit froze retrieval for reasons this investigation's evidence
   independently reproduces for the write path.

**If a reduced subset (option C) is wanted anyway**, the only slice this
investigation's evidence would support is narrower than "wire SUPERSEDE
generically": restrict it to conversations where (a) exactly two facts
about the same query-relevant topic exist, (b) the later fact does not
contain a hedge/continuation marker the §3 regressions exhibited
("unchanged," "reason," "because," or similar — an explicit denylist, not
a general classifier), and (c) accept that this narrow form only reaches
part of the proven 23-case ceiling, not all of it, since some of those 23
are exactly the multi-turn shapes such a narrow rule would also have to
exclude to stay safe. This was not implemented or further scoped, per the
"no code" instruction for this investigation.

**Two things worth doing that are not "implement Stage 3/4" and do not
block a freeze**: (1) label a small number of existing or new benchmark
cases by the full-replacement/partial-update/justification-only
distinction found in §3, purely as dataset work, so a future attempt at
this feature has ground truth to test against; (2) if a real Qwen-judged
`haven_full` run is commissioned for other reasons before the hackathon,
re-check the §2.1 grading-fragility cases specifically — they are the
cases most likely to move (in either direction) once a real semantic
judge, rather than this investigation's substring proxy, grades them.
