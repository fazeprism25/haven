# Demo Conversations — Personal AI Research space

Scripted conversation for the second, "Personal AI Research" demo Memory
Space (see `demo_memories_personal.md`'s own header and
`obsidian/server/demo_seed.py`'s `dataset_for_space`). Same mechanism as
`demo_conversations.md` -- replayed through the real `ManagerPipeline` via
`POST /api/v1/memory`, deterministic marker-based fake LLM, no API key
needed -- just a different, unrelated story: a personal, weekends-only
side project instead of Haven itself.

## Me, personal side project [external_key=personal-research-notes]

### Call 1 -- first run: project, decision, preference, task

- user: FACT[me-personal-project]: I'm building a personal knowledge-graph prototype using embeddings, separate from my day job.
- user: FACT[me-personal-decision]: I chose sentence-transformers over OpenAI's embedding API for this prototype, since a local model keeps it free to run.
- user: FACT[me-personal-preference]: I prefer reading papers on Sunday mornings with coffee, before anything else is on my plate.
- user: FACT[me-personal-task]: I need to reproduce a MemGPT-style paging experiment on my own hardware.
- assistant: Got it -- noted the prototype project, the embeddings decision, your Sunday reading preference, and the reproduction task.

### Call 2 -- same notes reopened unchanged (duplicate)

- user: FACT[me-personal-project]: I'm building a personal knowledge-graph prototype using embeddings, separate from my day job.
- user: FACT[me-personal-decision]: I chose sentence-transformers over OpenAI's embedding API for this prototype, since a local model keeps it free to run.
- user: FACT[me-personal-preference]: I prefer reading papers on Sunday mornings with coffee, before anything else is on my plate.
- user: FACT[me-personal-task]: I need to reproduce a MemGPT-style paging experiment on my own hardware.
- assistant: Got it -- noted the prototype project, the embeddings decision, your Sunday reading preference, and the reproduction task.

### Call 3 -- follow-up: hit a compute blocker, new task (incremental)

- user: FACT[me-personal-project]: I'm building a personal knowledge-graph prototype using embeddings, separate from my day job.
- user: FACT[me-personal-decision]: I chose sentence-transformers over OpenAI's embedding API for this prototype, since a local model keeps it free to run.
- user: FACT[me-personal-preference]: I prefer reading papers on Sunday mornings with coffee, before anything else is on my plate.
- user: FACT[me-personal-task]: I need to reproduce a MemGPT-style paging experiment on my own hardware.
- assistant: Got it -- noted the prototype project, the embeddings decision, your Sunday reading preference, and the reproduction task.
- user: FACT[me-personal-blocker]: My embedding experiments are blocked on not having a GPU for anything larger than a small local model.
- user: FACT[me-personal-followup]: Since I'm stuck on compute, my new task is to write up the literature review while I wait to sort out GPU access.
- assistant: Makes sense -- noted the compute blocker and the literature-review task in the meantime.
