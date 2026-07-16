# Memory Types

The Classifier assigns each extracted fact one of the values of
`obsidian.core.enums.MemoryType`. This is the authoritative list — it is
also what the server's `POST /memory` endpoint and the dashboard accept.

Every `MemoryType` also belongs to exactly one `MemoryDomain` (`Personal`,
`Work`, or `Knowledge`) — a fixed, deterministic grouping used by the
dashboard's Browse Memories view and the Classifier's own prompt (see
`obsidian/core/memory_domain.py`). Domain assignment carries no retrieval
or ranking meaning; it exists purely for presentation and to give the
Classifier a structured basis for choosing between similar types.

Since V2, each memory can also carry up to 3 canonicalized **topics** —
see [Topics](#topics) below.

## Personal

Durable signal about who the user is and what they want.

### Preference

A settled like/dislike or choice between options.

Examples

- Prefer local models

### Interest

Something the user is watching, following, or curious about, without
having adopted it as a settled preference or decision. Added in V2 to
stop these signals defaulting into Preference.

Examples

- The user is watching LlamaIndex.
- The user is interested in research papers on retrieval.

### Trait

An enduring characteristic or disposition — not a one-off preference.
Added in V2.

Examples

- The user likes building systems from scratch.

### Habit

A recurring behavior or routine. Added in V2.

### Skill

A skill the user possesses.

Examples

- Build Context Manager

### Goal

A future objective.

Examples

- Win the Qwen Hackathon

-----------------------------------

## Work

Project-state tracking: what's happening and what's next.

### Project

An ongoing project.

Examples

- Obsidian

### Task

A concrete action.

Examples

- Build Context Manager

### Decision

A choice made by the user, with the reasoning behind it.

Examples

- Build Manager AI first
- Use FastEmbed

See DECISION_MEMORY.md for the full field set (reason, alternatives
considered, status, supersedes/superseded by) and how superseding a
decision works.

### Question (`open_question`)

An explicitly unresolved question.

### Blocker

Something currently preventing progress on a project or task.

### Implementation State

What is built, stubbed, or in-progress for a project or component --
"done-ness," distinct from Task's "to-do."

### Code Areas (`code_area`)

A file or component relevant to a current focus.

-----------------------------------

## Knowledge

Durable knowledge the user holds or has recorded, independent of any one
project.

### Fact

An objective fact about the world or the user.

Examples

- Haven uses Claude for extraction.

### Belief

Something the user currently believes.

Examples

- Extraction is more important than retrieval.

### Person

Information about people.

### Event

A past or future event.

### Rule

A rule or guideline the user follows.

-----------------------------------

## Topics

Alongside `memory_type`, the Classifier may also tag a memory with up to
3 **topics** — short, free-form subject-matter labels, independent of
memory type (e.g. a `Preference` and an `Interest` can both be tagged
`AI`). Topics are:

- **LLM-generated**, not a fixed enum — the Classifier's prompt suggests
  a seed vocabulary (AI, Programming, Mechanical Engineering, Robotics,
  Fitness, Nutrition, Finance, Travel) as examples only; a genuinely new
  topic is still accepted.
- **Canonicalized** deterministically after generation
  (`obsidian/manager_ai/topic_canonicalizer.py`), so spelling variants
  and known synonyms ("machine learning", "ML", "artificial
  intelligence") collapse to one stored topic ("AI"). This keeps storage
  deterministic even though generation is LLM-driven.
- **Confidence-scored** — each topic carries an internal confidence
  (0.0–1.0), stored but not yet surfaced in the dashboard UI.
- **Informational only** — topics do not affect retrieval, ranking,
  acceptance, or benchmark scoring. They exist for dashboard filtering/
  display and for the "Why?" inspector's explanation of a memory's
  classification.

## Why no `Entity`/`Relationship` memory types

An earlier draft of the V2 ontology considered adding `Entity` and
`Relationship` as memory types (mirroring "Knowledge" ontologies
elsewhere). These were deliberately **not** added: `Entity` would
overlap heavily with the existing `Person`/`Fact` types (a named thing
worth remembering is already representable as one of those), and
`Relationship` would duplicate the ontology Concept graph's own
`OntologyRelationshipType` (`obsidian/ontology/enums.py`), which already
models relationships between Concepts as first-class graph edges — a
richer representation than a flat memory type could offer. Relationship
semantics stay in the ontology graph; `MemoryType` stays about
classifying individual memories.

## Note on Blocker / Implementation State / Code Area / Open Question

These four types were added to close a knowledge-representation gap
found by `obsidian/memory_engine/coverage_analyzer.py` (see
[`docs/architecture/PROJECT_STATE_KNOWLEDGE_MODEL.md`](../../docs/architecture/PROJECT_STATE_KNOWLEDGE_MODEL.md)
for the full design). As of their addition, the Classifier can assign them,
but nothing in the Extractor's prompt (`obsidian/manager_ai/extractor.py`)
asks for this kind of content yet, so in practice they won't be produced by
the existing write pipeline until that prompt changes -- a deliberately
separate, not-yet-done piece of work.

## Note

Earlier drafts of this doc also listed `Idea`, `Technology`, `Question`,
and `Resource` as memory types. Those aren't part of the implemented
`MemoryType` enum (`obsidian/core/enums.py`) — they were folder names in
the unused legacy `obsidian/vault/` scaffolding, which has since been
removed (see [KNOWN_ISSUES.md](KNOWN_ISSUES.md)). If they're wanted as
real categories, they'd need to be added to the enum and the Classifier's
prompt/schema.

## V2 ontology migration

See
[`docs/architecture/ONTOLOGY_V2_MIGRATION.md`](../../docs/architecture/ONTOLOGY_V2_MIGRATION.md)
for the full migration summary: what changed, backward compatibility,
UI changes, retrieval impact, and benchmark impact.
