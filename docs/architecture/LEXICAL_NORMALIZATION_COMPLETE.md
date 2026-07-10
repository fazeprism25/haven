# Lexical Normalization — Batch 5 and Completion Note

Status: Analysis + one code change. Only
`obsidian/memory_engine/keyword_candidate_retriever.py` (three new
`_VARIANT_GROUPS` entries) and its test file were modified. No benchmark
dataset, ontology file, or ranking/acceptance code was touched. All
numbers below were re-measured by running Haven's real, unmodified
pipeline (`OntologyPipeline` -> `HybridCandidateRetriever` ->
`MemoryEngine.query_with_trace`) against the current working tree, using
a throwaway, uncommitted harness (`scripts/lexical_batch5_audit.py`,
written for this investigation, deleted after use — same convention
`docs/architecture/ENTITY_CAT_INVESTIGATION.md`'s own throwaway script
used).
Version: 1.0

---

## 0. What changed

Added three variant groups to `_VARIANT_GROUPS` (Batch 5), exactly the
three pairs `ENTITY_CAT_INVESTIGATION.md`'s "Recommendation 0" identified
and this task's brief named:

```python
"name": frozenset({"name", "names", "naming", "named"}),
"decide": frozenset({"decide", "decides", "deciding", "decided"}),
"prioritize": frozenset({
    "prioritize", "prioritizes", "prioritizing", "prioritized",
    "priority", "priorities",
}),
```

Each was checked against the benchmark corpus before being added (grep
over `benchmarks/datasets/*/*.json`): `name`/`named` appears in 8 files,
`decide`-family in 54, `prioritize`-family in 3 — all single-sense,
closed-class inflections with no second meaning in this corpus (unlike
`dev`/`development` or `temp`/`temporary`, already excluded from the
table for exactly that reason). No ambiguous pair was found or added.
`decision`/`decisions` (a nominalization of `decide`) was deliberately
**not** added to the same group — it wasn't part of the audited/confirmed
pair set, and this corpus already uses "decision" as its own established
category-noun vocabulary across two whole dataset categories
(`decision_basic`, `decision_reconstruction`); conflating it with the
verb `decide` was not verified and is left for a future, separately
reviewed change if ever needed.

Test coverage: a new `TestResidualLexicalVariants` class in
`obsidian/tests/test_keyword_candidate_retriever.py`, mirroring
`TestPhase1AuditConfirmedVariants`'s pattern exactly — one parametrized
case per variant pair, plus a regression test replaying each of the five
targeted benchmark query/fact pairs verbatim. One pre-existing test
(`TestClitics::test_clitic_never_survives_as_its_own_token`) asserted
`"I've decided"` tokenizes to `["i", "decided"]`; this is now, correctly,
`["i", "decide"]`, and was updated accordingly. Full suite: 1995/1995
passing.

---

## 1. Re-measured audit: before / after

Reproduced with the same method `ENTITY_CAT_INVESTIGATION.md` and
`CANDIDATE_GENERATION_DECISION.md` used: 288 total benchmark cases, each
conversation entry written verbatim (`infer=False`), the target for a
case being whichever entry contains every string in
`expected.answer_contains`; 38 cases have no such single entry and are
excluded as ungradeable (same method, same count both audits reported).
**The "before" run below (Batch 5 variant groups monkeypatched out)
reproduces `ENTITY_CAT_INVESTIGATION.md`'s reported 177/47/26 split
exactly**, confirming this harness is measuring the same thing the prior
audit measured, not a different pipeline state.

| | Before (Batch 5 absent) | After (current code) | Δ |
|---|---|---|---|
| Gradeable cases | 250 | 250 | — |
| **PASS** | **177** | **183** | **+6** |
| **Candidate-generation failure** | **47** | **42** | **-5** |
| **Ranking/acceptance failure** | **26** | **25** | **-1** |

## 2. What actually moved, case by case

The net change matches what was expected in shape (fewer
candidate-generation failures, more passes) but **not case-for-case**
what the prior monkeypatch-based prediction claimed — rerunning the real,
full end-to-end pipeline (not an isolated single-pair monkeypatch)
surfaced one interaction the isolated test couldn't see. Reported exactly
as measured, per this task's own "verify every assumption" instruction:

**4 cases flipped straight from candidate-generation failure to full PASS**, as predicted:

| benchmark_id | Pair that fixed it |
|---|---|
| `concept_consolidation_basic_050` | name/named |
| `decision_basic_004` | prioritize/prioritized |
| `decision_reconstruction_basic_015` | decide/decided |
| `decision_reconstruction_basic_021` | decide/decided |

**1 case flipped from candidate-generation failure to ranking failure, not PASS** — a genuine, newly-observed interaction:

`decision_reconstruction_basic_004` ("Where did the user decide to go on
their fall vacation?") now correctly finds its target candidate ("Decided
on Portugal for the trip...") via the new `decide`/`decided` mapping —
the lexical fix works exactly as designed, confirmed directly:

```
rank  accepted  score   rejection_reason  fact
1     True      0.411   —                 "Trying to plan a two-week trip for the fall, deciding between Italy, ..."
2     False     0.331   score_gap_cut     "Decided on Portugal for the trip. The surf lessons in Ericeira..."
```

This conversation's first turn ("...deciding between Italy, Greece, and
Portugal") *also* shares `decide`/`fall`/`trip` tokens with the query once
`deciding` normalizes to `decide` — it was always a near-miss, but before
this change it wasn't even a keyword-path candidate, so it never competed
with the real target. Now both are candidates, the near-miss ranks
narrowly higher, and `AcceptanceStage`'s `score_gap_cut` rejects the
correct answer for being too close a runner-up. This is a
`DeterministicRanker`/`AcceptanceStage` tie-breaking gap, not a lexical
gap — the keyword match fired correctly; scoring two legitimate
candidates for the same query is out of this task's scope (ranking
stage, not candidate generation, and this task was explicitly scoped to
normalization only).

