---
title: Query Rewriter
tags: [haven, retrieval]
---

# Query Rewriter

[[Haven]]'s Query Rewriter was the first place I wired a real cloud LLM into the
pipeline: Qwen Cloud's `qwen-plus` model over DashScope's OpenAI-compatible
endpoint. It's a best-effort, fail-open enhancement — if the API call fails or the
setting is off, retrieval falls straight back to Haven's deterministic default with
no rewrites, never a broken query.

I later reused the exact same client pattern to wire Manager AI's Extractor,
Classifier, and ImportanceScorer to Qwen Cloud too, instead of inventing a second
integration. That decision is part of why the [[Alibaba Cloud Deployment]] made
sense — a same-region deployment keeps latency low for every one of those calls,
not just this one.

See also: [[Haven]], [[Ontology V2]], [[Benchmark Results]].
