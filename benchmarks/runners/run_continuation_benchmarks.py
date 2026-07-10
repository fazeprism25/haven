"""run_continuation_benchmarks — Phase 1 pilot runner for the continuation
benchmark (``docs/architecture/CONTINUATION_BENCHMARK_DESIGN.md``).

A second, additive pipeline alongside
``benchmarks/runners/run_benchmarks.py`` -- it does not import, call, or
modify that runner's execution path, and no existing dataset category's
results change because of anything in this module. Implements the design's
three-stage pipeline (§5):

    Stage A (Reconstruction) -- ``adapter.build_continuation_context(query)``
    Stage B (Continuation)   -- a fixed, temperature-0 model that has never
                                 seen the raw conversation, given only
                                 Stage A's context plus the query
    Stage C (Judging)        -- ``benchmarks/judges/continuation_judge.py``,
                                 scored against the case's ``ground_truth``/
                                 ``expected`` rubric

Dataset cases live under ``benchmarks/datasets_continuation/`` -- a
separate root from ``benchmarks/datasets/`` (which
``run_benchmarks.discover_dataset_dirs`` walks), specifically so this
dataset's different schema (``queries``/``ground_truth`` instead of a
single ``query`` string) never collides with the existing suite, the same
reasoning ``run_distractor.py`` already applies to its own result
filenames (see ``benchmarks/README.md``).
"""

from __future__ import annotations

import os
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type

from benchmarks.judges.continuation_judge import judge_continuation
from benchmarks.judges.llm_judge import _get_client, _resolve_model
from benchmarks.runners.run_benchmarks import _git_commit, save_results

REQUIRED_CONTINUATION_FIELDS = (
    "benchmark_id",
    "category",
    "conversation",
    "ground_truth",
    "queries",
    "expected",
)

# §5's fixed, adapter-agnostic stage-B system prompt. Deliberately minimal --
# it must not coach the continuation model toward extracting structure from
# messy input, or a bad Stage A context could be rescued by a good Stage B
# prompt, defeating the point of scoring Stage A's output at all.
CONTINUATION_SYSTEM_PROMPT = """
You are an AI assistant resuming work on an ongoing software project.
You have no memory of this project beyond what is provided below.
Do not assume anything not stated or clearly implied by the provided context.
""".strip()

_DEFAULT_ADAPTER_CONFIG = {
    "embedder": {
        "provider": "fastembed",
        "config": {"model": "BAAI/bge-small-en-v1.5"},
    },
    "vector_store": {
        "provider": "qdrant",
        "config": {"collection_name": "continuation_benchmark", "embedding_model_dims": 384},
    },
    "llm": {
        "provider": "ollama",
        "config": {"model": "qwen3:8b"},
    },
}


