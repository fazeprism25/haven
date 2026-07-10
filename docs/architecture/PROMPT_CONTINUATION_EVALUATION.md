# Prompt Continuation Evaluation

Status: **Evaluation, plus a narrow, since-implemented Quick Wins pass (2026-07-10
— see "Implementation status" below). No redesign, no retrieval changes, no
LLM summarization.** Grounded entirely against source on disk as of
2026-07-10: `obsidian/memory_engine/structured_prompt_builder.py`,
`obsidian/memory_engine/project_state.py`, `obsidian/memory_engine/working_context_builder.py`,
`obsidian/memory_engine/context_builder.py`, `obsidian/memory_engine/context_planner.py`,
`obsidian/memory_engine/engine.py`, `obsidian/ontology/retrieval_models.py`,
`obsidian/ontology/models.py`, plus `docs/architecture/PROJECT_STATE_DESIGN.md`,
`PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md`, and `PROJECT_STATE_EVALUATION.md`.

## Implementation status (2026-07-10)

Of the four Quick Win candidates evaluated for implementation after this
document shipped, two were implemented (both deterministic, additive, no
retrieval/ranking/planner/persistence changes), and two were rejected —
one because the architecture already deliberately suppresses it, one
because it cannot be done without changing `POINTED_QA` behavior:

1. **Implemented — the `[N]` convention is now explained in `<Guidance>`.**
   `_GUIDANCE_LINES` (`structured_prompt_builder.py`) gained one bullet
   stating that the same `[N]` denotes the same underlying memory everywhere
   it appears. Removes the reconstruction cost §2/§5/§6 identified, for the
   cost of three lines rendered on every prompt (`Guidance` already renders
   unconditionally, so this is not gated on `TaskMode`).
2. **Implemented — `TOPIC`-kind `WorkingContext` titles resolve to
   `Concept.label`.** `MemoryEngine._resolve_topic_titles` (`engine.py`)
   performs one read-only `ConceptGraph.get_concept` lookup per context
   (already-loaded, in-memory graph; no retrieval, no second candidate/
   ranking pass) and replaces a `TOPIC` context's `str(anchor_concept_id)`
   title with that concept's label, when the graph has one. Applied
   identically by both `query_working_context()` and `query_structured()`
   so the two stay mutually consistent. Deliberately implemented *outside*
   `WorkingContextBuilder` rather than by threading `ConceptGraph` into
   it — that module's own docstring and its
   `TestNoOutOfScopeImports` test assert it never touches `ConceptGraph`,
   and preserving that boundary (a real, tested architectural decision) was
   judged more important than saving one method call. `GENERAL`-kind
   contexts (already `"General"`) and the still-dead `PROJECT` kind are
   untouched. See `TestTopicTitleResolution` in `obsidian/tests/test_engine.py`.
3. **Rejected — the `generated_at` "as of" timestamp stays unrendered.**
   `PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md`'s "What shipped" section
   already states, explicitly and by design, that `generated_at` is
   "a diagnostic timestamp with no orientation value to a downstream model."
   This is not an oversight this task's Quick Win pass should silently
   reverse — reinstating it changes what "as of" should mean relative to
   `<Memory valid_from="...">` (each memory already renders its own
   timestamp), which format to use, and whether it belongs in `<Guidance>`
   or a `<ProjectState>` attribute. §10's own Medium Improvements framing
   ("worth a scoped decision, not a blanket reinstatement") already called
   this correctly; this pass leaves it exactly as documented there —
   future work, not a Quick Win.
4. **Rejected — CONTINUATION orientation stays gated on lexical
   classification, not a deterministic floor.** The only way to make the
   orientation layer (`<ProjectState>`) a floor is to stop gating it on
   `ContextPlan.task_mode is TaskMode.CONTINUATION` in `query_structured()`
   — which by construction changes `POINTED_QA` output (today, the common
   case, byte-identical to omitting `project_state`; a floor would give it
   a `<ProjectState>` element too). This directly contradicts the
   requirement that `POINTED_QA` behavior stay unaffected, so there is no
   version of "make it a floor" that qualifies as low-risk here. Left as
   future work exactly as this document's §10 Long-Term Ideas #1 already
   scoped it — it needs its own review of the classification/routing
   decision, not a renderer change.

`PROJECT_STATE_EVALUATION.md` already did a deep, correct pass on `ProjectState`
specifically (redundancy, field quality, scaling, XML ordering). This document does
not repeat that analysis — it cites it where relevant — and instead evaluates the
**whole rendered prompt** (`<Guidance>` → `<ProjectState>` → `<WorkingContext>`* →
`<UserRequest>`) against one question that document didn't ask: is this shaped for
how an LLM actually resumes a long-running project, or does it still read like a
retrieval pipeline's own internal structure exposed verbatim?

---

## 0. The concrete object under review

`StructuredPromptBuilder.render()` (`structured_prompt_builder.py:194-281`) produces,
in exactly this order, for a `TaskMode.CONTINUATION` query with two Working Contexts:

