# Haven Benchmark Framework — Independent Audit

Scope: the benchmark harness itself (runner, adapters, judge, dataset
integrity), not Haven's retrieval quality. Every claim below was verified
directly against the current working tree (2026-07-08) — file paths and
line numbers are given so each can be re-checked. Nothing was modified.

## Bottom line

**No — if Haven were submitted today on the numbers this framework can
currently produce, the results would not be scientifically defensible.**

Two independent problems each individually invalidate a head-to-head
Haven-vs-mem0 pass-rate claim:

1. **The judge does not see comparable input.** Haven's "answer" is not
   retrieved memory text — it is a pre-formatted context block that also
   contains explicit metadata (timestamps, confidence, importance,
   confirmation count, and for decisions: status/supersedes/superseded_by).
   mem0 and every baseline hand the judge raw prose with none of this.
   This is a structural advantage on exactly the categories the benchmark
   is designed to test (supersession, temporal, contradictions, decisions),
   independent of retrieval quality.
2. **The only committed result files are the framework's own
   self-declared "unverifiable snapshots,"** and the dataset the README
   describes is not the dataset that would actually run. A run today
   executes 288 cases across 14 directories; the README describes 223
   across 12. Three "populated" categories the README lists
   (`people`, `projects`, `recurring`) currently contribute **zero** cases
   — every file in them is a committed 0-byte file that the loader silently
   skips.

Both are fixable without touching Haven's algorithms — see the effort
estimates below. Section "If this is fixed" at the end lists what should
still be disclosed in the README even after both are resolved.

---

## Method

Read directly, not from memory of prior sessions:

- `benchmarks/README.md`, `benchmarks/RUNNER_SPEC.md`
- `benchmarks/runners/run_benchmarks.py`, `run_distractor.py`
- `benchmarks/adapters/{base,haven_adapter,haven_full_adapter,baselines,ablations}.py`
- `benchmarks/judges/llm_judge.py`
- `benchmarks/analysis/classify_failure.py`
- `obsidian/memory_engine/{engine,context_builder,working_context_builder,deterministic_ranker,deterministic_slot_allocator,hybrid_candidate_retriever,keyword_candidate_retriever,acceptance_stage}.py`
- `obsidian/ontology/retrieval_config.py`
- `mem0/memory/main.py` (`add`/`_add_to_vector_store`/`search`)
- All 322 files under `benchmarks/datasets/`, parsed programmatically (not
  sampled) to check category-field consistency, empty/corrupt files,
  duplicate conversations, duplicate `benchmark_id`s, and corpus-size
  distribution per category.
- `benchmarks/results/*.json` (the two committed result files) and their
  actual schema.

---

## Answers to the ten questions

**1. Does every adapter receive exactly the same information and opportunities?**
No. Write-path parity is good (see §A). Read-path parity is broken: Haven's
"answer" carries structured metadata no other adapter's answer carries
(§Critical-1).

**2. Are any adapters unintentionally advantaged or disadvantaged?**
Yes — Haven is advantaged by the metadata leak (§Critical-1). `mem0`'s own
`search()` defaults (`top_k=20`, `threshold=0.1`, §High-3) make it behave
close to a low-precision "almost return-all" on the tiny corpora this
dataset uses, which is a real, disclosed characteristic (README already
flags the ~2-memory-corpus problem) rather than a hidden bug — but it means
`mem0`'s pass/fail on `must_not_contain` cases is largely a bet on the
judge's tolerance, not on `mem0`'s own filtering.

**3. Is the benchmark methodology internally consistent?**
Mostly, for write-path and runner mechanics (praiseworthy: `BaseAdapter`,
the ablation design, the distractor sweep, the legacy-schema self-flagging
in `RUNNER_SPEC.md` are all methodologically sound). It is **not**
consistent between what the README claims about the dataset and what
`discover_dataset_dirs()` actually loads (§Critical-2).

