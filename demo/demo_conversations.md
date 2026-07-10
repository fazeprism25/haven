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
`_infer_memory_type` in `seed_demo.py`) -- not declared here -- so keep new
facts recognizable as a goal/decision/preference/project/task if you want
them to land in the matching Dashboard category.

## Priya, Software Engineer [external_key=priya-standup]

### Call 1 -- first run: goal, decision, preference, project, task

- user: FACT[priya-project]: I'm building a billing-service rewrite for our internal platform team.
- user: FACT[priya-decision]: I decided to use gRPC instead of REST for the new billing-service, since it needs to talk to five other internal services with strict latency budgets.
- user: FACT[priya-goal]: My goal this quarter is to ship the billing-service rewrite before the Q3 freeze.
- user: FACT[priya-preference]: I prefer writing integration tests before merging any service-boundary change.
- user: FACT[priya-task]: I need to set up a staging gRPC endpoint for the billing-service by Friday.
- assistant: Got it -- I've noted the project, the gRPC decision, this quarter's goal, your testing preference, and the staging task.

### Call 2 -- same standup notes reopened unchanged (duplicate)

- user: FACT[priya-project]: I'm building a billing-service rewrite for our internal platform team.
- user: FACT[priya-decision]: I decided to use gRPC instead of REST for the new billing-service, since it needs to talk to five other internal services with strict latency budgets.
- user: FACT[priya-goal]: My goal this quarter is to ship the billing-service rewrite before the Q3 freeze.
- user: FACT[priya-preference]: I prefer writing integration tests before merging any service-boundary change.
- user: FACT[priya-task]: I need to set up a staging gRPC endpoint for the billing-service by Friday.
- assistant: Got it -- I've noted the project, the gRPC decision, this quarter's goal, your testing preference, and the staging task.

### Call 3 -- follow-up: staging endpoint done, new task (incremental)

- user: FACT[priya-project]: I'm building a billing-service rewrite for our internal platform team.
- user: FACT[priya-decision]: I decided to use gRPC instead of REST for the new billing-service, since it needs to talk to five other internal services with strict latency budgets.
- user: FACT[priya-goal]: My goal this quarter is to ship the billing-service rewrite before the Q3 freeze.
- user: FACT[priya-preference]: I prefer writing integration tests before merging any service-boundary change.
- user: FACT[priya-task]: I need to set up a staging gRPC endpoint for the billing-service by Friday.
- assistant: Got it -- I've noted the project, the gRPC decision, this quarter's goal, your testing preference, and the staging task.
- user: FACT[priya-followup]: I finished the staging gRPC endpoint, so my new task is to write a load test for the billing-service before the freeze.
- assistant: Nice progress -- noted the new load-test task.

## Maya, University Student [external_key=maya-notes]

### Call 1 -- first run: goal, decision, preference, project, task

- user: FACT[maya-project]: I'm working on my senior capstone project, a mobile app that helps students split shared grocery bills.
- user: FACT[maya-decision]: I decided to use Flutter instead of native Swift and Kotlin for my capstone app, so I only have to maintain one codebase before the demo deadline.
- user: FACT[maya-goal]: My goal this semester is to finish a working capstone prototype before winter break.
- user: FACT[maya-preference]: I prefer studying on the campus library's silent floor rather than my dorm room.
- user: FACT[maya-task]: I need to finish the capstone's payment-splitting algorithm before my advisor meeting next week.
- assistant: Sounds like a solid plan for the semester.

### Call 2 -- follow-up: algorithm done, new task (incremental)

- user: FACT[maya-project]: I'm working on my senior capstone project, a mobile app that helps students split shared grocery bills.
- user: FACT[maya-decision]: I decided to use Flutter instead of native Swift and Kotlin for my capstone app, so I only have to maintain one codebase before the demo deadline.
- user: FACT[maya-goal]: My goal this semester is to finish a working capstone prototype before winter break.
- user: FACT[maya-preference]: I prefer studying on the campus library's silent floor rather than my dorm room.
- user: FACT[maya-task]: I need to finish the capstone's payment-splitting algorithm before my advisor meeting next week.
- assistant: Sounds like a solid plan for the semester.
- user: FACT[maya-followup]: I finished the payment-splitting algorithm, so my new task is to write the advisor meeting slides this weekend.
- assistant: Great, that should make the meeting go smoothly.

## Jordan, Product Manager [external_key=jordan-planning]

### Call 1 -- first run: goal, decision, preference, project, task

- user: FACT[jordan-project]: I'm leading the roadmap for our team's new analytics dashboard product.
- user: FACT[jordan-decision]: I decided to prioritize the export-to-CSV feature over new chart types this quarter, since three of our biggest customers asked for it directly.
- user: FACT[jordan-goal]: My goal this quarter is to ship the export-to-CSV feature and get it in front of our top five customers.
- user: FACT[jordan-preference]: I prefer running customer interviews before writing any PRD.
- user: FACT[jordan-task]: I need to schedule interviews with our top five customers before the next planning cycle.
- assistant: That's a clear set of priorities for the quarter.

### Call 2 -- same planning doc reopened unchanged (duplicate)

- user: FACT[jordan-project]: I'm leading the roadmap for our team's new analytics dashboard product.
- user: FACT[jordan-decision]: I decided to prioritize the export-to-CSV feature over new chart types this quarter, since three of our biggest customers asked for it directly.
- user: FACT[jordan-goal]: My goal this quarter is to ship the export-to-CSV feature and get it in front of our top five customers.
- user: FACT[jordan-preference]: I prefer running customer interviews before writing any PRD.
- user: FACT[jordan-task]: I need to schedule interviews with our top five customers before the next planning cycle.
- assistant: That's a clear set of priorities for the quarter.
