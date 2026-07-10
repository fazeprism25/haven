# Project-State Knowledge Model — Design

Status: **Design + low-risk representation-layer implementation.** No retrieval, ranking,
planning, gap-recovery, or prompt logic changed. Every claim about current code below was
checked against source on disk, not against `WORKING_CONTEXT_2_DESIGN.md`'s or
`CONTEXT_PLAN_OBJECT.md`'s descriptions, as of 2026-07-09.

This document answers one question left open by Phase 1 (`ContextPlanner`) and Phase 2
(`CoverageAnalyzer`): both are wired in and observational-only, but `CoverageReport` reports
`CONSTRAINT`, `BLOCKER`, `IMPLEMENTATION_STATE`, `CODE_AREA`, and `OPEN_QUESTION` as
permanently missing, because — per `coverage_analyzer.py`'s own docstring — "`MemoryType` has
no member that honestly corresponds to" them. The planner is asking for information the
storage layer cannot represent. This document audits whether that's actually true for all
five categories, decides the cleanest representation for the ones where it isn't, and
implements only that representation layer.

---

## 0. What already exists — the shape this must fit into

Verified directly against source:

- **`MemoryType`** (`obsidian/core/enums.py:75`) — 11 flat `str` values (`FACT, PREFERENCE,
  BELIEF, DECISION, GOAL, PROJECT, PERSON, TASK, EVENT, SKILL, RULE`). This is the
  Classifier's output vocabulary: `Classifier.build_prompt` (`classifier.py:149`) renders
  every member into the LLM prompt via `for t in MemoryType`, and `build_repair_prompt`
  (`classifier.py:177`) does the same. Adding a member automatically makes it choosable by
  the Classifier — no prompt-text edit required, but a real consequence worth naming (§8).
- **`MemoryRole`** (`obsidian/ontology/retrieval_models.py:1102`) — 7 values (`DECISION, GOAL,
  TASK, BELIEF, RESEARCH, OPEN_QUESTION, REFERENCE`), a *presentation* grouping distinct from
  `MemoryType`. Resolved by `resolve_role()` (`retrieval_models.py:1203`): first a
  `metadata["role"]` override if present and valid, else a fixed `_MEMORY_TYPE_ROLE` table
  (`retrieval_models.py:1185`) with a `MemoryRole.REFERENCE` fallback for any unmapped type.
  **`OPEN_QUESTION` already exists as a `MemoryRole` value with no `MemoryType` mapping to
  it** — reachable only via the `metadata["role"]` override — confirmed by
  `test_working_context_models.py::TestResolveRole::test_open_question_is_unreachable_without_override`.
  This override mechanism is the established, tested escape hatch for "a role with no natural
  type," already in production use.
- **`WorkingContextBuilder.build`** (`obsidian/memory_engine/working_context_builder.py:201`)
  iterates `for role in MemoryRole` to build **one bucket per role in every single
  `WorkingContext` it returns, always, even empty** ("total buckets" — see that module's own
  design-decisions note). This means **adding a new `MemoryRole` value changes the output
  shape of every `WorkingContext` produced by every query today** — a new, always-present
  empty bucket appears everywhere, even though nothing populates it. `context_planner.py`'s
  own module docstring (§"Why `ContextCategory` is its own enum, not `MemoryRole`",
  `context_planner.py:68-86`) already identified exactly this risk and chose to keep
  `ContextCategory` a separate, planning-only vocabulary rather than extend `MemoryRole` in
  Phase 1, specifically to avoid it. This document adopts the same constraint.
- **`ContextCategory`** (`obsidian/memory_engine/context_planner.py:202`) — a *third*,
  deliberately separate vocabulary (9 values: `DECISION, TASK, CONSTRAINT, BLOCKER, RESEARCH,
  OPEN_QUESTION, IMPLEMENTATION_STATE, CODE_AREA, BELIEF`), already complete — **`CONSTRAINT`,
  `BLOCKER`, `IMPLEMENTATION_STATE`, `CODE_AREA`, and `OPEN_QUESTION` all already exist as
  `ContextCategory` members.** Nothing needs to change here. The gap is entirely on the
  `MemoryType → ContextCategory` resolution side, not the category vocabulary itself.
