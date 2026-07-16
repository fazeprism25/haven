---
title: Haven
tags: [project, second-brain]
---

# Haven

Haven is my personal second-brain project: a deterministic, keyword-plus-ontology
retrieval pipeline with no embeddings, so every answer it gives back stays fully
explainable end to end. I decided early on to keep it that way — I'd rather have
slightly lower recall than a black-box ranker I can't explain to myself, let alone
to a hackathon judge.

The write side is a five-stage Manager AI pipeline (Extractor, Classifier,
ImportanceScorer, CanonicalMatcher, KnowledgeUpdater). The read side is the Memory
Engine, which the dashboard and the browser extension both call — no second
retrieval implementation exists anywhere in the stack.

Related notes:
- [[Ontology V2]] — how memories get grouped and connected under the hood.
- [[Query Rewriter]] — Haven's first real cloud-LLM integration.
- [[Benchmark Results]] — the honest, current numbers behind the retrieval claims.
- [[Alibaba Cloud Deployment]] — where the live hackathon demo actually runs.

Current focus: finishing the [[Ontology V2]] migration and the
[[Alibaba Cloud Deployment]] before the hackathon submission deadline.
