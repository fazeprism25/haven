"""End-to-end: the Benchmark Explorer -- loads, filters, exposes a detail
page per case, and reports missing-data states honestly rather than
crashing or fabricating.

Scenario 6 of the Haven end-to-end suite. Drives the real
``GET /api/v1/dashboard/benchmarks`` (list) and
``GET /api/v1/dashboard/benchmarks/{id}`` (detail) routes -- see
``obsidian/server/benchmark_explorer.py`` -- through ``TestClient``.

``benchmark_explorer`` reads two fixed, real repo directories
(``benchmarks/results/results*.json`` and ``benchmarks/datasets*/**/*.json``)
with no injectable env var (unlike the vault directories every other server
test isolates via ``HAVEN_VAULT_DIR``). ``test_e2e_universal_why.py``
already exercises this module against the repo's own real, committed
artifacts (the most "real FastAPI server" way to test it). This file instead
monkeypatches the module's three path constants to a ``tmp_path`` sandbox --
the same kind of test-only substitution ``test_dashboard.py`` already uses
for ``MemoryEngine.query_working_context`` -- so filtering, pagination-sized
result shape, and missing-data edge cases (orphan results, missing dataset
files, corrupt JSON, "not yet run" cases) can be asserted deterministically
without depending on, or risking flakiness from, the real repo's benchmark
history changing over time.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import obsidian.server.benchmark_explorer as benchmark_explorer


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))

    from obsidian.server.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect benchmark_explorer's fixed on-disk locations to an isolated
    sandbox under tmp_path, so each test controls exactly what's "on disk"."""
    results_dir = tmp_path / "bench_results"
    base_datasets_dir = tmp_path / "bench_datasets"
    continuation_datasets_dir = tmp_path / "bench_datasets_continuation"
    results_dir.mkdir()
    base_datasets_dir.mkdir()
    continuation_datasets_dir.mkdir()

    # _REPO_ROOT is also patched: _walk_dataset_cases records each dataset
    # case's source file as a path *relative to* _REPO_ROOT, which raises
    # ValueError if the sandbox lives outside the real repo tree (as
    # tmp_path always does).
    monkeypatch.setattr(benchmark_explorer, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        benchmark_explorer, "_RESULTS_GLOB", str(results_dir / "results*.json")
    )
    monkeypatch.setattr(benchmark_explorer, "_BASE_DATASETS_DIR", base_datasets_dir)
    monkeypatch.setattr(
        benchmark_explorer, "_CONTINUATION_DATASETS_DIR", continuation_datasets_dir
    )
    return tmp_path


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _seed_two_base_cases(sandbox: Path) -> None:
    """One passing (adapter 'mem0', via bare results.json), one failing
    (adapter 'haven', via results_haven.json) -- the two real filename
    conventions ``_adapter_from_filename`` documents."""
    _write_json(
        sandbox / "bench_datasets" / "identity" / "case_001.json",
        {
            "benchmark_id": "identity_case_001",
            "category": "identity",
            "conversation": [{"speaker": "user", "text": "My name is Sam."}],
            "query": "What is my name?",
            "expected": {"answer_contains": ["Sam"]},
        },
    )
    _write_json(
        sandbox / "bench_results" / "results.json",
        [
            {
                "benchmark_id": "identity_case_001",
                "category": "identity",
                "passed": True,
                "answer_score": 0.95,
                "query": "What is my name?",
                "expected": {"answer_contains": ["Sam"]},
                "answer": "Your name is Sam.",
                "judge_reason": "Correctly recalled the name.",
                "failure_type": None,
            }
        ],
    )

    _write_json(
        sandbox / "bench_datasets" / "identity" / "case_002.json",
        {
            "benchmark_id": "identity_case_002",
            "category": "identity",
            "conversation": [{"speaker": "user", "text": "My favourite colour is teal."}],
            "query": "What is my favourite colour?",
            "expected": {"answer_contains": ["teal"]},
        },
    )
    _write_json(
        sandbox / "bench_results" / "results_haven.json",
        {
            "metadata": {"adapter": "haven"},
            "results": [
                {
                    "benchmark_id": "identity_case_002",
                    "category": "identity",
                    "passed": False,
                    "answer_score": 0.1,
                    "query": "What is my favourite colour?",
                    "expected": {"answer_contains": ["teal"]},
                    "answer": "I don't know.",
                    "judge_reason": "Failed to recall the stated colour.",
                    "failure_type": "retrieval_miss",
                }
            ],
        },
    )


