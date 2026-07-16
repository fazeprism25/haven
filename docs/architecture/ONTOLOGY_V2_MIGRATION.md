# Ontology V2 Migration

Summary of the memory ontology redesign: richer fixed types, a `Domain`
grouping dimension, and an LLM-generated, canonicalized `Topic` tagging
dimension. Written after implementation and verification (full test suite
green, manual end-to-end smoke checks against the real API).

## Ontology changes

**Type (fixed, deterministic, additive-only).** `MemoryType`
(`obsidian/core/enums.py`) gained 3 new members — `INTEREST`, `TRAIT`,
`HABIT` — closing a gap where the Classifier had no way to distinguish
"the user is watching LlamaIndex" (a passing interest) or "the user likes
building systems" (an enduring trait) from a settled `PREFERENCE`, so it
defaulted all of them into `PREFERENCE`. All 15 pre-existing members are
unchanged in name and meaning.

**Domain (new grouping dimension).** A new `MemoryDomain` enum
(`PERSONAL`/`WORK`/`KNOWLEDGE`) plus a fixed, total mapping
(`obsidian/core/memory_domain.py`) buckets all 18 `MemoryType` members:

```
Personal:  Preference, Interest, Trait, Habit, Skill, Goal
Work:      Project, Task, Decision, Question (open_question), Blocker,
           Implementation State, Code Areas
Knowledge: Fact, Belief, Person, Event, Rule
```

`Entity`/`Relationship` were deliberately **not** added as memory types —
they would overlap with `Person`/`Fact` and duplicate the ontology
Concept graph's own `OntologyRelationshipType`, which already models
relationships as first-class graph edges. See
`obsidian/docs/MEMORY_TYPES.md`'s "Why no Entity/Relationship memory
types" section.

**Topic (new, LLM-generated, canonicalized dimension).** Each memory can
carry up to 3 `TopicTag` values (`obsidian/core/value_objects.py`), each
with a `name` and an internal `confidence` (not yet surfaced in the UI).
Topics are LLM-generated free text, not a fixed enum, but are
deterministically canonicalized after generation
(`obsidian/manager_ai/topic_canonicalizer.py`): known synonyms collapse
to one seed vocabulary name (e.g. "machine learning"/"ML" → "AI"), novel
topics are accepted Title-Cased, duplicates merge keeping the higher
confidence, and the result is capped at 3, sorted by descending
confidence with an alphabetical tie-break for determinism.

**Classifier.** `obsidian/manager_ai/classifier.py`'s prompt now lists
`MemoryType` grouped by domain with short disambiguation guidance
(Preference vs. Interest vs. Trait vs. Habit vs. Skill vs. Goal) instead
of a flat list, and asks for an optional `topics` key. Validation/repair
still gate strictly on `memory_type` (topics have no fixed vocabulary to
violate — only structure is validated).

## Backward compatibility

Zero data migration was needed. Because the change is purely additive at
the enum level (no rename, no value repurposing), every pre-V2
`memory_type` string already parses as a valid enum member. `topics` is a
new, optional field: `KnowledgeObject.from_dict`/`ClassificationResult.from_dict`
default it to `()` when absent, and `VaultWriter` only writes the
`topics:` frontmatter key when non-empty — so pre-V2 vault files are
byte-for-byte unaffected and load identically (verified manually against
a hand-written old-format frontmatter file with no `topics` key, and
covered by `obsidian/tests/test_vault_writer.py::TestTopicsFrontmatter`).

## UI changes

The dashboard's Browse Memories view replaced its hardcoded 5-tab split
(Projects/Decisions/Beliefs/Preferences/Tasks — covering only 5 of the
old 15 types) with 3 domain tabs (Personal/Work/Knowledge), each showing
grouped sub-sections per memory type in the domain's canonical order.
This surfaces all 18 types, not just 5. The backend `DashboardResponse`
API changed shape accordingly (`domains: List[DomainSection]` replacing
the 5 fixed fields) — a breaking but first-party-only change, since
`dashboard.html` is this API's only consumer.

The "Why?" inspector (`GET /api/v1/dashboard/inspect/memory/{id}`) now
also returns `classification_reason`, `domain`, and `topics` (name +
confidence), surfaced in the memory detail modal as a new "Why this type
& topics" section — closing the gap where the Classifier's own reasoning
existed (`ClassificationResult.reason`) but was never wired into any API
response.

## Retrieval impact

None by design (confirmed scope: topics are informational only).
`coverage_analyzer.MEMORY_TYPE_CATEGORY` and `category_preference.py` are
untouched — the 3 new types simply join the existing list of types with
no category mapping (same as `GOAL`/`PROJECT`/`PERSON`/`EVENT`/`SKILL`/
`PREFERENCE` already were). `retrieval_models._MEMORY_TYPE_ROLE` gained
explicit entries for the 3 new types (all → `REFERENCE`, matching how
`PREFERENCE`/`SKILL`/`PERSON`/`EVENT`/`PROJECT` already resolve) purely
to keep that table's "total mapping" docstring claim honest — `resolve_role`
already had a safe fallback, so this changes no runtime behavior. No
ranker, acceptance-stage, or slot-allocation code was touched.

## Benchmark impact

None. No benchmark fixture encodes a `memory_type` value that changed
(the continuation benchmark's `TURN_TYPE_TO_MEMORY_TYPE` table is
additive-compatible; the plain retrieval benchmark's `HavenAdapter`
always writes `MemoryType.FACT` regardless). Per the approved plan, no
benchmark artifacts were regenerated and `benchmarks/runners/run_benchmarks.py`
was not run — verification was the automated test suite only:
`obsidian/tests` (2583 passed) and `benchmarks/tests` (240 passed), both
zero regressions.

One pre-existing test file, `obsidian/tests/server/test_classifier_invalid_memory_type.py`
(plus one test in `test_llm_transport_retry.py`), used the string
`"interest"` as its example of a plausible-but-invented `memory_type` to
verify the Classifier's repair-retry-then-skip behavior. Since `interest`
is now a real type, these tests were updated to use `"hobby"` instead —
the contract they pin (any invented category triggers exactly one repair
retry, then a typed `ClassificationError`) is unchanged; only the
placeholder string changed.
