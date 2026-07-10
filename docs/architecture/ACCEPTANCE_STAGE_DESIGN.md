# Haven Acceptance Stage — Design Document

Status: Implemented as designed —
`obsidian/memory_engine/acceptance_stage.py`, wired into
`MemoryEngine.query_with_trace` between `DeterministicRanker` and
`DeterministicSlotAllocator` exactly as described in §3, with tests in
`obsidian/tests/test_acceptance_stage.py`. The rejection-reason constants
in §5 and the "shadow mode" rollout in §6.3 describe the pre-implementation
plan; the stage now runs in enforce mode, not shadow mode. Left otherwise
unchanged as the original design record.
Version: 0.1

---

# 1. Problem

Retrieval (`HybridCandidateRetriever`) and ranking (`DeterministicRanker`)
are now measured as "reasonably good." The remaining problem is downstream
of both: **too many weak candidates are accepted into the final context.**

Today, "acceptance" is not a real stage — it is two cheap side effects:

```
ranked_accepted = [rc for rc in ranked_all if rc.final_score >= config.minimum_candidate_score]
allocated       = slot_allocator.allocate(ranked_accepted, config)   # top config.max_results
```

`minimum_candidate_score` defaults to `0.1`. `max_results` defaults to `50`.
Neither is a quality control — they are a floor-cleaning pass and a budget
cap, and the evidence below shows neither one actually fires in practice.
This document proposes a real acceptance stage to sit between
`DeterministicRanker` and `DeterministicSlotAllocator` that decides, per
query, which prefix of the ranked list is trustworthy enough to hand to the
LLM — including the option to accept nothing at all.

---

# 2. Evidence: measured retrieval traces

Ten representative queries were run against the seeded demo vault
(`obsidian/memory_engine/engine.py:MemoryEngine.query_with_trace`, real
`HybridCandidateRetriever` + `DeterministicRanker`, no mocking) to look at
actual `final_score` distributions rather than assumed ones.

## 2.1 Acceptance is currently a no-op

| Query | merged | accepted | rejected |
|---|---|---|---|
| database for Project Atlas | 9 | 9 | 0 |
| CI/CD tool | 3 | 3 | 0 |
| beliefs about Rust | 5 | 5 | 0 |
| editor preferences | 1 | 1 | 0 |
| what is Project Nova | 7 | 7 | 0 |
| secrets management system | 4 | 4 | 0 |
| Haven roadmap | 11 | 11 | 0 |
| weather forecast (off-topic) | 0 | 0 | 0 |
| quantum computing (off-topic) | 0 | 0 | 0 |
| second brain / trustworthy retrieval | 8 | 8 | 0 |

Every single candidate that clears keyword/ontology retrieval is accepted,
every time, in every trace. The two off-topic queries return zero
candidates — but that's the retriever finding no token overlap, not the
acceptance stage abstaining. There is currently no code path where
retrieval finds *something* but acceptance correctly says "not good
enough." That gap is exactly what this design fills.

## 2.2 Score distributions cluster in narrow, largely flat bands

Full descending `final_score` lists, three representative queries:

```
"database for Project Atlas": [0.376, 0.376, 0.376, 0.326, 0.323, 0.323, 0.304, 0.304, 0.304]
"what is Project Nova":       [0.410, 0.410, 0.410, 0.410, 0.331, 0.331, 0.331]
"second brain / trustworthy": [0.410, 0.363, 0.292, 0.292, 0.292, 0.292, 0.292, 0.292]
```

Consecutive-gap analysis across all ten queries separates cleanly into two
populations:

- **Noise-level gaps** (ties / near-ties, same relevance tier):
  `0.0, 0.0, 0.0, 0.003, 0.008, 0.014, 0.019, 0.029`
- **Real gaps** (a genuine drop to a less relevant tier):
  `0.034, 0.036, 0.039, 0.047, 0.05, 0.064, 0.071, 0.073, 0.079, 0.143, 0.25, 0.355`

There's a visible break around **~0.03–0.04**: below that, two candidates
are statistically indistinguishable given how this ranker's weighted
average behaves; at or above it, something in the score breakdown
materially changed (a keyword-overlap term dropped out, an ontology path
stopped contributing, etc). This is the empirical basis for a gap-detection
threshold (§4.2).

## 2.3 The failure mode the user is describing, concretely

`"What secrets management system do I use?"`:

```
[ACC] rank=1 score=0.331  "I prefer dark mode in every editor and terminal I use."
[ACC] rank=2 score=0.317  "I believe a second brain is only useful if retrieval is fast..."
[ACC] rank=3 score=0.317  "I decided to store secrets and configuration in HashiCorp Vault..."
[ACC] rank=4 score=0.310  "I decided to use GitHub Actions for CI/CD instead of Jenkins..."
```

