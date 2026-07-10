NEW

↓

ACTIVE

↓

DORMANT

↓

ARCHIVED

↓

DELETED

Rules

NEW

Memory just extracted.

ACTIVE

Frequently used.

DORMANT

Rarely used.

ARCHIVED

Replaced by another memory.

DELETED

Removed permanently.

-----------------------------------

## Implementation note

This is the conceptual model. The implemented `KnowledgeObject` (the type
actually used by the write/read pipelines, the server, and the dashboard)
does not have a state machine — it tracks validity with
`valid_from`/`valid_until`/`last_confirmed` timestamps instead, and
"archived" simply means `valid_until is not None`. An earlier draft had a
`MemoryState` enum with these five values, attached to a separate `Memory`
dataclass — neither was read by the live pipeline, so both were removed
during the pre-release cleanup. See [KNOWN_ISSUES.md](KNOWN_ISSUES.md).