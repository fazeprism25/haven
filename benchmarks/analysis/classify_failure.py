"""Per-result and aggregate analysis of benchmark runs.

``classify_failure()`` is a coarse, judge-independent cross-check (did
anything even get retrieved?) for a single result. ``generate_report()``
aggregates one or more ``benchmarks/results/results_*.json`` files into a
single markdown report — the reproducible replacement for hand-authored
numbers like the ones in ``benchmarks/results/final_report.md``, which
cite metrics ("precision 0.301 -> 0.679") that don't trace back to any
result file in this repo. Run as a script to regenerate that report:

    python -m benchmarks.analysis.classify_failure
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

# Dataset authoring predates a consistent category-naming convention, so
# the same logical category shows up under several spellings across
# benchmarks/datasets/{decisions,beliefs,temporal}/*.json. Newer
# categories (contradictions, goals, identity, preferences, supersession)
# already use one lowercase-plural name consistently and need no entry
# here; add one only if a dataset file's "category" value doesn't match
# its intended grouping.
CATEGORY_ALIASES = {
    "decision": "decisions",
    "Decisions": "decisions",
    "decision_consistency": "decisions",
    "belief": "beliefs",
    "Beliefs": "beliefs",
    "belief_evolution": "beliefs",
    "Temporal": "temporal",
}


def classify_failure(result: Dict[str, Any]) -> str:
    """Classify why a benchmark failed, independent of the judge's own
    ``failure_type``. Distinguishes "nothing was retrieved" (a retrieval
    problem) from "something was retrieved but the judge rejected it" (a
    scoring/content problem) — useful for spotting when a benchmark's own
    query/dataset never resolves anything, as opposed to a real answer
    quality gap.
    """

    if result["passed"]:
        return "PASS"

    if len(result.get("retrieved_memories", [])) == 0:
        return "NO_RETRIEVAL"

    return "INCORRECT_ANSWER"


def normalize_category(raw: str) -> str:
    """Map a dataset file's raw ``category`` value to its canonical bucket."""
    return CATEGORY_ALIASES.get(raw, raw)


def load_result_file(path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Return ``(metadata, results)`` for one results file.

    Supports both the current ``{"metadata": {...}, "results": [...]}``
    shape (see ``benchmarks/runners/run_benchmarks.py:save_results``) and
    the legacy flat-list shape older committed result files predate that
    schema and still use — those have no metadata, so this returns an
    empty metadata dict for them rather than failing.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return {}, data
    return data.get("metadata", {}), data.get("results", [])


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate one run's results into pass-rate, category, and failure stats."""
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))

    by_category: Dict[str, List[int]] = defaultdict(lambda: [0, 0])
    failure_types: Counter = Counter()
    heuristic_types: Counter = Counter()

    for r in results:
        category = normalize_category(r.get("category", "unknown"))
        by_category[category][1] += 1
        if r.get("passed"):
            by_category[category][0] += 1
        else:
            failure_types[r.get("failure_type", "UNKNOWN")] += 1
            heuristic_types[classify_failure(r)] += 1

    return {
        "total": total,
        "passed": passed,
        "pass_rate": (passed / total) if total else 0.0,
        "by_category": dict(sorted(by_category.items())),
        "failure_types": dict(failure_types.most_common()),
        "heuristic_failure_types": dict(heuristic_types.most_common()),
    }


def _label_for(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def _worst_failures(results: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    return [r for r in results if not r.get("passed")][:limit]


def generate_report(result_paths: List[str], output_path: Optional[str] = None) -> str:
    """Build a markdown report comparing one or more result files.

    Each result file becomes one column in the summary/category tables
    and one section in the failure-type/sample-failure breakdowns, so
    running this against both ``results.json`` (mem0) and
    ``results_haven.json`` (Haven) produces a direct, regeneratable
    comparison instead of a hand-copied one.
    """
    runs = [
        (path, *load_result_file(path), )
        for path in result_paths
    ]
    runs_with_summary = [
        (path, metadata, summarize(results), results)
        for path, metadata, results in runs
    ]

    lines: List[str] = ["# Benchmark Report", ""]
    lines.append("Sources: " + ", ".join(result_paths))
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Run | Adapter | Commit | Generated At | Total | Passed | Pass Rate |")
    lines.append("|---|---|---|---|---|---|---|")
    for path, metadata, summary, _ in runs_with_summary:
        adapter = metadata.get("adapter", "unknown")
        commit = (metadata.get("git_commit") or "unknown")
        commit = commit[:12] if commit != "unknown" else commit
        generated_at = metadata.get("generated_at", "unknown (legacy file, no metadata)")
        lines.append(
            f"| {_label_for(path)} | {adapter} | {commit} | {generated_at} | "
            f"{summary['total']} | {summary['passed']} | {summary['pass_rate']:.1%} |"
        )
    lines.append("")

    lines.append("## Pass Rate by Category")
    lines.append("")
    all_categories = sorted(
        {cat for _, _, summary, _ in runs_with_summary for cat in summary["by_category"]}
    )
    lines.append("| Category | " + " | ".join(_label_for(p) for p, _, _, _ in runs_with_summary) + " |")
    lines.append("|" + "---|" * (len(runs_with_summary) + 1))
    for category in all_categories:
        row = [category]
        for _, _, summary, _ in runs_with_summary:
            passed, total = summary["by_category"].get(category, (0, 0))
            row.append(f"{passed}/{total}" if total else "-")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## Failure Types")
    lines.append("")
    for path, _, summary, _ in runs_with_summary:
        lines.append(f"### {_label_for(path)}")
        if not summary["failure_types"]:
            lines.append("No failures.")
        else:
            for failure_type, count in summary["failure_types"].items():
                lines.append(f"- {failure_type}: {count}")
        lines.append("")

    lines.append("## Sample Failures")
    lines.append("")
    for path, _, _, results in runs_with_summary:
        lines.append(f"### {_label_for(path)}")
        worst = _worst_failures(results)
        if not worst:
            lines.append("No failures.")
        for r in worst:
            lines.append(
                f"- **{r.get('benchmark_id')}** ({r.get('failure_type', 'UNKNOWN')}): "
                f"{r.get('judge_reason', '')}"
            )
        lines.append("")

    if any(not metadata for _, metadata, _, _ in runs_with_summary):
        lines.append(
            "> Note: one or more result files predate run metadata (legacy "
            "flat-list schema) — freshness relative to the current codebase "
            "cannot be confirmed for those runs."
        )
        lines.append("")

    report = "\n".join(lines)

    if output_path:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(report)

    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a markdown report from benchmark result files."
    )
    parser.add_argument(
        "--results",
        nargs="+",
        default=sorted(glob.glob("benchmarks/results/results*.json")),
        help="Path(s) to results_*.json files (default: all under benchmarks/results/).",
    )
    parser.add_argument(
        "--output",
        default="benchmarks/reports/latest.md",
        help="Where to write the generated markdown report.",
    )
    args = parser.parse_args()
    print(generate_report(args.results, args.output))
