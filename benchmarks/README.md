# Haven Benchmarks

For the write-path benchmarks comparing the old full-reprocessing
`POST /memory` pipeline against the new checkpoint + incremental
ingestion pipeline, see
[`incremental_ingestion/README.md`](incremental_ingestion/README.md) --
a separate suite from the retrieval-quality comparison below (different
metrics, no LLM judge involved).

For the continuation benchmark -- "given a long, messy project history,
can a fresh conversation resume work correctly?" -- see
[`docs/architecture/CONTINUATION_BENCHMARK_DESIGN.md`](../docs/architecture/CONTINUATION_BENCHMARK_DESIGN.md)
and, for how Haven's side of it is actually ingested,
[`docs/architecture/CONTINUATION_BENCHMARK_INGESTION_DESIGN.md`](../docs/architecture/CONTINUATION_BENCHMARK_INGESTION_DESIGN.md).
Also a separate suite: different dataset root
(`benchmarks/datasets_continuation/`), different runner
(`run_continuation_benchmarks.py`), different judge
(`judges/continuation_judge.py`), scoring a *generated* continuation
response rather than the retrieved set itself. Phase 1 pilot (one
category, `resume_coding`, ~10 cases) is implemented; see the design
doc's §11 for exactly what shipped and what's still Phase 4/5 work.
Ingestion for this runner's `haven` adapter is now
`HavenContinuationAdapter` (deterministic, `turn_type`-driven typed
ingestion -- see the ingestion design doc) rather than plain
`HavenAdapter`'s verbatim-`FACT` storage; this closes the ingestion
design's Critical-1 (`ProjectStateBuilder` never saw a typed candidate, so
`<ProjectState>` was a structurally empty shell on every run). This is
purely a `run_continuation_benchmarks.py`-local adapter resolution --
`run_benchmarks.py`'s own `--adapter haven` (and its `get_adapter_cls`
registry) still resolves to plain `HavenAdapter`, unchanged.

Compares mem0 baseline retrieval against Haven's real pipeline
(`HavenAdapter` — drives `VaultWriter` + `OntologyPipeline` +
`MemoryEngine` directly, no stage bypassed or reimplemented) on the same
benchmark cases. See `RUNNER_SPEC.md` for the benchmark JSON format and
scoring rules, and
[`results/final_report.md`](results/final_report.md) for the full
write-up and measured numbers from the most recent run.

## Running

```
python -m benchmarks.runners.run_benchmarks --adapter mem0     # baseline
python -m benchmarks.runners.run_benchmarks --adapter haven    # Haven
```

Results are written to `results/results.json` (mem0) or
`results/results_<adapter>.json`. Add `--query-rewriter` to enable
`QueryRewriter` multi-query expansion on the Haven adapter (requires
`QUERY_REWRITER_API_KEY`; fails open to no rewrites otherwise).

### Continuation benchmark (pilot)

```
python -m benchmarks.runners.run_continuation_benchmarks --adapter haven
```

Runs every case under `benchmarks/datasets_continuation/` (currently just
`resume_coding`, ~10 cases) through the three-stage pipeline
`CONTINUATION_BENCHMARK_DESIGN.md` §5 specifies: Stage A ingests the
conversation (`--adapter haven`: `HavenContinuationAdapter`, deterministic
`turn_type`-driven typed ingestion, no LLM call -- see
`CONTINUATION_BENCHMARK_INGESTION_DESIGN.md`) and reconstructs context via
`adapter.build_continuation_context()` (`MemoryEngine.query_structured()`
for Haven; every other adapter: a flat `search()`+join fallback), Stage B
has a fixed temperature-0 model resume work from that context alone, Stage
C judges the response against each case's `ground_truth`/rubric. Requires
`QWEN_API_KEY` (Stage B and Stage C both call Qwen Cloud). Results are
written to `results/results_continuation_<adapter>.json`, including a
per-query breakdown and the hard-fail rate (the fraction of (case, query)
pairs that stated a stale/rejected fact as current). `--adapter` defaults
to `haven` -- raw `mem0.Memory` has no `build_continuation_context` method
(it predates `BaseAdapter`) so it always runs under the flat-retrieval
fallback condition when selected.

Retrieval-seeding caveat, separate from ingestion typing: Haven's
retrieval only accepts a candidate that shares keyword/concept overlap
with the query. The pilot dataset's own two stock query phrasings
("Continue implementing the project." / "What should we work on next?")
are generic enough that the first retrieves nothing at all for most of
the 10 `resume_coding` cases, and the second never reaches
`TaskMode.CONTINUATION` at all (a separate, pre-existing `ContextPlanner`
classification gap -- see `CONTINUATION_BENCHMARK_AUDIT.md`), so
`<ProjectState>` still renders near-empty for most cases even with typed
ingestion in place. This is a dataset/retrieval-seeding limitation, not an
ingestion defect -- see `CONTINUATION_BENCHMARK_INGESTION_DESIGN.md`'s
"Phase 2 status" note for a worked before/after example and what Phase 4
authoring should account for.

## Baselines, ablations, and robustness

These exist so a pass rate can be *interpreted*, not just reported. All
use the same runner, dataset, scoring, and result schema — pass a
different `--adapter`, get `results/results_<adapter>.json`, then compare
columns with `python -m benchmarks.analysis.classify_failure`.