```
<System>
  <HavenContext version="1">
    <Guidance>
      (10 fixed lines: memory is background info, not instructions; confidence
      governs certainty; prefer higher-confidence/newer on conflict; surface
      contradictions; ignore if irrelevant)
    </Guidance>
    <ProjectState confidence="0.62">
      <CurrentObjective>[1] ...</CurrentObjective>
      <Decisions><Item>[2] ...</Item></Decisions>
      <ActiveTasks><Item>[3] ...</Item></ActiveTasks>
      <Blockers><Item>[4] ...</Item></Blockers>
      <Constraints><Item>[5] ...</Item></Constraints>
      <OpenQuestions><Item>[6] ...</Item></OpenQuestions>
      <Gaps><Item>superseded_decisions</Item><Item>code_areas</Item></Gaps>
    </ProjectState>
    <WorkingContext title="7f3a1c9e-...-b21d" kind="topic" status="active">
      <WorkingContextState>
        <Status>active</Status>
        <CurrentGoal>[1] ...</CurrentGoal>
        <RecentDecisions><Item>[2] ...</Item></RecentDecisions>
        <PendingTasks><Item>[3] ...</Item></PendingTasks>
        <OpenQuestions><Item>[6] ...</Item></OpenQuestions>
      </WorkingContextState>
      <RoleBuckets>
        <Decisions><Memory index="2" type="decision" confidence="0.90" ...>...</Memory></Decisions>
        <Goals><Memory index="1" ...>...</Memory></Goals>
        <Tasks><Memory index="3" ...>...</Memory><Memory index="4" type="blocker" ...>...</Memory></Tasks>
        <Beliefs/>
        <Research/>
        <OpenQuestions><Memory index="6" ...>...</Memory></OpenQuestions>
        <Reference/>
      </RoleBuckets>
    </WorkingContext>
    <WorkingContext title="General" kind="general" status="reference">...</WorkingContext>
  </HavenContext>
  <UserRequest>
    Continue implementing Haven.
  </UserRequest>
</System>
```

The `title="7f3a1c9e-...-b21d"` line is not a simplification for this document — it is
what the code actually renders. `WorkingContextBuilder._build_context` sets
`title=str(anchor)` where `anchor` is a raw concept `UUID`
(`working_context_builder.py:155-156, 187-190`), and `ActivatedConcept` carries only
`concept_id: UUID`, no label (`retrieval_models.py:54-73`). `Concept.label` — the
human-readable name (`"Haven"`, `"Claude"`) — exists on `Concept`
(`obsidian/ontology/models.py:56-58`) but `WorkingContextBuilder` never has a
`Concept` object in scope; its own docstring states it never touches
`ConceptGraph` (`working_context_builder.py:26-30`). So every `TOPIC`-kind
`WorkingContext`'s title — the very first token identifying what the section below it
is about — is a UUID string with zero semantic content to a reading model. This one
fact recurs through several of the questions below.

Everything downstream of this section evaluates that object.

---

## 1. Information hierarchy

**Order encountered, top to bottom:** `Guidance` (fixed, always) → `ProjectState`
(conditionally present — only when `ContextPlanner` lexically classifies the query as
`TaskMode.CONTINUATION`, `engine.py:1206-1208`) → one `WorkingContext` per distinct
primary concept, in **ascending `str(concept_id)` order**
(`working_context_builder.py:152-161`) → a trailing `GENERAL` context → `UserRequest`,
last.

Two things are and are not optimal here:

