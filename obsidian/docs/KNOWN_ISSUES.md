# Known Issues

Real gaps and rough edges in the current repository, as of this review.
Not a bug tracker — see [ROADMAP.md](ROADMAP.md) for planned work and
[TECH_DEBT.md](TECH_DEBT.md) for intentional shortcuts.

## Benchmark dataset coverage is partial

`benchmarks/` (repo root) has a real runner, a mem0 adapter, several Haven
adapter variants (full/retrieval/continuation/ablations), baseline
adapters, an LLM judge, and measured mem0-vs-Haven runs — see
[`benchmarks/results/final_report.md`](../../benchmarks/results/final_report.md)
and `benchmarks/README.md` for the current per-category breakdown. 5 of
19 dataset category folders are genuinely empty (`active_context`,
`insights`, `memory_recall`, `mistake_prevention`, `open_problems`); 288
cases currently execute across 11 categories (`concept_consolidation`,
`decision_reconstruction`, `refinements`, and an expanded `supersession`
set were added since this note was first written and are no longer
empty). The final report's own "Bugs Found" section also flags an
unresolved edge case: the keyword overlap denominator can collapse when
query terms are absent, inflating overlap scores ("Future improvement
planned," not yet done).

## No direct unit tests for several Manager AI stages

`Extractor`, `Classifier`, `ImportanceScorer`, `CanonicalMatcher`,
`ManagerPipeline`, and `BasePipelineStage` (all in `obsidian/manager_ai/`)
have no dedicated test file exercising them directly — found while
auditing test coverage during the pre-release cleanup pass. They are
exercised indirectly wherever `obsidian/demo.py` used to import them, but
that script has been removed as dead scaffolding (see below), so today
nothing in the test suite imports these five modules at all. Everything
downstream of them (`KnowledgeUpdater`, the full read pipeline, the
server) is well covered; this is specifically the LLM-facing extraction
stages.

## `ManagerPipeline` doesn't auto-drive `SUPERSEDE`

`KnowledgeUpdater` implements all four `KnowledgeDecision` operations.
`ManagerPipeline.match_and_apply()` now auto-drives `NEW`, `CONFIRM`, **and**
`UPDATE` — the latter via `CanonicalMatcher`'s conservative, deterministic
"whole-word prefix extension" rule, which refines a memory in place (id
preserved, `canonical_fact` overwritten) when a new fact literally elaborates
an existing one. Retrieval is validity-aware, so an archived memory
(`valid_until` in the past) is excluded from candidates.

`SUPERSEDE` is still **not** auto-driven: `CanonicalMatcher` never returns it,
and its `else` branch in `pipeline.py` remains a deliberate no-op. Detecting a
genuine *contradiction* (as opposed to a refinement) is a semantic judgement
this deterministic matcher intentionally does not make; reaching `SUPERSEDE`
today still requires calling `KnowledgeUpdater.supersede_decision()` directly,
which is what Decision Memory does. See `TECH_DEBT.md` for the remaining
Stage 3/4 work (contradiction detection + write-time supersession).

## `CONTINUATION` queries re-run full retrieval every time

`StructuredPromptBuilder`'s XML prompt (including `<ProjectState>` for
`CONTINUATION`-mode queries) is live via `POST /retrieve_working_context` —
see `ARCHITECTURE.md`. What's still missing is Step 2 of that integration: a
freshness check + bounded gap-fill fallback so a `CONTINUATION` query can
skip full retrieval when nothing has changed since the last one. Every such
query pays the complete `_allocate` pipeline cost today; see
[`docs/architecture/PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md`](../../docs/architecture/PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md)
§7 for the design. (`POST /retrieve_context` never renders `<ProjectState>`
or `<WorkingContext>` at all — it only ever calls the older flat
`ContextBuilder`, by design, not as a gap.)

## Only one conversation-export importer, and only ChatGPT/Obsidian sources

There is no per-provider conversation-export importer beyond ChatGPT
today, and ChatGPT itself is handled live (the browser extension posts
the captured exchange straight to `POST /memory`), not through a
file-import module — an earlier `obsidian/importers/` +
`obsidian/integrations/chatgpt/importer.py` file-export importer was
removed as orphaned dead code during the pre-release audit (nothing
called it, and it had a real classification bug: content-based
`_detect_source` misclassified a Gemini export as ChatGPT). The other
live import path is `obsidian/integrations/obsidian/importer.py`, which
bulk-imports an existing Obsidian vault's own notes.
`obsidian/integrations/claude/` and `obsidian/integrations/gemini/` are
still empty package stubs (`__init__.py` only). `ARCHITECTURE.md`'s "any
source (ChatGPT, Claude, Slack, etc.)" framing is aspirational for
everything but ChatGPT (live capture) and an Obsidian vault (bulk
import).

## Server is single-user, unauthenticated by design

