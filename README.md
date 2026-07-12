<div align="center">

<!-- VISUAL — wordmark.svg · obsidian/docs/media/wordmark.svg
     A quiet, text-first wordmark: "Haven" in a humanist serif or geometric sans,
     with the "a" subtly enclosed in a rounded square (a vault door). Monochrome,
     works on dark and light GitHub themes. No mascot, no gradient, no glow.
     First thing the viewer notices: restraint. It should look like infrastructure.
     TODO: uncomment once obsidian/docs/media/wordmark.svg exists —
     <img src="obsidian/docs/media/wordmark.svg" alt="Haven" width="360" /> -->

# Haven

### Memory for AI that can show its work.

Haven is a local-first memory system for LLMs that can explain — for every single
memory it retrieves — **why it matched, why it ranked where it did, and why it was
accepted or rejected.** No hidden scoring. No embedding roulette. Every answer comes
with a receipt.

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/server-FastAPI-009688?logo=fastapi&logoColor=white)
![Local-first](https://img.shields.io/badge/storage-plain%20Markdown-000000?logo=obsidian&logoColor=white)
![Retrieval: deterministic](https://img.shields.io/badge/retrieval-deterministic-8A2BE2)
![No API key needed for demo](https://img.shields.io/badge/demo-no%20API%20key-success)

**[Repository layout](#-repository-layout)** ·
**[Quick start](#-quick-start-5-minutes-no-api-key)** ·
**[How it works](#-how-it-works)** ·
**[Benchmarks](#-benchmarks)** ·
**[Inspection suite](#-the-inspection-suite)** ·
**[Roadmap](#-roadmap)**

</div>

## 🧭 Repository layout

Haven began as a fork of [mem0](https://github.com/mem0ai/mem0) and grew into its
own project. The upstream mem0 code (SDKs, integrations, the mem0 docs site) is
still in this repository for reference and as the benchmark baseline — but if
you're evaluating **Haven specifically**, you only need these directories:

| Path | What you'll find there |
|---|---|
| `obsidian/` | Haven's backend: write/read pipelines, ontology, Manager AI, the FastAPI server, and the dashboard. (Why it's called `obsidian/` — [see below](#why-the-backend-lives-in-a-folder-called-obsidian).) |
| `extension/` | The Chromium browser extension that adds Haven to ChatGPT. |
| `benchmarks/` | The mem0-vs-Haven benchmark harness, results, and the final engineering report. |
| `config/` | Environment-variable templates for the LLM-backed pieces (Manager AI, query rewriter, benchmark judge). |
| `demo/` | The deterministic demo dataset used by the no-API-key quickstart. |
| `obsidian/docs/` | Haven's own design docs, decision log, roadmap, and known issues — start here for anything beyond the README. |

Everything else at the repo root (`mem0/`, `docs/`, `tests/`, etc.) is upstream
mem0 and isn't part of Haven's evaluation surface.

### Why the backend lives in a folder called `obsidian/`

Early on, Haven used [Obsidian](https://obsidian.md) itself as its persistence
layer, so the code that talked to a vault lived under `obsidian/`. The name
stuck even as scope grew: today `obsidian/` holds Haven's actual backend — the
Memory Engine (read pipeline), Manager AI (write pipeline), the ontology /
concept graph, the FastAPI server, and the dashboard. It's an accident of
history, not a description of what's inside — think of it as Haven's `src/`.

### Documentation map

```
README.md              ← you are here: quickstart, results, how it works
   ↓
obsidian/docs/          ← start here for anything deeper: ARCHITECTURE.md,
                          ROADMAP.md, KNOWN_ISSUES.md, decision log
   ↓
docs/architecture/      ← original deep-dive design specs (ontology,
                          acceptance stage, ranking investigations) —
                          historical/detailed, referenced from obsidian/docs/
```

`obsidian/docs/README.md` indexes the middle layer and points into
`docs/architecture/` for deeper specs.

<!-- VISUAL — hero.gif · obsidian/docs/media/hero.gif · ~25s loop, 1280px wide, placed here,
     immediately after the fold. This is the single most important asset in the README.

     Three beats, ~8s each, with 1-word title cards between them:

     Beat 1 — "REMEMBER": ChatGPT open. User finishes a conversation about choosing
     Postgres for a billing service, clicks Haven's "Remember" button next to the
     reply. A toast confirms the save.

     Beat 2 — "RETRIEVE": A fresh ChatGPT conversation days later. User types
     "what did I decide for the billing service database?", clicks "Use Haven"
     near the compose box, and the retrieved memory context visibly appears in
     the prompt before sending.

     Beat 3 — "EXPLAIN": Cut to the Haven dashboard's Retrieval Inspector showing
     that same query — the ranked candidates with their score-breakdown bars
     (activation, keyword overlap, confidence, recency) and one candidate marked
     REJECTED with its reason.

     What the viewer should notice first: the score breakdown in beat 3. Every
     other memory tool can do beats 1–2. Only Haven has beat 3 — the GIF exists
     to make that contrast land without a single sentence of copy.
     TODO: uncomment once obsidian/docs/media/hero.gif exists —
     ![Haven in 25 seconds: remember from ChatGPT, retrieve into ChatGPT, inspect why](obsidian/docs/media/hero.gif) -->

---

## 💡 Why this exists

Every AI memory system makes the same promise: *your assistant will remember you.*
Then you ask it something, it injects three irrelevant memories and misses the one
that mattered, and you have **no way to find out why.** The embedding said so. The
end.

That black box is fine for a demo. It's disqualifying for a second brain — a system
you're supposed to trust with years of your decisions, preferences, and projects.
Trust requires the ability to audit.

Haven is built around one uncompromising idea:

> **A memory system you can't interrogate is a memory system you can't trust.**
> So every stage of Haven's pipeline is deterministic, traceable, and inspectable —
> and the correctness of no stage depends on an LLM behaving a particular way at
> runtime.

Two things are actually measured and reproducible from this repo, unpacked with
full methodology in [Benchmarks](#-benchmarks):

- **The write path never reprocesses what it's already seen.** Re-sending an
  unchanged conversation costs **zero LLM calls** (a full-reprocess design pays
  3 per send), and at 500 conversation turns Haven's extraction prompt holds
  **constant at ~769 est. tokens**, where full reprocessing grows linearly to
  **~6,800**.
- **The read path is benchmarked end-to-end against naive baselines** — keyword
  search, most-recent, embeddings, "return everything" — on a 288-case,
  LLM-judged suite that grades Haven's real pipeline the same way it grades
  every baseline. It beats keyword search and its own retrieval-only ablation
  outright; it currently loses to two deliberately naive baselines on raw pass
  rate, for a specific, root-caused reason we report rather than paper over
  (see [Benchmarks](#-benchmarks)).

---

## ✨ What makes Haven different

**🎯 Deterministic retrieval.** The read path is plain, testable Python: keyword
matching with IDF-weighted overlap, activation spreading across a concept graph,
and a ranker that scores every candidate on named, inspectable factors. Same vault,
same query → same answer, every time. No temperature, no vibes.

**🔍 Explainable by construction — not as a feature.** Explanation isn't a log
bolted on afterward; the pipeline's own data structures carry a score breakdown and
acceptance decision for every candidate, surfaced live in three built-in
inspectors ([see below](#-the-inspection-suite)).

**🛑 A memory system that can say "I don't know."** Haven's acceptance stage runs
five deterministic checks after ranking — minimum score, abstention check,
score-gap cut, relative threshold, hard cap — and will return *nothing* rather than
pad your prompt with weak matches, recording a reason for every rejection.

**📁 Your memories are files, not rows.** Every memory is a Markdown file with
YAML frontmatter in a folder you choose — openable as an [Obsidian](https://obsidian.md)
vault, greppable, diffable, syncable, yours. Delete Haven tomorrow and your second
brain is still sitting there in plain text.

**⚡ Never pays for the same conversation twice.** Conversation checkpoints
fingerprint what's already been ingested. Unchanged conversation → short-circuit
before the pipeline even runs. One new turn → only that turn is processed, against
a compact retrieved background instead of the full transcript.
([Details](#-never-ingest-the-same-conversation-twice).)

### How that compares

| | Typical embedding memory | **Haven** |
|---|---|---|
| Retrieval | Vector similarity, opaque | Hybrid keyword + concept-graph activation, deterministic |
| "Why did this memory surface?" | 🤷 | Per-candidate score breakdown, live in the dashboard |
| "Why did that one *not* surface?" | 🤷 | Rejection reason recorded by the acceptance stage |
| Weak matches | Injected anyway (top-k always returns k) | Abstains — returns nothing below the bar |
| Storage | Vector DB rows | Plain Markdown + YAML, Obsidian-compatible |
| Re-ingesting a long conversation | Full reprocess, every time | Checkpointed: 0 LLM calls if unchanged, 1 turn if incremental |
| Extraction prompt at turn 500 | grows linearly (~6,800 est. tokens) | constant (~769 est. tokens) |
| Reproducible offline demo | usually needs API keys | one click, no key, deterministic |

---

## 🚀 Quick start (5 minutes, no API key)

The demo is fully deterministic and needs **no LLM key** — a scripted fake LLM
replays real conversations through Haven's real, unmodified pipeline, so
everything below works on a plane.

```bash
# 1 · Install the server's dependencies (isolated from the repo's root package)
pip install -r obsidian/server/requirements.txt

# 2 · Run the server from the repo root
uvicorn obsidian.server.main:app --reload --port 8765
```

Then, in your browser:

3. **Pick a vault.** Open `http://127.0.0.1:8765/dashboard` — first run shows a
   *Select your vault* prompt. Paste any folder path (pointing at an existing
   Obsidian vault is safe; Haven only adds its own subfolders, non-destructively).
4. **Click "Import Demo Data."** Two seconds later you have 47 memories, 47
   concepts, and 57 relationships from three fictional people — every one written
   through the production write path, producing the exact same on-disk artifacts a
   real save would.
5. **Explore.** Query the Retrieval Inspector (`"billing-service"`, `"capstone"`),
   click any memory card to open the Memory Inspector, and open the Write
   Inspector's three `priya-standup` traces to watch write cost collapse as
   checkpointing kicks in.

<!-- VISUAL — quickstart-dashboard.png · obsidian/docs/media/quickstart-dashboard.png
     Screenshot of the dashboard immediately after demo import: the Overview stat
     row (47 memories / 47 concepts / 57 relationships), populated category cards,
     and the Resume Work panel. First thing the viewer should notice: everything
     is already full — this is the "it actually works out of the box" proof shot.
     Placed here so a skimming judge sees the payoff adjacent to the 5 commands.
     TODO: uncomment once obsidian/docs/media/quickstart-dashboard.png exists —
     ![Dashboard after one-click demo import](obsidian/docs/media/quickstart-dashboard.png) -->

<details>
<summary><strong>Connect it to ChatGPT (the browser extension)</strong></summary>

Chromium browsers (Chrome, Edge, Brave — not Firefox/Safari):

1. Go to `chrome://extensions`, enable **Developer mode**, click **Load
   unpacked**, and select this repo's `extension/` folder.
2. Open the popup — the status dot should read **Connected** (it talks to
   `http://127.0.0.1:8765` by default; changeable in the popup's Settings).
3. On `https://chatgpt.com`, type a message and click **Use Haven** near the
   compose box to pull matching context from your vault — or click **Remember**
   after a reply to save something new. The popup also searches your vault
   directly.

`Ctrl+C` stops the server; your Markdown vault on disk is untouched, and the
extension shows "Offline" until it's back.

</details>

<details>
<summary><strong>Use it on your own conversations (needs a Qwen API key)</strong></summary>

The quickstart above needs no key — it replays a scripted demo. Real
extraction from arbitrary conversations (Quick Capture, `/memory/preview`)
needs Manager AI bound to a live LLM. Haven is standardized on Qwen Cloud
for every AI call site:

```bash
cp config/manager_ai.env.example config/manager_ai.env
# then set MANAGER_AI_API_KEY in that file
```

See [`obsidian/server/README.md`](obsidian/server/README.md#configure-manager-ai-optional-for-the-demo--needed-for-real-extraction)
for the full picture, including the benchmark judge and optional Query
Rewriter's separate, independent configs.

</details>

<details>
<summary><strong>CLI / CI seeding and reset</strong></summary>

```bash
python scripts/seed_demo.py    # reproducible seeding of the fixed haven_data/ dirs
python scripts/reset_demo.py   # clear the active vault and re-import from scratch
```

Both share the dashboard buttons' underlying logic (`obsidian/server/demo_seed.py`).
**Import Demo Data** is additive; **Reset Demo** clears first (confirms — no undo).
Expected state after seeding is documented step-by-step in
[`obsidian/server/README.md`](obsidian/server/README.md).

</details>

---

## 🧠 How it works

Haven is three pipelines and a surface:

- a **write pipeline** (Manager AI) that turns raw conversations into canonical
  knowledge,
- an **ontology layer** that indexes that knowledge into a concept graph,
- a **read pipeline** (Memory Engine) that turns a query into an LLM-ready
  context string,
- and a **FastAPI server + dashboard + browser extension** on top.

![Haven architecture: deterministic write and read pipelines meeting at a Markdown vault and concept graph](obsidian/docs/media/architecture.svg)

### The write path: conversations → canonical knowledge

```
Conversation → Extractor → Classifier → ImportanceScorer → CanonicalMatcher → KnowledgeUpdater → VaultWriter
                                                                                      │
                                                                              OntologyPipeline
                                                                                      │
                                                                        ConceptGraph + concept files
```

The Extractor pulls atomic facts (with source event and evidence). The Classifier
assigns a `MemoryType` — 18 types across three domains (personal, work,
knowledge), from fact and preference to decision, goal, project, and task — plus
a canonicalized topic tag, each with a confidence and a stated reason. The
CanonicalMatcher compares each fact
against existing knowledge and returns a decision: `NEW`, `CONFIRM`, `UPDATE`, or
`SUPERSEDE` — so repeated confirmations *strengthen* a memory (confidence nudges
up, evidence chain grows) instead of duplicating it. Finally the VaultWriter
persists a Markdown file with YAML frontmatter, and the OntologyPipeline — the
*only* component allowed to mutate the concept graph — attaches it to concepts.

An LLM is used exactly once here, for language understanding in extraction.
Everything after that is deterministic code.

![Memory write pipeline: save_memory() through Extractor, Classifier, ImportanceScorer, CanonicalMatcher, KnowledgeUpdater, VaultWriter, and OntologyPipeline, with checkpoint dedup and best-effort tracing](obsidian/docs/media/memory-write-pipeline.svg)

### The read path: query → context, with receipts

```
query → QueryRewriter → HybridCandidateRetriever → DeterministicRanker → AcceptanceStage → ContextBuilder → LLM
                          keyword ∪ concept-activation      score everything      keep the trustworthy prefix
```

- **HybridCandidateRetriever** resolves query terms to concepts via an alias
  index, spreads activation across the concept graph, and independently matches
  IDF-weighted keyword overlap (with a phrase bonus). A memory found by both
  paths keeps evidence from both.
- **DeterministicRanker** scores *every* candidate — activation, attachment
  relevance, keyword overlap, importance, confidence, recency, confirmation
  count — with no filtering. The full breakdown is preserved on the candidate.
- **AcceptanceStage** then decides which prefix of the ranked list is
  trustworthy: minimum score → abstention check → score-gap cut → relative
  threshold → hard cap. It records a reason for every rejection, and returning
  an empty result is a legitimate, first-class outcome.

![Memory retrieval pipeline: ContextPlanner through HybridCandidateRetriever's dual ontology/keyword paths, DeterministicRanker, AcceptanceStage, and ContextBuilder, with diagnostics-only side outputs feeding the RetrievalTrace](obsidian/docs/media/memory-retrieval-pipeline.svg)

<details>
<summary><strong>Deep dive: why deterministic ranking beats "just use embeddings" here</strong></summary>

Three small, boring, high-leverage engineering decisions do most of the work —
each one found by inspecting real traces in the Retrieval Inspector:

1. **Stop-word filtering.** Queries like "what's the plan" used to match on
   "the". Removing stop-word-only matches closed off an entire class of
   false-positive matches.
2. **Controlled token normalization.** A deterministic normalization table
   (`project ↔ projects`, `build ↔ building`) instead of a stemmer — aggressive
   stemming mangles proper nouns, and a second brain is *full* of proper nouns.
3. **A tokenizer bug worth telling on ourselves about.** `What's` used to
   tokenize into `what` + `s` — and the orphaned `s` matched every possessive in
   the vault. One-character token, vault-wide false positives. Deterministic
   pipelines make this findable in a trace in minutes; in an embedding pipeline
   it would just be unexplained noise, forever.

The point isn't that any one of these is clever. It's that in a fully inspectable
pipeline, *retrieval quality becomes normal debuggable engineering* instead of
prompt-and-pray.

</details>

### Working Context: grouping accepted memories by topic

The same allocated candidates `ContextBuilder` renders as a flat string can
instead be grouped by `WorkingContextBuilder` into per-topic buckets (goals,
decisions, tasks, open questions) with a deterministic status summary — used
by the "Use Haven" injection preview, by `query_structured()`'s XML prompt for
continuation-style queries, and, read-only, as background for the write
pipeline's incremental-ingestion path.

![Working Context assembly: WorkingContextBuilder grouping ranked candidates into per-concept topic buckets with a deterministic state summary, and its three consumers](obsidian/docs/media/working-context-assembly.svg)

---

## 🔬 The inspection suite

Three built-in inspectors, one per question you'd ever ask a memory system. This
section is the product's thesis made tangible — each inspector is a live view over
data the pipeline records anyway: there is no separate "explain" algorithm, just
the same `RetrievalTrace` (or a persisted `WriteTrace`) re-exposed as-is.

![Explainability pipeline: every dashboard why-button resolves to either engine.query_with_trace() re-exposed as an InspectorResponse, or a persisted WriteTrace read back unmodified](obsidian/docs/media/explainability-pipeline.svg)

### Retrieval Inspector — *"why did I get these results?"*

Type any query, get the ranked candidates with per-factor score bars, and — the
part nothing else gives you — the candidates that were **rejected, with the
acceptance stage's stated reason**.

<!-- VISUAL — retrieval-inspector.png · obsidian/docs/media/retrieval-inspector.png
     Screenshot of the Retrieval Inspector for the query "what did Priya decide
     for the billing-service": 3–4 accepted candidates with visible score-breakdown
     bars (activation / keyword overlap / confidence / recency), and at least one
     grayed-out REJECTED candidate with its reason string visible.
     First thing the viewer should notice: the rejected row. Accepted results look
     like every search UI ever; a rejection with a reason is the novel object.
     TODO: uncomment once obsidian/docs/media/retrieval-inspector.png exists —
     ![Retrieval Inspector: ranked candidates with score breakdowns and a rejected candidate with its reason](obsidian/docs/media/retrieval-inspector.png) -->

### Memory Inspector — *"what does the system believe about this one fact?"*

Click any memory card: its ontology attachments, current confidence and evidence
chain, retrieval score breakdown, acceptance decision, and the full write-pipeline
trace that created it.

### Write Inspector — *"what did that save actually cost?"*

Every write leaves a `WriteTrace` on disk. Open the three `priya-standup` traces
in order and the incremental-ingestion story tells itself:

| Send | Checkpoint mode | Facts extracted | Pipeline stages run |
|---|---|---:|---|
| Fresh conversation | `first_run` | 5 | all |
| Same transcript re-sent | `duplicate` | 0 | **none — short-circuited, near-zero duration** |
| One new turn appended | `incremental` | 1 | only for the new turn |

<!-- VISUAL — write-inspector.gif · obsidian/docs/media/write-inspector.gif · ~10s
     Screen recording clicking through the three priya-standup traces in order.
     Hold ~3s on each; the duration and facts-extracted fields must be legible.
     First thing the viewer should notice: the duration collapsing to ~0 on the
     duplicate trace. This GIF is the checkpoint benchmark, experienced instead
     of read.
     TODO: uncomment once obsidian/docs/media/write-inspector.gif exists —
     ![Write Inspector: the same conversation sent three times — full run, free duplicate, one-turn incremental](obsidian/docs/media/write-inspector.gif) -->

---

## ♻️ Never ingest the same conversation twice

The naive design — and Haven's own behavior before this subsystem — reprocesses
the **entire conversation** through the extraction pipeline on every save. Click
"Remember" on turn 500 and you pay for turns 1–499 again.

Haven's fix has three parts:

1. **Conversation checkpoints.** Each save records a fingerprint of what's been
   ingested, keyed by the conversation's `external_key`.
2. **Duplicate prevention.** An unchanged conversation short-circuits *before any
   pipeline stage runs* — zero LLM calls, zero new facts, zero duplicates in your
   vault. (Also why you can mash "Remember" without fear.)
3. **Incremental ingestion with Working Context.** New turns are extracted alone.
   Instead of the full transcript, the Extractor receives a compact,
   *retrieval-built* background block — the goals, recent decisions, pending tasks,
   and open questions relevant to the new turn — so cross-turn references still
   resolve ("no longer using the previous language" needs to know what the
   previous language was).

Edited, deleted, or reordered earlier turns are detected and fall back to a full
reprocess (`checkpoint_mode="fallback"`) — never a crash, never silent corruption.
All five failure/edge cases in the benchmark suite behaved as designed.

**Measured, up to 500 turns** ([methodology](#-benchmarks)):

| | Old (full reprocess) | New (checkpointed) |
|---|---|---|
| Re-send unchanged conversation | 3 LLM calls per send | **0 LLM calls** |
| Extractor prompt @ 500 turns | ~27,200 chars (~6,800 est. tokens) | **~3,075 chars (~769 est. tokens)** |
| Prompt growth with conversation length | linear, unbounded | **constant** |

<!-- VISUAL — prompt-growth-chart.svg · obsidian/docs/media/prompt-growth-chart.svg
     A single line chart, plotted from benchmarks/incremental_ingestion/results/results.json:
     x-axis = conversation length in turns (25 → 500), y-axis = estimated extractor
     prompt tokens. Two lines: "full reprocess" climbing linearly to ~6,800, and
     "Haven incremental" flat at ~769. Label the endpoints with their values; no
     legend box needed if the lines are labeled inline.
     First thing the viewer should notice: one line is flat. The chart makes the
     asymptotic claim in one glance — this is the README's most persuasive
     single visual after the hero GIF, which is why it sits at the end of this
     section as its punchline.
     TODO: uncomment once obsidian/docs/media/prompt-growth-chart.svg exists —
     ![Extractor prompt size vs conversation length: linear growth vs Haven's constant ~769 tokens](obsidian/docs/media/prompt-growth-chart.svg) -->

### What actually gets injected

When you click **Use Haven** in ChatGPT, the context block your LLM receives is
the read pipeline's final output: only acceptance-surviving memories, rendered by
the ContextBuilder with their canonical facts. Because the acceptance stage can
abstain, the honest answer to "nothing relevant is in the vault" is an *empty*
injection — not three paragraphs of plausible-looking noise silently steering your
conversation. What you inject is exactly what the Retrieval Inspector shows you,
because it's the same pipeline output.

---

## 📊 Benchmarks

Two independent suites live in `benchmarks/`, measuring two different claims.
Both drive the real, unmodified pipeline — no stage is reimplemented or
bypassed to produce a number. See [`benchmarks/README.md`](benchmarks/README.md)
for the full harness spec.

![Benchmark framework architecture: dataset JSON through the adapter registry (real Haven pipeline, baselines, ablations, upstream mem0), ingestion, search, the LLM judge, results.json, and judge-independent failure classification](obsidian/docs/media/benchmark-framework.svg)

### Write path: checkpointing removes redundant LLM calls

Every scenario drives the **real server** (`obsidian.server.main.app`) through
FastAPI's `TestClient`, with a scripted, marker-based fake LLM standing in for
the cloud model
([`benchmarks/incremental_ingestion/fake_llm.py`](benchmarks/incremental_ingestion/fake_llm.py)) —
deliberate, since a real LLM's comprehension varies run to run and isn't
reproducible evidence. What's actually measured is *how much conversation
reaches the Extractor at all*, which the fake LLM isolates cleanly:

```bash
python -m benchmarks.incremental_ingestion.run_benchmarks           # full scale
python -m benchmarks.incremental_ingestion.run_benchmarks --quick   # smoke run
```

Raw per-request data lands in
[`benchmarks/incremental_ingestion/results/results.json`](benchmarks/incremental_ingestion/results/results.json)
(re-plottable without rerunning) plus a generated Markdown digest. Token counts
are estimates (`chars / 4`) — directionally correct, not exact.

<details>
<summary><strong>Findings we reported instead of hiding — including one against ourselves</strong></summary>

The suite's most significant finding is a **real accuracy gap in Haven's own
incremental path**, documented in
[`benchmarks/incremental_ingestion/README.md`](benchmarks/incremental_ingestion/README.md):

- **Working Context only surfaces goal/decision/task/open-question memories.**
  Two otherwise-identical scenarios differ only in whether "The user uses Python"
  was classified as a `decision` or a plain `fact`. As a decision, a later "no
  longer uses their previous language" resolves correctly, matching the full
  reprocess exactly. As a plain fact — the more realistic classification —
  incremental ingestion **silently drops the update.** Root cause traced to
  `WorkingContextState.from_buckets` (pre-existing code); reported, not yet fixed,
  per the benchmarking phase's scope. It's on the [roadmap](#-roadmap).
- **Working Context retrieval time grows with vault size** (~0.002 s at 25 turns
  → ~0.138 s at 500) — a real, growing overhead this architecture adds, worth
  watching at much larger vault scales.
- **A keyword-overlap denominator edge case** can inflate overlap scores when
  query terms are absent; root cause documented, fix planned.

</details>

### Read path: LLM-judged retrieval quality (288 cases)

`benchmarks/runners/run_benchmarks.py` runs Haven's real pipeline
(`HavenAdapter` → `VaultWriter` + `OntologyPipeline` + `MemoryEngine`, no stage
bypassed) against a 288-case, LLM-judged dataset, alongside four naive
baselines — return-everything, most-recent, BM25 keyword search, embedding
similarity — and a retrieval-only ablation of Haven itself. Full results,
per-category breakdown, and a root-cause analysis of every failure are in
[`benchmarks/reports/archive/deepseek_validation_report.md`](benchmarks/reports/archive/deepseek_validation_report.md);
raw per-case data is in `benchmarks/results/results_*.json`.

```bash
python -m benchmarks.runners.run_benchmarks --adapter mem0     # baseline
python -m benchmarks.runners.run_benchmarks --adapter haven    # Haven
```

<details>
<summary><strong>The honest headline — including a category where Haven currently loses</strong></summary>

Haven's full pipeline beats plain keyword search (BM25) and its own
retrieval-only ablation by a wide margin. It currently loses on raw pass rate
to two deliberately naive baselines — "return everything" and "return the most
recent memory" — specifically on the categories built to test contradiction
and supersession handling.

The traced root cause is real and scoped, not a benchmark artifact:
`CanonicalMatcher` only recognizes an `UPDATE` via a conservative
prefix-extension rule, and doesn't yet detect a restated or differently-phrased
correction as a `SUPERSEDE`. So when a fact is contradicted in different words,
the old and new versions both stay independently retrievable instead of the old
one being archived — which is exactly the gap "wire `SUPERSEDE` into the
automatic pipeline" on the [roadmap](#-roadmap) closes. A benchmark suite that
only ever finds wins is a marketing document; this one found the specific,
named reason Haven loses on four categories, which is why the win on the other
seven is worth believing.

</details>

---

## 🤝 Can you trust it?

The same standard applies to this README: every claim above traces to a measured
number, a file in this repo, or a limitation stated here.

**Current limitations** (from the
[final report](benchmarks/results/final_report.md), verbatim in spirit):

- Single-user by design, local-only deployment, no authentication
- Dashboard refreshes on click rather than pushing updates
- `SUPERSEDE` knowledge decisions are implemented and tested in
  `KnowledgeUpdater` but not yet driven automatically by the production
  pipeline (`UPDATE` already is, via `CanonicalMatcher`'s prefix-extension
  rule — roadmap item 1 covers the remaining `SUPERSEDE` gap)
- The Working Context memory-type gap described above

**Where trust comes from, concretely:** your data is plain Markdown you can read
without Haven; the entire retrieval decision for any query is inspectable in the
dashboard; the demo is deterministic and offline; and the benchmark suite
documents its own blind spots.

---

## 🗂️ Using Haven day to day

**ChatGPT** — the `extension/` folder is a Chromium extension that adds
**Remember** (save this conversation) and **Use Haven** (inject relevant context
into the compose box) directly on chatgpt.com, plus vault search from the popup.

<!-- VISUAL — this one already exists in the repo. -->
![The "Use Haven" button in the ChatGPT compose area](obsidian/docs/media/extension-use-haven-button-chatgpt.png)

![Extension to Haven to Obsidian flow: content script and popup message-passing through background.js to the FastAPI server for Use Haven, Remember, and search, plus the separate Obsidian vault importer converging on the same /memory/commit route](obsidian/docs/media/extension-obsidian-flow.svg)

**Quick Capture** — not everything arrives as a conversation. The dashboard's
Quick Capture panel takes a free-form Markdown note (`POST /api/v1/capture`),
preserves your original text verbatim under `notes/` with a `source:
quick-capture` marker, and runs it through the same extraction pipeline as
everything else — one input path, zero special cases.

**Obsidian** — your vault *is* an Obsidian vault. Click **Open in Obsidian** on
the dashboard (with a copyable-path fallback on every platform) and browse
memories and concept pages as linked Markdown notes. Haven initializes its
`vault/`, `concepts/`, and hidden `.haven/` folders non-destructively inside an
existing vault.

<!-- VISUAL — obsidian-graph.png · obsidian/docs/media/obsidian-graph.png
     Obsidian's graph view over a seeded Haven vault: memory notes clustered
     around concept nodes, one memory note open in a side pane showing its YAML
     frontmatter (type, confidence, valid_from).
     First thing the viewer should notice: this is a normal Obsidian vault —
     the "your memories are files you already own" claim, photographed.
     TODO: uncomment once obsidian/docs/media/obsidian-graph.png exists —
     ![A Haven vault opened in Obsidian: concept graph plus a memory note's plain-Markdown frontmatter](obsidian/docs/media/obsidian-graph.png) -->

**API** — everything above is a thin client over a local FastAPI server
(`POST /api/v1/memory`, `POST /api/v1/capture`, retrieval and inspection routes).
Full reference: [`obsidian/server/README.md`](obsidian/server/README.md).

---

## 🏗️ How it's built

<details>
<summary><strong>Repo tour — where Haven's code lives inside <code>obsidian/</code></strong></summary>

Top-level folders are covered in [Repository layout](#-repository-layout).
Inside `obsidian/`, the write and read pipelines split into:

| Path | What it is |
|---|---|
| `obsidian/manager_ai/` | Write pipeline — Extractor → Classifier → ImportanceScorer → CanonicalMatcher → KnowledgeUpdater |
| `obsidian/memory_engine/` | Read pipeline — retriever, ranker, acceptance stage, context builder, VaultWriter |
| `obsidian/ontology/` | Concept graph, alias index, activation spreading, OntologyPipeline |
| `obsidian/checkpoint/` | Conversation checkpoints & incremental ingestion |
| `obsidian/server/` | FastAPI server, dashboard, demo seeding |
| `obsidian/tests/` | Test suite for all of the above |

Design decisions are written down as they were made — start with
[`obsidian/docs/ARCHITECTURE.md`](obsidian/docs/ARCHITECTURE.md) and
[`obsidian/docs/DECISIONS.md`](obsidian/docs/DECISIONS.md). The one that shapes
everything else: **no stage's correctness may depend on an LLM behaving a
particular way at runtime** (Decision 002).

</details>

---

## 🗺️ Roadmap

Priority-ordered; the full version with sourcing lives in
[`obsidian/docs/ROADMAP.md`](obsidian/docs/ROADMAP.md).

**Next** — wire `SUPERSEDE` into the automatic pipeline so conversations
can contradict existing knowledge end-to-end (`UPDATE`/refinement is already
automatic) · fix the Working Context memory-type gap the benchmarks surfaced ·
promote the richer structured-XML prompt builder to the live context renderer ·
fill the five remaining empty benchmark categories and fix the keyword-overlap
denominator edge case.

**Later** — Claude and Gemini conversation importers (ChatGPT is implemented
today) · memory decay / adaptive forgetting · a visual concept-graph explorer
(the data already exists on the inspection API) · live dashboard push updates.

**Post-hackathon** — multi-user vaults and cross-device sync · automatic
remembering (no manual click) · broader agent-ecosystem integration.

Deliberately out of scope (see
[`obsidian/docs/HACKATHON_SCOPE.md`](obsidian/docs/HACKATHON_SCOPE.md)):
autonomous graph evolution, graph embeddings, RL, background workers — the
project optimizes for correctness and inspectability before autonomy.

---

## 👋 Contributing

Issues and PRs are welcome — the codebase is deliberately legible (plain Python,
every stage independently testable, decisions documented in `obsidian/docs/`).

```bash
pip install -r obsidian/server/requirements.txt
pip install pytest
python -m pytest obsidian/tests/          # Haven's test suite
```

Good first contributions: a new conversation importer
(`obsidian/integrations/claude/` and `gemini/` are waiting stubs), one of the
five empty benchmark dataset categories, or the concept-graph visualizer. For the
surrounding mem0 monorepo's conventions (linting, CI, PR template), see
[`AGENTS.md`](AGENTS.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md).

## 🙏 Acknowledgements

- **[mem0](https://github.com/mem0ai/mem0)** — the foundation this fork builds
  on and the baseline that kept the benchmarks honest. Apache-2.0, like this repo.
- **[Obsidian](https://obsidian.md)** — for proving that plain Markdown plus
  links is enough to hold a mind, which is the storage philosophy Haven bets on.
- **[FastAPI](https://fastapi.tiangolo.com)** — the local server and dashboard.

---

<div align="center">

**Haven** — because a second brain you can't question isn't a brain, it's a liability.

*Built by [Siddhartha Khajuria](https://github.com/siddharthakhajuria) · Apache-2.0*

</div>
