# Demo Memories — Personal AI Research space

A second, smaller bundled dataset, seeded instead of `demo_memories.md` when
the active Memory Space's name contains "personal" or "research" (see
`obsidian/server/demo_seed.py`'s `dataset_for_space`). Deliberately a
*different world* from the Haven-the-product story in `demo_memories.md`: a
personal, weekends-only side project exploring memory-system research, so
creating this space and switching to it visibly changes every Dashboard
section instead of duplicating the primary space's content.

## Projects

- (T-60d) I'm building a personal knowledge-graph prototype — an embeddings-based memory layer I tinker with outside work hours, unrelated to my day job.
- I contribute occasional notes to a small reading group that discusses memory-architecture papers.

## Decisions

- (T-55d) I chose `sentence-transformers` over OpenAI's embedding API for this prototype, since a local model keeps the whole side project free to run.
- (T-20d) I decided to keep this project strictly to weekends and evenings, with no work data or work time anywhere near it.

## Beliefs

- (T-55d) I believe explainable retrieval matters more than a marginally higher benchmark score, even in a hobby embeddings project.
- (T-30d) I'm assuming most published benchmark numbers in memory-architecture papers are somewhat optimistic versus a real, messy personal dataset.

## Preferences

- I prefer reading papers on Sunday mornings with coffee, before anything else is on my plate.
- I prefer reproducing a paper's core result myself before trusting its abstract.

## Active Tasks

- (T-10d) I'm working on a literature review of vector-symbolic memory architectures before deciding my prototype's next direction.
- (T-25d) I need to reproduce a MemGPT-style paging experiment on my own hardware to see if its claims hold up.
- (T-5d) I'm working on a small benchmark script to compare my prototype's retrieval against a plain embedding baseline.

## Technical Stack

- (T-55d) My prototype's embeddings pipeline runs locally on `sentence-transformers`, no cloud API key required.
- (T-10d) I'm reading about vector-symbolic memory architectures as background for the prototype's next iteration.

## Future Roadmap

- (T-0d) My goal is to finish a blog post explaining the project, aimed at wrapping up by the end of the year.
- (T-25d) I want to decide whether a symbolic graph layer is worth adding on top of the embeddings I already have working.

## Blockers

- (T-20d) I don't have a GPU for larger embedding experiments, so anything past a small local model is on hold.

## Open Questions

- (T-25d) Open question I haven't settled: is a symbolic graph layer worth the added complexity over embeddings alone for a project this size?
- (T-10d) Still unresolved: how much of my retrieval quality gap versus published numbers is my dataset being smaller, versus a real implementation gap?

## Rules

- (T-20d) The rule is: this project only gets worked on weekends and evenings — never during work hours.
- (T-20d) The rule is: never mix any work-related data or context into this personal experiment.

## Implementation State

- (T-55d) The embeddings pipeline is implemented and runs end to end on a small local dataset; the symbolic graph layer hasn't been started yet.
- (T-5d) The comparison benchmark script against a plain embedding baseline is half-written, not yet producing numbers.

## Code Areas

- (T-55d) `prototype/graph_memory.py` is where the embeddings pipeline lives.
- (T-5d) `prototype/bench_compare.py` is the half-finished benchmark script I'm currently editing.

## People

- My mentor reviews my reading notes and draft blog posts every few weeks.

## Events

- (T-30d) I'm attending a local ML meetup in September 2026 to talk to other people working on personal memory systems.
- (T-10d) I finished a first full read-through of the MemGPT paper on my own.

## Skills

- I'm comfortable with PyTorch for small local embedding experiments.
- I can implement a paper's core method from scratch instead of only using someone else's release.

## Interests

- I'm interested in cognitive science research on human memory consolidation as an analogy for retrieval design.
- I follow new local-first embedding models that don't require a cloud API key.

## Traits

- I enjoy tinkering with side projects that have no deadline pressure attached.

## Habits

- I review my reading notes every Sunday evening and decide what to read next.