The one fact that actually answers the question is rank 3, separated from
an irrelevant top pick by a gap of **0.014** — below the noise floor
identified in §2.2. All four are single-evidence-path keyword matches
(`matched_by_ontology=False` for all four), so there is no independent
signal available at acceptance time to prefer rank 3 over rank 1. **This
bounds what an acceptance stage can do:** it cannot fix a ranking mistake
it has no evidence to detect. What it *can* do is refuse to silently narrow
a genuinely ambiguous, tightly-clustered top group down to a single
(possibly wrong) pick — see §4.5.

## 2.4 Why the demo vault understates the benefit

The demo vault has ~30 memories; the largest candidate pool observed above
is 11. `max_results=50` never binds and never will at this vault size. The
acceptance stage's actual payoff shows up as personal vaults grow into the
hundreds of memories, where the same flat-plateau pattern seen here (many
candidates within 0.02–0.05 of each other) would otherwise dump dozens of
tangentially-related facts into context instead of 3–9. Benchmark numbers
on the current small fixtures will therefore *understate* the real-world
effect (§6).

---

# 3. Where this fits in the pipeline

```
Candidate[]
    │
    ▼
DeterministicRanker.score_all()      (unchanged — scores every candidate, filters nothing)
    │
    ▼
RankedCandidate[]  (all of them, sorted)
    │
    ▼
AcceptanceStage                      ← NEW — this document
    │
    ▼
RankedCandidate[]  (accepted subset, possibly empty)
    │
    ▼
DeterministicSlotAllocator           (unchanged — becomes a pure safety cap;
    │                                  see §4.6, rarely binds after AcceptanceStage runs)
    ▼
ContextBuilder
```

`DeterministicRanker.rank()` currently does the `minimum_candidate_score`
filtering itself (`obsidian/memory_engine/deterministic_ranker.py:176-209`).
That responsibility moves to `AcceptanceStage`; the ranker keeps
`score_all()` (unfiltered) as its only output method, since
`AcceptanceStage` needs the full unfiltered list to do gap detection.
`MemoryEngine.query_with_trace` already calls `score_all()` directly
(`engine.py:402`) and does its own filtering inline
(`engine.py:403-407`) — that inline filter is exactly the code
`AcceptanceStage` replaces, and `CandidateTrace.rejection_reason` is
already designed to carry a reason string per candidate, so trace/inspector
support is additive, not a redesign (§5).

---

# 4. Algorithm

Five stages, applied in order to the full, score-sorted `RankedCandidate[]`
list. Every stage can only shrink the surviving set. Each rejected
candidate is tagged with *which* stage rejected it (for the trace).

```
def accept(ranked_all: list[RankedCandidate], config: AcceptanceConfig) -> list[RankedCandidate]:
    if not ranked_all:
        return []

    survivors = sorted(ranked_all)                       # descending final_score, existing tie-break
    top_score = survivors[0].final_score

    # Stage 1 — absolute floor (today's minimum_candidate_score, unchanged semantics)
    survivors = [c for c in survivors if c.final_score >= config.minimum_candidate_score]
    if not survivors:
        return []

    # Stage 2 — abstention: is even the best candidate good enough to trust at all?
    if survivors[0].final_score < config.abstention_score:
        return []

    # Stage 3 — score-gap cut: find the largest "real" drop within a lookahead window
    cut = _find_gap_cut(survivors, min_gap=config.min_gap, window=config.gap_window)
    survivors = survivors[:cut]

    # Stage 4 — relative quality floor: must stay within a fraction of the top score
    floor = top_score * config.relative_floor_ratio
    survivors = [c for c in survivors if c.final_score >= floor]

    # Stage 5 — hard cap, independent of the context-budget max_results
    return survivors[:config.acceptance_max_k]
```

```
def _find_gap_cut(survivors: list[RankedCandidate], min_gap: float, window: int) -> int:
    """Return the index to slice at. No sufficiently large gap => no cut."""
    limit = min(window, len(survivors) - 1)
    best_gap, best_index = 0.0, len(survivors)
    for i in range(limit):
        gap = survivors[i].final_score - survivors[i + 1].final_score
        if gap >= min_gap and gap > best_gap:
            best_gap, best_index = gap, i + 1
    return best_index
```

## 4.1 Stage 1 — absolute floor

Unchanged from today (`minimum_candidate_score`, default `0.1`). Kept as
stage 1 because it's cheap, already-tested, and catches degenerate
candidates (e.g. every weight zeroed, per the ranker's own documented
degenerate case) before any of the more elaborate logic runs.

## 4.2 Stage 2 — abstention

