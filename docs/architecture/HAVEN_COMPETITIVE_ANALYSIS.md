# Haven vs. the Field — Competitive Analysis

Status: **Analysis only. No implementation, no architecture changes.** Written
as an external, adversarial review — the posture is "hackathon judge with no
stake in Haven winning," not "Haven's own documentation." Haven's side is
grounded in direct source/doc citations from this repo (mirroring the method
`HAVEN_STRESS_TEST.md`, `PROJECT_STATE_EVALUATION.md`,
`PROMPT_CONTINUATION_EVALUATION.md`, `CONTINUATION_BENCHMARK_DESIGN.md`,
`GENERIC_CONTINUATION_QUERY_ANALYSIS.md`, and `benchmarks/README.md` already
established — every claim about Haven below is one of those documents'
verified findings, cited by mechanism ID (`M1`–`M17`) or scenario ID
(`S1`–`S55`) where applicable, not re-derived). The comparator systems'
claims are grounded in their own public papers, docs, and repos as of
2026-07 (cited inline); Haven's own README and architecture docs are treated
as marketing copy to be checked, not as ground truth, per this task's
instructions.

**The frame that matters most, stated once up front:** Haven is not a worse
version of mem0, Zep, or Letta. It is a different bet — deterministic,
explainable, zero-LLM-at-read-time retrieval plus a structured "resume work"
object, instead of embedding similarity plus LLM-judged conflict resolution.
That bet is real, differentiated, and defensible in the places this document
says so. It is also unfinished, unbenchmarked in the one place that would
prove its headline claim, and weaker than every comparator on the single
capability a memory system exists to provide: knowing what's still true.
Both of those sentences have to survive in the final README, or the README
is not technically defensible.

---

## 0. The five systems, one paragraph each

**Haven** (this repo). Local-first, file-backed (`Markdown` + YAML) memory
system built as a mem0 fork. Write path: one LLM call for extraction, then
fully deterministic classification-adjacent processing (`Classifier` is now
LLM-backed live, per `M17`) through a `CanonicalMatcher` → `KnowledgeUpdater`
→ `VaultWriter`. Read path: zero LLM calls — keyword + concept-graph
activation retrieval, a named-factor `DeterministicRanker`, and an
`AcceptanceStage` that can abstain. Its headline differentiator is a
structured `ProjectState`/`WorkingContext` object meant to answer "where do
things stand," rendered into an XML-ish structured prompt, gated behind a
five-way lexical query classifier. Single-user, local-only, no auth,
hackathon-stage (`README.md` "Current limitations", `HACKATHON_SCOPE.md`).

**mem0** (the upstream this repo forked). The most widely adopted open-source
memory layer (43k+ GitHub stars as of 2026). Two-phase pipeline: one LLM call
extracts atomic facts, a second LLM call classifies each as `ADD` / `UPDATE`
/ `DELETE` / `NOOP` against the existing vector-indexed store — this is
mem0's actual contradiction-resolution mechanism, and it runs on every write,
automatically, no human in the loop.[^mem0-ops] A graph variant (`Mem0g`)
adds a Neo4j-backed entity/relation layer with its own Conflict
Detector.[^mem0-arch] Retrieval is vector similarity over a three-scope
(user/session/agent) hierarchy; mem0's own 2026 paper reports a shift toward
deferring conflict resolution to retrieval time to cut write-side LLM calls
60–70%.[^mem0-2026] Reports 26% higher accuracy than OpenAI's native memory
and 91% lower p95 latency on its own benchmark suite.[^mem0-2026]

**OpenMemory (MCP)**. Not an independent architecture — a local-first,
protocol-standardized wrapper *around mem0*: "an MCP server using Mem0 under
the hood," backed by Qdrant (vectors) + Postgres, exposed over SSE so
multiple MCP-compatible clients (Claude Desktop, Cursor, Copilot) share one
memory store.[^openmemory] Its actual innovation is not memory
representation or retrieval — both are mem0's, unchanged — it's protocol
standardization (MCP) and per-app access control (`check_memory_access_
permissions`, allow/deny ACLs between apps and specific
memories).[^openmemory] For every dimension in §2 below except "developer
experience" and "cost," OpenMemory's answer is simply "mem0's answer, run
locally."

**Zep / Graphiti**. `Graphiti` is the open-source (Apache-2.0) temporal
knowledge-graph engine;[^graphiti-oss] `Zep` is the commercial product built
on it. Graphiti's core mechanism is a **bi-temporal edge model**: every fact
carries four timestamps — `t_valid`/`t_invalid` (when the fact was true in
the world) and `t'_created`/`t'_expired` (when Graphiti's own record of it
changed).[^zep-paper] New information doesn't overwrite old facts; an LLM
compares each new edge against semantically related existing edges, and on a
detected contradiction, invalidates (sets `t_invalid`) rather than deletes
the old edge — full history survives, queryable at any point in
time.[^graphiti-invalidation] Organized as three subgraph tiers: episodes
(raw messages), semantic entities/facts, and community summaries.[^zep-paper]
Reports 94.8% vs. MemGPT's 93.4% on the DMR benchmark and strong results on
LongMemEval's temporal-reasoning-heavy cases.[^zep-paper] As of 2026, Zep's
self-hostable Community Edition is discontinued — the only self-host path is
running raw Graphiti against your own Neo4j/FalkorDB/Kuzu instance; Zep Cloud
is credit-metered, $0–475+/month.[^zep-pricing]

