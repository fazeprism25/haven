"""Deterministic concept detector for :class:`~obsidian.manager_ai.models.KnowledgeObject` instances.

Phase 2D — Concept extraction layer.

The detector has exactly one responsibility: scan a
:class:`~obsidian.manager_ai.models.KnowledgeObject` and return an ordered,
deduplicated list of candidate concept *labels* — raw strings naming the
entities worth indexing in the Concept Graph.

Design constraints
------------------
* **No LLM** — purely rule-based; no model calls, no external I/O.
* **Deterministic** — identical inputs always produce identical outputs.
* **No graph logic** — the detector never constructs :class:`Concept` objects,
  derives UUIDs, or touches storage.  It produces only *labels*, leaving ID
  derivation to downstream stages (Validator / ConceptGraph).
* **Shared tokeniser** — uses :mod:`obsidian.ontology.text_utils` so the
  write path and the detection path tokenise text identically.
* **Order-preserving deduplication** — labels appear in first-seen order;
  the output list is stable across repeated calls.

Algorithm
---------
The detector applies a *capitalized-span* heuristic to
``knowledge.canonical_fact``:

1. **Span extraction** — split the text on whitespace.  Strip leading and
   trailing non-alphanumeric characters from each word (e.g. ``"Haven."``
   → ``"Haven"``).  Collect runs of consecutive words whose first character
   is an uppercase ASCII letter into a single multi-word span (e.g.
   ``"Memory"`` followed by ``"Engine"`` → ``"Memory Engine"``).

2. **Stop-word trimming** — remove leading and trailing tokens from each
   span whose normalised form appears in
   :data:`~obsidian.ontology.text_utils.STOP_WORDS` (e.g.
   ``"The Memory Engine"`` → ``"Memory Engine"``).

3. **Filtering** — discard labels that are empty after trimming, shorter
   than :data:`MIN_LABEL_CHARS` characters, or contain no alphabetic
   characters at all.

4. **Deduplication** — use the normalised form (lowercase + stripped) as
   the deduplication key so ``"Haven"`` and ``"HAVEN"`` collapse to the
   first occurrence while preserving the original casing.

5. **Return** — the ordered list of surviving labels.

Rationale
---------
Concepts in Haven are *named things* — projects (``Haven``), people
(``Siddhartha``), technologies (``Claude``, ``LLM``), organisations
(``DTU``).  In English prose these are consistently written with initial
uppercase letters.  ``canonical_fact`` values from the Manager AI pipeline
follow this convention, making the capitalized-span heuristic
**high-precision** at the cost of recall: it will miss concepts written
entirely in lowercase but will never fabricate a concept.  An LLM-backed
extractor can be substituted later without changing the
:class:`ConceptDetector` interface.

Limitations
-----------
* Sentence-initial words are capitalised by English convention even when
  they are not concepts.  Trimming :data:`~obsidian.ontology.text_utils.STOP_WORDS`
  handles the most common cases (``"The"``, ``"A"``, ``"I"``).  A
  sentence-initial proper noun (``"Siddhartha is building Haven"``) is
  correctly captured.
* All-uppercase abbreviations (``"LLM"``, ``"API"``, ``"DTU"``) are
  captured because their first character is an uppercase ASCII letter.
* Hyphenated tokens (``"Claude-3"``, ``"GPT-4"``) and tokens with internal
  digits (``"Python3"``) are treated as single words and captured if the
  first character is an uppercase letter.
* Detection operates only on ``canonical_fact``; the ``metadata`` dict and
  ``evidence_chain`` are not scanned.
"""

from __future__ import annotations

import re
from typing import List, Set

from obsidian.manager_ai.models import KnowledgeObject
from obsidian.ontology.text_utils import STOP_WORDS, normalize, strip_clitic


# ---------------------------------------------------------------------------
# Public constant
# ---------------------------------------------------------------------------

MIN_LABEL_CHARS: int = 2
"""Minimum number of characters a candidate label must have to be kept.

Labels shorter than this threshold are discarded after stop-word trimming.
The default of 2 allows two-character abbreviations such as ``"AI"`` while
rejecting lone letters.
"""

# ---------------------------------------------------------------------------
# Module-private compiled pattern
# ---------------------------------------------------------------------------