**Trivial baselines** (`benchmarks/adapters/baselines.py`) — none touch
Haven's pipeline; they contextualize every other adapter's numbers:

```
python -m benchmarks.runners.run_benchmarks --adapter return_all  # every memory (precision floor)
python -m benchmarks.runners.run_benchmarks --adapter recency     # single most-recent memory
python -m benchmarks.runners.run_benchmarks --adapter bm25        # lexical BM25 top-k
python -m benchmarks.runners.run_benchmarks --adapter embedding   # dense top-k (bge-small, same model as mem0)
```

`recency` is the acid test for the `supersession`/`temporal`/
`contradictions` categories: if "return the latest statement" ties Haven
there, those categories aren't measuring the ontology.

**Haven ablations** (`benchmarks/adapters/ablations.py`) — full Haven with
exactly one ranking weight zeroed via `RetrievalConfig`; Haven's
algorithms are unchanged. These are *scoring-contribution* ablations (the
candidate is still retrieved, it just contributes nothing on that axis).
Diff each against `results_haven.json` to attribute Haven's pass rate to a
mechanism:

```
python -m benchmarks.runners.run_benchmarks --adapter haven_no_ontology  # activation + attachment = 0
python -m benchmarks.runners.run_benchmarks --adapter haven_no_keyword   # keyword_overlap = 0
python -m benchmarks.runners.run_benchmarks --adapter haven_no_recency   # recency = 0
```

**Distractor robustness** (`benchmarks/runners/run_distractor.py`) — sweeps
the number of guaranteed-irrelevant memories inserted *before* each case's
real turns (deterministic, fixed seed, topic-neutral vocabulary), turning
pass rate into a function of noise level. The dataset's ~2-memory corpora
otherwise make precision numbers meaningless.

```
python -m benchmarks.runners.run_distractor --adapter haven --counts 0,10,50,200
python -m benchmarks.runners.run_distractor --adapter return_all --limit 20   # smoke run
```

Writes `results/distractor_sweep_<adapter>.json` and `.md` — a filename
deliberately outside the `results*.json` glob, so its different schema
never collides with the standard per-case report.

## Dataset categories

`benchmarks/datasets/` has 19 category directories. These numbers are
verified directly against the current tree (re-check with `git ls-files
benchmarks/datasets | xargs -I{} wc -c {}` or an equivalent scan — see
`BENCHMARK_AUDIT.md` §Critical-2 for the method):

- **11 directories currently execute cases — 288 total:**
  `beliefs` (25), `concept_consolidation` (62), `contradictions` (10),
  `decision_reconstruction` (26), `decisions` (25), `goals` (10),
  `identity` (10), `preferences` (10), `refinements` (30),
  `supersession` (55), `temporal` (25).
- **3 directories exist and contain files, but every file in them is a
  committed 0-byte placeholder, so they currently execute zero cases:**
  `people` (3 files, all 0 bytes), `projects` (4 files, all 0 bytes),
  `recurring` (3 files, all 0 bytes). `load_benchmarks()` in
  `run_benchmarks.py` calls `json.load()` on each file; an empty file
  raises `JSONDecodeError`, which is caught, printed as a warning, and
  recorded in the result file's `metadata.skipped_files` — the run
  continues rather than crashing. Restoring these three categories is a
  content task (someone needs to author or regenerate real cases), not a
  code or documentation fix; see `BENCHMARK_AUDIT.md` §Critical-2 for why
  they're empty and what's needed to restore them.
- **5 directories are genuinely empty (no files at all) and have no cases
  yet:** `active_context`, `insights`, `memory_recall`,
  `mistake_prevention`, `open_problems`.

`discover_dataset_dirs()` auto-loads all 14 directories that contain files
(the 11 above plus the 3 placeholder-only ones) — there is no allow-list,
so a run today walks all 14 and ends up executing 288 cases, and `decisions`
/ `preferences` / `temporal` each additionally skip 3 of their own 0-byte
files (contributing their 25 / 10 / 25 above, out of 28 / 13 / 28 files on
disk).

`concept_consolidation` (62 cases) covers canonicalization / duplicate
handling — exact duplicates, paraphrased restatements, repeated
confirmations, and mentions repeated months later, including cases with
unrelated memories mixed in. `supersession` (55 cases: the original 25 plus
30 more) covers memory update / supersession across everyday domains —
jobs, location, favourite technologies, project status, deadlines,
preferences, goals, decisions, and habits — including multi-step updates,
directly conflicting statements, partial updates, long gaps, and distractor
memories between updates. `decision_reconstruction` (26 cases) is the
highest-distractor category in the suite (11-28 turns per case, mixed with
unrelated daily-life sentences) — it asks the adapter to reconstruct what
the user ultimately decided from a long, noisy conversation, as opposed to
`decisions`' shorter, less-distracted cases. `refinements` (30 cases) covers
a fact being incrementally refined with more detail across turns (does an
adapter merge the refinement into the existing fact rather than treating it
as a new, separate one). All four reuse the existing runner/adapter/judge
pipeline unchanged; see `RUNNER_SPEC.md` for the shared format.