- **`CoverageAnalyzer._MEMORY_TYPE_CATEGORY`** (`obsidian/memory_engine/coverage_analyzer.py:101`)
  is the *only* place that resolves a real candidate's `MemoryType` into a `ContextCategory`
  for coverage purposes. Unlike `resolve_role`, `_resolve_category`
  (`coverage_analyzer.py:110`) **does not consult any `metadata` override** — it is a pure
  `MemoryType`-keyed lookup with `None` for unmapped types (silently excluded from every
  category's count, `analyze_coverage`, `coverage_analyzer.py:371-373`).
- **`DecisionMetadata`** (`obsidian/manager_ai/models.py:270`, documented in full in
  `docs/DECISION_MEMORY.md`) is the established house pattern for "richer semantics for one
  `MemoryType`, without new `KnowledgeObject` dataclass fields": a frozen, optional dataclass
  stored under `KnowledgeObject.metadata["decision"]`, `None` when absent, zero `VaultWriter`/
  `MemoryStore` code changes needed because `metadata` already round-trips as a nested YAML
  dict (`docs/DECISION_MEMORY.md:32-35`). This is the precedent this document reuses for §7's
  deferred work, and explicitly does **not** reuse for the base representation in §3 — see
  that section for why.
- **`Extractor.build_prompt`** (`obsidian/manager_ai/extractor.py:260`) only asks the LLM for
  facts "worth remembering about the user" — identity, preferences, decisions, goals, adopted
  knowledge. It does not ask for blockers, implementation state, code areas, or open
  questions, and changing that prompt is explicitly out of scope for this task. **This means
  even after this document's changes, nothing in the write pipeline will actually produce a
  `BLOCKER`/`IMPLEMENTATION_STATE`/`CODE_AREA`/`OPEN_QUESTION`-classified `KnowledgeObject`
  today** — this document builds the vocabulary so a *future* write path (a later Extractor
  prompt revision, a manual entry, a different importer) has something honest to classify
  into. This is the explicit scope boundary requirement 3 of the task draws.

---

## 1. The actual gap — correcting a documented misconception

`coverage_analyzer.py`'s module docstring (lines 61-71) and `obsidian/docs/ARCHITECTURE.md`
(lines 176-181) both state that `MemoryType` has no member "honestly corresponding to"
`CONSTRAINT`, `BLOCKER`, `OPEN_QUESTION`, `IMPLEMENTATION_STATE`, or `CODE_AREA`. **This is
false for `CONSTRAINT`, and the code directly contradicts its own docstring**:

```python
# coverage_analyzer.py:101-107 — _MEMORY_TYPE_CATEGORY, as it exists today
_MEMORY_TYPE_CATEGORY: Dict[MemoryType, ContextCategory] = {
    MemoryType.DECISION: ContextCategory.DECISION,
    MemoryType.TASK: ContextCategory.TASK,
    MemoryType.RULE: ContextCategory.CONSTRAINT,   # <- this entry already exists
    MemoryType.FACT: ContextCategory.RESEARCH,
    MemoryType.BELIEF: ContextCategory.BELIEF,
}
```

`MemoryType.RULE` ("A rule or guideline the user follows") already maps to
`ContextCategory.CONSTRAINT`. A `RULE`-classified `KnowledgeObject`, if one existed with an
accepted candidate, would already be counted toward a `CONSTRAINT` requirement today — the
docstring's claim is stale, not the behavior. It is untested (no test in
`test_coverage_analyzer.py` exercises `MemoryType.RULE`), which is likely why the
inconsistency went unnoticed. §6 fixes the docstrings; no code change is needed for
`CONSTRAINT` at the `MemoryType`/category-resolution layer.

