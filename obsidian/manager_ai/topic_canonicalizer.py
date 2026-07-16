"""Canonicalization for the Classifier's free-text ``topics`` output.

Topics are LLM-generated (see :mod:`obsidian.manager_ai.classifier`'s
``topics`` prompt key), so the same underlying topic can arrive worded
differently across calls -- ``"machine learning"``, ``"ML"``, and
``"Artificial Intelligence"`` should all collapse to one stored topic, not
three. This module is the deterministic post-processing step that makes
that true: given whatever raw ``{"topic": ..., "confidence": ...}`` dicts
the Classifier's response contained, it always produces the same
:class:`~obsidian.core.value_objects.TopicTag` tuple for the same input --
no LLM call, no randomness, no I/O.

Canonicalization has two paths:

1. **Known alias** -- the cleaned (stripped/lowercased/whitespace-collapsed)
   topic string matches an entry in :data:`TOPIC_ALIASES`; the table's
   canonical display name is used verbatim.
2. **Novel topic** -- no alias match. The cleaned string is Title-Cased and
   used as its own canonical name, so a genuinely new topic the LLM
   introduces (not in the seed vocabulary) is still accepted, per "topics
   may be LLM-generated" -- this module only forces *known* synonyms
   together, it never rejects an unrecognized topic.

Multiple raw entries that canonicalize to the same name are merged,
keeping the higher confidence. The final list is capped at
:data:`MAX_TOPICS` (3), sorted by descending confidence with an
alphabetical tie-break, so ranking is deterministic even when two topics
tie exactly.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from obsidian.core.value_objects import TopicTag

#: Maximum number of topics kept per memory (highest-confidence first).
MAX_TOPICS = 3

#: Fixed lowercase-alias -> canonical-display-name table, seeded with the
#: suggested vocabulary plus common synonyms/variants for each. Not
#: exhaustive by design -- an unmatched topic is still accepted (see the
#: module docstring's "Novel topic" path), this table only exists to
#: collapse the *known* ways of saying the same thing.
TOPIC_ALIASES: Dict[str, str] = {
    # AI
    "ai": "AI",
    "artificial intelligence": "AI",
    "machine learning": "AI",
    "ml": "AI",
    "deep learning": "AI",
    "llm": "AI",
    "llms": "AI",
    "large language models": "AI",
    "generative ai": "AI",
    # Programming
    "programming": "Programming",
    "coding": "Programming",
    "software": "Programming",
    "software engineering": "Programming",
    "software development": "Programming",
    "development": "Programming",
    "dev": "Programming",
    # Mechanical Engineering
    "mechanical engineering": "Mechanical Engineering",
    "mech eng": "Mechanical Engineering",
    "mechanical": "Mechanical Engineering",
    # Robotics
    "robotics": "Robotics",
    "robots": "Robotics",
    "robot": "Robotics",
    # Fitness
    "fitness": "Fitness",
    "working out": "Fitness",
    "workout": "Fitness",
    "exercise": "Fitness",
    "gym": "Fitness",
    # Nutrition
    "nutrition": "Nutrition",
    "diet": "Nutrition",
    "dieting": "Nutrition",
    "food": "Nutrition",
    # Finance
    "finance": "Finance",
    "financial": "Finance",
    "money": "Finance",
    "investing": "Finance",
    "investment": "Finance",
    "personal finance": "Finance",
    # Travel
    "travel": "Travel",
    "traveling": "Travel",
    "travelling": "Travel",
    "trips": "Travel",
    "vacation": "Travel",
}

_WHITESPACE_RE = re.compile(r"\s+")


def _clean(raw_name: str) -> str:
    """Strip, lowercase, and collapse internal whitespace in *raw_name*."""
    return _WHITESPACE_RE.sub(" ", raw_name.strip().lower())


def canonicalize_topic_name(raw_name: str) -> str:
    """Return the canonical display name for *raw_name*.

    Looks up the cleaned (stripped/lowercased/whitespace-collapsed) form in
    :data:`TOPIC_ALIASES`; falls back to Title-Casing the cleaned form when
    there is no alias entry, so a novel LLM-proposed topic is still usable.
    """
    cleaned = _clean(raw_name)
    return TOPIC_ALIASES.get(cleaned, cleaned.title())


def canonicalize_topics(raw: List[Dict[str, Any]]) -> Tuple[TopicTag, ...]:
    """Canonicalize, dedupe, cap, and deterministically order *raw* topics.

    Parameters
    ----------
    raw : list[dict[str, Any]]
        Entries shaped like ``{"topic": str, "confidence": float}`` -- the
        Classifier's already-validated ``topics`` response value (see
        :meth:`obsidian.manager_ai.classifier.Classifier._validate_parsed`).
        Defensively tolerant here too: an entry missing ``"topic"`` (empty
        after cleaning) or ``"confidence"`` is skipped or defaulted rather
        than raising, since this function is also exercised directly in
        unit tests independent of the Classifier's own validation gate.

    Returns
    -------
    tuple[TopicTag, ...]
        At most :data:`MAX_TOPICS` tags, sorted by descending confidence
        (alphabetical tie-break on ``name``), with duplicate canonical
        names merged to whichever raw entry had the higher confidence.
    """
    best: Dict[str, float] = {}
    for entry in raw:
        raw_name = entry.get("topic", "") if isinstance(entry, dict) else ""
        if not isinstance(raw_name, str) or not raw_name.strip():
            continue
        canonical = canonicalize_topic_name(raw_name)
        if not canonical:
            continue
        confidence = entry.get("confidence", 0.5)
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
        confidence = min(max(float(confidence), 0.0), 1.0)
        if canonical not in best or confidence > best[canonical]:
            best[canonical] = confidence

    ordered = sorted(best.items(), key=lambda item: (-item[1], item[0]))
    return tuple(
        TopicTag(name=name, confidence=confidence)
        for name, confidence in ordered[:MAX_TOPICS]
    )
