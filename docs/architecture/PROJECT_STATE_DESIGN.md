# ProjectState — Architecture Design

Status: **Phase A implemented (2026-07-09); Phases B–E remain architecture only.**
See "Phase A implementation note" immediately below for exactly what shipped and how it
differs from this document's own Phase A description in §10. Every claim about Haven
behavior *prior to* the Phase A implementation was checked against source on disk as of
2026-07-09 — the four prerequisite documents (`WORKING_CONTEXT_2_DESIGN.md`,
`CONTEXT_PLANNER_DESIGN.md`, `CONTEXT_PLAN_OBJECT.md`, `PROJECT_STATE_KNOWLEDGE_MODEL.md`)
plus a direct read of `engine.py`, `working_context_builder.py`, `context_builder.py`,
`retrieval_models.py`, `context_planner.py`, `coverage_analyzer.py`, `gap_recovery.py`,
`category_preference.py`, `pipeline.py`, `knowledge_updater.py`, and `canonical_matcher.py`.
`FINAL_ENGINEERING_ROADMAP.md` does not exist in this repo.

## Phase A implementation note

Phase A is implemented in `obsidian/memory_engine/project_state.py`
(`ProjectState`, `StateRef`, `ProjectStateField`, `FieldDerivation`,
`ProjectStateBuilder`), projected to a plain-primitive `ProjectStateTrace` in
`obsidian/ontology/retrieval_models.py`, and wired into
`MemoryEngine.query_with_trace()` (`RetrievalTrace.project_state`) in
`obsidian/memory_engine/engine.py`. It is **narrower than §10's own Phase A
description below**, in one deliberate way: rather than "a one-shot full-vault
aggregation function," the shipped `ProjectStateBuilder.build()` derives a
`ProjectState` entirely from **one query's already-allocated candidates** —
the exact same list `ContextBuilder` renders into that call's context string.
There is no vault scan, no `MemoryStore` query, and no cross-query
aggregation; `MemoryEngine.get_project_state()` as a standalone method was
not added — `ProjectState` is only reachable via `RetrievalTrace.project_state`
today. This makes `gaps` a strictly weaker signal than the rest of this
document assumes: it means "absent from *this run's* reconstruction," not
"absent from the vault" (see §8's `STATE_DRIFT` discussion, which does not
apply — there is no persisted prior state for anything to drift from).

The shipped field set is also narrower than §2's full code sample:
`project_key`, `version`, `identity`, `phase`, `rejected_approaches`,
`do_not_do`, `recent_discoveries`, and `last_incorporated_event_id` are all
omitted rather than included as permanently-empty or placeholder fields —
`identity`/`phase` because they are `INFERRED`-only (Phase D, not built),
`version`/`last_incorporated_event_id` because they are meaningless without
persistence (Phase B, not built), and `rejected_approaches`/`do_not_do`/
`recent_discoveries` because they were outside the implementation task's
explicit field list. `current_objective` is implemented, but only its
unambiguous deterministic case per §3's own table ("the *default* is still
deterministic (highest-scoring GOAL)") — ties among multiple accepted goals
are never LLM-resolved; the top-ranked one always wins. Everything else in
§2–§4 (persistence, the `ManagerPipeline` write-time hook, `WorkingContext`
wiring, inferred fields) remains exactly as designed below: unimplemented.
See `obsidian/memory_engine/project_state.py`'s own module docstring for the
complete, authoritative list of what Phase A does and does not include, and
`obsidian/docs/ARCHITECTURE.md`'s "Project State (Phase A)" section for how
it's wired into the read pipeline.

---

## 0. What already exists — grounding, and one correction to a prior doc

The four prerequisite documents already built real, wired-in machinery. This is not a
greenfield problem:

- **`ContextPlanner`** (`obsidian/memory_engine/context_planner.py`) classifies a query into
  one of five `TaskMode`s via a fixed lexical pattern table and produces a `ContextPlan` —
  `requirements: Tuple[CategoryRequirement, ...]` over a 9-value `ContextCategory` enum
  (`DECISION, TASK, CONSTRAINT, BLOCKER, RESEARCH, OPEN_QUESTION, IMPLEMENTATION_STATE,
  CODE_AREA, BELIEF`). Deterministic only; `PlanningMethod.LLM_FALLBACK` is defined but never
  produced.