What is a real, separate gap for `CONSTRAINT`: `_MEMORY_TYPE_ROLE`
(`retrieval_models.py:1190`) maps `MemoryType.RULE → MemoryRole.BELIEF`, not a distinct
constraint role — so even though coverage counts it correctly, a `WorkingContext` bundles
rules in with general beliefs rather than a durable, never-dropped bucket.
`WORKING_CONTEXT_2_DESIGN.md` §5/§7 already flagged this and proposed a new
`MemoryRole.CONSTRAINT`. Per §0's `WorkingContextBuilder` finding, that is a `MemoryRole`
addition — out of scope for the low-risk bar this document targets (§8) — and is called out
as deferred work in §7, not implemented here.

**The four categories genuinely unrepresentable today — no `MemoryType`, no coverage
resolution, no natural role**: `BLOCKER`, `IMPLEMENTATION_STATE`, `CODE_AREA`,
`OPEN_QUESTION`. These are this document's actual scope.

---

## 2. Should these become new `MemoryType`s, new `MemoryRole`s, metadata, or something else?

Evaluated against the four real gaps from §1, one mechanism at a time:

**New `MemoryRole` values (rejected).** Per §0's `WorkingContextBuilder` finding, this
changes the bucket shape of every `WorkingContext` returned by every query today, which
directly contradicts "retrieval behaviour is intentionally unchanged." `context_planner.py`
already made this call for `ContextCategory` vs. `MemoryRole` in Phase 1; this document keeps
that boundary rather than re-opening it.

**A parallel vocabulary, reusing `ContextCategory` itself as the classification target
(rejected).** `ContextCategory` is documented as planning-only ("mechanics-agnostic ...
this is a deliberately separate, planning-only vocabulary for this phase,"
`context_planner.py:82-86`) and lives in `memory_engine`, a layer `core`/`manager_ai` must
not depend on (`core` has no dependency on `memory_engine` anywhere in this codebase, and
introducing one would invert the existing one-directional dependency `retrieval_models.py`'s
own comments describe, lines 682-691). Making the Classifier assign a `ContextCategory`
directly would require `manager_ai` to import from `memory_engine`, a new and unwanted
dependency edge.

**Metadata-only, no new `MemoryType` (rejected as the *sole* mechanism).** The
`DecisionMetadata` precedent (`metadata["decision"]`) works because it adds *extra detail
about* an object whose primary classification (`MemoryType.DECISION`) already exists and
already correctly categorizes it. `BLOCKER`/`IMPLEMENTATION_STATE`/`CODE_AREA`/
`OPEN_QUESTION` are different in kind: **the classification itself is missing**, not detail
on top of an existing one. Today the Classifier has nowhere honest to put "the build is
blocked by X" — it must force it into `FACT` or `TASK`, which `_MEMORY_TYPE_CATEGORY` then
resolves to `RESEARCH` or `TASK`, silently losing the category the fact actually belongs to.
Metadata can enrich a classification; it cannot substitute for one that doesn't exist. (An
override-only design, mirroring `resolve_role`'s `metadata["role"]` path, was also
considered and is discussed as future work in §7 — it doesn't fix the *default* path, which
is what `_resolve_category` uses, and every fact would need the override attached by hand.)

**New `MemoryType` values (recommended).** These four categories sit at exactly the same
grain as existing `MemoryType` members — "what *kind* of atomic statement is this," the
question `MemoryType` exists to answer (compare: `FACT` vs. `BELIEF` vs. `TASK` are already
this fine-grained). Concretely:

