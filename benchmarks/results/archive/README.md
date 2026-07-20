# Archive

Result files preserved for history rather than deleted outright, even where
stale or superseded — same convention as `obsidian/docs/archive/`.

- `judge_error_2026-07-11/` — `results_haven_full.json`,
  `results_haven_no_keyword.json`, `results_haven_no_ontology.json`, and
  `results_haven_no_recency.json` from a 2026-07-11 rerun (commit
  `aa508ce9`'s pipeline) in which **every one of the 288 judge calls in
  every file failed** with a provider 403 ("free quota has been
  exhausted"): all cases carry `failure_type: JUDGE_ERROR`,
  `answer_score: 0.0`, `passed: false`. **No usable scores** — the pass
  rates in these files measure the judge's quota, not Haven. This run's
  `results_haven_full.json` had accidentally overwritten the canonical
  2026-07-08 DeepSeek-judged file in `benchmarks/results/` (commit
  `e322db00`); the canonical file has been restored from `aa508ce9`.
  Re-run the three ablation adapters against a working judge before
  citing any ablation-vs-full comparison beyond `haven_retrieval`.
- `qwen_partial_2026-07-11/` — `results.json`, `results_bm25.json`,
  `results_embedding.json`, `results_haven_retrieval.json`,
  `results_recency.json`, `results_return_all.json`, and
  `.checkpoint_haven_full.json` from a 2026-07-11 rerun judged by Qwen
  Cloud (`qwen3.5-plus-2026-02-15`, the config's actual default provider).
  **Not canonical and not complete**: `haven_full` never finished (only
  the in-progress checkpoint exists, no `results_haven_full.json`).
  Archived for engineering reference only, per the explicit decision to
  ship the complete DeepSeek suite below as the hackathon release's
  canonical benchmark artifacts.
- `pre_288case_run/` — `results.json` (mem0) and `results_haven.json`
  (`haven`/`haven_retrieval` under its old name), both 100-case runs with no
  `metadata` block, predating the 288-case dataset and the
  `metadata.generated_at`/`git_commit`/`adapter` schema. Superseded by the
  DeepSeek-judged 288-case run.

The **active/canonical** results at `benchmarks/results/results*.json` are
the complete 2026-07-08 DeepSeek-judged run: `results_bm25.json`,
`results_embedding.json`, `results_haven_full.json`,
`results_haven_retrieval.json`, `results_recency.json`,
`results_return_all.json`. Despite the `QWEN_*`-named config
(`benchmarks/judges/llm_judge.py` itself is unmodified), that run's
`QWEN_API_KEY`/`QWEN_BASE_URL`/`QWEN_JUDGE_MODEL` were pointed at
DeepSeek's endpoint — see
`benchmarks/reports/archive/deepseek_validation_report.md` for the full
validation writeup and confirmation the judge behaved reliably. Raw run
logs for this canonical run are kept alongside it at
`benchmarks/results/deepseek_run_logs/` (git-ignored, regeneratable).

**No mem0 baseline (`results.json`) is part of the canonical set.** The
2026-07-08 DeepSeek pass never ran the plain `mem0` adapter — the
validation report's own "Sources" list only covers the six files above.
The only 288-case, current-schema mem0 baseline that exists is the
Qwen-judged one archived above (`qwen_partial_2026-07-11/results.json`),
and it is deliberately **not** substituted in here: mixing a
DeepSeek-judged suite with a Qwen-judged mem0 row would make cross-adapter
comparisons apples-to-oranges. The Benchmark Explorer simply shows no
`mem0` rows until a real DeepSeek-judged mem0 run exists.