By design (documented in `obsidian/server/README.md`), the app itself has no
auth layer — fine for a local hackathon demo running on `127.0.0.1`. The
[Alibaba Cloud deployment](../../deploy/alibaba-cloud/README.md) exposes it
beyond localhost, so auth was added — deliberately at the nginx reverse-proxy
layer (HTTP Basic Auth) rather than inside `obsidian/server/main.py`, keeping
this exact limitation true of the app code itself.

## Legacy/dead scaffolding (removed)

The following abandoned first-draft scaffolding was identified in an
earlier review and has since been deleted as part of the pre-release
cleanup pass (this note is kept only so the removal isn't mysterious to
someone diffing history):

- `obsidian/core/types.py`'s parallel `Memory`/`MemoryCandidate`/
  `MemoryUpdate`/`ExtractionReport`/`MemoryOperation`/`RelationshipCandidate`
  dataclasses and the unused `MemoryState`/`OperationType` enums they
  depended on. The system that's actually wired end-to-end uses
  `KnowledgeObject` (`obsidian/manager_ai/models.py`) instead, which
  tracks validity via `valid_from`/`valid_until` rather than a state
  machine, and the ontology's own `OntologyRelationshipType`
  (`obsidian/ontology/enums.py`) for graph edges. `core.types.Conversation`/
  `Event`/`Attachment` are kept — they're the real model
  `ConversationLoader` and `ChatGPTImporter` build and consume.
- `obsidian/core/interfaces.py` — a full ABC layer (`ManagerAI`,
  `MemoryEngine`, `Vault`, `ContextRetriever`, `PromptBuilder`,
  `ConversationImporter`) that nothing ever subclassed; the live pipeline
  stages and `obsidian.memory_engine.engine.MemoryEngine` were built
  independently of it.
- `obsidian/manager_ai/linker.py`, `supersession.py`, `state_manager.py`,
  and `writer.py`, plus `obsidian/core/models.py` — all empty (0 bytes)
  and unreferenced.
- `obsidian/app.py` — empty (0 bytes); the real entry point is
  `obsidian/server/main.py`.
- `obsidian/demo.py` and `obsidian/memory_engine/markdown_writer.py` — an
  earlier, superseded end-to-end demo script (the real one is
  `scripts/seed_demo.py`) and the writer it alone used; this script wrote
  to a bare `vault/` directory relative to the current working directory,
  which is how the stray `vault/Alice_works_at_Acme_Corp._00000000.md`
  ended up tracked at the repo root — both are now removed.
- `obsidian/core/value_objects.py`'s unused `MemoryIdentity`,
  `TemporalContext`, `MemoryMetadata`, and `Relationship` dataclasses, the
  `RelationshipType` enum they depended on (`obsidian/core/enums.py`), and
  `obsidian/core/validation.py` (its `validate_*` helpers had no callers
  outside the module itself). `value_objects.py`'s `Entity` is kept — it's
  the real model `core.types.Event.entities` uses.
- `obsidian/context_manager/` (empty directory), `obsidian/vault/` (empty
  `Beliefs/`, `Decisions/`, ... category folders), and
  `obsidian/templates/` (per-type Markdown templates, all 0 bytes) — the
  live `VaultWriter` writes flat Markdown files to a configurable vault
  directory (`haven_data/vault/` by default) rather than per-category
  folders, and no code ever read `obsidian/templates/`.
- `obsidian/frontend/` and `obsidian/test_data/` — empty packages with no
  other contents.
- Four 0-byte empty test stubs (`test_context.py`, `test_importance.py`,
  `test_manager.py`, `test_retrieval.py`) under `obsidian/tests/`.

## Deprecation warnings

`datetime.datetime.utcnow()` is used throughout the pipeline (ranker,
knowledge updater, ontology models) and is deprecated as of Python's
current `datetime` guidance — 1200+ `DeprecationWarning`s show up in a full
test run. Not a correctness bug today, but will need a pass to
timezone-aware `datetime.now(datetime.UTC)` before it's a hard error in a
future Python version.

## Documentation/implementation drift (fixed in this pass)

Two docs described enum values that didn't match the code and one described
a feature as unimplemented when it now is; corrected in this review —
flagged here so the discrepancy doesn't quietly reappear:

- `MEMORY_TYPES.md` listed categories (`Idea`, `Technology`, `Question`,
  `Resource`) that aren't in the actual `MemoryType` enum, and was missing
  four that are (`Fact`, `Event`, `Skill`, `Rule`).
- `RELATIONSHIPS.md` listed relationship types that don't match
  `OntologyRelationshipType`, the enum the ontology graph actually uses.
- `docs/architecture/ACCEPTANCE_STAGE_DESIGN.md` was marked "design only —
  not implemented"; `AcceptanceStage` has since been implemented and is
  live in the read pipeline.