| New `MemoryType` | Matches `ContextCategory` | Rationale |
|---|---|---|
| `BLOCKER` | `BLOCKER` | "Something currently preventing progress" — distinct from `TASK` (an action to take) and `FACT` (neutral); today forced into one of those, losing the "this is blocking something" signal entirely. |
| `IMPLEMENTATION_STATE` | `IMPLEMENTATION_STATE` | "What is built, stubbed, or in-progress" — `WORKING_CONTEXT_2_DESIGN.md` §2 explicitly distinguishes this from `TASK` ("this is 'done-ness,' not 'to-do'"); no existing type captures it. |
| `CODE_AREA` | `CODE_AREA` | "A file or component relevant to the current focus" — closest existing thing is `EntityType.FILE` (`core/enums.py:165`), but `EntityType` classifies named entities *inside* memory content for the ontology layer (`core/value_objects.py`, used by `Entity`), not a `KnowledgeObject`'s own `memory_type`; reusing it would conflate two different classification axes. |
| `OPEN_QUESTION` | `OPEN_QUESTION` | Already a `MemoryRole` with no natural `MemoryType` source — today only reachable by hand-attaching `metadata["role"]="open_question"` to an otherwise-arbitrarily-typed object. A direct type closes that gap for the common case while leaving the override path intact for the rare "this `DECISION` also reads as an open question" case. |

No new `MemoryType.CONSTRAINT` is added — §1 already showed `RULE` fills that slot; adding a
second, overlapping type would be the "assume adding a new enum is automatically correct"
mistake this task explicitly warns against.

---

## 3. The recommended representation, precisely

**Additive changes only, three files, no new modules** (per this codebase's own minimal-diff
convention — `DecisionMetadata` lives in the existing `manager_ai/models.py`, not a new
file; this follows the same rule):

1. **`obsidian/core/enums.py`** — four new `MemoryType` members: `BLOCKER`,
   `IMPLEMENTATION_STATE`, `CODE_AREA`, `OPEN_QUESTION`. Existing 11 members, their values,
   and their order are untouched.