**Letta (MemGPT)**. OS-inspired tiered memory: **core memory** (always in
context, agent persona + key user facts, edited only via explicit LLM
function calls), **recall memory** (full conversation history, disk-cache
analog, searched on demand), **archival memory** (long-term cold storage,
vector-backed, inserted/queried via tool calls).[^letta-arch] The defining
design choice is **self-editing**: the agent itself decides, at inference
time, via its own tool calls, what's important enough to write to which
tier and when to search — there is no separate deterministic write pipeline
at all; memory management *is* agent reasoning.[^letta-arch] This makes
Letta's memory quality a direct function of the base model's judgment on
every turn, for better (context-sensitive triage no fixed pipeline can match)
and worse (no two runs are guaranteed to store the same thing the same way).

[^mem0-ops]: docs.mem0.ai, "Add Memory"; Zeng, "Mem0 — Overall Architecture and Principles," Medium.
[^mem0-arch]: Chhikara et al., "Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory," arXiv:2504.19413.
[^mem0-2026]: mem0.ai/blog, "The 2026 Token Optimization Playbook"; "State of AI Agent Memory 2026."
[^openmemory]: mem0.ai/blog, "Introducing OpenMemory MCP"; a2a-mcp.org, "OpenMemory MCP."
[^graphiti-oss]: github.com/getzep/graphiti; neo4j.com/blog, "Graphiti: Knowledge graph memory for an agentic world."
[^zep-paper]: Rasmussen et al., "Zep: A Temporal Knowledge Graph Architecture for Agent Memory," arXiv:2501.13956.
[^graphiti-invalidation]: neo4j.com/blog, "Graphiti: Knowledge graph memory for an agentic world."
[^zep-pricing]: vectorize.io, "Mem0 vs Zep (Graphiti): AI Agent Memory Compared (2026)"; help.getzep.com/faq.
[^letta-arch]: Leonie Monigatti, "Virtual context management with MemGPT and Letta"; rywalker.com/research/letta.

---

## 1. The philosophy axis — not features, bets

The task is right that comparing feature checklists misses the point. Four
axes actually separate these systems, and Haven takes a real, coherent
position on all four — the question is whether that position is earned.

### 1.1 Memory retrieval vs. working-context reconstruction

mem0, OpenMemory, and (for its archival tier) Letta all answer "what's
relevant" with a **ranked list of facts**. Zep/Graphiti answers with a
**temporally-scoped subgraph** — still fundamentally a retrieval answer, just
graph-shaped instead of list-shaped. Letta's core memory is the one
comparator that isn't retrieval at all — it's a standing, agent-maintained
block that's simply *always present*, no query required.

Haven's `ProjectState` is the only comparator-adjacent attempt at **state
reconstruction as a distinct object from the ranked list it's built from** —
a deliberate second pass that re-derives "current objective / decisions /
blockers / constraints / open questions" as its own typed structure, not
just a differently-sorted fact list. This is Haven's most genuinely
philosophically distinct idea in the whole comparison. It is also, as built,
closer to Letta's core-memory *shape* (a standing summary block) than its own
docs claim — except Haven's version is deterministically re-derived from a
flat top-50 rank every single call rather than incrementally maintained
(`M5`: "no persistence and no cross-query memory... rebuilt from scratch...
every time," `project_state.py` module docstring). Letta's core memory is
expensive (self-edited, non-deterministic, costs a tool call) but
*persistent* — it doesn't need to win a ranking contest against the rest of
the vault every time someone asks a question. Haven's is cheap (zero
persistence cost, `PROJECT_STATE_EVALUATION.md` §7) but **stateless in the
one sense that matters**: two identical questions five minutes apart can
legitimately produce two different "current objectives" with no contradiction
ever surfaced (`M5`, `M10`, scenario `S10`). "Structured orientation" is the
right idea; "recomputed from a lossy ranking on every call, with no notion of
what changed since last time" is a materially weaker version of it than
either Letta's persistent core memory or Graphiti's persistent graph.

### 1.2 Similarity search vs. state reconstruction

This is really two different questions smuggled into one, and Haven answers
them differently, which is worth naming explicitly: **candidate generation**
(how do you find relevant raw material) vs. **context assembly** (how do you
turn raw material into something a downstream model can act on). Haven's
candidate generation is *not* semantic at all — it is the one system in this
comparison that explicitly rejects embeddings for retrieval, by design, in
three separate module docstrings (`keyword_candidate_retriever.py`,
`hybrid_candidate_retriever.py`, `query_resolver.py`, cited in
`GENERIC_CONTINUATION_QUERY_ANALYSIS.md` §3). Every other system in this
document uses embeddings for candidate generation, without exception —
mem0/OpenMemory (vector store), Graphiti/Zep (embeddings feed entity
resolution and semantic search alongside the graph), Letta (archival memory
is vector-backed RAG). Haven's context *assembly* is where the real
state-reconstruction idea lives (`ProjectState`), and it's genuinely
different from anyone else's assembly step — but it inherits every failure
mode of a keyword-only candidate generator underneath it (§3.5 below), and
the two design choices are not actually coupled: nothing about deterministic,
tiered context assembly requires giving up embeddings for retrieval. Haven
conflates "deterministic reasoning over candidates" with "deterministic,
lexical-only candidate generation," and only the first one is the part its
own philosophy needs.