_STRIP_RE: re.Pattern[str] = re.compile(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$")
"""Strip leading/trailing non-alphanumeric characters from a word token.

Keeps internal characters intact so that hyphenated tokens (``"Claude-3"``)
and tokens with apostrophes (``"O'Brien"``) are preserved correctly.
"""

_SPAN_BREAK_CHARS: frozenset = frozenset(".,;:!?")
"""Characters that, when appearing as the final character of a raw word token,
force the current capitalized span to close before the next token is examined.

This ensures that comma- or period-separated concept names such as
``"Claude, Haven, and Qdrant"`` produce three separate spans (``"Claude"``,
``"Haven"``, ``"Qdrant"``) rather than one merged span (``"Claude Haven"``).
The break is detected on the *raw* token (before punctuation stripping) so
that span boundaries implied by punctuation are honoured even though the
punctuation itself is removed from the label.
"""


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _clean_word(word: str) -> str:
    """Strip leading and trailing non-alphanumeric characters from *word*.

    Examples
    --------
    >>> _clean_word("Haven.")
    'Haven'
    >>> _clean_word("(DTU)")
    'DTU'
    >>> _clean_word("Claude-3")
    'Claude-3'
    """
    return _STRIP_RE.sub("", word)


def _is_head_capital(word: str) -> bool:
    """Return ``True`` if *word* starts with an uppercase ASCII letter.

    Parameters
    ----------
    word : str
        A cleaned, non-empty word token.

    Examples
    --------
    >>> _is_head_capital("Haven")
    True
    >>> _is_head_capital("LLM")
    True
    >>> _is_head_capital("is")
    False
    >>> _is_head_capital("3D")
    False
    """
    return bool(word) and word[0].isupper() and word[0].isalpha()


def _extract_capitalized_spans(text: str) -> List[str]:
    """Extract runs of consecutive capitalized words from *text*.

    Splits *text* on whitespace, cleans each token, then collects
    contiguous runs of tokens whose first character is an uppercase ASCII
    letter.  Each run is joined with a single space to form a candidate
    span.

    Parameters
    ----------
    text : str
        Free-text input (typically ``KnowledgeObject.canonical_fact``).

    Returns
    -------
    list[str]
        Candidate spans in first-seen order.  May contain stop words at
        the leading or trailing position; see :func:`_trim_stop_words`.

    Examples
    --------
    >>> _extract_capitalized_spans("Siddhartha uses Haven for knowledge")
    ['Siddhartha', 'Haven']
    >>> _extract_capitalized_spans("The Memory Engine is core")
    ['The Memory Engine']
    """
    current: List[str] = []
    spans: List[str] = []

    for raw in text.split():
        # Detect a span-break *before* stripping punctuation so that
        # "Claude," and "Haven," are treated as separate spans even though
        # the comma is removed from the final label.
        breaks_span = bool(raw) and raw[-1] in _SPAN_BREAK_CHARS
        cleaned = _clean_word(raw)

        if cleaned and _is_head_capital(cleaned):
            current.append(cleaned)
            if breaks_span:
                spans.append(" ".join(current))
                current = []
        else:
            if current:
                spans.append(" ".join(current))
                current = []

    if current:
        spans.append(" ".join(current))

    return spans


def _is_stop_word_token(word: str) -> bool:
    """Return ``True`` if *word* is a stop word, contraction included.

    A plain stop word (``"the"``, ``"I"``) matches directly via
    :func:`normalize`. A contraction whose clitic-stripped form is a stop
    word (``"I'm"`` -> ``"I"``, ``"It's"`` -> ``"It"``) also counts — the
    whole token is dropped rather than reduced to its stripped form, since
    a bare pronoun/auxiliary contraction is never itself a concept name.
    A possessive on a real proper noun (``"Haven's"`` -> ``"Haven"``) does
    *not* match, since ``"haven"`` is not in :data:`STOP_WORDS`, so it is
    correctly left alone by this check.
    """
    return normalize(word) in STOP_WORDS or normalize(strip_clitic(word)) in STOP_WORDS


def _trim_stop_words(label: str) -> str:
    """Remove leading and trailing stop-word tokens from *label*.

    Only whole tokens are removed; internal stop words are kept because
    they may be part of a multi-word proper noun (e.g. ``"Tower of London"``
    would never be a span — ``"of"`` is lowercase and breaks the run —
    but defensive trimming protects against edge cases in unusual text).

    A token is also trimmed when it is a contraction of a stop word (e.g.
    ``"I'm"``, ``"It's"``, ``"That's"``) — see :func:`_is_stop_word_token`
    — so a sentence like ``"I'm building Haven"`` does not detect a bogus
    ``"I'm"`` concept purely because the capitalized-span heuristic (see
    the module docstring's "Limitations") has no notion of contractions.

    Parameters
    ----------
    label : str
        A candidate span, possibly starting with ``"The"``, ``"A"``, etc.

    Returns
    -------
    str
        The trimmed label, which may be empty if every token was a stop
        word.

    Examples
    --------
    >>> _trim_stop_words("The Memory Engine")
    'Memory Engine'
    >>> _trim_stop_words("I")
    ''
    >>> _trim_stop_words("I'm")
    ''
    >>> _trim_stop_words("Haven's")
    "Haven's"
    >>> _trim_stop_words("Haven")
    'Haven'
    """
    words = label.split()

    while words and _is_stop_word_token(words[0]):
        words = words[1:]

    while words and _is_stop_word_token(words[-1]):
        words = words[:-1]

    return " ".join(words)


def _is_valid_label(label: str) -> bool:
    """Return ``True`` if *label* is a plausible concept label.

    A label is valid when it:

    * has at least :data:`MIN_LABEL_CHARS` characters,
    * contains at least one alphabetic character.

    Parameters
    ----------
    label : str
        A trimmed candidate label.

    Examples
    --------
    >>> _is_valid_label("Haven")
    True
    >>> _is_valid_label("AI")
    True
    >>> _is_valid_label("X")   # too short
    False
    >>> _is_valid_label("123")  # no alpha
    False
    """
    if len(label) < MIN_LABEL_CHARS:
        return False
    if not any(c.isalpha() for c in label):
        return False
    return True


def _deduplicate(labels: List[str]) -> List[str]:
    """Remove duplicate labels, preserving first-seen order.

    Deduplication uses :func:`~obsidian.ontology.text_utils.normalize`
    (lowercase + strip) as the comparison key, so ``"Haven"`` and
    ``"HAVEN"`` collapse to the first occurrence while keeping its
    original casing.

    Parameters
    ----------
    labels : list[str]
        Ordered list of candidate labels, possibly containing duplicates.

    Returns
    -------
    list[str]
        Deduplicated list in first-seen order.
    """
    seen: Set[str] = set()
    result: List[str] = []

    for label in labels:
        key = normalize(label)
        if key not in seen:
            seen.add(key)
            result.append(label)

    return result


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class ConceptDetector:
    """Extract candidate concept labels from a
    :class:`~obsidian.manager_ai.models.KnowledgeObject`.

    The detector is **stateless** — it holds no configuration and can be
    instantiated without arguments.

    Detection is performed on ``knowledge.canonical_fact`` only.  No LLM
    is called; no graph or storage is accessed.  The returned labels are
    *candidates* — the caller decides which to promote to full
    :class:`~obsidian.ontology.models.Concept` objects.

    Class attributes
    ----------------
    MIN_LABEL_CHARS : int
        Minimum character count for a label to be kept (default: ``2``).

    Public methods
    --------------
    detect(knowledge) → list[str]
        Detect concept labels from a :class:`KnowledgeObject`.
    detect_from_text(text) → list[str]
        Detect concept labels from a raw text string.

    Examples
    --------
    >>> from obsidian.ontology.concept_detector import ConceptDetector
    >>> detector = ConceptDetector()
    >>> detector.detect_from_text("Siddhartha uses Haven for knowledge")
    ['Siddhartha', 'Haven']
    >>> detector.detect_from_text("The Memory Engine is the core of Haven")
    ['Memory Engine', 'Haven']
    >>> detector.detect_from_text("no concepts here at all")
    []
    """

    MIN_LABEL_CHARS: int = MIN_LABEL_CHARS

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, knowledge: KnowledgeObject) -> List[str]:
        """Detect candidate concept labels from *knowledge*.

        Delegates to :meth:`detect_from_text` using
        ``knowledge.canonical_fact`` as the input text.

        Parameters
        ----------
        knowledge : KnowledgeObject
            The knowledge object to analyse.

        Returns
        -------
        list[str]
            Ordered, deduplicated list of candidate concept labels.
            Empty list when no concepts are detected.

        Examples
        --------
        >>> from obsidian.manager_ai.models import KnowledgeObject
        >>> ko = KnowledgeObject(canonical_fact="Haven uses Claude daily")
        >>> ConceptDetector().detect(ko)
        ['Haven', 'Claude']
        """
        return self.detect_from_text(knowledge.canonical_fact)

    def detect_from_text(self, text: str) -> List[str]:
        """Detect candidate concept labels from a raw *text* string.

        Applies the capitalized-span heuristic described in the module
        docstring: extract spans → trim stop words → filter → deduplicate.

        Parameters
        ----------
        text : str
            Free-text input.  Typically ``KnowledgeObject.canonical_fact``
            but can be any string for testing or ad-hoc use.

        Returns
        -------
        list[str]
            Ordered, deduplicated list of candidate concept labels.
            Empty list when *text* is empty, whitespace-only, or contains
            no capitalised words surviving the filters.

        Examples
        --------
        >>> ConceptDetector().detect_from_text("")
        []
        >>> ConceptDetector().detect_from_text("DTU is in Copenhagen")
        ['DTU', 'Copenhagen']
        >>> ConceptDetector().detect_from_text("The Memory Engine is core")
        ['Memory Engine']
        """
        if not text or not text.strip():
            return []

        spans = _extract_capitalized_spans(text)
        trimmed = [_trim_stop_words(span) for span in spans]
        filtered = [label for label in trimmed if _is_valid_label(label)]
        return _deduplicate(filtered)