- **`CoverageAnalyzer`** (`coverage_analyzer.py`) compares a plan's requirements against what
  a single retrieval run actually accepted, via a `MemoryType → ContextCategory` table
  (`MEMORY_TYPE_CATEGORY`, now populated for `DECISION, TASK, RULE→CONSTRAINT, FACT→RESEARCH,
  BELIEF, BLOCKER, IMPLEMENTATION_STATE, CODE_AREA, OPEN_QUESTION`). `GOAL, PROJECT, PERSON,
  EVENT, SKILL, PREFERENCE` have no category mapping — a real, documented gap, not an
  oversight.
- **`CategoryPreferenceScorer`** (`category_preference.py`, Phase 3) is the *only* one of the
  four that changes what a query returns: a small (`0.05`), bounded, additive score bonus for
  any candidate whose category the plan requested, applied before `AcceptanceStage`.
- **`GapRecoveryDecision`** (`gap_recovery.py`, Phase 4) decides *whether* another retrieval
  pass would be warranted from `ContextPlan.confidence` and `CoverageReport.missing_required_categories`
  alone — but issues no retry. Every one of these four stages runs inside
  `MemoryEngine.query_with_trace` (`engine.py:663-810`), **from scratch, on every call**, over
  whatever `HybridCandidateRetriever` returns for that one query.
- **`PROJECT_STATE_KNOWLEDGE_MODEL.md`'s representation-layer work is real and merged**: four
  new `MemoryType` values (`BLOCKER, IMPLEMENTATION_STATE, CODE_AREA, OPEN_QUESTION`,
  `core/enums.py`) exist, map through `_MEMORY_TYPE_ROLE` (`retrieval_models.py:1362-1365`)
  and `MEMORY_TYPE_CATEGORY`. **Nothing in the write pipeline populates them yet** — the
  Extractor's prompt (`extractor.py:260`) still only asks for identity/preference/decision/
  goal/adopted-knowledge content; this is an open, explicitly-flagged gap this document
  inherits, not one it closes.
- **`ContextKind.PROJECT` is defined but dead** (`retrieval_models.py:1306`) —
  `WorkingContextBuilder._build_context` only ever constructs `TOPIC` (per-concept) or
  `GENERAL` (`working_context_builder.py:156,168`). No code path produces a
  project-wide, single, holistic context today.
- **Correction to `WORKING_CONTEXT_2_DESIGN.md` §5**: that document justifies incremental
  materialization by analogy — *"the same way `CanonicalMatcher` already maintains
  canonical-fact state incrementally rather than recomputing from the full history each
  time."* Direct read of `canonical_matcher.py` and `pipeline.py` shows this is not accurate.
  `CanonicalMatcher.match_with_target` takes an `existing: List[KnowledgeObject]` the *caller*
  loaded and mutates it **in memory, for the duration of one `ManagerPipeline.process()` call
  only** (`pipeline.py:325,330,342` — `existing.append`/`existing[...] = matched_knowledge`).
  Nothing persists that list between calls. `MemoryStore.load()` rebuilds its entire in-memory
  index from every markdown note in the vault on every load; `VaultWriter.write()`
  (`vault_writer.py:181`) writes one note per `KnowledgeObject` and nothing else. **There is
  no precedent anywhere in this codebase for a persisted, incrementally-updated derived
  aggregate.** This matters directly for §4 below: building one is new infrastructure, not an
  extension of an existing pattern, and should be costed and risked as such.

The core gap this document addresses, restated precisely: every read-side mechanism built so
far — `ContextPlan`, `CoverageReport`, `GapRecoveryDecision`, `WorkingContext` itself — is
**ephemeral and request-scoped**. Even `WORKING_CONTEXT_2_DESIGN.md`'s own §5 already named
the fix ("materialized, incrementally-maintained view... updated at write time") as the
single highest-leverage change, and it is the one piece of that whole design that was never
built. `ProjectState` is that missing piece, specified precisely enough to build.

---

## 1. What `ProjectState` is, precisely, and how it differs from everything above

