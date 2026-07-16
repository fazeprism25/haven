# Demo Memories

Sample memories for seeding Haven's vault and concept graph via
`scripts/seed_demo.py` / the dashboard's "Import Demo Data" button. Each
bullet under a section becomes one memory; the section heading determines
its memory type (see `obsidian/server/demo_seed.py`'s `SECTION_MEMORY_TYPES`
for the full 18-section mapping). Edit freely — add, remove, or reorder
bullets and re-run the seeding script.

Every fact below is true of *this* repository — the demo is Haven telling
its own story, not a fictional stand-in product. A bullet may start with a
`(T-90d)` marker to place it that many days in the past instead of "now"
(stripped before the fact is stored) — see `parse_bulk_memories`. This
staggers the timeline roughly: initial idea → deterministic-retrieval
architecture → the Manager AI write pipeline → the Memory Engine read
pipeline and dashboard → the Query Rewriter (Haven's first cloud-LLM call) →
Working Context/Structured Prompt → Memory Review → the 288-case benchmark →
the Context Planner design → extension/dashboard polish → Ontology V2 and a
documentation-honesty pass → the Alibaba Cloud deployment and this very demo
rebuild, right up to the hackathon submission itself.

## Projects

- (T-200d) I'm building Haven, a personal second-brain system: a deterministic, keyword-plus-ontology retrieval pipeline with no embeddings, so every answer stays fully explainable end to end.
- (T-16d) I maintain Haven's Manager AI write pipeline — Extractor, Classifier, ImportanceScorer, CanonicalMatcher, KnowledgeUpdater — that turns a conversation into canonical facts before anything touches the vault.
- (T-7d) I maintain Haven's benchmark suite under `benchmarks/`, comparing the full pipeline against naive baselines (BM25, Return-All, Recency, embeddings) and a retrieval-only ablation.
- (T-2d) I built a browser extension and dashboard on top of Haven's server so memories can be captured and reviewed without leaving the page I'm reading.

## Decisions

- (T-190d) I decided to make Haven's retrieval deterministic — keyword and ontology-graph based, with zero embeddings — so every ranked result stays fully explainable. It's one of the foundational decisions I've made for Haven's whole architecture.
- (T-11d) I decided to wire Haven's Query Rewriter to Qwen Cloud (`qwen-plus` via DashScope) using an OpenAI-compatible client — Haven's first cloud-LLM decision.
- (T-6d) I decided to wire Manager AI's Extractor, Classifier, and ImportanceScorer to that same Qwen Cloud pattern instead of inventing a second client integration — the same decision extended to the write path.
- (T-7d) I chose a DeepSeek judge over the local Ollama qwen3:8b judge for the canonical 288-case benchmark pass — a decision I revisit before every benchmark run, since it scores retrieval quality more consistently across categories.
- (T-1d) Of all the decisions I've made recently, deploying Haven on Alibaba Cloud is the one I'm proudest of — driven by Qwen DashScope's lower latency in the same region, because I want hackathon judges to experience a live deployment instead of localhost.
- (T-2d) I decided to strip the fabricated "vs. mem0" benchmark numbers from the README and article entirely and replace them with the real, more nuanced 288-case story — the hardest of the many decisions I've made on this project.

## Beliefs

- (T-190d) I believe deterministic, explainable retrieval is worth more in a personal memory system than a few extra points of recall from an opaque embedding-based ranker.
- (T-16d) I believe a second brain is only useful if retrieval is fast and trustworthy — a system that returns a stale or wrong fact is worse than no memory system at all.
- (T-3d) I believe a benchmark number that can't be traced back to a committed result file should never appear in a README, no matter how good the story would be if it were true.
- (T-7d) I'm assuming most of Haven's remaining retrieval misses come from missing ontology edges — like the `IS_A` relationship type that's declared but never emitted — rather than from the ranking formula itself.
- (T-150d) I believe small, frequent commits with fast feedback loops produce fewer regressions than large batched merges.

## Preferences

- I prefer dark mode in every editor and terminal I use.
- I prefer writing a short design doc and going through a few rounds of plan review before touching code for anything non-trivial.
- I prefer keeping personal notes in Markdown with YAML frontmatter rather than a proprietary note format, so they stay portable.
- I prefer citing the exact result file behind a claim over a clean-sounding number I can't trace.

## Active Tasks

- (T-0d) I'm currently working on rebuilding Haven's demo dataset so a first-time hackathon judge can see nearly every feature from one click.
- (T-2d) I'm working on finishing the Ontology V2 migration — `topic_canonicalizer.py` and `memory_domain.py` — before the hackathon submission deadline.
- (T-4d) I'm working on hardening the Alibaba Cloud deployment's provisioning script so it passes a security review before the demo link goes live.
- (T-2d) I'm working on polishing the dashboard and browser extension so a first-time judge can exercise nearly every feature from one click.
- (T-25d) I need to re-run the 288-case benchmark suite any time a retrieval-affecting change lands, per my own rule about never citing a stale number.

## Technical Stack

- (T-16d) Haven's write pipeline runs on Python 3.11 and persists to a plain-file Markdown vault, with no external database required.
- (T-11d) Haven's Query Rewriter and Manager AI's three LLM stages both call Qwen Cloud's `qwen-plus` model at the same DashScope OpenAI-compatible endpoint.
- (T-7d) I ran a 288-case, DeepSeek-judged benchmark comparing Haven's full pipeline against BM25, Return-All, Recency, and embedding baselines plus a retrieval-only ablation: Haven Full passed 240/288 (83.3%), beating BM25 (69.8%) and its own retrieval-only ablation (66.0%) by a wide margin, but losing raw pass rate to the naive Return-All (90.3%) and Recency (80.6%) baselines specifically on contradiction and supersession-heavy categories.
- (T-2d) Haven's dashboard and browser extension both call the same Memory Engine, in-process or over HTTP — no second retrieval implementation exists anywhere in the stack.
- (T-4d) Haven's demo deployment runs on Alibaba Cloud, provisioned via the scripts under `deploy/`.

## Future Roadmap

- (T-0d) My goal is to close the CanonicalMatcher SUPERSEDE gap so Haven's pass rate clears the naive Return-All/Recency baselines on contradiction-heavy categories too, not just BM25.
- (T-7d) My goal is to add the missing `IS_A` ontology relationship so entity-category queries like "what database does the user use" resolve without a keyword coincidence.
- (T-7d) I want to fill the five empty benchmark categories (active_context, insights, memory_recall, mistake_prevention, open_problems) instead of authoring new categories under different names.
- (T-0d) My goal is to ship an honest, fully-documented hackathon submission where every claim in the README and article traces back to a real, committed result file.
- (T-0d) My goal is to improve Haven's overall recall past 90% once the SUPERSEDE gap and the `IS_A` bridge both land.

## Blockers

- (T-4d) The Alibaba Cloud deployment is blocked on a pending security review of its provisioning script before the demo link can point at a live instance.
- (T-2d) Finishing the Ontology V2 migration before submission is blocked on double-checking `topic_canonicalizer.py`'s canonicalization table for regressions.
- (T-25d) Re-running the 288-case benchmark after any retrieval change is blocked on judge availability, since a fair comparison needs the same DeepSeek judge every time.

## Open Questions

- (T-25d) One open question I haven't resolved yet: should CanonicalMatcher detect SUPERSEDE (not just UPDATE), so a differently-phrased correction archives the old fact instead of leaving both independently retrievable?
- (T-9d) Still an open question: should Haven support memory decay, so older, unconfirmed facts lose retrieval weight over time instead of staying as salient as the day they were written?
- (T-6d) Unresolved question: should the Context Planner's 5-mode task table be implemented now, or is a per-category Acceptance Stage the better investment first?
- (T-2d) Open question I keep coming back to: should Manager AI's LLM provider be made swappable per deployment, now that Qwen Cloud has proven the OpenAI-compatible pattern end to end?

## Rules

- (T-1d) One of the rules I follow closely, this week especially: never cite a benchmark number in a README, article, or slide that doesn't trace back to a specific, committed result file.
- (T-150d) Another one of my rules: every retrieval-affecting change needs a benchmark run before merge, so a regression is caught before it reaches the demo.
- (T-190d) A rule among the ones I follow for Haven's whole codebase: no new retrieval, ranking, or ontology logic ever gets duplicated — reuse the existing Memory Engine method (`query`, `query_with_trace`, `query_working_context`, `query_structured`), never a second implementation.
- (T-0d) One more rule I'm following right now: a "not implemented yet" claim about Haven's own pipeline must be re-verified against the current file contents before it's repeated, since stale investigation notes have been wrong before.

## Implementation State

- (T-25d) CanonicalMatcher currently detects UPDATE via a conservative prefix-extension rule but never auto-returns SUPERSEDE — a differently-phrased correction leaves both the old and new fact independently retrievable.
- (T-3d) The Ontology V2 migration — `topic_canonicalizer.py` and `memory_domain.py` grouping all 18 memory types into 3 domains — is implemented and in the working tree, ready for the hackathon submission.
- (T-4d) The Alibaba Cloud provisioning script is written and present under `deploy/`, but hasn't yet passed a security review.
- (T-6d) Manager AI's three LLM stages are wired to a real Qwen Cloud client; Memory Review's preview/commit split means editing a reviewed fact never re-runs any of them.
- (T-11d) Working Context and Structured Prompt assembly are implemented at the engine layer and wired into the dashboard's "Resume Work" panel and the browser extension's preview dialog.

## Code Areas

- (T-25d) I'm actively modifying `obsidian/manager_ai/canonical_matcher.py` to close the SUPERSEDE gap.
- (T-11d) `obsidian/memory_engine/query_rewriter.py` is the file behind Haven's first Qwen Cloud integration — not being modified this week, but where that pattern started.
- (T-3d) I'm modifying `obsidian/manager_ai/topic_canonicalizer.py` and `obsidian/core/memory_domain.py` to finish the Ontology V2 migration.
- (T-7d) `benchmarks/judges/llm_judge.py` is the file that talks to the DeepSeek judge for the canonical 288-case benchmark pass.
- (T-4d) The `deploy/` folder holds the Alibaba Cloud provisioning scripts I'm currently modifying.
- (T-2d) `obsidian/server/static/dashboard.html` and the `extension/` folder are the two code areas I'm modifying most this week for the hackathon's UI polish pass.

## People

- I have a hackathon teammate handling a security pass on the Alibaba Cloud provisioning script while I finish the Ontology V2 migration.
- A fellow hackathon participant is building a personal-memory tool on top of a vector database instead of a keyword-plus-ontology graph, which is a useful contrast to compare notes with.

## Events

- (T-7d) I ran the canonical 288-case DeepSeek-judged benchmark pass on 2026-07-08 (commit `042e16e6`).
- (T-3d) I did the README/article documentation-honesty pass on 2026-07-12, replacing fabricated benchmark numbers with the real 288-case story.
- (T-0d) The hackathon submission deadline is 2026-07-20 — this final polish pass, including rebuilding the demo dataset, is the last stretch before it.

## Skills

- I'm comfortable designing deterministic, explainable retrieval pipelines without leaning on embeddings.
- I'm skilled at root-causing a retrieval-quality gap by tracing it through the actual pipeline stages instead of guessing.
- I've picked up enough Alibaba Cloud / DashScope provisioning to deploy a small production service solo.

## Interests

- I'm watching how MemGPT/Letta-style self-editing memory compares to Haven's own deterministic, no-embeddings approach.
- I'm curious about GraphRAG's global/local search split as a coarser precedent for Haven's own Context Planner design.
- I'm following DeepSeek and Qwen's open model releases, since both already power a real part of Haven's own pipeline.

## Traits

- I tend to re-verify an old investigation note against the current code before repeating it, rather than trusting my own past notes blindly.
- I like building systems where every output is traceable back to a real, inspectable cause.

## Habits

- I run the full test suite before ending any session that touched pipeline code.
- I do a documentation-honesty pass — checking every headline claim against a real result file — before any public-facing submission.