**4. Hidden assumptions that favor one retrieval strategy?**
Yes, two: (a) the metadata leak (§Critical-1) structurally favors whichever
adapter happens to format retrieved facts with visible timestamps — which
today is only Haven; (b) the judge's system prompt (§High-1) is written to
be lenient about old memories co-occurring with new ones, which quietly
assumes retrieval is "dump plausible candidates, let the judge sort out
currency" — a posture that happens to match Haven's own multi-candidate
context blocks and undermines the categories' ability to isolate retrieval
quality from judge reasoning quality.

**5. Categories that overlap too much?**
`concept_consolidation` (dedup/confirmation/paraphrase, 62 cases) and
`refinements` (incremental detail added to an existing fact, 30 cases) test
closely related phenomena — "does a repeated/updated mention of the same
fact get merged, not duplicated." `decision_reconstruction` (26 cases,
undocumented) and `decisions` (25 cases) both ask "what did the user decide"
differing mainly in distractor density. None of this is necessarily wrong
(escalating difficulty tiers are reasonable), but nobody has stated in
writing whether the overlap is deliberate, and two of the four directories
involved aren't even mentioned in the README (§Critical-2).

**6. Categories too easy to meaningfully separate systems?**
`goals`, `identity` (10 cases each, single-turn corpora, avg conversation
length 1.0) and most of `preferences` are single-fact insert-then-query
cases — every adapter, including `return_all`, should trivially pass these;
they establish a ceiling, not a discriminator. More importantly, per §4,
`contradictions`/`supersession` (avg corpus size 2.0–2.4 turns) may be
"easy" for a different, more insidious reason: the judge's own leniency
rules mean a naive adapter that returns *both* the old and new statement
can still pass, because the judge is told not to fail an answer merely for
containing an old memory. This has not been tested — no ablation isolates
"judge resolved currency from raw text" from "retriever excluded the old
fact." The `recency` baseline the README ships is the right instrument for
this but must actually be run and diffed before any claim is trusted (no
current, non-legacy result file does this — §Critical-2/§High-2).

**7. Categories unrealistically difficult or artificial?**
`decision_reconstruction` is the one genuinely realistic, high-distractor
category in the suite (11–28 turns, mixed with unrelated daily-life
sentences) — and it is completely undocumented (§Critical-2). Nothing else
in the populated categories is unrealistically hard; if anything most
categories are unrealistically *easy* (tiny corpora, §6), which the README
already partly acknowledges via the distractor-sweep tool.

**8. Does the judge fairly evaluate every adapter?**
No — see §Critical-1. The judge is given Haven's answer in a different,
more information-rich shape than every other adapter's answer, and is
never told this is happening.

**9. Dataset problems (duplicates, leakage, wording bias, impossible expectations)?**
- 19 committed 0-byte JSON files across `decisions`, `people`, `preferences`,
  `projects`, `recurring`, `temporal` — three whole categories (`people`,
  `projects`, `recurring`) are 100% empty despite the README listing them
  as populated (§Critical-2).
- Two dataset directories (`decision_reconstruction`, 26 cases;
  `refinements`, 30 cases — 56 cases, ~19% of what a run today actually
  executes) are auto-discovered and scored but never mentioned in
  `README.md` or `RUNNER_SPEC.md`.
- `README.md`'s own stated case count for `concept_consolidation` (34) does
  not match the actual file count (62).
- No duplicate `benchmark_id`s and no duplicate conversation content were
  found (checked programmatically across all 322 files) — the corpus is
  otherwise clean on that axis.
- 65 files carry a stale `category` value that doesn't match their
  directory (`"Beliefs"`, `"belief_evolution"`, `"decision_consistency"`,
  `"Decisions"`, `"Temporal"`, etc.) — cosmetically patched over by a
  hand-maintained alias table in `classify_failure.py` (§Low-2), which is
  already missing entries for the two undocumented categories.
- No impossible expectations found in the sampled cases; `expected` fields
  are consistently derivable from their own conversation.

**10. Infrastructure issues that could invalidate conclusions?**
Yes — the only committed result files (`results/results.json`,
`results/results_haven.json`) are in the legacy flat-list schema the
codebase's own `RUNNER_SPEC.md` (line 93-94) explicitly says must be
"treated as unverifiable snapshots rather than current numbers." Confirmed
by inspection: their `category` values (`"decision_consistency"`) are the
pre-rename category names, meaning they predate the current dataset
layout entirely. There is, right now, no valid current benchmark
comparison in the repository at all (§Critical-2/§High-2).

