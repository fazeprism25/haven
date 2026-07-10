import os
import json
import subprocess
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Type
from mem0 import Memory
from benchmarks.judges.llm_judge import judge_answer
import time

REQUIRED_BENCHMARK_FIELDS = ("benchmark_id", "category", "conversation", "query", "expected")


def get_adapter_cls(name: str) -> Type[Any]:
    """Resolve an adapter name to the class ``run_benchmark`` should drive.

    ``"mem0"`` (the default) resolves to ``mem0.Memory`` directly, exactly
    matching the runner's original, un-parametrized behavior. Other names
    resolve to a :class:`~benchmarks.adapters.base.BaseAdapter` subclass.
    Imports are lazy so selecting ``"mem0"`` never requires importing
    adapters (like Haven) that aren't otherwise needed.
    """
    def _baseline(class_name: str) -> Type[Any]:
        return getattr(
            __import__("benchmarks.adapters.baselines", fromlist=[class_name]),
            class_name,
        )

    def _ablation(class_name: str) -> Type[Any]:
        return getattr(
            __import__("benchmarks.adapters.ablations", fromlist=[class_name]),
            class_name,
        )

    registry = {
        "mem0": lambda: Memory,
        "haven": lambda: __import__(
            "benchmarks.adapters.haven_adapter", fromlist=["HavenAdapter"]
        ).HavenAdapter,
        # "haven_retrieval" is the clearer name for what "haven" already is
        # (real MemoryEngine retrieval over pre-built KnowledgeObjects, no
        # Manager AI extraction) now that "haven_full" also exists. Same
        # class, same results -- "haven" is kept as-is so nothing that
        # already depends on that name changes.
        "haven_retrieval": lambda: __import__(
            "benchmarks.adapters.haven_adapter", fromlist=["HavenAdapter"]
        ).HavenAdapter,
        # Haven Full: the real end-to-end system -- Conversation ->
        # Extractor -> Classifier -> ImportanceScorer -> CanonicalMatcher ->
        # KnowledgeUpdater -> VaultWriter -> OntologyPipeline -> MemoryEngine.
        # See benchmarks/adapters/haven_full_adapter.py.
        "haven_full": lambda: __import__(
            "benchmarks.adapters.haven_full_adapter", fromlist=["HavenFullAdapter"]
        ).HavenFullAdapter,
        # Trivial baselines (benchmarks/adapters/baselines.py) — context for
        # every other adapter's numbers; none touch Haven's pipeline.
        "return_all": lambda: _baseline("ReturnAllAdapter"),
        "recency": lambda: _baseline("RecencyAdapter"),
        "bm25": lambda: _baseline("BM25Adapter"),
        "embedding": lambda: _baseline("EmbeddingAdapter"),
        # Haven scoring-contribution ablations (benchmarks/adapters/ablations.py).
        "haven_no_ontology": lambda: _ablation("HavenNoOntologyAdapter"),
        "haven_no_keyword": lambda: _ablation("HavenNoKeywordAdapter"),
        "haven_no_recency": lambda: _ablation("HavenNoRecencyAdapter"),
    }
    try:
        return registry[name]()
    except KeyError:
        raise ValueError(
            f"Unknown adapter {name!r}; choose from {sorted(registry)}"
        ) from None


