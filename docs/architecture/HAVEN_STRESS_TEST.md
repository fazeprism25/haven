# Haven Stress Test — 55 Hostile User Scenarios

Status: **Analysis only. No code changed.** This document assumes the current
working tree (committed baseline `042e16e6` plus the large uncommitted delta
already present: `ContextPlanner`, `CoverageAnalyzer`, `GapRecovery`,
`ProjectState`, `WorkingContextBuilder`, `StructuredPromptBuilder`,
`CategoryPreferenceScorer`, the live `ManagerAILLM`/`Classifier` binding,
Memory Review) is "the finished hackathon build," per instructions. Every
claim about behavior below is grounded in a direct read of the source file
named, not inferred from a design doc's stated intent — where a design doc
and the code disagreed, the code won and the doc is cited only as
corroborating context. No new benchmark categories, no architecture changes,
no implementation.

Sources read in full: `obsidian/memory_engine/{engine,context_planner,
coverage_analyzer,gap_recovery,project_state,acceptance_stage,
deterministic_ranker,deterministic_slot_allocator,working_context_builder,
structured_prompt_builder}.py`, `obsidian/manager_ai/{canonical_matcher,
knowledge_updater,classifier}.py`, `docs/architecture/{PRE_BENCHMARK_FREEZE_
AUDIT,GENERIC_CONTINUATION_QUERY_ANALYSIS,STAGE_3_4_SUPERSEDE_INVESTIGATION,
PROJECT_STATE_EVALUATION,PROMPT_CONTINUATION_EVALUATION,CONTINUATION_
BENCHMARK_AUDIT}.md`, plus a directory census of `benchmarks/datasets/` and
`benchmarks/datasets_continuation/`.

---

## Part 1 — The 17 mechanisms every scenario below traces back to

Reading scenario-by-scenario without this section would make each one look
like an isolated anecdote. It isn't — nearly all 55 scenarios below reduce to
combinations of these 17 already-verified, already-documented mechanisms.
Cited as **M1**–**M17** throughout.

| # | Mechanism | Verified where |
|---|---|---|
| M1 | `ContextPlanner` classifies task mode by **pure substring match** against a fixed, ordered pattern table (`CONTINUATION` → `CODING_DEBUGGING` → `STRUCTURING` → `RESEARCH` → `POINTED_QA` default), first match wins. No semantics, no negation handling, no word-boundary check. | `context_planner.py:484-619` |
| M2 | `CoverageReport`, `GapRecoveryDecision`, and `ProjectState` are computed on **every** `query_with_trace()` call but are **100% diagnostics-only** — attached to the trace, never read back to retry retrieval, change acceptance, or alter the returned context/prompt. A confidently-flagged gap (`should_retry=True`) is silently discarded. | `engine.py` module docstring "Explicitly out of scope", `gap_recovery.py:93-97` |
| M3 | `CanonicalMatcher` **never automatically returns `SUPERSEDE`** — only `NEW`/`CONFIRM`/(strict-prefix-only) `UPDATE`. A contradiction always becomes a brand-new, unlinked `KnowledgeObject`; the old one stays `valid_until=None` (active) forever unless a human manually calls `supersede_decision()`. | `canonical_matcher.py:50-54`, confirmed dead-code by `STAGE_3_4_SUPERSEDE_INVESTIGATION.md:34-38` |
| M4 | The one retrieval-time compensation for lexically-empty queries (**category fallback**) only fires when task mode is `CONTINUATION` **and** normal acceptance found **literally zero** candidates. One stray accepted candidate suppresses it entirely. | `engine.py:863-880` |
| M5 | `ProjectState` has **no persistence and no cross-query memory** — rebuilt from scratch from *this call's* top-50 ranked slice every time. Two calls five minutes apart, or two phrasings of the same question, can report a different `current_objective` with no contradiction ever surfaced. | `project_state.py` module docstring, `PROJECT_STATE_EVALUATION.md` §7 |
| M6 | `DeterministicSlotAllocator` is a **flat top-K across every category combined** (default `max_results=50`), no per-category reservation. High-volume categories (many small `TASK`/`FACT` memories) can crowd out rare, high-value ones (`BLOCKER`, `RULE`) purely on volume. | `deterministic_slot_allocator.py:37-64`, `PROJECT_STATE_EVALUATION.md` §7 |
| M7 | Recency scoring uses a **7-day half-life** (`RECENCY_SCALE_DAYS=7.0`) with no floor — a month-old fact's recency contribution drops to ~0.19 of its fresh value, which can push an otherwise-good candidate under the abstention/relative-floor thresholds. | `deterministic_ranker.py:144-149,270` |
| M8 | 5 of 19 benchmark dataset categories are **completely empty** (`active_context`, `insights`, `memory_recall`, `mistake_prevention`, `open_problems` — 0 files, silently skipped, no warning). `people`/`projects`/`recurring` have only 3–4 files each. | live census: `find benchmarks/datasets/*/ -name '*.json' | wc -l`, corroborated by `PRE_BENCHMARK_FREEZE_AUDIT.md` §2 |
| M9 | Both candidate-generation paths (`QueryResolver` ontology lookup, `KeywordCandidateRetriever` keyword match) require **literal lexical or alias overlap** with the query — no fuzzy matching, no embeddings, by explicit design. A content-free query ("Continue.") is *structurally guaranteed* zero candidates, independent of vault size or content. | `GENERIC_CONTINUATION_QUERY_ANALYSIS.md` §2, verified against `keyword_candidate_retriever.py`/`query_resolver.py` docstrings |
| M10 | Nothing in the rendered prompt states an **"as of" timestamp** or a **scope boundary** ("this is the whole vault vs. a fragment"). `ProjectState.generated_at` is computed but deliberately never rendered. | `PROMPT_CONTINUATION_EVALUATION.md` §4, `structured_prompt_builder.py` (`_render_project_state` has no timestamp) |
| M11 | The same fact can render **up to three times** in one `CONTINUATION`-mode prompt at identical granularity (`ProjectState` field → `WorkingContextState` summary → full `<Memory>` in `RoleBuckets`) — pure token-budget cost, no added reasoning power. | `PROJECT_STATE_EVALUATION.md` §3, `PROMPT_CONTINUATION_EVALUATION.md` §3 |
| M12 | A `TOPIC`-kind `WorkingContext`'s grouping key is the single **highest-activation** supporting concept only — a fact spanning two concepts (e.g. "chose Terraform over Ansible") anchors to whichever wins the tie-break, splitting semantically-linked facts into different sections. Section *ordering* across contexts is a raw UUID sort, not a relevance sort. | `working_context_builder.py:176-190`, `PROMPT_CONTINUATION_EVALUATION.md` §1 |
| M13 | `QueryRewriter` (LLM-based query expansion, fails open) exists, is fully tested, and is **never instantiated at any real call site** in `server/main.py`/`server/dashboard.py` — every production `MemoryEngine` runs with `query_rewriter=None`. | `GENERIC_CONTINUATION_QUERY_ANALYSIS.md` §3 table row 2, confirmed against `server/main.py` construction sites |
| M14 | `ContextCategory`/coverage tracking has **no entry at all** for `MemoryType.GOAL`, `PROJECT`, `PERSON`, `EVENT`, `SKILL`, `PREFERENCE` — these can never be a "required category," never show up as a gap, and (for `GOAL` specifically) are outside `ContextPlanner`'s requirement tables even though `current_objective` is sourced from `GOAL`. | `coverage_analyzer.py:105-122`, `PROJECT_STATE_EVALUATION.md` §1(c) |
| M15 | `ProjectState` has **no project-scoping dimension** — in a multi-project vault, a "current objective" or "blocker" surfaced for the query asked could actually belong to a different project that simply outranked the relevant one in the flat top-50. Nothing in the output would reveal this happened. | `PROMPT_CONTINUATION_EVALUATION.md` §8, `project_state.py:311-388` (no `project_key` field) |
| M16 | `AcceptanceStage`'s **abstention** rule is whole-query: if the single best candidate scores below `0.25`, *nothing* is returned even if several good candidates sit just below it. The **score-gap cut** can also silently truncate a legitimately relevant tail if one gap in the first 10 positions exceeds `0.04`. | `acceptance_stage.py:99-148,336-343` |
| M17 | Write-side classification (`DECISION`/`TASK`/`BLOCKER`/`RULE` typing) now runs through a **live LLM** (`ManagerAILLM`, `server/main.py:1016-1019`) — meaning `ProjectState`'s usefulness in real (non-benchmark) usage is a *probabilistic* function of classifier accuracy per turn, not the deterministic guarantee the "MEMORY_DIRECT, never-fabricated" framing implies for the fields it does populate. | `obsidian/manager_ai/classifier.py`, `server/main.py:1016-1019` |

