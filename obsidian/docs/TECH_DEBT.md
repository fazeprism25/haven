# Technical Debt

This document tracks intentional shortcuts taken during MVP development.

Only add items that are consciously postponed. Items later completed are
removed rather than left checked off, to keep this a live "still owed"
list — see [PROGRESS.md](PROGRESS.md) for what's been delivered.

---

## Manager AI

### Extractor

* Move prompts from Python into prompt template files.
* Replace last-event mapping with event_index returned by the LLM.
* Replace manual JSON validation with shared validation utilities.

### Classifier

* Move prompts into prompt template files.
* Share parsing logic with Extractor.
* Share JSON validation with other pipeline stages.

### Pipeline orchestration

* `NEW`/`CONFIRM`/`UPDATE` now run automatically end-to-end;
  `ManagerPipeline.match_and_apply()` drives `UPDATE` via `CanonicalMatcher`'s
  conservative deterministic prefix-extension rule, and retrieval is
  validity-aware (archived memories are excluded). Remaining work:
  * **Stage 3** — wire `SUPERSEDE` into `match_and_apply()`: generalise the
    archive-and-recreate `(archived_old, new)` pair beyond
    `MemoryType.DECISION` so a contradiction archives the old memory (sets
    `valid_until`) and creates a replacement, both persisted.
  * **Stage 4** — give `CanonicalMatcher` (or a restored Supersession stage)
    the ability to *decide* `UPDATE` vs `SUPERSEDE` vs `NONE` semantically
    (contradiction/identity detection), populating the existing
    `SupersessionResult` model. This is the LLM-shaped judgement the current
    deterministic matcher deliberately avoids.

---

## Pipeline (general)

* Introduce a shared `BasePipelineStage` after the Manager AI modules
  stabilize.
* Add latency tracking for every stage.
* Capture raw LLM responses for debugging.
* Store structured pipeline traces for developer inspection.
* Replace stage-specific public methods with a common benchmark interface.
* Add per-stage latency and token usage metrics.
* Support partial pipeline execution for benchmarking.

---

## Base LLM interface

* Replace `generate(prompt)` with a structured `LLMRequest` object.
* Support separate system and user prompts.
* Support configurable temperature.
* Support configurable max tokens.

---

## Context / Prompt assembly

* Add prompt versioning.
* Add prompt caching.
* Add retrieval statistics.
* Wire `StructuredPromptBuilder` into `/retrieve_context` (needs a
  `RankedCandidate[] → WorkingContext[]` assembly stage — see
  [KNOWN_ISSUES.md](KNOWN_ISSUES.md)).

---

## Vault / Markdown Writer

* Introduce persistent storage backend beyond flat Markdown files.
* Add graph indexing.
* Add embedding cache.
* Replace text-based filenames with stable MemoryIdentity-based filenames.
* Separate YAML frontmatter generation from Markdown rendering.
* Support template-based rendering for different memory types.
* Add `observed_at` (conversation timestamp) alongside `created_at`
  (storage timestamp).
* Add automatic backlink generation between related memories.
* Support incremental updates instead of rewriting entire notes.

---

## Retrieval

* Embeddings as a third retrieval path alongside keyword and
  ontology/concept-activation (hybrid retrieval currently combines the
  latter two, plus recency as a ranking factor — not embeddings).
* Reranking with a lightweight model.
* Fill the 5 still-empty benchmark dataset categories and fix the
  keyword-overlap denominator edge case flagged in
  [`benchmarks/results/final_report.md`](../../benchmarks/results/final_report.md)
  (retrieval quality benchmarking itself is done — see
  [KNOWN_ISSUES.md](KNOWN_ISSUES.md)).

---

## Importers

* Support automatic conversation syncing instead of manual exports.
* Add incremental import (only new messages).
* Preserve original conversation metadata and attachments.
* Implement the Claude and Gemini importers (currently empty package
  stubs).

The earlier file-export-based `ConversationLoader`/`ChatGPTImporter`
(`obsidian/importers/`, `obsidian/integrations/chatgpt/`) was removed as
orphaned dead code during the pre-release audit — nothing called it, and
its circular-dependency/dispatcher-reduction debt items above are moot
now that it's gone. Live conversation ingestion goes through
`POST /memory` (ChatGPT, captured by the browser extension) and
`obsidian/integrations/obsidian/importer.py` (Obsidian vault bulk
import) instead — see [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

---

## Product

* Automatic background syncing while chatting with AI (today: manual
  "Use Haven" / "Remember" buttons in the extension).
* Live memory updates without requiring manual export.
* Extend the browser extension beyond ChatGPT to Claude, Gemini, and other
  AI assistants.

---

## Future Features

* Memory decay.
* Memory persistence score.
* Adaptive forgetting.
* User feedback loop.
* Prompt evaluation benchmarks.