2. **`obsidian/ontology/retrieval_models.py`** — four new entries in `_MEMORY_TYPE_ROLE`,
   mapping each new type to an **existing** `MemoryRole` (no new role values, so
   `WorkingContextBuilder`'s bucket set is unchanged for every context, exactly as today):
   - `BLOCKER → MemoryRole.TASK` (a blocker demands resolution — the closest existing
     actionable role; `WORKING_CONTEXT_2_DESIGN.md`'s own framing, "something currently
     preventing progress," is inherently something to act on).
   - `IMPLEMENTATION_STATE → MemoryRole.REFERENCE` (status/background information, not itself
     actionable or a decision).
   - `CODE_AREA → MemoryRole.REFERENCE` (matches `REFERENCE`'s own definition verbatim:
     "Background reference material that does not fit another role").
   - `OPEN_QUESTION → MemoryRole.OPEN_QUESTION` (direct, exact match — the one already-named
     role this type exists to feed).
3. **`obsidian/memory_engine/coverage_analyzer.py`** — four new entries in
   `_MEMORY_TYPE_CATEGORY`, each mapping the new type to the identically-named
   `ContextCategory` it was designed for (`BLOCKER→BLOCKER`, `IMPLEMENTATION_STATE→
   IMPLEMENTATION_STATE`, `CODE_AREA→CODE_AREA`, `OPEN_QUESTION→OPEN_QUESTION`).

No change to `KnowledgeObject`, `EvidenceEntry`, `Candidate`, `RankedCandidate`,
`CandidateTrace`, `WorkingContext`, `RoleBucket`, `ContextPlan`, `CoverageReport`, or any
`to_dict`/`from_dict` method — every one of those already handles an arbitrary `MemoryType`
value generically (`MemoryType(data["memory_type"])`), so four new enum members round-trip
through every existing serializer with zero code changes, the same property
`docs/DECISION_MEMORY.md:32-35` describes for `DecisionMetadata`.

---

## 4. Backward compatibility and migration

- **Old vaults load unchanged.** `KnowledgeObject.from_dict` (`models.py:198`) does
  `MemoryType(data["memory_type"])` — a plain enum lookup by value string. Every persisted
  `memory_type` value from before this change (`"fact"`, `"decision"`, ... `"rule"`) is still
  a valid `MemoryType` member; nothing about how those eleven original strings resolve
  changes. No vault file needs rewriting, no migration script is needed.
- **Old `RetrievalTrace`/`CandidateTrace`/`WorkingContext` JSON deserializes unchanged.**
  Every `from_dict` in `retrieval_models.py` reconstructs `MemoryType` the same way; a
  persisted trace referencing only the original 11 values parses exactly as before.
- **`resolve_role` stays total.** Adding four keys to `_MEMORY_TYPE_ROLE` cannot make any
  *existing* `MemoryType` unresolvable — the four new keys are additions, not edits to the
  seven pre-existing entries — and even an accidentally-omitted new key would still resolve
  safely via the existing `MemoryRole.REFERENCE` fallback (`retrieval_models.py:1225`), never
  a `KeyError`.
- **One pre-existing test's premise intentionally changes and must be updated.**
  `test_working_context_models.py::TestResolveRole::test_open_question_is_unreachable_without_override`
  currently asserts no `MemoryType` maps to `MemoryRole.OPEN_QUESTION`. This document
  deliberately makes that no longer true for the *new* `MemoryType.OPEN_QUESTION` — every
  pre-existing `MemoryType`'s resolution is unaffected. §5 replaces this test with one that
  states the new, correct invariant directly.
- **The Classifier can now emit these four values for real conversations**, since
  `build_prompt`/`build_repair_prompt` iterate `for t in MemoryType` (§0). Given the
  Extractor's prompt is unchanged (§0's last bullet — out of scope here), this is expected to
  be rare in practice today (nothing asks for blocker/implementation-state/code-area/
  open-question-shaped facts), but it is a real, honest possibility, not a hidden one — see
  §8's risk discussion.

---

## 5. Testing

Added to `obsidian/tests/test_working_context_models.py` (extending the existing
`TestResolveRole` class, following its own structure) and `obsidian/tests/test_coverage_analyzer.py`:

- **`MemoryType` completeness** — `resolve_role` and `_resolve_category` both stay total
  (every member of `MemoryType`, old and new, resolves without a `KeyError`/exception).
- **New representative mappings** — `resolve_role(BLOCKER) is MemoryRole.TASK`,
  `resolve_role(IMPLEMENTATION_STATE) is MemoryRole.REFERENCE`,
  `resolve_role(CODE_AREA) is MemoryRole.REFERENCE`,
  `resolve_role(OPEN_QUESTION) is MemoryRole.OPEN_QUESTION`.
- **Corrected invariant** —
  `MemoryType.OPEN_QUESTION` now reaches `MemoryRole.OPEN_QUESTION` directly (replaces the
  now-stale "unreachable without override" test); the `metadata["role"]` override still wins
  over the new default, and still works for every other type, unchanged.
- **`analyze_coverage` closes the gap** — a `CandidateTrace` with `memory_type=BLOCKER` (etc.
  for the other three) that is `accepted=True` now contributes to that `ContextCategory`'s
  `retrieved_count`, and a `ContextPlan` requirement for that category can now reach
  `CoverageStatus.FULL`/`PARTIAL`, not only `MISSING` — verified by constructing a plan +
  candidate list per new type and asserting `CoverageStatus` is no longer forced to
  `MISSING` regardless of input, which is what today's partial table guarantees.
  `RULE → CONSTRAINT` also gets its first test coverage (§1's finding), asserting the
  pre-existing but previously-untested mapping.
- **Round-trip / backward compatibility** — `KnowledgeObject.to_dict()`/`from_dict()`,
  `CandidateTrace.to_dict()`/`from_dict()`, and `RetrievalTrace.to_dict()`/`from_dict()` for
  each new `MemoryType`, plus a fixture reproducing a *pre-change* serialized payload (only
  the original 11 `memory_type` values) to confirm it still deserializes correctly —
  determinism and non-regression, not new behavior for old data.
- **Classifier repair-prompt coverage** — `test_repair_prompt_lists_every_valid_memory_type`
  (`tests/server/test_classifier_invalid_memory_type.py:112`) already iterates `for
  memory_type in MemoryType` generically; it requires no edit and will automatically cover
  the four new values, which is exercised as a passive regression check, not a new test.

---

## 6. Documentation corrections (in scope — these are the false claims from §1)

- `obsidian/memory_engine/coverage_analyzer.py` module docstring (lines 54-71): correct "why
  category resolution is partial" to state that `CONSTRAINT` *is* represented today (via
  `MemoryType.RULE`, previously untested), and that `BLOCKER`/`OPEN_QUESTION`/
  `IMPLEMENTATION_STATE`/`CODE_AREA` now also have direct entries following this change —
  update `_MEMORY_TYPE_CATEGORY`'s own comment (lines 95-100) once the four new entries land.
- `obsidian/docs/ARCHITECTURE.md` (lines 176-181, "Coverage Analysis" section): same
  correction — remove the now-false "have no corresponding `MemoryType` today" claim,
  describe the four newly-closed categories and `RULE`'s pre-existing but newly-tested
  `CONSTRAINT` mapping.
- `obsidian/docs/MEMORY_TYPES.md`: add the four new types in the same format as the existing
  eleven (name, one-line description, example), and a note (matching the doc's existing
  "Note" section style) that the write pipeline does not populate them yet.

---

## 7. What this deliberately does *not* implement (out of scope, per the task's own limits)

- **No structured per-type metadata** (e.g. an `ImplementationStatus` enum for
  `IMPLEMENTATION_STATE` mirroring `DecisionStatus`, a `file_paths: List[str]` field for
  `CODE_AREA`, a "blocking what" reference for `BLOCKER`). The `DecisionMetadata` pattern
  (§0) is the natural mechanism if and when a concrete consumer needs it — the same optional,
  `metadata["<key>"]`-keyed, zero-migration shape — but adding it speculatively for four
  types with no consumer yet is exactly the premature-abstraction this codebase's own
  `DecisionMetadata` comment warns against implicitly (it was added when Decision Memory had
  a concrete rendering consumer, not ahead of one). Flagged here as the recommended next step
  once retrieval/rendering work (out of scope) actually needs it.
- **No `MemoryRole.CONSTRAINT`** (or any new `MemoryRole` value) — §2/§0 already explained why:
  it changes every `WorkingContext`'s bucket shape today, which this document's low-risk bar
  excludes. `RULE` keeps resolving to `MemoryRole.BELIEF`.
- **No `metadata`-override parity for `_resolve_category`** — `resolve_role` supports a
  `metadata["role"]` override; `_resolve_category` does not and this document does not add
  one. Adding it would let any `KnowledgeObject` declare an ad hoc `ContextCategory`
  regardless of `memory_type`, which is a real future improvement but changes
  `CoverageAnalyzer`'s resolution *semantics* (a second resolution path, not just new table
  entries) rather than only closing the type-vocabulary gap this document targets.
