# Haven Final Engineering Report

Version: Hackathon Submission
Author: Siddhartha Khajuria

---

# Executive Summary

Haven is a deterministic, explainable AI memory system built on top of mem0.

Unlike traditional memory systems that rely primarily on embeddings or opaque retrieval heuristics, Haven focuses on:

- deterministic retrieval
- ontology-aware retrieval
- explainable ranking
- acceptance-stage filtering
- transparent diagnostics
- local-first storage
- browser integration

The system stores memories as Markdown KnowledgeObjects, constructs an ontology graph, retrieves relevant context deterministically, explains every retrieval decision, and injects context directly into LLM conversations.

---

# Architecture

User

↓

Browser Extension

↓

FastAPI Server

↓

Query Rewriter

↓

Hybrid Retrieval

↓

Deterministic Ranker

↓

Acceptance Stage

↓

Context Builder

↓

LLM

Memory Write Path

Browser

↓

POST /memory

↓

KnowledgeObject

↓

VaultWriter

↓

OntologyPipeline

↓

Markdown Vault + Ontology Graph

---

# Major Features

## Memory

- Markdown KnowledgeObjects
- Ontology Graph
- Local-first storage
- Memory versioning support
- Browser memory saving

## Retrieval

- Hybrid keyword + ontology retrieval
- Query Rewriter
- Stop-word filtering
- Controlled token normalization
- Deterministic ranking
- Keyword overlap scoring
- Acceptance Stage

## Explainability

- Retrieval Inspector
- Memory Inspector
- Dashboard
- Candidate traces
- Score breakdown
- Acceptance reasoning

## Product

- Browser Extension
- Dashboard UI
- Local API
- Live Retrieval Inspector
- Memory Inspector

---

# Engineering Improvements

## Retrieval Inspector

Added complete pipeline tracing.

Every retrieved memory exposes:

- keyword contribution
- ontology contribution
- activation score
- attachment score
- importance
- confidence
- recency
- acceptance decision
- rejection reason

---

## Stop-word Filtering

Removed stop-word-only retrievals.

Result:

- major reduction in false positives

---

## Token Normalization

Added deterministic normalization table.

Examples:

- project ↔ projects
- commit ↔ commits
- build ↔ building

Avoided aggressive stemming to preserve proper nouns.

---

## Keyword Overlap Ranking

Introduced IDF-weighted overlap scoring.

Added phrase bonus.

Integrated into deterministic ranking.

---

## Acceptance Stage

Implemented deterministic acceptance after ranking.

Stages:

1. Minimum score
2. Abstention
3. Score-gap detection
4. Relative threshold
5. Maximum accepted candidates

---

## Tokenizer Fix

Fixed contraction handling.

Previously:

What's

↓

what

s

Now:

What's

↓

what

Removed spurious retrieval caused by contraction fragments.

---

## Memory API

Implemented:

POST /api/v1/memory

Immediate availability without restart.

---

## Dashboard

Implemented dashboard exposing:

- Projects
- Decisions
- Beliefs
- Preferences
- Tasks
- Recent memories
- Retrieval Inspector
- Memory Inspector

---

# Measured Improvements

The precision/false-positive/accepted-candidates figures previously listed here
did not trace back to any result file in this repo — see
`benchmarks/analysis/classify_failure.py`'s module docstring. They have been
removed rather than corrected in place, since no run reproducing them was ever
committed.

What is measured and reproducible:

- **Write path** (`benchmarks/incremental_ingestion/`): re-sending an unchanged
  conversation costs 0 LLM calls vs. 3 for a full reprocess; at 500 turns the
  extraction prompt holds constant at ~769 est. tokens vs. ~6,800 for full
  reprocessing. See `benchmarks/incremental_ingestion/results/report.md`.
- **Read path** (`benchmarks/runners/run_benchmarks.py`): a 288-case,
  LLM-judged suite comparing Haven's full pipeline against naive baselines
  (return-everything, most-recent, BM25, embeddings) and a retrieval-only
  ablation. Haven Full passes 240/288 (83.3%) — beats BM25 (69.8%) and its own
  retrieval-only ablation (66.0%) by a wide margin, loses on raw pass rate to
  the two naive baselines on contradiction/supersession-heavy categories for a
  root-caused, scoped reason (`CanonicalMatcher` doesn't yet detect a
  differently-phrased contradiction as `SUPERSEDE`). Full breakdown:
  `benchmarks/reports/archive/deepseek_validation_report.md`.

---

# Bugs Found During Development

## Contraction Tokenization

Root cause:

"What's"

became

"what"

"s"

Result:

"s" matched every possessive memory.

Status:

Fixed.

---

## Keyword Overlap Denominator

Found an edge case where absent query terms collapsed the denominator and inflated overlap scores.

Root cause documented.

Future improvement planned.

---

# Explainability

Unlike traditional memory systems, Haven explains every retrieval.

For every memory the system exposes:

- why it matched
- why it ranked
- why it was accepted
- why it was rejected

No hidden scoring.

---

# Product Components

- Browser Extension
- Local FastAPI Server
- Dashboard
- Retrieval Inspector
- Memory Inspector
- Markdown Vault
- Ontology Graph

---

# Current Limitations

- Single-user design
- Local-only deployment
- No authentication
- No live synchronization
- Dashboard polling instead of push updates
- Canonical supersession pipeline not yet integrated into production writes

---

# Future Work

- Memory supersession
- Incremental ontology updates
- Multi-user support
- Cloud synchronization
- Automatic conversation remembering
- Live dashboard updates
- Better semantic normalization

---

# Conclusion

Haven evolved from a baseline memory engine into a complete explainable memory platform.

The project prioritizes:

- correctness
- transparency
- determinism
- local-first design

while remaining lightweight enough for real-time browser integration.