class TestBenchmarkExplorerLoads:
    def test_empty_sandbox_loads_with_zero_rows_not_an_error(
        self, client: TestClient, sandbox: Path
    ) -> None:
        response = client.get("/api/v1/dashboard/benchmarks")
        assert response.status_code == 200
        body = response.json()
        assert body["rows"] == []
        assert body["total"] == 0
        assert body["total_filtered"] == 0
        assert body["facets"] == {
            "categories": [],
            "adapters": [],
            "kinds": [],
            "failure_types": [],
        }

    def test_seeded_cases_load_with_correct_facets(
        self, client: TestClient, sandbox: Path
    ) -> None:
        _seed_two_base_cases(sandbox)

        body = client.get("/api/v1/dashboard/benchmarks").json()
        assert body["total"] == 2
        assert body["total_filtered"] == 2
        assert set(body["facets"]["adapters"]) == {"mem0", "haven"}
        assert body["facets"]["categories"] == ["identity"]
        assert body["facets"]["kinds"] == ["base"]
        assert body["facets"]["failure_types"] == ["retrieval_miss"]

    def test_list_rows_are_lightweight_summaries_suitable_for_a_big_table(
        self, client: TestClient, sandbox: Path
    ) -> None:
        _seed_two_base_cases(sandbox)

        rows = client.get("/api/v1/dashboard/benchmarks").json()["rows"]
        for row in rows:
            # The list view must never ship the heavy per-case payload
            # (conversation/answer/per_query) -- that's fetched only on
            # expand via the detail route (see _to_summary's docstring).
            assert "conversation" not in row
            assert "answer" not in row
            assert set(row.keys()) == {
                "benchmark_id",
                "adapter",
                "kind",
                "category",
                "has_result",
                "passed",
                "score",
                "failure_classification",
            }


class TestBenchmarkExplorerFiltering:
    def test_filter_by_category(self, client: TestClient, sandbox: Path) -> None:
        _seed_two_base_cases(sandbox)
        body = client.get(
            "/api/v1/dashboard/benchmarks", params={"category": "identity"}
        ).json()
        assert body["total_filtered"] == 2

        body = client.get(
            "/api/v1/dashboard/benchmarks", params={"category": "nope"}
        ).json()
        assert body["total_filtered"] == 0
        # total (unfiltered) is unaffected by the filter.
        assert body["total"] == 2

    def test_filter_by_adapter(self, client: TestClient, sandbox: Path) -> None:
        _seed_two_base_cases(sandbox)
        body = client.get(
            "/api/v1/dashboard/benchmarks", params={"adapter": "haven"}
        ).json()
        assert body["total_filtered"] == 1
        assert body["rows"][0]["benchmark_id"] == "identity_case_002"

    def test_filter_by_passed(self, client: TestClient, sandbox: Path) -> None:
        _seed_two_base_cases(sandbox)
        passed = client.get(
            "/api/v1/dashboard/benchmarks", params={"passed": "true"}
        ).json()
        assert passed["total_filtered"] == 1
        assert passed["rows"][0]["benchmark_id"] == "identity_case_001"

        failed = client.get(
            "/api/v1/dashboard/benchmarks", params={"passed": "false"}
        ).json()
        assert failed["total_filtered"] == 1
        assert failed["rows"][0]["benchmark_id"] == "identity_case_002"

    def test_filter_by_failure_type(self, client: TestClient, sandbox: Path) -> None:
        _seed_two_base_cases(sandbox)
        body = client.get(
            "/api/v1/dashboard/benchmarks", params={"failure_type": "retrieval_miss"}
        ).json()
        assert body["total_filtered"] == 1
        assert body["rows"][0]["benchmark_id"] == "identity_case_002"

    def test_combined_filters_narrow_further_than_either_alone(
        self, client: TestClient, sandbox: Path
    ) -> None:
        _seed_two_base_cases(sandbox)
        body = client.get(
            "/api/v1/dashboard/benchmarks",
            params={"adapter": "haven", "passed": "true"},
        ).json()
        # "haven" adapter's only case failed -- combining with passed=true
        # yields nothing, proving filters AND together rather than OR.
        assert body["total_filtered"] == 0

    def test_dashboard_html_implements_client_side_pagination_over_this_list(
        self, client: TestClient
    ) -> None:
        """The Explorer has no server-side page/limit params (see
        ``list_benchmarks``'s signature) -- pagination is client-side over
        the full row list. Confirm the served page still wires that up."""
        html = client.get("/dashboard").text
        assert 'id="benchmark-pagination"' in html
        assert "function renderBenchmarkPagination" in html or "benchmark-pagination" in html


