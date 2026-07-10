"""CLI entrypoint for the incremental-ingestion benchmark suite.

Usage
-----
    python -m benchmarks.incremental_ingestion.run_benchmarks
    python -m benchmarks.incremental_ingestion.run_benchmarks --quick
    python -m benchmarks.incremental_ingestion.run_benchmarks --categories 1,4,5

Writes ``results/results.json`` (raw, re-plottable data) and
``results/report.md`` (a human-readable Markdown digest of the same
data) into this directory.
"""

from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from benchmarks.incremental_ingestion import scenarios as sc
from benchmarks.incremental_ingestion.metrics import ScenarioResult, write_results
from benchmarks.incremental_ingestion.report import generate_markdown_report

_RESULTS_DIR = Path(__file__).parent / "results"

_CATEGORY_RUNNERS = {
    "1": ("duplicate_remember", lambda quick: sc.duplicate_remember_scenarios(repeats=3 if quick else 5)),
    "2": (
        "growing_conversation",
        lambda quick: sc.growing_conversation_scenarios(increments=[1, 10] if quick else [1, 2, 5, 10]),
    ),
    "3": (
        "long_conversation",
        lambda quick: sc.long_conversation_scenarios(sizes=[25, 100] if quick else [25, 50, 100, 200, 500]),
    ),
    "4": ("context_dependent_updates", lambda quick: sc.context_dependent_update_scenarios()),
    "5": ("failure_cases", lambda quick: sc.failure_case_scenarios()),
}


def _git_commit() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL)
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def run(categories: List[str], quick: bool) -> List[ScenarioResult]:
    results: List[ScenarioResult] = []
    for key in categories:
        name, runner = _CATEGORY_RUNNERS[key]
        print(f"Running category {key} ({name})...")
        category_results = runner(quick)
        results.extend(category_results)
        print(f"  -> {len(category_results)} scenario(s) complete.")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--categories",
        default="1,2,3,4,5",
        help="Comma-separated category numbers to run (default: all, 1-5).",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Reduced sizes/repeats for a fast smoke run (not the full-scale numbers).",
    )
    args = parser.parse_args()
    categories = [c.strip() for c in args.categories.split(",") if c.strip()]

    scenarios = run(categories, args.quick)

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "categories_run": categories,
        "quick_mode": args.quick,
        "total_scenarios": len(scenarios),
    }

    results_path = _RESULTS_DIR / "results.json"
    write_results(scenarios, results_path, metadata)
    print(f"Wrote {results_path}")

    report_path = _RESULTS_DIR / "report.md"
    report_path.write_text(generate_markdown_report(scenarios, metadata), encoding="utf-8")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
