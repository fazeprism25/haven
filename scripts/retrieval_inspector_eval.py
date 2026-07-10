"""Dev-only script: 50-query Retrieval Inspector evaluation for AcceptanceStage.

Seeds a fresh, isolated copy of the demo vault (same source data as
``scripts/seed_demo.py``, same write path: ``VaultWriter`` +
``OntologyPipeline``), then runs 50 hand-labelled queries against it twice
through ``MemoryEngine.query_with_trace`` -- once with AcceptanceStage
effectively limited to its pre-existing behaviour (stage 1 only, i.e. today's
``minimum_candidate_score`` floor, matching the pipeline before this feature),
and once with AcceptanceStage's real defaults -- and reports the difference:
accepted-candidate counts, a retrieval-level precision/recall proxy against
the hand-labelled expected fact per query, the off-topic false-positive rate,
per-candidate rejection-reason breakdowns (from the Retrieval Inspector trace,
not re-derived), and latency.

No LLM calls are made anywhere in this script -- retrieval, ranking,
acceptance, and context building are all deterministic, so this is runnable
without Ollama/Qwen/any API key.

Usage:
    python scripts/retrieval_inspector_eval.py
"""

from __future__ import annotations

import re
import shutil
import statistics
import tempfile
from collections import Counter
from pathlib import Path
from typing import List, NamedTuple, Tuple

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine.acceptance_stage import AcceptanceConfig
from obsidian.memory_engine.engine import MemoryEngine
from obsidian.memory_engine.memory_store import MemoryStore
from obsidian.memory_engine.vault_writer import VaultWriter
from obsidian.ontology.alias_index import AliasIndex
from obsidian.ontology.concept_graph_loader import ConceptGraphLoader
from obsidian.ontology.concept_parser import ConceptParser
from obsidian.ontology.ontology_pipeline import OntologyPipeline
from obsidian.ontology.retrieval_config import RetrievalConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_MEMORIES_FILE = REPO_ROOT / "demo" / "demo_memories.md"

SECTION_MEMORY_TYPES = {
    "Projects": MemoryType.PROJECT,
    "Decisions": MemoryType.DECISION,
    "Beliefs": MemoryType.BELIEF,
    "Preferences": MemoryType.PREFERENCE,
    "Active Tasks": MemoryType.TASK,
    "Technical Stack": MemoryType.FACT,
    "Future Roadmap": MemoryType.GOAL,
}

# "Stage 1 only" reproduces the pipeline's behaviour before AcceptanceStage
# existed: minimum_candidate_score is the only cutoff (via RetrievalConfig,
# untouched), abstention/gap-cut/relative-floor/hard-cap are all disabled.
BEFORE_ACCEPTANCE = AcceptanceConfig(
    abstention_score=0.0,
    min_gap=1.0,
    gap_window=1,
    relative_floor_ratio=0.0,
    acceptance_max_k=1_000_000,
)
AFTER_ACCEPTANCE = AcceptanceConfig()  # real defaults from the design doc


class Query(NamedTuple):
    text: str
    expected_substrings: Tuple[str, ...]  # () for off-topic queries