**`ProjectState` is a durable, versioned, per-project snapshot of "where things currently
stand," produced as a byproduct of the *write* pipeline and read directly at query time — not
re-derived by running retrieval.** Contrast:

| Object | Scope | When computed | Persisted? |
|---|---|---|---|
| `ContextPlan` | one query | before retrieval, every call | No |
| `CoverageReport` / `GapRecoveryDecision` | one query | after retrieval, every call | No |
| `WorkingContext` | one query | after allocation, every call, from whatever this query's retrieval happened to activate | No |
| **`ProjectState`** | **one project, all history** | **incrementally, at write time** | **Yes** |

A `WorkingContext` answers "what did *this query's* retrieval find." `ProjectState` answers
"what is true about this project *right now*," independent of any particular query. The
"continue implementing Haven" case in the brief is definitionally a request for the second
thing — running six parallel category retrievals (the `CONTINUATION` mode's plan) every
single time is the *expensive* way to answer a question that shouldn't require retrieval at
all once the state already exists somewhere durable.

---

## 2. Q1 — What should `ProjectState` contain?

Reusing existing vocabulary wherever it exists (`ContextCategory`, `MemoryRole`,
`ContextKind.PROJECT`) rather than inventing a fourth parallel taxonomy:

```python
class FieldDerivation(str, Enum):
    DETERMINISTIC = "deterministic"  # pure projection of stored fields, no judgment
    MEMORY_DIRECT = "memory_direct"  # a category's accepted KnowledgeObjects, verbatim
    INFERRED = "inferred"            # bounded LLM synthesis, fails open (§3)

@dataclass(frozen=True)
class StateRef:
    """A lightweight pointer into the vault -- not a RankedCandidate.

    ProjectState is persisted; it must never embed a transient ranking score
    or an in-memory-only object. `RankedCandidate`/`Candidate` stay
    retrieval-pipeline-local (see §4's "why not a KnowledgeObject/Candidate").
    """
    knowledge_object_id: UUID
    canonical_fact: str              # snapshot at time of write, may drift from live vault
    valid_from: datetime
    confidence: float
    importance: float

@dataclass(frozen=True)
class ProjectStateField(Generic[T]):
    value: T
    derivation: FieldDerivation
    source_ids: Tuple[UUID, ...]     # KnowledgeObject ids backing this value; () if none
    confidence: float                # 0.0-1.0; 1.0 for DETERMINISTIC/MEMORY_DIRECT
    last_updated: datetime

@dataclass(frozen=True)
class ProjectState:
    project_key: str                              # anchor concept id (ContextKind.PROJECT)
    version: int                                   # monotonic; bumped every incremental update
    identity: ProjectStateField[str]                # "what this is" -- one line
    phase: Optional[ProjectStateField[str]]         # INFERRED, nullable
    current_objective: Optional[ProjectStateField[StateRef]]  # top GOAL, or INFERRED synthesis
    implementation_state: Tuple[StateRef, ...]       # top-K, MEMORY_DIRECT
    active_tasks: Tuple[StateRef, ...]               # top-K, MEMORY_DIRECT
    decisions: Tuple[StateRef, ...]                  # current (non-superseded) only
    superseded_decisions: Tuple[StateRef, ...]        # bounded recent history, not full archive
    rejected_approaches: Tuple[StateRef, ...]         # MEMORY_DIRECT (empty until Extractor changes, §8)
    blockers: Tuple[StateRef, ...]                    # never-drop tier
    constraints: Tuple[StateRef, ...]                 # never-drop tier, never INFERRED
    do_not_do: Tuple[StateRef, ...]                   # never-drop tier, never INFERRED
    code_areas: Tuple[StateRef, ...]
    open_questions: Tuple[StateRef, ...]
    recent_discoveries: Tuple[StateRef, ...]
    gaps: Tuple[ContextCategory, ...]                  # explicit "no data", never inferred away
    last_incorporated_event_id: Optional[UUID]          # write-time watermark, see §4
    created_at: datetime
    updated_at: datetime
```

`project_key` reuses (and finally activates) `ContextKind.PROJECT` — the anchor concept a
project is already implicitly organized around in the ontology graph, not a new identity
system. `gaps` reuses `ContextCategory` directly, the same vocabulary `ContextPlan`/
`CoverageReport` already use, so a `ProjectState` gap and a `CoverageReport` gap are
diagnosable the same way.

---

## 3. Q2–Q5 — deterministic, memory-direct, inferred, and never-inferred, in one table

| Field | Derivation | Why |
|---|---|---|
| `version`, `created_at`, `updated_at`, `last_incorporated_event_id` | **Deterministic** | Pure bookkeeping over the update process itself. |
| `decisions` / `superseded_decisions` split | **Deterministic** | Already fully decided by `KnowledgeObject.valid_until` + `DecisionMetadata.status` (`KnowledgeUpdater`'s existing supersession fields) — a projection, not a judgment. |
| `implementation_state`, `active_tasks`, `code_areas`, `open_questions`, `blockers`, `constraints`, `do_not_do`, `recent_discoveries` | **Memory-direct** | Each is "the top-K active `KnowledgeObject`s whose `MemoryType` resolves to this `ContextCategory`" — exactly `CoverageAnalyzer.resolve_category`'s existing table, reused verbatim. No synthesis: the field *is* a set of pointers to real memories, verbatim. |
| `rejected_approaches` | **Memory-direct**, but currently always empty | The category exists (`ContextCategory.CODE_AREA`... no — there is in fact no `ContextCategory.REJECTED_APPROACH` yet either); see §8, this is an open write-side gap, not a ProjectState defect. |
| `gaps` | **Deterministic** | A category is a gap iff its memory-direct field is empty after §4's aggregation. Never inferred to be "probably fine" — this is the single most important invariant in this whole design (see below). |
| `phase` | **Inferred** | "What phase is this project in" has no direct `KnowledgeObject` field — no amount of table lookup produces it. Bounded LLM call, cached by `(project_key, version)`, re-run only when triggered (§7), fails open to the *previous* persisted value (never a guess) on any error — exact `QueryRewriter` contract. |
| `current_objective` | **Inferred when ambiguous, else memory-direct** | When exactly one active `GOAL`-typed memory exists, this is memory-direct (no inference needed). When several compete, ranking them requires judgment `DeterministicRanker`'s existing recency/importance/confirmation scoring already approximates well — so the *default* is still deterministic (highest-scoring `GOAL`), and LLM synthesis is reserved for the case where scores are within a tie band, mirroring `CategoryPreferenceScorer`'s "only tips close races" philosophy. |
| `identity` | **Inferred once, then sticky** | No `KnowledgeObject` is "the project's one-line description" by construction. Synthesized once (or on explicit user correction), then persisted and never silently re-synthesized — an identity that changes every few conversations because phrasing drifted would be actively confusing, unlike `phase`, which is expected to change. |

**Never inferred, under any circumstance:**

- **`constraints`, `do_not_do`** — a durable rule must be the user's actual words, sourced
  from a `MemoryType.RULE` (or, once extraction catches up, a `CONSTRAINT`-typed) object
  verbatim. Paraphrasing a constraint is how a fabricated constraint enters the system; this
  is the exact failure `WORKING_CONTEXT_2_DESIGN.md` §7 already protects with its "never
  removed" tier, extended here to "never synthesized" as well.
- **`blockers`** — same reasoning: a blocker the AI invents because it seems plausible given
  the phase is worse than no blocker at all, since it reads as ground truth.
- **`decisions` status (final vs. superseded)** — already fully decided deterministically by
  the supersession chain (`KnowledgeUpdater`); there is no ambiguity here to resolve with
  judgment, so introducing one would be a pure regression.
- **`gaps`** — the absence signal itself must never be filled by inference. This is the same
  principle `WORKING_CONTEXT_2_DESIGN.md` §6 already established for Gap Detection
  ("say 'I don't have information on X' instead of proceeding as if X doesn't matter") and the
  `gap_honesty` benchmark category (§9 there) exists specifically to measure it. A
  `ProjectState` that quietly infers a plausible-sounding blocker or constraint list to avoid
  an empty section would be strictly worse than the amnesia the brief is trying to fix — it
  would replace "the AI doesn't know" with "the AI is confidently wrong," which is a harder
  failure to catch.

---

## 4. Q6 — Can `ProjectState` be materialized incrementally at write time?

Yes, and per §0's correction, this is genuinely new infrastructure, not an extension of an
existing incremental-update pattern. Three parts:

**a) Persistence: a new artifact type, deliberately outside the `KnowledgeObject`/`Candidate`
type system.** `ProjectState` must not be written as a vault markdown note the way a
`KnowledgeObject` is — if it were, `HybridCandidateRetriever` would pick it up as a retrieval
candidate on the next query, which is exactly wrong (it is a *derived index*, not a fact the
user stated). Recommend a single JSON sidecar per project
(e.g. `_state/<project_key>.json`, written by a small new writer analogous to `VaultWriter`
but with no relationship to it), read by a new `ProjectStateStore` that `MemoryEngine` holds
alongside `MemoryStore`. This keeps `ProjectState` fully outside the existing candidate
generation path with zero risk of contaminating retrieval — no change to
`HybridCandidateRetriever`, `resolve_role`, or `resolve_category` is needed or wanted.

**b) Update trigger: one new hook in `ManagerPipeline`, exactly where
`WORKING_CONTEXT_2_DESIGN.md` §5/§8 already proposed it — after `match_and_apply` produces
its `List[ExtractionDecision]` for a conversation, not per-fact.** For each decision whose
`knowledge.memory_type` resolves to a `ContextCategory` (reusing `resolve_category` verbatim,
per §0's note that `CoverageAnalyzer`/`CategoryPreferenceScorer` already share this table for
exactly this reason — a third independent copy would be the same drift risk those two modules
already avoided):