Two measured, quantified facts worth carrying into every scenario below:

- **`STAGE_3_4_SUPERSEDE_INVESTIGATION.md`'s oracle test**: even a
  *hypothetically perfect* auto-supersede mechanism only fixes 23 of 91
  measured stale-context failures — 32 remain broken because the surviving
  correct fact simply doesn't share vocabulary with the query (M9), and a
  naive "archive the earlier turn" heuristic would introduce **new**
  regressions on 3/82 (~3.7%) already-passing cases where a later turn
  *elaborates* or explicitly reaffirms an earlier one rather than replacing it.
- **`CONTINUATION_BENCHMARK_AUDIT.md`'s Critical-1**: the *dedicated*
  continuation benchmark (`benchmarks/datasets_continuation/`) ingests every
  turn as generic `MemoryType.FACT` (no classifier in that adapter path), so
  `ProjectState`'s actual reconstruction logic — the thing the benchmark
  exists to test — never fires on any of its 10 pilot cases today. Any
  Haven-vs-baseline number from that benchmark, if run right now, measures
  ranking/grouping quality, not reconstruction quality.

---

## Part 2 — 55 scenarios

Each entry: conversation shape → what a human would expect → what the code
says will actually happen → the weakness → severity → classification.
Severity is about **user-visible harm to trust in Haven as a memory system**,
not code-quality. Classification uses the categories from the task:
*benchmark issue / retrieval issue / prompt issue / planner issue /
ProjectState issue / architecture limitation*.

### A. Continuing a long software project

**S1 — "Continue implementing Haven."**
Textbook continuation query. Human expects: current objective, open
blockers, recent decisions, what's half-built. Haven: `ContextPlanner`
correctly classifies `CONTINUATION` (M1); if any real candidates retrieve,
`ProjectState` renders — but `implementation_state`/`code_areas` are the
weakest-populated fields because neither is in `CONTINUATION`'s requirement
table at all, and even if it were, requirement-table membership never
reaches ranking for this call path (`_allocate` is deliberately
planner-blind, M6/M14 interaction). The user gets decisions/blockers
reliably, "what's half-built" unreliably. **Severity: Medium. Classification:
ProjectState issue / planner issue.**

**S2 — "Continue." (single word, nothing else)**
Human expects Haven to infer intent from history. Haven: guaranteed zero
candidates from both retrieval paths (M9) — not probabilistic, structural.
`ContextPlanner` gets the classification right (`CONTINUATION`) but there is
nothing to classify *into*. The category-fallback path (M4) will fire here
specifically because zero candidates is exactly its trigger condition, so in
practice this exact single-word case is one of the *better*-handled ones —
but only because it's the cleanest possible trigger for M4, not because the
system "understood" the query. **Severity: Low (mitigated by M4) but fragile
— any lexical noise at all changes the outcome (see S3). Classification:
retrieval issue.**

**S3 — "Continue, but focus on the API work."**
One real content word ("API") added to S2's shape. Human expects: same
orientation as S2, filtered to the API sub-thread. Haven: the keyword path
now has something to match, so it's plausible 1–2 candidates get accepted
normally — which **disables the M4 fallback entirely** (M4 only fires on
zero accepted candidates), leaving the user with a thin 1–2-item context and
none of S2's fallback-driven `ProjectState` fill-in. Counterintuitively, the
*more* specific query produces the *less* complete orientation. **Severity:
Medium. Classification: retrieval issue / ProjectState issue.**

