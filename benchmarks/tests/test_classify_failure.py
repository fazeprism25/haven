"""Tests for :mod:`benchmarks.analysis.classify_failure`.

Covers the per-result heuristic classifier plus the aggregate report
generator (category normalization, legacy-file handling, and the
markdown it produces from one or more results_*.json-shaped files).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from benchmarks.analysis import classify_failure as analysis


def _result(**overrides: Any) -> Dict[str, Any]:
    base = {
        "benchmark_id": "x",
        "category": "decisions",
        "passed": True,
        "failure_type": "NONE",
        "judge_reason": "ok",
        "retrieved_memories": [{"memory": "hi"}],
    }
    base.update(overrides)
    return base


class TestClassifyFailure:
    def test_passed_result_is_pass(self) -> None:
        assert analysis.classify_failure(_result(passed=True)) == "PASS"

    def test_failed_with_no_retrieval_is_no_retrieval(self) -> None:
        result = _result(passed=False, retrieved_memories=[])
        assert analysis.classify_failure(result) == "NO_RETRIEVAL"

    def test_failed_with_retrieval_is_incorrect_answer(self) -> None:
        result = _result(passed=False, retrieved_memories=[{"memory": "hi"}])
        assert analysis.classify_failure(result) == "INCORRECT_ANSWER"


class TestNormalizeCategory:
    def test_known_alias_maps_to_canonical_name(self) -> None:
        assert analysis.normalize_category("decision_consistency") == "decisions"
        assert analysis.normalize_category("Decisions") == "decisions"
        assert analysis.normalize_category("belief_evolution") == "beliefs"
        assert analysis.normalize_category("Temporal") == "temporal"

    def test_unknown_category_passes_through_unchanged(self) -> None:
        assert analysis.normalize_category("goals") == "goals"
        assert analysis.normalize_category("something_new") == "something_new"


class TestLoadResultFile:
    def test_current_shape_returns_metadata_and_results(self, tmp_path: Path) -> None:
        path = tmp_path / "results_haven.json"
        payload = {"metadata": {"adapter": "haven"}, "results": [_result()]}
        path.write_text(json.dumps(payload), encoding="utf-8")

        metadata, results = analysis.load_result_file(str(path))

        assert metadata == {"adapter": "haven"}
        assert results == [_result()]

    def test_legacy_flat_list_returns_empty_metadata(self, tmp_path: Path) -> None:
        path = tmp_path / "results.json"
        path.write_text(json.dumps([_result()]), encoding="utf-8")

        metadata, results = analysis.load_result_file(str(path))

        assert metadata == {}
        assert results == [_result()]


class TestSummarize:
    def test_counts_pass_and_fail(self) -> None:
        results = [_result(passed=True), _result(passed=False, retrieved_memories=[])]
        summary = analysis.summarize(results)

        assert summary["total"] == 2
        assert summary["passed"] == 1
        assert summary["pass_rate"] == 0.5

    def test_empty_results_has_zero_pass_rate(self) -> None:
        summary = analysis.summarize([])
        assert summary["total"] == 0
        assert summary["pass_rate"] == 0.0

    def test_by_category_uses_normalized_names(self) -> None:
        results = [
            _result(category="decision_consistency", passed=True),
            _result(category="Decisions", passed=False, retrieved_memories=[]),
        ]
        summary = analysis.summarize(results)

        assert summary["by_category"] == {"decisions": [1, 2]}

    def test_failure_types_tally_only_failures(self) -> None:
        results = [
            _result(passed=True, failure_type="NONE"),
            _result(passed=False, failure_type="SUPERSESSION", retrieved_memories=[]),
            _result(passed=False, failure_type="SUPERSESSION", retrieved_memories=[]),
        ]
        summary = analysis.summarize(results)

        assert summary["failure_types"] == {"SUPERSESSION": 2}


class TestGenerateReport:
    def test_report_includes_each_source_and_pass_counts(self, tmp_path: Path) -> None:
        mem0_path = tmp_path / "results.json"
        mem0_path.write_text(json.dumps([_result(passed=True)]), encoding="utf-8")

        haven_path = tmp_path / "results_haven.json"
        haven_payload = {
            "metadata": {"adapter": "haven", "git_commit": "abc123def456", "generated_at": "2026-01-01"},
            "results": [_result(passed=False, retrieved_memories=[])],
        }
        haven_path.write_text(json.dumps(haven_payload), encoding="utf-8")

        report = analysis.generate_report([str(mem0_path), str(haven_path)])

        assert "results" in report
        assert "results_haven" in report
        assert "abc123def456"[:12] in report
        assert "SUPERSESSION" not in report  # no such failure in this fixture

    def test_legacy_file_triggers_staleness_note(self, tmp_path: Path) -> None:
        path = tmp_path / "results.json"
        path.write_text(json.dumps([_result()]), encoding="utf-8")

        report = analysis.generate_report([str(path)])

        assert "predate run metadata" in report

    def test_no_legacy_file_omits_staleness_note(self, tmp_path: Path) -> None:
        path = tmp_path / "results_haven.json"
        payload = {"metadata": {"adapter": "haven"}, "results": [_result()]}
        path.write_text(json.dumps(payload), encoding="utf-8")

        report = analysis.generate_report([str(path)])

        assert "predate run metadata" not in report

    def test_writes_to_output_path_when_given(self, tmp_path: Path) -> None:
        path = tmp_path / "results_haven.json"
        payload = {"metadata": {"adapter": "haven"}, "results": [_result()]}
        path.write_text(json.dumps(payload), encoding="utf-8")

        output_path = tmp_path / "reports" / "latest.md"
        report = analysis.generate_report([str(path)], output_path=str(output_path))

        assert output_path.read_text(encoding="utf-8") == report

    def test_sample_failures_section_lists_failing_benchmark_ids(self, tmp_path: Path) -> None:
        path = tmp_path / "results_haven.json"
        payload = {
            "metadata": {"adapter": "haven"},
            "results": [
                _result(
                    benchmark_id="supersession_basic_001",
                    passed=False,
                    failure_type="SUPERSESSION",
                    judge_reason="stale memory surfaced",
                    retrieved_memories=[{"memory": "old"}],
                )
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

        report = analysis.generate_report([str(path)])

        assert "supersession_basic_001" in report
        assert "stale memory surfaced" in report
