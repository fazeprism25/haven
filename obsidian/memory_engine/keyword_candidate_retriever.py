"""Deterministic keyword-based candidate retrieval for the Haven Memory Engine.

This module has exactly one responsibility: given a query string and a
collection of :class:`~obsidian.manager_ai.models.KnowledgeObject`
instances, return the subset whose ``canonical_fact`` shares at least one
keyword with the query.

::

    query, KnowledgeObject[]
        │
        ▼
    KeywordCandidateRetriever   (this module)
        │
        ▼
    KnowledgeObject[]

Explicitly out of scope
------------------------
* **No ranking.** This module reports how strong a keyword match is (see
  "Keyword-overlap scoring" below) but never reorders, filters by, or acts
  on that number — combining it with every other evidence source into a
  final ordering is exclusively
  :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s
  responsibility. The only ordering this module imposes on its own output
  is a deterministic tie-break (see "Determinism" below), not a relevance
  ranking.
* **No slot allocation.** No context budget or truncation logic — that is
  :class:`~obsidian.memory_engine.deterministic_slot_allocator.DeterministicSlotAllocator`'s
  responsibility.
* **No context formatting.** No prompt assembly or string rendering.
* **No graph traversal, no ontology imports.** This module never imports
  from :mod:`obsidian.ontology` and has no notion of Concepts, activation,
  or attachment relevance. It matches purely on the surface text of
  ``canonical_fact``.
* **No embeddings, no LLM calls, no fuzzy matching.** Matching is exact,
  case-insensitive keyword-token equality computed locally and
  synchronously — nothing is sent to a model or scored by similarity. The
  overlap score below is arithmetic over token sets already produced by
  this exact-match tokeniser, not a similarity model.

Keyword-overlap scoring
------------------------
Every match additionally carries a ``keyword_overlap_score`` in
``[0.0, 1.0]`` (see :class:`KeywordMatch`), a deterministic measure of how
strong that keyword match is — distinct from *whether* it matched, which
is all :meth:`retrieve` alone can express. Three signals feed it, all
computed locally from the same call's inputs, with no state carried
between calls:

1. **How many query keywords overlap the fact** — more overlapping
   tokens raises the score.
2. **How rare each overlapping token is across the given candidate pool**
   — a token that appears in only one or two candidates is much stronger
   evidence than one that appears in half of them, so rarer tokens count
   for more. Rarity is measured as inverse document frequency (IDF) over
   *this call's* ``knowledge_objects`` — see :func:`_idf_weights`.
3. **Exact phrase overlap** — if two or more of the query's tokens appear
   as a contiguous, same-order run inside the fact's own token sequence
   (e.g. the query and fact both contain the adjacent pair
   ``"second brain"``, not just both containing "second" and "brain"
   somewhere), a fixed bonus is added on top of the token-overlap score,
   rewarding a precise phrase match over an equivalent bag-of-words match.
   Deliberately keyed on the *overlapping* tokens' own order, not the
   whole query verbatim — see :func:`_has_multiword_phrase_overlap` and
   "Design decisions" below for why.

See :func:`_keyword_overlap_score` for the exact formula.

Matching semantics
------------------
The query and every candidate's ``canonical_fact`` are tokenised into
lowercase alphanumeric tokens (punctuation and whitespace are delimiters,
except that a contraction/possessive apostrophe — ``"what's"``,
``"Haven's"``, ``"don't"`` — is resolved to its underlying word rather
than left as a separate one- or two-letter fragment; see
:func:`_strip_clitic` and "Design decisions" below). Each token is then
normalised against a small, explicit table of known inflectional variants
(``"projects"`` -> ``"project"``, ``"working"`` -> ``"work"`` — see
:data:`_VARIANT_GROUPS` and "Design decisions" below), on
*both* sides of the match, so ``"project"`` in a query matches
``"projects"`` in a fact and vice versa. Stop words (``a``, ``the``,
``is``, ``my``, ``who`` — the same list as
:data:`obsidian.ontology.text_utils.STOP_WORDS`, see "Design decisions"
below) are then removed from the query's token set only. A candidate is
returned if its (normalised, unfiltered) token set intersects the
query's (normalised, stop-word filtered) keyword set — i.e. this is an
*any-keyword-matches* (OR) search appropriate for a recall-oriented
candidate-generation stage, not an *all-keywords-match* (AND) filter. A
query that tokenises to no keywords, or whose every token is a stop word
(e.g. ``"what is this"``, or punctuation-only input), matches nothing,
since there is nothing content-bearing left to search for.

Determinism
-----------
* **Search order does not affect the result set.** Every candidate is
  tested independently against the query; nothing short-circuits.
* **Output order is independent of input order.** The matched candidates
  are sorted by ascending ``str(knowledge_object.id)`` before being
  returned, the same tie-break convention already used by
  :class:`~obsidian.ontology.retrieval_models.RankedCandidate` and
  :meth:`~obsidian.memory_engine.memory_store.MemoryStore.all`. Calling
  :meth:`KeywordCandidateRetriever.retrieve` twice with the same query
  against the same candidates in a different input order yields identical
  output.
* **Tokenisation is a pure function of its input string.** No locale-,
  time-, or state-dependent behaviour.

Design decisions
-----------------
* **Normalisation is duplicated locally rather than imported.** A
  general-purpose tokeniser already exists at
  :mod:`obsidian.ontology.text_utils`, and duplicating logic is normally
  avoided. However, this module's hard architectural boundary is "no
  ontology imports" (matching the same boundary already drawn by
  :mod:`~obsidian.memory_engine.memory_store`, which documents and tests —
  see ``TestNoConceptImports`` in ``test_memory_store.py`` — that it never
  imports :mod:`obsidian.ontology`). Between those two competing
  instructions, the architectural boundary wins: this module defines its
  own minimal, private tokeniser rather than reaching into the Ontology
  subsystem. This mirrors existing precedent in
  :class:`~obsidian.manager_ai.canonical_matcher.CanonicalMatcher`, which
  likewise inlines its own ``.strip().lower()`` normalisation rather than
  importing :mod:`obsidian.ontology.text_utils`. The tokeniser here is
  intentionally tiny (one compiled regex) so the duplication is as small
  as possible.
* **Query-side stop words are filtered; the same boundary applies.**
  :mod:`obsidian.ontology.text_utils` already defines ``STOP_WORDS`` and a
  read-path tokeniser (``tokenize_query``) that removes them, precisely to
  reduce match noise at query time — the same problem this module has.
  For the same "no ontology imports" reason ``_tokenize`` is duplicated
  rather than imported, ``_STOP_WORDS`` below duplicates
  ``obsidian.ontology.text_utils.STOP_WORDS``'s *content* rather than
  importing it; ``test_keyword_candidate_retriever.py`` asserts the two
  sets stay identical so this duplication cannot silently drift. Only the
  query side is filtered — ``canonical_fact`` tokens are left exactly as
  ``_tokenize`` produces them, mirroring ``tokenize_query`` (read path,
  filtered) vs. ``tokenize_label`` (write path, unfiltered) in
  :mod:`obsidian.ontology.text_utils`. Filtering the fact side would have
  no observable effect anyway, since matching is intersection against the
  already-filtered query keyword set — a stop word absent from that set
  can never contribute a match regardless of whether it remains in a
  fact's own token set.
* **Inflectional variants are a closed, hand-curated table, not a
  stemmer.** A general suffix-stripping stemmer (drop trailing ``"s"``,
  ``"-ing"``, ``"-ed"``) was considered and rejected: Haven's own domain
  vocabulary includes ``"Atlas"``, ``"Postgres"``, and ``"Kubernetes"`` —
  all end in ``"s"`` without being plurals, and any of the standard
  suffix-stripping heuristics would corrupt them (e.g. ``"atlas"`` ->
  ``"atla"``). :data:`_VARIANT_GROUPS` can only ever normalise a token
  that some group's author explicitly decided was safe to normalise,
  which makes it impossible for this step to silently mangle a word
  nobody has reviewed. This is the deliberately conservative trade-off:
  it fixes exactly the variant pairs it's told about and nothing else,
  rather than fixing every regular English inflection at the risk of
  breaking irregular or non-inflectional words that merely look similar.
* **Contraction/possessive clitics are consumed at the tokeniser level, not
  filtered afterward as stop words.** An earlier version of this module
  treated the apostrophe as a plain delimiter, so ``"what's"`` tokenised
  to ``["what", "s"]`` and ``"Haven's"`` to ``["haven", "s"]`` — a bare
  ``"s"`` (or ``"t"``, ``"m"``, ...) is not a stop word, so it survived
  query-side filtering and matched *any* fact containing its own
  unrelated possessive, with a keyword-overlap score that could reach
  ``1.0`` when every other query keyword happened to be absent from the
  corpus (see ``docs/architecture/ACCEPTANCE_STAGE_DESIGN.md``'s companion
  investigation for the full trace). Rather than special-casing
  ``_STOP_WORDS`` with these fragments — which would still leave the
  question of *why* a meaningless single-letter token exists to filter in
  the first place — :data:`_TOKEN_RE` now captures the apostrophe and
  what follows it as part of the *same* match as the preceding word, and
  :func:`_strip_clitic` drops the suffix only when it is a known clitic
  (:data:`_CLITIC_SUFFIXES`). A fragment like ``"s"`` therefore never
  exists as its own token to begin with, on either side of the match.
* **Both sides are normalised; only the query is stop-word filtered.**
  Unlike stop-word removal (query-only, since a stop word absent from the
  query keyword set can never match regardless of the fact side),
  variant normalisation has to run on both sides — a fact's ``"projects"``
  and a query's ``"project"`` only become the same string if both are
  mapped to the same canonical form.
* **Token-set intersection, not substring search.** Substring matching
  would silently behave like fuzzy matching (e.g. ``"claud"`` matching
  ``"claude"``), which is explicitly out of scope. Splitting both sides
  into token sets and checking for intersection keeps matches exact and
  whole-word.
* **``canonical_fact`` only.** ``KnowledgeObject`` has other text-bearing
  fields (``metadata``), but the requirement scopes searching to
  ``canonical_fact`` specifically, so nothing else is inspected.
* **IDF is computed per call, over the given candidate pool, not stored.**
  There is no persistent term-frequency index anywhere in Haven; adding
  one would be a real redesign of this module's statelessness. Computing
  document frequency from ``knowledge_objects`` fresh on every call is
  more work per call but requires no new state, no invalidation on
  writes, and keeps :meth:`retrieve_with_scores` a pure function of its
  two arguments — consistent with every other guarantee in this module.
* **A token missing from the candidate pool contributes zero weight, not
  a maximal "unseen word" bonus.** A query token that appears in none of
  ``knowledge_objects`` has no evidence about its rarity, so it is treated
  as contributing nothing to either the overlap score or the
  normalising total, rather than assuming it is maximally rare. This
  keeps the score bounded and avoids rewarding typos or off-topic query
  words with an inflated rarity bonus.
* **The phrase bonus is additive and fixed, not multiplicative or
  learned.** A small constant (:data:`_PHRASE_MATCH_BONUS`) keeps the
  score's behaviour easy to reason about and testable in isolation,
  rather than introducing a second free parameter that interacts with the
  overlap ratio.
* **Phrase adjacency is checked on the overlapping tokens' own order, not
  the whole query string.** An earlier version of this scoring function
  checked whether the entire (lightly normalised) query text appeared
  verbatim in the fact text. That check only ever fires when *every*
  query token — including ones with no match anywhere in the corpus — is
  present in the fact, which makes it redundant with a full-overlap
  IDF ratio (already ``1.0``) in the only cases it can fire, and
  never fires at all for realistic multi-word queries that share a
  precise sub-phrase with a fact but also contain other, unmatched words
  ("Tell me about Project Atlas" vs. a fact containing "Project Atlas"
  verbatim but not the words "tell", "me", or "about"). Checking
  contiguous order over just the tokens that actually overlapped fixes
  this: it rewards precise phrasing exactly when it's informative, and is
  a no-op (returns ``False``) whenever fewer than two tokens overlap.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Pattern, Set

from obsidian.manager_ai.models import KnowledgeObject

# Compiled once at import time; mirrors the tokenisation shape used
# elsewhere in the codebase (lowercase alphanumeric runs), reimplemented
# locally per the "no ontology imports" design decision above. The
# trailing optional group keeps a "'" plus what follows it attached to the
# preceding alphanumeric run as a single match, so an English contraction
# or possessive (e.g. "what's", "Haven's") is never split into two
# `findall` matches in the first place -- see :func:`_strip_clitic` and
# "Design decisions" below for why that matters.
_TOKEN_RE: Pattern[str] = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")

# English clitics that can follow an apostrophe in a contraction or
# possessive. Not a general apostrophe-handling grammar -- just the closed
# set of suffixes :func:`_strip_clitic` recognises as "not a word on their
# own" and drops, the same closed-table philosophy already used by
# :data:`_VARIANT_GROUPS` below.
_CLITIC_SUFFIXES: FrozenSet[str] = frozenset({"s", "t", "m", "re", "ve", "ll", "d"})

# Content-identical duplicate of obsidian.ontology.text_utils.STOP_WORDS —
# see "Design decisions" above for why this is copied rather than
# imported, and test_keyword_candidate_retriever.py::TestStopWordParity
# for the cross-check that keeps the two lists in sync.
_STOP_WORDS: FrozenSet[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "is",
        "it",
        "its",
        "was",
        "are",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "may",
        "might",
        "must",
        "can",
        "could",
        "not",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "this",
        "that",
        "these",
        "those",
        "my",
        "your",
        "his",
        "her",
        "our",
        "their",
        "what",
        "which",
        "who",
        "whom",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "every",
        "both",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "only",
        "same",
        "so",
        "than",
        "too",
        "very",
        "about",
        "also",
        "just",
        "into",
        "then",
    }
)


# Explicit, hand-curated inflectional-variant groups, keyed by canonical
# form. Deliberately a closed table rather than algorithmic suffix
# stripping (e.g. "strip a trailing 's'", "strip '-ing'"): Haven's own
# domain vocabulary contains proper nouns that end in "s" but are not
# plurals -- "Atlas", "Postgres", "Kubernetes" -- and generic stemming
# rules would mangle all three (e.g. "atlas" -> "atla"). A closed table
# has no such failure mode: a token that isn't listed here is never
# touched, so this can only ever fix a known, named variant pair, never
# silently corrupt an unrelated word. Extend by adding more entries here;
# each new group should be reviewed for the same false-positive risk.
#
# Groups below are organised in five reviewed batches, each added for the
# reason given, none touching "atlas"/"postgres"/"kubernetes" (the module's
# own running example of words a generic stemmer would corrupt -- see
# above -- deliberately left alone rather than given aliases, so that
# example keeps meaning what it says).
_VARIANT_GROUPS: Dict[str, FrozenSet[str]] = {
    # --- Original five ---
    "project": frozenset({"project", "projects"}),
    "task": frozenset({"task", "tasks"}),
    "work": frozenset({"work", "works", "working", "worked"}),
    "build": frozenset({"build", "builds", "building", "built"}),
    "commit": frozenset({"commit", "commits", "committing", "committed"}),

    # --- Batch 2: inflectional variants confirmed against the LEXICAL
    # candidate-generation failure bucket in
    # docs/architecture/CANDIDATE_GENERATION_DECISION.md ("Phase 1 --
    # Lexical normalization"); each recovers at least one benchmark case
    # in that audit's sample (see TestPhase1AuditConfirmedVariants's
    # regression tests for the exact query/fact pairs). ---
    "embedding": frozenset({"embedding", "embeddings"}),
    "model": frozenset(
        {"model", "models", "modeling", "modelling", "modeled", "modelled"}
    ),
    "benchmark": frozenset({"benchmark", "benchmarks"}),
    "dataset": frozenset({"dataset", "datasets"}),
    "host": frozenset({"host", "hosts", "hosting", "hosted"}),
    "payment": frozenset({"payment", "payments"}),
    "system": frozenset({"system", "systems"}),
    "secret": frozenset({"secret", "secrets"}),
    "current": frozenset({"current", "currently"}),
    "learn": frozenset({"learn", "learns", "learning", "learned"}),
    "combine": frozenset({"combine", "combines", "combining", "combined"}),
    "evaluate": frozenset(
        {
            "evaluate",
            "evaluates",
            "evaluating",
            "evaluated",
            "evaluation",
            "evaluations",
        }
    ),
    "optimize": frozenset(
        {
            "optimize",
            "optimizes",
            "optimizing",
            "optimized",
            "optimization",
            "optimizations",
            "optimise",
            "optimises",
            "optimising",
            "optimised",
            "optimisation",
            "optimisations",
        }
    ),
    "prefer": frozenset(
        {
            "prefer",
            "prefers",
            "preferring",
            "preferred",
            "preference",
            "preferences",
            "prefs",
        }
    ),

    # --- Batch 3: well-established technology abbreviations/aliases.
    # Each pair is a single, unambiguous, industry-standard shorthand for
    # its expansion in a technical/personal-notes corpus -- excluded are
    # abbreviations with a genuine second sense in everyday English
    # ("dev" as in "a developer" vs. "development"; "temp" as in
    # "temperature" vs. "temporary"; "admin" as a person vs. a process),
    # since conflating two senses is a different, worse failure mode than
    # an inflectional miss. ---
    "api": frozenset({"api", "apis"}),
    "repo": frozenset({"repo", "repos", "repository", "repositories"}),
    "config": frozenset({"config", "configs", "configuration", "configurations"}),
    "database": frozenset({"database", "databases", "db", "dbs"}),
    "auth": frozenset(
        {"auth", "authenticate", "authenticates", "authenticated", "authenticating", "authentication"}
    ),
    "environment": frozenset({"environment", "environments", "env", "envs"}),
    "infrastructure": frozenset({"infrastructure", "infra"}),
    "specification": frozenset({"specification", "specifications", "spec", "specs"}),
    "application": frozenset({"application", "applications", "app", "apps"}),
    "production": frozenset({"production", "prod"}),
    "javascript": frozenset({"javascript", "js"}),
    "typescript": frozenset({"typescript", "ts"}),
    "backend": frozenset({"backend", "backends"}),
    "frontend": frozenset({"frontend", "frontends"}),
    "info": frozenset({"info", "information"}),
    "misc": frozenset({"misc", "miscellaneous"}),

    # --- Batch 4: American/British spelling variants. ---
    "color": frozenset({"color", "colors", "colour", "colours"}),
    "behavior": frozenset({"behavior", "behaviors", "behaviour", "behaviours"}),
    "license": frozenset(
        {"license", "licenses", "licensed", "licensing", "licence", "licences", "licenced", "licencing"}
    ),
    "organize": frozenset(
        {
            "organize",
            "organizes",
            "organizing",
            "organized",
            "organization",
            "organizations",
            "organise",
            "organises",
            "organising",
            "organised",
            "organisation",
            "organisations",
        }
    ),
    "analyze": frozenset(
        {
            "analyze",
            "analyzes",
            "analyzing",
            "analyzed",
            "analysis",
            "analyses",
            "analyse",
            "analysing",
            "analysed",
        }
    ),
    "customize": frozenset(
        {
            "customize",
            "customizes",
            "customizing",
            "customized",
            "customization",
            "customizations",
            "customise",
            "customises",
            "customising",
            "customised",
            "customisation",
            "customisations",
        }
    ),
    "favorite": frozenset({"favorite", "favorites", "favourite", "favourites"}),
    "cancel": frozenset(
        {"cancel", "cancels", "canceling", "cancelled", "canceled", "cancelling"}
    ),

    # --- Batch 5: residual inflectional variants confirmed against the
    # remaining LEXICAL candidate-generation failures in
    # docs/architecture/ENTITY_CAT_INVESTIGATION.md ("Recommendation 0" --
    # each pair was confirmed by monkeypatching this table and re-running
    # the real pipeline against the benchmark corpus, not just inspected by
    # eye). ---
    "name": frozenset({"name", "names", "naming", "named"}),
    "decide": frozenset({"decide", "decides", "deciding", "decided"}),
    "prioritize": frozenset(
        {
            "prioritize",
            "prioritizes",
            "prioritizing",
            "prioritized",
            "priority",
            "priorities",
        }
    ),
}

# Flattened variant -> canonical lookup, built once at import time.
_CANONICAL_FORM: Dict[str, str] = {
    variant: canonical
    for canonical, variants in _VARIANT_GROUPS.items()
    for variant in variants
}


def _normalize_token(token: str) -> str:
    """Map *token* to its canonical form if it is a known inflectional variant.

    Returns *token* unchanged if it is not listed in :data:`_VARIANT_GROUPS`.
    Pure function: same input always yields the same output.
    """
    return _CANONICAL_FORM.get(token, token)


def _strip_clitic(token: str) -> str:
    """Drop a trailing English contraction/possessive clitic from *token*.

    ``"what's"`` -> ``"what"``, ``"haven's"`` -> ``"haven"``, ``"don't"``
    -> ``"don"``. *token* is the output of a single :data:`_TOKEN_RE` match,
    so it contains at most one apostrophe, always preceded by at least one
    alphanumeric character (the regex requires it) — this only ever
    strips a suffix already anchored to a preceding word, never produces or
    consumes a bare fragment on its own. Returns *token* unchanged if it
    has no apostrophe, or if the text after its apostrophe is not in
    :data:`_CLITIC_SUFFIXES` (e.g. ``"o'brien"``, left intact). Pure
    function: same input always yields the same output.
    """
    base, separator, suffix = token.partition("'")
    if separator and suffix in _CLITIC_SUFFIXES:
        return base
    return token


def _tokenize(text: str) -> List[str]:
    """Split *text* into lowercase word tokens, normalising known variants.

    Non-alphanumeric, non-apostrophe characters (punctuation, whitespace)
    are delimiters and are discarded. :data:`_TOKEN_RE` keeps a trailing
    ``"'" + suffix`` attached to its preceding alphanumeric run as a single
    match — e.g. ``"Haven's"`` tokenises as one match, ``"haven's"``, never
    as the two independent tokens ``"haven"`` and ``"s"`` — so
    :func:`_strip_clitic` can then drop a recognised clitic suffix (see
    :data:`_CLITIC_SUFFIXES`) to yield the underlying word, ``"haven"``.
    This is what keeps a meaningless contraction fragment like ``"s"`` or
    ``"t"`` from ever becoming its own searchable token: it never exists as
    a standalone match to begin with, rather than being produced and then
    filtered out after the fact. Each token is then passed through
    :func:`_normalize_token` so that e.g. ``"projects"`` and ``"project"``
    tokenise to the same string on both the query and ``canonical_fact``
    side of matching. Pure function: same input always yields the same
    output.
    """
    return [
        _normalize_token(_strip_clitic(token))
        for token in _TOKEN_RE.findall(text.lower())
    ]


def _query_keywords(query: str) -> Set[str]:
    """Tokenise *query* and remove stop words.

    Only the query side is filtered — see "Design decisions" above.
    Pure function: same input always yields the same output.
    """
    return set(_tokenize(query)) - _STOP_WORDS


# Fixed bonus added to the token-overlap score when two or more query
# tokens appear as a contiguous, same-order run in the fact's own token
# sequence. See the module docstring's "Keyword-overlap scoring" and
# "Design decisions".
_PHRASE_MATCH_BONUS: float = 0.15


def _has_multiword_phrase_overlap(
    query_tokens: List[str], fact_tokens: List[str]
) -> bool:
    """True if some run of 2+ consecutive *query_tokens* also appears,
    in the same order, as a consecutive run in *fact_tokens*.

    Operates on already-tokenised (lowercase, variant-normalised) word
    sequences, not raw text, so punctuation differences never matter and
    the same normalisation used for overlap matching applies here too.
    Deliberately keyed on the *overlapping subsequence*, not the whole
    query: a query token with zero overlap anywhere in the corpus (a typo,
    an off-topic word) must not prevent an otherwise-exact phrase match on
    the words that did land. A single shared token is not a "phrase" — at
    least two consecutive tokens must match for this to fire, which is
    also why a fact and query sharing only one token can never receive
    this bonus regardless of :data:`_PHRASE_MATCH_BONUS`.

    Implementation note (performance): checking every window length from
    ``len(query_tokens)`` down to 2 is redundant. If a contiguous run of
    length L >= 2 matches at some position, its first two elements are
    themselves a matching contiguous run of length 2 (a shared bigram) at
    that same position — and conversely, a shared bigram *is* a length-2
    contiguous run, which already satisfies "some run of 2+". So "exists a
    matching run of length >= 2" is exactly equivalent to "exists a
    matching adjacent bigram"; only length-2 windows need to be checked.
    This turns an O(len(query_tokens)^2 * len(fact_tokens)) triple loop
    (catastrophic when ``query_tokens`` scales with vault size, e.g. a
    dashboard "digest query" built from every memory's own text) into a
    single O(len(query_tokens) + len(fact_tokens)) pass, with no change to
    the boolean this function returns for any input — see
    ``TestPhraseOverlapEquivalence`` in
    ``obsidian/tests/test_keyword_candidate_retriever.py`` for the
    brute-force cross-check.
    """
    if len(query_tokens) < 2:
        return False
    fact_bigrams = {
        (fact_tokens[i], fact_tokens[i + 1]) for i in range(len(fact_tokens) - 1)
    }
    if not fact_bigrams:
        return False
    return any(
        (query_tokens[i], query_tokens[i + 1]) in fact_bigrams
        for i in range(len(query_tokens) - 1)
    )


def _document_frequencies(knowledge_objects: List[KnowledgeObject]) -> Dict[str, int]:
    """Count how many of *knowledge_objects* contain each token at least once.

    A token repeated within one fact still counts once for that fact
    (document frequency, not raw term frequency). Pure function of its
    input list; order-independent.
    """
    return _document_frequencies_from_tokens(
        [_tokenize(ko.canonical_fact) for ko in knowledge_objects]
    )


def _document_frequencies_from_tokens(
    tokenized_facts: List[List[str]],
) -> Dict[str, int]:
    """Same computation as :func:`_document_frequencies`, over pre-tokenised facts.

    Factored out so a caller that must tokenise every fact anyway for its
    own purposes (:meth:`KeywordCandidateRetriever.retrieve_with_scores`
    also needs each fact's token sequence for matching/scoring) does not
    have to tokenise the whole pool a second time just to compute document
    frequencies -- :func:`_document_frequencies` itself is unchanged and
    still tokenises internally, for callers (and the existing test suite)
    that only have ``KnowledgeObject`` instances on hand.
    """
    frequencies: Dict[str, int] = {}
    for tokens in tokenized_facts:
        for token in set(tokens):
            frequencies[token] = frequencies.get(token, 0) + 1
    return frequencies


def _idf_weights(document_frequencies: Dict[str, int], corpus_size: int) -> Dict[str, float]:
    """Convert document frequencies into inverse-document-frequency weights.

    ``idf(token) = 1 + ln(corpus_size / df(token))``, ranging from ``1.0``
    (the token appears in every candidate — no rarity signal) up to
    ``1 + ln(corpus_size)`` (the token appears in exactly one candidate —
    maximally rare). Empty when *corpus_size* is ``0``.
    """
    if corpus_size <= 0:
        return {}
    return {
        token: 1.0 + math.log(corpus_size / df)
        for token, df in document_frequencies.items()
    }


def _keyword_overlap_score(
    query_keywords: Set[str],
    query_tokens_ordered: List[str],
    fact_tokens_ordered: List[str],
    idf_weights: Dict[str, float],
) -> float:
    """Score how strongly a fact's tokens match *query_keywords*, in ``[0.0, 1.0]``.

    ``score = (IDF-weighted overlap) / (IDF-weighted query keywords)``,
    plus :data:`_PHRASE_MATCH_BONUS` if two or more query tokens appear as
    a contiguous, same-order run in the fact's tokens (see
    :func:`_has_multiword_phrase_overlap`), capped at ``1.0``. Returns
    ``0.0`` if there is no overlap at all (including when *query_keywords*
    is empty). See the module docstring's "Keyword-overlap scoring" for
    the rationale behind each term.

    Parameters
    ----------
    query_keywords : set[str]
        Stop-word-filtered query keywords (see :func:`_query_keywords`) —
        drives the IDF-weighted overlap ratio.
    query_tokens_ordered : list[str]
        The query's full tokenised word sequence, stop words included, in
        original order — drives the phrase-adjacency check, where a
        stop word genuinely part of a phrase (e.g. "Tower of London")
        should still count.
    fact_tokens_ordered : list[str]
        The fact's full tokenised word sequence, in original order.
    idf_weights : dict[str, float]
        Corpus-wide IDF weights, see :func:`_idf_weights`.
    """
    fact_tokens = set(fact_tokens_ordered)
    overlap = query_keywords & fact_tokens
    if not overlap:
        return 0.0

    total_weight = sum(idf_weights.get(token, 0.0) for token in query_keywords)
    if total_weight <= 0.0:
        return 0.0

    overlap_weight = sum(idf_weights.get(token, 0.0) for token in overlap)
    score = overlap_weight / total_weight

    if _has_multiword_phrase_overlap(query_tokens_ordered, fact_tokens_ordered):
        score += _PHRASE_MATCH_BONUS

    return min(1.0, score)


@dataclass(frozen=True)
class KeywordMatch:
    """A single keyword-path match, paired with its overlap-strength score.

    Parameters
    ----------
    knowledge_object : KnowledgeObject
        The matched candidate.
    keyword_overlap_score : float
        Match strength in ``[0.0, 1.0]`` — see :func:`_keyword_overlap_score`.

    Raises
    ------
    ValueError
        If *keyword_overlap_score* is outside ``[0.0, 1.0]``.
    """

    knowledge_object: KnowledgeObject
    keyword_overlap_score: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.keyword_overlap_score <= 1.0:
            raise ValueError(
                "keyword_overlap_score must be in [0.0, 1.0]; "
                f"got {self.keyword_overlap_score}"
            )


class KeywordCandidateRetriever:
    """Retrieves ``KnowledgeObject`` candidates matching a query by keyword.

    Stateless and reusable across calls to :meth:`retrieve` with different
    queries or candidate collections.

    Examples
    --------
    >>> retriever = KeywordCandidateRetriever()
    >>> results = retriever.retrieve("claude", knowledge_objects)
    >>> all(isinstance(ko, KnowledgeObject) for ko in results)
    True
    """

    def retrieve(
        self,
        query: str,
        knowledge_objects: Iterable[KnowledgeObject],
    ) -> List[KnowledgeObject]:
        """Return every candidate whose ``canonical_fact`` shares a keyword with *query*.

        Defined as :meth:`retrieve_with_scores`'s matched objects, in the
        same order — this method's behaviour is unchanged by the
        existence of scoring; it just doesn't expose the score.

        Parameters
        ----------
        query : str
            The raw search string. Tokenised the same way as
            ``canonical_fact`` (lowercase alphanumeric tokens), then
            stop-word filtered — see the module docstring's "Matching
            semantics".
        knowledge_objects : Iterable[KnowledgeObject]
            The pool of candidates to search, typically
            :meth:`~obsidian.memory_engine.memory_store.MemoryStore.all`.
            Not mutated; never re-fetched or hydrated.

        Returns
        -------
        list[KnowledgeObject]
            Every candidate whose ``canonical_fact`` token set intersects
            the query's stop-word-filtered keyword set, sorted by
            ascending ``str(knowledge_object.id)`` for deterministic
            output regardless of input order. Empty if *query* contains no
            content-bearing keywords (no alphanumeric tokens, or every
            token is a stop word) or no candidate matches.
        """
        return [match.knowledge_object for match in self.retrieve_with_scores(query, knowledge_objects)]

    def retrieve_with_scores(
        self,
        query: str,
        knowledge_objects: Iterable[KnowledgeObject],
    ) -> List[KeywordMatch]:
        """Same matching as :meth:`retrieve`, paired with a keyword-overlap score.

        Parameters
        ----------
        query : str
            Same contract as :meth:`retrieve`.
        knowledge_objects : Iterable[KnowledgeObject]
            Same contract as :meth:`retrieve`. Consumed once and
            materialised internally (document frequencies require a full
            pass over the pool before any candidate can be scored).

        Returns
        -------
        list[KeywordMatch]
            One entry per candidate matched by :meth:`retrieve`'s own
            criteria, each carrying its ``keyword_overlap_score`` (see the
            module docstring's "Keyword-overlap scoring"). Sorted by
            ascending ``str(knowledge_object.id)``, the same tie-break
            :meth:`retrieve` uses. Empty under the same conditions
            :meth:`retrieve` returns empty.
        """
        keywords = _query_keywords(query)
        if not keywords:
            return []

        query_tokens_ordered = _tokenize(query)
        pool = list(knowledge_objects)
        # Each fact is tokenised exactly once here and reused for both the
        # document-frequency pass and the matching/scoring pass below,
        # rather than tokenising the whole pool twice (see
        # _document_frequencies_from_tokens's docstring).
        fact_tokens_by_position = [_tokenize(ko.canonical_fact) for ko in pool]
        idf_weights = _idf_weights(
            _document_frequencies_from_tokens(fact_tokens_by_position), len(pool)
        )

        matches = []
        for ko, fact_tokens_ordered in zip(pool, fact_tokens_by_position):
            if not (keywords & set(fact_tokens_ordered)):
                continue
            score = _keyword_overlap_score(
                keywords, query_tokens_ordered, fact_tokens_ordered, idf_weights
            )
            matches.append(KeywordMatch(knowledge_object=ko, keyword_overlap_score=score))

        return sorted(matches, key=lambda match: str(match.knowledge_object.id))