QUERIES: List[Query] = [
    # --- Projects ---
    Query("What is Project Atlas?", ("Project Atlas, a B2B SaaS tool",)),
    Query("Tell me about Project Nova", ("internal analytics dashboard",)),
    Query("What is Haven?", ("personal second brain that stores memories",)),
    Query("What do I contribute to Haven's evaluation work?", ("benchmark fixtures",)),
    # --- Decisions ---
    Query("What database did I choose for Project Atlas?", ("MongoDB over Postgres",)),
    Query("What database do I use for Project Nova?", ("Postgres for Project Nova",)),
    Query("What CI/CD tool did I pick?", ("GitHub Actions for CI/CD",)),
    Query("What vector database do I use?", ("Qdrant as the vector database",)),
    Query("Where do I store secrets?", ("HashiCorp Vault",)),
    # --- Beliefs ---
    Query("What do I believe about Rust?", ("no longer believe Rust",)),
    Query("What do I believe about Platform Engineering?", ("Platform Engineering",)),
    Query("What do I believe about commit size and CI?", ("small, frequent commits",)),
    Query("What do I believe makes a second brain useful?", ("retrieval is fast and trustworthy",)),
    # --- Preferences ---
    Query("What editor theme do I prefer?", ("dark mode",)),
    Query("Do I write design docs before coding?", ("short design doc",)),
    Query("What shell do I prefer?", ("Zsh over Bash",)),
    Query("What format do I keep notes in?", ("Markdown with YAML frontmatter",)),
    # --- Active Tasks ---
    Query("What am I wiring in Haven's FastAPI server?", ("hybrid candidate retriever",)),
    Query("What tests do I need to write for the query rewriter?", ("V1 query rewriter",)),
    Query("What am I migrating for Project Nova?", ("event-driven pipeline",)),
    Query("What flakiness am I investigating?", ("GitHub Actions matrix build",)),
    Query("What demo am I drafting?", ("demo dataset and seeding script",)),
    # --- Technical Stack ---
    Query("What language does Haven's write pipeline run on?", ("Python 3.11",)),
    Query("What backend does Project Atlas use?", ("Node.js service layer",)),
    Query("What does Project Nova's dashboard read from?", ("reads from Postgres",)),
    Query("What powers semantic search?", ("Semantic search runs on Qdrant",)),
    # --- Future Roadmap ---
    Query("What retrieval feature do I want to add to Haven?", ("semantic, embedding-based retrieval",)),
    Query("What browser extension do I want to build?", ("browser extension that captures memories",)),
    Query("What decision handling do I want to implement?", ("UPDATE and SUPERSEDE decision handling",)),
    Query("What cloud provider do I want for the LLM judge?", ("cloud provider in addition to local Ollama",)),
    # --- Rephrasings reusing the same facts (still on-topic) ---
    Query("Why did I choose MongoDB over Postgres for Atlas?", ("MongoDB over Postgres",)),
    Query("Which vector DB supports hybrid dense and sparse retrieval?", ("hybrid dense and sparse retrieval",)),
    Query("Have I ever nearly committed an API key by mistake?", ("near-miss where an API key",)),
    Query("Is Rust still the right choice for our product work?", ("no longer believe Rust",)),
    Query("What is my view on siloed DevOps teams?", ("siloed DevOps team",)),
    Query("Do I prefer batched merges or frequent commits?", ("small, frequent commits",)),
    Query("What terminal theme do I use?", ("dark mode",)),
    Query("What note-taking format keeps things portable?", ("stay portable",)),
    Query("What am I building for my personal second brain?", ("personal second brain that stores memories",)),
    Query("What's the status of Haven's browser extension idea?", ("browser extension that captures memories",)),
    # --- Off-topic (expect empty context) ---
    Query("What's the weather forecast for tomorrow?", ()),
    Query("Explain quantum computing to me", ()),
    Query("What's a good recipe for banana bread?", ()),
    Query("How do I train for a marathon?", ()),
    Query("What's the capital of France?", ()),
    Query("Recommend a good sci-fi movie", ()),
    Query("How does photosynthesis work?", ()),
    Query("What's the best way to learn guitar?", ()),
    Query("Give me a stock market forecast", ()),
    Query("How do I fix a leaky kitchen faucet?", ()),
]

assert len(QUERIES) == 50, f"expected 50 queries, got {len(QUERIES)}"


def _seed_vault(vault_dir: Path, concept_dir: Path) -> None:
    memories = []
    memory_type = MemoryType.FACT
    for line in DEMO_MEMORIES_FILE.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^##\s+(.+)", line)
        if heading:
            memory_type = SECTION_MEMORY_TYPES[heading.group(1).strip()]
            continue
        bullet = re.match(r"^[-*]\s+(.+)", line)
        if bullet:
            memories.append((memory_type, bullet.group(1).strip()))

    vault_writer = VaultWriter(vault_dir)
    from obsidian.ontology.concept_graph import ConceptGraph

    graph = ConceptGraph()
    ontology_pipeline = OntologyPipeline(graph, concept_dir)
    for mtype, canonical_fact in memories:
        knowledge = KnowledgeObject(canonical_fact=canonical_fact, memory_type=mtype)
        vault_writer.write(knowledge)
        ontology_pipeline.process(knowledge)


def _build_engine(vault_dir: Path, concept_dir: Path, acceptance_config: AcceptanceConfig) -> MemoryEngine:
    concept_graph = ConceptGraphLoader().load(concept_dir)
    memory_store = MemoryStore(vault_dir)
    memory_store.load()
    alias_index = AliasIndex()
    alias_index.build([
        ConceptParser().read(p).concept for p in sorted(concept_dir.glob("*.md"))
    ])
    return MemoryEngine(
        alias_index,
        concept_graph,
        memory_store,
        config=RetrievalConfig(),
        acceptance_config=acceptance_config,
    )


