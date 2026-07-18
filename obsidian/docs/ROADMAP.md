# Roadmap

What's next, roughly in priority order. Sourced from
[PROGRESS.md](PROGRESS.md)'s "Remaining" section,
[KNOWN_ISSUES.md](KNOWN_ISSUES.md), [TECH_DEBT.md](TECH_DEBT.md), and the
"Future Work" section of
[`benchmarks/results/final_report.md`](../../benchmarks/results/final_report.md)
— this doc doesn't introduce anything not already tracked there.

## Next

1. **Wire `SUPERSEDE` into `ManagerPipeline`** so a real conversation can
   automatically contradict existing knowledge, not just via a direct
   `KnowledgeUpdater` call (`UPDATE` is already auto-driven via
   `CanonicalMatcher`'s prefix-extension rule; the final report's own
   "Current Limitations" calls the remaining `SUPERSEDE` gap out as
   "canonical supersession pipeline not yet integrated into production
   writes").
2. **Freshness check + bounded gap-fill fallback for `CONTINUATION` queries**
   (Step 2 of the `ProjectState`×`WorkingContext` integration), so a
   continuation query stops paying full retrieval cost on every call —
   `StructuredPromptBuilder`'s `<HavenContext>`/`<ProjectState>` prompt is
   already live via `POST /retrieve_working_context`, this step is purely a
   performance/staleness optimization on top of it. See
   [`docs/architecture/PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md`](../../docs/architecture/PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md)
   §7.
3. **Fill the 5 still-empty benchmark dataset categories** (`active_context`,
   `insights`, `memory_recall`, `mistake_prevention`, `open_problems`) and
   fix the keyword-overlap denominator edge case the final report flags.
   (`concept_consolidation` is no longer empty — 62 cases.)

## Nice to have

- Additional conversation importers (Claude, Gemini — ChatGPT is the only
  one implemented today).
- Memory decay / adaptive forgetting for confidence scores over time.
- Graph visualization for the concept graph (data already exists via
  `/dashboard/inspect/memory/{id}`'s `ontology` field; no visual graph view
  yet).
- Prompt versioning and caching (see [TECH_DEBT.md](TECH_DEBT.md)).
- Live dashboard updates (push instead of polling) and better semantic
  normalization (both called out in the final report's "Future Work").

## Post-hackathon

- Multi-user vaults / cross-device sync.
- Automatic entity resolution beyond the current deterministic alias
  matching.
- Automatic conversation remembering (no manual "Remember" click).
- Broader agent ecosystem integration.

## Explicitly out of scope

Carried over from [HACKATHON_SCOPE.md](HACKATHON_SCOPE.md) — not planned,
not started: automatic ontology restructuring, graph embeddings, community
detection, background workers, autonomous graph evolution, reinforcement
learning, graph compression.
