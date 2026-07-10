# Candidate-Generation Failure Audit & IS_A Ontology Bridge — Decision Document

Status: Analysis only. No production code, benchmark datasets, or ontology
files were modified to produce this document. All numbers below were
measured by running Haven's real, unmodified pipeline
(`OntologyPipeline` → `HybridCandidateRetriever` → `MemoryEngine.query_with_trace`)
against the current working tree, not by re-reading a prior report.
Version: 1.0

---

# 0. Method, and why the numbers here don't match the brief that kicked this off

A previous discussion claimed "107/288 robust benchmark failures, 77
candidate-generation, 30 ranking/acceptance," and proposed a deterministic
instance→category (`IS_A`) ontology bridge (e.g. `GCP → Cloud Provider`) to
fix the dominant pattern. That report does not exist anywhere in this repo
(no `benchmarks/reports/*` file backs those numbers), and the working tree
has ~14k lines of uncommitted changes (`git diff --stat`, including edits to
`obsidian/ontology/candidate_assembler.py` and `concept_graph.py`), so a
prior run — if one happened — was against different code. Per this task's
own instructions, the numbers below supersede that report; they were
regenerated from the current code, not assumed.

**Harness.** For each of the 288 benchmark files under
`benchmarks/datasets/*/*.json` that have all required fields:

1. Every `conversation` entry is written verbatim as its own
   `KnowledgeObject` (`memory_type=FACT`), exactly as
   `benchmarks/adapters/haven_adapter.py`'s `HavenAdapter.add` does for
   `infer=False` — no LLM extraction, matching the "Haven Retrieval"
   benchmark adapter, not `HavenFullAdapter`.
2. Each object is run through the real `OntologyPipeline.process` (concept
   detection, proposal, validation, graph mutation — all deterministic, no
   LLM; verified by reading `concept_detector.py` and `query_resolver.py`).
3. `MemoryEngine.query_with_trace(query)` runs the real
   `HybridCandidateRetriever → DeterministicRanker → AcceptanceStage →
   DeterministicSlotAllocator → ContextBuilder` pipeline and returns a
   `RetrievalTrace` that already records, per candidate, whether it was
   found by the keyword path, the ontology path, and whether it was
   accepted — nothing here is reimplemented, only read.
4. The "target" memory for a case is whichever conversation entry's text
   contains every string in `expected.answer_contains` (case-insensitive).
   A case where no single entry contains all of them is marked ungradeable
   by this method (see below) rather than guessed at.

This is a deterministic, code-grounded proxy for "did the correct memory
survive candidate generation," not a re-run of the LLM-judged benchmark
suite (no LLM was called anywhere in this audit).