def _run(engine: MemoryEngine, label: str) -> dict:
    total_accepted = 0
    latencies_ms: List[float] = []
    rejection_counts: Counter = Counter()
    on_topic_hits = 0
    on_topic_total = 0
    precisions: List[float] = []
    off_topic_false_positives = 0
    off_topic_total = 0

    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    for query in QUERIES:
        context, trace = engine.query_with_trace(query.text)
        latencies_ms.append(trace.pipeline_stats.retrieval_latency_ms)
        accepted_traces = [ct for ct in trace.candidates if ct.accepted]
        total_accepted += len(accepted_traces)
        for ct in trace.candidates:
            if not ct.accepted:
                rejection_counts[ct.rejection_reason] += 1

        if query.expected_substrings:
            on_topic_total += 1
            relevant_accepted = sum(
                1
                for ct in accepted_traces
                if any(sub in ct.canonical_fact for sub in query.expected_substrings)
            )
            found = relevant_accepted > 0
            if found:
                on_topic_hits += 1
            if accepted_traces:
                precisions.append(relevant_accepted / len(accepted_traces))
            print(
                f"  [{'HIT ' if found else 'MISS'}] {query.text!r} "
                f"-> accepted={len(accepted_traces)} context_len={len(context)}"
            )
        else:
            off_topic_total += 1
            if accepted_traces:
                off_topic_false_positives += 1
            print(
                f"  [{'FP  ' if accepted_traces else 'OK  '}] {query.text!r} "
                f"-> accepted={len(accepted_traces)}"
            )

    recall = on_topic_hits / on_topic_total if on_topic_total else 0.0
    precision = statistics.mean(precisions) if precisions else 0.0
    fp_rate = off_topic_false_positives / off_topic_total if off_topic_total else 0.0
    avg_latency = statistics.mean(latencies_ms)

    print(f"\n  On-topic recall      : {on_topic_hits}/{on_topic_total} = {recall:.3f}")
    print(f"  Precision (proxy)    : {precision:.3f}  (mean relevant/accepted over queries with >=1 accepted)")
    print(f"  Off-topic FP rate    : {off_topic_false_positives}/{off_topic_total} = {fp_rate:.3f}")
    print(f"  Total accepted (50q) : {total_accepted}")
    print(f"  Avg latency (ms)     : {avg_latency:.3f}")
    print(f"  Rejection breakdown  : {dict(rejection_counts)}")

    return {
        "recall": recall,
        "precision": precision,
        "fp_rate": fp_rate,
        "total_accepted": total_accepted,
        "avg_latency_ms": avg_latency,
        "rejection_counts": dict(rejection_counts),
    }


def main() -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="haven_inspector_eval_"))
    vault_dir = tmp_root / "vault"
    concept_dir = tmp_root / "concepts"
    vault_dir.mkdir(parents=True)
    concept_dir.mkdir(parents=True)
    try:
        _seed_vault(vault_dir, concept_dir)

        before_engine = _build_engine(vault_dir, concept_dir, BEFORE_ACCEPTANCE)
        after_engine = _build_engine(vault_dir, concept_dir, AFTER_ACCEPTANCE)

        before = _run(before_engine, "BEFORE (AcceptanceStage stage-1-only, i.e. today's behaviour)")
        after = _run(after_engine, "AFTER (AcceptanceStage with design-doc defaults)")

        print(f"\n{'=' * 70}\nSUMMARY (after - before)\n{'=' * 70}")
        print(f"  Recall     : {before['recall']:.3f} -> {after['recall']:.3f}")
        print(f"  Precision  : {before['precision']:.3f} -> {after['precision']:.3f}")
        print(f"  FP rate    : {before['fp_rate']:.3f} -> {after['fp_rate']:.3f}")
        print(f"  Accepted   : {before['total_accepted']} -> {after['total_accepted']}")
        print(
            f"  Latency ms : {before['avg_latency_ms']:.3f} -> {after['avg_latency_ms']:.3f} "
            f"({after['avg_latency_ms'] - before['avg_latency_ms']:+.3f})"
        )
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
