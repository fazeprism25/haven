import json
import os
from typing import Any, Dict, Optional

from openai import OpenAI

from config.load_env import load_benchmark_judge_env

load_benchmark_judge_env()

# Qwen Cloud (Alibaba Model Studio / DashScope) exposes an
# OpenAI-compatible endpoint, so the official ``openai`` client can talk
# to it directly by pointing ``base_url`` at DashScope instead of OpenAI.
DEFAULT_QWEN_MODEL = "qwen-plus"
DEFAULT_QWEN_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

SYSTEM_PROMPT = """
You are an impartial evaluator for memory-system benchmarks.

Your job is to determine whether the MODEL ANSWER satisfies the benchmark.

You will receive:

1. A question.
2. The expected answer requirements.
3. The model answer.

Evaluation Rules

1. Judge SEMANTIC MEANING, not exact wording.

2. The answer DOES NOT need to literally contain words listed in "answer_contains".

3. Paraphrases, synonyms and implied meaning are acceptable.

4. Historical memories are acceptable if the answer clearly identifies the user's CURRENT belief, decision or preference.

5. Do NOT fail simply because an old memory appears in the answer.

6. Only fail if:
   - the answer is factually incorrect,
   - essential information is missing,
   - the answer contradicts the latest memory,
   - or the answer is too incomplete to answer the question.

7. Ignore wording differences.

8. Be strict about correctness but flexible about phrasing.

failure_type MUST be exactly one of:

NONE
RETRIEVAL
SUPERSESSION
TEMPORAL
REASONING
INCOMPLETE
INCORRECT
JUDGE_ERROR

Return ONLY valid JSON.

Example:

{
    "passed": true,
    "score": 0.96,
    "reason": "The answer correctly captures the user's current decision despite using different wording.",
    "failure_type": "NONE"
}
"""

def _resolve_model(model: Optional[str] = None) -> str:
    """Resolve the Qwen model name from an explicit arg, env var, or default."""
    return model or os.environ.get("QWEN_JUDGE_MODEL", DEFAULT_QWEN_MODEL)


def _get_client() -> OpenAI:
    """Build an OpenAI-compatible client pointed at the Qwen Cloud API.

    The API key is never hardcoded; it must be supplied via the
    ``QWEN_API_KEY`` environment variable. The base URL defaults to
    DashScope's international endpoint but can be overridden with
    ``QWEN_BASE_URL`` (e.g. to use the mainland-China endpoint).
    """
    api_key = os.environ.get("QWEN_API_KEY")
    if not api_key:
        raise RuntimeError(
            "QWEN_API_KEY environment variable is not set. Set it to a "
            "valid Qwen Cloud API key to run the benchmark judge."
        )
    base_url = os.environ.get("QWEN_BASE_URL", DEFAULT_QWEN_BASE_URL)
    return OpenAI(api_key=api_key, base_url=base_url)


def judge_answer(
    query: str,
    expected: Dict[str, Any],
    answer: str,
    model: Optional[str] = None,
) -> Dict[str, Any]:

    prompt = f"""
    Question:
    {query}

    Expected:
    {json.dumps(expected)}

    Answer:
    {answer}

    Return only JSON.
    """
    client = _get_client()
    response = client.chat.completions.create(
        model=_resolve_model(model),
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": prompt
            }
        ]
    )
    text = response.choices[0].message.content

    try:
        return json.loads(text)

    except Exception:

        return {
            "passed": False,
            "score": 0.0,
            "reason": "Judge returned invalid JSON",
            "failure_type": "JUDGE_ERROR",
            "raw_output": text
        }


if __name__ == "__main__":

    result = judge_answer(
        query="What should I build first?",
        expected={
            "answer_contains": ["Manager AI"],
            "must_not_contain": ["GraphRAG first"]
        },
        answer="""
    Earlier the user preferred GraphRAG first.
    Later they changed their mind.
    Their current decision is to build Manager AI first.
    """
    )

    print(json.dumps(result, indent=2))