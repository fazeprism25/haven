"""Dev-only script: seed haven_data/{vault,concepts,checkpoints,write_traces}
with demo data for the hackathon walkthrough.

Two passes, in order:

1. **Bulk facts** (``demo/demo_memories.md``) -- written straight through
   ``VaultWriter``/``OntologyPipeline``. No LLM call, instant.
2. **Scripted conversations** (``demo/demo_conversations.md``) -- replayed
   through Haven's real ``POST /api/v1/memory`` contract (via FastAPI's
   ``TestClient`` against the unmodified ``obsidian.server.main.app``),
   with the app's real ``ManagerAILLM`` swapped for a deterministic,
   marker-based fake LLM -- so this needs no API key. This is what
   produces real, schema-current ``WriteTrace`` and
   ``ConversationCheckpoint`` files.

Parsing and the fake-LLM machinery live in ``obsidian.server.demo_seed``,
shared with the dashboard's "Import Demo Data"/"Reset Demo" actions
(``POST /api/v1/dev/seed_demo``/``reset_demo`` in
``obsidian/server/main.py``) -- this script is the standalone-CLI caller of
that same logic, always targeting the fixed ``haven_data/`` directories via
its own ``TestClient``, independent of any already-running server.

Clears every demo output directory first, so re-running this script (or
``scripts/reset_demo.py``) is fully deterministic.

Usage:
    python scripts/seed_demo.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# Run as `python scripts/seed_demo.py` (per the README), sys.path[0] is this
# script's own directory, not the repo root, so `import obsidian` fails
# unless the repo root happens to already be on sys.path (e.g. an editable
# install of the root package). Insert it explicitly so the script works
# from a genuinely clean environment too.
sys.path.insert(0, str(REPO_ROOT))

VAULT_DIR = REPO_ROOT / "haven_data" / "vault"
CONCEPT_DIR = REPO_ROOT / "haven_data" / "concepts"
CHECKPOINT_DIR = REPO_ROOT / "haven_data" / "checkpoints"
WRITE_TRACE_DIR = REPO_ROOT / "haven_data" / "write_traces"

# obsidian.server.main.lifespan() reads these from os.environ at startup
# (see benchmarks/incremental_ingestion/harness.py's BenchmarkClient for the
# same pattern), so they must be set before the app is imported/started
# below -- not merely before this script's own writes.
os.environ["HAVEN_VAULT_DIR"] = str(VAULT_DIR)
os.environ["HAVEN_CONCEPT_DIR"] = str(CONCEPT_DIR)
os.environ["HAVEN_CHECKPOINT_DIR"] = str(CHECKPOINT_DIR)
os.environ["HAVEN_WRITE_TRACE_DIR"] = str(WRITE_TRACE_DIR)

from obsidian.memory_engine.vault_writer import VaultWriter  # noqa: E402
from obsidian.ontology.concept_graph import ConceptGraph  # noqa: E402
from obsidian.ontology.ontology_pipeline import OntologyPipeline  # noqa: E402
from obsidian.server import demo_seed  # noqa: E402


def _seed_bulk_facts() -> int:
    """Pass 1: write demo/demo_memories.md straight through the write path.

    Uses its own fresh ``ConceptGraph``/``VaultWriter``/``OntologyPipeline``
    (not the app's) since this runs before the app -- and therefore its own
    ``ConceptGraphLoader`` startup load -- ever starts in pass 2 below.
    """
    graph = ConceptGraph()
    vault_writer = VaultWriter(VAULT_DIR, concept_graph=graph)
    ontology_pipeline = OntologyPipeline(graph, CONCEPT_DIR)
    return demo_seed.seed_bulk_facts(vault_writer, ontology_pipeline)


def _seed_conversations() -> int:
    """Pass 2: replay demo/demo_conversations.md through the real /memory route.

    Uses ``TestClient`` against the unmodified ``obsidian.server.main.app``
    -- the same technique
    ``benchmarks/incremental_ingestion/harness.py::BenchmarkClient`` already
    uses -- so every checkpoint lookup, Working Context retrieval, and
    write-trace capture in ``save_memory`` runs for real. The only thing
    swapped is ``app.state.manager_pipeline``'s LLM (see
    ``demo_seed.ScriptedDemoLLM``); no route, pipeline, or retrieval logic
    is reimplemented here.
    """
    from fastapi.testclient import TestClient

    from obsidian.server.main import app

    calls = demo_seed.parse_conversations(
        demo_seed.DEMO_CONVERSATIONS_FILE.read_text(encoding="utf-8")
    )

    with TestClient(app) as client:
        client.app.state.manager_pipeline = demo_seed.build_scripted_pipeline()
        for call in calls:
            payload = {
                "conversation": [
                    {"role": role, "content": content} for role, content in call["turns"]
                ],
                "external_key": call["external_key"],
            }
            response = client.post("/api/v1/memory", json=payload)
            if response.status_code != 200:
                raise RuntimeError(
                    f"Seeding conversation call {call['label']!r} "
                    f"[external_key={call['external_key']}] failed: "
                    f"{response.status_code} {response.text}"
                )
    return len(calls)


def main() -> None:
    for demo_dir in (VAULT_DIR, CONCEPT_DIR, CHECKPOINT_DIR, WRITE_TRACE_DIR):
        if demo_dir.exists():
            shutil.rmtree(demo_dir)

    fact_count = _seed_bulk_facts()
    call_count = _seed_conversations()

    print(f"Seeded {fact_count} bulk memories.")
    print(
        f"Replayed {call_count} scripted conversation calls "
        "(write traces + checkpoints generated)."
    )
    print(f"Vault:        {VAULT_DIR}")
    print(f"Concepts:     {CONCEPT_DIR}")
    print(f"Checkpoints:  {CHECKPOINT_DIR}")
    print(f"Write traces: {WRITE_TRACE_DIR}")


if __name__ == "__main__":
    main()