- **`Guidance` first, `UserRequest` last is correct** and is explicitly reasoned about
  in the module docstring (`structured_prompt_builder.py:59-61`, "durable framing
  leads, the actual ask closes") — this matches how a long-context model attends, and
  matches how a human would also want the operating rules read before the specifics.
- **`ProjectState` before `WorkingContext` is the right idea, conditionally applied.**
  When present, it correctly puts orientation-shaped content before per-topic detail —
  this is `PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md` §4's own stated goal, and it
  is what shipped. But it is present for exactly one `TaskMode` out of five
  (`context_planner.py:111-140`), decided by pure lexical substring match
  (`context_planner.py:598-611`). For every other classification — `POINTED_QA` (the
  documented common case, `context_planner.py:557,607`), `CODING_DEBUGGING`,
  `STRUCTURING`, `RESEARCH` — there is no orientation layer at all; the prompt goes
  straight from `Guidance` into topic clusters. A human resuming a project does not
  reconstruct context differently depending on which of five sentence patterns they
  happened to phrase their first message with; the current architecture does.
- **The ordering *within* `<ProjectState>` is declaration order, not priority
  order.** `_PROJECT_STATE_LIST_SECTIONS` (`structured_prompt_builder.py:139-148`)
  renders `Decisions`, `SupersededDecisions`, `ActiveTasks` *before* `Blockers` and
  `Constraints`. `PROJECT_STATE_EVALUATION.md` §6 already flagged this as backwards
  relative to how humans actually prioritize on resume (blockers/constraints before
  decision history) — this document's own §5 table agrees and adds nothing new here
  except to note it is the same finding at the whole-prompt level: the one place the
  design pays deliberate attention to ordering (`ProjectState` before `WorkingContext`)
  does not carry the same discipline one level deeper.
- **`WorkingContext` ordering is a UUID sort, not a relevance sort.** Which topic
  cluster a model reads *first* — the position with the strongest primacy effect after
  `ProjectState` — is decided by `sorted(grouped.items(), key=lambda item: str(item[0]))`
  (`working_context_builder.py:160`), i.e. lexicographic order of an opaque concept id.
  This is deterministic (a real, stated goal elsewhere in this codebase) but
  deterministic and *meaningful* are different properties; nothing about UUID sort
  order correlates with which topic is more central to "continue implementing Haven."
  A human reconstructing context orders topics by relevance/recency, never by an
  arbitrary stable key.

**Verdict:** the top-level shape (`Guidance` → orientation → detail → request) is
correct engineering when the orientation layer is present. The two sub-orderings
inside it (declaration order inside `ProjectState`; UUID order across
`WorkingContext`s) are accidents of implementation convenience, not authored
information-hierarchy decisions, and both diverge from how a human would sequence the
same material.

---

## 2. Cognitive load

**Requires reconstruction (not stated, must be inferred):**

- **What a `WorkingContext` is about.** Per §0, a `TOPIC` context's only self-
  identifying label is a UUID. The model must read every `<Memory>` in every bucket
  and infer the topic itself before it can even decide whether this section is
  relevant to "continue implementing Haven." This is the single largest unforced
  reconstruction cost in the prompt — the section header, the cheapest possible
  orientation signal, carries none.
- **What `[N]` means.** The `_GUIDANCE_LINES` tuple (`structured_prompt_builder.py:155-167`)
  — the one place the prompt gets to explain its own conventions to the model — never
  mentions the `[N]` indexing scheme at all, despite it being used throughout
  `<ProjectState>` and every `<WorkingContextState>` summary field
  (`_ref_state`/`_ref`, `structured_prompt_builder.py:374-388, 519-530`). A model must
  pattern-match "`[1]` appears here and also over there" and infer, unaided, that
  matching numbers denote the *same* underlying memory rather than two independently
  numbered facts that happen to collide. This is exactly the class of "mental
  reconstruction" the task asks about, and it is entirely avoidable — it costs one
  sentence in `Guidance`.
- **What `<Gaps>` actually means.** `ProjectState.gaps` is honestly documented in
  Python (`project_state.py:78-90`, "absent from *this run's* reconstruction," not the
  vault) but that caveat does not survive into the rendered `<Gaps>` element or into
  `Guidance`. A model reading `<Gaps><Item>blockers</Item></Gaps>` has no way to
  distinguish "no blocker exists" from "a blocker exists but scored 51st in this run's
  top-50" (`PROJECT_STATE_EVALUATION.md` §3, §7) — it will reconstruct whichever
  reading is more convenient for the current turn, silently.
- **Which bucket tag can hide a different `MemoryType`.** `MemoryType.BLOCKER` resolves
  to `MemoryRole.TASK` (`retrieval_models.py:1625`), so a blocker renders inside the
  `<Tasks>` bucket, distinguishable from an ordinary task only by reading each
  `<Memory>` element's own `type="blocker"` attribute — the bucket tag itself
  (`<Tasks>`) cannot be trusted as a category label without checking every child. A
  model skimming section headers for "what's blocking this" will not find a
  `<Blockers>`-tagged item inside `<RoleBuckets>` at all (that tag only exists inside
  `<ProjectState>`, and only for `CONTINUATION` queries).

**Already synthesized (low cost, working as intended):**

- `WorkingContextState` (`Status`/`CurrentGoal`/`RecentDecisions`/`PendingTasks`/
  `OpenQuestions`) is a genuine, pre-computed executive summary
  (`WorkingContextState.from_buckets`, `retrieval_models.py:1732-1772`) — this is the
  "gist" layer working as designed, and it is cheap for a model to consume because
  each reference already inlines the full fact text (`[N] <fact text>`, not just
  `[N]`), so no cross-reference lookup is actually required to *use* the summary, only
  to notice it overlaps with detail below (see Q3).
- `Guidance`'s certainty/conflict/relevance rules are explicit and require no
  inference — this is exactly what a model should not have to reconstruct itself.

**Repeated (see Q3 for the full accounting):** the same fact's text appears up to
three times at three levels of detail for a `CONTINUATION` query with populated
categories — `PROJECT_STATE_EVALUATION.md` §3 already demonstrated this concretely
(`current_objective` → `WorkingContextState.current_goal` → `<Goals>` bucket member).
That analysis is correct and this document adopts it rather than re-deriving it.

**Net:** the cognitive load in this prompt is not evenly distributed. Guidance and
`WorkingContextState` are cheap; the two structurally cheapest things this prompt
could give a model for free — a human-readable section title, and one sentence
explaining `[N]` — are both currently missing, and both would eliminate real,
per-turn reconstruction work rather than merely trimming tokens.

---

## 3. Duplication

The task names `ProjectState`, `WorkingContextState`, and `RoleBuckets` explicitly.
`PROJECT_STATE_EVALUATION.md` §3 and §6 already identified and verified this
triplication with line citations; restated here at the whole-prompt level, with the
verdict this document adds:

| Pair | Why it exists | Beneficial? | Harmful? | Size vs. reasoning |
|---|---|---|---|---|
| `ProjectState` field (e.g. `CurrentObjective`) vs. `WorkingContextState` field (e.g. `CurrentGoal`) | Both are independently-derived "top-ranked `GOAL`" projections built by the *same rule* at two different layers (`project_state.py:617-638` vs. `retrieval_models.py:1748,1768`) — `ProjectState` didn't originally exist when `WorkingContextState` was designed; it was added on top (`PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md`, "Step 1's deferred renderer") without removing the now-overlapping summary it sits above. | Marginally — the two happen to always agree (same rule, same `[N]`), so there's no risk of contradiction. | Yes — for a `CONTINUATION` query, a model reads the *same one-line reference* twice before ever reaching the full evidence a third time. | Pure size cost. Both are equally terse (`[N] <fact>`) — this is not a summary sitting above a detail, it's two summaries at identical granularity sitting above one detail. Removing either loses nothing a model couldn't get from the other. |
| `WorkingContextState` field vs. `RoleBuckets` member | `WorkingContextState` is deliberately a "pure projection" gist layer (`retrieval_models.py:1702-1704`) sitting above the full `<Memory>` element with all its attributes (`confidence`, `importance`, `confirmations`, dates, decision metadata). | Yes, cleanly — this is gist-then-detail working as intended, the one duplication in this table that earns its cost. | No, not in isolation. | Justified: the gist is 1 line, the detail is 1 line + up to 9 attributes. Different information content, not a copy. |
| `ProjectState` field vs. `RoleBuckets` member | Same relationship as the row above, one level higher. | Same reasoning — orientation-layer gist above full evidence. | No, not in isolation. | Justified on its own — but see below. |

**The actual harmful duplication is specifically the middle layer.** `ProjectState`
and `WorkingContextState` are not a gist-then-detail pair with each other — they are
two gists of identical granularity, both sitting above the same one detail layer, for
no reason traceable to when either was designed to solve a different problem than the
other now happens to overlap with. `PROJECT_STATE_EVALUATION.md`'s recommendation #4
(suppress `WorkingContextState`'s summary fields when `ProjectState` is already present
in the same rendered prompt) is the correct fix and this document reaffirms it as the
single highest-value duplication removal available — it removes exactly the row that
adds size without adding reasoning power, and leaves the two rows that are genuinely
gist-then-detail untouched.

One duplication the task's examples don't name, worth flagging separately: **`Guidance`
is static, repeated verbatim on every single prompt**, including ones with an empty
`<HavenContext>`. This is not informational duplication in the "same fact stated
twice" sense — it's fixed framing overhead, not a derived value computed twice — but
it is real, constant per-call token cost with the same content every time, which is
worth naming precisely because Q8's scaling question is about content that grows;
`Guidance`'s cost does not grow, but it also never shrinks even when the rest of the
prompt is empty.

---

## 4. Missing orientation

Assume an LLM has forgotten everything. In the first 30 seconds, it wants, roughly in
this order: *what is this thing → what state is it in → what was I about to do → what's
blocking/constraining me → what did I already decide → what's half-done*
(`PROJECT_STATE_EVALUATION.md` §5 derives the same ordering from first principles and
this document agrees with it). Comparing that ideal against what the current prompt
can deterministically provide today:

| Ideal orientation need | Currently present? |
|---|---|
| What is this project (identity) | **No.** `identity` is `INFERRED`-only in the design and not implemented in `project_state.py` at all — not a present-but-empty field, absent from the dataclass entirely (`project_state.py:68-76`, confirmed against the actual field list at `project_state.py:377-386`). |
| What phase it's in | **No.** Same status as identity. |
| What I was about to do | **Weak, conditional.** `current_objective` exists but only renders for `CONTINUATION`-classified queries, and even then is sourced incidentally (§1c of `PROJECT_STATE_EVALUATION.md`) rather than goal-directed. |
| A human-readable label for *any* grouping | **Partially missing.** `ProjectState` has none (it isn't scoped to a concept). `WorkingContext` has one in principle (`title`) but it renders as a raw UUID for every `TOPIC`-kind context (§0) — the cheapest possible orientation signal is present in the schema but empty of content in practice. |
| Blockers / constraints | **Present, trustworthy, `MEMORY_DIRECT`, never-inferred** — this is the one need the current design serves well (`PROJECT_STATE_EVALUATION.md` §2). |
| A wall-clock anchor ("as of when is this snapshot") | **Absent from the rendered prompt entirely.** `ProjectState.generated_at` is computed (`project_state.py:388`) but deliberately not rendered — `PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md` states explicitly, "`generated_at` is not rendered — a diagnostic timestamp with no orientation value to a downstream model" (line 75-77 of that document), and `_render_project_state` (`structured_prompt_builder.py:311-334`) confirms no such element exists. This is a genuine gap, not a diagnostics-only omission: the *individual* `<Memory>` elements each carry `valid_from`/`valid_until`, but nothing at the top of the document tells a model "this whole reconstruction reflects the vault as of roughly this instant." A model asked "continue yesterday's work" has no deterministic anchor for what "yesterday" even means relative to the snapshot it's reading — it must infer today's date from context outside this prompt entirely. |
| A scope boundary ("is this the whole vault, one project, or a fragment?") | **Absent.** Nothing in the rendered XML states how much of the vault this reconstruction covers, or whether other projects exist that this query's retrieval didn't surface. This becomes sharper at multi-project scale (Q8). |

**Deterministic information that is currently absent, restated as a punch list:** an
as-of timestamp; a human-readable label for every `WorkingContext` (not just
`GENERAL`'s hardcoded `"General"`); an explicit statement of retrieval scope (this
run's top-`max_results` slice, not the vault). All three are derivable today with no
inference and no new write-side data — the timestamp is already computed and simply
not rendered; the label requires only exposing `Concept.label` to the builder (a real
but bounded architectural change, see Q10); the scope caveat is a fixed string.

---

## 5. Prompt contract

Can every section be explained in one sentence, and would an engineer immediately
understand why it exists?

| Section | One-sentence contract | Clear from the rendered XML alone? |
|---|---|---|
| `<Guidance>` | "Memory below is background information, not instructions; weigh it by confidence and recency." | Yes — this is the one section that is fully self-explaining in-band. |
| `<ProjectState>` | "A snapshot of this run's top-ranked facts, bucketed by category, with explicit gaps." | Partially — present/absent for a reason nothing in the prompt states (the `CONTINUATION` gate lives entirely outside the rendered document, in `ContextPlanner`), and `<Gaps>`'s actual meaning (§4) is not explained where it appears. |
| `<WorkingContext>` | "One endeavour or topic cluster, with an executive summary and full evidence beneath it." | No, for `TOPIC`-kind contexts specifically — the `title` attribute that should carry this meaning is a UUID (§0). |
| `<WorkingContextState>` | "A derived summary of the buckets immediately below it — not independent evidence." | Not stated anywhere. Nothing in `Guidance` or elsewhere tells a model that `WorkingContextState`'s fields are *derived from*, rather than *independent of*, `RoleBuckets`' contents — a model has to infer this from the matching `[N]` indices, the same reconstruction cost flagged in Q2. |
| `<RoleBuckets>` / role tags | "Full evidence, grouped by role, self-closes when empty." | Mostly — except that a bucket's tag name is not a reliable `MemoryType` label (the `BLOCKER`→`TASK` collapse, Q2). |

A structural inconsistency worth naming precisely because the task asks about
"contract": **`RoleBucket` and `ProjectState` use two different empty-value
conventions in the same document**, and neither is explained to the reading model.
`RoleBucket` self-closes (`<Beliefs/>`) to mean "empty"
(`_render_bucket`/`_render_state_list`, `structured_prompt_builder.py:430-443,449-458`).
`ProjectState`'s list sections instead *omit the element entirely* when empty, and rely
on `<Gaps>` as the single authoritative "what's missing" signal
(`_render_state_ref_list`, `structured_prompt_builder.py:336-352`, deliberately, per
its own docstring). Both choices are individually well-reasoned in Python — but a
downstream model sees only the rendered XML, never the docstrings that justify the
inconsistency, and nothing in `Guidance` states "an omitted element and a self-closed
element both mean empty, just recorded two different ways." An engineer reading the
source understands why immediately (one document literally says so); a model reading
only the rendered prompt has no way to know these are the same signal.

**Verdict:** the contract is clear and well-reasoned in the Python source. It does not
uniformly reach the rendered document itself — several of the rules that make the XML
sensible (the `[N]` convention, the two empty-value conventions, what `ProjectState`
confidence measures, `Gaps`' actual scope) live only in code comments a downstream LLM
never sees.

---

## 6. Trust and provenance

**What the prompt does well:** `Guidance` explicitly states that confidence should
govern certainty, that higher-confidence/more-recent memories win on conflict, and
that contradictions should be surfaced rather than guessed
(`_GUIDANCE_LINES`, `structured_prompt_builder.py:161-165`). Every populated field
traces to a concrete `<Memory>` element via the shared `[N]` index
(`PROJECT_STATE_EVALUATION.md` §8 verified this holds even under shuffled input order).
This part is genuinely sound.

**Where it falls short, all concrete and checkable:**

- **Two different `confidence` semantics, same attribute name, no in-band
  disambiguation.** `<ProjectState confidence="0.62">` is a completeness fraction —
  `(8 - len(gaps)) / 8` (`project_state.py:598-600`) — while every `<Memory
  confidence="...">` is a per-fact certainty score (`structured_prompt_builder.py:479-480`).
  `Guidance` tells the model to weigh *memory* confidence, correctly, but never warns
  that `<ProjectState>`'s own `confidence` attribute is a different kind of number
  entirely. A model treating "0.62 confidence" on the whole `ProjectState` block the
  same way it treats a low-confidence memory would be reasoning about the wrong thing.
- **`WorkingContextState` is never labeled as derived.** As in Q5 — a model has no
  stated rule for "this summary field is a pointer to evidence you'll see again below,
  not a second, independent claim." The shared `[N]` index makes this discoverable by
  a careful reader, but "discoverable by inference" is not the same as "the contract
  states it."
- **`Gaps`' scope is undocumented in-band.** As in Q2/Q4 — the honest caveat
  ("this run's reconstruction," not the vault) exists only in `project_state.py`'s
  module docstring, never in `Guidance` or near the `<Gaps>` element itself.
- **No authority ranking between `ProjectState` and `WorkingContext` content is
  stated**, though in practice there's nothing to rank (§3 established they mostly
  agree by construction) — worth naming only because if the two ever *did* diverge
  (e.g. a future gap-fill path populates one and not the other), nothing in `Guidance`
  tells the model which one to trust more.

**Verdict:** provenance (which memory backs which claim) is well-engineered and
verified. Confidence *interpretation* is not uniformly clear — the prompt asks a model
to reason about "confidence" as one concept while actually encoding two, distinguished
only by which XML level the attribute appears at, a distinction nowhere stated.

---

## 7. Continuation quality

Evaluated against the task's four example requests, checked against the actual
`TaskMode` lexical classifier (`context_planner.py:484-536, 598-611`) rather than
assumed:

- **"Continue implementing Haven."** Matches the substring `"continue"` →
  `TaskMode.CONTINUATION` (`context_planner.py:488`, and the classifier's own comment
  at lines 478-483 explicitly calls out this exact phrase as the reason `CONTINUATION`
  is checked before `CODING_DEBUGGING`, since `"implement"` alone would otherwise match
  the latter). **Well supported** — gets the full `ProjectState` orientation layer,
  subject to the quality caveats `PROJECT_STATE_EVALUATION.md` §1 already documented
  (plan-blind ranking, no identity/phase).
- **"Continue yesterday's work."** Also matches `"continue"` → `CONTINUATION`. **Well
  supported for retrieval routing**, but "yesterday" itself is not something the prompt
  can ground — per Q4, there is no as-of timestamp anywhere in the rendered document,
  so a model cannot deterministically reason about what "yesterday" means relative to
  the snapshot it's reading; it can only use each `<Memory>`'s own `valid_from`, one
  fact at a time, with no document-level "now" to compare against.
- **"Where were we?"** Matches the exact pattern `"where were we"`
  (`context_planner.py:489`) → `CONTINUATION`. **Well supported for routing**, but this
  is also the shortest, most semantically underspecified query of the four — its
  retrieval quality depends entirely on whatever the upstream retriever does with three
  words, which is out of this document's scope (retrieval is explicitly excluded from
  this review). The prompt-construction layer itself does its job once results arrive;
  it cannot compensate for a query with almost no lexical signal to retrieve against.
- **"What should I do next?"** **Not well supported, and verifiably so.** Checked
  against every pattern table in `context_planner.py:484-536`: it matches none of
  `CONTINUATION`'s eight patterns, none of `CODING_DEBUGGING`'s eleven, none of
  `STRUCTURING`'s six, none of `RESEARCH`'s five. `_classify` falls through to
  `TaskMode.POINTED_QA` (`context_planner.py:611`) — the same classification as an
  unrelated one-off factual question. `query_structured` only builds a `ProjectState`
  when `task_mode is TaskMode.CONTINUATION` (`engine.py:1206-1208`); for `POINTED_QA`
  it stays `None` and no `<ProjectState>` element renders at all
  (`structured_prompt_builder.py:270-271`, confirmed byte-identical to omitting the
  parameter). **The single request in this task's own example list that most directly
  asks for orientation ("what should I do next") is the one request the current
  architecture is verified to route around the orientation layer entirely,** landing
  it in the exact same no-`ProjectState` path as an unrelated pointed question. Even
  where `ProjectState` is absent, the model still gets `WorkingContext`'s per-topic
  detail (whatever `_allocate` — plan-blind, per `engine.py:1103-1114` — happened to
  rank highest), so this is a degraded answer, not a broken one, but it is a real,
  concrete case of the classification gate failing the exact use case the task asks
  about.

**Summary:** three of four example requests are well routed to the orientation layer;
the fourth is a documented, verifiable gap in the lexical classifier's coverage, not a
matter of retrieval quality. All four inherit the weaknesses already established in
Q1–Q6 once they do reach `ProjectState` (declaration-order sections, opaque
`WorkingContext` titles, no timestamp anchor, `active_tasks`/`implementation_state`
thinness per `PROJECT_STATE_EVALUATION.md` §3).

---

## 8. Scaling

At 500 conversations / 10,000 memories / multiple concurrent projects, three effects
compound beyond what `PROJECT_STATE_EVALUATION.md` §7 already established for
single-project scaling (content-quality degradation of a flat top-K ranking, not a
cost blowup):

- **Cross-project bleed becomes a real risk, not a hypothetical.** `ProjectState` has
  no `project_key`/scoping dimension in Phase A (confirmed absent from the dataclass,
  `project_state.py:311-388`) — `PROJECT_STATE_DESIGN.md` §7 already names multi-project
  scoping as unbuilt, "even though Haven's own vault today has effectively one
  project." With multiple concurrent projects, `ProjectStateBuilder.build` still
  buckets across the *entire* `allocated` list with no project boundary
  (`project_state.py:547-576`) — meaning a `current_objective` or `blockers` entry for
  "Project A" can, in principle, actually be a Project B fact that simply outranked
  Project A's own candidates in the flat top-50. Nothing in `ProjectState` or its
  rendering would reveal this has happened; `<Gaps>` would look identical either way.
  This is the multi-project-specific sharpening of the same "flat top-K, no category
  balance" root cause `PROJECT_STATE_EVALUATION.md` §7 already diagnosed for the
  single-project case.
- **`WorkingContext` count scales with concept diversity, and each context pays fixed
  scaffolding regardless of size.** Grouping is one context per distinct primary
  concept among the allocated slice (`working_context_builder.py:141-161`), bounded
  above by `max_results` (default 50) but not bounded below — a near-singleton context
  (one candidate, one concept) still renders a full `<WorkingContextState>` skeleton
  (`Status`, `CurrentGoal`, `RecentDecisions`, `PendingTasks`, `OpenQuestions` — several
  self-closing) plus all seven `<RoleBuckets>` role tags, most self-closing
  (`_render_context`/`_render_bucket`, `structured_prompt_builder.py:394-409,449-463`).
  As a 10,000-memory vault yields more concept diversity per query, the
  scaffolding-to-content ratio gets worse — more sections, each thinner, each still
  paying the same ~12-line fixed tag overhead. This is the section that becomes noisy
  first: not because any single piece of content grows, but because the number of
  near-empty structural wrappers grows with vault diversity while `ProjectState` stays
  bounded at 8 fields regardless of scale.
- **The UUID-title problem (§0) compounds with context count.** At low scale, a model
  can plausibly infer a `TOPIC` context's subject from context clues even without a
  label, especially if there's only one or two such sections. At high concept
  diversity, a model facing a dozen UUID-titled sections in one prompt has no cheap way
  to decide which to read first or skip — the ordering is already an arbitrary UUID
  sort (Q1), and now there are more of them to sort through.

**Conclusion:** consistent with `PROJECT_STATE_EVALUATION.md` §7's finding that Phase
A is cheap but degrades in *content quality* as the vault grows — this document adds
that the `WorkingContext` section (untouched by that prior evaluation, which focused
on `ProjectState`) degrades in *structural noise* on the same growth axis, and that
multi-project use turns the existing "flat top-K, no category balance" weakness into a
correctness risk (cross-project attribution), not just a coverage one.

---

## 9. Comparison

**A. Flat list of retrieved memories.** This is not hypothetical — it is
`ContextBuilder`'s actual, currently-shipping output (`context_builder.py`), used by
`MemoryEngine.query`. One `[N]` block per candidate, six fixed fields, joined by blank
lines, in input rank order, no grouping, no hierarchy (`context_builder.py:135-157,
159-185`). *Strengths:* every fact appears exactly once — zero duplication of any
kind; no scaffolding tax; trivial to reason about; nothing to misinterpret about
structure because there is none. *Weaknesses:* zero orientation — no gist, no
grouping, no "what's blocking me" section; the model does 100% of the reconstruction
work Q2 catalogued as currently uneven in B; ordering is purely rank order, with no
concept of "read this part first for the big picture."

**B. Current Haven prompt** (this document's subject). *Strengths:* real hierarchy
(orientation → per-topic detail), a genuine gist layer (`WorkingContextState`), shared
indexing that keeps cross-references consistent, explicit certainty/conflict framing.
*Weaknesses,* all established above: the orientation layer is conditionally present
(Q1, Q7) rather than a reliable floor; near-duplicate gist layers at the same
granularity (Q3); missing identity/phase/timestamp/scope (Q4); undocumented in-band
conventions (`[N]`, two empty-value styles, two `confidence` meanings — Q5, Q6); opaque
`WorkingContext` titles (§0); structural-noise growth at scale (Q8) that A does not
suffer from (A has no scaffolding to bloat) but also does not solve (A never had
orientation to begin with).

**C. An ideal deterministic continuation prompt** (described, not designed — per this
task's constraints). Its defining properties, derived from where B already succeeds
and where it doesn't: a single orientation block that is *always* present, not gated
on lexical query classification; ordered by actual human-resumption priority
(identity/phase → objective → blockers/constraints → decisions → in-progress → gaps),
not dataclass declaration order; every fact rendered exactly once, referenced rather
than restated at every layer that needs to mention it; every non-obvious convention
(index sharing, empty-value meaning, what a confidence number measures) explained once,
in-band, near where it's first used; an explicit as-of anchor and an explicit scope
statement (this run's slice, not the vault); every grouping carrying a human-readable
label, never a raw identifier; and a structural cost that does not grow merely because
the vault's concept diversity does. B is much closer to C than A is — it already has
the right two-tier shape and the right framing rules — but it falls short of C
specifically on: conditional presence, gist-layer duplication, missing timestamp/scope/
labels, and undocumented in-band conventions. None of those are retrieval problems;
all of them are renderer- and routing-level gaps in B as it exists today.

---

## 10. Recommendations

Ranked by continuation-quality impact, prompt simplicity, implementation effort, and
architectural risk. Items already recommended in `PROJECT_STATE_EVALUATION.md` are
marked *(reaffirmed)* and cited rather than re-derived; this document's own additions
are unmarked.

### Quick Wins (trivial, additive, no risk)

1. **Done (2026-07-10).** Added one `Guidance` bullet explaining the `[N]` convention
   ("the same `[N]` denotes the same underlying memory everywhere it appears in this
   document"). Removes an entire class of per-turn reconstruction (Q2, Q5, Q6) for the
   cost of three lines, rendered on every prompt since `Guidance` is unconditional.
2. Reorder `<ProjectState>`'s list sections so `Blockers`/`Constraints` render
   immediately after `CurrentObjective`, before `Decisions`; move `SupersededDecisions`
   to just before `Gaps` *(reaffirmed, `PROJECT_STATE_EVALUATION.md` §9 #1)*. Not part
   of this pass's four candidates — still open.
3. Disambiguate the `<ProjectState confidence="...">` attribute from `<Memory
   confidence="...">` — rename in the rendered XML (e.g. `field_coverage`) or add a
   one-line `Guidance` caveat *(reaffirmed, §9 #5)*. Not part of this pass's four
   candidates — still open.
4. Add a fixed caveat near `<Gaps>` (or in `Guidance`) stating it reflects "this run's
   retrieval," not the vault *(reaffirmed, §9's rendering-clarification note in §6)*.
   Not part of this pass's four candidates — still open.
5. **Done (2026-07-10), as a drive-by while candidate 2 below touched the same
   method.** Fixed the stale `query_structured` docstring claiming `<ProjectState>`
   isn't rendered — it already was, since the Step 2 renderer landed
   *(reaffirmed, §9 #3)*.

### Medium Improvements (real code change, additive, still no persistence/inference)

1. Suppress `WorkingContextState`'s summary fields when `ProjectState` is already
   present in the same rendered prompt, removing the one genuinely harmful duplication
   identified in Q3 *(reaffirmed, §9 #4)*. Not part of this pass's four candidates —
   still open.
2. **Done (2026-07-10), via a narrower mechanism than originally sketched here.**
   `TOPIC`-kind `WorkingContext` titles now resolve `concept_id` to `Concept.label`
   where the graph has one, instead of rendering the raw UUID (§0). Rather than giving
   `WorkingContextBuilder` itself read access to `ConceptGraph` — which its docstring
   and `TestNoOutOfScopeImports` explicitly rule out — the resolution runs as a
   post-processing step in `MemoryEngine` (`_resolve_topic_titles`), applied
   identically by `query_working_context()` and `query_structured()` so the two never
   diverge. This preserves `WorkingContextBuilder`'s tested isolation from
   `ConceptGraph` entirely; only `MemoryEngine`, which already held a `ConceptGraph`
   reference, gained one new read-only lookup per context. See
   `obsidian/tests/test_engine.py::TestTopicTitleResolution`.
3. **Rejected as a Quick Win; left exactly as scoped here.** Render an explicit as-of
   timestamp at the top of `<HavenContext>` (reusing `ProjectState.generated_at`,
   already computed but currently withheld per
   `PROJECT_STATE_WORKING_CONTEXT_INTEGRATION.md`'s explicit "no orientation value"
   judgment call). This document's Q4/Q7 findings (no anchor for "yesterday," no
   anchor for recency reasoning generally) argue that judgment call should be revisited
   deliberately, not reversed by default — worth a scoped decision, not a blanket
   reinstatement, and therefore not attempted in the 2026-07-10 Quick Wins pass.

### Long-Term Ideas (cross persistence/plan-awareness/routing boundaries — name, don't design)

1. **Evaluated for the 2026-07-10 Quick Wins pass and rejected as such.** Make some
   orientation layer available regardless of lexical `TaskMode` classification, rather
   than gated on `CONTINUATION` alone. Q7 verified a concrete failure: "What should I
   do next?" — arguably the most orientation-seeking phrasing possible — currently
   classifies as `POINTED_QA` and receives no `ProjectState` at all. The only
   deterministic way to make orientation a floor is to stop gating `<ProjectState>` on
   `TaskMode.CONTINUATION` in `query_structured()` — which necessarily changes
   `POINTED_QA`'s rendered output (today verified byte-identical to omitting
   `project_state`; a floor gives it a `<ProjectState>` element too). That is a direct,
   unavoidable behavior change to the most common task mode, so this cannot be "low
   risk and does not affect `POINTED_QA`" by construction — it stays future work,
   needing its own review of the classification/routing decision, not a renderer
   change.
2. Multi-project scoping (`project_key`) before "multiple concurrent projects" (Q8) can
   be answered safely — today a flat top-K ranking has no project boundary and can, in
   principle, blend two projects' facts into one `ProjectState` with no signal that it
   happened. Already flagged as major, deliberately-deferred risk in
   `PROJECT_STATE_DESIGN.md` §7; this document's Q8 sharpens it from a coverage concern
   into a correctness one at true multi-project scale.
3. `identity`/`phase` fields (`INFERRED`) — already correctly gated behind Phase A/B
   being solid *(reaffirmed, `PROJECT_STATE_EVALUATION.md` §9, roadmap)*. This document
   reaffirms it as the single most human-prioritized piece of orientation still absent
   (Q4), while agreeing it should stay deferred until the deterministic layers below it
   are trustworthy.
4. Wire `CategoryPreferenceScorer`/plan-awareness into the ranking that actually feeds
   `query_structured` (currently "deliberately not planner-aware,"
   `engine.py:1103-1114`) *(reaffirmed, `PROJECT_STATE_EVALUATION.md` §9 #7, its own
   highest-leverage/highest-risk item)*. This document's Q7 shows this is the
   underlying cause of `active_tasks`/`implementation_state`/`code_areas` being thin
   exactly where "what should I do next?"-style continuation would most want them
   populated.

---

## 11. Bottom line

The current prompt is not a retrieval pipeline's internals dumped verbatim — `Guidance`,
the `[N]` indexing scheme, and the `ProjectState`-before-`WorkingContext` ordering are
real, deliberate cognitive-load reductions, and they work. But three structural facts
keep it from being a reliably optimized continuation prompt rather than a well-annotated
one: **the orientation layer is conditional on a five-way lexical guess, not a floor**
(Q1, Q7, verified to fail on a plausible continuation phrasing); **the layer directly
below it partially duplicates it at the same granularity rather than adding detail**
(Q3); and **the cheapest possible orientation signal available anywhere in the
document — a section's own name — is, for the most common context kind, an opaque
UUID** (§0, recurring through Q1/Q4/Q5/Q8). None of these require inference,
persistence, or a redesign to fix — they are the Quick Wins and Medium Improvements
above, in that order of leverage.