### 1.3 Raw memories vs. structured orientation

Where Haven is least defensible against this specific framing: the structure
it renders (`ProjectState`) is honestly, verifiably **thin exactly where a
human reconstructing project state reaches first** — `identity`/`phase` are
not implemented at all, not empty-but-present (`PROJECT_STATE_EVALUATION.md`
§1(c), §5), and `current_objective` is incidentally sourced (whichever `GOAL`
happened to rank highest this call) rather than goal-directed, because `GOAL`
has no `ContextCategory` entry at all (`M14`). Meanwhile it is strong exactly
where a human resumes *last* — blockers, constraints, decision history — the
inverse of ideal ordering (`PROJECT_STATE_EVALUATION.md` §5's own table, not
disputed by this document). "Structured orientation" is a real category
distinct from raw-memory dumps (comparator A, `PROMPT_CONTINUATION_
EVALUATION.md` §9), and Haven's version genuinely beats a flat list on
information hierarchy — but none of the other four systems attempt this
category at all except Letta's core memory (persistent, not re-derived) and
Zep's community-summary tier (persistent, incrementally maintained). Haven is
not competing against nothing here; it's competing against two systems that
solved the *persistence* half of the problem and left the *structure* half
weaker, against a system that solved the *structure* half and left
*persistence* unaddressed.

### 1.4 Conservative-and-durable vs. LLM-adjudicated-and-current

The sharpest, most consequential philosophical split in this whole document,
and the one most likely to decide a technical judge's opinion: **when two
memories conflict, who decides which one is true, and when?**

- **mem0**: an LLM decides, automatically, at write time, every time
  (`ADD`/`UPDATE`/`DELETE`/`NOOP`).[^mem0-ops]
- **Graphiti/Zep**: an LLM decides, automatically, at write time, and
  *keeps the losing fact* with an invalidation timestamp rather than
  deleting it — the only comparator that resolves contradictions
  automatically **and** preserves full history.[^graphiti-invalidation]
- **Letta**: the agent itself decides, at inference time, via tool calls —
  no separate resolution step exists.[^letta-arch]
- **Haven**: nothing decides, ever, automatically. `CanonicalMatcher` only
  auto-fires `NEW`/`CONFIRM`/(strict-text-prefix-only) `UPDATE` — `SUPERSEDE`
  is fully implemented and tested but **never called** by any production
  code path; a human must invoke `supersede_decision()` manually (`M3`,
  confirmed dead-code by `STAGE_3_4_SUPERSEDE_INVESTIGATION.md:34-38`, cited
  in `HAVEN_STRESS_TEST.md`). "We're using MySQL" and "We're using Postgres
  now" — the literal, canonical test case for what a second brain is *for* —
  coexist forever, both fully valid, both equally retrievable, with no
  automatic resolution of any kind (`S28`). Worse: a same-fact elaboration
  ("MySQL, well actually now Postgres") gets accepted by the strict-prefix
  `UPDATE` path and **appends** rather than replaces, producing one canonical
  record containing both the true and false claim fused into a single string
  (`S29`) — an outcome none of the three comparators above can produce,
  because none of them do prefix-matching as their update mechanism.

