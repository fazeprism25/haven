"""Haven's Benchmark Explorer — read-only presentation over existing benchmark artifacts.

::

    Benchmark Explorer UI (dashboard.html's #benchmarks section)
            |
            v
        FastAPI (this module)
            |
            v
    benchmarks/results/results*.json  +  benchmarks/datasets*/**/*.json
            (already on disk — nothing here runs a benchmark, an
             adapter, a judge, or an LLM call)

This module contributes no retrieval, ranking, scoring, or benchmark-execution
logic of its own. It only reads two kinds of files that already exist on disk
and joins them by ``benchmark_id``:

- **Result files** (``benchmarks/results/results*.json``) — the judged
  outcome of a previous run: ``passed``/``answer_score``, the judge's
  ``judge_reason``/``failure_type``, and whatever the adapter answered with.
  See ``benchmarks/RUNNER_SPEC.md``'s "Output Schema" for the base-suite shape
  and ``benchmarks/runners/run_continuation_benchmarks.py``'s ``main()`` for
  the continuation-pilot shape (detected by the presence of a ``"queries"``
  list instead of a single ``"query"`` string on each result row).
- **Dataset case files** (``benchmarks/datasets/**/*.json`` and
  ``benchmarks/datasets_continuation/**/*.json``) — the conversation and
  expectations a result was judged against. A result file alone never
  contains the conversation that produced it (see ``RUNNER_SPEC.md``'s
  Output Schema — no ``conversation`` key), so this is the only place that
  data can come from.

Every join is by ``benchmark_id`` string match; nothing is re-derived,
re-scored, or re-ranked. Fields this repo's benchmark artifacts have never
captured for *any* run — Working Context, the rendered Structured Prompt,
a Retrieval Trace, per-candidate Acceptance Decisions, ProjectState — are
reported as explicitly absent (see ``_ALWAYS_MISSING_FIELDS`` below) rather
than fabricated or silently dropped; see
``docs/architecture/CONTINUATION_BENCHMARK_AUDIT.md`` Critical-1 for why
even the continuation pilot's committed artifacts (were any committed) would
not carry these either.
"""

from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/v1/dashboard/benchmarks")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RESULTS_GLOB = str(_REPO_ROOT / "benchmarks" / "results" / "results*.json")
_BASE_DATASETS_DIR = _REPO_ROOT / "benchmarks" / "datasets"
_CONTINUATION_DATASETS_DIR = _REPO_ROOT / "benchmarks" / "datasets_continuation"

#: Fields the Explorer always tries to show per case (per the brief this
#: module implements) that no committed artifact -- base suite or
#: continuation pilot -- has ever captured. Kept as an explicit list (rather
#: than silently omitting the keys) so the UI can render an honest "not
#: available" instead of looking like the data was forgotten.
_ALWAYS_MISSING_FIELDS = (
    "working_context",
    "structured_prompt",
    "retrieval_trace",
    "acceptance_decisions",
    "project_state",
)
_ALWAYS_MISSING_REASON = (
    "Not captured by any committed benchmark result file today -- "
    "see benchmarks/RUNNER_SPEC.md's Output Schema (base suite) and "
    "docs/architecture/CONTINUATION_BENCHMARK_AUDIT.md Critical-1 "
    "(continuation pilot: this data was never persisted by any run)."
)


def _normalize_category(raw: str) -> str:
    """Best-effort canonical category name, reusing the base suite's own alias
    table instead of duplicating it (``benchmarks/analysis/classify_failure.py``
    already maintains it for the same reason: dataset authoring predates a
    consistent naming convention across categories). Falls back to *raw*
    unchanged if the analysis module can't be imported for any reason -- this
    module must still work even if that one is missing or broken.
    """
    try:
        from benchmarks.analysis.classify_failure import normalize_category

        return normalize_category(raw)
    except Exception:
        return raw


def _adapter_from_filename(path: str) -> str:
    """Infer an adapter name from a legacy result file with no ``metadata.adapter``.

    Mirrors ``benchmarks/README.md``'s own documented convention: bare
    ``results.json`` is the mem0 baseline, ``results_<adapter>.json`` is
    everything else.
    """
    name = os.path.splitext(os.path.basename(path))[0]
    if name == "results":
        return "mem0"
    if name.startswith("results_continuation_"):
        return name[len("results_continuation_"):]
    if name.startswith("results_"):
        return name[len("results_"):]
    return name