- `NEW`/`CONFIRM`/`UPDATE` on a category-mapped type → upsert a `StateRef` into that
  category's tuple on the project's persisted `ProjectState`, then apply top-K + tier-aware
  truncation (§6) so the field never grows unbounded.
- A `DECISION` whose `DecisionMetadata.status` moves to `SUPERSEDED` → move its `StateRef`
  from `decisions` to `superseded_decisions` (bounded).
- Bump `version`, set `last_incorporated_event_id` to the conversation's terminal event id,
  set `updated_at`.

**c) Read-time collapses to three steps, cheapest first — exactly the "load state, verify,
gap-fill" pattern `WORKING_CONTEXT_2_DESIGN.md` §5 already named as the target, now with a
real persistence mechanism to run it against:**

1. `ProjectStateStore.load(project_key)` — O(1) file read, not a retrieval pass.
2. A cheap freshness check, reusing `GapDetector`-shaped logic already designed in
   `CONTEXT_PLAN_OBJECT.md` §5: for each category a `ContextPlan` actually requires, is the
   corresponding `ProjectState` field non-empty and not in `gaps`?
3. Only for a category that fails step 2, fall back to today's already-built per-category
   retrieval (`CategoryPreferenceScorer` + `AcceptanceStage`, unchanged) to gap-fill *that
   category only* — never a full re-derivation.

