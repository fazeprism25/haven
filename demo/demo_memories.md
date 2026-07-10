# Demo Memories

Sample memories for seeding Haven's vault and concept graph via
`scripts/seed_demo.py`. Each bullet under a section becomes one memory;
the section heading determines its memory type. Edit freely — add,
remove, or reorder bullets and re-run the seeding script.

## Projects

- I'm building Project Atlas, a B2B SaaS tool for managing customer onboarding workflows.
- I maintain Project Nova, an internal analytics dashboard used by the sales team.
- I'm building Haven, a personal second brain that stores memories as canonical facts plus a concept graph.
- I contribute demo data and benchmark fixtures to Haven's retrieval evaluation suite.

## Decisions

- I chose MongoDB over Postgres for Project Atlas because it stores mostly unstructured, user-generated content.
- I chose Postgres for Project Nova because its data is highly relational.
- I decided to use GitHub Actions for CI/CD instead of Jenkins or CircleCI, since the team is already on GitHub.
- I picked Qdrant as the vector database for semantic search because it supports hybrid dense and sparse retrieval with strong filtering.
- I decided to store secrets and configuration in HashiCorp Vault rather than plain .env files, after a near-miss where an API key almost got committed.

## Beliefs

- I no longer believe Rust is the right fit for most of our day-to-day product work — the development velocity slowdown outweighs the compile-time guarantees. Gradual typing with TypeScript, or Python under strict Mypy, strikes a better balance between speed and safety.
- I believe Platform Engineering — a dedicated team building an internal developer platform with self-service golden paths — beats both a siloed DevOps team and full "you build it, you run it" ownership.
- I believe small, frequent commits with fast CI feedback loops produce fewer regressions than large batched merges.
- I believe a second brain is only useful if retrieval is fast and trustworthy — a system that returns stale or wrong facts is worse than no memory system at all.

## Preferences

- I prefer dark mode in every editor and terminal I use.
- I prefer writing a short design doc before touching code for anything non-trivial.
- I prefer Zsh over Bash for daily shell work.
- I prefer keeping personal notes in Markdown with YAML frontmatter rather than a proprietary note format, so they stay portable.

## Active Tasks

- I'm wiring Haven's FastAPI retrieval server to the ontology-based hybrid candidate retriever.
- I need to write integration tests for the V1 query rewriter before merging it into the main retrieval path.
- I'm migrating Project Nova's reporting pipeline off nightly cron jobs onto an event-driven pipeline.
- I need to investigate flakiness in the GitHub Actions matrix build.
- I'm drafting the demo dataset and seeding script that show off Haven's write pipeline end to end.

## Technical Stack

- Haven's write pipeline runs on Python 3.11 and persists to a plain-file Markdown vault, with no external database required.
- Project Atlas's backend is a Node.js service layer on top of MongoDB.
- Project Nova's dashboard reads from Postgres and is deployed on the same Kubernetes cluster as the rest of the analytics stack.
- Semantic search runs on Qdrant, self-hosted on Kubernetes for hybrid dense and sparse retrieval.

## Future Roadmap

- I want to add semantic, embedding-based retrieval to Haven's memory engine, moving beyond keyword-only candidate retrieval.
- I want to build a browser extension that captures memories directly from any webpage.
- I want to implement UPDATE and SUPERSEDE decision handling in the Haven write pipeline instead of leaving them as TODO stubs.
- I want to run the LLM judge benchmark against a cloud provider in addition to local Ollama.
