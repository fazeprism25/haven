# ENTITY_CAT Candidate-Generation Failure Investigation

Status: Analysis only. No production code, benchmark dataset, or ontology
file was modified to produce this document. Every number below was
measured by running Haven's real, unmodified pipeline (`OntologyPipeline`
-> `HybridCandidateRetriever` -> `MemoryEngine.query_with_trace`) against
the current working tree, using a throwaway, uncommitted harness script
(`scripts/entity_cat_audit.py`, written for this investigation and not
part of the repo — deleted after use, same convention the prior
`CANDIDATE_GENERATION_DECISION.md` audit used for its own `classify.py`).
Version: 1.0

---

# 0. Method, and a correction to the brief that kicked this off

The task brief that scoped this investigation stated the current
candidate-generation-failure bucket breakdown as `ENTITY_CAT 25,
ONTOLOGY_COV 17, ROLE_PROPERTY 3, PARAPHRASE 2` (47 total, LEXICAL fixed to
0). Per this task's own instruction ("verify against current codebase, do
not assume previous discussions are correct"), every one of those numbers
was independently re-derived rather than taken on faith — exactly the
posture the repo's own prior audit (`docs/architecture/
CANDIDATE_GENERATION_DECISION.md`) took toward an earlier claim ("107/288
robust benchmark failures... proposed IS_A bridge") that turned out not to
be backed by any file in the repo.

**What verified cleanly.** The top-level counts are correct and
reproducible: 250 gradeable cases (38 excluded as ungradeable — same
`concept_consolidation`/`beliefs` synthesis cases the prior audit
excluded, for the same reason), **177 PASS, 47 candidate-generation
failures, 26 ranking/acceptance failures**. `LEXICAL` is genuinely 0 in
the sense that all 47 remaining cases have **zero** keyword-token overlap
between query and target even after the widened `_VARIANT_GROUPS` table
(confirmed directly against `KeywordCandidateRetriever` — batches 2–4 of
`_VARIANT_GROUPS`, ~50 groups, are real and present in
`obsidian/memory_engine/keyword_candidate_retriever.py:362-509`).

**What did not verify.** The ENTITY_CAT/ONTOLOGY_COV/PARAPHRASE
sub-bucket split does not reproduce under a mechanical, reproducible
rubric, and — more importantly — **6 of the 47 failures are not actually
entity/category gaps at all: they are additional, still-open lexical
variant gaps** that the existing Phase 1 lexical-normalization mechanism
already fixes once three more variant pairs are added (empirically
confirmed below, not assumed). No report file in this repo backs the
25/17/3/2 split, so this document supersedes it with a rubric stated
explicitly enough to be checked.

**Classification rubric used** (applied mechanically to all 47, not
eyeballed):
- **Residual LEXICAL**: query and target share no token today, but do
  share a token once one additional, low-risk inflectional pair (of the
  same closed-table kind Phase 1 already uses) is added — confirmed by
  actually monkeypatching `_CANONICAL_FORM` and re-running the real
  pipeline, not just inspected by eye.
- **ENTITY_CAT**: the query contains a category noun, and the required
  answer text, as it appears verbatim in the source fact, is a
  proper-noun-shaped span (capitalized in the original text, not merely
  by sentence-initial position) that `ConceptDetector` already promotes to
  a `Concept`.
- **ONTOLOGY_COV**: same category-noun-in-query shape, but the answer
  text is lowercase prose (or capitalized only as a sentence-initial
  accident, e.g. "Multiple specialized agents" — the real content is
  lowercase) that `ConceptDetector`'s capitalized-span heuristic never
  promotes to a `Concept`.
- **ROLE_PROPERTY**: the query's only signal is a relationship-implying
  verb ("live") with **no category noun anywhere in the query** — needs a
  verb-to-relationship-type mapping, not a category bridge.
- **PARAPHRASE**: no category-noun/entity relationship connects query and
  answer at all — abstract beliefs/objectives, date/time answers,
  opinion/reasoning questions, "enumerate everything" queries, or
  verb/synonym mismatches with no shared vocabulary.

## Verified counts

| Bucket | Count | % of 47 |
|---|---|---|
| **ENTITY_CAT** (genuine) | **14** | 30% |
| PARAPHRASE | 14 | 30% |
| ONTOLOGY_COV | 10 | 21% |
| Residual LEXICAL (new finding) | 6 | 13% |
| ROLE_PROPERTY | 3 | 6% |
| **Total** | **47** | 100% |

`ROLE_PROPERTY = 3` is the one sub-count that matches the brief exactly
(the three "Where does the user \[currently\] live?" cases —
`supersession_basic_026/027/028`). Everything else diverges, chiefly
because 6 cases the brief likely counted under `ENTITY_CAT` are cheaper
lexical fixes in disguise, and several abstract-belief/date/enumeration
cases appear to have been folded into a much smaller `PARAPHRASE` bucket
than a token-overlap-driven rubric supports.

### The 6 residual-lexical cases (new finding, in scope because it changes what "ENTITY_CAT" actually contains)

Each was confirmed by monkeypatching `_CANONICAL_FORM` with one new pair
and re-running the real pipeline end-to-end — not inferred from reading
code:

| benchmark_id | Missing pair | Query | Target | Result after patch |
|---|---|---|---|---|
| `concept_consolidation_basic_050` | `named`->`name` | "...what is its **name**?" | "I have a cat **named** Biscuit." | **PASS** |
| `decision_reconstruction_basic_004` | `decided`->`decide` | "Where did the user **decide** to go..." | "**Decided** on Portugal..." | **PASS** |
| `decision_reconstruction_basic_015` | `decided`->`decide` | "Which city did the user **decide** to move to?" | "**Decided** on Raleigh..." | **PASS** |
| `decision_reconstruction_basic_021` | `decided`->`decide` | "Which breed did the user **decide** to adopt?" | "**Decided** on a Border Collie..." | **PASS** |
| `decision_basic_004` | `prioritized`->`priority` | "Which subsystem should be **prioritized** next?" | "Manager AI is currently a higher **priority**..." | **PASS** |
| `supersession_basic_006` | `planned`->`plan` | "What embedding solution is currently **planned**?" | "That **plan** has been replaced with FastEmbed." | candidate found, ranking still rejects it (out of this investigation's scope) |

This is a materially cheaper fix than anything discussed below — same
mechanism, same review discipline, same file
(`obsidian/memory_engine/keyword_candidate_retriever.py`) as the Phase 1
work already shipped. **Recommendation 0, orthogonal to everything else
in this document: add these 3 pairs (`name/named`, `decide/decides/
deciding/decided`, `prioritize/prioritizes/prioritizing/prioritized/
priority/priorities`) to `_VARIANT_GROUPS` before any ontology work.** It
closes 5 more candidate-generation failures outright and turns a 6th into
a ranking-only problem, at zero architectural cost.

---

# 1. Audit of the 14 genuine ENTITY_CAT failures (Task 1)

For each: benchmark, query, expected answer, what candidate generation
actually found, and the exact reason the target never became a
candidate. In every case the reason is identical in shape: **the query's
category noun and the target fact's proper-noun answer share zero
tokens**, so `KeywordCandidateRetriever` finds nothing, and no `Concept`
named after the category noun exists for `QueryResolver`/
`ActivationSpreader` to seed from, so the ontology path finds nothing
either — confirmed by `matched_by_keyword=False, matched_by_ontology=False`
for the target in every case, and a keyword match on an *unrelated*
(often stale, pre-supersession) fact where one exists.

| benchmark_id | Query | Category noun | Expected answer | What was found instead | Missing candidate |
|---|---|---|---|---|---|
| `decision_reconstruction_basic_009` | Which **laptop** did the user decide to buy? | laptop | ThinkPad | 1 near-miss: the fact listing all 3 laptop options (shares "laptop") | "Went with the ThinkPad..." (0 shared tokens with query) |
| `decision_reconstruction_basic_014` | What **name** did they decide on for the startup? | startup (name) | Northbeam | 2 near-misses: the fact listing the 3 name candidates, a Fieldstone-domain fact | "We're going with Northbeam..." |
| `decision_reconstruction_basic_016` | What **eating plan** did the user decide to follow? | eating plan | Mediterranean | 0 candidates | "Going with the Mediterranean approach..." |
| `decision_reconstruction_basic_018` | Which **CI/CD provider** did the team decide to use? | CI/CD provider | GitHub Actions | 1 near-miss: the fact listing all 3 CI/CD options | "Going with GitHub Actions..." |
| `decision_reconstruction_basic_019` | Which **note-taking app** did the user decide to use? | note-taking app | Obsidian | 2 near-misses: the fact listing all 3 app options, a Notion-collaboration fact | "Going with Obsidian..." |
| `decision_basic_002` | Which **system** should design a new benchmark framework? | system (generic) | GPT | 0 candidates | "I decided to use GPT for architecture and planning." |
| `basic_020` (decisions) | What **background job system** did the user ultimately decide to use? | background job system | Celery | 2 near-misses: cron-approach fact, the BullMQ/Sidekiq/cron options fact | "...I'll go with Celery instead..." |
| `supersession_basic_003` | What is the current **model** strategy? | model | GPT, Qwen | 0 candidates | "I replaced that plan with GPT for planning and Qwen for coding." |
| `supersession_basic_007` | Which **component** currently has priority? | component (generic) | Manager AI | 1 near-miss: the superseded "GraphRAG first" fact | "That decision has been replaced. Manager AI comes first." |
| `supersession_basic_024` | What **deployment approach** is currently preferred? | deployment approach | Ollama | 0 candidates | "I've shifted toward running as much as possible locally with Ollama." |
| `supersession_basic_030` | What is the user's current job title and **company**? | company | Acme | 0 candidates | "I got promoted to senior software engineer, still at Acme." |
| `supersession_basic_033` | What **database** does the user currently use? | database | PostgreSQL | 1 stale hit: "I use MongoDB for my **database**." (shares literal token "database") | "I migrated everything to PostgreSQL." |
| `supersession_basic_034` | What **frontend framework** is the user currently using? | frontend framework | Svelte | 1 stale hit: "I started with Vue for the **frontend**." | "I've now moved the whole team to Svelte." |
| `supersession_basic_054` | What **tracking tool** is the team currently using? | tracking tool | Linear (Jira also valid) | 2 near-misses: an unrelated offsite fact, "We decided to use Jira for **tracking**." | "...we moved everything to Linear because Jira got too slow..." |

**A pattern worth naming explicitly**: 4 of the 14
(`decision_reconstruction_009/014/018/019`) are "compare N options, pick
one" conversations where the *comparison* turn shares a keyword with the
query (laptop/startup name/CI-CD/note-taking app) but the *decision* turn
that actually names the winner does not repeat the category word — it
just says "Going with X." The 4 `supersession_*` cases with a stale hit
(`003`/`033`/`034`/`054`... `003` has none, correction: `033`, `034`,
`054`, `007`) show the same shape one level worse: the category word
appears **only** in the now-superseded fact ("I use MongoDB for my
**database**"), so candidate generation surfaces the *wrong, stale*
answer's fact as its only lead while missing the current one entirely.

---

# 2. Categorization into architectural patterns (Task 2)

Grouped by what kind of thing the category noun names, counted only from
the 14 confirmed cases:

| Category → Concept type | Count | Examples |
|---|---|---|
| Product/tool → category (dev tools, apps) | 5 | laptop→ThinkPad, CI/CD provider→GitHub Actions, note-taking app→Obsidian, background job system→Celery, tracking tool→Linear/Jira |
| Model/model family → category | 2 | model→GPT (x1 case), model strategy→GPT+Qwen (x1 case, **two** instances of one category in one fact) |
| Company → category | 1 | company→Acme |
| Database → category | 1 | database→PostgreSQL |
| Frontend framework → category | 1 | frontend framework→Svelte |
| Deployment approach/tool → category | 1 | deployment approach→Ollama |
| Internal architecture component → category | 2 | subsystem→GPT ("system", generic), component→Manager AI ("component", generic) |
| Personal/lifestyle choice → category | 2 | eating plan→Mediterranean, startup name→Northbeam |

A second, orthogonal and more load-bearing split — **is the query's own
category word specific enough to alias safely**, i.e. is it a distinctive
term ("database", "laptop", "CI/CD provider") vs. a generic architecture
word that shows up across unrelated contexts ("system", "component",
"deployment approach"):

| Query category-noun specificity | Count |
|---|---|
| Specific, safe to alias (laptop, database, frontend framework, company, model, embedding solution, eating plan, startup name, CI/CD provider, note-taking app, background job system, tracking tool) | 11 |
| Generic, unsafe to alias without over-activation risk ("system", "component", "deployment approach") | 3 |

This second split is the one that actually determines the ceiling in
Task 4 — product-category taxonomy shape only tells you what the
category *is*; specificity tells you whether indexing it is *safe*.

---

# 3. Ontology readiness (Task 3)

Checked directly against the running pipeline for all 14 (not assumed):

| Check | Finding |
|---|---|
| Does a category `Concept` (e.g. "Database", "Laptop", "Model") already exist? | **No, for all 14 (0/14).** Confirmed: no category-shaped concept exists anywhere in the ontology — `ConceptDetector`'s capitalized-span heuristic only ever promotes proper nouns from `canonical_fact` text, and category words ("database", "laptop", "model") are never capitalized in ordinary prose, so they are never candidates for promotion in the first place. Grepped the codebase for any pre-seeded/bootstrap category concepts ("Database", "Framework", "Cloud Provider", `seed_concepts`) — none exist. |
| Is the **instance** `Concept` missing? | **No — it already exists for all 14 (14/14).** Verified directly: `ConceptDetector.detect_from_text` already extracts PostgreSQL, Svelte, GitHub Actions, GPT, Qwen, Celery, Ollama, Acme, ThinkPad, Northbeam, Mediterranean, Linear, Jira, and Manager AI as `Concept` labels from their source facts. The instance side of the bridge needs no new detection work. |
| Is the `IS_A` relationship missing? | **Yes, universally.** `OntologyRelationshipType.IS_A` is a defined enum value (`obsidian/ontology/enums.py:46`) but is **never emitted** anywhere in the codebase — `OntologyManager.propose()` (`obsidian/ontology/ontology_manager.py:122-159`) only ever proposes `RELATED_TO` edges between co-occurring detected concepts. Confirmed by grep: `IS_A` appears only in the enum definition, its docstring, `activation_spreader.py`'s example, and tests — zero production call sites construct one. This is a real, unused gap, not a partially-built feature. |
| Is the query using different terminology than what would need to be indexed? | **No, for 11/14.** The query's category noun (database, laptop, company, model, frontend framework, etc.) already tokenizes to a distinctive single word that could plausibly serve as a category alias without ambiguity. For the remaining 3 (`decision_basic_002` "system", `supersession_basic_007` "component", `supersession_basic_024` "deployment approach"), the terminology gap isn't wording — it's that the natural category word is too generic to safely alias (see Task 4). |

**One more fact worth stating precisely** because it changes the
recommendation in Task 5: `ActivationSpreader.spread`'s propagation is
undirected (its own docstring, confirmed at
`activation_spreader.py:88` and the direction-agnostic neighbor lookup at
`:236-238`), so once a category `Concept` and an `IS_A` edge exist, a
query resolving to the *category* node already reaches the *instance*
node with zero traversal changes — this is a wiring/data gap, not a
missing capability in `ActivationSpreader`.

---

# 4. Ceiling estimate (Task 4) — conservative, per approach

Baseline: 14 genuine ENTITY_CAT failures (already net of the 6 reclassified
as residual lexical, which are excluded here since they need no ontology
work at all).

**A. Deterministic `IS_A` relationships only** (a curated
instance-label -> category-label table, `OntologyManager` emitting
`CREATE_CONCEPT` for the category + `CREATE_RELATIONSHIP(IS_A)`, and the
category's one distinctive token registered as an alias):

Recovers the 11 cases with a specific, safely-aliasable category noun.
**Does not** recover the 3 generic-noun cases (`system`, `component`,
`deployment approach`) — aliasing "system" or "component" to one category
concept would make that concept over-activate for every future query
containing those extremely common architecture words, the same
over-broad-category risk the prior `CANDIDATE_GENERATION_DECISION.md`
already flagged for identical words.

**Ceiling: 11 / 14 (79%) of genuine ENTITY_CAT**, i.e. **11 / 47 (23%)**
of all candidate-generation failures, **11 / 250 (4.4%)** absolute
gradeable-case pass-rate gain — before ranking-stage effects are
considered (candidate generation is necessary but not sufficient for a
final PASS; some of these may still need ranking/acceptance to cooperate,
same caveat the prior audit noted).

**B. Improving `ConceptDetector` only** (loosening the capitalized-span
heuristic to catch lowercase category-shaped prose):

**Ceiling: ~0 of the 14**, and this is the most important negative
result in this document. `ConceptDetector` improvements target
*detection* (creating a concept node), not *resolution* (a query knowing
which node to seed from). All 14 genuine ENTITY_CAT cases already have
their instance concept detected (Task 3) — the failure is entirely on the
category side, which is a lowercase, generic English word by
construction ("database", "model") and was never going to be caught by a
capitalized-span heuristic regardless of how it's tuned. `ConceptDetector`
changes are the right lever for the separate `ONTOLOGY_COV` bucket (10
cases, out of this investigation's scope), not for `ENTITY_CAT`.

**C. Adding deterministic aliases only** (skip category concepts/`IS_A`
edges entirely; alias the category word directly onto the current
instance concept, e.g. `"database"` -> the `PostgreSQL` concept):

Numerically similar-looking ceiling (~11) but **architecturally unsound**
for this exact benchmark corpus, demonstrated with the corpus's own data:
- `AliasIndex` is a static one-to-one label -> concept-UUID map with a
  "smallest UUID wins" conflict policy (`alias_index.py:8-15`). Pointing
  `"database"` at `PostgreSQL` directly breaks the instant a second
  database is ever mentioned — which is exactly what
  `supersession_basic_033` is about (`MongoDB` -> `PostgreSQL`). A plain
  alias has no notion of "current instance"; an `IS_A` edge does, for
  free, via the validity gate that already exists
  (`engine.py:_active_candidates`, filtering on `valid_until` once
  `KnowledgeUpdater.supersede_decision` marks the old fact archived).
- Worse, `supersession_basic_003`'s expected answer requires **both**
  `GPT` and `Qwen` simultaneously (`answer_contains: ["GPT", "Qwen"]`) —
  two concepts under one category, at the same time, neither superseding
  the other. A single alias can only ever point at one concept, so C
  cannot represent this case at all; A's `IS_A` fan-out (one category,
  many instance edges) can.

**Not recommended**, independent of its raw count.

**D. A + B + C combined**: ceiling ≈ same as A alone, **11 / 14 (79%)**.
B contributes nothing without A's category-node-plus-edge machinery to
resolve into, and C is a strictly worse-engineered subset of what A
already covers (same reachable cases, minus supersession/multi-instance
correctness).

---

# 5. Design recommendation (Task 5)

**Immediate, zero-architecture-cost step (do this regardless of anything
else): add the 3 variant pairs identified in §0** (`name/named`,
`decide.../decided`, `prioritiz.../priority`) to `_VARIANT_GROUPS` in
`obsidian/memory_engine/keyword_candidate_retriever.py`. Confirmed by
direct pipeline run to flip 5 of the 6 residual-lexical cases straight to
PASS and the 6th into a (separately scoped) ranking problem, using the
exact same mechanism, review discipline, and file the shipped Phase 1
work already established. This is strictly cheaper than anything below
and should land first.

**Smallest deterministic fix for the remaining, genuine `ENTITY_CAT`
gap: a narrow, hand-curated instance -> category `IS_A` table, scoped to
exactly the 11 cases with a safely-specific category noun (Task 4's
approach A).**

Why this over the alternatives:
- **Preserves explainability.** One new, already-defined relationship
  type (`IS_A` — not a new enum value, just a first real use of an
  existing one), a closed, hand-reviewed table (same review discipline
  `_VARIANT_GROUPS` already established and that this repo's engineering
  culture already trusts), and a real `Concept`/`Relationship` graph edge
  an inspector can read directly off the Retrieval Trace, same as any
  other evidence path.
- **Avoids benchmark-specific hacks.** The taxonomy is keyed on
  well-known instance labels (PostgreSQL, Svelte, GitHub Actions, GPT,
  Qwen, Celery, Ollama, Linear, Jira) that are real products/services, not
  reverse-engineered from benchmark text. `Acme`, `Northbeam`, and
  `Mediterranean` are idiosyncratic-enough labels that they should **not**
  be hardcoded into a "well-known technology" taxonomy — see the excluded
  cases below.
- **Avoids LLM retrieval and embeddings.** Pure data-table + existing
  deterministic graph mutation and traversal (`OntologyManager`,
  `ActivationSpreader` — confirmed direction-agnostic, no change needed
  there).
- **Minimizes maintenance cost** by staying narrow: 11 curated pairs, not
  an open-ended "any tech entity" taxonomy. Explicitly excludes the 3
  generic-noun cases (`system`, `component`, `deployment approach`) —
  extending the alias table to cover those would be the maintenance trap
  the prior audit already warned about (over-broad category words
  causing unrelated queries to over-activate), so this recommendation
  deliberately leaves them unfixed rather than papering over the risk.
- **Integrates naturally.** `OntologyManager.propose()` already has the
  exact `CREATE_CONCEPT` + `CREATE_RELATIONSHIP` proposal machinery this
  needs (`ontology_manager.py:107-159`) — adding an `IS_A` branch is
  additive to a phase that already exists for structurally the same
  purpose (`RELATED_TO` creation), not a new subsystem.

### Scope: which of the 14 this taxonomy should and should not cover

| Include (11) | Category label | Exclude (3) | Why excluded |
|---|---|---|---|
| ThinkPad | Laptop | GPT (`decision_basic_002`, "system") | query's only category word is "system" — too generic |
| Northbeam | *excluded — see below* | Manager AI (`supersession_basic_007`, "component") | query's only category word is "component" — too generic |
| Mediterranean | Eating Plan | Ollama (`supersession_basic_024`, "deployment approach") | "approach"/"deployment" both too generic on their own |
| GitHub Actions | CI/CD Provider | | |
| Obsidian | Note-Taking App | | |
| Celery | Background Job System | | |
| GPT, Qwen | Model | | |
| Acme | *excluded — see below* | | |
| PostgreSQL | Database | | |
| Svelte | Frontend Framework | | |
| Linear, Jira | Tracking Tool | | |

`Northbeam` (a fictional startup name) and `Acme` (a placeholder company
name) are structurally ENTITY_CAT-shaped and have a safe category noun
("startup"/"company") in their queries, but are personal/idiosyncratic or
placeholder labels that a global taxonomy has no principled way to
anticipate — the same limitation the prior decision document already
identified for this class of entity. Recommend covering `Category:
Company` and `Category: Startup` as **general category concepts** (the
alias, e.g. "company", is what's curated — not a closed list of every
possible company name), so `Acme` and `Northbeam` get attached to their
category at write time via the *existing* `CREATE_CONCEPT`-for-any-new-
label mechanism rather than needing to be pre-listed. This differs from
the tech-product entries, where the category (e.g. "Database") is
curated **and** matching it against new instance labels needs the
curated table (a personal vault will keep mentioning new databases the
taxonomy can't have anticipated) — those genuinely need per-label mapping
entries, revisited as new instances appear, exactly as
`CANDIDATE_GENERATION_DECISION.md`'s original Phase 2 roadmap scoped it.

### Explicit non-goals

- **Not fixing `ONTOLOGY_COV`** (10 cases: lowercase prose like "modular
  monolith," "mechanical engineering," "boutique studio" that
  `ConceptDetector` never promotes at all). Out of this investigation's
  scope by the task brief, and — per Task 4's finding B — not fixable by
  the same mechanism anyway; it needs a separate, riskier change to
  concept detection this document does not recommend making yet.
- **Not fixing `PARAPHRASE`** (14 cases — abstract beliefs, date/time
  answers, opinion questions, enumeration queries). No category
  relationship exists to bridge by construction.
- **Not fixing `ROLE_PROPERTY`** (3 cases — "where does the user live").
  Needs a verb -> relationship-type mechanism, a genuinely different and
  larger change (a new traversal mode seeded on relationship types, not
  concepts) that this document does not scope.
- **Not touching `system`/`component`/`deployment approach`.** Documented
  as a deliberate exclusion, not an oversight — extending the alias table
  to cover them is exactly the over-broad-category risk this design is
  built to avoid.

---

# 6. Verification addendum (later session, code unchanged since)

This document and its two siblings
(`LEXICAL_NORMALIZATION_COMPLETE.md`, `CANDIDATE_GENERATION_DECISION.md`)
were found sitting uncommitted in the working tree at the start of a new
session (`git status` — untracked, zero commits touch them; the throwaway
audit scripts they describe were already deleted, as claimed).
`git status` also confirms `obsidian/ontology/` and
`obsidian/memory_engine/` have **no** uncommitted changes since these
documents were written, so the claims below were re-checked against the
identical code, not a moved target.

Re-verified independently, not re-read on faith:

- **Source code claims** — read `concept_detector.py`, `query_resolver.py`,
  `activation_spreader.py`, `candidate_assembler.py`, `alias_index.py`,
  and `ontology_manager.py` directly. Confirmed: `ConceptDetector` is
  exactly the capitalized-span heuristic described (§3); `QueryResolver`
  does only whole-phrase and single-token exact `AliasIndex` lookups, no
  fuzzy/partial matching; `ActivationSpreader.spread`'s neighbor lookup
  (`activation_spreader.py:235-239`) is direction-agnostic, confirming the
  "wiring/data gap, not a traversal gap" claim; `OntologyManager.propose()`
  (`ontology_manager.py:141`) only ever constructs
  `OntologyRelationshipType.RELATED_TO` proposals — `IS_A` (`enums.py:46`)
  is defined and consumed by `ActivationSpreader`'s weight table but has
  zero production call sites that create one; `AliasIndex`'s
  smallest-UUID-wins conflict policy is exactly as described
  (`alias_index.py:71-97`).
- **Benchmark case text** — spot-read the raw dataset files for
  `decision_reconstruction_basic_009`, `supersession_basic_033`,
  `supersession_basic_003`, and `decision_basic_002` directly; query text,
  conversation turns, and `expected.answer_contains` all match this
  document's table exactly.
- **Live pipeline trace** — wrote a throwaway harness (deleted after use,
  same convention as the original audit) that runs the real
  `OntologyPipeline.process` + `MemoryEngine.query_with_trace` for those
  same 4 cases (2 "specific noun" cases, 2 "generic noun" cases, covering
  both sub-patterns from §2). Confirmed directly from the returned
  `RetrievalTrace`, not inferred:
  - All 4 targets never appear in `trace.candidates` at all (not merely
    "found but rejected") — consistent with "candidate-generation
    failure," not a ranking failure.
  - `supersession_basic_033` and `decision_reconstruction_basic_009`
    reproduce the documented stale/near-miss hit exactly (`"I use MongoDB
    for my database."` at `accepted=True, matched_by_keyword=True,
    matched_by_ontology=False`; the laptop-options-listing turn,
    likewise).
  - `supersession_basic_003` and `decision_basic_002` reproduce the
    documented "0 candidates, empty context" result exactly.
  - Concept-graph state after ingesting each case: the instance concepts
    (`PostgreSQL`, `MongoDB`, `GPT`, `Qwen`, `Aider`, `ThinkPad`, `Dell
    XPS`, `MacBook`) are present; no category concept (`Database`,
    `Model`, `System`, `Laptop`) exists in any of the 4 graphs; the only
    relationship type present across all 4 is `related_to` — zero `is_a`
    edges. This directly confirms Task 3's "instance exists / category
    doesn't / `IS_A` never emitted" conclusion for a sampled 4/14 rather
    than assuming it holds for all 14 by extrapolation from 2.

**One new observation, not previously recorded, that reinforces rather
than changes the existing recommendation**: `decision_reconstruction_
basic_009`'s concept graph also contains a nontrivial number of noise
concepts from ordinary sentence-initial capitalization — `Confirmed`,
`Need`, `Tried`, `Still`, `There's`, `Went`, `Watered`, `Ordering`,
`Monday`, `I've` — none of which are real entities. These don't interfere
with this bucket's 14 failures (they share no tokens with any of the
14 queries), so they don't change the ceiling estimate. But they are a
relevant data point against Task 4's rejected "approach B" (loosening
`ConceptDetector` to also catch lowercase category prose): the detector
already over-generates in the *opposite* direction (capitalization
false-positives), with no compensating precision mechanism beyond
`STOP_WORDS` trimming. Loosening it further without first addressing this
existing noise source would compound a precision problem the detector
already has, independently of whether it also gains ONTOLOGY_COV recall —
a point worth carrying into that separately-scoped future investigation,
not a reason to revisit this document's ENTITY_CAT-scoped conclusion.

**Conclusion of this addendum**: no correction was needed. Every
re-checked claim reproduced exactly. This document's Task 1-5 analysis
and recommendation stand as written and are ready to hand off as the
basis for a later implementation session.

### What this buys, honestly stated

Applying Recommendation 0 (3 lexical pairs) + this `IS_A` taxonomy (11
pairs) together resolves at most **17 of the 47 candidate-generation
failures (36%)**, or **17 of 250 gradeable cases (6.8%)** absolute
pass-rate gain — before accounting for whatever fraction the ranking
stage then accepts. The remaining 30 failures (10 `ONTOLOGY_COV`, 14
`PARAPHRASE`, 3 `ROLE_PROPERTY`, 3 generic-noun `ENTITY_CAT`) need
different, larger mechanisms this document deliberately does not propose
building yet.