---

## Critical findings

### Critical-1 — Judge receives structurally different "answers" per adapter — RESOLVED

**Status (update, post-audit):** Fixed. `HavenAdapter.search()`
(`benchmarks/adapters/haven_adapter.py`) no longer routes through
`MemoryEngine.query()`'s prompt-formatted context string. It now calls
`query_with_trace()` directly and returns one raw, unannotated
`{"id": ..., "memory": candidate.canonical_fact}` entry per accepted
candidate — no `type`/`confidence`/`valid_from` metadata, matching the
plain-text shape mem0/baselines already returned. The committed
`benchmarks/results/results_haven_retrieval.json` and
`results_haven_full.json` (both stamped `git_commit: 042e16e6`) confirm
clean output; only the superseded `results_haven.json` still shows the old
metadata-laden format. The rest of this finding is kept below as the
historical record of what was wrong and why, per this doc's own
introduction.

**Where (as originally found):** `obsidian/memory_engine/context_builder.py:159-214`
(`ContextBuilder._format_candidate` / `_format_decision_fields`),
`benchmarks/adapters/haven_adapter.py:237-274` (`HavenAdapter.search`),
`benchmarks/runners/run_benchmarks.py:248-251` (`" ".join(mem["memory"]...)`),
contrasted with `benchmarks/adapters/baselines.py:95-98` (`_as_results`) and
`mem0/memory/main.py:783-796` (`_add_to_vector_store`, `infer=False` path).

**What's happening:** `MemoryEngine.query()` is designed to build a
prompt-ready context string for a *downstream* LLM — by design it renders,
per candidate: `type`, `confidence: 0.XX`, `importance: 0.XX`,
`confirmations: N`, `valid_from: <ISO timestamp>`, `valid_until: <ISO
timestamp | "none">`. `HavenAdapter.search` returns this whole string as
the sole `results[0]["memory"]` entry (by design, per its own docstring).
`run_benchmark` then joins every result's `"memory"` field and hands the
joined string to the LLM judge **as if it were the final answer** — there
is no downstream generation step in this benchmark for *any* adapter.
mem0 (`infer=False`) and every baseline instead return raw, unannotated
message text — no timestamps, no scores, no type labels.

Verified directly from a real run: `benchmarks/results/results_haven.json`
(`decision_basic_001`) shows the judge received:
```
[1] The reason is that extraction quality appears to be a larger bottleneck...
    type: fact | confidence: 0.50 | importance: 0.50 | confirmations: 0
    valid_from: 2026-07-02T20:38:06.261444 | valid_until: none

[2] For my personal AI project I decided to build the Manager AI...
    type: fact | confidence: 0.50 | importance: 0.50 | confirmations: 0
    valid_from: 2026-07-02T20:38:06.252339 | valid_until: none
```
while `benchmarks/results/results.json`'s mem0 run on the same case shows
plain, repeated prose with no annotations at all.

**Why it matters:** the categories this benchmark cares most about
(supersession, temporal, contradictions, decisions) are precisely the ones
where an explicit, parseable `valid_from` timestamp (or, for decisions
written through the full pipeline, an explicit `status`/`supersedes` field)
hands the judge the exact signal it needs to determine "which one is
current" — a signal only Haven's answer carries. This is not a claim that
Haven's *retrieval* is unfairly good; it is that the *evaluation channel*
is unfairly informative for Haven regardless of what its retrieval actually
did. A system that retrieved the wrong two facts but rendered them with
correct timestamps would still look better to the judge than a system that
retrieved the right fact with no timestamp at all.

**Impact:** High — likely inflates Haven's pass rate specifically on the
categories used to argue Haven's core value proposition (ontology-aware
currency resolution). Cannot be sized without re-running the benchmark with
the leak removed.

