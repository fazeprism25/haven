"""Shared text processing utilities for the Haven Ontology.

Both the write path (concept extraction from KnowledgeObjects) and the
read path (concept detection in user queries) must tokenise text in
exactly the same way.  Keeping a single tokeniser here prevents
mismatches that would silently degrade retrieval quality.

Design constraints
------------------
* Pure functions – no state, no I/O.
* No third-party dependencies – the standard library ``re`` module is
  sufficient for the tokenisation quality required by Phase 1.
* Stable output – the same input must always produce the same output so
  that concept indices built during ingestion remain valid at query time.
"""

from __future__ import annotations

import re
from typing import FrozenSet, List


# ---------------------------------------------------------------------------
# Stop-word list
# ---------------------------------------------------------------------------

STOP_WORDS: FrozenSet[str] = frozenset(
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
"""Frozenset of lowercase stop words removed by :func:`tokenize_query`.

Only :func:`tokenize_query` (the read path) removes stop words.
:func:`tokenize_label` (the write path) preserves every token so that
multi-word concept labels like ``"Memory Engine"`` remain intact.
"""


# ---------------------------------------------------------------------------
# Internal compiled pattern
# ---------------------------------------------------------------------------

_TOKEN_RE: re.Pattern[str] = re.compile(r"[a-z0-9]+")
"""Compiled regex that extracts lowercase alphanumeric token fragments."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """Return *text* lowercased and stripped of leading/trailing whitespace.

    Parameters
    ----------
    text : str
        The raw input string.

    Returns
    -------
    str
        The normalised string.

    Examples
    --------
    >>> normalize("  Haven  ")
    'haven'
    >>> normalize("Memory Engine")
    'memory engine'
    """
    return text.strip().lower()


def tokenize(text: str) -> List[str]:
    """Split *text* into lowercase alphanumeric tokens.

    Non-alphanumeric characters (punctuation, whitespace) act as
    delimiters and are discarded.  Numbers are preserved as tokens.

    Parameters
    ----------
    text : str
        The raw input string.

    Returns
    -------
    list[str]
        Ordered list of lowercase token strings.  Empty if *text*
        contains no alphanumeric characters.

    Examples
    --------
    >>> tokenize("Haven, Memory Engine!")
    ['haven', 'memory', 'engine']
    >>> tokenize("Phase 1 implementation")
    ['phase', '1', 'implementation']
    """
    return _TOKEN_RE.findall(text.lower())


def tokenize_query(text: str) -> List[str]:
    """Tokenise *text* and remove stop words.

    Used by the **read path** (query-time concept detection) to reduce
    noise in the token stream before matching against concept indices.

    Parameters
    ----------
    text : str
        The user's raw query string.

    Returns
    -------
    list[str]
        Ordered list of lowercase content-bearing tokens with stop words
        removed.

    Examples
    --------
    >>> tokenize_query("what is the memory engine")
    ['memory', 'engine']
    >>> tokenize_query("Haven retrieval quality")
    ['haven', 'retrieval', 'quality']
    """
    return [token for token in tokenize(text) if token not in STOP_WORDS]


def tokenize_label(label: str) -> List[str]:
    """Tokenise a concept label without removing stop words.

    Used by the **write path** (index-time concept registration) to
    produce the token index for a :class:`~obsidian.ontology.models.Concept`.

    Stop words are deliberately preserved here so that concept labels
    such as ``"Tower of London"`` or ``"State of the Art"`` are indexed
    completely, matching the full surface form.

    Parameters
    ----------
    label : str
        The canonical label of the concept.

    Returns
    -------
    list[str]
        Ordered list of lowercase tokens from the label.

    Examples
    --------
    >>> tokenize_label("Memory Engine")
    ['memory', 'engine']
    >>> tokenize_label("Tower of London")
    ['tower', 'of', 'london']
    """
    return tokenize(label)
