# Benchmark Runner Specification

Covers the main-suite adapters `benchmarks/runners/run_benchmarks.py`
drives via `get_adapter_cls` — `mem0`, `haven` (alias: `haven_retrieval`),
`haven_full`, the trivial baselines (`return_all`, `recency`, `bm25`,
`embedding`), and the Haven scoring-contribution ablations
(`haven_no_ontology`, `haven_no_keyword`, `haven_no_recency`) — see
`benchmarks/adapters/base.py` for the adapter interface itself. The
continuation benchmark (`benchmarks/runners/run_continuation_benchmarks.py`,
`HavenContinuationAdapter`) is a separate, additive pipeline over a
different dataset root (`benchmarks/datasets_continuation/`) and is not
covered by this spec.

## Benchmark Format
Each benchmark file must contain:
- `benchmark_id`: Unique identifier for the benchmark
- `category`: Category of the benchmark (e.g., "decisions", "beliefs") —
  required; `run_benchmark()` reads it unconditionally, and
  `load_benchmarks()` skips (with a printed warning) any file missing it
  or any other required field, the same way it skips malformed JSON.
- `conversation`: Array of `{"speaker": ..., "text": ...}` memory entries
  to insert (`run_benchmark` reads each entry's `"text"`)
- `query`: The query to execute against the adapter
- `expected.answer_contains`: Strings the final answer should convey
- `expected.must_not_contain`: Strings the final answer should not convey

`benchmarks/runners/run_benchmarks.py:discover_dataset_dirs()` loads
every subdirectory of `benchmarks/datasets/` automatically — a new
category directory is picked up with no separate wiring step. Within each
directory, `load_benchmarks()` walks every `*.json` file and calls
`json.load()` on it; a file that fails to parse (including a committed
0-byte file — an empty file is not valid JSON) or that parses but is
missing one of the required fields above is skipped: a warning is printed,
the path is appended to `metadata.skipped_files` in the output (see
"Output Schema" below), and the run continues. As of the current tree this
means 3 of the 14 non-empty category directories (`people`, `projects`,
`recurring`) contribute zero cases to every run — every file in them is a
committed 0-byte placeholder. See `README.md`'s "Dataset categories"
section for the full current per-category breakdown, and
`BENCHMARK_AUDIT.md` §Critical-2 for how that was verified.

## Execution Flow
1. Construct a fresh adapter instance (`AdapterCls.from_config(config)`)
   — no prior data.
2. `delete_all(...)` as a defensive reset (exceptions swallowed).
3. Insert the conversation. Adapters that expose `add_conversation(...)`
   (e.g. `HavenFullAdapter`, whose `ManagerPipeline` needs the whole
   conversation as one unit) get it in a single call; adapters without one
   (raw `mem0.Memory`, `HavenAdapter`) fall back to one `add(...)` call per
   entry, `infer=False` (store verbatim, no LLM extraction).
4. Execute the benchmark query via `search(...)`.
5. Join every retrieved memory's `"memory"` field into one answer string.
6. Score the answer with the LLM judge (`benchmarks/judges/llm_judge.py`).

## Answer Scoring
Scoring is **not** literal substring matching — `expected.answer_contains`
/ `must_not_contain` are requirements handed to an LLM judge (Qwen Cloud,
via `judge_answer()`), which evaluates semantic meaning: paraphrases and
synonyms are accepted, and old/superseded memories in the answer are only
penalized if they contradict the *current* belief/decision the query asks
about. The judge returns:

```json
{
  "passed": true,
  "score": 0.0-1.0,
  "reason": "...",
  "failure_type": "NONE|RETRIEVAL|SUPERSESSION|TEMPORAL|REASONING|INCOMPLETE|INCORRECT|JUDGE_ERROR"
}
```

Requires `QWEN_API_KEY` (raises `RuntimeError` if unset); optionally
`QWEN_JUDGE_MODEL` / `QWEN_BASE_URL` to override the model/endpoint. The
judge call pins `temperature=0` (`llm_judge.py`) but has no seed parameter;
because it is still a live LLM call, pass/fail on borderline answers **can
vary between runs** — treat single runs near a category's pass-rate
boundary as noisy, not authoritative.

## Output Schema
`save_results()` writes:
```json
{
  "metadata": {
    "generated_at": "2026-...",
    "adapter": "haven",
    "git_commit": "...",
    "query_rewriter_enabled": false,
    "dataset_dirs": ["benchmarks/datasets/...", ...],
    "total_benchmarks": 140,
    "skipped_files": ["benchmarks/datasets/.../basic_026.json", ...]
  },
  "results": [
    {
      "benchmark_id": "...",
      "category": "...",
      "passed": true/false,
      "answer_score": 0.0-1.0,
      "query": "...",
      "expected": {...},
      "answer": "...",
      "retrieved_memories": [...],
      "judge_reason": "...",
      "failure_type": "...",
      "timings": {"add": ..., "search": ..., "judge": ..., "total": ...}
    },
    ...
  ]
}
```

The `metadata` block lets a report (see
`benchmarks/analysis/classify_failure.py`) confirm what commit/config
produced a result file — result files saved before this schema existed
(e.g. the committed `benchmarks/results/results*.json` as of the v1.0
release) are a flat `results` list with no `metadata`, and should be
treated as unverifiable snapshots rather than current numbers.