Haven's own internal audit (`STAGE_3_4_SUPERSEDE_INVESTIGATION.md`, cited
directly in `HAVEN_STRESS_TEST.md` Part 1) found that even a *hypothetically
perfect* auto-supersede mechanism would only fix 23 of 91 measured
stale-context failures, and a naive version would introduce new regressions
on ~3.7% of currently-passing cases. That is a genuinely defensible reason to
ship conservative — "never silently destroy a memory" is a real, worthwhile
safety property, and it's one this document's own comparator research
confirms Graphiti pays for differently: automatic LLM-adjudicated
invalidation is not risk-free either (an LLM can misjudge "unchanged,
restated" as "replaced," precisely the `S32`/`S33` failure shape Haven's own
investigation names as the reason it hasn't shipped an alternative). But
"conservative because the alternative is risky" and "solved" are different
claims, and only the first one is currently true of Haven. Today, a Haven
user gets **zero** automatic contradiction resolution where mem0 and
Graphiti both give **some** (imperfect, LLM-dependent, but present and
running on every write). This is the single largest, most consequential gap
between Haven and the field, not a close call, and it should be named as
such in any README claim about "trustworthy second brain."

---

## 2. Dimension-by-dimension

Each row: Haven's real, verified behavior first (never assumed better),
then how the field compares. "✓" marks a genuine advantage, "✗" a genuine
gap, "≈" parity or a real tradeoff with no clear winner.

### 2.1 Memory representation

| System | Representation |
|---|---|
| Haven | Typed `KnowledgeObject`s (fact/preference/belief/decision/goal/task/etc.) as Markdown+YAML files, indexed into a separate `ConceptGraph`. |
| mem0 | Atomic natural-language facts in a vector store; optional graph layer (entities+relations) in Neo4j. |
| OpenMemory | Identical to mem0 (Qdrant+Postgres backing), plus an ACL layer mem0 itself doesn't have. |
| Graphiti/Zep | Bi-temporal knowledge graph: episodes → entities/facts (with 4 timestamps each) → community summaries. |
| Letta | Three tiers by *access pattern*, not fact type: core (in-context), recall (raw history), archival (vector cold storage). |

Haven's typed-object model is more structured than mem0's atomic-fact model
and less structured than Graphiti's full temporal graph — a genuine middle
ground, and the plain-Markdown-file storage (openable in Obsidian, greppable,
diffable) is a real, verifiable UX advantage none of the other four offer:
mem0/OpenMemory are vector-DB rows, Graphiti/Zep are graph-DB nodes, Letta's
tiers are DB-backed with no user-owned file representation at all. **✓ for
data ownership**, **≈ for expressiveness** (less than a full temporal graph,
more than atomic facts).

### 2.2 Retrieval strategy

Haven: keyword (IDF-weighted, phrase-bonused) ∪ concept-graph activation
spreading — explicitly, by design, no embeddings (`GENERIC_CONTINUATION_
QUERY_ANALYSIS.md` §3). mem0/OpenMemory: vector similarity. Graphiti/Zep:
hybrid — vector + graph traversal + temporal filtering. Letta: vector RAG
for archival, direct inclusion for core.

**This is Haven's most exposed dimension.** A purely lexical/graph-activation
retriever is *structurally guaranteed* to return zero candidates for any
query that shares no vocabulary with the vault — not probabilistically weak,
guaranteed (`M9`, verified per-query in `GENERIC_CONTINUATION_QUERY_
ANALYSIS.md` §2: `"Continue."` produces zero candidates as a mathematical
certainty of the tokenizer, independent of vault content or size). Every
comparator's embedding-based retrieval degrades gracefully on vocabulary
mismatch; Haven's fails discretely. The stated justification (no "atlas" →
"atla" fuzzy-match corruption risk, `keyword_candidate_retriever.py:144-156`)
is a real, defensible engineering tradeoff for *precision*, and it is a large
part of why Haven's own benchmark shows a 5× lower false-positive rate than
mem0 on 288 cases with short, high-precision-demanding queries (README.md,
`benchmarks/results/final_report.md`) — but the same tradeoff is the
documented root cause of a whole scenario class (S2, S3, S18, S49) failing
on exactly the query shape ("Continue implementing X," a technical error
string, a renamed module) a long-running coding-assistant memory system will
see constantly. **✗ recall on vocabulary mismatch, ✓ precision on
vocabulary match** — a real tradeoff, not a strict loss, but the "we don't do
embedding roulette" framing in the README elides that the alternative isn't
free either.

### 2.3 Context reconstruction

Haven: `ProjectState` (8 typed fields, `MEMORY_DIRECT`/`DETERMINISTIC`,
never-fabricated) + `WorkingContext` (per-topic clusters with a gist layer).
mem0/OpenMemory: none — retrieval returns a flat ranked list, full stop.
Graphiti/Zep: community summaries (persistent, incrementally maintained
cluster abstractions) plus temporal filtering ("facts true as of time T").
Letta: core memory *is* the reconstruction — always-present, agent-curated.

Haven is the only system besides Letta/Zep attempting reconstruction beyond
"here are k relevant strings," and its reconstruction is provably higher-
fidelity in one specific way none of the three others can claim: **every
field traces to a real `KnowledgeObject` via a shared `[N]` index, verified
deterministic under shuffled input order** (`PROJECT_STATE_EVALUATION.md`
§8, `TestProjectStateBuilderDeterminism`). Zep's community summaries and
Letta's core memory are both LLM-synthesized text with no equivalent
per-claim provenance guarantee. **✓ provenance**, **✗ persistence** (Haven's
reconstruction is recomputed and can drift call-to-call; Zep's and Letta's
are standing objects that only change when something actually changes).

### 2.4 Long-running software-engineering support

None of the five systems were purpose-built for this exact niche (mem0/
OpenMemory/Letta are general-purpose agent memory; Zep targets enterprise
customer-support/temporal use cases). Haven is the only one whose own
internal docs explicitly frame "continue implementing X" as the target
scenario and built a dedicated benchmark category for it
(`CONTINUATION_BENCHMARK_DESIGN.md`). That focus is real and is Haven's
clearest niche differentiation — but the benchmark built to prove it works
is a 10-case pilot that, by its own admission, doesn't yet measure the thing
it's supposed to (§2.12). No comparator has an equivalent SWE-continuation
benchmark either, so this is currently an **untested claim of focus**, not a
demonstrated capability gap in Haven's favor.

### 2.5 Contradiction handling

Covered in depth in §1.4. Restated as a ranking: **Graphiti/Zep > mem0 ≥
Letta > Haven**, unambiguously, as currently shipped. Graphiti wins outright
because it's the only one that resolves *and* preserves history losslessly.
mem0 resolves but deletes the loser (`DELETE` op) — real but less complete
than Graphiti's invalidation-not-deletion. Letta's resolution quality is
whatever the base model does in a tool call — unmeasured, un-guaranteed, but
at least *attempted* automatically. Haven attempts nothing automatically;
`Guidance`'s "prefer higher-confidence/recent memory on conflict" instruction
to the downstream LLM (`structured_prompt_builder.py:161-165`) is real and
does work as a safety net (`HAVEN_STRESS_TEST.md` Part 4) but is fundamentally
a **downstream mitigation**, not contradiction *handling* — it asks a
different, later model to do at inference time what Haven itself declined to
do at write time, for every single query that touches a contradicted fact,
forever, with no learning from having done it before.

