# Memory Types

The Classifier assigns each extracted fact one of the values of
`obsidian.core.enums.MemoryType`. This is the authoritative list — it is
also what the server's `POST /memory` endpoint and the dashboard accept.

Fact

An objective fact about the world or the user.

Examples

- Haven uses Claude for extraction.

-----------------------------------

Preference

Personal preference.

Examples

- Prefer local models

-----------------------------------

Belief

Something the user currently believes.

Examples

- Extraction is more important than retrieval.

-----------------------------------

Decision

A choice made by the user, with the reasoning behind it.

Examples

- Build Manager AI first
- Use FastEmbed

See DECISION_MEMORY.md for the full field set (reason, alternatives
considered, status, supersedes/superseded by) and how superseding a
decision works.

-----------------------------------

Goal

A future objective.

Examples

- Win the Qwen Hackathon

-----------------------------------

Project

An ongoing project.

Examples

- Obsidian

-----------------------------------

Person

Information about people.

-----------------------------------

Task

A concrete action.

Examples

- Build Context Manager

-----------------------------------

Event

A past or future event.

-----------------------------------

Skill

A skill the user possesses.

-----------------------------------

Rule

A rule or guideline the user follows.

-----------------------------------

Blocker

Something currently preventing progress on a project or task.

-----------------------------------

Implementation State

What is built, stubbed, or in-progress for a project or component --
"done-ness," distinct from Task's "to-do."

-----------------------------------

Code Area

A file or component relevant to a current focus.

-----------------------------------

Open Question

An explicitly unresolved question.

-----------------------------------

## Note on Blocker / Implementation State / Code Area / Open Question

These four types were added to close a knowledge-representation gap found
by `obsidian/memory_engine/coverage_analyzer.py` (see
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
