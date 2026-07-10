# The Context Planner — Investigation and Design

Status: **Investigation only. No code changed.** Every claim about current Haven behavior
below was checked against source on disk (not against `WORKING_CONTEXT_2_DESIGN.md`'s
description of it) as of 2026-07-09. Where that existing doc turned out to be incomplete
or imprecise, this document says so explicitly and corrects it — see §3. This document
narrows scope to the Context Planner specifically (task §§1–7 of the brief); it assumes,
but does not re-derive, the broader Working Context 2.0 shape that doc already proposes.

---

## 0. What "Context Planner" means, precisely

A component that runs **before retrieval**, consumes only the raw query (plus cheap
ontology lookups), and outputs a `ContextPlan` — a decision about *what kinds* of state a
query needs, not the state itself. It never touches memory content. Its failure mode is
"asked for the wrong categories," not "retrieved the wrong facts" — that second failure
mode belongs to retrieval/ranking/acceptance, which are unchanged.

---

## 1. Do task-shaped requests cluster into a small number of modes?

Partially, and the brief's own example list (coding, research, writing, debugging,
planning, design, learning, continuation, question answering) overclusters on the surface
and underclusters where it matters. Working through each pair for whether required-context
*categories* actually differ (not whether the surface task differs):

| Pair | Same required categories? | Verdict |
|---|---|---|
| coding / debugging | ~80% overlap (both want implementation state, code areas, constraints); debugging additionally wants recent errors/discoveries and rejected fixes, coding wants active tasks | Keep separate — the 20% divergence is exactly the part a planner earns its cost on |
| planning / design / architecture | Near-total overlap (goals, constraints, decisions, rejected approaches) — the difference is deliverable shape, not context need | Collapse into one mode |
| writing | Same set as planning minus code-specific categories | Fold into planning's set as a variant, not a separate mode |
| research / learning | Diverge in one critical way: *research* is about the user's own project (findings live in Haven), *learning* is frequently about general external knowledge Haven has no facts about at all | Keep separate — but see the null-case finding below |
| question answering | Needs almost nothing — one fact, one category, no reconstruction | Not a task mode at all; it's the *absence* of a plan (see below) |
| continuation | Categorically different: scope is maximal and undetermined until the query is understood | Its own mode, as `WORKING_CONTEXT_2_DESIGN.md` already proposed |

