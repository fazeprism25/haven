"""Concrete ``LLMInterface`` implementation.

Every Manager AI stage (``Extractor``, ``Classifier``, ``ImportanceScorer``)
depends on the shared ``obsidian.manager_ai.transport_retry.LLMInterface``
``Protocol`` (``generate(prompt) -> str``), not a concrete provider, so this
module supplies exactly one thing: a class implementing that protocol. It
contains no pipeline logic of its own.

Mirrors :mod:`obsidian.memory_engine.query_rewriter`'s ``_get_client``/
``_resolve_model`` OpenAI-compatible client pattern (env-var-configured API
key/base URL/model) rather than inventing a new one, using its own
``MANAGER_AI_*`` environment variables since this is a separate production
call site with its own credentials/model choice.

Unlike ``QueryRewriter`` (a best-effort read-path enhancement that fails
open to "no rewrites"), a Remember request is a deliberate write the user
is waiting on — so :meth:`ManagerAILLM.generate` raises on any failure
(missing API key, network error, empty response) instead of swallowing it;
the caller (the ``/memory`` endpoint) is expected to surface that as a
failed save, not a silent no-op.
"""

from __future__ import annotations

import os
from typing import Optional

from config.load_env import load_manager_ai_env

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - exercised only when openai isn't installed
    OpenAI = None  # type: ignore[assignment,misc]

load_manager_ai_env()

DEFAULT_MODEL = "qwen-plus"
DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0


def _resolve_model(model: Optional[str] = None) -> str:
    """Resolve the Manager AI model from an explicit arg, env var, or default."""
    return model or os.environ.get("MANAGER_AI_MODEL", DEFAULT_MODEL)


def _resolve_timeout() -> float:
    """Resolve the per-request timeout (seconds) from an env var or default."""
    raw = os.environ.get("MANAGER_AI_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _get_client() -> "OpenAI":
    """Build an OpenAI-compatible client for the Manager AI pipeline.

    The API key is never hardcoded; it must be supplied via the
    ``MANAGER_AI_API_KEY`` environment variable. Raises ``RuntimeError`` if
    it is unset or the ``openai`` package isn't installed — callers here
    treat that as a fatal error (see module docstring), unlike
    ``QueryRewriter``'s fail-open equivalent.
    """
    api_key = os.environ.get("MANAGER_AI_API_KEY")
    if not api_key:
        raise RuntimeError("MANAGER_AI_API_KEY environment variable is not set.")
    if OpenAI is None:
        raise RuntimeError("the 'openai' package is not installed.")
    base_url = os.environ.get("MANAGER_AI_BASE_URL", DEFAULT_BASE_URL)
    return OpenAI(api_key=api_key, base_url=base_url)


class ManagerAILLM:
    """Shared ``LLMInterface`` implementation for the Manager AI stages.

    One instance is constructed and passed to ``Extractor``, ``Classifier``,
    and ``ImportanceScorer`` alike — all three only ever call
    :meth:`generate`, so a single client/model/timeout configuration serves
    every stage without any stage-specific state.
    """

    def __init__(self, model: Optional[str] = None) -> None:
        self._model = _resolve_model(model)
        self._timeout = _resolve_timeout()

    def generate(self, prompt: str) -> str:
        """Return the raw text response for *prompt*.

        Raises
        ------
        RuntimeError
            If ``MANAGER_AI_API_KEY`` is unset or the ``openai`` package
            isn't installed.
        """
        client = _get_client()
        response = client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            timeout=self._timeout,
        )
        return response.choices[0].message.content or ""