### 2.6 Project state

Haven's `ProjectState` is a category of object literally absent from every
comparator except Letta's core memory (which is persona/user-fact focused,
not project/task focused, and per-agent rather than per-project) — this is
Haven's single most novel, hackathon-defensible idea. But per §1.3/`M5`/`M14`/
`M15`, it's incidental (not goal-directed), stateless (recomputed, not
incremental), and unscoped (no `project_key` — a multi-project vault can
blend two projects' blockers with **zero signal that it happened**, `M15`,
`S12`, `S13`, `S50`). **✓ conceptual novelty, ✗ execution maturity.**

### 2.7 Working context reconstruction

Haven's `WorkingContext` groups candidates by concept and renders a gist
layer (`WorkingContextState`) above full evidence — real gist-then-detail
structure (`PROMPT_CONTINUATION_EVALUATION.md` §3 confirms this specific
pairing is genuinely non-redundant, unlike the `ProjectState`/
`WorkingContextState` overlap one level up). But its section ordering is a
raw UUID sort with no relevance signal (`working_context_builder.py:160`,
`M12`), and — this is worth stating plainly since it is checkable by any
technically literate judge who opens the Retrieval Inspector or reads a raw
prompt — every `TOPIC`-kind context's section title *used to render as a raw
concept UUID* and (per this repo's own session-fix log,
`PROMPT_CONTINUATION_EVALUATION.md` "Implementation status") now resolves to
a human label only along two of Haven's several query paths, not all of
them (`ContextBuilder`'s plain-text `query()` path still has no equivalent,
per `S55`). No comparator has this specific class of bug because none of
them render an opaque internal identifier as a user-facing section header in
the first place.

### 2.8 Explainability

**Haven's clearest, least contestable win.** The Retrieval Inspector's
per-candidate score breakdown (activation/keyword/confidence/recency, with a
recorded rejection *reason* for every candidate the acceptance stage
declined) has no equivalent in any of the four comparators. mem0/OpenMemory
expose a similarity score at best; Zep exposes temporal validity ranges but
not a ranking-factor breakdown; Letta exposes nothing about *why* the agent
chose to write/recall something, because that choice was made inside an
opaque LLM tool call, not a scored pipeline. This is a real, structural
consequence of Haven's zero-LLM-at-read-time design — the other four
systems' retrieval quality depends, at least partly, on an LLM call whose
internal reasoning cannot be inspected the way a scoring formula can. This
one dimension is where "deterministic and inspectable" earns its keep most
convincingly. **✓, decisively.**

### 2.9 Determinism

Same root cause, same verdict, restated precisely: Haven's read path issues
**zero** LLM calls (README's own architecture diagram, verified against
`engine.py`'s `_run_retrieval` — the only LLM calls in the whole system are
one at write-time extraction and one, now, at write-time classification per
`M17`). mem0's retrieval is deterministic (vector similarity is a pure
function) but its *write*-time conflict resolution is LLM-adjudicated, same
as Graphiti's invalidation and Letta's entire memory-management loop.
**Haven is the only system in this comparison where a repeated query against
an unchanged store is guaranteed byte-identical** (`HAVEN_STRESS_TEST.md`
Part 4's determinism claim, independently corroborated by
`PROJECT_STATE_EVALUATION.md` §8's shuffled-input-order test) — with the one
honest caveat this document must not omit: `age_days`/recency scoring is
computed against wall-clock `datetime.utcnow()` on every call, so a
candidate sitting exactly on a score-gap-cut boundary can theoretically flip
sides between two calls if enough real time elapses between them (`M10`,
scenario `S10`) — a documented, low-severity, non-hypothetical exception to
the "always identical" framing. **✓, with one asterisk the README should
carry.**

### 2.10 Scalability

**Haven's clearest, least contestable loss.** `PROJECT_STATE_EVALUATION.md`
§7 (Haven's own internal evaluation, not this document's invention)
concludes explicitly: "No, not as currently wired... the actual scaling
failure is qualitative... a flat top-50 ranking across nine-plus categories
increasingly reflects recency and raw score, not category coverage" as a
vault grows into the thousands of memories. Graphiti was *purpose-built* for
this problem — bi-temporal invalidation means old facts don't need to be
re-ranked against new ones to stay correct, and community summaries give it
a bounded-size "orientation" layer that doesn't degrade the way Haven's flat
top-K does. mem0's three-scope hierarchy (user/session/agent) gives it a
natural sharding boundary Haven's single flat vault lacks entirely (`M15`
names this as a known, unaddressed gap). Letta's tiered core/recall/archival
split is explicitly an OS-memory-hierarchy answer to the same scaling
question. Haven has no comparable mechanism today; its own docs recommend
one (Phase B "incremental materialization," `PROJECT_STATE_DESIGN.md`) but
it does not exist in the shipped code. **✗, and Haven's own documentation
already says so more bluntly than this document needs to.**

### 2.11 Cost

