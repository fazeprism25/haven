# Haven Progress

## Overall Goal

Build a deterministic long-term memory system that outperforms baseline
mem0 on retrieval quality while remaining explainable, modular, and
benchmarkable.

---

# Completed

## Manager AI (write pipeline)

- [x] Extractor
- [x] Classifier
- [x] ImportanceScorer
- [x] CanonicalMatcher
- [x] KnowledgeUpdater — `NEW`, `CONFIRM`, `UPDATE`, `SUPERSEDE` all
      implemented
- [x] ManagerPipeline integration — drives `NEW`, `CONFIRM`, and `UPDATE`
      end-to-end automatically; `SUPERSEDE` currently requires
      calling `KnowledgeUpdater` directly (see
      [ARCHITECTURE.md](ARCHITECTURE.md#write-pipeline-manager-ai))
- [x] ChatGPT conversation importer
- [ ] Claude / Gemini conversation importers — package stubs only
      (`obsidian/integrations/claude/`, `obsidian/integrations/gemini/`)

## Knowledge Model

- [x] KnowledgeObject
- [x] EvidenceEntry
- [x] KnowledgeDecision
- [x] Decision Memory (`DecisionMetadata`, `DecisionStatus`,
      `supersede_decision`) — see [DECISION_MEMORY.md](DECISION_MEMORY.md)

## Persistence

- [x] VaultWriter
- [x] MemoryStore / VaultIndex
- [x] MemoryParser

## Ontology

All phases below are implemented and covered by tests — this section used
to track them as an in-progress roadmap; they're now part of every
retrieval call.

- [x] Concept, Relationship, Attachment, OntologyProposal models
- [x] Identity generation, text normalization, serialization, validation
- [x] Concept Markdown format + `ConceptWriter`
- [x] `ConceptParser` (round-trip Markdown ↔ `Concept`/`Relationship`)
- [x] `ConceptGraph` + `ConceptGraphLoader`
- [x] `OntologyValidator`
- [x] `OntologyManager` + `OntologyPipeline` (write-path integration)
- [x] `ActivationSpreader`
- [x] `HybridCandidateRetriever` (concept-aware + keyword retrieval,
      merged)
- [x] `MemoryEngine` integration (`obsidian/memory_engine/engine.py`)

## Memory Engine (read pipeline)

- [x] HybridCandidateRetriever
- [x] DeterministicRanker
- [x] AcceptanceStage (abstention / score-gap cut / relative floor / hard
      cap — design in
      [`docs/architecture/ACCEPTANCE_STAGE_DESIGN.md`](../../docs/architecture/ACCEPTANCE_STAGE_DESIGN.md))
- [x] DeterministicSlotAllocator
- [x] ContextBuilder (flat renderer — live, backs `/retrieve_context`)
- [x] QueryRewriter (optional multi-query expansion, fails open on error)
- [x] WorkingContextBuilder (groups allocated candidates by anchor concept —
      the assembly stage `StructuredPromptBuilder` needed; live via
      `MemoryEngine.query_working_context`)
- [x] StructuredPromptBuilder (XML renderer — live via
      `MemoryEngine.query_structured` / `POST /retrieve_working_context`,
      including an optional `<ProjectState>` for `CONTINUATION`-mode
      queries; `POST /retrieve_context` is intentionally unaffected and
      still only calls `ContextBuilder`, see `ARCHITECTURE.md`)
- [x] ProjectState (deterministic project-status snapshot from a query's
      accepted candidates — `ProjectStateBuilder`, rendered by
      `StructuredPromptBuilder` for `CONTINUATION` queries; also attached to
      `RetrievalTrace` for every query)
- [x] MemoryEngine (`query`, `query_with_trace`, `query_working_context`,
      `query_structured`)

## Server, Dashboard, Extension

- [x] FastAPI server (`obsidian/server/`) — `/health`, `/retrieve_context`,
      `/retrieve_working_context`, `/memory`, `/dashboard`,
      `/dashboard/inspect`, `/dashboard/inspect/memory/{id}`
- [x] Memory Dashboard (`GET /dashboard`) — overview, categories, recent
      memories, Retrieval Inspector, Memory Inspector panel
- [x] Browser extension (`extension/`) — Use Haven / Remember buttons on
      ChatGPT, popup search and settings
- [x] Demo data seeding (`scripts/seed_demo.py`, `demo/demo_memories.md`)

## Benchmarks (`benchmarks/`, repo root — not under `obsidian/`)

- [x] Benchmark runner (`benchmarks/runners/run_benchmarks.py`)
- [x] Mem0 adapter (drives `mem0.Memory` directly) and Haven adapter
      (`benchmarks/adapters/haven_adapter.py`, drives Haven's real
      `VaultWriter`/`OntologyPipeline`/`MemoryEngine` — no pipeline stage
      bypassed or reimplemented)
- [x] LLM judge (`benchmarks/judges/llm_judge.py`)
- [x] Datasets — 159 cases across 11 populated categories (`beliefs`,
      `contradictions`, `decisions`, `goals`, `identity`, `people`,
      `preferences`, `projects`, `recurring`, `supersession`, `temporal`);
      6 more category folders exist but are empty (`active_context`,
      `concept_consolidation`, `insights`, `memory_recall`,
      `mistake_prevention`, `open_problems`)
- [x] A results run comparing mem0 baseline vs. Haven — see
      [`benchmarks/results/final_report.md`](../../benchmarks/results/final_report.md)
      for the full write-up and measured numbers (retrieval precision
      0.301 → 0.679, false-positive rate 0.500 → 0.100, accepted
      candidates 278 → 110, ~0ms latency change)

---

# Remaining

## Manager AI pipeline orchestration

- [ ] Wire `SUPERSEDE` into `ManagerPipeline.match_and_apply()` so a
      real conversation can trigger a contradiction-driven archive/replace
      automatically, not just via a direct `KnowledgeUpdater` call.
      (`UPDATE` is already auto-driven via `CanonicalMatcher`'s
      prefix-extension rule.)

## Prompt assembly

- [ ] Step 2 of `ProjectState`×`WorkingContext` integration: a freshness
      check + bounded gap-fill fallback so a `CONTINUATION` query stops
      paying full retrieval cost every call (every such query still runs
      the complete pipeline today). See
      [`docs/architecture/PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md`](../../docs/architecture/PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md)
      §7.

## Other importers

- [ ] Claude and Gemini conversation importers (currently ChatGPT only)

---

Last updated: ontology phases 2C–9, Decision Memory, the FastAPI server,
the dashboard, and a mem0-vs-Haven benchmark run are all complete.