**Two corrections to the existing design doc's 8-mode table** (`CODING, DEBUGGING,
PLANNING, WRITING, RESEARCH, BENCHMARKING, ARCHITECTURE, CONTINUATION`,
`WORKING_CONTEXT_2_DESIGN.md` §3):

1. **`BENCHMARKING` is not a task mode, it's a Haven-specific vocabulary leak.** It exists
   in that table because Haven's own project happens to have a benchmark suite — it is not
   a category of request a general "second brain" user would issue about *any* project.
   Treat it as a project-specific config row (`benchmark_status` folded into `DEBUGGING`'s
   required set when the resolved concept is a testing/CI concept), not a first-class mode.
   Leaving it in as a peer of `CODING`/`WRITING` overfits the planner's design to the one
   project it was designed against.
2. **A "requires nothing" case is missing, and it is not a corner case — it is the common
   case.** Pointed factual queries ("what database do I use," "what's my editor
   preference") are the majority of Haven's *existing* benchmark corpus (identity,
   preferences, decisions categories) and need exactly the single matching fact, not a
   reconstructed state. A planner that defaults every query into a task-mode bucket with a
   nonempty required-category list will silently regress these — the correct plan for most
   queries is **"skip planning, do the single-fact retrieval Haven already does today."**

**Revised clustering — 5 real modes, not 8:**

`POINTED_QA` (no plan, direct single-category retrieval — this is what Haven does today
for every query, since no planner exists yet) · `CODING_DEBUGGING` (implementation state,
code areas, constraints, active tasks, recent errors) · `STRUCTURING` (planning + design +
architecture + writing, merged — goals, decisions, rejected approaches, constraints) ·
`RESEARCH` (findings, open questions, decisions — assumes Haven has relevant history) ·
`CONTINUATION` (maximal scope, all categories, ambiguous by construction).

`LEARNING` deliberately has no bucket: the planner's job for a learning-shaped query
("explain X," "how does Y work" where X/Y resolve to no `Concept` in the graph) is to
**recognize zero project-context is available and say so**, not to force it into
`RESEARCH`'s category set and retrieve noise. This is itself evidence for keeping the
deterministic layer simple (§4) — "no concept resolved, no history exists" is a cheap,
reliable signal, exactly the kind of thing that doesn't need an LLM to detect.

---

## 2. Per-mode category requirements, checked against what exists

Using the corrected 5-mode clustering, and the `WorkingContext` category vocabulary from
`WORKING_CONTEXT_2_DESIGN.md` §2 (verified accurate by direct code read — see §3 below for
what's already real vs. still proposed):

| Mode | Required categories | Categories that already exist as `MemoryRole` | Categories that don't exist yet |
|---|---|---|---|
| `POINTED_QA` | whichever single category the resolved `Concept`'s dominant `MemoryType` maps to | Yes — `resolve_role()` already does this mapping today | none |
| `CODING_DEBUGGING` | implementation_state, code_areas, constraints, active_tasks, recent_discoveries | `TASK` (active_tasks) | implementation_state, code_areas, constraints (constraints currently collapses into `BELIEF` via `RULE`), recent_discoveries has a loose existing fit (`RESEARCH`/`FACT`) |
| `STRUCTURING` | goals, decisions, rejected_approaches, constraints, open_questions | `GOAL`, `DECISION`, `OPEN_QUESTION` | rejected_approaches, constraints |
| `RESEARCH` | recent_discoveries, open_questions, decisions | `OPEN_QUESTION`, `DECISION` | recent_discoveries (loose fit only) |
| `CONTINUATION` | all of the above, project-wide | — | all of the above |

This mostly confirms `WORKING_CONTEXT_2_DESIGN.md` §2's category list is the right
vocabulary — the new categories it proposes (`blockers`, `rejected_approaches`,
`constraints`, `implementation_state`, `code_areas`, `benchmark_status`, `do_not_do`) are
real gaps, verified: grepping the whole repo for these concepts as data (not prose) finds
nothing — no `MemoryRole`, no `MemoryType`, no field anywhere carries "this is a blocker"
or "this is a rejected approach" as structured state today. `RULE`-typed `KnowledgeObject`s
exist and resolve to `BELIEF` (confirmed, `_MEMORY_TYPE_ROLE` table,
`obsidian/ontology/retrieval_models.py:858–870`) — they are not currently distinguishable
from general beliefs at read time, which is exactly the doc's `CONSTRAINT`-split proposal.

---

## 3. Comparison against the current `WorkingContext` — corrections to the existing doc

Direct source verification (via an independent Explore pass over `engine.py`,
`working_context_builder.py`, `retrieval_models.py`, `acceptance_stage.py`,
`query_rewriter.py`, `deterministic_slot_allocator.py`, `structured_prompt_builder.py`,
`activation_spreader.py`, `manager_ai/pipeline.py`, `server/main.py`, and
`benchmarks/`) found `WORKING_CONTEXT_2_DESIGN.md`'s grounding section directionally
correct but incomplete in seven specific ways that change how a Context Planner should be
designed:

1. **`WorkingContextBuilder.build()` already partitions by scope — just not by plan.**
   The doc describes it as producing role-buckets; what it actually does is two-level:
   group candidates by **primary concept** first (`_primary_concept_id` — the
   highest-`activation_score` `ActivatedConcept` in `Candidate.supporting_concepts`, ties
   broken by concept id), producing one `WorkingContext` per distinct concept plus one
   `GENERAL` catch-all for zero-evidence candidates — *then* bucket by `MemoryRole` inside
   each. **This means concept-scoped context partitioning is not new — it already happens
   on every query today, driven by whatever retrieval happened to activate, not by an
   upstream plan.** A `CONTINUATION`-mode query today produces however many `WorkingContext`
   objects there are activated concepts, never one holistic reconstruction. The Context
   Planner's actual job re: scope is narrower than the existing doc implies: it doesn't need
   to invent concept-level scoping, it needs to **decide when the existing per-concept
   splitting should be overridden and merged into one project-wide context** (for
   `CONTINUATION`), and to parameterize `ActivationSpreader`'s real scope knob, which is
   named `max_depth` (a hop count), not "radius" as the existing doc calls it.
2. **`DeterministicSlotAllocator` has no token budget — it's a pure top-K-by-count cutoff**
   (`sorted(ranked_candidates)[:config.max_results]`, its own docstring says explicitly:
   "No separate token/character budget concept is introduced"). The existing doc's §7
   ("reuse `DeterministicSlotAllocator`'s existing allocation philosophy" for a
   token-budget/tiered-priority scheme) is proposing genuinely new logic, not extending
   anything token-aware that exists. Worth being explicit about this in implementation
   planning so Phase-0 estimates aren't understated.
3. **`ContextKind.PROJECT` is defined but dead** — only `TOPIC` and `GENERAL` are ever
   constructed. The existing doc's "Project/system identity" category has no existing kind
   to attach to; this is a small additive change (construct `PROJECT` when the Context
   Planner's scope is project-wide), not a gap in category vocabulary.
4. **`AcceptanceStage` has zero per-category notion today** — its five stages
   (absolute floor → query-level abstention → score-gap cut, exempting candidates sharing a
   supporting concept → relative floor → hard cap) all operate over one `List[RankedCandidate]`
   for an entire query. Running it "per category" (existing doc §6) is a real, nontrivial
   change to its call contract, not a parameterization.
5. **`query_working_context`/`query_structured` already duplicate the
   rewrite/retrieve/merge/rank/accept/allocate prefix on purpose** — `MemoryEngine._allocate`'s
   own docstring calls this a deliberate small duplication so the two new methods share zero
   code with `query`/`query_with_trace`. **A Context Planner driving a third,
   per-category-parameterized version of this same prefix needs to decide whether it becomes
   a third independently-maintained copy, or whether this is the point where the shared
   prefix finally gets factored out.** This is flagged nowhere in the existing doc and is a
   real design decision this document surfaces for Phase 1 (§9).
6. **Two failure taxonomies already exist in `benchmarks/`, uncoordinated**: the LLM judge's
   own `failure_type` (`NONE, RETRIEVAL, SUPERSESSION, TEMPORAL, REASONING, INCOMPLETE,
   INCORRECT, JUDGE_ERROR`, `benchmarks/judges/llm_judge.py`) and `classify_failure.py`'s
   independent 3-value heuristic (`PASS, NO_RETRIEVAL, INCORRECT_ANSWER`). The existing
   doc's proposed new failure types (`GAP_MISSED`, `STALE_PRESENTED_AS_CURRENT`, etc.) need
   to be reconciled against one of these, not bolted on as a third taxonomy.
7. **The benchmark corpus already has five categories that sound exactly like "context
   reconstruction" and are entirely empty**: `active_context`, `insights`, `memory_recall`,
   `mistake_prevention`, `open_problems` — 0 files each, confirmed in
   `docs/architecture/PRE_BENCHMARK_FREEZE_AUDIT.md` §2, silently skipped by the runner (no
   warning on an empty-but-present directory). `decision_reconstruction` exists and is
   populated (26 gradeable cases) but is currently the *weakest*-performing populated
   category at 57.7% pass. **Before authoring the existing doc's proposed new benchmark
   categories (`cold_open_continuation`, `gap_honesty`, etc.), these five empty
   directories and the weak `decision_reconstruction` category are the same territory and
   should be filled first** — this is real existing benchmark infrastructure sitting unused,
   not something to duplicate under new names.

**What's redundant, not just missing**: nothing in the existing category vocabulary is
redundant with what's proposed. The overlap risk is entirely on the *benchmark* side (point
7 above), not the `WorkingContext` schema side.

---

## 4. Can the planner stay deterministic?

**Yes, for the large majority of queries — the same split `QueryRewriter` already earns its
keep on.** Evidence for why deterministic-first is viable here specifically (not just "our
house style"):

- **Concept resolution already exists and is a strong, cheap, non-LLM signal.**
  `HybridCandidateRetriever` already resolves query entities against the `Concept` graph
  before any ranking happens. Whether a query resolves to zero, one, or many concepts, and
  what `MemoryType` those concepts' backing `KnowledgeObject`s skew toward, is available for
  free — the Context Planner doesn't need new resolution machinery, it needs to *read* a
  signal retrieval already computes.
- **Lexical mode signals are cheap and already precedented.** Haven's own
  `keyword_candidate_retriever.py` already does deterministic lexical normalization (variant
  groups, ~50 groups) as a design choice proven out over four investigation docs
  (`LEXICAL_NORMALIZATION_COMPLETE.md`, `ENTITY_CAT_INVESTIGATION.md`,
  `RANKING_FAILURE_INVESTIGATION.md`, `PRE_BENCHMARK_FREEZE_AUDIT.md`) — pattern/keyword
  classification of "continue," "where were we," "why did we," "how do I fix" against a
  fixed phrase table is the same shape of cheap, auditable heuristic this project has
  already invested in and validated works better than expected before reaching for
  anything probabilistic.
- **The one place this project already tried "loosen a deterministic heuristic" and
  measured the cost tells against doing the same for planning.**
  `PRE_BENCHMARK_FREEZE_AUDIT.md` Task 4 found that loosening `ConceptDetector`'s
  capitalized-span heuristic (to catch more `ONTOLOGY_COV` cases) **already measurably hurt
  precision** in this exact codebase — noise concepts won query-seed contention in 2 of 26
  ranking failures. This is direct evidence, not speculation, that "make the classifier more
  permissive" has a real, measured failure mode here, which argues for keeping the
  deterministic layer narrow and precise (only route to the LLM fallback, never guess).
- **Deterministic stages are ~0ms; only the LLM path has real latency cost.**
  `benchmarks/results/final_report.md` measured `AcceptanceStage`'s cost at "~0 ms" latency
  increase for a comparable deterministic decision stage. `QueryRewriter` (the existing LLM
  fallback precedent) budgets a 10s timeout per call, Qwen-plus over DashScope. This is the
  concrete cost gap between the two layers: essentially free vs. a multi-second tail.

**Where rules genuinely can't decide, mirroring `QueryRewriter`'s exact contract** (off by
default in the sense of "only invoked on low confidence," one bounded call, in-memory
cache keyed by normalized query text, fails open to the deterministic layer's best guess on
any error):

1. **`CONTINUATION`-mode queries** — by definition open-ended; "continue implementing X"
   carries no lexical signal about *which* categories matter beyond "all of them," which is
   already the deterministic answer, so the LLM's actual marginal job here is narrower than
   the existing doc implies: not "pick the mode" (already `CONTINUATION` by pattern match)
   but "pick the *scope*" (which concept/project this continuation is about, when more than
   one active project exists in the vault).
2. **Zero or ambiguous concept resolution with no continuation pattern** — a query that
   matches no phrase pattern and resolves to zero or many roughly-equal-activation concepts.
   This is the boundary between `POINTED_QA` (nothing to plan) and `LEARNING` (nothing
   Haven has) — genuinely hard to tell apart deterministically, and getting it wrong in
   either direction has an asymmetric, low-severity cost (worst case: no plan is made and
   Haven falls back to today's single-fact retrieval, which is a strict floor, never worse
   than current behavior — see §5's "plan-once, fail-open" design).
3. **Mode boundary disagreement** — deterministic lexical signal says one mode, concept-type
   signal says another (e.g., "why isn't this working" lexically reads `CODING_DEBUGGING`
   but resolves to a concept whose `KnowledgeObject`s are dominantly `GOAL`-typed, suggesting
   `STRUCTURING`). Low-frequency by construction (both signals usually agree), and exactly
   the kind of narrow, auditable disagreement `AcceptanceStage`'s own philosophy already
   validates as worth a distinct, loggable decision point rather than silently picking one.

**Estimated split, extrapolating from this project's own numbers** (not measured — no
Context Planner exists yet to measure): if `CONTINUATION`-shaped queries are a small tail
of real usage (the existing benchmark corpus's populated categories are almost entirely
`POINTED_QA`-shaped single-fact lookups) and mode disagreement is rare by construction, the
LLM fallback should trigger on a minority of queries — plausibly similar in proportion to
how rarely `QueryRewriter`'s multi-query expansion actually changes retrieval outcomes
today, though that specific number isn't measured anywhere in this repo and shouldn't be
asserted as fact.

---

## 5. How should planning interact with retrieval?

Comparing the brief's three options directly:

**A. Plan once, then retrieve.** Cheapest, simplest to reason about and debug (one plan,
inspectable independent of retrieval outcome — the same "auditable decision object" value
`AcceptanceStage`'s `AcceptanceDecision` already provides). Risk: a wrong plan silently
under- or over-scopes retrieval with no correction.

**B. Plan → retrieve → verify coverage → retrieve again (bounded).** This is what
`WORKING_CONTEXT_2_DESIGN.md` §4/§6 already proposes as "Gap Detection" + "Additional
Retrieval," capped at one retry per gap. It directly addresses A's risk at a fixed,
boundable cost (never more than 2x the retrieval calls of A, and only for categories that
came back empty or under the abstention floor — most categories won't need the retry).

**C. Iteratively refine** (unbounded plan-retrieve-replan loop). No termination guarantee
without an explicit budget; breaks the "deterministic, auditable, cheap" property this
entire pipeline is built around (`AcceptanceStage`, `QueryRewriter`, and the ranker are all
explicitly one-pass-only or capped-retry). Also has no precedent anywhere in this codebase
— every existing "more than one pass" mechanism here (`QueryRewriter`'s ≤2 rewrites,
`AcceptanceStage`'s single gap-cut) is bounded by a small fixed constant, never a loop
condition.

**Recommendation: B, not A or C — and this is not actually a new pattern for this
codebase, it's `AcceptanceStage`'s own abstention design (§6, `WORKING_CONTEXT_2_DESIGN.md`)
applied one level up.** A is strictly worse than B at equivalent implementation cost (B is
A plus one bounded retry pass); C is a real cost/complexity/predictability regression this
project's existing design philosophy has already rejected in every other stage that could
have been iterative. The one place B needs to earn its added complexity over A: it only
pays for itself once retrieval per-category is real (§3 point 4's `AcceptanceStage`
per-category change) — until that lands, "verify coverage" has nothing granular to check
coverage *of*, so B's gap-detection loop is not separable from the broader Working Context
2.0 retrieval changes; it cannot be built as an isolated planner-only feature.

---

## 6. Computational cost

| Layer | Latency | Complexity | Effort | Notes |
|---|---|---|---|---|
| Deterministic mode classification (phrase/pattern table + concept-resolution readout) | ~0ms, in-process | Low | Small — a lookup table plus reading a signal retrieval already computes | Same cost class as `AcceptanceStage`, measured at "~0 ms" in this repo |
| Concept-resolution readout (reusing existing retrieval) | Already paid for by retrieval | None additional | None — read-only consumption of existing `ActivatedConcept` data | Zero marginal cost if planner runs *after* an initial cheap resolution pass, not before |
| LLM fallback (ambiguous/`CONTINUATION` cases only) | Up to the same ~10s timeout budget `QueryRewriter` already uses (same Qwen-plus/DashScope path is the natural reuse) | Medium | Small if the `QueryRewriter` client/cache/fail-open scaffolding is reused directly rather than rebuilt | Only pays this cost for the minority of queries the deterministic layer can't resolve (§4) |
| Per-category retrieval (retrieval called once per required category instead of once per query) | Linear in number of required categories (up to 5, `CONTINUATION`'s full set) — each pass reuses the existing sub-10ms-class retrieval/rank/accept stages | Medium-high | This is the largest single implementation cost in the whole design — it changes `AcceptanceStage`'s call contract (§3 point 4) and decides the `_allocate`-duplication question (§3 point 5) | Not planner-only work — this is Working Context 2.0's retrieval-layer cost, the planner just triggers it |
| Gap Detection + one bounded retry | At most 2x a single category's retrieval cost, only for categories that came back empty | Low-medium once per-category retrieval exists | Small on top of per-category retrieval | Cannot be measured or built independent of the row above |

**Benchmark impact**: neutral-to-positive on the *existing* corpus (mostly `POINTED_QA`-shaped
— the planner should route these to a zero-overhead pass-through, meaning no regression
risk on the corpus that currently drives the 78.8% number), and is the *only* lever that can
move the five empty "reconstruction-shaped" categories (§3 point 7) from 0 measured cases to
something scoreable — but that requires authoring those benchmark cases, which is dataset
work orthogonal to planner implementation.

**Demo impact**: high, and cheaply so — Phase-0/1 (deterministic-only planner, no gap
detection yet) already produces a visibly different, mode-adaptive context breakdown for
"continue implementing Haven" vs. "what database do I use," which is a legible, inspectable
demo moment (an explicit `ContextPlan` object to show, the same way `AcceptanceDecision`
and `RetrievalTrace` are already shown in the dashboard's "Write Inspector"/"Retrieval
Inspector"). This is achievable without touching `AcceptanceStage`'s per-category contract
at all.

**Product impact**: the deterministic mode-classification layer alone (Phase 0/1) is
low-risk, additive, and directly answers the brief's motivating complaint ("continue
implementing Haven" reading as amnesia). The higher-payoff-but-higher-cost pieces (gap
detection, per-category retrieval, incremental materialization) are real architecture work,
not a weekend feature — the existing doc's phased roadmap (§10 there) correctly sequences
this, and this document's §9 below refines that sequencing based on the corrections in §3.

---

## 7. Comparison against existing systems — is this novel?

Checked directly, not asserted: read this repo's own vendored `mem0` core
(`mem0/memory/main.py`) and its own vendored `openmemory/` (a genuinely separate,
first-party mem0 product, distinct from an unrelated same-named community project —
worth flagging since a naive web search surfaces the wrong "OpenMemory" first), plus
external research on Zep/Graphiti, Letta/MemGPT, and Microsoft GraphRAG, plus published
retrieval-routing literature (Adaptive-RAG, Self-RAG).

| System | What it actually does at read time | Does it plan *categories* by task type? |
|---|---|---|
| **mem0 core** (`mem0/memory/main.py:search`, verified directly in this repo) | Flat vector similarity + optional metadata filters (`user_id`/`agent_id`/`run_id`, operator-based filter dict), a score `threshold`, optional `rerank`. No query classification of any kind. | No — every query gets the same retrieval shape regardless of what it's asking for. |
| **mem0's OpenMemory** (`openmemory/api/app/`, verified directly in this repo) | Thin MCP wrapper (`add_memories`/`search_memory`/`list_memories`/`delete_memories`) over mem0 core. Categorization exists but is **write-time only** (`get_categories_for_memory`, a single GPT-4o-mini prompt tagging free-text categories) — structurally the same idea as Haven's own write-time `Classifier`, and confirms neither mem0 layer has any read-time planning. | No. |
| **Zep / Graphiti** | Bitemporal knowledge graph (event time + ingestion time), fact validity windows, hybrid semantic+BM25+graph search, no LLM calls at retrieval time (~300ms P95). Optimizes *fact correctness over time* — "is this still true," not "what categories of state does this task need." | No — Zep's sophistication is temporal truth-tracking, orthogonal to task-mode-based category selection. A Zep-style engine under Haven would make `AcceptanceStage`'s validity filter and supersession handling stronger, but wouldn't itself produce a `ContextPlan`. |
| **Letta / MemGPT** | LLM-driven self-editing memory: the model itself calls `memory_insert`/`memory_replace`/`memory_rethink` tools to decide what enters its own context window, across core (always-in-context) / recall / archival tiers, with a heartbeat loop enabling multi-step reasoning. | **Closest existing analog** — the agent implicitly "plans" what memory to load, per turn. But it's LLM-first and per-turn: there is no separate, auditable, upstream `ContextPlan` object with a discrete task_mode and category list — it's dissolved into whatever the model decides to do via tool calls that turn. This is the opposite of Haven's deterministic-first philosophy: Letta trusts the model to plan; Haven's whole design (`AcceptanceStage`, `QueryRewriter`) explicitly doesn't, by design, for cost and auditability reasons. |
| **Microsoft GraphRAG** | Global search (community-summary rollup, for holistic/dataset-wide questions) vs. local search (entity fan-out, for pointed questions) — a genuine precedent for *routing retrieval strategy by query shape*. Mode selection in the original design is **manual** (the caller picks global or local); follow-on work (DRIFT, dynamic community selection) automates the choice closer to what's proposed here. | **Partial precedent, coarser-grained.** GraphRAG routes on a binary axis (holistic vs. pointed), not a task-typed category-requirement table (coding needs X, debugging needs Y). It's the nearest published precedent for "classify query shape, then change what gets pulled," but it doesn't decompose "what gets pulled" into named state categories the way this design proposes. |
| **Adaptive-RAG / Self-RAG (published research, not shipped memory products)** | A cheap classifier routes queries among retrieval *effort* levels (no retrieval / single-hop / multi-hop). Structurally the closest published analog to "deterministic-first classify, LLM only when ambiguous" — but the axis being routed is retrieval depth, not category composition. | No — same shape of idea (classify-then-route), different axis (effort, not category). |

**Direct answer to "is this novel": no, not in its individual mechanisms, but the
*combination and axis* has no shipped precedent I found.** Every piece of this design
exists somewhere: classify-then-route is Adaptive-RAG's whole thesis; strategy selection by
query shape is GraphRAG's global/local split; letting the read side decide what state
matters is Letta's raison d'être; explicit fact-validity/staleness awareness is Zep's core
value proposition. **What none of the four systems examined do is decompose "what does
resuming this task need" into a fixed, auditable table of *named state categories*
(implementation state, blockers, rejected approaches, constraints) keyed to a small set of
task types, computed deterministically before retrieval, with an explicit "this category is
a gap, not absent" signal downstream.** That specific shape — a `ContextPlan` as a first-class,
inspectable, deterministic-by-default artifact sitting between "what is being asked" and
"what gets retrieved" — is the part of this idea that doesn't have a name in the literature
or in any of these four products yet. It is a recombination, not an invention from nothing,
and the honest framing for anyone pitching this externally is "this applies GraphRAG's
routing idea and Adaptive-RAG's classify-then-route shape to a category-of-state axis
instead of an effort or holistic/local axis" — not "nobody has thought about planning
context before."

---

## Alternatives considered

1. **Do nothing; keep improving retrieval/ranking precision.** Rejected as the sole path:
   `PRE_BENCHMARK_FREEZE_AUDIT.md` already found every remaining deterministic retrieval
   improvement fails the project's own ROI bar (§4 there) — the ceiling on "better facts"
   is close to exhausted for this architecture; the actual gap the brief describes
   (amnesia on "continue implementing X") is a *composition* problem, not a *precision*
   problem, and more ranking tuning doesn't touch it.
2. **Let the LLM plan every time** (Letta-style, no deterministic layer at all). Rejected:
   contradicts the project's established, validated philosophy (`AcceptanceStage`,
   `QueryRewriter`) of deterministic-first with narrow LLM fallback, and reintroduces
   per-query latency/cost this project has consistently avoided elsewhere.
3. **One flat `ContextPlan` for all task types (no mode table at all — always retrieve
   every category)** was implicitly considered by asking whether task modes cluster at all
   (§1). Rejected: the existing benchmark corpus is dominated by `POINTED_QA`-shaped
   queries; always retrieving every category on every query wastes retrieval passes on
   categories the query doesn't need and would regress today's cheap single-fact path for
   no benefit.
4. **8-mode table as originally proposed in `WORKING_CONTEXT_2_DESIGN.md`.** Superseded by
   §1's 5-mode revision — `BENCHMARKING` is project-specific vocabulary, not a general task
   type, and the 8-mode table has no "requires nothing" (`POINTED_QA`) case at all, which
   is the most common case in the existing corpus.

## Trade-offs and risks

- **Risk: a wrong `task_mode` classification silently under-scopes a query** (plans for
  `POINTED_QA` when the query actually needed `CONTINUATION`). Mitigated by making the
  under-scoped path a strict floor, never worse than today's behavior (no plan = today's
  single-pass retrieval), and by the LLM fallback triggering specifically when
  concept-resolution/pattern signals disagree (§4).
- **Risk: per-category retrieval multiplies retrieval calls** (up to 5x for
  `CONTINUATION`). Mitigated by each pass reusing the already-cheap
  retrieve/rank/accept stages (measured "~0ms" for the deterministic stages;
  the retrieval stage itself was not separately latency-profiled in any doc found in this
  repo, which is itself worth measuring before committing to Phase 2).
- **Risk: this duplicates the already-duplicated `_allocate` prefix a third time**
  (§3 point 5) if per-category retrieval is bolted on without addressing that debt first.
  This is a real design decision, not an implementation detail — recommend resolving it
  explicitly in Phase 1 rather than deferring it silently into a third copy.
- **Risk: benchmarking this convincingly requires dataset work that doesn't exist yet**
  (§3 point 7 — five relevant categories are empty). Without it, "does context planning
  actually help" is a demo-only claim, not a measured one, for however long that dataset
  gap persists.
- **Trade-off, accepted deliberately**: this design explicitly does not attempt Zep-style
  bitemporal fact tracking or Letta-style self-editing memory. Both are legitimate
  directions but solve a different problem (fact correctness over time; agent-driven memory
  editing) than the one this document scopes (category-of-state planning). Conflating them
  would be a scope-creep risk worth naming and rejecting explicitly, not silently avoiding.

## Implementation roadmap

Refines `WORKING_CONTEXT_2_DESIGN.md` §10's phasing with this document's corrections
folded in; phases unchanged in name where this doc found no issue, annotated where it did.

**Phase 0 (unchanged).** New `MemoryRole` values, `WorkingContextBuilder` bucketing,
`StructuredPromptBuilder` rendering. No planner yet.

**Phase 1 — deterministic-only `ContextPlanner`, revised scope.** Implement the 5-mode
table from §1 (not the original 8-mode table — drop `BENCHMARKING` as a first-class mode,
add the `POINTED_QA`/no-plan default). Output a `ContextPlan` dataclass, logged/inspectable
the same way `AcceptanceDecision` and `RetrievalTrace` already are. **Before starting**,
resolve the `_allocate`-duplication question from §3 point 5 — decide explicitly whether a
per-category retrieval loop becomes a third duplicate of the rewrite/retrieve/merge prefix
or the point where it's factored out; this decision changes Phase 1's shape, not just
Phase 2's.

**Phase 2 — per-category `AcceptanceStage` + Gap Detection.** This is where §3 point 4's
correction matters most: budget real implementation time for changing `AcceptanceStage`'s
call contract from "one query, one candidate list" to "one category, one candidate list,"
not just wiring a new caller around the existing signature.

**Phase 3+ (largely unchanged from the existing doc)**: incremental `WorkingContextState`
materialization, LLM fallback for `CONTINUATION`/ambiguous cases (reusing `QueryRewriter`'s
client/cache/timeout/fail-open scaffolding directly rather than rebuilding it), and the
semantic self-check layer.

**New, not in the existing doc**: a **Phase 1.5 dataset pass** — populate the five empty
benchmark categories (`active_context`, `insights`, `memory_recall`, `mistake_prevention`,
`open_problems`) and add cases to the weak `decision_reconstruction` category (57.7% pass)
*before* authoring the existing doc's proposed new categories under different names. This
is cheaper than Phase 5 there and de-risks Phase 2/3's "did this help" question earlier,
using infrastructure that already exists and is currently unused.

## Recommendation

Build Phase 1 (deterministic-only, 5-mode `ContextPlanner`, `POINTED_QA` default,
inspectable `ContextPlan`) now — it is low-risk, additive, cheap, reuses concept-resolution
signal retrieval already computes, and directly demonstrates the brief's motivating case
without touching `AcceptanceStage`'s contract. Treat Phase 2 (per-category retrieval + gap
detection) as the real architecture investment — it's where the actual cost lives (§6) and
where the `_allocate`-duplication decision (§3 point 5) has to be made deliberately rather
than by default. Do the Phase 1.5 dataset pass in parallel with Phase 1, not after — the
benchmark infrastructure to measure whether any of this works already exists and is
sitting empty. Do not build Zep-style bitemporal tracking or Letta-style self-editing
memory as part of this effort; they answer different questions and would dilute this
design's actual contribution, which is real but narrower than "context planning" sounds —
it is: **a deterministic, auditable table from task-shape to required state categories,
with an explicit unknown-category signal downstream, layered on a fact-retrieval pipeline
that already does the hard part (finding correct facts) well.**
