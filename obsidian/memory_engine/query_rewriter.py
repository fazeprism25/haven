"""Multi-query expansion for the Haven Memory Engine ("Design 2").

This module has exactly one responsibility: given a raw user query, ask an
LLM for up to two alternate phrasings of the same search intent, so a
downstream retrieval stage can search with several differently-worded
queries instead of just one. It does not retrieve, rank, or score
anything itself.

::

    raw query
        │
        ▼
    QueryRewriter          (this module)
        │
        ▼
    RewriteResult(original, rewrites<=2)

Explicitly out of scope
------------------------
* **No ranking, no retrieval.** This module never touches
  :class:`~obsidian.memory_engine.hybrid_candidate_retriever.HybridCandidateRetriever`,
  :class:`~obsidian.ontology.query_resolver.QueryResolver`, or any
  ``KnowledgeObject``/``Candidate`` type. It only ever consumes and
  produces plain strings.
* **No ontology changes.** Nothing here reads or writes
  :mod:`obsidian.ontology`.
* **No benchmark changes.** :mod:`benchmarks.judges.llm_judge` is read-only
  reference for the OpenAI-compatible client pattern; nothing in that
  module is imported or modified.
* **No MemoryEngine wiring of its own.** This module never constructs or
  calls :class:`~obsidian.memory_engine.engine.MemoryEngine` itself --
  wiring is entirely the caller's responsibility, via
  :class:`MemoryEngine`'s optional ``query_rewriter`` constructor
  parameter (see that module's "Optional multi-query expansion"). Haven's
  server (:mod:`obsidian.server.main`) constructs one shared instance at
  startup and passes it to every ``MemoryEngine`` it builds only when the
  "Query Rewriting" dashboard setting is on (off by default) -- see
  ``GET``/``PUT /api/v1/settings/query-rewriting``.

Fail-open contract
-------------------
Query rewriting is a *best-effort enhancement*, never a precondition for
retrieval to work. Every way the LLM call can go wrong — a missing API
key, a network timeout, a malformed or unexpected JSON response, or any
other API error — must degrade to "no rewrites", not raise. Concretely,
:meth:`QueryRewriter.rewrite` always returns a
:class:`RewriteResult` whose ``original`` field is the caller's exact
input string; ``rewrites`` is only ever non-empty on a fully successful,
well-formed LLM round trip. This is why the LLM call is wrapped in a
single broad ``except Exception`` (see :meth:`QueryRewriter._fetch_rewrites`)
rather than an enumerated list of expected exception types: the contract
is "never raise", not "handle the errors we thought of".

Determinism
-----------
:meth:`QueryRewriter.rewrite` caches the computed rewrite tuple keyed by
the *normalised* query (``.strip().lower()``, mirroring
:func:`obsidian.ontology.text_utils.normalize`'s definition without
importing it — see "Design decisions" below). Calling :meth:`rewrite`
twice with the same (or differently-cased/whitespaced) query against the
same :class:`QueryRewriter` instance issues at most one LLM call; the
second call reuses the cached rewrites. The cache stores only the
*rewrites*, never the whole :class:`RewriteResult`, so ``original`` in the
returned result always reflects that particular call's exact input string
— a cache hit never substitutes a previous call's differently-cased
``original`` for the current one.

Design decisions
-----------------
* **Normalisation is duplicated locally rather than imported.** Requirements
  explicitly forbid ontology changes for this task, and importing
  :mod:`obsidian.ontology.text_utils` would add a dependency edge from this
  module to the Ontology subsystem that nothing else here needs. The
  normalisation this module needs is one line (``.strip().lower()``), so
  it is inlined as :func:`_normalize_query` rather than imported.
* **Same OpenAI-compatible client pattern as the benchmark judge.**
  :func:`_get_client` and :func:`_resolve_model` mirror
  :mod:`benchmarks.judges.llm_judge`'s ``_get_client``/``_resolve_model``
  shape exactly (env-var-configured API key/base URL/model, ``OpenAI``
  client pointed at an OpenAI-compatible endpoint), but with their own
  ``QUERY_REWRITER_*`` environment variables rather than the judge's
  ``QWEN_*`` ones — this is a production module, not a benchmark tool, and
  should not be coupled to the benchmark harness's configuration surface.
  Its own local config file (``config/query_rewriter.env``, see
  :func:`obsidian.manager_ai.llm`'s and
  :mod:`benchmarks.judges.llm_judge`'s identical ``load_*_env()`` calls) is
  loaded the same way, independently of the other two.
* **Cache is per-instance, in-memory, unbounded.** ``QueryRewriter`` is
  expected to be constructed once and reused, analogous to
  :class:`~obsidian.memory_engine.hybrid_candidate_retriever.HybridCandidateRetriever`;
  nothing in the current requirements calls for cache eviction or
  cross-process sharing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from config.load_env import load_query_rewriter_env

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - exercised only when openai isn't installed
    # QueryRewriter is an optional, opt-in enhancement (see "Fail-open
    # contract" below) that the server never constructs by default, so a
    # missing `openai` package must not break importing this module (and
    # therefore the whole engine/server) for callers who never use it.
    OpenAI = None  # type: ignore[assignment,misc]

load_query_rewriter_env()

DEFAULT_MODEL = "qwen-plus"
DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_REWRITES = 2

SYSTEM_PROMPT = """
You are a query-rewriting assistant for a personal memory search system.