New. If the *best* candidate found doesn't clear `abstention_score`, return
`[]` rather than force-including a weak best-of-a-bad-lot. This is the
literal codification of one of the demo vault's own seeded beliefs:
*"a system that returns stale or wrong facts is worse than no memory
system at all."*

No trace in §2 exhibits a genuinely low-but-nonzero top score (the lowest
observed top score is 0.331, for the one query that already has a
real-but-buried answer). **This is a real gap in the current evidence** —
recommend collecting traces for queries with partial/coincidental keyword
overlap but no true answer (e.g. a query using a word that appears in one
unrelated memory) before finalizing this constant. Starting point:
`abstention_score = 0.25`, chosen conservatively low specifically so it
doesn't fire on the "secrets management" case (top score 0.331), which
does have a right answer.

## 4.3 Stage 3 — score-gap cut

New. Directly grounded in §2.2's noise/real-gap split. `min_gap = 0.04` sits
just above the highest observed noise-level gap (0.029) and just below the
lowest observed real gap (0.034). `window` bounds how far into the ranked
list gap detection is allowed to look — without it, a large gap deep in a
long tail (position 40 of 50) could accept everything ahead of it,
defeating the purpose. Starting point: `gap_window = 10`, i.e. only look
for a cliff among the top 10 candidates; beyond that, later stages (4, 5)
do the pruning instead.

If no gap `>= min_gap` exists in the window (the common case in these
traces — 6 of 8 non-empty queries have no gap over 0.04 within their first
few positions), this stage is a no-op and stage 4 does the work.

## 4.4 Stage 4 — relative quality floor

New. `relative_floor_ratio` requires every surviving candidate to be within
some fraction of the *best* candidate's score — a percentile-style cutoff
anchored to this query's actual evidence quality rather than a fixed global
number. This is what prunes the flat-plateau cases stage 3 can't touch: for
`"Haven roadmap"` (top score 0.662), a ratio of `0.55` sets the floor at
`0.364`, cutting the bottom two candidates (0.337, 0.337) while keeping the
top nine. For `"database for Project Atlas"` (top score 0.376, tightly
clustered down to 0.304), the same ratio sets the floor at `0.207` — below
everything, so all 9 survive, which is correct: that trace's spread (0.304
to 0.376, i.e. within 19% of top) reflects genuinely comparable evidence
quality, not noise. Starting point: `relative_floor_ratio = 0.55`.

## 4.5 Stage 5 — hard cap

New. `acceptance_max_k` (starting point: `8`) is a safety net independent
of `max_results` (which stays at 50 as an outer context-budget cap — see
§4.6). Necessary because stages 3–4 can both no-op simultaneously on a
long, gently-sloping plateau (no single gap `>= min_gap`, and every score
within `relative_floor_ratio` of the top) — a pattern not yet observed at
demo-vault scale but expected as vaults grow (§2.4). Deliberately simple:
take the top K of whatever survived stages 1–4, no new logic.

## 4.6 What does NOT change