Haven: zero embedding infrastructure, zero vector DB, one LLM call per write,
zero LLM calls per read, plain-file storage — the cheapest system in this
comparison to run at small-to-medium scale, and the only one with a
genuinely zero-API-key offline demo mode (fake-LLM-scripted, `README.md`
Quick Start). mem0/OpenMemory need a vector DB (Qdrant, Postgres) plus
per-write and often per-read LLM calls. Zep Cloud is credit-metered,
$0–475+/month with no self-hosted equivalent since Community Edition's
deprecation — self-hosting means operating your own Neo4j/FalkorDB
cluster.[^zep-pricing] Letta's cost scales with how often the agent decides
to call a memory tool, which is itself model-judgment-dependent and thus
less predictable than any of the deterministic-write systems. **✓ for
Haven, clearly**, though it's worth naming the trade this buys: Haven's
zero-read-cost claim is a direct consequence of the retrieval-recall
weakness in §2.2 — cheap because it does less work per query, not because it
does the same work more efficiently.

### 2.12 Benchmark quality

This is where Haven's own internal audits (already on disk in this repo,
independent of this document) are the most damaging source available, and
should be read directly rather than summarized softly. Three separate,
serious findings, all already true before this document was written:

1. The **main** 288-case suite is real and unusually rigorous *for what it
   covers* — it includes trivial baselines (`return_all`/`recency`/`bm25`/
   `embedding`), Haven-specific ablations (`haven_no_ontology`/
   `haven_no_keyword`/`haven_no_recency`), and a distractor-count sweep
   (`run_distractor.py`) — infrastructure genuinely more rigorous than what's
   publicly documented for mem0's or Zep's own benchmark suites (both report
   headline numbers on LOCOMO/DMR/LongMemEval without publishing an
   equivalent ablation-plus-distractor-sweep harness for public
   reproduction). **This is a real, defensible strength of Haven's
   engineering culture, not spin.**