class TestBenchmarkExplorerDetailPage:
    def test_detail_for_a_passing_case(self, client: TestClient, sandbox: Path) -> None:
        _seed_two_base_cases(sandbox)
        detail = client.get(
            "/api/v1/dashboard/benchmarks/identity_case_001",
            params={"adapter": "mem0", "kind": "base"},
        ).json()
        assert detail["has_result"] is True
        assert detail["judge_result"] == {"passed": True, "score": 0.95}
        assert detail["conversation"] == [{"speaker": "user", "text": "My name is Sam."}]

    def test_detail_requires_the_exact_adapter_and_kind_combination(
        self, client: TestClient, sandbox: Path
    ) -> None:
        _seed_two_base_cases(sandbox)
        # Right benchmark_id, wrong adapter -> 404, not a silent fallback to
        # whichever adapter happens to match.
        response = client.get(
            "/api/v1/dashboard/benchmarks/identity_case_001",
            params={"adapter": "haven", "kind": "base"},
        )
        assert response.status_code == 404


class TestMissingDataStates:
    def test_dataset_case_with_no_result_yet_is_still_browsable(
        self, client: TestClient, sandbox: Path
    ) -> None:
        """A continuation dataset case authored but never run shows up
        honestly as "not yet run" rather than being hidden."""
        _write_json(
            sandbox / "bench_datasets_continuation" / "pilot" / "case_010.json",
            {
                "benchmark_id": "continuation_case_010",
                "category": "long_horizon",
                "queries": [{"query": "What did I decide?"}],
                "ground_truth": "Some ground truth.",
            },
        )

        body = client.get("/api/v1/dashboard/benchmarks").json()
        row = next(r for r in body["rows"] if r["benchmark_id"] == "continuation_case_010")
        assert row["has_result"] is False
        assert row["adapter"] == "(none — not yet run)"
        assert row["passed"] is None

        detail = client.get(
            "/api/v1/dashboard/benchmarks/continuation_case_010",
            params={"adapter": "(none — not yet run)", "kind": "continuation"},
        ).json()
        assert detail["judge_result"] is None
        assert detail["ground_truth"] == "Some ground truth."

    def test_orphan_result_with_no_matching_dataset_file_still_loads(
        self, client: TestClient, sandbox: Path
    ) -> None:
        """A result row whose benchmark_id has no dataset case on disk
        (e.g. the dataset file was renamed/deleted after the run) must not
        crash the Explorer -- it loads with dataset-only fields absent."""
        _write_json(
            sandbox / "bench_results" / "results.json",
            [
                {
                    "benchmark_id": "orphaned_case_999",
                    "category": "identity",
                    "passed": True,
                    "answer_score": 0.8,
                    "query": "orphan query",
                    "expected": {},
                    "answer": "orphan answer",
                }
            ],
        )
        body = client.get("/api/v1/dashboard/benchmarks").json()
        assert body["total"] == 1

        detail = client.get(
            "/api/v1/dashboard/benchmarks/orphaned_case_999",
            params={"adapter": "mem0", "kind": "base"},
        ).json()
        assert detail["source_dataset_file"] is None
        assert detail["conversation"] is None
        # Fields the result row itself carries are still populated.
        assert detail["query"] == "orphan query"
        assert detail["answer"] == "orphan answer"

    def test_corrupt_result_file_is_skipped_not_a_500(
        self, client: TestClient, sandbox: Path
    ) -> None:
        _seed_two_base_cases(sandbox)
        (sandbox / "bench_results" / "results_broken.json").write_text(
            "{not valid json", encoding="utf-8"
        )

        response = client.get("/api/v1/dashboard/benchmarks")
        assert response.status_code == 200
        assert response.json()["total"] == 2

    def test_fields_never_captured_by_any_artifact_are_explicitly_null(
        self, client: TestClient, sandbox: Path
    ) -> None:
        _seed_two_base_cases(sandbox)
        detail = client.get(
            "/api/v1/dashboard/benchmarks/identity_case_001",
            params={"adapter": "mem0", "kind": "base"},
        ).json()
        for field in (
            "working_context",
            "structured_prompt",
            "retrieval_trace",
            "acceptance_decisions",
            "project_state",
        ):
            assert detail[field] is None
        assert detail["always_missing_reason"]