def load_continuation_benchmarks(
    dataset_dir: str, skipped: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Load every continuation-benchmark JSON file under *dataset_dir*.

    Mirrors ``run_benchmarks.load_benchmarks`` exactly (same skip-not-raise
    behavior for malformed JSON or missing required fields), against
    ``REQUIRED_CONTINUATION_FIELDS`` instead of
    ``run_benchmarks.REQUIRED_BENCHMARK_FIELDS`` -- a continuation case has
    no top-level ``query`` (it has ``queries``, a list) so it would already
    be skipped by the *existing* loader if it were ever discovered there;
    this loader exists so this dataset is loaded on its own terms instead.
    """
    cases: List[Dict[str, Any]] = []

    for root, _, files in os.walk(dataset_dir):
        for file in files:
            if not file.endswith(".json"):
                continue
            file_path = os.path.join(root, file)

            with open(file_path, "r") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError:
                    print(f"Skipping invalid continuation dataset file: {file_path}")
                    if skipped is not None:
                        skipped.append(file_path)
                    continue

            missing = [key for key in REQUIRED_CONTINUATION_FIELDS if key not in data]
            if missing:
                print(f"Skipping continuation dataset file missing {missing}: {file_path}")
                if skipped is not None:
                    skipped.append(file_path)
                continue

            cases.append(data)

    return cases


def discover_continuation_dataset_dirs(
    datasets_root: str = "benchmarks/datasets_continuation",
) -> List[str]:
    """Return every category directory under *datasets_root*.

    Same auto-discovery convention as
    ``run_benchmarks.discover_dataset_dirs``, pointed at the continuation
    dataset's own root so adding a category here never touches
    ``benchmarks/datasets/``.
    """
    if not os.path.isdir(datasets_root):
        return []
    return sorted(
        os.path.join(datasets_root, name)
        for name in os.listdir(datasets_root)
        if os.path.isdir(os.path.join(datasets_root, name))
    )


def _continuation_context(adapter: Any, query: str) -> str:
    """Stage A. Prefers ``adapter.build_continuation_context``; falls back
    to the same flat ``search()`` + join
    :meth:`~benchmarks.adapters.base.BaseAdapter.build_continuation_context`'s
    own default does, for adapters (like raw ``mem0.Memory``) that predate
    :class:`BaseAdapter` entirely and so have no such method at all."""
    builder = getattr(adapter, "build_continuation_context", None)
    if builder is not None:
        return builder(query)
    result = adapter.search(query=query)
    return " ".join(mem["memory"] for mem in result.get("results", []))


def generate_continuation(context: str, query: str, model: Optional[str] = None) -> str:
    """Stage B -- a fixed, temperature-0 model resuming work from *context* alone.

    Reuses ``benchmarks/judges/llm_judge.py``'s Qwen Cloud client/model
    resolution for infrastructure consistency (§5). Its only variable input
    across every adapter and case is *context* -- the prompt template and
    system prompt never change -- so any score difference between adapters
    is attributable to what Stage A handed this call, not to per-adapter
    prompt engineering.
    """
    client = _get_client()
    prompt = f"<context>\n{context}\n</context>\n\n{query}"
    response = client.chat.completions.create(
        model=_resolve_model(model),
        temperature=0,
        messages=[
            {"role": "system", "content": CONTINUATION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    return response.choices[0].message.content or ""


def run_continuation_case(
    case: Dict[str, Any],
    adapter_cls: Type[Any],
    continuation_model: Optional[str] = None,
    judge_model: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one continuation-benchmark case through all three stages.

    Ingestion mirrors ``run_benchmarks.run_benchmark`` exactly (fresh
    adapter, defensive ``delete_all``, ``add_conversation`` when available).
    Every entry in ``case["queries"]`` is then scored independently against
    the same ``ground_truth``/``expected`` (§6: "report both the mean and
    the per-query-phrasing breakdown, never the mean alone") -- the case
    score is their mean, and ``hard_fail`` is true if *any* query hit the
    ``must_not_state``/``forbidden_actions`` ceiling.
    """
    case_start = time.perf_counter()

    adapter = adapter_cls.from_config(_DEFAULT_ADAPTER_CONFIG)
    try:
        adapter.delete_all(filters={"user_id": "user123"})
    except Exception:
        pass

    add_start = time.perf_counter()
    if hasattr(adapter, "add_conversation"):
        adapter.add_conversation(
            case["conversation"], user_id="user123", agent_id="agent456"
        )
    else:
        for turn in case["conversation"]:
            adapter.add(
                messages=[{"role": "user", "content": turn.get("text", "")}],
                user_id="user123",
                agent_id="agent456",
                infer=False,
            )
    add_time = time.perf_counter() - add_start

    query_results: List[Dict[str, Any]] = []
    for query_spec in case["queries"]:
        query_text = query_spec["text"]
        q_start = time.perf_counter()
        try:
            context = _continuation_context(adapter, query_text)
            response = generate_continuation(context, query_text, model=continuation_model)
            judge_result = judge_continuation(
                query_text,
                case["ground_truth"],
                case["expected"],
                response,
                model=judge_model,
            )
        except Exception as e:
            context = ""
            response = ""
            judge_result = {
                "must_state_score": 0.0,
                "must_not_state_violations": [],
                "forbidden_action_violations": [],
                "prioritization_correct": False,
                "coherence_score": 0.0,
                "reason": str(e),
                "failure_type": "JUDGE_ERROR",
                "score": 0.0,
                "passed": False,
            }

        query_results.append(
            {
                "query": query_text,
                "task_mode_hint": query_spec.get("task_mode_hint"),
                "context": context,
                "response": response,
                "score": judge_result.get("score", 0.0),
                "passed": judge_result.get("passed", False),
                "must_state_score": judge_result.get("must_state_score", 0.0),
                "must_not_state_violations": judge_result.get("must_not_state_violations", []),
                "forbidden_action_violations": judge_result.get("forbidden_action_violations", []),
                "prioritization_correct": judge_result.get("prioritization_correct", False),
                "coherence_score": judge_result.get("coherence_score", 0.0),
                "failure_type": judge_result.get("failure_type", "JUDGE_ERROR"),
                "reason": judge_result.get("reason", ""),
                "timing": round(time.perf_counter() - q_start, 3),
            }
        )

    case_score = (
        sum(q["score"] for q in query_results) / len(query_results)
        if query_results
        else 0.0
    )
    hard_fail = any(
        q["must_not_state_violations"] or q["forbidden_action_violations"]
        for q in query_results
    )
    passed = bool(query_results) and all(q["passed"] for q in query_results)

    return {
        "benchmark_id": case["benchmark_id"],
        "category": case["category"],
        "tier": case.get("tier"),
        "domain": case.get("domain"),
        "case_score": round(case_score, 4),
        "passed": passed,
        "hard_fail": hard_fail,
        "queries": query_results,
        "timings": {
            "add": round(add_time, 3),
            "total": round(time.perf_counter() - case_start, 3),
        },
    }


def get_continuation_adapter_cls(adapter_name: str) -> Type[Any]:
    """Resolve *adapter_name* for this runner -- distinct from, and not
    reusing, ``run_benchmarks.get_adapter_cls`` for Haven specifically.

    ``"haven"`` resolves to
    :class:`~benchmarks.adapters.haven_continuation_adapter.HavenContinuationAdapter`
    -- deterministic, ``turn_type``-driven typed ingestion (see
    ``docs/architecture/CONTINUATION_BENCHMARK_INGESTION_DESIGN.md``) --
    instead of ``run_benchmarks.get_adapter_cls``'s ``"haven"`` entry
    (:class:`~benchmarks.adapters.haven_adapter.HavenAdapter`, which stores
    every turn as verbatim ``MemoryType.FACT`` and so never gives
    ``ProjectStateBuilder`` a typed candidate to work with -- that design's
    §4 for why a continuation-only subclass is needed rather than a mode
    flag on ``HavenAdapter`` itself).

    Every other adapter name (``mem0``, ``haven_full``, ``return_all``,
    ``recency``, ``bm25``, ``embedding``, and Haven's ablations) still
    resolves exactly as ``run_benchmarks.get_adapter_cls`` already does --
    none of them read ``turn_type``/``supersedes_turn``/``resolves_turn``,
    so none of them need a continuation-only counterpart. This function is
    used only by this module; ``HavenContinuationAdapter`` is intentionally
    absent from ``run_benchmarks.get_adapter_cls``'s own registry, so it has
    no effect on the base suite's adapter-parity contract.
    """
    if adapter_name == "haven":
        from benchmarks.adapters.haven_continuation_adapter import (
            HavenContinuationAdapter,
        )

        return HavenContinuationAdapter

    from benchmarks.runners.run_benchmarks import get_adapter_cls

    return get_adapter_cls(adapter_name)


def main(adapter_name: str = "haven") -> None:
    adapter_cls = get_continuation_adapter_cls(adapter_name)

    datasets = discover_continuation_dataset_dirs()

    print("START (continuation benchmark)")
    print("ADAPTER:", adapter_name)

    all_cases: List[Dict[str, Any]] = []
    skipped_files: List[str] = []
    for dataset_dir in datasets:
        loaded = load_continuation_benchmarks(dataset_dir, skipped=skipped_files)
        print("Loaded from", dataset_dir, "=", len(loaded))
        all_cases.extend(loaded)

    print("\nTotal continuation cases =", len(all_cases))
    if skipped_files:
        print("Skipped", len(skipped_files), "invalid/incomplete dataset files")

    results = []
    for case in all_cases:
        print(f"Running {case['benchmark_id']}...", end=" ")
        result = run_continuation_case(case, adapter_cls=adapter_cls)
        print("PASS" if result["passed"] else ("HARD_FAIL" if result["hard_fail"] else "FAIL"))
        results.append(result)

    print("\nSaving results")

    output_path = f"benchmarks/results/results_continuation_{adapter_name}.json"

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "adapter": adapter_name,
        "git_commit": _git_commit(),
        "dataset_dirs": datasets,
        "total_cases": len(all_cases),
        "skipped_files": skipped_files,
    }

    save_results({"metadata": metadata, "results": results}, output_path)

    total_queries = sum(len(r["queries"]) for r in results)
    hard_fail_queries = sum(
        1
        for r in results
        for q in r["queries"]
        if q["must_not_state_violations"] or q["forbidden_action_violations"]
    )
    passed = sum(1 for r in results if r["passed"])
    total = len(results)

    print("\n====================================")
    print("PASSED :", passed)
    print("FAILED :", total - passed)
    print("TOTAL  :", total)
    print("PASS % :", round(100 * passed / total, 2) if total > 0 else 0)
    print(
        "HARD-FAIL RATE (queries) :",
        round(100 * hard_fail_queries / total_queries, 2) if total_queries > 0 else 0,
        "%",
    )
    print("====================================")
    print("DONE")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--adapter",
        default="haven",
        help="Memory system to benchmark (default: haven).",
    )
    args = parser.parse_args()

    main(adapter_name=args.adapter)