Given the user's search query, produce up to two alternate phrasings that
preserve the original search intent but use different wording, synonyms,
or phrasing structure. These alternates will be used as additional search
queries alongside the original, to improve recall.

Rules:

1. Preserve the original meaning and intent exactly. Do not add, remove,
   or speculate about information not implied by the query.
2. Each rewrite must be a short search phrase, not a full sentence or an
   answer to the query.
3. Do not repeat the original query verbatim as a rewrite.
4. Produce zero, one, or two rewrites — never more than two.
5. Return ONLY valid JSON, no markdown fences, no commentary.

Return JSON in exactly this shape:

{
    "rewrites": ["first alternate phrasing", "second alternate phrasing"]
}
"""


@dataclass(frozen=True)
class RewriteResult:
    """The outcome of rewriting a single query.

    Parameters
    ----------
    original : str
        The caller's exact input string, unchanged.
    rewrites : tuple[str, ...]
        Up to two alternate search phrases. Empty whenever rewriting was
        skipped, failed, or the model declined to produce any (see the
        module's "Fail-open contract").

    Examples
    --------
    >>> result = RewriteResult(original="haven and claude", rewrites=("claude and haven",))
    >>> result.queries
    ('haven and claude', 'claude and haven')
    """

    original: str
    rewrites: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.rewrites) > MAX_REWRITES:
            raise ValueError(
                f"rewrites must contain at most {MAX_REWRITES} entries; "
                f"got {len(self.rewrites)}"
            )

    @property
    def queries(self) -> Tuple[str, ...]:
        """Return ``(original,) + rewrites`` — every query to search with."""
        return (self.original,) + self.rewrites


def _normalize_query(query: str) -> str:
    """Lowercase and strip *query* for cache-key/dedup comparisons.

    Deliberately mirrors :func:`obsidian.ontology.text_utils.normalize`'s
    definition without importing it (see module "Design decisions").
    """
    return query.strip().lower()


def _resolve_model(model: Optional[str] = None) -> str:
    """Resolve the rewriter model from an explicit arg, env var, or default."""
    return model or os.environ.get("QUERY_REWRITER_MODEL", DEFAULT_MODEL)


def _resolve_timeout() -> float:
    """Resolve the per-request timeout (seconds) from an env var or default."""
    raw = os.environ.get("QUERY_REWRITER_TIMEOUT_SECONDS")
    if raw is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _get_client() -> OpenAI:
    """Build an OpenAI-compatible client for query rewriting.

    The API key is never hardcoded; it must be supplied via the
    ``QUERY_REWRITER_API_KEY`` environment variable. Raises ``RuntimeError``
    if it is unset — callers in this module treat that as a fail-open
    signal, not a fatal error.
    """
    api_key = os.environ.get("QUERY_REWRITER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "QUERY_REWRITER_API_KEY environment variable is not set."
        )
    if OpenAI is None:
        raise RuntimeError("the 'openai' package is not installed.")
    base_url = os.environ.get("QUERY_REWRITER_BASE_URL", DEFAULT_BASE_URL)
    return OpenAI(api_key=api_key, base_url=base_url)


def _parse_rewrites(raw_text: str, original: str) -> Tuple[str, ...]:
    """Parse the model's JSON response into a validated rewrite tuple.

    Raises on any malformed or unexpected shape (missing key, wrong type,
    invalid JSON); callers are expected to treat *any* exception from this
    function as "no rewrites" per the fail-open contract.

    Rewrites that are empty after stripping, duplicate the original query
    (case-/whitespace-insensitively), or duplicate an earlier rewrite in
    the same response are dropped. The result is truncated to
    :data:`MAX_REWRITES` entries.
    """
    data = json.loads(raw_text)
    raw_rewrites = data["rewrites"]
    if not isinstance(raw_rewrites, list):
        raise TypeError("'rewrites' must be a JSON array")

    seen = {_normalize_query(original)}
    cleaned: List[str] = []
    for item in raw_rewrites:
        if not isinstance(item, str):
            raise TypeError("every 'rewrites' entry must be a string")
        phrase = item.strip()
        if not phrase:
            continue
        key = _normalize_query(phrase)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(phrase)
        if len(cleaned) == MAX_REWRITES:
            break
    return tuple(cleaned)


class QueryRewriter:
    """Produces up to two alternate phrasings of a query via an LLM.

    Reusable across multiple calls to :meth:`rewrite`; maintains an
    internal cache keyed by normalised query text (see module
    "Determinism").

    Parameters
    ----------
    model : str, optional
        Overrides ``QUERY_REWRITER_MODEL``/:data:`DEFAULT_MODEL` for every
        call made through this instance.

    Examples
    --------
    >>> rewriter = QueryRewriter()
    >>> result = rewriter.rewrite("What database did I pick for Atlas?")  # doctest: +SKIP
    >>> result.original
    'What database did I pick for Atlas?'
    """

    def __init__(self, model: Optional[str] = None) -> None:
        self._model = model
        self._cache: Dict[str, Tuple[str, ...]] = {}

    def rewrite(self, query: str) -> RewriteResult:
        """Return *query* plus up to two alternate phrasings.

        Parameters
        ----------
        query : str
            The raw user query. Returned verbatim as
            :attr:`RewriteResult.original` regardless of outcome.

        Returns
        -------
        RewriteResult
            ``original`` is always *query* unchanged. ``rewrites`` holds
            up to two alternate phrases on a successful LLM round trip,
            and is empty whenever *query* is blank/whitespace-only or
            rewriting failed for any reason (missing API key, timeout,
            malformed JSON, or any other API error — see the module's
            "Fail-open contract").
        """
        key = _normalize_query(query)

        if not key:
            return RewriteResult(original=query, rewrites=())

        if key not in self._cache:
            self._cache[key] = self._fetch_rewrites(query)

        return RewriteResult(original=query, rewrites=self._cache[key])

    def _fetch_rewrites(self, query: str) -> Tuple[str, ...]:
        """Call the LLM for rewrites of *query*, failing open on any error.

        A single broad ``except Exception`` is intentional here — see the
        module's "Fail-open contract" for why this method must never raise,
        regardless of which of the many possible failure modes (missing
        key, timeout, connection error, HTTP error, empty/malformed
        response body, unexpected JSON shape, ...) occurs.
        """
        try:
            client = _get_client()
            response = client.chat.completions.create(
                model=_resolve_model(self._model),
                timeout=_resolve_timeout(),
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": query},
                ],
            )
            raw_text = response.choices[0].message.content
            return _parse_rewrites(raw_text, query)
        except Exception:
            return ()


# ---------------------------------------------------------------------------
# RewriteSuggester -- the browser extension's typing-time suggestion
# ---------------------------------------------------------------------------
#
# A different question from QueryRewriter's above: QueryRewriter always
# tries to produce alternate phrasings, because more differently-worded
# queries only ever help retrieval recall -- there's no such thing as a
# query "too clear to benefit" from extra search variants under the hood.
# The extension's UI has the opposite requirement (see
# ``obsidian.server.schemas.QueryRewriteSuggestionResponse`` and
# ``content/rewrite-suggestion.js``): show a suggestion only when it would
# *meaningfully change what the user should search for*, never as a
# paraphrase-everything nag. "Is this draft ambiguous enough to be worth
# rewriting" is a judgement call that depends on meaning, not on any
# surface property (length, punctuation, keyword match) of the text, so it
# is delegated to the same LLM in a single call, asked directly for a
# yes/no decision alongside its rewrite -- one round trip, same latency and
# cost profile as QueryRewriter.rewrite(), just a different prompt/parse.
SUGGESTION_SYSTEM_PROMPT = """
You are a retrieval-suggestion assistant for a personal memory search system.

The user is typing a query into a personal memory assistant that retrieves
their own earlier notes, decisions, and conversations. Some queries are
already specific and self-contained -- searching with them as-is works
fine. Others are vague, depend on conversational context, or reference
"it"/"that"/"the project" without saying what they mean -- these would
retrieve much better results if rewritten into an explicit, self-contained
search query first.

Decide whether THIS query would meaningfully benefit from being rewritten
before being used to search. Say yes only when a rewrite would change what
gets retrieved for the better -- not for every query that could in theory
be phrased differently.

Say yes for queries like:
- vague continuations ("continue", "keep going", "where did we leave off")
- pronouns/references with no antecedent in the query itself ("go back to
  that thing we discussed", "catch me up on the project")
- asking to recall a past decision or state without naming what it was
  ("what did we finally decide", "what's the latest architecture we
  settled on")

Say no for queries like:
- already-specific, self-contained questions, even short ones ("what is
  python?", "explain recursion", "how does quicksort work", "what is
  oauth?")
- requests with no dependency on retrieving anything from memory ("convert
  5 km to miles", "summarize this paragraph", "write a haiku")

Rules:

1. Do not invent facts or guess at unstated details when writing a
   rewrite -- only make the existing intent explicit.
2. A rewrite must be a short search phrase, not a full sentence or an
   answer to the query.
3. Return ONLY valid JSON, no markdown fences, no commentary.

Return JSON in exactly this shape:

{
    "needs_rewrite": true or false,
    "rewrite": "explicit, retrieval-oriented phrasing" or null
}

"rewrite" must be null when "needs_rewrite" is false, and a non-empty
string when it is true.
"""


@dataclass(frozen=True)
class SuggestionResult:
    """The outcome of deciding whether to suggest a rewrite for one query.

    Parameters
    ----------
    original : str
        The caller's exact input string, unchanged.
    suggestion : str, optional
        The suggested rewrite, or ``None`` when no suggestion is worth
        showing (query already clear, or rewriting was skipped/failed --
        see the module's "Fail-open contract").
    """

    original: str
    suggestion: Optional[str] = None

    @property
    def changed(self) -> bool:
        """Whether there is a suggestion worth showing the user."""
        return self.suggestion is not None

    @property
    def rewritten(self) -> str:
        """The suggestion if there is one, else *original* unchanged."""
        return self.suggestion if self.suggestion is not None else self.original


def _parse_suggestion(raw_text: str, original: str) -> Optional[str]:
    """Parse the model's JSON response into an optional suggestion string.

    Raises on any malformed or unexpected shape; callers are expected to
    treat *any* exception from this function as "no suggestion" per the
    fail-open contract. Returns ``None`` whenever ``needs_rewrite`` is
    false, or the model's ``rewrite`` is blank or identical to *original*
    (case-/whitespace-insensitively) -- a model that says "yes, rewrite"
    but then echoes the input back has not actually proposed anything.
    """
    data = json.loads(raw_text)
    needs_rewrite = data["needs_rewrite"]
    if not isinstance(needs_rewrite, bool):
        raise TypeError("'needs_rewrite' must be a JSON boolean")
    if not needs_rewrite:
        return None

    rewrite = data.get("rewrite")
    if not isinstance(rewrite, str):
        raise TypeError("'rewrite' must be a string when needs_rewrite is true")
    phrase = rewrite.strip()
    if not phrase or _normalize_query(phrase) == _normalize_query(original):
        return None
    return phrase


class RewriteSuggester:
    """Decides whether a compose-box draft is worth suggesting a rewrite for.

    Reusable across multiple calls to :meth:`suggest`; maintains an
    internal cache keyed by normalised query text, same contract as
    :class:`QueryRewriter` (see that class's docstring).

    Parameters
    ----------
    model : str, optional
        Overrides ``QUERY_REWRITER_MODEL``/:data:`DEFAULT_MODEL` for every
        call made through this instance. Shares :class:`QueryRewriter`'s
        environment configuration (``QUERY_REWRITER_API_KEY``/
        ``_BASE_URL``/``_MODEL``/``_TIMEOUT_SECONDS``) -- same LLM backend,
        just a different prompt for a different decision.
    """

    def __init__(self, model: Optional[str] = None) -> None:
        self._model = model
        self._cache: Dict[str, Optional[str]] = {}

    def suggest(self, query: str) -> SuggestionResult:
        """Return *query* plus an optional suggested rewrite.

        Parameters
        ----------
        query : str
            The raw compose-box draft. Returned verbatim as
            :attr:`SuggestionResult.original` regardless of outcome.

        Returns
        -------
        SuggestionResult
            ``suggestion`` is set only when the model judged the query
            worth rewriting; ``None`` whenever *query* is blank/
            whitespace-only, the model judged it already clear, or
            anything about the call failed (see the module's "Fail-open
            contract").
        """
        key = _normalize_query(query)

        if not key:
            return SuggestionResult(original=query, suggestion=None)

        if key not in self._cache:
            self._cache[key] = self._fetch_suggestion(query)

        return SuggestionResult(original=query, suggestion=self._cache[key])

    def _fetch_suggestion(self, query: str) -> Optional[str]:
        """Call the LLM for a suggestion, failing open on any error.

        Single broad ``except Exception``, same reasoning as
        :meth:`QueryRewriter._fetch_rewrites`.
        """
        try:
            client = _get_client()
            response = client.chat.completions.create(
                model=_resolve_model(self._model),
                timeout=_resolve_timeout(),
                messages=[
                    {"role": "system", "content": SUGGESTION_SYSTEM_PROMPT},
                    {"role": "user", "content": query},
                ],
            )
            raw_text = response.choices[0].message.content
            return _parse_suggestion(raw_text, query)
        except Exception:
            return None