2. But 5 of 19 dataset category directories are **completely empty** (no
   files), 3 more contain only 0-byte placeholders that are silently skipped
   at load time (`benchmarks/README.md`'s own category table, `M8`) — so the
   288-case number, while real, is not "19 categories of coverage," it's 11.
3. The **continuation benchmark** — the one built specifically to test
   Haven's headline `ProjectState` differentiator — is a 10-case pilot
   (`resume_coding` only, of 8 designed categories) that its own design doc
   states plainly was, until a recent ingestion fix, structurally incapable
   of exercising `ProjectStateBuilder` at all (Critical-1: every turn stored
   as generic `FACT`, `CONTINUATION_BENCHMARK_DESIGN.md` "Ingestion update").
   Post-fix, the same document's own §11 postscript states the *retrieval-
   seeding* problem remains: the pilot's two stock query phrasings mostly
   still fail to populate `ProjectState` for a reason unrelated to ingestion
   (`benchmarks/README.md`'s "Retrieval-seeding caveat"). **As of this
   writing, there is no trustworthy Haven-vs-baseline number for the one
   capability that most differentiates Haven from the field.** LOCOMO itself
   is independently documented to have gold-answer-quality problems and a
   growing test-vs-real-world performance gap (models scoring near-perfectly
   on LOCOMO drop to 40–60% on harder, decision-relevant benchmarks like
   MemoryArena) — so "we don't cite LOCOMO" would be a defensible position
   Haven could take, but Haven doesn't have a working alternative to cite in
   its place yet either.

**Verdict: Haven's benchmark *infrastructure* is a genuine strength; Haven's
benchmark *coverage of its own core claim* is currently the single weakest,
most reputationally exposed part of this whole comparison** — weaker than
simply having no benchmark, because a headline number exists and would look
authoritative to a judge who doesn't read `CONTINUATION_BENCHMARK_AUDIT.md`'s
Critical-1 finding.

### 2.13 Failure modes

| System | Characteristic failure |
|---|---|
| Haven | Silent, structural: zero candidates for lexically-novel queries (§2.2); stale facts resurface with no distinction from current ones (§1.4); "gaps" can't distinguish "not in vault" from "outranked" (`M2`, `PROJECT_STATE_EVALUATION.md` §1(a)) — every one of these fails *quietly*, never with an error. |
| mem0 | LLM misclassifies `ADD`/`UPDATE`/`DELETE` — can silently delete a still-true fact it judged superseded, no undo path documented. |
| Graphiti/Zep | LLM misjudges a contradiction (invalidates a fact that was actually just restated, the `S32`/`S33` shape) — mitigated by keeping history, but the *live* answer can still be wrong for a window. |
| Letta | Agent forgets to call the memory-write tool, or calls it inconsistently across runs — memory quality is gated on the base model remembering to manage its own memory. |
| OpenMemory | Inherits mem0's failure mode exactly, plus a new one: ACL misconfiguration silently denying a legitimate app's access. |

Haven's failure mode is the most *diagnosable* (every one of the above is
independently documented, reproducible, and traced to a named mechanism in
this repo's own audits) but not obviously the least *harmful* — "fails
silently and consistently" and "fails silently and inconsistently" are both
silent failures from the end user's chair.

### 2.14 Developer experience

Haven: open a folder as an Obsidian vault, read raw Markdown, grep the vault,
inspect every retrieval decision live in a dashboard — genuinely excellent
for a developer who wants to *understand* the system, and the one-click,
API-key-free demo is a real, verifiable advantage for anyone evaluating it
cold (this document did not need any credentials to read Haven's own
architecture). mem0/OpenMemory have the most mature SDK/docs/integration
ecosystem of the five by a wide margin (43k+ stars, official integrations
across major agent frameworks). Zep's docs are enterprise-oriented and
assume a paid tier for anything beyond prototyping since Community Edition's
removal. Letta's self-editing model means less integration code to write per
use case, at the cost of behavior that's harder to unit-test (it's the
agent's own judgment, not a fixed pipeline). **≈** — Haven wins on
"inspectability for a curious developer," loses on "ecosystem maturity for a
developer who just wants it to work."

---

## 3. Hackathon judge verdict

**Strongest advantages.** (1) The Retrieval Inspector's per-candidate score
breakdown plus recorded rejection reasons — no comparator has this, and it's
not a UI nicety, it's a structural consequence of the zero-LLM-read-path
design that nothing else in this field currently makes. (2) Determinism as a
verified, tested property (shuffled-input-order equality), not a marketing
adjective. (3) Plain-file, vendor-independent storage. (4) A genuinely more
rigorous *benchmark harness* (ablations, baselines, distractor sweep) than
what's publicly visible for any of the four comparators, even though its
*coverage* is thin (§2.12).

**Weakest disadvantages.** (1) Zero automatic contradiction resolution,
against a field where three of four comparators have some (§1.4/§2.5) — this
is a "second brain" tool's core promise, and Haven currently ships without
it. (2) Scalability: Haven's own internal audit already concedes this
doesn't work past a few hundred memories in its current form (§2.10). (3)
The one benchmark built to prove Haven's headline differentiator currently
can't (§2.12) — and the gap is easy for any judge who reads the repo's own
docs to find, since Haven's audits document it themselves. (4) A retrieval
layer that returns exactly zero results for the single most natural
continuation-query phrasing a real user would type (`"Continue."`, `M9`) —
reproducible in one query, in under a minute, by any judge who tries it.

**Most unique ideas.** (1) `ProjectState` as a distinct, typed
reconstruction object separate from the ranked candidate list it's built
from — genuinely novel among the four comparators, none of which attempt
this specific shape. (2) The abstention design — `AcceptanceStage` returning
*nothing* rather than padding a response with weak matches is a real,
uncommon commitment; every comparator's default retrieval returns top-k
regardless of quality. (3) Rejecting embeddings entirely as a design
constraint, defended in writing across three modules — contrarian, and
(per §2.2) genuinely a mixed bag rather than obviously wrong, which makes it
more interesting than it would be if it were simply a limitation nobody
chose on purpose.

**Least convincing claims.** (1) "No embedding roulette" as an unqualified
positive, when the alternative (zero-recall on vocabulary mismatch) is
arguably a worse failure mode for a memory system than an occasionally
imprecise embedding match — a judge who tries `"same bug as before"` or a
verbatim error string (`S18`) will find nothing, and "we don't do embedding
roulette" doesn't read as an adequate explanation for that in the moment. (2)
Any framing that implies `ProjectState` "reconstructs where things stand" as
a general capability, when its own internal evaluation found it structurally
missing `identity`/`phase` and only reachable via one of five lexical query
classifications (§1.3) — a technically literate judge asking "what is this
project" or "what should I do next?" (the two most natural orientation
questions) has a real, non-trivial chance of getting nothing (`S9`, `S23`).

**Easiest criticisms.** "Type `'Continue.'` and see what happens" — zero
candidates, guaranteed, reproducible in five seconds, no special knowledge
required. "Ask the same orientation question two different ways" — visibly
different completeness of answer depending on which of eight fixed lexical
patterns matched (`S27`, the single most damaging-to-trust scenario
`HAVEN_STRESS_TEST.md` itself ranks #1). "State a contradiction and ask which
one is true" — both facts surface, unresolved, forever (`S28`).

**Hardest criticisms to answer.** "If determinism is the whole value
proposition, why does the write path already depend on a live LLM classifier
(`M17`) whose accuracy Haven can't guarantee turn to turn?" — this is a real
tension the README's "no stage's correctness may depend on an LLM behaving a
particular way at runtime" framing (Decision 002, cited in the README) does
not fully resolve, since `ProjectState`'s usefulness in real (non-benchmark)
usage is now a probabilistic function of that classifier's per-turn accuracy,
exactly as `M17` documents. "Why is the fix for stale/contradictory memories
(`SUPERSEDE`) fully built, tested, and *still not wired in*?" — the honest
answer (measured regression risk, `STAGE_3_4_SUPERSEDE_INVESTIGATION.md`) is
defensible engineering but does not change the fact that the thing the
product is named and marketed for is the thing currently disabled by
deliberate choice.

---

## 4. README claims — triage

### Safe claims (measured, verifiable, currently true)

- Zero LLM calls in the read path (verified against `engine.py`).
- Deterministic retrieval: same vault + same query → same result, verified
  under shuffled candidate-order tests (`PROJECT_STATE_EVALUATION.md` §8).
- Every retrieved fact traces to a real, inspectable source via the shared
  `[N]`/provenance index (§2.3, §2.8).
- The acceptance stage can abstain and return nothing (a real, tested,
  first-class outcome, distinct from every comparator's default top-k
  behavior).
- Local-first, plain-Markdown storage with no vendor lock-in.
- The 278→110 candidates / 0.301→0.679 precision / 0.500→0.100 false-positive
  numbers against the mem0 baseline, **scoped explicitly to the 288-case main
  suite** (not the continuation benchmark).
- `SUPERSEDE`'s absence from the automatic write path is a known, documented,
  deliberate limitation — the README's existing "Current limitations" section
  already states this correctly and should keep doing so.

### Claims needing benchmark evidence before they can be made

- Anything implying `ProjectState`/continuation quality is validated —
  the only benchmark built for this (`resume_coding`, 10 cases) has an
  open, self-documented retrieval-seeding gap that leaves `<ProjectState>`
  near-empty on most cases even post-ingestion-fix (`benchmarks/README.md`).
  Do not cite a "continuation benchmark" number until Phase 4/5 (§10 of
  `CONTINUATION_BENCHMARK_DESIGN.md`) ships and the seeding gap is closed.
- Any precision/recall comparison against Zep or Graphiti specifically —
  none has been run; the only baseline comparison that exists is against
  mem0 on the main suite.
- Any claim that Haven "handles contradictions" (as opposed to "surfaces
  them to a downstream model with confidence/recency framing") — §1.4/§2.5
  establish this is currently the field's weakest point for Haven, not a
  strength, and it needs either a shipped `SUPERSEDE` path or a benchmark
  quantifying the current gap before any positive claim is made.
- Any scaling claim ("works for years of memories") — `PROJECT_STATE_
  EVALUATION.md` §7 already concludes this doesn't hold past a few hundred
  memories in the current architecture; no benchmark currently measures the
  degradation curve directly (tier-based `short`/`medium`/`long` continuation
  cases would, once Phase 4 exists).

### Claims requiring future work before they can be made at all

- "Haven tracks project phase / knows what's done vs. in progress" —
  `identity`/`phase` are unimplemented, not just unmeasured (§1.3).
- "Haven works across multiple concurrent projects" — no `project_key`
  scoping exists anywhere in the pipeline (`M15`); cross-project fact bleed
  is a documented, unmitigated risk with zero in-output signal when it
  happens.
- "Ask Haven anything and it'll orient you" — orientation is gated behind a
  five-way lexical classifier that provably misses several of the most
  natural orientation phrasings (`S7`, `S23`, `S24`, `S25`, `S26`).
- Any claim resembling "Haven remembers what you decided and won't
  contradict itself" — currently the opposite is true by default (§1.4).

### Claims that should not be made

- Any comparison framing Haven as strictly "better" than mem0/Zep/Graphiti/
  Letta on memory quality — the honest comparison is a tradeoff profile
  (explainability/determinism/cost vs. contradiction handling/scale/
  ecosystem maturity), and a "better" framing invites exactly the kind of
  five-second reproducible counterexample (`"Continue."`, a stated
  contradiction) that damages credibility faster than a modest claim would.
- "No embedding roulette" as an unqualified selling point without the
  paired admission that the alternative is zero-recall-on-vocabulary-
  mismatch, not "more reliable recall" — the current README phrasing
  ("Vector similarity, opaque" vs. "Hybrid keyword + concept-graph
  activation, deterministic," in the "How that compares" table) implies a
  strict improvement where the real relationship is a tradeoff.
- Anything using the word "trust" or "second brain" without an adjacent,
  equally prominent statement that contradiction resolution is manual today
  — trust claims are the highest-scrutiny claims in this whole space (it's
  literally competitors' stated design goal too), and Haven is currently
  weakest, not strongest, on the mechanism that claim depends on.
- Citing the continuation-benchmark pilot's pass rate, if reported at all,
  as evidence of "reconstruction quality" — per `HAVEN_STRESS_TEST.md`
  Scenario 54's own severity rating ("Critical... anyone citing this
  benchmark's number as evidence of continuation quality is citing a number
  that cannot currently support that claim") — that finding is Haven's own,
  already on disk, and should not be quietly dropped when writing the
  README.

---

## 5. Bottom line

Haven is a real, differentiated bet, not a re-skinned mem0 fork — the
zero-read-time-LLM, fully inspectable, abstention-capable retrieval pipeline
is a genuine, verified, structurally-earned advantage over all four
comparators on determinism and explainability specifically, and the plain-
file storage plus zero-cost demo are real developer-experience wins. But
"deterministic and explainable" is a claim about *how* Haven decides, not
*what* it knows — and on the second question, the field currently beats
Haven cleanly: three of four comparators resolve contradictions
automatically where Haven resolves none; Graphiti was purpose-built for the
scale problem Haven's own audits admit it doesn't solve yet; and the one
benchmark that could prove Haven's headline "resume my project" claim isn't
there yet, by Haven's own internal audit's own words. A README that leads
with the explainability/determinism story, states the contradiction-handling
and scaling gaps as plainly as this document's own source material already
does, and doesn't cite the continuation benchmark until it's actually
measuring what it claims to — that README would survive a technical judge's
five minutes of poking at it. The version implied by the current draft's
"How that compares" table, which frames every row as a clean Haven win,
would not.
