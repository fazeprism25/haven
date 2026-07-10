# Decision Memory

Decision Memory lets Haven remember **why** a decision was made — reason,
alternatives considered, and supersession lineage — not only **what** was
decided. It is fully deterministic: no stage of it calls an LLM.

## Fields

| Field | Where it lives |
|---|---|
| Decision | `KnowledgeObject.canonical_fact` (unchanged) |
| Confidence | `KnowledgeObject.confidence` (unchanged) |
| Created | `KnowledgeObject.valid_from` (unchanged) |
| Last Confirmed | `KnowledgeObject.last_confirmed` (unchanged) |
| Reason | `DecisionMetadata.reason` |
| Alternatives Considered | `DecisionMetadata.alternatives_considered` |
| Status | `DecisionMetadata.status` — `active` / `superseded` / `reversed` |
| Supersedes | `DecisionMetadata.supersedes` |
| Superseded By | `DecisionMetadata.superseded_by` |

`DecisionMetadata` and `DecisionStatus` live in `obsidian/manager_ai/models.py`.
`DecisionMetadata` is stored under `KnowledgeObject.metadata["decision"]` —
the `KnowledgeObject`'s existing free-form metadata dict — rather than as
new `KnowledgeObject` dataclass fields. This is the load-bearing design
choice of this feature:

* Every other memory type's `KnowledgeObject` shape is completely
  unaffected.
* Every decision written before this feature existed has no `"decision"`
  metadata key, so `get_decision_metadata(ko)` simply returns `None` for
  it — no migration, no backfill.
* `VaultWriter` and `MemoryStore` needed zero code changes: `metadata` was
  already dumped/parsed as a nested YAML dict, so `DecisionMetadata`
  round-trips through the vault for free.

## API

```python
from obsidian.manager_ai.models import (
    DecisionMetadata,
    DecisionStatus,
    get_decision_metadata,
    with_decision_metadata,
)

metadata = DecisionMetadata(
    reason="Qdrant's filtered search fits our metadata needs better.",
    alternatives_considered=["Chroma", "Pinecone"],
)
decision = with_decision_metadata(knowledge_object, metadata)  # new KnowledgeObject
get_decision_metadata(decision)  # -> metadata, or None if never attached
```

### Superseding a decision

```python
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater

archived_old, new_decision = KnowledgeUpdater.supersede_decision(
    fact=fact,
    knowledge=existing_decision,   # must be MemoryType.DECISION
    reason="Qdrant's filtered search fits our metadata needs better than Chroma's.",
    alternatives_considered=["Chroma", "Pinecone"],
)
vault_writer.write(archived_old)
vault_writer.write(new_decision)
```

`archived_old` keeps its id, gets `valid_until` set, and its
`DecisionMetadata.status` becomes `superseded` with `superseded_by`
pointing at `new_decision.id`. `new_decision` is a fresh `KnowledgeObject`
whose `DecisionMetadata.status` is `active` with `supersedes` pointing
back at the old id — the link is queryable from either object. Raises
`ValueError` if `knowledge.memory_type` is not `MemoryType.DECISION`.

`supersede_decision` reuses `KnowledgeUpdater._apply_supersede`'s existing
archive-and-recreate mechanics (the same ones every memory type's generic
`SUPERSEDE` decision already uses) and only layers `DecisionMetadata`
bookkeeping on top — it does not change that method or the generic
`metadata["supersedes"]` string it already sets.

## Where it surfaces

* **Dashboard** (`GET /api/v1/dashboard`) — every `DashboardMemory` carries
  an optional `decision` object (`reason`, `alternatives_considered`,
  `status`, `supersedes`, `superseded_by`) plus `last_confirmed`. The
  Dashboard UI shows a status badge on decision cards and a "Decision"
  section in the memory detail modal.
* **Retrieval / Memory Inspector**
  (`GET /api/v1/dashboard/inspect/memory/{id}`) — the same
  `DashboardMemory` payload backs the inspector modal, so opening any
  decision from the Dashboard shows its full Decision Memory fields.
* **Context Builder** (`obsidian/memory_engine/context_builder.py`) —
  appends `status`/`reason`/`alternatives_considered`/`supersedes`/
  `superseded_by` immediately under a `MemoryType.DECISION` candidate's
  normal six-field block, but only when `DecisionMetadata` is present.
  Every non-decision candidate, and every decision candidate with no
  `DecisionMetadata`, renders byte-identical to the format that existed
  before this feature — a pure addition, not a change to the existing
  six-field contract.
* **Structured Prompt Builder** (`obsidian/memory_engine/structured_prompt_builder.py`)
  — the same fields, rendered as extra `<Memory>` XML attributes
  (`status`, and — only when non-empty — `reason`, `alternatives_considered`,
  `supersedes`, `superseded_by`) inside a `WorkingContext`'s `<Decisions>`
  role bucket. Same additive rule as `ContextBuilder`: absent metadata or a
  non-decision type renders the `<Memory>` element exactly as before.

## Tests

* `obsidian/tests/test_decision_memory.py` — `DecisionMetadata`/
  `DecisionStatus`, the `get_decision_metadata`/`with_decision_metadata`
  helpers, vault round-tripping, backward compatibility, and
  `KnowledgeUpdater.supersede_decision`.
* `obsidian/tests/test_context_builder.py::TestDecisionFields` — rendering
  in the Context Builder.
* `obsidian/tests/test_structured_prompt_builder.py::TestDecisionFields` —
  rendering in the `<Decisions>` role bucket of the Structured Prompt
  Builder.
* `obsidian/tests/server/test_dashboard.py::TestDecisionMemory` — the
  Dashboard/Inspector API projection.
