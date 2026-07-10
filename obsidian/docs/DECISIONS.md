# Architectural Decisions

This document records major design decisions and the reasoning behind them.

---

## Decision 001

Manager AI is decomposed into multiple stages instead of using a single prompt.

Reason:

* Easier debugging.
* Easier benchmarking.
* Easier evaluation.
* Smaller prompts.
* More deterministic behaviour.

---

## Decision 002

The LLM proposes.

Python validates.

Reason:

LLMs are excellent generators but should not be trusted to enforce system correctness.

Every LLM output is validated before entering the pipeline.

---

## Decision 003

Core types are separated from Manager AI models.

Reason:

The Core package represents stable public domain objects.

The Manager AI package represents temporary internal reasoning.

This separation prevents implementation details from leaking into the rest of the system.

---

## Decision 004

Each pipeline stage has exactly one responsibility.

Reason:

Single-responsibility modules are easier to benchmark, replace, and debug.

---

## Decision 005

Introduce BasePipelineStage after three pipeline stages.

Reason:

Extractor, Classifier, and Importance all shared the same execution flow:

Prompt
↓

LLM
↓

JSON Parsing
↓

Validation
↓

Domain Model

Rather than duplicate this logic across every stage, create a reusable pipeline abstraction while allowing each stage to implement only its stage-specific behavior.

---

## Decision 006

Pipeline stages expose strongly typed public APIs.

Reason:

Public methods such as

Extractor.extract()

Classifier.classify()

Importance.score()

are clearer and safer than exposing a generic execute(*args, **kwargs) interface.

The shared pipeline execution remains an internal implementation detail.

---

## Decision 007

Decision Memory's extra fields (reason, alternatives considered, status, supersedes, superseded by) live in `KnowledgeObject.metadata["decision"]`, not as new `KnowledgeObject` dataclass fields.

Reason:

Every other memory type, and every decision written before this feature existed, keeps working with zero migration — `metadata` is already a free-form dict that already round-trips through VaultWriter and MemoryStore unchanged.

Adding dedicated `KnowledgeObject` fields instead would force every non-decision memory to carry them as permanently-empty dead weight, and would touch the ontology and retrieval data model to accommodate a decision-only concern.

See DECISION_MEMORY.md.