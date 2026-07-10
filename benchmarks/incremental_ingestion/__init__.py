"""Benchmark suite comparing Haven's two ``POST /memory`` code paths:

* **old / full** -- no ``external_key`` supplied. Every "Remember" click
  reprocesses the entire conversation through the Manager AI pipeline,
  exactly as the endpoint behaved before the Conversation Checkpoint
  subsystem existed (PR 1-4 in this project's own history).
* **new / incremental** -- ``external_key`` supplied. Checkpointing
  (PR 1-3) short-circuits unchanged conversations, and incremental
  ingestion with Working Context (PR 4) sends only new turns to the
  Extractor plus a compact, retrieval-based background block.

Both code paths are the *same, real, unmodified* production code
(``obsidian.server.main.save_memory``) -- this package never
reimplements or bypasses any pipeline stage. It only:

1. Drives that endpoint through :class:`fastapi.testclient.TestClient`,
   exactly like ``obsidian/tests/server/test_save_memory*.py`` already
   does.
2. Installs a scripted, marker-based fake LLM (see
   :mod:`benchmarks.incremental_ingestion.fake_llm`) instead of a real
   one, so the suite is fast, free, and reproducible.
3. Wraps (never replaces) a handful of real collaborator methods with
   timers so per-request cost breakdowns can be read after each call
   (see :mod:`benchmarks.incremental_ingestion.harness`).

See ``README.md`` in this directory for the full methodology, what these
numbers do and do not prove, and how to run the suite.
"""