**S4 — "Where did I leave off with the ranking tier-3 revision-marker idea?"**
Human expects a precise pointer to the exact abandoned/deferred sub-thread.
Haven: classifies as `POINTED_QA` (no continuation pattern matches "where
did I leave off with X") — no `ProjectState`, no orientation layer at all
(M1), degrading straight to `WorkingContext`'s flat per-topic detail with no
"this was deferred, not abandoned" signal, since Haven has no concept of
task/decision *status* beyond `DecisionStatus.SUPERSEDED` (decisions only,
not tasks or ideas). **Severity: Medium. Classification: planner issue.**

**S5 — Two-week gap, then "let's keep going"**
"Keep going" is in `ContextPlanner`'s `CONTINUATION` pattern list (M1), so
classification is correct. But everything written before the gap has decayed
under the 7-day recency half-life (M7) relative to anything written more
recently on an unrelated topic — if the user did *anything* else memory-worthy
in the interim, those newer, unrelated facts can out-rank the two-week-old
project state in the flat top-50 (M6), especially once M4's fallback is
suppressed by even one accepted candidate (S3's dynamic). **Severity: High —
this is exactly the "returning after time away" case Haven is meant to solve
well. Classification: retrieval issue / architecture limitation.**

**S6 — "Continue implementing the OAuth flow" when the user actually finished
OAuth three weeks ago and is now on billing**
Human expects Haven to recognize OAuth is done and redirect. Haven has no
mechanism to detect "this objective is stale" — `current_objective` is
whichever top-ranked `GOAL`-typed candidate wins this call's ranking (M5),
with no notion of goal completion/closure at all (no `MemoryType` or status
value represents "done"). If the OAuth goal candidate still has decent
recency/importance, it can resurface as if still current, actively
misdirecting the session. **Severity: High. Classification: ProjectState
issue / architecture limitation.**

### B. Returning after a long absence

**S7 — Returning after exactly one month, generic "what was I working on?"**
Doesn't match any `_MODE_PATTERNS` entry (checked directly: no `continue`/
`where were we`/etc. substring) → falls to `POINTED_QA`, **no ProjectState
at all** (M1, same failure class as the design docs' own worked example,
"What should I do next?"). The single most orientation-seeking phrasing in
this scenario family is the one the classifier is verified to route away
from the orientation layer. **Severity: High. Classification: planner
issue.**

**S8 — Returning after 6 months, vault has grown to hundreds of memories**
Human expects Haven to summarize the arc, not drown in noise. Haven has no
notion of "session" or "absence duration" anywhere — recency scoring treats
every fact identically regardless of when the *user* was last active, only
how old the fact itself is (M7). A vault with months of unrelated activity
produces `WorkingContext` sprawl (M12: one context per distinct concept, no
lower bound) with UUID-sorted section order — the returning user gets a
wall of same-weight topic clusters in an arbitrary order, not "here's what
changed since you left." **Severity: High. Classification: architecture
limitation.**

**S9 — "Remind me what Haven even is"**
Genuinely first-orientation question. `identity`/`phase` fields are
`INFERRED`-only and **not implemented at all** — not present-but-empty, absent
from the dataclass (`PROJECT_STATE_EVALUATION.md` §1(c)/§5). Haven
structurally cannot answer "what is this project" from `ProjectState`; the
best it can do is whatever `FACT`-typed memories happen to rank highest
under `RESEARCH` category and get lucky on lexical overlap with "Haven."
**Severity: Medium (query is rare but foundational). Classification:
ProjectState issue / architecture limitation.**

**S10 — Returning user asks the exact same "where were we" question twice in
one sitting, five minutes apart**
Human expects identical or near-identical answers. Because `ProjectState`
has zero persistence or memoization (M5) and ranking has no explicit tie-break
guard beyond `str(id)` ordering for genuine ties, **the answer is
deterministic call-to-call only if the underlying data and clock second are
identical** — recency's `age_days` is computed against `datetime.utcnow()`
freshly each call, so a candidate hovering exactly at a score-gap-cut
boundary (M16) can flip sides between two calls seconds apart if enough wall
time elapses to shift the ordering by a hair. In practice unlikely to be
visible within a five-minute window, but the *architecture* provides no
guarantee against it, and it is the mechanism S27's larger scenario relies
on. **Severity: Low in isolation, flagged for completeness. Classification:
architecture limitation.**

**S11 — User's vault has 40% of memories older than the 60-day mark**
`RECENCY_SCALE_DAYS=7.0` means 60-day-old facts have `recency_raw ≈
1/(1+60/7) ≈ 0.10`. If `weight_recency` is a meaningful fraction of the
scoring weight sum, a large swath of the vault is permanently
recency-penalized regardless of how important or true it still is — Haven's
scoring has no separate "durable fact" vs. "ephemeral chatter" axis beyond
`importance`/`confidence`, which are set once at write time and rarely
revisited. **Severity: Medium. Classification: retrieval issue /
architecture limitation.**

### C. Switching between two or more active projects

**S12 — "What's blocking the API work?" asked in a vault with two unrelated
active projects, one under multiple names**
No `project_key` scoping exists anywhere in the pipeline (M15). If "API"
also loosely matches vocabulary in the other project (plausible — "API" is
generic), a blocker from the wrong project can outrank the right one in the
flat top-50 with **zero signal in the output that cross-project bleed
occurred**. `<Gaps>` would look identical whether the right blocker is
missing or a wrong one displaced it. **Severity: High. Classification:
architecture limitation / ProjectState issue.**

**S13 — User switches from "Project A" to "Project B" mid-session, then asks
a generic continuation question**
Human expects Haven to track which project is "current." Haven has no
session/focus state at all — every query is independently resolved from the
whole vault (M5, M15 combined). Switching projects produces no different
behavior than any other query; whichever project's facts rank higher wins,
irrespective of which one the user just said they were switching to.
**Severity: High. Classification: architecture limitation.**

**S14 — Two projects share a decision-relevant term ("we chose Postgres" in
both)**
`CanonicalMatcher`'s exact-text-match CONFIRM path (M3) could, in the
degenerate case, treat an identical sentence in two different projects'
conversations as the same fact being reconfirmed rather than two independent
decisions — `canonical_matcher.py`'s matching is global across the whole
vault, with no project boundary to prevent this. **Severity: Medium
(requires near-identical phrasing to trigger). Classification: retrieval
issue / architecture limitation.**

**S15 — "compare what I decided for Project A vs Project B on caching"**
No comparison/multi-scope capability exists anywhere — `query_structured`
and `query` both resolve a single flat ranked slice per call (M6). The user
gets one undifferentiated blend of both projects' caching decisions with no
labeling of which is which beyond whatever's in the raw fact text itself.
**Severity: Medium. Classification: architecture limitation.**

### D. Resuming debugging

**S16 — "This isn't working, same bug as before"**
`"isn't working"` matches `CODING_DEBUGGING` (M1) — correct. But
"CONTINUATION" is checked *before* `CODING_DEBUGGING` in the fixed table
order specifically because `"continue implementing"` would otherwise
misfire into the latter (documented intentionally, `context_planner.py:478-
483`) — meaning if the user had instead said "continuing to debug the same
issue," it would classify as `CONTINUATION` and get the 6-category
continuation treatment (heavier on decisions/tasks) rather than
`CODING_DEBUGGING`'s narrower implementation/code-area/constraint focus.
Two nearly-identical phrasings of the same intent get materially different
context shapes. **Severity: Medium. Classification: planner issue.**

**S17 — "Why is this failing again?" three days after the bug was fixed and
confirmed working**
No mechanism marks a `BLOCKER`/bug fact as resolved beyond a human manually
triggering `supersede_decision` (M3, scoped to `DECISION` type only —
`BLOCKER` has no equivalent resolution API at all in `KnowledgeUpdater`).
The stale "this is broken" fact and any later "fixed it" fact (if separately
extracted and typed correctly) both remain active and can both surface,
contradicting each other in the same context with no arbitration beyond
`Guidance`'s generic "prefer higher-confidence/newer" instruction to the
downstream LLM. **Severity: High. Classification: architecture limitation /
retrieval issue.**

**S18 — Debugging session references a stack trace or error string verbatim**
`KeywordCandidateRetriever` requires literal token-set intersection (M9) —
an error message like `KeyError: 'user_id'` tokenizes into
punctuation-stripped pieces that likely won't match how the *original*
debugging conversation phrased the same issue in prose. Highly specific
technical strings are exactly the content most likely to fail lexical
overlap with paraphrased vault content. **Severity: Medium. Classification:
retrieval issue.**

**S19 — "Same error, different file this time"**
Human expects Haven to recognize this as a recurring pattern worth flagging
("this is the third time this class of bug has appeared"). Haven has no
occurrence-counting or pattern-recognition concept beyond raw
`confirmation_count` on an *identical* canonical fact (M3's CONFIRM path,
exact-text only) — a differently-worded recurrence of the same underlying
bug is treated as a wholly unrelated `NEW` fact, never linked to its
siblings. **Severity: Medium. Classification: architecture limitation.**

### E. Resuming research

**S20 — "What did I find out about the embedding options?"**
`"find out"` is a `RESEARCH` pattern (M1) — correctly classified, but
`RESEARCH` mode's requirement table needs `RESEARCH`, `BELIEF`, `DECISION`
only (`context_planner.py:465-469`) — no `IMPLEMENTATION_STATE`/`CODE_AREA`,
so if the "finding" was really "we tried FastEmbed, it worked" (arguably
implementation state), it's outside what this mode biases toward, though
plan-bias never reaches this call path anyway (M6). **Severity: Low.
Classification: planner issue.**

**S21 — Research spans a rejected-and-reconsidered decision ("we ruled out X,
then came back to it")**
Nothing in `ProjectState`/coverage tracks "rejected approaches" —
`project_state.py`'s own docstring names `rejected_approaches`/`do_not_do`
as explicitly write-side-gapped, omitted entirely (not empty-but-present).
A user re-researching something Haven already has evidence was tried and
abandoned gets no "you already looked into this" signal at all. **Severity:
Medium. Classification: architecture limitation.**

**S22 — "Investigate why the benchmark score dropped"**
`"investigate"` matches `RESEARCH` (M1) — correct — but this phrasing is
also plausibly a debugging task. Because `RESEARCH` is checked *after*
`CODING_DEBUGGING` and `CODING_DEBUGGING`'s patterns (`"bug"`, `"fix"`,
`"error"` etc.) don't happen to appear here, it lands in `RESEARCH` — but a
nearly-identical phrasing like "investigate why the benchmark is broken"
(if "broken" isn't in the pattern list — confirmed: it isn't) still lands
`RESEARCH`, while "debug why the benchmark score dropped" lands
`CODING_DEBUGGING`. Same intent, different category set, decided by word
choice alone. **Severity: Low. Classification: planner issue.**

### F. Vague / orientation-seeking questions

**S23 — "What should I work on next?"**
Directly verified in `GENERIC_CONTINUATION_QUERY_ANALYSIS.md`: this exact
phrasing **does** classify as `CONTINUATION` (it's in the pattern list) —
but its close cousin **"What should I do next?"** (no "work on") is
separately verified in `PROMPT_CONTINUATION_EVALUATION.md` §7 to
misclassify as `POINTED_QA`, receiving **no `ProjectState` at all**. The
single most orientation-seeking phrasing family in the whole task is split
by the classifier into two different outcomes based on one word. **Severity:
High. Classification: planner issue.**

**S24 — "So, um, where are we"**
Casual phrasing with filler words. `"where were we"` is a pattern; `"where
are we"` (present tense, no "were") is **not** in the list — confirmed
against `_MODE_PATTERNS`. Falls to `POINTED_QA`. **Severity: Medium.
Classification: planner issue.**

**S25 — "anything I'm forgetting?"**
No pattern matches this at all → `POINTED_QA`, no `ProjectState`, and
separately, Haven has no field or mechanism that represents "things the user
might be forgetting" as a first-class concept — the closest analog
(`open_questions`, `blockers`) only surfaces what was explicitly captured as
such at write time, not an inferred "you haven't touched X in a while"
signal. **Severity: Medium. Classification: planner issue / architecture
limitation.**

**S26 — "give me the big picture"**
No pattern match → `POINTED_QA`. Ironically the request most explicitly
asking for the orientation layer Haven built (`ProjectState`) is the
phrasing least likely to receive it, mirroring S7/S23/S24's pattern
precisely. **Severity: High (this is a recurring, not isolated, failure
mode — 4+ scenarios in this document hit the identical gap from different
angles). Classification: planner issue.**

**S27 — Ambiguous query re-asked with slightly different wording produces a
visibly different answer**
Direct consequence of M1 (classification is all-or-nothing per exact
phrasing) plus M5 (no persisted "current" state to reconcile against). A
user testing Haven by paraphrasing the same question twice — a very
plausible hostile-testing action — can get one answer with full
`ProjectState` orientation and a structurally different, orientation-free
answer for a synonym. This is the single most damaging failure mode for
*trust*, because it's directly, cheaply reproducible by any user within one
sitting. **Severity: High. Classification: planner issue.**

### G. Contradictory memories

**S28 — "We're using MySQL" (week 1), later "We're using Postgres now" (week
3)**
Human expects Haven to know Postgres is current. Verified in code: no
`SUPERSEDE` fires automatically (M3) — `"We're using Postgres now"` doesn't
begin with the normalized text of `"We're using MySQL"`, so it fails
`UPDATE`'s strict-prefix rule too, and becomes a wholly independent `NEW`
fact. **Both facts remain equally valid, equally retrievable, forever.** A
query like "what database do we use" can surface both, in either order,
with only `Guidance`'s generic recency-preference instruction (not a hard
rule) telling the downstream LLM which to trust. **Severity: High — this is
literally the headline use case second-brain tools are built for.
Classification: architecture limitation.**

**S29 — Same as S28, but the newer fact is phrased as elaboration:
"We're using MySQL, well actually now Postgres"**
`UPDATE`'s prefix rule requires the new text to *begin with* the old text
verbatim (`canonical_matcher.py:118-124`) — `"We're using MySQL, well
actually now Postgres"` does start with `"We're using MySQL"`, so this
*does* fire `UPDATE`. But `_apply_update` **appends new text as an
elaboration of the same fact**, not a replacement — the resulting
`canonical_fact` becomes the full run-on sentence "We're using MySQL, well
actually now Postgres," which retains the (now-false) "MySQL" substring
verbatim inside what Haven now treats as its one true canonical record for
that fact. A downstream LLM reading `canonical_fact` sees both claims fused
into one string with no signal which part is current. **Severity: High
— worse than S28 in one respect: this looks confident (single memory,
higher `confirmation_count`) while being actively self-contradictory
inside its own text. Classification: architecture limitation.**

**S30 — Direct contradiction with completely different wording: "the
deadline is Friday" then "actually the deadline got pushed to next month"**
Neither exact-match CONFIRM nor prefix-match UPDATE fires (different
wording, no shared prefix) → `NEW`. Both deadline claims coexist. Measured
directly in `STAGE_3_4_SUPERSEDE_INVESTIGATION.md` §3: this exact shape
(a later turn that legitimately supersedes but shares no textual prefix) is
squarely in the 91-case failure population that document quantifies.
**Severity: High. Classification: architecture limitation.**

**S31 — User explicitly corrects Haven: "no wait, I meant Thursday not
Friday"**
Same mechanism as S30 — no linkage between the correction and the original
claim beyond whatever an LLM downstream infers from `Guidance`'s
recency-preference framing. If the correction is short and lexically
generic ("Thursday not Friday"), it may not even retrieve as a strong
candidate against a later unrelated query about the deadline (M9). **Severity:
Medium. Classification: architecture limitation / retrieval issue.**

**S32 — A REAL contradiction sits right next to a JUSTIFICATION that looks
like one:** "The design deadline is Monday and the dev deadline is Thursday"
→ "The design deadline moved to Wednesday; the dev deadline is unchanged."
This exact pair is the literal example in `STAGE_3_4_SUPERSEDE_INVESTIGATION.md`
§3 of a case where naive supersession logic would wrongly destroy a still-true
fact. Today, since SUPERSEDE never auto-fires (M3), this specific failure
mode is *avoided* — but only as a side effect of Haven not having any
supersession judgment at all, not because it correctly distinguishes
"replaced" from "unchanged, restated." The underlying ambiguity the corpus
would need to test is simply never exercised (M8 gap: no benchmark case
labels this partial-update-vs-full-replacement distinction). **Severity:
Medium (currently latent, not currently harmful) — flagged because it
predicts exactly what would break if SUPERSEDE were later added without
addressing this. Classification: benchmark issue / architecture limitation.**

**S33 — A justification turn ("the reason is X") is mistaken for a
restatement of the decision**
Second literal example from the same investigation: "I decided to build the
Manager AI before GraphRAG" followed by "the reason is extraction quality is
the bigger bottleneck." The second turn contains no restatement of the
decision at all. If retrieval happens to rank the justification turn (if it
were ever separately extracted as a `DECISION`-typed fact by the live
Classifier, M17) above the actual decision turn, a query asking "what did I
decide" could surface only the reasoning with no decision statement.
**Severity: Medium (depends on classifier behavior, which is now
LLM-driven and non-deterministic per M17). Classification: retrieval issue
/ architecture limitation.**

### H. Evolving goals / changing priorities

**S34 — Goal changes from "ship the benchmark" to "ship the demo" mid-project**
`current_objective` is "whichever `GOAL`-typed candidate ranks highest this
call" (M5) — if both goal statements are still active (no supersede
mechanism marks the old one done, M3), which one "wins" as
`current_objective` depends on relative recency/importance/confidence at
query time, not on any explicit "this replaces that" signal. Could flip
back and forth across sessions if their scores are close. **Severity: High.
Classification: ProjectState issue.**