class TestBenchmarkExplorerCorpusCaching:
    """Performance-audit regression tests for _build_corpus()'s cache.

    _build_corpus previously re-read and re-JSON-parsed every result and
    dataset file on every single call (~325 files, ~125ms, measured against
    the repo's real committed benchmark corpus) -- including
    get_benchmark_detail, which only needs one row. It is now cached, keyed
    on a cheap (path, mtime, size) signature (see _corpus_signature), so a
    request that finds nothing changed on disk skips re-reading and
    re-parsing every file. These tests confirm the cache is transparent:
    unchanged disk state reuses the cached corpus (no re-read), while any
    addition, removal, or edit is still picked up on the very next call --
    the "always reflects what's on disk right now" contract the module
    docstring already promises is unaffected by the cache.
    """

    def test_second_call_with_no_disk_changes_skips_reparsing(
        self, sandbox: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _seed_two_base_cases(sandbox)

        call_count = {"n": 0}
        original_load_json = benchmark_explorer._load_json

        def counting_load_json(path):
            call_count["n"] += 1
            return original_load_json(path)

        first = benchmark_explorer._build_corpus()
        assert call_count["n"] == 0  # not yet patched; establishes cache

        monkeypatch.setattr(benchmark_explorer, "_load_json", counting_load_json)
        second = benchmark_explorer._build_corpus()

        assert second == first
        assert call_count["n"] == 0, (
            "second _build_corpus() call re-read files from disk despite "
            "nothing having changed since the first call"
        )

    def test_adding_a_result_file_invalidates_the_cache(self, sandbox: Path) -> None:
        _seed_two_base_cases(sandbox)
        first = benchmark_explorer._build_corpus()
        assert len(first) == 2

        _write_json(
            sandbox / "bench_results" / "results_extra.json",
            {
                "metadata": {"adapter": "extra_adapter"},
                "results": [
                    {
                        "benchmark_id": "identity_case_003",
                        "category": "identity",
                        "passed": True,
                        "answer_score": 1.0,
                        "query": "q",
                        "expected": {},
                        "answer": "a",
                    }
                ],
            },
        )

        second = benchmark_explorer._build_corpus()
        assert len(second) == 3

    def test_editing_a_dataset_file_invalidates_the_cache(self, sandbox: Path) -> None:
        _seed_two_base_cases(sandbox)
        first = benchmark_explorer._build_corpus()
        first_case = next(
            row for row in first if row["benchmark_id"] == "identity_case_001"
        )
        assert first_case["conversation"] == [
            {"speaker": "user", "text": "My name is Sam."}
        ]

        _write_json(
            sandbox / "bench_datasets" / "identity" / "case_001.json",
            {
                "benchmark_id": "identity_case_001",
                "category": "identity",
                "conversation": [{"speaker": "user", "text": "My name is actually Sam."}],
                "query": "What is my name?",
                "expected": {"answer_contains": ["Sam"]},
            },
        )

        second = benchmark_explorer._build_corpus()
        second_case = next(
            row for row in second if row["benchmark_id"] == "identity_case_001"
        )
        assert second_case["conversation"] == [
            {"speaker": "user", "text": "My name is actually Sam."}
        ]

    def test_removing_a_result_file_invalidates_the_cache(self, sandbox: Path) -> None:
        _seed_two_base_cases(sandbox)
        first = benchmark_explorer._build_corpus()
        assert len(first) == 2

        (sandbox / "bench_results" / "results_haven.json").unlink()

        second = benchmark_explorer._build_corpus()
        assert len(second) == 1

    def test_http_detail_route_reflects_a_change_made_after_the_list_route_cached_it(
        self, client: TestClient, sandbox: Path
    ) -> None:
        _seed_two_base_cases(sandbox)
        list_body = client.get("/api/v1/dashboard/benchmarks").json()
        assert list_body["total"] == 2

        _write_json(
            sandbox / "bench_results" / "results_extra.json",
            {
                "metadata": {"adapter": "extra_adapter"},
                "results": [
                    {
                        "benchmark_id": "identity_case_003",
                        "category": "identity",
                        "passed": True,
                        "answer_score": 1.0,
                        "query": "q",
                        "expected": {},
                        "answer": "a",
                    }
                ],
            },
        )

        detail = client.get(
            "/api/v1/dashboard/benchmarks/identity_case_003",
            params={"adapter": "extra_adapter", "kind": "base"},
        )
        assert detail.status_code == 200
        assert detail.json()["answer"] == "a"