def _load_json(path: Path) -> Optional[Any]:
    """Return the parsed JSON at *path*, or ``None`` for anything unreadable.

    A 0-byte placeholder or a malformed file is skipped, not an error --
    identical tolerance to ``run_benchmarks.load_benchmarks``'s own
    skip-with-warning behavior for the same files.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _load_result_files() -> List[Dict[str, Any]]:
    """Every parseable ``benchmarks/results/results*.json`` file.

    Returns one entry per file: ``{"path", "adapter", "metadata", "results",
    "kind"}``. ``kind`` is ``"continuation"`` when a result row carries a
    ``"queries"`` list (the continuation pilot's per-case shape -- see
    ``run_continuation_benchmarks.run_continuation_case``) rather than a
    single ``"query"`` string (the base suite's shape -- see
    ``RUNNER_SPEC.md``), and is otherwise ``"base"``.
    """
    files: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(_RESULTS_GLOB)):
        data = _load_json(Path(path))
        if data is None:
            continue
        if isinstance(data, list):
            metadata, results = {}, data
        elif isinstance(data, dict):
            metadata, results = data.get("metadata", {}) or {}, data.get("results", []) or []
        else:
            continue
        adapter = metadata.get("adapter") or _adapter_from_filename(path)
        kind = "continuation" if (results and "queries" in results[0]) else "base"
        files.append(
            {
                "path": path,
                "adapter": adapter,
                "metadata": metadata,
                "results": results,
                "kind": kind,
            }
        )
    return files


def _walk_dataset_cases(root: Path) -> Dict[str, Dict[str, Any]]:
    """``benchmark_id -> case dict`` for every parseable ``*.json`` under *root*.

    A later file silently wins on a duplicate ``benchmark_id`` (not expected
    in practice -- dataset ids are authored to be unique -- but this must
    never raise over it).
    """
    cases: Dict[str, Dict[str, Any]] = {}
    if not root.is_dir():
        return cases
    for path in sorted(root.rglob("*.json")):
        data = _load_json(path)
        if not isinstance(data, dict) or "benchmark_id" not in data:
            continue
        data = dict(data)
        data["_source_file"] = str(path.relative_to(_REPO_ROOT))
        cases[data["benchmark_id"]] = data
    return cases


def _merge_case(
    *,
    adapter: str,
    kind: str,
    benchmark_id: str,
    category: str,
    result: Optional[Dict[str, Any]],
    dataset_case: Optional[Dict[str, Any]],
    result_file: Optional[str],
) -> Dict[str, Any]:
    """Join one result row (if any) with its dataset case (if found) into one
    detail object -- every field the Benchmark Explorer's brief asks for,
    populated from whichever source actually has it, ``None`` when neither
    does.
    """
    detail: Dict[str, Any] = {
        "benchmark_id": benchmark_id,
        "adapter": adapter,
        "kind": kind,
        "category": category,
        "has_result": result is not None,
        "source_result_file": result_file,
        "source_dataset_file": (dataset_case or {}).get("_source_file"),
        "conversation": (dataset_case or {}).get("conversation"),
    }

    if kind == "base":
        detail["query"] = (result or {}).get("query") or (dataset_case or {}).get("query")
        detail["expected"] = (result or {}).get("expected") or (dataset_case or {}).get("expected")
        detail["answer"] = (result or {}).get("answer")
        detail["retrieved_memories"] = (result or {}).get("retrieved_memories")
        detail["judge_result"] = (
            {"passed": result.get("passed"), "score": result.get("answer_score")}
            if result is not None
            else None
        )
        detail["judge_explanation"] = (result or {}).get("judge_reason")
        detail["failure_classification"] = (result or {}).get("failure_type")
        detail["timings"] = (result or {}).get("timings")
    else:  # continuation
        detail["ground_truth"] = (dataset_case or {}).get("ground_truth")
        detail["queries"] = (dataset_case or {}).get("queries")
        detail["expected"] = (dataset_case or {}).get("expected")
        detail["tier"] = (dataset_case or {}).get("tier")
        detail["domain"] = (dataset_case or {}).get("domain")
        per_query = (result or {}).get("queries")
        detail["judge_result"] = (
            {
                "passed": result.get("passed"),
                "score": result.get("case_score"),
                "hard_fail": result.get("hard_fail"),
            }
            if result is not None
            else None
        )
        # Stage C's per-query reasons, joined -- the closest thing to a
        # single "judge explanation" this schema has (each query is judged
        # independently, see run_continuation_benchmarks.run_continuation_case).
        detail["judge_explanation"] = (
            "; ".join(f"[{q.get('query', '?')}] {q.get('reason', '')}" for q in per_query)
            if per_query
            else None
        )
        detail["failure_classification"] = (
            sorted({q.get("failure_type") for q in per_query if q.get("failure_type")})
            if per_query
            else None
        )
        # Stage A's raw context string per query -- what the design doc calls
        # the "rendered, structured prompt" for Haven, or a flat join for
        # every other adapter (see CONTINUATION_BENCHMARK_DESIGN.md §3). Kept
        # under its own name rather than mapped onto "structured_prompt" /
        # "working_context" below: this schema never decomposes it into those
        # two separate objects, so labeling it as either would overclaim what
        # the artifact actually contains.
        detail["per_query"] = per_query
        detail["timings"] = (result or {}).get("timings")

    for field in _ALWAYS_MISSING_FIELDS:
        detail[field] = None
    detail["always_missing_fields"] = list(_ALWAYS_MISSING_FIELDS)
    detail["always_missing_reason"] = _ALWAYS_MISSING_REASON

    return detail


#: Cache for _build_corpus(), invalidated by comparing _corpus_signature()
#: rather than a fixed TTL -- see that function's docstring.
_corpus_cache: Optional[List[Dict[str, Any]]] = None
_corpus_cache_signature: Optional[Tuple[Tuple[str, Optional[int], Optional[int]], ...]] = None


def _corpus_signature() -> Tuple[Tuple[str, Optional[int], Optional[int]], ...]:
    """Cheap stand-in for "has anything _build_corpus() reads changed?".

    ``(path, mtime_ns, size)`` for every result and dataset file
    ``_build_corpus`` would read, without opening or JSON-parsing any of
    them -- an ``os.stat`` per file is far cheaper than the
    read-plus-``json.load`` every one of those files otherwise gets on
    every single call (see ``_load_json``). A file that vanishes between
    the listing and the ``stat`` call (e.g. a concurrent benchmark run
    still writing its results file) contributes ``(path, None, None)``
    rather than raising, so a signature can always be computed. Any
    addition, removal, edit, or resize of a relevant file changes the
    signature, so the cache this guards still reflects "what's on disk
    right now" -- it is only stale between an edit and the next call, and
    a stat-based check is the cheapest thing that still detects the edit
    rather than assuming nothing ever changes.
    """
    paths = sorted(glob.glob(_RESULTS_GLOB))
    for root in (_BASE_DATASETS_DIR, _CONTINUATION_DATASETS_DIR):
        if root.is_dir():
            paths.extend(str(p) for p in sorted(root.rglob("*.json")))
    signature = []
    for path in paths:
        try:
            stat = os.stat(path)
            signature.append((path, stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append((path, None, None))
    return tuple(signature)


def _build_corpus() -> List[Dict[str, Any]]:
    """Every browsable (adapter, kind, benchmark_id) row, dataset-joined.

    Cached across calls, keyed on :func:`_corpus_signature` -- still
    reflects "what's on disk right now" (the same convention
    ``obsidian.server.dashboard`` uses for the vault/concept state), since
    any relevant file changing invalidates the signature and forces a
    rebuild, but a call that finds nothing changed since the last one skips
    re-reading and re-parsing every result/dataset file from scratch.
    """
    global _corpus_cache, _corpus_cache_signature
    signature = _corpus_signature()
    if _corpus_cache is not None and signature == _corpus_cache_signature:
        return _corpus_cache

    result_files = _load_result_files()
    base_cases = _walk_dataset_cases(_BASE_DATASETS_DIR)
    continuation_cases = _walk_dataset_cases(_CONTINUATION_DATASETS_DIR)

    rows: List[Dict[str, Any]] = []
    seen_continuation_ids: Set[str] = set()

    for rf in result_files:
        dataset_lookup = continuation_cases if rf["kind"] == "continuation" else base_cases
        for r in rf["results"]:
            benchmark_id = r.get("benchmark_id")
            if not benchmark_id:
                continue
            dataset_case = dataset_lookup.get(benchmark_id)
            raw_category = r.get("category") or (dataset_case or {}).get("category") or "unknown"
            if rf["kind"] == "continuation":
                seen_continuation_ids.add(benchmark_id)
            rows.append(
                _merge_case(
                    adapter=rf["adapter"],
                    kind=rf["kind"],
                    benchmark_id=benchmark_id,
                    category=_normalize_category(raw_category),
                    result=r,
                    dataset_case=dataset_case,
                    result_file=os.path.relpath(rf["path"], _REPO_ROOT),
                )
            )

    # Continuation cases with no judged run committed yet are still browsable
    # -- honestly marked "not yet run" rather than hidden. As of this writing
    # no results_continuation_*.json is committed at all (see
    # CONTINUATION_BENCHMARK_AUDIT.md); if one is added later it is picked up
    # automatically by the results*.json glob above and merged in the loop.
    for benchmark_id, case in continuation_cases.items():
        if benchmark_id in seen_continuation_ids:
            continue
        rows.append(
            _merge_case(
                adapter="(none — not yet run)",
                kind="continuation",
                benchmark_id=benchmark_id,
                category=_normalize_category(case.get("category", "unknown")),
                result=None,
                dataset_case=case,
                result_file=None,
            )
        )

    _corpus_cache = rows
    _corpus_cache_signature = signature
    return rows


def _to_summary(detail: Dict[str, Any]) -> Dict[str, Any]:
    """Lightweight row for the list view -- no conversation/answer/per_query
    payload, so a page of a couple thousand rows stays a small response.
    Full detail is fetched only for the row(s) a user actually expands.
    """
    judge = detail.get("judge_result") or {}
    return {
        "benchmark_id": detail["benchmark_id"],
        "adapter": detail["adapter"],
        "kind": detail["kind"],
        "category": detail["category"],
        "has_result": detail["has_result"],
        "passed": judge.get("passed"),
        "score": judge.get("score"),
        "failure_classification": detail.get("failure_classification"),
    }


def _failure_types_of(row: Dict[str, Any]) -> List[str]:
    ft = row.get("failure_classification")
    if ft is None:
        return []
    return ft if isinstance(ft, list) else [ft]


@router.get("")
def list_benchmarks(
    category: Optional[str] = None,
    adapter: Optional[str] = None,
    kind: Optional[str] = None,
    passed: Optional[bool] = None,
    failure_type: Optional[str] = None,
) -> Dict[str, Any]:
    """List every browsable benchmark case, optionally filtered.

    Returns ``{"rows": [...summary...], "facets": {...}, "total": N,
    "total_filtered": M}`` in one call -- ``facets`` lists every distinct
    category/adapter/kind/failure-type value actually present, so the
    dashboard can build its filter dropdowns from real data instead of a
    hardcoded list that could drift from what's on disk.
    """
    corpus = _build_corpus()
    rows = [_to_summary(d) for d in corpus]

    facets = {
        "categories": sorted({r["category"] for r in rows}),
        "adapters": sorted({r["adapter"] for r in rows}),
        "kinds": sorted({r["kind"] for r in rows}),
        "failure_types": sorted({ft for r in rows for ft in _failure_types_of(r)}),
    }

    def matches(r: Dict[str, Any]) -> bool:
        if category and r["category"] != category:
            return False
        if adapter and r["adapter"] != adapter:
            return False
        if kind and r["kind"] != kind:
            return False
        if passed is not None and r["passed"] != passed:
            return False
        if failure_type and failure_type not in _failure_types_of(r):
            return False
        return True

    filtered = [r for r in rows if matches(r)]
    return {
        "rows": filtered,
        "facets": facets,
        "total": len(rows),
        "total_filtered": len(filtered),
    }


@router.get("/{benchmark_id}")
def get_benchmark_detail(benchmark_id: str, adapter: str, kind: str) -> Dict[str, Any]:
    """Full detail for one (adapter, kind, benchmark_id) row -- fetched on
    expand, not bundled into the list response (see ``_to_summary``).

    Raises
    ------
    HTTPException
        404 if no row matches this exact (adapter, kind, benchmark_id)
        combination.
    """
    for detail in _build_corpus():
        if (
            detail["benchmark_id"] == benchmark_id
            and detail["adapter"] == adapter
            and detail["kind"] == kind
        ):
            return detail
    raise HTTPException(
        status_code=404,
        detail=f"No benchmark case {benchmark_id!r} for adapter={adapter!r}, kind={kind!r}.",
    )