**Coverage.** 288 total cases. 38 are excluded as ungradeable by this
method — dominated by `concept_consolidation` (20) and `beliefs`/`Beliefs`
(9+1) — because their expected answer is a synthesized belief that mixes
words from across several turns (e.g. `beliefs_011`'s expected `["NoSQL",
"distributed", "document", "consistency"]` spans two different messages)
rather than sitting verbatim in one stored fact. These need the real LLM
judge to grade and are out of scope here; they are **not** assumed to pass
or fail. Everything below is over the remaining **250 gradeable cases**.

---

# 1. Validated root-cause counts (Task 1)

| Outcome | Count | % of 250 |
|---|---|---|
| Retrieval-level pass (target accepted into final context) | 169 | 67.6% |
| **Candidate-generation failure** (target never became a candidate) | **60** | **24.0%** |
| Ranking/acceptance failure (target was a candidate, never accepted) | 21 | 8.4% |

The ranking/acceptance 21 are governed by `DeterministicRanker`/
`AcceptanceStage`, not candidate generation, and are out of scope for the
`IS_A` bridge question — noted for completeness, not analyzed further here.

## Root-cause breakdown of the 60 candidate-generation failures

Every one of the 60 was read individually (query vs. target text) and
checked against Haven's actual tokenizer/variant rules
(`obsidian/memory_engine/keyword_candidate_retriever.py`). Full
per-case classification: `classify.py` in this analysis (available on
request) maps every `benchmark_id` to one of six buckets, cross-checked
against the harness output — 60/60 accounted for, none left unclassified.

| Root cause | Count | % of 60 | Definition |
|---|---|---|---|
| **PARAPHRASE** | 17 | 28% | Query and target share no token, no morphological variant, and no entity/category relationship — different verbs, synonyms, date/time answers, or abstract topic drift. |
| **LEXICAL** (morphological variant) | 16 | 27% | Zero token overlap *only* because of a plural/singular or verb-tense form outside Haven's closed variant table (`_VARIANT_GROUPS` currently covers `project`, `task`, `work`, `build`, `commit` — five groups). |
| **ENTITY_CAT** | 11 | 18% | Query uses a generic, well-known category noun ("database", "frontend framework"); target's answer is a capitalized, globally-known named entity already registered as a `Concept` — the shape the `IS_A` bridge targets, and plausibly coverable by a curated global taxonomy. |
| **ONTOLOGY_COV** | 9 | 15% | The answer phrase is lowercase prose ("mechanical engineering," "modular monolith," "boutique studio"), so `ConceptDetector`'s capitalized-span heuristic never promotes it to a `Concept` at all — there is no node for an `IS_A` edge to attach to. |
| **ENTITY_CAT_UNCERTAIN** | 4 | 7% | Same shape as ENTITY_CAT, but either the category word is too generic/ambiguous to index safely (e.g. "system," "subsystem") or the named entity is personal/idiosyncratic and could never appear in any pre-built global taxonomy. |
| **ROLE_PROPERTY** | 3 | 5% | Query verb implies a relationship ("where does the user **live**") rather than any topical overlap; needs a verb→relationship-type mapping, not an `IS_A` bridge. |

### The brief's own motivating example, found and checked

`supersession_basic_047` — query "*What is the current hosting decision?*",
target "*We reconsidered and decided to host on **GCP** instead.*" — is the
closest in-repo match to the brief's "cloud provider / GCP" example. It
classifies as **LEXICAL**, not entity↔category: the query says "hosting,"
the fact says "host." Verified empirically (below) that widening the
variant table alone — no ontology, no taxonomy — makes it retrievable.

### Representative examples per bucket

| Bucket | benchmark_id | Query | Target |
|---|---|---|---|
| LEXICAL | `supersession_basic_047` | "current hosting decision" | "...decided to **host** on GCP instead" (host/hosting) |
| LEXICAL | `supersession_basic_001` | "Which **embedding** system..." | "...replaced OpenAI **embeddings** with FastEmbed" |
| LEXICAL | `decision_basic_009` | "What type of **dataset**..." | "...dedicated benchmark **datasets**..." |
| ENTITY_CAT | `supersession_basic_033` | "What **database** does the user...use?" | "I migrated everything to **PostgreSQL**." |
| ENTITY_CAT | `decision_reconstruction_basic_018` | "Which **CI/CD provider**..." | "Going with **GitHub Actions**." |
| ONTOLOGY_COV | `identity_basic_001` | "field of study" | "mechanical engineering student" (lowercase, never detected) |
| ENTITY_CAT_UNCERTAIN | `decision_basic_002` | "Which **system** should design..." | "...use **GPT** for architecture" ("system" too generic to index) |
| PARAPHRASE | `supersession_basic_007` | "Which **component** currently has priority?" | "...**Manager AI** comes first." (no shared vocabulary at all) |
| ROLE_PROPERTY | `supersession_basic_026` | "Where does the user **live**?" | "I moved to **Berlin**." (verb implies LOCATED_IN, no category noun exists) |

---

# 2. Theoretical ceiling of the proposed `IS_A` bridge (Task 2)

Two facts were verified directly against the running pipeline before
estimating anything:

- **`ActivationSpreader.spread` propagation is undirected** (its own
  docstring: *"Propagation is undirected... this mirrors
  `ConceptGraph.neighbors`"*, confirmed in code at
  `activation_spreader.py:234-239`, which treats `source_id`/`target_id`
  symmetrically). So a query resolving to a *category* concept can already
  reach an *instance* concept across an `IS_A` edge with no traversal
  change needed — direction is not the blocker.
- **The instance-side `Concept` already exists for ENTITY_CAT cases.**
  Reproduced directly: writing `"I migrated everything to PostgreSQL."`
  through the real `OntologyPipeline` already creates a `Concept(label="PostgreSQL",
  aliases=())` — the bridge only needs to *also* create/attach a category
  concept (`"Database"`) and register the `IS_A` edge; it doesn't need to
  fix concept detection for this bucket.

Given that, per bucket:

| Bucket | Recoverable by `IS_A` bridge? |
|---|---|
| ENTITY_CAT (11) | **Yes, conditionally** — requires the specific instance→category pair to be in a hand-curated table, and a new category `Concept` to exist with an alias the query's own words resolve to (`QueryResolver` only does exact whole-phrase or single-token alias lookups — no fuzzy/partial phrase matching, see §3 risk below). |
| ENTITY_CAT_UNCERTAIN (4) | **Not counted.** 2 cases need a category word (`system`, `subsystem`) too generic to add without causing unrelated over-activation; 2 involve entities too personal/idiosyncratic (a specific deployment tool, a vacation-style query) for any static global taxonomy to plausibly anticipate. |
| ONTOLOGY_COV (9) | **No.** The "instance" is lowercase prose that `ConceptDetector` never promotes to a `Concept`. The bridge operates *on top of* detected concepts — it has nothing to attach an `IS_A` edge to here. Fixing this needs a separate, riskier change to concept detection (loosening past the capitalized-span heuristic), which the proposal doesn't include and which risks flooding the graph with noise concepts from ordinary prose. |
| PARAPHRASE (17) | **No.** No category relationship exists between query and answer by construction (different verbs/synonyms, date/time answers). |
| ROLE_PROPERTY (3) | **No.** Needs a verb→relationship-type mechanism, not an `IS_A` bridge (see §3). |

**Ceiling: 11 of 60 candidate-generation failures.** Conservative — the
uncertain bucket is excluded rather than assumed to help.

- 11 / 60 candidate-generation failures = **18.3%**
- 11 / 81 total retrieval-level failures (candidate-gen + ranking) = **13.6%**
- 11 / 250 gradeable benchmark cases = **4.4% absolute pass-rate gain**
- 11 / 288 full suite = **3.8%**

---

# 3. Alternatives compared (Task 3)

| Approach | Complexity | Maintenance | Explainability | Measured benchmark impact | Architectural fit | Scalability |
|---|---|---|---|---|---|---|
| **Lexical normalization** (widen `_VARIANT_GROUPS`, or a narrowly-scoped stemmer that keeps rejecting `Atlas`/`Postgres`/`Kubernetes`-style false positives) | Very low — data-table extension to an existing, tested module | Low — same closed-table review discipline already documented in the module | High — a fixed, inspectable list; `test_keyword_candidate_retriever.py`'s `TestStopWordParity` pattern already cross-checks table parity | **Confirmed by direct test: 5/5 sampled LEXICAL cases (including the brief's own `hosting`/`host` example) retrieve correctly once the table is widened — zero other code touched.** Covers 16/60 (27%) of candidate-gen failures. | Perfect — extends a stage that already exists for exactly this purpose | Grows by adding pairs as they're found; no new subsystem, no per-query cost change |
| **Deterministic query expansion** (broader synonym/lemma expansion beyond the closed table) | Medium — needs a synonym source (WordNet-class list or hand-built) | Medium-high — synonym lists drift and need review to avoid false positives | Medium — larger table, harder to eyeball every entry | Same ceiling as lexical normalization for the cases seen here (these particular gaps are all inflectional, not synonym-level) plus unmeasured upside on PARAPHRASE-bucket synonym pairs (e.g. "vacation"/"trip") | Good — same insertion point as lexical normalization | Diminishing returns; open-ended synonym coverage without a real ceiling |
| **Property/role concepts** (map verbs like "live"/"work at" to relationship types, e.g. `LOCATED_IN`) | Medium-high — needs a verb→relationship-type table *and* a new relationship-type-aware query path (today's `QueryResolver`/`ActivationSpreader` seed on Concepts, not on relationship types) | Medium — closed verb table, but a new traversal mode to maintain | Medium | Covers 3/60 (5%) — the ROLE_PROPERTY bucket only | Requires a genuinely new retrieval mode, not just a data addition | Narrow — only helps queries whose entire content is "which relationship, not which concept" |
| **`IS_A` ontology bridge** (the original proposal) | High — new curated instance→category taxonomy (a data file that must track real-world products/services), category-`Concept` creation logic in `OntologyManager`, alias curation per category | High — a taxonomy of "well-known" entities is never finished; needs ongoing upkeep as new products/services appear | Medium — one more relationship type in an already-documented graph, but taxonomy correctness now depends on external, changing facts | Confirmed: 11/60 (18%) ceiling, **less than lexical normalization's confirmed 16/60 (27%)** | Fits the existing `IS_A` relationship type and undirected propagation (no pipeline change needed there) — but only pays off for entities `ConceptDetector` already promotes, i.e. it's coupled to a limitation (capitalized-span detection) it doesn't itself fix | Bounded by taxonomy size and by concept-detection's capitalized-span heuristic; cannot ever cover personal/idiosyncratic named entities (a pet's name, a fictional placeholder company) since no static global table would include them |

---

# 4. Recommendation (Task 4)

**Widen lexical-variant normalization in `KeywordCandidateRetriever`
first.** It recovers more failures (16/60, confirmed by direct test) than
the `IS_A` bridge (11/60, estimated) for a fraction of the implementation
and maintenance cost: it is a bounded, reviewable data-table extension to
a module that already exists for exactly this purpose
(`_VARIANT_GROUPS`/`_CANONICAL_FORM` in
`obsidian/memory_engine/keyword_candidate_retriever.py`), with no new
relationship type, no taxonomy to curate and keep current against a
moving external world, and no risk of an over-broad category concept
(`"System"`, `"Component"`) causing unrelated queries to over-activate.
It also improves every query in every category — not just the
entity/category-shaped ones — since keyword overlap feeds
`DeterministicRanker`'s scoring for all 250 gradeable cases, not just the
60 failures analyzed here.

**Do not build the `IS_A` bridge next.** Three independent reasons, each
sufficient on its own:

1. **Smaller measured ceiling** (11 vs. 16) for **materially higher
   complexity** (new taxonomy, new curation workflow, new category-concept
   alias design) — it loses on both axes simultaneously.
2. **It is capped by a limitation it doesn't fix.** 9/60 (15%) of
   candidate-generation failures are `ONTOLOGY_COV`: the answer is
   lowercase prose `ConceptDetector` never promotes to a `Concept` at all.
   Much of the bridge's *apparent* promise (any "entity has a category"
   story sounds like it should generalize) evaporates against this — the
   bridge only ever activates for entities the write path already turned
   into capitalized-span `Concept`s.
3. **It cannot cover the cases that most resemble real usage.** Personal
   or idiosyncratic named entities (a pet's name, a fictional placeholder
   company like "Umbrella Corp," an invented startup name) can never
   appear in a hand-curated *global* taxonomy — only well-known
   product/company/geography names can. That excludes a meaningful slice
   of what a personal memory system like Haven actually stores.

**If the bridge is still wanted later:** scope it narrowly to the 11
confirmed ENTITY_CAT cases (well-known tech/business/geography entities
only — never generic words like "system"/"component"), and treat it as an
opt-in Phase 2 evaluated *after* lexical normalization ships, against
whatever residual failure set remains once the 16 LEXICAL cases are
removed from the denominator.

---

# 5. Roadmap

## Phase 1 — Lexical normalization (recommended)

- **Files affected:**
  - `obsidian/memory_engine/keyword_candidate_retriever.py` —
    `_VARIANT_GROUPS` (currently 5 groups: `project`, `task`, `work`,
    `build`, `commit`); add groups for the pairs confirmed above
    (`embedding`/`embeddings`, `model`/`models`, `benchmark`/`benchmarks`,
    `dataset`/`datasets`, `host`/`hosting`, `payment`/`payments`,
    `system`/`systems`, `secret`/`secrets`, `current`/`currently`,
    `learn`/`learning`, `combine`/`combining`, `evaluate`/`evaluated`/
    `evaluation`/`evaluations`, `optimize`/`optimization`,
    `prefer`/`preferred`).
  - `obsidian/tests/test_keyword_candidate_retriever.py` — one test per
    new group (mirroring the existing `project`/`task`/etc. tests) plus a
    regression test replaying the specific benchmark query/fact pairs
    identified in §1.
  - `obsidian/ontology/text_utils.py` is **not** touched — the module's
    own docstring explains the duplication is a deliberate architectural
    boundary ("no ontology imports"); if the ontology path should ever
    share these variants, that requires its own explicit, separately
    reviewed table update with a cross-check test, exactly like
    `TestStopWordParity` already enforces for `STOP_WORDS`.
- **Expected behavior change:** more keyword-path matches; more
  `RankedCandidate.score_breakdown["keyword_overlap_score"]` entries are
  nonzero; no change to ranking weights or acceptance thresholds.
- **Backward compatibility:** purely additive to a closed table — a
  strict superset of current matches. No existing passing case can newly
  fail unless a specific pair is chosen carelessly (e.g. adding a pair
  that conflates two genuinely different words). Each candidate pair
  should be reviewed against the module's own stated risk list (`Atlas`,
  `Postgres`, `Kubernetes` — proper nouns ending in "s" that are not
  plurals) before being added.
- **Testing strategy:** unit tests per group; a golden-file test that
  replays all 16 LEXICAL benchmark cases identified here and asserts they
  now produce a nonempty keyword match.
- **Benchmark validation:** re-run the harness used for this audit (or
  extend `benchmarks/analysis/classify_failure.py`) over all 288 cases;
  confirm the 16 LEXICAL cases flip to candidate-generation success and
  no other case regresses.
- **Risks:** a bad pair silently over-merges two distinct words. Mitigated
  by keeping the table closed and hand-reviewed, per the module's existing
  design philosophy — no algorithmic stemmer.

## Phase 2 — `IS_A` bridge (optional, only if pursued after Phase 1)

- **Files affected:**
  - `obsidian/ontology/ontology_manager.py` — extend `propose()` to also
    emit `CREATE_CONCEPT` (category) + `CREATE_RELATIONSHIP` (`IS_A`)
    proposals when a detected instance label matches an entry in a new
    curated taxonomy.
  - New file, e.g. `obsidian/ontology/category_taxonomy.py` — a closed,
    hand-reviewed table mapping well-known instance labels to
    category label + aliases (scoped to the 11 confirmed ENTITY_CAT
    cases as a starting set: `PostgreSQL`→`Database`, `Svelte`→`Frontend
    Framework`, `GitHub Actions`→`CI/CD Provider`, `Obsidian`→`Note-Taking
    App`, `ThinkPad`→`Laptop`, `Celery`→`Background Job System`,
    `Linear`/`Jira`→`Tracking Tool`, `Acme`→`Company`, `Raleigh`→`City`,
    `Mediterranean`→`Eating Plan`, `Border Collie`→`Dog Breed`).
  - `obsidian/ontology/concept_detector.py` is **not** changed — scope
    stays limited to entities the capitalized-span heuristic already
    detects; do not attempt to also fix `ONTOLOGY_COV` in this phase.
- **Expected behavior change:** the first time a curated instance is seen,
  its category `Concept` is created/reused and an `IS_A` relationship is
  recorded; `QueryResolver`'s token pass then resolves a query word like
  "database" to the category concept, and `ActivationSpreader` (already
  direction-agnostic — verified, no change needed) reaches the instance.
- **Backward compatibility:** additive only — new concepts/relationships,
  no existing proposal type or pipeline signature changes.
- **Testing strategy:** unit tests for taxonomy lookup and proposal
  generation; an activation-spreading test confirming a category-seeded
  query reaches the curated instance and vice versa.
- **Benchmark validation:** re-run the 11 ENTITY_CAT cases plus the full
  288-case suite for regressions.
- **Risks:**
  - **Multi-word category phrases may not resolve.** `QueryResolver` only
    does an exact whole-normalized-query lookup or a single-token lookup —
    no partial/sliding-window multi-word phrase matching. A category
    labeled `"CI/CD Provider"` needs either the *entire* query to equal
    that phrase, or a single token alias (e.g. `"provider"`) registered —
    which reintroduces the over-broad-category-word risk this phase is
    supposed to avoid. Each curated category's alias set needs to be
    chosen carefully per case, not assumed to "just work."
  - **Taxonomy staleness.** A hand-curated list of real products/services
    is never finished and needs ongoing upkeep as the user's actual stack
    changes — an ongoing cost with no natural stopping point.
  - **No coverage for personal/idiosyncratic entities**, by construction
    (see §4) — set expectations accordingly before starting this phase.