**2 cases flipped from ranking failure to PASS — a side effect, not part of the targeted set:**

`concept_consolidation_basic_012` and `concept_consolidation_basic_029`
were already candidates before this change (found via keyword or ontology
path) but rejected at ranking. Widening `_VARIANT_GROUPS` changes
`keyword_overlap_score` for every query in the corpus, not just the
five targeted ones (the same corpus-wide effect
`CANDIDATE_GENERATION_DECISION.md` predicted: "it also improves every
query in every category... since keyword overlap feeds
`DeterministicRanker`'s scoring for all 250 gradeable cases, not just the
failures analyzed"). These two crossed their acceptance threshold as a
result. Not investigated further — outside this task's scope, and a
positive-only side effect (no case here regressed from PASS).

**No case regressed.** Every one of the 250 gradeable cases that passed
before this change still passes after it — confirmed directly by the
before/after PASS-set comparison in the harness, not assumed from the
aggregate counts alone.

## 3. Updated candidate-generation failure breakdown

Using the same classification rubric `ENTITY_CAT_INVESTIGATION.md`
defined (LEXICAL / ENTITY_CAT / ONTOLOGY_COV / ROLE_PROPERTY / PARAPHRASE),
carried forward against the current 42:

| Bucket | Before (of 47) | After (of 42) |
|---|---|---|
| ENTITY_CAT (genuine) | 14 | 14 (unchanged — out of scope) |
| PARAPHRASE | 14 | 14 (unchanged — out of scope) |
| ONTOLOGY_COV | 10 | 10 (unchanged — out of scope) |
| Residual LEXICAL | 6 | **1** |
| ROLE_PROPERTY | 3 | 3 (unchanged — out of scope) |
| **Total** | **47** | **42** |

The one remaining LEXICAL entry is `supersession_basic_006`
("planned"->"plan"), already identified and deliberately excluded by
`ENTITY_CAT_INVESTIGATION.md` (§0): a monkeypatch confirmed that even
adding this pair does not produce a PASS — the candidate is found, but
`AcceptanceStage` still rejects it, the same `score_gap_cut`-shaped
ranking gap seen in §2 above, not a lexical gap. Adding a variant pair
that only relabels a candidate-generation failure as a ranking failure,
without ever producing a PASS, is not a lexical-normalization win, so —
consistent with this task's explicit scope ("do not add ontology
changes," implicitly: don't chase a fix past the layer this task
covers) — it is left out.

---

## 4. Why lexical normalization is complete

Every deterministic, unambiguous inflectional/nominalization pair
identified against this benchmark corpus — across five reviewed batches,
now including `name`, `decide`, and `prioritize` — has been added to
`_VARIANT_GROUPS`. What remains in the 42 candidate-generation failures
is no longer reachable by adding more variant pairs:

- **14 ENTITY_CAT** and **10 ONTOLOGY_COV** cases need a category
  concept or relationship the ontology doesn't have (`ConceptDetector`
  never promotes lowercase category prose to a `Concept`; no `IS_A`
  edge exists to bridge instance -> category). No token-normalization
  table closes a gap where the query and the answer describe two
  *different concepts* related by meaning, not two spellings of the
  *same* word.
- **14 PARAPHRASE** cases share no vocabulary with their answer at all
  by construction (dates, opinions, "enumerate everything" queries,
  genuine synonym pairs with no shared root) — nothing is left to
  normalize; these need semantic matching, not lexical matching.
- **3 ROLE_PROPERTY** cases need a verb -> relationship-type mapping
  (e.g. "live" -> `LOCATED_IN`), a new traversal mode seeded on
  relationship types rather than a token-equivalence table.
- **1 residual LEXICAL** case (`supersession_basic_006`) is provably
  not fixable at this layer — confirmed above, adding its pair only
  moves the failure downstream to ranking without ever producing a PASS.

Widening `_VARIANT_GROUPS` further would mean picking pairs that were
*not* validated against a real corpus gap — exactly the "benchmark-
specific mapping" and "ambiguous mapping" risk this task's brief warned
against, and the module's own design philosophy (a closed,
hand-reviewed table, never a stemmer) already rejects doing that on
spec rather than evidence.

**Recommendation: future retrieval-quality work should move to the
ontology layer** (the `IS_A` bridge scoped in
`ENTITY_CAT_INVESTIGATION.md` §4/§5, or `ConceptDetector` coverage for
lowercase category prose), not to further lexical-normalization passes.
The lexical mechanism has reached its ceiling for this corpus: it
converted its entire identified backlog (16 -> 6 -> 1 residual, non-
fixable-at-this-layer case) across three successive passes, and every
remaining failure bucket is, by the analysis above, a *different kind*
of gap that no additional variant pair can close.
