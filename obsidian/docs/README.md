# Haven Docs

Haven is documented at three levels:

- **Root [`README.md`](../../README.md)** — quickstart: run the server, load
  the browser extension, seed demo data.
- **[`obsidian/server/README.md`](../server/README.md)** — full HTTP API
  reference (endpoints, request/response shapes, the dashboard, the
  Retrieval Inspector, the prompt renderers).
- **This folder** — design and status docs for the system itself.
- **[`benchmarks/results/final_report.md`](../../benchmarks/results/final_report.md)**
  — the hackathon engineering report: measured mem0-vs-Haven numbers,
  what shipped, known limitations.

## Start here

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — how a conversation becomes a
  `KnowledgeObject`, how a `KnowledgeObject` becomes retrievable, and how a
  query becomes a context string. Read this first.
- **[PROGRESS.md](PROGRESS.md)** — what's implemented vs. outstanding, by
  subsystem.
- **[KNOWN_ISSUES.md](KNOWN_ISSUES.md)** — real gaps and rough edges in the
  current repository.
- **[ROADMAP.md](ROADMAP.md)** — what's next, in priority order.

## Deep dives

- **[DECISION_MEMORY.md](DECISION_MEMORY.md)** — how Haven remembers *why*
  a decision was made, and how superseding a decision works.
- **[DECISIONS.md](DECISIONS.md)** — the architectural decisions behind the
  pipeline shape (why stages are separated, why the LLM only proposes,
  etc.).
- **[MEMORY_TYPES.md](MEMORY_TYPES.md)** — the semantic categories a memory
  can be classified into.
- **[MEMORY_STATES.md](MEMORY_STATES.md)** — the conceptual memory
  lifecycle model (see that doc's implementation note — it's not fully
  built yet).
- **[IMPORTANCE.md](IMPORTANCE.md)** — importance tiers used by the
  Importance Scorer.
- **[RELATIONSHIPS.md](RELATIONSHIPS.md)** — relationship types between
  Concepts in the ontology graph.
- **[HACKATHON_SCOPE.md](HACKATHON_SCOPE.md)** — the original must-have /
  nice-to-have / post-hackathon scope split.
- **[TECH_DEBT.md](TECH_DEBT.md)** — intentional shortcuts taken during MVP
  development.
- **[MEMORY_TYPES.md](MEMORY_TYPES.md)**, **[DEBUG_LOG.md](DEBUG_LOG.md)** —
  supporting reference and a dated debugging log from early development.
- **[`docs/architecture/ONTOLOGY_SPEC.md`](../../docs/architecture/ONTOLOGY_SPEC.md)**
  and **[`docs/architecture/ACCEPTANCE_STAGE_DESIGN.md`](../../docs/architecture/ACCEPTANCE_STAGE_DESIGN.md)**
  — the original design specs for the ontology layer and the acceptance
  stage (both now implemented; see each doc's status line).