**Effort to fix:** Low. Two independent options, either sufficient:
(a) strip `HavenAdapter.search`'s returned string down to `canonical_fact`
text only (or synthesize one `"memory"` entry per candidate instead of one
formatted block for the whole context) before benchmark comparison; or
(b) give mem0/baselines equivalent metadata (creation timestamp is already
in mem0's vector-store payload, just not surfaced in the `add`/`search`
response the runner reads) so both sides expose the same fields. Either is
a benchmarks-only adapter change, not a Haven production change.

**Fix before final Qwen benchmark?** Yes — this is the single highest-
leverage fix available; it directly undermines the categories the write-up
will lean on hardest.

---

### Critical-2 — The dataset the README describes is not the dataset that runs

**Where:** `benchmarks/README.md:79-98`, `benchmarks/runners/run_benchmarks.py:116-131`
(`discover_dataset_dirs`, no allow-list — every subdirectory auto-loads),
and the dataset tree itself.

**What's verified (programmatic scan of all 322 files under
`benchmarks/datasets/`):**

| Category | Files | Empty (0 bytes, committed at HEAD) | Actually runnable today |
|---|---|---|---|
| people | 3 | 3 | **0** |
| projects | 4 | 4 | **0** |
| recurring | 3 | 3 | **0** |
| decisions | 28 | 3 | 25 |
| temporal | 28 | 3 | 25 |
| preferences | 13 | 3 | 10 |
| concept_consolidation | 62 | 0 | 62 (README claims 34) |
| decision_reconstruction | 26 | 0 | 26 (**not in README at all**) |
| refinements | 30 | 0 | 30 (**not in README at all**) |
| beliefs / contradictions / goals / identity / supersession | 25/10/10/10/55 | 0 | unchanged |

`git show HEAD:benchmarks/datasets/people/basic_002.json` returns 0 bytes —
these are not working-tree artifacts, they are committed empty files.
`benchmarks/runners/run_benchmarks.py`'s own `load_benchmarks()` silently
skips them with a printed warning and records them in `metadata.skipped_files`
— nothing crashes, which is exactly why this has gone unnoticed.

**Actual total a run today would execute: 288 cases across 14 directories.**
The README says **223 cases across 12 categories** and explicitly lists
`people`, `projects`, and `recurring` as populated (contrasting them with
5 *genuinely* empty-folder categories it does call out:
`active_context`, `insights`, `memory_recall`, `mistake_prevention`,
`open_problems`). `decision_reconstruction` and `refinements` — 56 cases,
about a fifth of a real run — are invisible in both `README.md` and
`RUNNER_SPEC.md`.

**Why it matters:** anyone reading the README to understand what "Haven
passed 91% of the benchmark suite" means would materially misjudge both
the size and composition of that claim. A category no one has publicly
reasoned about the design of (`decision_reconstruction`, `refinements`)
silently contributes to an aggregate pass rate. And the fact that three
"populated" categories are actually contributing zero cases means the
*already-narrow* per-category sample sizes (10–55 cases) are, for those
three, zero — worse than the README's own stated numbers, not just
differently distributed.