`DeterministicSlotAllocator` and `max_results` (default 50) stay exactly
as they are, downstream of `AcceptanceStage`. Two independent caps —
"is this candidate good enough" (acceptance) and "do we have room for it"
(slot allocation) — solve two different problems and the task explicitly
scopes `max_results` as the slot allocator's concern
(`retrieval_config.py:18-19`, `deterministic_ranker.py:120-126`). After
`AcceptanceStage`, `max_results` should essentially never bind in practice
(it isn't binding today either — see §2.1) — it remains purely as an
absolute worst-case ceiling.

## 4.7 What this design explicitly does not attempt

- **It cannot correct an ordering mistake it has no evidence for.** §2.3 is
  the concrete example: two single-path keyword matches, one right and one
  wrong, separated by a noise-level gap. The correct behavior here is to
  keep both (no gap, both within the relative floor) and let the LLM
  disambiguate from the fuller context — not to guess.
- **It is not confidence estimation via score-breakdown introspection.** An
  earlier version of this design considered rejecting single-evidence-path
  candidates (`matched_by_keyword` XOR `matched_by_ontology`) more
  aggressively than multi-path ones. Discarded: in every trace above, the
  correct answer is *itself* frequently a single-path keyword match (the
  HashiCorp Vault fact, the dark-mode fact, the CI/CD fact) — this signal
  doesn't separate right from wrong the way gap/relative-floor evidence
  does, and adding it would mostly add tunable surface area without a
  measured justification. Not included in v1.

---

# 5. Trace / Retrieval Inspector integration

`CandidateTrace.rejection_reason` (`retrieval_models.py:358-364`) already
carries exactly two string constants today. This design adds one per new
stage so the inspector can show *why*, not just *that*, a candidate was
dropped:

```
REJECTION_BELOW_MINIMUM_SCORE       (existing, stage 1)
REJECTION_BELOW_ABSTENTION_FLOOR    (new, stage 2 — whole query abstained)
REJECTION_SCORE_GAP_CUT             (new, stage 3)
REJECTION_BELOW_RELATIVE_FLOOR      (new, stage 4)
REJECTION_ACCEPTANCE_CAP_EXCEEDED   (new, stage 5)
REJECTION_SLOT_BUDGET_EXCEEDED      (existing, unchanged, now rarely hit)
```

No schema change beyond adding string constants and setting the right one
per candidate — `CandidateTrace`'s shape, `RetrievalPipelineStats`, and
`RetrievalTrace` are all untouched.

---

# 6. Estimates

## 6.1 Expected benchmark improvement

Low-to-moderate on the *current* fixtures, for a structural reason, not a
design weakness: the benchmark's LLM judge (`benchmarks/judges/llm_judge.py`)
already tolerates noisy context reasonably well ("Judge SEMANTIC MEANING,
not exact wording... Do NOT fail simply because an old memory appears in
the answer"), and the demo/benchmark vaults are small enough that
`max_results` never binds and candidate pools top out around 10 (§2.4). The
`RETRIEVAL` and `INCOMPLETE` failure_type categories the judge already
reports are the ones to watch — expect a small reduction in `RETRIEVAL`
failures caused by the answering LLM latching onto a plausible-but-wrong
high-ranked fact from a long, low-signal tail, and a hopefully-zero
increase in `INCOMPLETE` failures from over-aggressive abstention (this is
the metric that would reveal `abstention_score` or `relative_floor_ratio`
set too high).

The stronger claim is about vault scale, not benchmark score: this stage's
real job is capping context growth as a personal vault grows into the
hundreds/thousands of memories, a regime today's ~30-memory demo vault and
~10-candidate traces can't exercise or measure yet.

## 6.2 Implementation complexity

Small. ~80–120 lines of pure, allocation-free Python (two functions, one
new frozen-dataclass config extension), no new dependencies, no change to
`Candidate`/`RankedCandidate`/`KnowledgeObject`. Directly analogous in
shape and size to the existing `DeterministicSlotAllocator`
(`deterministic_slot_allocator.py`, 116 lines total). Five new unit-testable
stages, each independently testable against the exact score lists captured
in §2 as golden fixtures (this doc's traces should become the first test
cases — they're already real, already deterministic, and already show both
the "no cut" and "should cut" cases).

## 6.3 Regression risk

Main risk is **over-abstention**: too-aggressive `abstention_score` or
`relative_floor_ratio` silently returns `""` context for queries that used
to get a (mediocre but present) answer, which the benchmark would surface
as new `INCOMPLETE`/`RETRIEVAL` failures — worth calling out because that
failure mode is *silent* in production (no exception, no error, just an
empty context) in a way a bug in ranking math is not.

Mitigation: ship in **shadow mode** first — compute and log what
`AcceptanceStage` *would* reject (via the new `CandidateTrace` rejection
reasons in §5) without actually changing what `ContextBuilder` receives,
run it across the full benchmark suite and a sample of real Retrieval
Inspector traces, and only flip it to enforce once the shadow logs confirm
`abstention_score`/`relative_floor_ratio`/`gap_window`/`acceptance_max_k`
don't cut anything that the LLM judge would have scored as necessary. This
also directly closes the gap noted in §4.2 (no observed low-top-score
trace yet) before that constant goes live.

Secondary risk: the five constants are new tunable surface area on
`RetrievalConfig`, all defaulted from the trace evidence in §2 rather than
guessed, but a vault with very different score dynamics (e.g. one that
relies much more heavily on `weight_recency` or `weight_confirmation_count`)
could see different noise/signal gap boundaries than the ones measured
here. Low blast radius, though — every constant is independently
overridable per `RetrievalConfig` instance, consistent with how every other
tunable in this file already works.

---

# 7. Open questions for validation before implementation

1. Collect traces for queries with a real answer buried under a **low**
   top score (not yet observed — see §4.2) to calibrate `abstention_score`.
2. Confirm the 0.03–0.04 noise/signal gap boundary (§2.2) holds once the
   ranker's weight configuration is tuned further — this design assumes
   today's `RetrievalConfig()` defaults.
3. Decide whether `AcceptanceStage` should be a new class
   (`obsidian/memory_engine/acceptance_stage.py`, mirroring the existing
   one-file-per-stage layout) or folded into `DeterministicRanker` as a
   second method — recommend a new file, consistent with
   `deterministic_ranker.py`'s own explicit "no allocation" /
   "out of scope" boundary and the existing stage-per-module pattern.

No implementation in this pass, per the task — this document only.
