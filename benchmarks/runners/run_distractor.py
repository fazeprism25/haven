"""Distractor-robustness sweep for any registered benchmark adapter.

The retrieval suite's cases store only their own 1-2 memories, so
retrieval happens over a corpus of ~2 documents — a setting where "return
everything" is nearly optimal and any precision/false-positive number is
close to meaningless. This runner stresses that: before each case's real
conversation, it inserts ``n`` guaranteed-irrelevant memories and sweeps
``n``, so pass rate becomes a function of noise level. A robust retriever
holds its pass rate as ``n`` grows; ``return_all`` should visibly decay.

It changes nothing about scoring or the adapters. It reuses
:func:`benchmarks.runners.run_benchmarks.run_benchmark` **verbatim** by
prepending distractor turns to a *copy* of each benchmark's
``conversation`` list — which ``run_benchmark`` already iterates and
inserts one by one. Prepending (not appending) keeps every case's real
memories the most-recent ones, so the sweep isolates the noise variable
and does not confound the recency-based adapters.

Determinism
-----------
Distractors come from a fixed :class:`random.Random` seed over a
topic-neutral vocabulary (gardening / cooking / weather) chosen to be
disjoint from the tech-focused dataset — so a distractor can never
accidentally match a query or a ``must_not_contain`` string, and the exact
same distractor set is produced on every run for a given ``(n, seed)``.

Output
------
Writes ``benchmarks/results/distractor_sweep_<adapter>.json`` (the raw
per-``n`` aggregates, re-plottable) and a Markdown digest alongside it.
The filename deliberately does **not** match the ``results*.json`` glob
that :mod:`benchmarks.analysis.classify_failure` reads, so this suite's
different schema never collides with the standard per-case report.
"""

from __future__ import annotations

import argparse
import json
import os
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from benchmarks.runners.run_benchmarks import (
    _git_commit,
    discover_dataset_dirs,
    get_adapter_cls,
    load_benchmarks,
    run_benchmark,
)

DEFAULT_COUNTS = (0, 10, 50, 200)
DEFAULT_SEED = 20260706

# Topic-neutral vocabulary, disjoint from the tech dataset so a generated
# distractor cannot collide with any query or expected/forbidden string.
_SUBJECTS = (
    "The gardener", "My neighbour", "The baker", "A hiker", "The florist",
    "My cousin", "The beekeeper", "A fisherman", "The chef", "The postman",
)
_VERBS = (
    "planted", "watered", "harvested", "baked", "seasoned",
    "wrapped", "delivered", "arranged", "sketched", "brewed",
)
_OBJECTS = (
    "tomatoes in the greenhouse", "a loaf of sourdough", "the rose bushes",
    "a pot of soup", "the maple syrup", "a basket of apples",
    "the herb garden", "a jar of honey", "the picnic blanket",
    "a tray of muffins",
)
_TIMES = (
    "on Monday morning", "before the rain", "last weekend", "at dawn",
    "after lunch", "during the festival", "in the afternoon", "at sunset",
    "over the holidays", "just after breakfast",
)


def generate_distractors(n: int, seed: int = DEFAULT_SEED) -> List[Dict[str, str]]:
    """Return *n* deterministic, guaranteed-irrelevant conversation entries.

    Each entry is a ``{"speaker": "user", "text": ...}`` dict in the same
    shape the dataset uses. The same ``(n, seed)`` always yields the same
    list; larger ``n`` is a superset-in-distribution (not a literal prefix)
    of smaller ``n`` since each item is drawn independently from the seeded
    stream.
    """
    if n < 0:
        raise ValueError(f"n must be >= 0; got {n}")
    rng = random.Random(seed)
    entries: List[Dict[str, str]] = []
    for index in range(n):
        text = (
            f"{rng.choice(_SUBJECTS)} {rng.choice(_VERBS)} "
            f"{rng.choice(_OBJECTS)} {rng.choice(_TIMES)}."
        )
        # A stable index keeps every distractor distinct even on a vocab collision.
        entries.append({"speaker": "user", "text": f"Note {index + 1}: {text}"})
    return entries


def inject_distractors(
    benchmark: Dict[str, Any], distractors: List[Dict[str, str]]
) -> Dict[str, Any]:
    """Return a shallow copy of *benchmark* with *distractors* prepended.

    The original dict (and its ``conversation`` list) is never mutated, so
    the same loaded benchmark can be reused across every ``n`` in a sweep.
    """
    injected = dict(benchmark)
    injected["conversation"] = list(distractors) + list(benchmark["conversation"])
    return injected


