---
title: Benchmark Results
tags: [haven, benchmarks]
---

# Benchmark Results

I ran the canonical 288-case, DeepSeek-judged benchmark comparing [[Haven]]'s full
pipeline against naive baselines — BM25, Return-All, Recency, embeddings — plus a
retrieval-only ablation. Haven Full passed 240/288 (83.3%), beating BM25 (69.8%)
and its own retrieval-only ablation (66.0%) by a wide margin.

The honest part: it *loses* on raw pass rate to the two naive baselines,
Return-All (90.3%) and Recency (80.6%), specifically on contradiction and
supersession-heavy categories. Root cause: `CanonicalMatcher` detects `UPDATE` via
a conservative prefix-extension rule but never auto-returns `SUPERSEDE`, so a
differently-phrased correction leaves both the old and new fact independently
retrievable instead of archiving the old one. That's the same gap tracked in
[[Ontology V2]]'s open question about ontology-shaped holes.

I decided to strip the old, fabricated "vs. mem0" numbers from the README and
article entirely and tell this more nuanced, real story instead — a rule I now
hold myself to for every future claim: never cite a benchmark number that doesn't
trace back to a committed result file.

See also: [[Haven]], [[Query Rewriter]], [[Alibaba Cloud Deployment]].
