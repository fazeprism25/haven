# Incremental Ingestion Benchmarks

Compares Haven's two `POST /memory` code paths, both real and unmodified:

* **`old_full`** -- no `external_key`. Every "Remember" click reprocesses
  the whole conversation through the Manager AI pipeline (Haven's
  behaviour before the Conversation Checkpoint subsystem, PR 1-4,
  existed).
* **`new_incremental`** -- `external_key` supplied. Checkpointing (PR 1-3)
  short-circuits unchanged conversations; incremental ingestion with
  Working Context (PR 4) sends only new turns to the Extractor plus a
  compact, retrieval-based background block.

## Running

```
python -m benchmarks.incremental_ingestion.run_benchmarks            # full scale
python -m benchmarks.incremental_ingestion.run_benchmarks --quick     # fast smoke run
python -m benchmarks.incremental_ingestion.run_benchmarks --categories 3,4
```

Writes `results/results.json` (raw, one entry per request -- re-plottable
without rerunning anything) and `results/report.md` (a Markdown digest of
the same data).

## Methodology

Every scenario drives the real `obsidian.server.main.app` through
`fastapi.testclient.TestClient`, exactly like `obsidian/tests/server/`
already does -- no pipeline stage is reimplemented or bypassed. Three
things are specific to this benchmark suite, not to Haven itself:

1. **A scripted, marker-based fake LLM** (`fake_llm.py`) stands in for
   the real Manager AI LLM. It extracts exactly the facts its `FACT[id]:`
   / `FACTIF[needle]:` markers describe, deterministically -- see that
   module's docstring for the full syntax and rationale. This is a
   deliberate trade-off: a real LLM's language-understanding quality
   varies run to run and isn't reproducible evidence, whereas what
   actually changed between the two pipelines is *how much of the
   conversation, and which background facts, reach the Extractor at
   all*. The fake LLM isolates exactly that variable. **It cannot, and
   does not try to, benchmark real LLM comprehension.**
2. **No real LLM call latency.** `elapsed_seconds` measures true
   wall-clock time for the whole HTTP call, but with an instant fake LLM,
   that time is dominated by retrieval/checkpoint overhead, not by
   anything proportional to prompt size (see Finding 2 below). In
   production, LLM latency and cost scale with tokens sent, so
   `llm_calls` and `extractor_prompt_*` are the trustworthy proxies for
   "faster/cheaper"; `elapsed_seconds` in this suite is not.
3. **Token counts are an estimate**, `chars / 4` -- a common rough
   heuristic for English text. This repo has no tokenizer dependency,
   and adding one solely for these benchmarks wasn't judged worth a new
   dependency; treat `extractor_prompt_tokens_est` as directionally
   correct, not exact.

## Key findings

**1. The core prompt-size claim holds, and holds increasingly well at scale.**
At 500 turns, `old_full` sends the Extractor a ~27,200-character prompt
(~6,800 est. tokens); `new_incremental` sends ~3,075 characters (~769 est.
tokens) -- a constant size regardless of total conversation length,
against `old_full`'s linear growth. Category 1 (duplicate remember) shows
the other half of the story: repeat sends of an unchanged conversation
cost `new_incremental` zero LLM calls per repeat, against 3 per repeat for
`old_full`.

**2. Working Context retrieval time grows with the vault's accumulated size,
and can offset the wall-clock benefit of a smaller prompt.** In category 3,
`working_context_seconds` grows from ~0.002s (25 turns) to ~0.138s (500
turns) -- and at every size, `new_incremental`'s total `elapsed_seconds` is
about the same as (sometimes marginally worse than) `old_full`'s, even
though its prompt is far smaller. With a real LLM this would still net out
strongly in `new_incremental`'s favour (LLM latency/cost scales with the
~9x larger prompt `old_full` sends), but it means retrieval cost is a real,
growing overhead this architecture adds, worth watching as vaults grow
much larger than this benchmark's scale -- not something to fix now, just
to know about.

**3. Existing Context only surfaces goal/decision/task/open-question-classified
memories -- not plain facts, preferences, beliefs, or other memory types.**
This is the most significant finding. Category 4's two "context-dependent
update" scenarios are identical except for one thing: whether the
referenced earlier statement ("The user uses Python.") was classified as
`memory_type="decision"` or `memory_type="fact"`. When classified as a
decision, `new_incremental` resolves the later reference ("no longer uses
their previous language") correctly, matching `old_full` exactly. When
classified as a plain fact -- which is the more realistic classification
for a statement like "I use Python" -- `new_incremental` **silently drops
the update**, in both the "anchored" (referent inside the anchor window)
and "orphaned" (referent far away, no shared keywords nearby) variants.

This traces to `WorkingContextState.from_buckets` (unmodified,
pre-existing code): it only ever populates `current_goal` /
`recent_decisions` / `pending_tasks` / `open_questions`, so any memory
resolved to `MemoryRole.RESEARCH`, `BELIEF`, or `REFERENCE` (which is what
most plain facts resolve to -- see `resolve_role` in
`obsidian.ontology.retrieval_models`) never reaches
`Extractor._render_existing_context`, regardless of whether retrieval
found and ranked it as relevant. Note that keyword distance from the
anchor window did **not** appear to be the deciding factor here -- both
the "anchored" and "orphaned" variants behaved identically for a given
memory type, which itself is worth a closer look independent of the
memory-type gap.

**This is a real accuracy gap for a very plausible real-world case**
("I switched from X to Y" where X/Y are plain facts, not goals or
decisions) **and is reported here, not fixed** -- per this benchmarking
phase's scope.

## Failure-case behaviour (category 5)

All five failure/edge cases behaved as the PR 4 design intended: an
edited, deleted, or reordered earlier turn all fall back to a full
reprocess (`checkpoint_mode="fallback"`, no crash); an incremental click
into an unrelated topic correctly omits the `EXISTING CONTEXT` section
rather than emitting an empty scaffold; and a simulated Working Context
retrieval failure degrades to `existing_context=None` and still saves
successfully, exactly as documented. No regressions found in this
category.