def _aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    by_category: Dict[str, List[int]] = {}
    for r in results:
        bucket = by_category.setdefault(r.get("category", "unknown"), [0, 0])
        bucket[1] += 1
        if r.get("passed"):
            bucket[0] += 1
    return {
        "total": total,
        "passed": passed,
        "pass_rate": (passed / total) if total else 0.0,
        "by_category": {k: {"passed": v[0], "total": v[1]} for k, v in sorted(by_category.items())},
    }


def run_sweep(
    adapter_name: str,
    counts: Optional[List[int]] = None,
    seed: int = DEFAULT_SEED,
    limit: Optional[int] = None,
    categories: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run the full benchmark set at each distractor count and aggregate.

    Parameters
    ----------
    adapter_name : str
        Any name registered in ``run_benchmarks.get_adapter_cls``.
    counts : list[int], optional
        Distractor counts to sweep. Defaults to :data:`DEFAULT_COUNTS`.
    seed : int
        Seed for :func:`generate_distractors`.
    limit : int, optional
        Cap the number of benchmarks (useful for a smoke run — a full
        sweep is ``len(counts) * len(benchmarks)`` judge calls).
    categories : list[str], optional
        If given, only benchmarks whose ``category`` is in this list run.
    """
    counts = list(counts) if counts is not None else list(DEFAULT_COUNTS)
    adapter_cls = get_adapter_cls(adapter_name)

    all_benchmarks: List[Dict[str, Any]] = []
    for dataset_dir in discover_dataset_dirs():
        all_benchmarks.extend(load_benchmarks(dataset_dir))
    if categories is not None:
        wanted = set(categories)
        all_benchmarks = [b for b in all_benchmarks if b.get("category") in wanted]
    if limit is not None:
        all_benchmarks = all_benchmarks[:limit]

    sweep: List[Dict[str, Any]] = []
    for n in counts:
        distractors = generate_distractors(n, seed=seed)
        results: List[Dict[str, Any]] = []
        for benchmark in all_benchmarks:
            print(f"[n={n}] {benchmark['benchmark_id']}...", end=" ")
            result = run_benchmark(inject_distractors(benchmark, distractors), adapter_cls=adapter_cls)
            print("PASS" if result["passed"] else "FAIL")
            results.append(result)
        aggregate = _aggregate(results)
        aggregate["distractors"] = n
        sweep.append(aggregate)
        print(f"\n[n={n}] pass rate = {aggregate['pass_rate']:.1%} ({aggregate['passed']}/{aggregate['total']})\n")

    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "adapter": adapter_name,
            "git_commit": _git_commit(),
            "seed": seed,
            "counts": counts,
            "total_benchmarks": len(all_benchmarks),
            "categories": categories,
        },
        "sweep": sweep,
    }


def render_report(data: Dict[str, Any]) -> str:
    """Render a Markdown digest of a :func:`run_sweep` result."""
    meta = data["metadata"]
    lines = ["# Distractor Robustness Sweep", ""]
    lines.append(f"Adapter: `{meta['adapter']}` · seed: {meta['seed']} · "
                 f"benchmarks: {meta['total_benchmarks']} · commit: {(meta.get('git_commit') or 'unknown')[:12]}")
    lines.append("")
    lines.append("| Distractors | Passed | Total | Pass Rate |")
    lines.append("|---|---|---|---|")
    for row in data["sweep"]:
        lines.append(f"| {row['distractors']} | {row['passed']} | {row['total']} | {row['pass_rate']:.1%} |")
    lines.append("")
    lines.append("A retriever robust to noise holds its pass rate as the distractor "
                 "count grows; a decaying row is precision lost to irrelevant memories.")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", default="haven", help="Registered adapter name (default: haven).")
    parser.add_argument(
        "--counts",
        default=",".join(str(c) for c in DEFAULT_COUNTS),
        help="Comma-separated distractor counts (default: %(default)s).",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Distractor RNG seed.")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of benchmarks (smoke runs).")
    parser.add_argument(
        "--categories",
        default=None,
        help="Comma-separated category filter (default: all categories).",
    )
    args = parser.parse_args()

    counts = [int(c) for c in args.counts.split(",") if c.strip()]
    categories = [c.strip() for c in args.categories.split(",")] if args.categories else None

    data = run_sweep(
        args.adapter, counts=counts, seed=args.seed, limit=args.limit, categories=categories
    )

    base = f"benchmarks/results/distractor_sweep_{args.adapter}"
    os.makedirs("benchmarks/results", exist_ok=True)
    with open(f"{base}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    with open(f"{base}.md", "w", encoding="utf-8") as f:
        f.write(render_report(data))

    print(render_report(data))
    print(f"Wrote {base}.json and {base}.md")


if __name__ == "__main__":
    main()