- **No Extractor/Classifier prompt changes.** Per §0, nothing in the write pipeline asks for
  blocker/implementation-state/code-area/open-question content today. This document makes
  the vocabulary available; it does not make the pipeline populate it. Explicitly excluded by
  the task.
- **No retrieval, ranking, `ContextPlanner`, `CoverageAnalyzer`-logic, or gap-recovery
  changes.** `analyze_coverage`'s comparison logic, `ContextPlanner`'s classification tables,
  `AcceptanceStage`, `DeterministicRanker`, and `WorkingContextBuilder`'s grouping algorithm
  are all untouched — only the `MemoryType → {MemoryRole, ContextCategory}` *data tables* two
  of them already read from gain four rows each.
- **No benchmark changes.** Per the task, and because nothing in the write pipeline populates
  these types yet (making a benchmark exercise them would require synthetic-only fixtures
  disconnected from real extraction, which is not a meaningful benchmark).

---

## 8. Risks

- **The Classifier can now legitimately assign these four types to real facts** (§4's last
  point). Since the Extractor's own prompt doesn't solicit this content, the practical
  exposure is low — but if a conversation happens to contain a sentence an existing prompt
  rule already causes the Extractor to surface (e.g. "durable knowledge the user has
  explicitly adopted," `extractor.py:332-335`, could incidentally read as implementation
  state or a blocker), the Classifier may now pick one of the four new types where it
  previously had to force a `FACT`/`TASK`/`BELIEF`. This is a *more honest* classification,
  not a regression, but it does mean write-time behavior for edge-case facts can change
  without any prompt edit — worth a one-line callout in `MEMORY_TYPES.md` (§6) rather than a
  silent side effect.