**Impact:** High/Critical — this alone means no total-pass-rate number
currently quoted (or quotable from today's tree) matches its own
documentation, and the two whole-category dropouts remove coverage the
write-up would claim to have.

**Effort to fix:** Low for the accounting (regenerate the empty files or
correct the README's category list and counts — this is a documentation
and/or dataset-restoration fix, not a code fix); the task instructions here
forbid touching the dataset, so this audit stops at flagging it, but it is
mechanically simple to remedy once someone with write authorization looks
at why exactly these 19 files are 0 bytes (a botched save, a bad merge, or
a generation script that crashed partway — worth checking git blame/log on
those specific paths before deciding whether to restore or regenerate).

**Fix before final Qwen benchmark?** Yes, both halves: (a) restore or
formally retire the 19 empty files and correct the three README
category-count claims; (b) document `decision_reconstruction` and
`refinements` in the README (or explicitly exclude them from the
Qwen-benchmark run if they're not ready to be reported on).

---

## High-severity findings

### High-1 — Judge leniency rules may make the "hard" categories judge-driven, not retriever-driven
**Where:** `benchmarks/judges/llm_judge.py:37-45` (`SYSTEM_PROMPT` rules 4–5:
"Historical memories are acceptable if the answer clearly identifies the
CURRENT belief... Do NOT fail simply because an old memory appears").
Combined with `contradictions`/`supersession` corpora averaging only
2.0–2.4 turns (verified via per-category conversation-length scan), it is
plausible that `return_all`/`mem0` pass many contradiction/supersession
cases not because they excluded the stale fact, but because the judge
itself reasons out "actually" / "replaced" framing from raw co-present
text. The harness already ships the right instrument to check this (the
`recency` baseline, and the `haven_no_recency` ablation), but **no current,
non-legacy result file runs them** — see Critical-2. Until `recency` and
`return_all` are actually run against the *current* 288-case dataset and
diffed against `haven`, any claim that these categories "measure the
ontology" is asserted, not demonstrated.
**Impact:** High if true — would mean part of Haven's category-level
advantage is an artifact of judge tolerance interacting with answer length/
shape rather than retrieval correctness. **Effort:** Low — the tooling
exists; this is a "run it and look" task, not a build task.
**Fix before final Qwen benchmark?** Yes — run `recency`, `return_all`, and
`haven_no_recency` against the full current dataset and include the diff
in the write-up; if `haven` doesn't clearly separate from `recency` on
these categories, say so rather than reporting the raw pass rate alone.

### High-2 — No valid current comparison exists in the repo
**Where:** `benchmarks/results/results.json`, `results_haven.json`;
confirmed flat-list (no `metadata` key), and their `category` values
(`"decision_consistency"`) match pre-rename category names, i.e. they
predate the current 288-case dataset entirely. `RUNNER_SPEC.md:89-94`
already self-flags this schema as unverifiable.
**Impact:** High — there is currently no committed evidence for *any*
Haven-vs-mem0 pass-rate claim against the present codebase/dataset state.
**Effort:** Low — re-run `run_benchmarks.py` for `mem0`, `haven`, and the
baselines/ablations, which regenerates the current, metadata-carrying
schema automatically.
**Fix before final Qwen benchmark?** Yes, trivially — this has to happen
anyway to produce the Qwen numbers themselves.

### High-3 — `EmbeddingAdapter` baseline is not actually comparable to mem0's own retrieval the way its docstring claims
**Where:** `benchmarks/adapters/baselines.py:37` (`DEFAULT_TOP_K = 5`) and
its class docstring ("directly comparable to mem0's own embedding
retrieval"), vs. `mem0/memory/main.py:1247,1281` (`top_k: int = 20`,
`threshold: float = 0.1`). The baseline returns a fixed top-5 with no
similarity floor; mem0 returns up to top-20 above a 0.1 cosine floor. On
the dataset's 1–7-turn corpora this difference rarely binds, but the
docstring's comparability claim is stronger than the code actually
guarantees.
**Impact:** Medium-High for anyone using the `embedding` baseline number as
a stand-in for "what mem0's own retrieval alone would do."
**Effort:** Low — either loosen the docstring's claim or parametrize `top_k`
to match mem0's default.
**Fix before final Qwen benchmark?** Optional — doesn't affect the Haven-vs-
mem0 numbers directly, but should be corrected before citing the embedding
baseline as a proxy for mem0's retrieval mechanism specifically.

---

## Medium / Low findings (not blocking, should still be tracked)

- **Medium — RESOLVED** — `classify_failure.py`'s `NO_RETRIEVAL` vs
  `INCORRECT_ANSWER` heuristic (`benchmarks/analysis/classify_failure.py:41-56`)
  checks `len(retrieved_memories) == 0`. This used to systematically
  under-report Haven's retrieved count because `HavenAdapter.search`
  collapsed its whole context into one list entry. Fixed together with
  Critical-1 above: `search()` now returns one result entry per accepted
  candidate, so this heuristic reflects the real candidate count for Haven
  same as every other adapter.
- **Medium** — mixing `haven_full` (real LLM extraction write path) results
  into the same comparison table as `mem0`/`haven` (both `infer=False`,
  verbatim storage) without an explicit caveat would silently compare
  systems with different write-path opportunity. The code correctly keeps
  these separate (`README.md` documents the distinction), but nothing
  enforces it in `classify_failure.generate_report`, which will happily
  tabulate whatever result files it's pointed at side by side. Process
  discipline, not a code defect — flag it in whatever script/notes drive
  the actual Qwen write-up.
- **Low** — `run_benchmark`'s hardcoded mem0 `config` (`run_benchmarks.py:172-192`)
  configures an `ollama`/`qwen3:8b` LLM that is never invoked (runner always
  calls `add` with `infer=False`). Dead configuration; harmless but
  confusing to a reader trying to understand what mem0 is actually
  configured to do.
- **Low** — 65 of 322 dataset files carry a stale `category` value that
  doesn't match their directory (`"Beliefs"`, `"belief_evolution"`,
  `"decision_consistency"`, `"Decisions"`, `"Temporal"`) and are only
  normalized for reporting purposes via a hand-maintained alias table in
  `classify_failure.py:30-38`, which does not yet have entries for
  `decision_reconstruction`/`refinements` (though those happen to need
  none, since their `category` field already matches their directory).
  Fragile but currently harmless; will silently misreport if a future
  dataset file's `category` drifts from its directory again without a
  matching alias added.
- **Low** — `RUNNER_SPEC.md:53-55` states the judge has "no temperature/seed
  pinned"; the code (`llm_judge.py:120`) actually passes `temperature=0`.
  Minor doc/code mismatch, understates the judge's determinism rather than
  overstating it, so not a fairness problem — just worth correcting so the
  documented caveat matches reality.

---

## What Haven and mem0 get *right* here (for balance)

- `BaseAdapter`'s four-call contract and the fact that `haven`/`mem0`/all
  four baselines share one runner, one write contract (`infer=False`,
  verbatim storage), and one judge is a sound design — the asymmetry found
  is narrow (the answer-formatting/metadata channel) not systemic.
- The baseline suite (`return_all`, `recency`, `bm25`, `embedding`) and the
  scoring-contribution ablations (`haven_no_ontology`, `haven_no_keyword`,
  `haven_no_recency`) are exactly the right instruments for attributing a
  pass rate to a mechanism — the framework is not naive, it is
  under-exercised (nobody has run/reported them against the current
  dataset — see Critical-2/High-2).
- The distractor sweep (`run_distractor.py`) correctly identifies and
  addresses the tiny-corpus precision problem, and its own docstring is
  honest about why it exists.
- No duplicate `benchmark_id`s or duplicate conversation content anywhere
  in the 322-file dataset — whatever produced this corpus did not
  copy-paste cases.
- The framework's own `RUNNER_SPEC.md` already self-flags the legacy
  result-file schema as unverifiable — that's the right instinct; it just
  hasn't been acted on (no fresh run has replaced those files).

---

## If both Criticals are fixed: what should still be disclosed in the README

Even after removing the metadata leak and correcting the dataset
accounting, disclose:

1. Corpus sizes are tiny (avg 1–7 turns/case, `contradictions`/`supersession`
   at 2.0–2.4) — the *un-swept* (`--counts 0`) numbers should never be
   presented without the distractor-sweep context the README already
   describes.
2. `recency`/`return_all` results on the supersession-family categories,
   run against the current dataset, so a reader can judge for themselves
   how much of Haven's edge (if any) survives the "does the judge do the
   work" question in High-1.
3. `goals`/`identity`/most of `preferences` are near-ceiling sanity checks
   (single-turn corpora), not discriminative categories — label them as
   such rather than implying they carry the same evidentiary weight as
   `supersession`/`temporal`.
4. `decision_reconstruction` and `refinements`, once documented, should
   have their design rationale stated (why they exist, how they differ
   from `decisions`/`concept_consolidation`) so a reader can judge the
   overlap question themselves.
5. The judge is a live, temperature-0 but still non-seeded cloud LLM call —
   single-run pass/fail near a category's boundary should be labeled noisy,
   exactly as `RUNNER_SPEC.md` already recommends.
