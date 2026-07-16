---
title: Ontology V2
tags: [haven, ontology]
---

# Ontology V2

The Ontology V2 migration groups all 18 of [[Haven]]'s memory types into 3
`MemoryDomain`s (Personal, Work, Knowledge) via `obsidian/core/memory_domain.py`,
plus a new `topic_canonicalizer.py` for canonicalized topic tags. It's implemented
and sitting in the working tree, ready for the hackathon submission, though I'm
still double-checking the canonicalization table for regressions before I'm
comfortable calling it done.

One open question this raises: should [[Benchmark Results|the benchmark]] treat a
missing `IS_A` relationship (the ontology edge type that's declared but never
actually emitted anywhere) as its own tracked gap, separate from the
`CanonicalMatcher` SUPERSEDE gap? Both are ontology-shaped holes, but they cause
different kinds of failures.

See also: [[Haven]], [[Query Rewriter]] (which reads the same concept graph this
migration reorganizes).