- **`_MEMORY_TYPE_ROLE`'s new entries are judgment calls, not derived facts** — particularly
  `BLOCKER → TASK`. If a later phase adds `MemoryRole.CONSTRAINT`/`BLOCKER` (deferred, §7),
  this table's `BLOCKER` entry should move to the new role; today's choice is the best
  available fit among the *existing* seven roles, explicitly not a permanent decision.
- **Reusing `ContextCategory` value names for the new `MemoryType` values** (`BLOCKER` is
  both a `MemoryType` and a `ContextCategory` member) is intentional clarity, not a coupling —
  they remain two independent enums in two independent modules (`core.enums` has no
  dependency on `memory_engine.context_planner`, confirmed by grep); the name match only
  makes `_MEMORY_TYPE_CATEGORY`'s new rows self-explanatory.

---

## 9. Report

**Designed** (this document): the full audit in §0-§2, including the corrected
`CONSTRAINT`/`RULE` finding (§1) that changes the scope from five categories to four; the
decision to use four new `MemoryType` members mapped through existing `MemoryRole`/
`ContextCategory` tables rather than new `MemoryRole` values, a metadata-only mechanism, or a
`ContextCategory`-as-classification-target design; the backward-compatibility argument (§4);
and the explicitly deferred extensions (§7).

**Implemented** (this pass, representation layer only):
- Four new `MemoryType` members: `BLOCKER`, `IMPLEMENTATION_STATE`, `CODE_AREA`,
  `OPEN_QUESTION` (`obsidian/core/enums.py`).
- Four new `_MEMORY_TYPE_ROLE` entries (`obsidian/ontology/retrieval_models.py`), reusing
  existing `MemoryRole` values only.
- Four new `_MEMORY_TYPE_CATEGORY` entries (`obsidian/memory_engine/coverage_analyzer.py`),
  closing the `CoverageAnalyzer` gap for these four categories.
- Stale-docstring corrections in `coverage_analyzer.py` and `obsidian/docs/ARCHITECTURE.md`
  (the false "`CONSTRAINT` has no `MemoryType`" claim), plus new entries in
  `obsidian/docs/MEMORY_TYPES.md`.
- Tests: `resolve_role`/`_resolve_category` totality and new mappings, the corrected
  `OPEN_QUESTION` reachability invariant, `analyze_coverage` no longer forcing `MISSING` for
  the four categories, the previously-untested `RULE → CONSTRAINT` mapping, and
  serialization/backward-compatibility round trips for old and new data.

**Deferred** (explicitly, per §7): structured per-type metadata (`ImplementationStatus`,
code-area file paths, blocker targets); `MemoryRole.CONSTRAINT`/`BLOCKER`/other new role
values and the `WorkingContextBuilder` bucket changes they'd require; `metadata`-override
parity for `_resolve_category`; any Extractor/Classifier prompt change to actually populate
these types from real conversations; and all retrieval/ranking/planner/gap-recovery wiring
that would make `ContextPlanner`/`CoverageAnalyzer` stop being purely observational.

**Explicitly not attempted**: making Haven's retrieval or benchmark scores better. Per the
task, this is a knowledge-model capability change only — `CoverageReport` can now say "this
category has content" for four more kinds of state, but nothing downstream reads that signal
differently than it did before this change.
