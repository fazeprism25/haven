"""Shared transport-retry helper for the Manager AI LLM stages.

``Extractor``, ``Classifier``, and ``ImportanceScorer`` each already retry
once, on their own, when the LLM's response is unusable -- unparseable JSON
or a value that fails validation (a ``ValueError``, repaired via a
stage-specific "repair prompt" -- see each stage's ``build_repair_prompt``).
That retry is deliberately untouched by this module.

This module handles a different failure mode: the LLM call itself never
returned a response at all -- a network error, a timeout, or some other
transient transport failure from the provider client. Retrying *that* with
a "repair prompt" makes no sense (there is no bad response to repair), so
it is handled here, once, before the response ever reaches a stage's
parse/validate logic.

Deliberately not a decorator/context-manager wrapping a stage method: each
stage still calls this once per ``llm.generate`` call site (the first
attempt and, separately, the repair attempt), so a transient network error
on *either* call gets its own single retry.
"""

from __future__ import annotations

from typing import Protocol, Tuple, Type

try:
    from openai import APIConnectionError, APITimeoutError

    _OPENAI_TRANSPORT_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
        APIConnectionError,
        APITimeoutError,
    )
except ImportError:  # pragma: no cover - exercised only when openai isn't installed
    _OPENAI_TRANSPORT_EXCEPTIONS = ()

#: Exceptions treated as transient transport failures worth one retry --
#: network errors and timeouts, from the generic stdlib exceptions any
#: ``LLMInterface`` implementation (including test doubles) might raise, up
#: to the ``openai`` SDK's own connection/timeout exceptions. Deliberately
#: excludes non-transient provider errors (e.g. ``openai.APIStatusError``
#: for a 400/401/429) -- those won't be fixed by retrying, and excludes
#: ``ValueError`` entirely, which is reserved for parse/validation failures
#: handled by each stage's own repair-prompt retry.
TRANSPORT_EXCEPTIONS: Tuple[Type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    OSError,
) + _OPENAI_TRANSPORT_EXCEPTIONS


class LLMInterface(Protocol):
    """Minimal interface for the LLM passed to :func:`generate_with_transport_retry`."""

    def generate(self, prompt: str) -> str:
        """Return the raw text response for *prompt*."""
        ...


def generate_with_transport_retry(llm: LLMInterface, prompt: str) -> str:
    """Call ``llm.generate(prompt)``, retrying exactly once on a transport failure.

    Parameters
    ----------
    llm : LLMInterface
        The LLM client to call.
    prompt : str
        The prompt to send, unchanged between the first attempt and the
        retry -- this is a transport retry, not a repair retry, so there is
        no reason to alter the prompt.

    Returns
    -------
    str
        The raw response from whichever attempt succeeded.

    Raises
    ------
    Exception
        Whatever :data:`TRANSPORT_EXCEPTIONS` member the second attempt
        raised, if it also fails -- never swallowed, never replaced with a
        default response. Because it is raised from inside the first
        attempt's ``except`` block, Python chains it to the first failure
        automatically (``__context__``), so both attempts remain visible in
        the traceback. A ``ValueError`` (or anything else outside
        :data:`TRANSPORT_EXCEPTIONS`) from either attempt is never caught
        here and propagates immediately, untouched -- that is each stage's
        own parse/validation retry to handle, not this helper's.
    """
    try:
        return llm.generate(prompt)
    except TRANSPORT_EXCEPTIONS:
        return llm.generate(prompt)
