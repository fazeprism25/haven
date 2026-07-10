# Hackathon Scope

The original must-have / nice-to-have / post-hackathon split, with delivery
status annotated. See [PROGRESS.md](PROGRESS.md) for full detail.

## Must Have

- [x] Conversation import — ChatGPT only; Claude/Gemini importers are
      stubs
- [x] Manager AI
- [x] Memory extraction
- [x] Classification
- [x] Importance
- [x] Markdown generation
- [x] Vault storage
- [x] Retrieval
- [x] Prompt injection (via the browser extension / `/retrieve_context`)
- [x] Demo UI (the Memory Dashboard, `GET /dashboard`)

## Nice to Have

- [x] Supersession — `KnowledgeUpdater`'s `SUPERSEDE` decision and
      Decision Memory's `supersede_decision()`; not yet auto-triggered by
      `ManagerPipeline` (see [KNOWN_ISSUES.md](KNOWN_ISSUES.md))
- [x] Relationship linking — the ontology's `Relationship`/
      `OntologyRelationshipType`
- [ ] Memory decay
- [x] Entity graph — the ontology `ConceptGraph`
- [ ] Graph visualization — the data exists (dashboard's `ontology` field
      per memory) but there's no visual graph view
- [x] Benchmarks — a runner, mem0/Haven adapters, an LLM judge, and a
      measured comparison run exist (`benchmarks/`), plus a dashboard-style
      UI for browsing results (`obsidian/server/benchmark_explorer.py`,
      surfaced as a tab in the Memory Dashboard — see
      [`benchmarks/results/final_report.md`](../../benchmarks/results/final_report.md))

## Post Hackathon

- [x] Knowledge graph — delivered early, as the ontology `ConceptGraph`
- [ ] Multi-user vaults
- [ ] Cross-device sync
- [ ] Memory evolution
- [ ] Automatic entity resolution
- [ ] Agent ecosystem