**S35 — User explicitly deprioritizes something ("forget the ranking-tier
work, it's not worth it")**
No `MemoryType`/category represents "deprioritized" or "abandoned" as
distinct from "blocker" or "open question" — the closest the taxonomy gets
is `RULE` (constraint, `NEVER_DROP` tier per M1's requirement tables) or
simply not writing a new fact at all. If extracted as a generic `FACT`, it
carries no special weight and can be outranked by older, more-established
memories about the same ranking-tier work, resurfacing exactly the thing the
user asked to drop. **Severity: Medium. Classification: architecture
limitation.**

**S36 — Priorities flip twice in one week (A → B → back to A)**
Each flip is a `NEW`, unlinked fact (M3). Three independent, equally-valid
`GOAL`/`DECISION` objects now exist for what the user experienced as one
evolving thread. `ProjectState.decisions` will show all three with no
ordering signal beyond `valid_from` timestamps buried in each `<Memory>`
element, and no synthesis ("you flip-flopped on this twice") is possible
since that requires inference `ProjectStateBuilder` explicitly never does
(Phase A is `MEMORY_DIRECT`/`DETERMINISTIC` only). **Severity: Medium.
Classification: ProjectState issue.**

**S37 — "Actually, scratch that whole plan, we're doing it differently"**
"Plan" triggers `STRUCTURING` mode (M1) if it's the first matching pattern —
but a wholesale plan reversal is arguably closer to `CONTINUATION`'s
decision/blocker-heavy needs. Whichever mode wins, the old plan's individual
facts (decisions, tasks written under it) are never bulk-invalidated — M3
operates fact-by-fact, with no concept of "archive everything under this
plan." Remnants of the old plan persist and can resurface piecemeal.
**Severity: High. Classification: architecture limitation.**

**S38 — Slow goal drift with no single explicit "I changed my mind" moment**
(e.g. gradually, over 10 conversations, focus shifts from feature A to
feature B without ever saying so directly). No mechanism detects drift at
all — `ProjectState` has no trend/delta view, only a single-call snapshot
(M5). Haven cannot answer "how has my focus shifted" even in principle from
current architecture, since that requires comparing snapshots over time,
which nothing persists. **Severity: Medium. Classification: architecture
limitation.**

### I. Abandoned branches / dead work

**S39 — A code approach is tried, abandoned, and never mentioned again**
No "abandoned" status exists for `IMPLEMENTATION_STATE`/`CODE_AREA` facts
(only `DECISION` has a status enum, `DecisionStatus`, and it's
`ACTIVE`/`SUPERSEDED`, manually triggered only). An abandoned implementation
fact remains `valid_until=None` (active) indefinitely and can resurface as
if still relevant. **Severity: Medium. Classification: architecture
limitation.**

**S40 — User asks "did we ever try X?" about something explored then dropped**
If the exploration was captured as a `FACT`/`RESEARCH`-category memory,
`RESEARCH` mode retrieval could plausibly surface it — this is one of the
better-served scenarios in this document precisely because `RESEARCH`
memories have no "resolved" concept to complicate them (unlike
decisions/blockers). Caveat: still gated by M9's lexical-overlap
requirement — "X" must appear in the query in roughly the same words it was
recorded with. **Severity: Low. Classification: retrieval issue.**

**S41 — A whole feature branch is abandoned; user later starts a similarly-
named feature from scratch**
`CanonicalMatcher`'s conservative design (M3) is actually protective here —
it won't falsely UPDATE an old, unrelated feature's facts just because the
new feature shares a name fragment (prefix-match requires the *entire*
old text as a verbatim prefix, not just name overlap). But this protection
comes at the cost of S28-style permanent duplication if the two features
really did supersede each other conceptually. **Severity: Low-Medium.
Classification: architecture limitation.**

### J. Architecture changes over time

**S42 — "We moved from REST to GraphQL" — old REST-specific `CODE_AREA`/
`IMPLEMENTATION_STATE` facts still around**
Same M3 mechanism as S28 applied to `CODE_AREA`/`IMPLEMENTATION_STATE`
types specifically — these categories have no supersede path at all (only
`DECISION` does, and only manually). Stale architecture facts accumulate
indefinitely and compete on equal footing with current ones in
`ProjectState.code_areas`/`implementation_state`, which are already the
weakest-populated fields (§S1, `PROJECT_STATE_EVALUATION.md` §3). **Severity:
High. Classification: architecture limitation.**

**S43 — Large refactor: many small "renamed X to Y" facts accumulate**
Each rename is very plausibly captured as its own `FACT`/`CODE_AREA` memory.
`DeterministicSlotAllocator`'s flat top-K (M6) has no dedup-by-topic
concept beyond `CanonicalMatcher`'s exact/prefix matching — many small,
individually-low-importance rename facts can crowd the context budget for a
`CODE_AREA`-heavy query without any of them being wrong, individually.
**Severity: Low-Medium. Classification: retrieval issue.**

**S44 — Architecture decision reversed and re-reversed (e.g. "no embeddings"
→ "add embeddings" → "no, revert to no embeddings")**
Same three-way duplication as S36, compounded by M11's rendering
triplication if any of the three ends up `DECISION`-typed and reaches a
`CONTINUATION` `ProjectState` — the same reversed-then-reversed-again fact
could appear, in its various versions, up to three times per surviving
version in one rendered prompt. **Severity: Medium. Classification:
architecture limitation / prompt issue.**

### K. Forgotten blockers

**S45 — A blocker was resolved verbally in conversation ("oh that's fixed
now") without an explicit new fact about the fix**
If the resolution isn't extracted as its own memory (classifier judgment,
M17 — non-deterministic), the original blocker simply never gets marked
resolved — there is no "blocker resolved" write path or API at all
(`KnowledgeUpdater` has no blocker-resolution method, only
`supersede_decision` scoped to `DECISION`). The stale blocker can keep
surfacing in every future `CONTINUATION` query indefinitely. **Severity:
High — blockers/constraints are marked the single most trustworthy field
in `PROJECT_STATE_EVALUATION.md` §2, so a stale one is maximally
misleading. Classification: architecture limitation.**

**S46 — Two blockers exist for the same underlying issue, phrased
differently by the user on two occasions**
Both are `NEW` (no dedup beyond exact/prefix, M3). `ProjectState.blockers`
shows both as if they were two separate problems, inflating the apparent
blocker count and diluting focus on what's actually one issue. **Severity:
Medium. Classification: architecture limitation.**

**S47 — Blocker mentioned once, then never referenced again for months —
does it still count as "active"?**
Nothing time-bounds a blocker's active status — `valid_until` is only set
by the manual supersede path. A blocker from 6 months ago (that may well be
irrelevant or already implicitly resolved) surfaces with exactly the same
`NEVER_DROP` priority tier (M1's requirement table) as one from yesterday,
and — per M7 — actually loses *some* ranking weight to recency decay, but
never enough to be excluded outright since `BLOCKER`/`CONSTRAINT` are the one
category the design explicitly protects from being dropped first under a
budget squeeze (though nothing implements that protection in the allocator
itself yet — `PriorityTier.NEVER_DROP` is a schema-level tag `context_
planner.py` sets but "No allocator reads this field yet," per that field's
own docstring). **Severity: Medium. Classification: planner issue
(unenforced design intent) / architecture limitation.**

### L. Stale implementation state

**S48 — "What's the current state of the extraction pipeline?" — code has
moved on significantly since the last captured `IMPLEMENTATION_STATE` fact**
Same M3 staleness mechanism, compounded by M14 (`IMPLEMENTATION_STATE` isn't
in `CONTINUATION`'s requirement table either — see S1) and by the fact this
category is explicitly flagged as one of the two weakest-populated fields in
`ProjectState` (`PROJECT_STATE_EVALUATION.md` §3). Double jeopardy: even
correctly-typed implementation facts are structurally underweighted, and
stale ones aren't cleared. **Severity: High. Classification: ProjectState
issue.**

**S49 — Implementation state described in code-specific jargon that shifted
over the project's life** (e.g. old name for a module vs. new name after a
rename)
M9's exact-lexical-overlap requirement means a query using the *current*
module name won't retrieve `IMPLEMENTATION_STATE` facts recorded under the
*old* name, and vice versa — no synonym/alias resolution beyond
`AliasIndex`'s hand-curated concept aliases, which cover named entities, not
every historical rename. **Severity: Medium. Classification: retrieval
issue.**

### M. Multiple active features / nested TODOs

**S50 — Three features in flight simultaneously, user asks a generic
continuation question**
No feature-level scoping exists (same root cause as M15's project-scoping
gap, one level down). `ProjectState`/`WorkingContext` blend all three
features' decisions/tasks/blockers into one flat, UUID-ordered structure
(M12) with no per-feature grouping beyond whatever concept-anchoring happens
to fall out of the ontology graph incidentally. **Severity: High.
Classification: architecture limitation.**

**S51 — A task has sub-tasks ("implement auth" → "add OAuth" → "add token
refresh")**
`MemoryType.TASK` has no parent/child relationship field at all — every task
is a flat, independent `KnowledgeObject`. Haven cannot represent or query
"what's left under the auth epic" as a structured question; it can only
surface individually-matching `TASK` facts and hope their text happens to
share vocabulary with the query. **Severity: Medium. Classification:
architecture limitation.**

**S52 — Deeply nested TODO restructuring (a task is broken into 5 subtasks,
2 of which are later merged back)**
Same flat-`TASK` limitation as S51, compounded by M3 — the "merge" itself
produces at best two more independent `NEW` facts (or nothing, if
undetected), with the original 5 subtasks never marked complete/obsolete.
`active_tasks` can accumulate stale entries indefinitely. **Severity:
Medium. Classification: architecture limitation.**

**S53 — User asks "what's still open across everything?"**
No pattern in `_MODE_PATTERNS` matches this generic aggregation request →
`POINTED_QA` (M1), no `ProjectState`. Even if it did classify as
`CONTINUATION`, the flat top-50 budget (M6) with no per-category floor means
a vault with many open items across many features could easily have some
categories' open items entirely crowded out of the 50-slot budget with no
signal to the user that truncation, not absence, occurred (this is exactly
the `gaps`-cannot-explain-itself finding in `PROJECT_STATE_EVALUATION.md`
§8). **Severity: High. Classification: planner issue / ProjectState issue.**

### N. Benchmark & prompt-structure specific findings (not user conversations, but directly user-visible)

**S54 — A user (or evaluator) trusts the continuation benchmark's headline
number as a measure of "does Haven remember my project"**
Directly measured: `CONTINUATION_BENCHMARK_AUDIT.md`'s Critical-1 shows the
dedicated continuation benchmark's adapter stores every ingested turn as
generic `MemoryType.FACT` — `ProjectStateBuilder` never sees a `DECISION`/
`TASK`/`BLOCKER`/`RULE` object in any of its 10 pilot cases, so
`ProjectState` output is a structurally empty shell in **every** case, and
whatever score the benchmark reports reflects flat-retrieval ranking
quality, not the reconstruction mechanism it claims to test. Anyone citing
this benchmark's number as evidence of continuation quality is citing a
number that cannot currently support that claim. **Severity: Critical (for
anyone relying on the number, e.g. hackathon judges). Classification:
benchmark issue.**

**S55 — A skeptical user deliberately re-runs the same query twice to "test"
Haven's consistency, or pastes the raw structured prompt into a chat and
reads it themselves**
Two independent, user-discoverable-in-under-a-minute problems surface
immediately: (1) `TOPIC`-kind `WorkingContext` sections used to render a raw
concept UUID as their title (now fixed for `query_working_context`/
`query_structured` via `_resolve_topic_titles`, per session memory — but
**`ContextBuilder`'s plain-text `query()` output has no equivalent
concept-grouping or titling at all**, since it never builds `WorkingContext`
objects); (2) the `[N]` cross-reference convention, the two different
empty-value XML conventions (self-closing vs. omitted-element), and the two
different meanings of the word "confidence" in the same document (`
<ProjectState confidence="0.62">` = completeness fraction vs. `<Memory
confidence="0.9">` = per-fact certainty) are all real, both flagged directly
in `PROMPT_CONTINUATION_EVALUATION.md` §5/§6 — a technically literate user
reading the raw prompt (not just the model's response to it) will notice the
inconsistency immediately, even though a downstream LLM mostly won't be
confused by it. **Severity: Low-Medium (mostly a trust/polish issue for
technically literate users who inspect internals, e.g. via the Retrieval
Inspector). Classification: prompt issue.**

---

## Part 3 — Ranked weaknesses

Ranked by **user impact** (how often and how badly it degrades a real
second-brain session), **implementation effort** (rough, given everything
already built in the working tree), and **hackathon priority** — what's
worth fixing before a demo vs. what's a known, documented, deliberately
deferred limitation.

| Rank | Weakness (mechanism) | Scenarios | User impact | Effort | Hackathon priority |
|---|---|---|---|---|---|
| 1 | Orientation-seeking phrasings inconsistently route to `POINTED_QA` (M1) | S7,S23,S24,S25,S26,S27,S53 | **Very high** — hits the exact "returning after time away" headline use case, reproducible by any user rephrasing one question | **Low** — extend `_MODE_PATTERNS`, already the exact fix `GENERIC_CONTINUATION_QUERY_ANALYSIS.md` recommends (#2) | **Fix before demo.** Cheapest, highest-visibility fix available; a demo script that happens to phrase the "resume" question one way and a live judge who phrases it another way is a real, likely-to-occur risk. |
| 2 | Contradictions never auto-resolve; old + new facts coexist forever (M3) | S28,S29,S30,S31,S34,S36,S42,S44,S46 | **Very high** — this is the single most-repeated failure shape in this document (9+ scenarios) and the literal reason a second brain exists | **High** — `STAGE_3_4_SUPERSEDE_INVESTIGATION.md` already priced this precisely: +23/250 ceiling, 3.7% measured regression risk, new LLM call in a previously LLM-free layer, one-object→two-object contract break across 3+ call sites | **Do not implement before hackathon** (matches that investigation's own explicit recommendation) — but **do** narrate this limitation proactively in any demo, since a live judge testing exactly this (very likely, per the task's own scenario list) will find it in under a minute. |
| 3 | Continuation benchmark's headline number doesn't measure what it claims to (M8, Critical-1) | S54 | **Critical if cited, zero if not** — pure reputational/credibility risk, not a runtime user-harm | **Medium** — `CONTINUATION_BENCHMARK_AUDIT.md` already specs two fix options (a: real classifier ingestion, b: deterministic turn_type→MemoryType mapping) | **Fix or don't cite.** Either wire option (b) — cheap, deterministic, no LLM cost — before reporting any number from this benchmark, or simply don't present it as evidence of reconstruction quality. |
| 4 | No project/feature scoping anywhere in the pipeline (M15) | S12,S13,S14,S15,S50 | **High** in any multi-project vault (the realistic steady-state for a personal second brain after a few months) | **High** — genuinely new dimension threading through `ProjectState`, `WorkingContext`, ranking | **Post-hackathon.** Correctly out of scope for a hackathon timeline; worth naming explicitly as the reason multi-project demos should be avoided or staged carefully. |
| 5 | Flat top-K allocation, no per-category reservation (M6) | S1,S3,S5,S8,S42,S48,S53 | **High**, compounds every other weakness above it (it's why S28-style duplicate facts can crowd out the correction, why S47's blocker can lose to noise) | **Medium-high** — `PROJECT_STATE_EVALUATION.md` §9 already scopes this as recommendation #7, explicitly flagged "highest leverage, highest risk" | **Post-hackathon**, but the underlying diagnosis (`gaps` cannot distinguish "absent" from "outranked") is a **cheap, additive fix** (`PROJECT_STATE_EVALUATION.md` §9 #6 — thread `ranked_all` into `ProjectStateBuilder`) worth doing regardless of the larger allocator redesign. |
| 6 | `Guidance` doesn't explain `[N]`, the two empty-value conventions, or the two meanings of `confidence` (M11-adjacent, prompt issue) | S55 | **Low-medium** — invisible to end users who never read the raw prompt, visible to technical evaluators inspecting the Retrieval Inspector | **Trivial** — one of these (`[N]` convention) is already fixed per session memory; the `confidence` disambiguation and `<Gaps>` caveat are one-line `Guidance` additions already scoped in `PROMPT_CONTINUATION_EVALUATION.md` §10 | **Fix before demo if time allows** — cheapest items in this whole document, directly reduce the chance a technical judge finds an easy, avoidable inconsistency. |
| 7 | Recency 7-day half-life has no floor and no "durable fact" exemption (M7) | S5,S11,S28(partially) | **Medium** — mostly affects vaults with real usage history (weeks+), which is exactly the scenario a hackathon demo may not have time to simulate but a real user will hit immediately | **Low-medium** — tunable constant plus possibly a durable-fact flag; no architecture change | **Post-hackathon**, note in follow-up backlog. |
| 8 | No status/resolution concept for blockers, tasks, or non-decision facts (M3 extended) | S17,S39,S41,S45,S47,S51,S52 | **High cumulative** (7 scenarios) but each individually **medium** | **High** — needs new `MemoryType`/status semantics and write-side extraction changes, not purely additive | **Post-hackathon**, largest single architecture gap named in this document after contradiction-resolution itself. |
| 9 | Empty/thin benchmark categories understate real second-brain coverage (M8) | S8,S9,S12 (proxy via "people/projects" gaps) | **Medium** — doesn't affect runtime behavior, affects how trustworthy the published pass rate is as a claim about real usage | **Low** — dataset authoring only, no code | **Worth a punch-list mention in any results writeup**; not a code fix. |
| 10 | No "as of" timestamp or scope statement rendered anywhere (M10) | S5(temporal half),S8 | **Medium** | **Trivial** — value already computed (`ProjectState.generated_at`), just needs rendering; already scoped and deliberately deferred once in `PROMPT_CONTINUATION_EVALUATION.md` §10 as "needs a scoped decision, not a blanket reinstatement" | **Post-hackathon**, low cost whenever it is picked up. |

---

## Part 4 — What this stress test did *not* find

Worth stating plainly, since a hostile-testing exercise that finds nothing
positive is not credible: several mechanisms held up well under adversarial
scenario construction and are worth *not* touching.

- **Determinism and provenance are genuinely solid.** Every scenario above
  that produces a wrong or incomplete answer produces the *same* wrong or
  incomplete answer given the same input — nothing in 55 scenarios relied on
  flakiness. Every populated `ProjectState`/`WorkingContext` field traces
  cleanly to a real `KnowledgeObject` via the shared `[N]` index
  (`PROJECT_STATE_EVALUATION.md` §8's determinism tests). A user who distrusts
  an answer can always ask "why" and get a real, checkable citation — Haven
  never fabricates a fact it doesn't have.
- **The conservative `CanonicalMatcher` design (M3) is a defensible tradeoff,
  not a bug** — it never silently destroys a memory it shouldn't (verified:
  zero cases in this document where an unrelated memory was wrongly
  overwritten). The cost (duplicate/stale facts accumulate) is real and
  drives roughly a third of this document's scenarios, but the alternative
  failure mode (silent data loss) is worse, and `STAGE_3_4_SUPERSEDE_
  INVESTIGATION.md`'s own regression testing already demonstrates a naive
  fix would trade this problem for that one, not eliminate it.
  `PROJECT_STATE_EVALUATION.md`'s never-inferred fields follow the same
  principle and hold up the same way.
- **`Guidance`'s explicit confidence/conflict framing genuinely works** for
  the cases this document tested by reasoning — a downstream LLM told
  explicitly to prefer higher-confidence, more-recent memories and to
  surface (not silently resolve) contradictions is the right mitigation for
  M3's duplication problem, given the architecture; it's a real, working
  safety net, just not a substitute for actual supersession.
