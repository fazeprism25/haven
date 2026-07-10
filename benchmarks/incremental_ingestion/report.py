"""Turns a list of :class:`ScenarioResult` into a readable Markdown report.

The JSON file written by :func:`metrics.write_results` is the source of
truth (raw enough to re-plot later); this module produces a companion,
human-readable digest of the same data -- tables plus the notes each
scenario already recorded.
"""

from __future__ import annotations

from typing import Dict, List

from benchmarks.incremental_ingestion.metrics import ScenarioResult


def _fmt(value, digits: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _by_category(scenarios: List[ScenarioResult]) -> Dict[str, List[ScenarioResult]]:
    grouped: Dict[str, List[ScenarioResult]] = {}
    for scenario in scenarios:
        grouped.setdefault(scenario.category, []).append(scenario)
    return grouped


def _duplicate_remember_section(scenarios: List[ScenarioResult]) -> List[str]:
    lines = ["## 1. Duplicate Remember", ""]
    for scenario in scenarios:
        lines.append(f"**{scenario.scenario_id}** -- {scenario.description}")
        lines.append("")
        lines.append("| pipeline | send | status | llm_calls | elapsed_s |")
        lines.append("|---|---|---|---|---|")
        for r in scenario.requests:
            lines.append(
                f"| {r.pipeline} | {r.label} | {r.response_status} | {r.llm_calls} | {_fmt(r.elapsed_seconds)} |"
            )
        lines.append("")
        for note in scenario.notes:
            lines.append(f"> {note}")
        lines.append("")
    return lines


def _growing_conversation_section(scenarios: List[ScenarioResult]) -> List[str]:
    lines = ["## 2. Growing Conversation", ""]
    lines.append("| scenario | pipeline | growth click turns | growth click chars | growth click tokens (est.) | llm_calls |")
    lines.append("|---|---|---|---|---|---|")
    for scenario in scenarios:
        for r in scenario.requests:
            if "growth" not in r.label:
                continue
            lines.append(
                f"| {scenario.scenario_id} | {r.pipeline} | {r.turn_count_sent} | "
                f"{r.extractor_prompt_chars} | {r.extractor_prompt_tokens_est} | {r.llm_calls} |"
            )
    lines.append("")
    for scenario in scenarios:
        for note in scenario.notes:
            lines.append(f"> [{scenario.scenario_id}] {note}")
    lines.append("")
    return lines


def _long_conversation_section(scenarios: List[ScenarioResult]) -> List[str]:
    lines = ["## 3. Long Conversation", ""]
    lines.append(
        "| total turns | pipeline | final click chars | final click tokens (est.) | "
        "elapsed_s | working_context_s | checkpoint_overhead_s |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for scenario in scenarios:
        by_pipeline: Dict[str, object] = {}
        for r in scenario.requests:
            by_pipeline[r.pipeline] = r  # keep the last (final click) per pipeline
        total_turns = scenario.scenario_id.rsplit("_", 1)[-1]
        for pipeline, r in by_pipeline.items():
            lines.append(
                f"| {total_turns} | {pipeline} | {r.extractor_prompt_chars} | "
                f"{r.extractor_prompt_tokens_est} | {_fmt(r.elapsed_seconds)} | "
                f"{_fmt(r.working_context_seconds)} | {_fmt(r.checkpoint_overhead_seconds)} |"
            )
    lines.append("")
    for scenario in scenarios:
        for note in scenario.notes:
            lines.append(f"> [{scenario.scenario_id}] {note}")
    lines.append("")
    return lines


def _context_dependent_section(scenarios: List[ScenarioResult]) -> List[str]:
    lines = ["## 4. Context-dependent updates", ""]
    lines.append("| scenario | match | missing_in_new | extra_in_new |")
    lines.append("|---|---|---|---|")
    for scenario in scenarios:
        acc = scenario.accuracy
        lines.append(
            f"| {scenario.scenario_id} | {acc.match if acc else '-'} | "
            f"{acc.missing_in_new if acc else '-'} | {acc.extra_in_new if acc else '-'} |"
        )
    lines.append("")
    for scenario in scenarios:
        lines.append(f"**{scenario.scenario_id}** -- {scenario.description}")
        for note in scenario.notes:
            lines.append(f"> {note}")
        lines.append("")
    return lines


def _failure_case_section(scenarios: List[ScenarioResult]) -> List[str]:
    lines = ["## 5. Failure cases", ""]
    for scenario in scenarios:
        lines.append(f"**{scenario.scenario_id}** -- {scenario.description}")
        for note in scenario.notes:
            lines.append(f"> {note}")
        lines.append("")
    return lines


_SECTION_BUILDERS = {
    "1_duplicate_remember": _duplicate_remember_section,
    "2_growing_conversation": _growing_conversation_section,
    "3_long_conversation": _long_conversation_section,
    "4_context_dependent_updates": _context_dependent_section,
    "5_failure_cases": _failure_case_section,
}


def generate_markdown_report(scenarios: List[ScenarioResult], metadata: Dict[str, object]) -> str:
    grouped = _by_category(scenarios)
    lines: List[str] = [
        "# Incremental Ingestion Benchmark Report",
        "",
        f"Generated at: {metadata.get('generated_at', '-')}",
        f"Git commit: {metadata.get('git_commit', '-')}",
        f"Total scenarios: {len(scenarios)}",
        "",
        "See `README.md` in this directory for full methodology, and "
        "`results/results.json` for the raw per-request data behind every "
        "number below.",
        "",
    ]
    for category in sorted(grouped):
        builder = _SECTION_BUILDERS.get(category)
        if builder is not None:
            lines.extend(builder(grouped[category]))
    return "\n".join(lines)