This is the actual scaling win: updating costs `O(decisions in one conversation)`, not
`O(every active memory in the vault)`, so query latency for "continue implementing X" stays
flat whether the project has 5 conversations or 500 — the opposite of today's
`WorkingContextBuilder.build`, which re-sorts and re-buckets every allocated candidate on
every single call regardless of history length.

---

## 5. Q7 — Interaction with `WorkingContext`

`ProjectState` becomes the backbone `WorkingContext` reads *from* for project-wide queries,
without changing `WorkingContext`'s own shape:

- A new construction path, `WorkingContextBuilder.from_project_state(state: ProjectState,
  gap_fill: List[RankedCandidate]) -> WorkingContext`, builds exactly one
  `kind=ContextKind.PROJECT` context (finally activating that dead enum value, per §0) by
  converting each `ProjectState` category's `StateRef` tuples into the existing
  `RoleBucket`-via-`MemoryRole` shape — reusing `resolve_role`/`MemoryRole` unchanged, so
  every downstream consumer (`StructuredPromptBuilder`, the Retrieval Inspector) sees the
  identical object shape it already handles. `gap_fill` (§4c's step 3 output, if any) is
  merged in the same pass.
- **For narrow, single-concept, or `POINTED_QA` queries: entirely unaffected.**
  `ProjectState` is never consulted; `WorkingContextBuilder.build`'s existing per-concept
  `TOPIC`/`GENERAL` grouping runs exactly as it does today. This preserves the same
  "strict floor, never a regression" property `CONTEXT_PLAN_OBJECT.md` §2 already established
  for `ContextPlan`'s `POINTED_QA` sentinel — `ProjectState` is additive machinery for the
  `CONTINUATION`/project-wide case only, never a change to any other query's behavior.
- `ContextPlanner` itself needs no change: `TaskMode.CONTINUATION`'s existing
  `requirements` table (already "all categories," `context_planner.py:445-452`) is precisely
  the signal that should route to "read `ProjectState` first" instead of "run six per-category
  retrieval passes" — a routing decision that belongs in the still-unbuilt
  `MemoryEngine.query_context_plan` (`CONTEXT_PLAN_OBJECT.md` §8), not a new field on
  `ContextPlan`.

---

## 6. Q8 — Should `ProjectState` become a first-class retrieval target?

**Yes.** Recommend a new `MemoryEngine.get_project_state(project_key) -> ProjectState` method
and a corresponding read-only endpoint. For the specific "continue implementing Haven" case
the brief opens with, this is a categorically cheaper and more direct answer than routing
through `ContextPlanner` → six parallel category retrievals → `GapDetector` → `WorkingContextBuilder`
every time: it is one file read plus a bounded freshness check. `ContextPlan`/`CoverageReport`/
`GapRecoveryDecision` remain valuable for the case `ProjectState` can't answer alone —
narrow, evidence-specific queries, and gap-filling when `ProjectState` itself has a genuine
hole — but they should not be the *first* thing a `CONTINUATION`-mode query does once
`ProjectState` exists.

Priority under a token budget reuses `WORKING_CONTEXT_2_DESIGN.md` §7's tiering directly
(`constraints`/`do_not_do`/top blocker never-drop; architecture-snapshot-equivalent fields
collapse first) rather than inventing a second budget scheme — `ProjectState`'s categories
already carry the same `PriorityTier` vocabulary `CategoryRequirement` uses (§2).

---

## 7. Q9 — Evolution over months (500 conversations, thousands of memories)

- **Flat update cost, bounded field size.** §4's incremental delta plus top-K/tier-aware
  truncation at write time (not render time) keeps every `ProjectState` field a small,
  constant-size structure regardless of project age — the property that makes this different
  from `WorkingContextBuilder`'s current full-recompute-per-query design, which slows down as
  the vault grows.
- **Superseded/resolved items age out of the hot fields but are never deleted.** They remain
  ordinary `KnowledgeObject`s in the vault (`valid_until` set, or `DecisionMetadata.status`
  moved), reachable through normal retrieval/audit tooling — `ProjectState` is a "now"
  snapshot, not a history log. `superseded_decisions` keeps a small bounded recent window as
  a convenience, not a full archive.
- **Phase transitions.** `phase` is re-inferred only when triggered — e.g. after N new
  `implementation_state`/`decision` deltas have landed since the last inference, not on every
  write — same cache-and-fail-open discipline as `QueryRewriter`, so most conversations touch
  no LLM at all.
- **Multi-project vaults.** `project_key` must be a first-class scoping dimension from the
  start (§2), even though Haven's own vault today has effectively one project. Retrofitting
  this after building single-project-only would mean redesigning the persistence layer, not
  extending it — cheap to build in now, expensive to add later.
- **Deliberately not building**: Zep-style bitemporal fact-validity tracking. `version` +
  `updated_at` is sufficient for "is this stale" detection; anything more sophisticated solves
  fact-correctness-over-time, a different problem than project-state reconstruction, and is
  already explicitly out of scope per `CONTEXT_PLANNER_DESIGN.md`'s own alternatives-considered
  section — that reasoning applies unchanged here.

---

## 8. Q10 — Failure modes

- **Silent drift.** The write-time hook fails to fire (exception swallowed, hook never
  registered for a given write path) and `ProjectState` quietly goes stale while the vault
  keeps growing. Mitigation: because every field's derivation rule is *pure* per §3, a full
  rebuild from the entire vault is always available as a reconciliation path, not a new code
  path — compare `last_incorporated_event_id` against the vault's actual latest event
  periodically (or lazily, on read, when the gap exceeds a threshold) and rebuild from scratch
  when it has drifted too far. This is the safety net the incremental path needs precisely
  because §0 established there's no existing precedent for incremental persistence in this
  codebase to lean on.
- **Confabulation via the inferred fields.** Bounded by keeping §3's never-inferred list
  genuinely untouched by any LLM path, and by `phase`/`current_objective`/`identity` failing
  open to the *previous persisted value* (never a guess) on any LLM error — the same contract
  `QueryRewriter` already uses.
- **Partial category resolution inherited from `CoverageAnalyzer`.** `GOAL, PROJECT, PERSON,
  EVENT, SKILL, PREFERENCE` have no `ContextCategory` mapping today. `current_objective` is
  the sharpest exposure — it must be sourced via `MemoryRole.GOAL` (the `resolve_role` path),
  not via `ContextCategory` like every other field, since `GOAL` has no category mapping at
  all. This is a genuine, existing seam this design must route around, not paper over.
- **Concurrent-write races.** Two conversations about the same project processed concurrently
  could both read version *N* and write *N+1*, one clobbering the other's delta. Requires
  optimistic concurrency on the sidecar write (check `version` before writing, retry the delta
  against the fresh version on conflict) — the same shape of guard this environment's own
  Artifact tool already uses (`baseVersion`/409), not a novel mechanism to invent.
- **`rejected_approaches` has no write-side source yet.** The Extractor's prompt doesn't ask
  for "we tried X and dropped it" content — the same gap `PROJECT_STATE_KNOWLEDGE_MODEL.md`
  §0 already flagged for the read side. `ProjectState` can only surface what gets written;
  until the Extractor changes, this field stays honestly empty and shows up in `gaps` — it
  must not be forced to synthesize content to fill the category, per §3's constraint.
- **Field-growth regression.** Without enforcing top-K at write time (§4b), `ProjectState`
  degrades back into an ever-growing blob — the exact problem it exists to avoid. Truncation
  must be a write-time invariant, not a render-time afterthought.

---

## 9. Benchmark implications

- **Directly unblocks `cold_open_continuation`** (`WORKING_CONTEXT_2_DESIGN.md` §9, proposed,
  never built) — now testable as "read `ProjectState`, no retrieval pipeline required" instead
  of depending on the full per-category retrieval loop existing first.
- **New failure type**: `STATE_DRIFT` — `ProjectState.version`/`last_incorporated_event_id`
  lags the vault's actual latest write. Distinct from `GAP_MISSED`/`STALE_PRESENTED_AS_CURRENT`
  (already proposed in `WORKING_CONTEXT_2_DESIGN.md` §9): those are read-side gap-detection
  failures; `STATE_DRIFT` is a write-side incremental-update bug, needs its own detection and
  its own benchmark case.
- **Needs a synthetic long-horizon fixture generator.** Nothing in the existing benchmark
  corpus exercises more than a handful of turns per case (`PRE_BENCHMARK_FREEZE_AUDIT.md`,
  referenced in `CONTEXT_PLANNER_DESIGN.md` §3 point 7) — a 500-turn synthetic project with
  planted decisions/rejections/blockers/supersessions is new infrastructure this design
  requires to be measured honestly, not something that falls out of existing fixtures.

---

## 10. Phased roadmap

**Phase A — foundation, prove correctness before optimizing for cost. IMPLEMENTED
(2026-07-09), narrower than described below — see "Phase A implementation note" at the top
of this document for exactly what shipped and why it differs.**
`ProjectState`/`StateRef`/`ProjectStateField` dataclasses + `to_dict`/`from_dict`; activate
`ContextKind.PROJECT`; a one-shot **full-vault aggregation function** (no incremental hook
yet) implementing every `DETERMINISTIC`/`MEMORY_DIRECT` field from §3; `ProjectStateStore`
(JSON sidecar reader, no writer yet); `MemoryEngine.get_project_state()` computing fresh on
every call. This answers "can we compute `ProjectState` correctly, at all" before touching the
harder incremental-update question — mirrors `CONTEXT_PLAN_OBJECT.md` §10's own "before
hackathon" sizing discipline of shipping the object before the optimization.
*What actually shipped instead:* `ProjectStateBuilder.build()` derives `ProjectState` from
one query's already-allocated candidates (not a full-vault scan); `ContextKind.PROJECT`
was **not** activated; no `ProjectStateStore` was built (nothing is read from or written to
disk); there is no standalone `MemoryEngine.get_project_state()` method — `ProjectState` is
attached to `RetrievalTrace.project_state` inside `query_with_trace()` instead.

**Phase B — the real engineering investment: incremental materialization.**
The `ManagerPipeline` write-time hook (§4b), `ProjectStateStore` writer with optimistic
concurrency, `version`/`last_incorporated_event_id` watermarking. Turns Phase A's
full-recompute into the incremental delta that actually scales — this is where most of the
genuine implementation risk lives, since §0 established there is no existing pattern in this
codebase to extend.

**Phase C — wiring into the read path.**
`WorkingContextBuilder.from_project_state` (§5); routing `TaskMode.CONTINUATION` to
`ProjectState` first inside `query_context_plan` (depends on that method existing per
`CONTEXT_PLAN_OBJECT.md` §8 — a sequencing dependency on already-designed, not-yet-built work,
not new scope invented here); the §4c freshness-check + bounded gap-fill fallback.

**Phase D — inferred fields.**
`phase`/`current_objective`(tie-break case)/`identity`(first-synthesis case) via a bounded LLM
call reusing `QueryRewriter`'s client/cache/fail-open scaffolding directly, per the same
reuse discipline every prior phase in this codebase has followed.

**Phase E — hardening.**
Reconciliation/drift-detection job (§8), the `STATE_DRIFT` benchmark category and long-horizon
fixture generator (§9), Extractor prompt work to actually populate `rejected_approaches`
(currently blocked on write-side scope, §8) — the last item is explicitly Extractor work, not
`ProjectState` work, and should be scoped and reviewed separately.

---

## 11. Risks (consolidated)

- **New persistence layer with no existing precedent** (§0, §4a) is the single largest source
  of implementation risk in this design — budget Phase B accordingly, and do not assume it
  inherits reliability properties from `MemoryStore`/`VaultWriter` just because they're
  nearby in the codebase.
- **Confabulation risk is concentrated entirely in the three `INFERRED` fields** (§3) — the
  never-inferred list is what makes the rest of the design safe to build quickly; any future
  change that moves a field from `MEMORY_DIRECT`/`DETERMINISTIC` to `INFERRED` should be
  treated as a deliberate, reviewed decision, not a convenience refactor.
- **`ProjectState` can silently become the stale, over-trusted "cache" it's designed to avoid
  being** if Phase E's reconciliation job is skipped or deprioritized after Phase B ships —
  the drift-detection safety net is not optional hardening, it's the thing that keeps an
  incremental system honest over a 500-conversation horizon.
- **Scope creep into fact-correctness-over-time (Zep-style) or self-editing memory
  (Letta-style)** is a real temptation once a persisted state object exists — both are
  explicitly rejected in §7 for the same reasons `CONTEXT_PLANNER_DESIGN.md` already rejected
  them: different problems, and conflating them dilutes this design's actual, narrower
  contribution.

---

## 12. Recommendation

Build Phase A now: it is low-risk, proves the datamodel and category vocabulary are right
before any persistence engineering happens, and is independently useful (a
correctly-computed-if-recomputed-every-time `ProjectState` is already strictly better than
today's from-scratch `WorkingContext` assembly for the `CONTINUATION` case, even before
incremental materialization exists). Treat Phase B as the real architecture investment and
staff/review it accordingly — it is genuinely new infrastructure for this codebase, not an
extension of an existing pattern, and is where a wrong call (persistence format, concurrency
model, reconciliation strategy) is expensive to unwind later. Do not build Phase D's inferred
fields before Phase A/B are solid; an LLM-synthesized `phase` sitting on top of an unreliable
or unvalidated aggregation layer would hide correctness problems rather than surface them.
The center of this design's value is exactly what the brief asked for: **a small number of
fields that are either pure derivations of stored facts or direct pointers to them, a short,
explicit list of fields that must never be guessed, and an update strategy that gets cheaper
to maintain the longer the project runs — not more expensive, which is what today's
full-recompute-per-query `WorkingContext` does instead.**
