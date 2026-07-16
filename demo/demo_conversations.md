# Demo Conversations

Scripted multi-turn conversations for `scripts/seed_demo.py`'s second
seeding pass. Unlike `demo_memories.md` (written straight to the vault, no
LLM involved), these are replayed through Haven's real `ManagerPipeline`
(Extractor -> Classifier -> ImportanceScorer -> CanonicalMatcher ->
KnowledgeUpdater) via the real `POST /api/v1/memory` contract -- with a
deterministic, marker-based fake LLM standing in for the cloud model (see
`benchmarks/incremental_ingestion/fake_llm.py`), so seeding needs no API
key and produces real, schema-current `WriteTrace` and
`ConversationCheckpoint` files: the same artifacts a judge would get by
actually clicking "Remember" on these conversations themselves.

Both threads below continue the same story as `demo_memories.md` -- Haven's
own development, told truthfully -- from two contributors' points of view:
the maintainer running the benchmark/documentation-honesty pass, and a
teammate handling the Alibaba Cloud deployment. (`demo_conversations_personal.md`
is the equivalent file for the second, "Personal AI Research" demo Memory
Space -- see `obsidian/server/demo_seed.py`'s `dataset_for_space`.)

## Format

Each `##` heading is one fictional person's conversation thread, tagged
with the `external_key` used for conversation-level checkpointing. Each
`###` heading under it is one "Remember" call for that thread, sent in
file order -- and, like the real extension, each call's turns are the
**entire transcript so far**, not just what's new. Haven's own checkpoint
diffing (`obsidian/checkpoint/diff.py`) decides whether a call lands as
`first_run`, `duplicate`, or `incremental` -- this file never states the
mode itself, only what the person says in each call.

`- role: text` bullets are turns. A turn's text becomes a real,
deterministically-extracted fact when it starts with `FACT[id]:` (see
`fake_llm.py`'s marker syntax); plain turns (e.g. assistant replies) are
never extracted, exactly like an unremarkable line in a real conversation.
Each fact's `MemoryType` is inferred from its own wording (see
`_MEMORY_TYPE_KEYWORDS`/`infer_memory_type` in `demo_seed.py`) -- not
declared here -- so keep new facts recognizable as a
decision/blocker/rule/open-question/goal/preference/project/task if you
want them to land in the matching Dashboard category.

## Me, Haven's maintainer [external_key=haven-benchmark-honesty]

### Call 1 -- first run: project, decision, goal, preference, task

- user: FACT[me-project]: I maintain Haven's benchmark suite, comparing the full pipeline against naive baselines like BM25, Return-All, and Recency.
- user: FACT[me-decision]: I chose a DeepSeek judge over the local Ollama qwen3:8b judge for the canonical 288-case benchmark pass, since it scores retrieval quality more consistently.
- user: FACT[me-goal]: My goal this week is to close the CanonicalMatcher SUPERSEDE gap so Haven's pass rate clears the naive baselines on contradiction-heavy categories.
- user: FACT[me-preference]: I prefer citing the exact result file behind a benchmark claim over a clean-sounding number I can't trace.
- user: FACT[me-task]: I need to re-run the 288-case benchmark suite before merging any retrieval-affecting change.
- assistant: Got it -- I've noted the benchmark suite project, the DeepSeek judge decision, this week's SUPERSEDE goal, your citation preference, and the re-run task.

### Call 2 -- same standup notes reopened unchanged (duplicate)

- user: FACT[me-project]: I maintain Haven's benchmark suite, comparing the full pipeline against naive baselines like BM25, Return-All, and Recency.
- user: FACT[me-decision]: I chose a DeepSeek judge over the local Ollama qwen3:8b judge for the canonical 288-case benchmark pass, since it scores retrieval quality more consistently.
- user: FACT[me-goal]: My goal this week is to close the CanonicalMatcher SUPERSEDE gap so Haven's pass rate clears the naive baselines on contradiction-heavy categories.
- user: FACT[me-preference]: I prefer citing the exact result file behind a benchmark claim over a clean-sounding number I can't trace.
- user: FACT[me-task]: I need to re-run the 288-case benchmark suite before merging any retrieval-affecting change.
- assistant: Got it -- I've noted the benchmark suite project, the DeepSeek judge decision, this week's SUPERSEDE goal, your citation preference, and the re-run task.

### Call 3 -- follow-up: benchmark run finished, new task (incremental)

- user: FACT[me-project]: I maintain Haven's benchmark suite, comparing the full pipeline against naive baselines like BM25, Return-All, and Recency.
- user: FACT[me-decision]: I chose a DeepSeek judge over the local Ollama qwen3:8b judge for the canonical 288-case benchmark pass, since it scores retrieval quality more consistently.
- user: FACT[me-goal]: My goal this week is to close the CanonicalMatcher SUPERSEDE gap so Haven's pass rate clears the naive baselines on contradiction-heavy categories.
- user: FACT[me-preference]: I prefer citing the exact result file behind a benchmark claim over a clean-sounding number I can't trace.
- user: FACT[me-task]: I need to re-run the 288-case benchmark suite before merging any retrieval-affecting change.
- assistant: Got it -- I've noted the benchmark suite project, the DeepSeek judge decision, this week's SUPERSEDE goal, your citation preference, and the re-run task.
- user: FACT[me-followup]: I finished the 288-case benchmark run, so my new task is to strip the fabricated benchmark numbers from the README and replace them with the real 240/288 story.
- assistant: Nice progress -- the documentation-honesty pass is now on my task list too.

## Teammate, handling the Alibaba Cloud deployment [external_key=haven-deployment]

### Call 1 -- first run: project, decision, preference, task

- user: FACT[team-project]: I'm working on hardening the Alibaba Cloud deployment's provisioning script for Haven's hackathon demo.
- user: FACT[team-decision]: I decided to deploy Haven on Alibaba Cloud because the Qwen DashScope integration cuts latency for a same-region deployment.
- user: FACT[team-preference]: I prefer running a security review before any production deployment goes live, even for a demo.
- user: FACT[team-task]: I need to finish hardening the Alibaba Cloud provisioning script before the demo link can go live.
- assistant: Got it -- noted the deployment project, the Alibaba Cloud decision, your pre-deploy review preference, and the hardening task.

### Call 2 -- follow-up: security review still pending (incremental)

- user: FACT[team-project]: I'm working on hardening the Alibaba Cloud deployment's provisioning script for Haven's hackathon demo.
- user: FACT[team-decision]: I decided to deploy Haven on Alibaba Cloud because the Qwen DashScope integration cuts latency for a same-region deployment.
- user: FACT[team-preference]: I prefer running a security review before any production deployment goes live, even for a demo.
- user: FACT[team-task]: I need to finish hardening the Alibaba Cloud provisioning script before the demo link can go live.
- assistant: Got it -- noted the deployment project, the Alibaba Cloud decision, your pre-deploy review preference, and the hardening task.
- user: FACT[team-blocker]: The Alibaba Cloud deployment is blocked on a pending security review of the provisioning script before the demo link can go live.
- assistant: Understood -- flagging that as a blocker until the security review clears.