def load_benchmarks(
    dataset_dir: str, skipped: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Load every benchmark JSON file under *dataset_dir*.

    Files that fail to parse, or that parse but are missing one of
    ``REQUIRED_BENCHMARK_FIELDS`` (the schema ``benchmarks/RUNNER_SPEC.md``
    documents), are skipped rather than raised — one incomplete dataset
    entry must never take down an entire run. *skipped*, when supplied, is
    appended with the path of each skipped file so a caller can report on
    or track coverage gaps; it is optional and unused by default so
    existing callers/tests are unaffected.
    """
    benchmarks = []

    for root, _, files in os.walk(dataset_dir):
        for file in files:
            if file.endswith(".json"):
                file_path = os.path.join(root, file)

                with open(file_path, "r") as f:
                    try:
                        data = json.load(f)
                    except json.JSONDecodeError:
                        print(f"Skipping invalid dataset file: {file_path}")
                        if skipped is not None:
                            skipped.append(file_path)
                        continue

                missing = [key for key in REQUIRED_BENCHMARK_FIELDS if key not in data]
                if missing:
                    print(
                        f"Skipping dataset file missing {missing}: {file_path}"
                    )
                    if skipped is not None:
                        skipped.append(file_path)
                    continue

                benchmarks.append(data)

    return benchmarks


def discover_dataset_dirs(datasets_root: str = "benchmarks/datasets") -> List[str]:
    """Return every dataset category directory under *datasets_root*.

    Replaces a hardcoded subset of category names: previously
    ``main()`` only ever loaded ``decisions``/``beliefs``/``temporal``/
    ``supersession``, silently excluding every other category directory
    added to ``benchmarks/datasets/`` since (e.g. ``contradictions``,
    ``goals``, ``identity``, ``preferences``). Discovering directories at
    run time means a new category starts contributing to runs as soon as
    its directory exists, with no separate wiring step to forget.
    """
    return sorted(
        os.path.join(datasets_root, name)
        for name in os.listdir(datasets_root)
        if os.path.isdir(os.path.join(datasets_root, name))
    )


def _git_commit() -> Optional[str]:
    """Best-effort current commit SHA, or ``None`` outside a git checkout."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def run_benchmark(
    benchmark: Dict[str, Any],
    adapter_cls: Type[Any] = Memory,
    query_rewriter: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run *benchmark* against *adapter_cls*.

    *adapter_cls* must expose the same interface
    :class:`~benchmarks.adapters.base.BaseAdapter` defines
    (``from_config`` / ``delete_all`` / ``add`` / ``search``); it defaults
    to ``mem0.Memory``, matching this function's original, un-parametrized
    behavior exactly.

    *query_rewriter*, when supplied, is handed to *adapter_cls* as a
    ``query_rewriter`` constructor keyword instead of going through
    ``from_config`` (whose *config* dict is mem0-shaped and has no slot for
    it). Only adapters that accept that keyword — currently
    :class:`~benchmarks.adapters.haven_adapter.HavenAdapter` — support this.
    ``None`` (the default) preserves the original
    ``adapter_cls.from_config(config)`` construction path unchanged.
    """
    benchmark_start = time.perf_counter()

    collection_name = f"benchmark_{benchmark['benchmark_id']}"

    config = {
        "embedder": {
            "provider": "fastembed",
            "config": {
                "model": "BAAI/bge-small-en-v1.5"
            }
        },
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": collection_name,
                "embedding_model_dims": 384
            }
        },
        "llm": {
            "provider": "ollama",
            "config": {
                "model": "qwen3:8b"
            }
        }
    }

    if query_rewriter is not None:
        mem0 = adapter_cls(query_rewriter=query_rewriter)
    else:
        mem0 = adapter_cls.from_config(config)

    try:
        mem0.delete_all(filters={"user_id": "user123"})
    except Exception:
        pass
    add_start = time.perf_counter()
    # Add memories. Adapters that expose add_conversation() (see
    # benchmarks/adapters/base.py) get the whole conversation as one unit --
    # required by Haven Full's ManagerPipeline, which operates on a single
    # Conversation rather than isolated messages. Backends with no such
    # method (raw mem0.Memory) keep the original one-add-call-per-entry loop.
    if hasattr(mem0, "add_conversation"):
        mem0.add_conversation(
            benchmark["conversation"], user_id="user123", agent_id="agent456"
        )
    else:
        for memory in benchmark["conversation"]:

            mem0.add(
                messages=[
                    {
                        "role": "user",
                        "content": memory["text"]
                    }
                ],
                user_id="user123",
                agent_id="agent456",
                infer=False,
            )
    add_time = time.perf_counter() - add_start
    search_start = time.perf_counter()
    # Search
    try:

        result = mem0.search(
            query=benchmark["query"],
            filters={"user_id": "user123"},
        )

    except Exception as e:

        return {
            "benchmark_id": benchmark["benchmark_id"],
            "answer_score": 0.0,
            "passed": False,
            "answer": "",
            "retrieved_memories": [],
            "error": str(e),
        }
    search_time = time.perf_counter() - search_start
    answer = " ".join(
        mem["memory"]
        for mem in result.get("results", [])
    )
    judge_start = time.perf_counter()
    try:
        judge = judge_answer(
            benchmark["query"],
            benchmark["expected"],
            answer
        )
    except Exception as e:
        judge = {
            "passed": False,
            "score": 0.0,
            "reason": str(e),
            "failure_type": "JUDGE_ERROR"
        }
    judge_time = time.perf_counter() - judge_start
    answer_score = judge["score"]
    passed = judge["passed"]

    if not passed:
        print("\nFAILED BENCHMARK")
        print("ID:", benchmark["benchmark_id"])
        print("QUERY:", benchmark["query"])
        print("EXPECTED:", benchmark["expected"])
        print("ANSWER:", answer)
        print("-" * 80)
        print("REASON:", judge["reason"])
        print("FAILURE TYPE:", judge["failure_type"])
    total_time = time.perf_counter() - benchmark_start
    return {
        "benchmark_id": benchmark["benchmark_id"],
         "category": benchmark["category"],

        "passed": passed,
        "answer_score": answer_score,

        "query": benchmark["query"],

        "expected": benchmark["expected"],

        "answer": answer,

        "retrieved_memories": result.get("results", []),
        "judge_reason": judge["reason"],
        "failure_type": judge["failure_type"],
        "timings": {
            "add": round(add_time, 3),
            "search": round(search_time, 3),
            "judge": round(judge_time, 3),
            "total": round(total_time, 3),
        }
    }


def save_results(data: Any, output_path: str):

    os.makedirs(
        os.path.dirname(output_path),
        exist_ok=True
    )

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)


def main(adapter_name: str = "mem0", enable_query_rewriter: bool = False):

    adapter_cls = get_adapter_cls(adapter_name)

    query_rewriter = None
    if enable_query_rewriter:
        if adapter_name not in ("haven", "haven_retrieval", "haven_full"):
            raise ValueError(
                "enable_query_rewriter is only supported with adapter_name in "
                "('haven', 'haven_retrieval', 'haven_full') (got %r)" % adapter_name
            )
        from obsidian.memory_engine.query_rewriter import QueryRewriter

        query_rewriter = QueryRewriter()

    datasets = discover_dataset_dirs()

    print("START")
    print("ADAPTER:", adapter_name)

    all_benchmarks = []
    skipped_files: List[str] = []

    for dataset_dir in datasets:

        loaded = load_benchmarks(dataset_dir, skipped=skipped_files)

        print(
            "Loaded from",
            dataset_dir,
            "=",
            len(loaded)
        )

        all_benchmarks.extend(loaded)

    print("\nTotal benchmarks =", len(all_benchmarks))
    if skipped_files:
        print("Skipped", len(skipped_files), "invalid/incomplete dataset files")

    results = []

    for benchmark in all_benchmarks:

        print(
            f"Running {benchmark['benchmark_id']}...",
            end=" "
        )

        result = run_benchmark(
            benchmark, adapter_cls=adapter_cls, query_rewriter=query_rewriter
        )

        print(
            "PASS"
            if result["passed"]
            else "FAIL"
        )

        results.append(result)

    print("\nSaving results")

    output_path = (
        "benchmarks/results/results.json"
        if adapter_name == "mem0"
        else f"benchmarks/results/results_{adapter_name}.json"
    )

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "adapter": adapter_name,
        "git_commit": _git_commit(),
        "query_rewriter_enabled": enable_query_rewriter,
        "dataset_dirs": datasets,
        "total_benchmarks": len(all_benchmarks),
        "skipped_files": skipped_files,
    }

    save_results({"metadata": metadata, "results": results}, output_path)

    passed = sum(
        1 for r in results
        if r["passed"]
    )

    total = len(results)

    print("\n====================================")
    print("PASSED :", passed)
    print("FAILED :", total - passed)
    print("TOTAL  :", total)
    print(
        "PASS % :",
        round(100 * passed / total, 2)
        if total > 0 else 0
    )
    print("====================================")

    print("DONE")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--adapter",
        default="mem0",
        help="Memory system to benchmark (default: mem0).",
    )
    parser.add_argument(
        "--query-rewriter",
        action="store_true",
        help=(
            "Enable QueryRewriter multi-query expansion (haven adapter only). "
            "Disabled by default. Requires QUERY_REWRITER_API_KEY to actually "
            "produce rewrites; fails open (no rewrites) otherwise."
        ),
    )
    args = parser.parse_args()

    main(adapter_name=args.adapter, enable_query_rewriter=args.query_rewriter)