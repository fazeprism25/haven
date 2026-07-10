"""continuation_judge — Stage C of the continuation benchmark pipeline.

See ``docs/architecture/CONTINUATION_BENCHMARK_DESIGN.md`` §5/§6. Unlike
``benchmarks/judges/llm_judge.py`` (fact-presence via
``answer_contains``/``must_not_contain``), this judge scores a *generated
continuation response* (Stage B's output) against a case's ``ground_truth``
and rubric (``expected``), and has a notion of priority and forbidden
action that the existing judge does not. It reuses the existing judge's
Qwen Cloud client/model-resolution helpers (``QWEN_API_KEY``,
``QWEN_JUDGE_MODEL``, ``QWEN_BASE_URL``) for infrastructure consistency, at
``temperature=0`` for reproducibility -- the same live-LLM-variance caveat
``benchmarks/RUNNER_SPEC.md`` documents for the existing judge applies here
too.

The judge itself only returns raw sub-scores/violations (§5's contract);
weighting those into a single 0.0-1.0 score per §6's table, including the
hard-fail ceiling, is done deterministically in :func:`_weighted_score` --
kept out of the LLM call so the scoring formula can't silently drift
between runs the way an LLM asked to "compute the weighted score itself"
could.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from benchmarks.judges.llm_judge import _get_client, _resolve_model

FAILURE_TYPES = (
    "NONE",
    "STALE_STATE_SURFACED",
    "REJECTED_APPROACH_REVIVED",
    "CONSTRAINT_VIOLATED",
    "BLOCKER_IGNORED",
    "MISPRIORITIZED",
    "INCOMPLETE",
    "JUDGE_ERROR",
)

# §6's scoring table.
MUST_STATE_WEIGHT = 0.40
VIOLATIONS_WEIGHT = 0.35
PRIORITIZE_WEIGHT = 0.15
COHERENCE_WEIGHT = 0.10

# §6: "Any violation caps the whole per-query score at a fixed low ceiling
# (proposed: 0.2)".
HARD_FAIL_CEILING = 0.2

# Not specified by the design (which only fixes the hard-fail ceiling); 0.6
# is this implementation's threshold for a per-query "passed" bool, kept
# alongside the continuous score so callers get both.
PASS_THRESHOLD = 0.6

SYSTEM_PROMPT = """
You are an impartial evaluator for a memory-system continuation benchmark.

You will receive:

1. The query a returning engineer asked (e.g. "Continue implementing the
   project." or "What should we work on next?").
2. The ground_truth: the full, structured project state as of that query
   (current objective, active decisions, superseded/rejected approaches,
   constraints, active and resolved blockers, active tasks, open
   questions, top priority next action).
3. The rubric (expected): must_state, must_not_state, forbidden_actions,
   must_prioritize -- all mechanically derived from ground_truth.
4. A response produced by a separate AI model that has never seen the raw
   conversation -- only a reconstructed context plus the query.

Score the RESPONSE against the rubric. Use ground_truth to reason about
*why* something is stale, rejected, or forbidden -- do not just
substring-match against the rubric's fixed strings; accept paraphrases and
synonyms.

Return ONLY valid JSON with exactly these fields:

{
  "must_state_score": 0.0-1.0,      // fraction of expected.must_state semantically covered
  "must_not_state_violations": [],   // list of expected.must_not_state / ground_truth stale items the response states as current (empty list if none)
  "forbidden_action_violations": [], // list of expected.forbidden_actions the response recommends (empty list if none)
  "prioritization_correct": true,    // does the response's PRIMARY recommended action match expected.must_prioritize / ground_truth.top_priority_next_action (not just mention it)
  "coherence_score": 0.0-1.0,        // holistic: does this read like a competent engineer's own resumption, not a fact list
  "reason": "...",
  "failure_type": "NONE|STALE_STATE_SURFACED|REJECTED_APPROACH_REVIVED|CONSTRAINT_VIOLATED|BLOCKER_IGNORED|MISPRIORITIZED|INCOMPLETE|JUDGE_ERROR"
}

Do not compute an overall score yourself -- the caller weights these fields
deterministically. Just report the sub-scores and violations accurately.
"""


def _weighted_score(judge_result: Dict[str, Any]) -> float:
    """Apply §6's weighting, including the hard-fail ceiling, deterministically."""
    must_state = float(judge_result.get("must_state_score", 0.0) or 0.0)
    coherence = float(judge_result.get("coherence_score", 0.0) or 0.0)
    prioritized = 1.0 if judge_result.get("prioritization_correct") else 0.0
    violated = bool(judge_result.get("must_not_state_violations")) or bool(
        judge_result.get("forbidden_action_violations")
    )

    score = (
        MUST_STATE_WEIGHT * must_state
        + VIOLATIONS_WEIGHT * (0.0 if violated else 1.0)
        + PRIORITIZE_WEIGHT * prioritized
        + COHERENCE_WEIGHT * coherence
    )
    if violated:
        score = min(score, HARD_FAIL_CEILING)
    return round(score, 4)


def judge_continuation(
    query: str,
    ground_truth: Dict[str, Any],
    expected: Dict[str, Any],
    response: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Score Stage B's *response* against *ground_truth*/*expected* (Stage C).

    Returns the judge's raw sub-scores/violations/reason/failure_type plus
    two fields the judge itself never computes: ``score`` (the §6-weighted
    0.0-1.0 total, hard-fail ceiling applied) and ``passed`` (``score >=
    PASS_THRESHOLD`` AND no ``must_not_state``/``forbidden_action``
    violations -- a query can't pass purely on a high weighted score if it
    hit the hard-fail gate).
    """
    prompt = f"""
    Query:
    {query}

    ground_truth:
    {json.dumps(ground_truth, indent=2)}

    expected (rubric):
    {json.dumps(expected, indent=2)}

    Response:
    {response}

    Return only JSON.
    """
    client = _get_client()
    api_response = client.chat.completions.create(
        model=_resolve_model(model),
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    text = api_response.choices[0].message.content

    try:
        result = json.loads(text)
    except Exception:
        return {
            "must_state_score": 0.0,
            "must_not_state_violations": [],
            "forbidden_action_violations": [],
            "prioritization_correct": False,
            "coherence_score": 0.0,
            "reason": "Judge returned invalid JSON",
            "failure_type": "JUDGE_ERROR",
            "score": 0.0,
            "passed": False,
            "raw_output": text,
        }

    result["score"] = _weighted_score(result)
    result["passed"] = bool(
        result["score"] >= PASS_THRESHOLD
        and not result.get("must_not_state_violations")
        and not result.get("forbidden_action_violations")
    )
    return result
