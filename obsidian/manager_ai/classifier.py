"""Classifier stage of the Manager AI pipeline.

The Classifier is the second stage.  Given an :class:`ExtractedFact` it
produces a :class:`ClassificationResult`.  It does **not** extract
memories, score importance, determine relationships, perform
supersession, or write ExtractionReports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List

from obsidian.core.enums import MemoryDomain, MemoryType
from obsidian.core.memory_domain import resolve_domain
from obsidian.manager_ai.models import ClassificationResult, ExtractedFact
from obsidian.manager_ai.topic_canonicalizer import MAX_TOPICS, canonicalize_topics
from obsidian.manager_ai.transport_retry import LLMInterface, generate_with_transport_retry

#: Short disambiguation guidance for memory types the Classifier has
#: historically over-used as a catch-all (``PREFERENCE`` in particular --
#: see :class:`ClassificationError`'s docstring for the ``"interest"``
#: failure mode this used to produce before ``INTEREST`` existed as a real
#: type). Keyed by :class:`MemoryType`; a type with no entry gets no extra
#: guidance line in the prompt.
_TYPE_GUIDANCE: Dict[MemoryType, str] = {
    MemoryType.PREFERENCE: (
        "a settled like/dislike or choice between options "
        '(e.g. "the user prefers dark mode")'
    ),
    MemoryType.INTEREST: (
        "something the user is watching, following, or curious about, "
        "without having adopted it as a preference or decision "
        '(e.g. "the user is watching LlamaIndex", "the user is '
        'interested in research papers on retrieval")'
    ),
    MemoryType.TRAIT: (
        "an enduring characteristic or disposition, not a one-off "
        'preference (e.g. "the user likes building systems from '
        'scratch")'
    ),
    MemoryType.HABIT: "a recurring behavior or routine",
    MemoryType.SKILL: "a skill or competency the user possesses",
    MemoryType.GOAL: "a future objective the user is working toward",
}

#: Display order and label for each :class:`MemoryDomain` in the prompt.
_DOMAIN_ORDER: List[MemoryDomain] = [
    MemoryDomain.PERSONAL,
    MemoryDomain.WORK,
    MemoryDomain.KNOWLEDGE,
]

_DOMAIN_LABELS: Dict[MemoryDomain, str] = {
    MemoryDomain.PERSONAL: "Personal",
    MemoryDomain.WORK: "Work",
    MemoryDomain.KNOWLEDGE: "Knowledge",
}


def _render_type_listing() -> str:
    """Render every :class:`MemoryType`, grouped by :class:`MemoryDomain`.

    Replaces the old flat ``for t in MemoryType`` dump with a
    domain-grouped listing plus short disambiguation guidance (see
    :data:`_TYPE_GUIDANCE`) so the LLM has a real basis for choosing
    between similar Personal-domain types instead of defaulting everything
    to ``preference``. Grouping and guidance are presentation only -- the
    set of valid values is still exactly ``MemoryType``'s members, so
    :meth:`Classifier._validate_parsed` needs no change to accept them.
    """
    by_domain: Dict[MemoryDomain, List[MemoryType]] = {d: [] for d in _DOMAIN_ORDER}
    for member in MemoryType:
        by_domain[resolve_domain(member)].append(member)

    sections = []
    for domain in _DOMAIN_ORDER:
        lines = [f"{_DOMAIN_LABELS[domain]}:"]
        for member in by_domain[domain]:
            guidance = _TYPE_GUIDANCE.get(member)
            if guidance:
                lines.append(f"- {member.value}: {guidance}")
            else:
                lines.append(f"- {member.value}")
        sections.append("\n".join(lines))
    return "Available memory types:\n" + "\n\n".join(sections)


#: Shared topic-tagging instructions, reused verbatim by the main and
#: repair prompts. The vocabulary is illustrative only -- topics are not a
#: fixed enum, and a topic outside this list is still valid (see
#: :mod:`obsidian.manager_ai.topic_canonicalizer` for how novel topics are
#: canonicalized rather than rejected).
_TOPIC_INSTRUCTIONS = (
    f"You may also tag the fact with up to {MAX_TOPICS} topics -- short "
    "labels for the subject matter, independent of memory type. Examples: "
    "AI, Programming, Mechanical Engineering, Robotics, Fitness, "
    "Nutrition, Finance, Travel. These are examples, not a fixed list -- "
    "use a different topic if none of these fit. Omit \"topics\" entirely "
    "if none apply."
)


class ClassificationError(Exception):
    """A single fact could not be classified into a valid :class:`MemoryType`.

    Raised by :meth:`Classifier.classify` only after it has already retried
    once with an explicit repair prompt (see that method) and the LLM *still*
    returned an unusable response -- most often a plausible-sounding but
    non-existent category such as ``"interest"``. It is deliberately a
    distinct type, not a bare :class:`ValueError`, so the pipeline can catch
    exactly this "give up on this one fact" signal and skip it while still
    classifying every other fact in the same conversation -- one bad fact
    never aborts a whole note (see
    :meth:`~obsidian.manager_ai.pipeline.ManagerPipeline.extract_classify_score`).
    """


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


@dataclass
class Classifier:
    """Second stage of the Manager AI pipeline.

    Parameters
    ----------
    llm : LLMInterface
        The language model used to classify a fact.
    """

    llm: LLMInterface

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(self, fact: ExtractedFact) -> ClassificationResult:
        """Classify *fact* and return a :class:`ClassificationResult`.

        The LLM is asked once; if its response is unusable -- unparseable, or
        (the common case) a plausible but non-existent ``memory_type`` such as
        ``"interest"`` -- it is asked **exactly once more** with a repair
        prompt that names the invalid value and lists the valid enum values
        verbatim (see :meth:`build_repair_prompt`). Only if that second
        attempt is also unusable does this raise :class:`ClassificationError`,
        the signal the pipeline uses to skip this one fact rather than abort
        the whole conversation.

        Each of the (up to two) calls above is itself wrapped in
        :func:`~obsidian.manager_ai.transport_retry.generate_with_transport_retry`,
        which silently retries once on a transient network/timeout failure
        before this method ever sees a response to parse -- a distinct
        concern from the repair retry above, which only fires once a
        response *was* received but was unusable.

        Parameters
        ----------
        fact : ExtractedFact
            The fact to classify.

        Returns
        -------
        ClassificationResult
            The classification result.

        Raises
        ------
        ClassificationError
            If the LLM still returns an invalid/unusable classification after
            the one repair retry.
        """
        raw_response = generate_with_transport_retry(
            self.llm, self.build_prompt(fact)
        )
        try:
            validated = self._validate_parsed(self.parse_response(raw_response))
        except ValueError as first_error:
            # One repair retry: re-ask with the invalid value called out and
            # the valid MemoryType values spelled in full. A second failure
            # is a per-fact skip (ClassificationError), never a crash.
            repair_response = generate_with_transport_retry(
                self.llm, self.build_repair_prompt(fact, raw_response, first_error)
            )
            try:
                validated = self._validate_parsed(
                    self.parse_response(repair_response)
                )
            except ValueError as second_error:
                raise ClassificationError(
                    f"Could not classify fact {fact.text!r} into a valid "
                    f"memory type after one repair retry: {second_error}"
                ) from second_error
        return self._convert_to_result(validated)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def build_prompt(self, fact: ExtractedFact) -> str:
        """Construct the classification prompt for *fact*.

        The prompt instructs the LLM to return a JSON object with the keys
        ``memory_type``, ``confidence``, ``reason``, and the optional
        ``topics``.
        """
        prompt = (
            "You are an expert memory classifier.  Your task is to classify "
            "the following fact into one of the predefined memory types, "
            "and optionally tag it with topics.\n\n"
            "Fact:\n"
            f"Text: {fact.text}\n"
            f"Evidence: {fact.evidence}\n"
            f"Confidence: {fact.confidence}\n\n"
            f"{_render_type_listing()}\n\n"
            "Do not default to \"preference\" when a more specific type "
            "fits better -- see the guidance next to each type above, "
            "especially the distinction between preference, interest, "
            "trait, habit, skill, and goal.\n\n"
            f"{_TOPIC_INSTRUCTIONS}\n\n"
            "Return a JSON object with these keys:\n"
            '- "memory_type": one of the available types (string)\n'
            '- "confidence": a float between 0.0 and 1.0 indicating how '
            "confident you are in this classification\n"
            '- "reason": a short explanation of why this type -- and any '
            "topics below -- were chosen (string)\n"
            '- "topics" (optional): an array of up to '
            f"{MAX_TOPICS} objects, each "
            '{"topic": <short topic name>, "confidence": <float 0.0-1.0>}\n\n'
            "Return ONLY the JSON object, no additional text."
        )
        return prompt

    def build_repair_prompt(
        self,
        fact: ExtractedFact,
        previous_response: str,
        error: Exception,
    ) -> str:
        """Construct the one-shot repair prompt for a rejected classification.

        Used by :meth:`classify` after the first response failed validation.
        Unlike :meth:`build_prompt`, it quotes back the LLM's own invalid
        response and the concrete validation error, then re-lists the valid
        :class:`MemoryType` values and insists on one of them verbatim -- no
        synonyms, no invented categories (the ``"interest"`` failure mode --
        ironic today only in that ``interest`` is now a real type, but the
        principle still holds for any other invented category).
        Intentionally avoids the ``Conversation:``/``Classification:`` stage
        markers so it is never mistaken for an extract/importance prompt.
        """
        valid_values = ", ".join(f'"{t.value}"' for t in MemoryType)
        return (
            "Your previous classification response was rejected and could "
            f"not be used.\n\nReason: {error}\n\n"
            "Your previous (invalid) response was:\n"
            f"{previous_response}\n\n"
            "You MUST choose exactly one memory type from this list, spelled "
            "exactly as shown -- do not invent a new category or use a "
            f"synonym:\n{valid_values}\n\n"
            "Fact:\n"
            f"Text: {fact.text}\n"
            f"Evidence: {fact.evidence}\n"
            f"Confidence: {fact.confidence}\n\n"
            f"{_TOPIC_INSTRUCTIONS}\n\n"
            "Return ONLY a JSON object with these keys: "
            '"memory_type" (one of the values above, verbatim), "confidence" '
            '(a float between 0.0 and 1.0), "reason" (a string), and '
            'optionally "topics" (an array of up to '
            f"{MAX_TOPICS} "
            '{"topic": str, "confidence": float} objects). No other text.'
        )

    def parse_response(self, raw_response: str) -> Dict[str, Any]:
        """Parse the LLM response into a dictionary.

        Parameters
        ----------
        raw_response : str
            The raw text returned by the LLM.

        Returns
        -------
        dict[str, Any]
            Parsed JSON object.

        Raises
        ------
        ValueError
            If the response cannot be parsed as a JSON object.
        """
        text = raw_response.strip()
        # Remove markdown code fences if present
        if text.startswith("```"):
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline:]
            if text.endswith("```"):
                text = text[:-3].strip()
            elif text.endswith("```\n"):
                text = text[:-4].strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"LLM response could not be parsed as JSON: {exc}"
            ) from exc

        if not isinstance(parsed, dict):
            raise ValueError(
                "LLM response is not a JSON object; "
                f"got {type(parsed).__name__}"
            )

        return parsed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_parsed(parsed: Dict[str, Any]) -> Dict[str, Any]:
        """Validate that the parsed dictionary has the required keys and types.

        Parameters
        ----------
        parsed : dict[str, Any]
            The parsed JSON object.

        Returns
        -------
        dict[str, Any]
            The same dictionary, validated.

        Raises
        ------
        ValueError
            If any required key is missing or has an invalid type.
        """
        required_keys = {"memory_type", "confidence", "reason"}
        missing = required_keys - set(parsed.keys())
        if missing:
            raise ValueError(
                f"Parsed object is missing required keys: {missing}"
            )

        if not isinstance(parsed["memory_type"], str):
            raise ValueError(
                "'memory_type' must be a string, "
                f"got {type(parsed['memory_type']).__name__}"
            )

        # Validate that the memory_type value is a valid MemoryType
        try:
            MemoryType(parsed["memory_type"])
        except ValueError as exc:
            raise ValueError(
                f"Invalid memory_type value: {parsed['memory_type']}"
            ) from exc

        confidence = parsed["confidence"]
        if not isinstance(confidence, (int, float)):
            raise ValueError(
                "'confidence' must be a number, "
                f"got {type(confidence).__name__}"
            )
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(
                f"'confidence' must be between 0 and 1, got {confidence}"
            )

        if not isinstance(parsed["reason"], str):
            raise ValueError(
                "'reason' must be a string, "
                f"got {type(parsed['reason']).__name__}"
            )

        if "topics" in parsed:
            Classifier._validate_topics(parsed["topics"])

        return parsed

    @staticmethod
    def _validate_topics(topics: Any) -> None:
        """Validate the optional ``topics`` value, if present.

        ``topics`` itself is optional (see :meth:`_validate_parsed`), but
        when the key *is* present its value must be well-formed: a list of
        at most :data:`~obsidian.manager_ai.topic_canonicalizer.MAX_TOPICS`
        objects, each with a non-empty string ``"topic"`` and a
        ``"confidence"`` float in ``[0.0, 1.0]``. There is no fixed topic
        vocabulary to violate -- only structure is validated here; the
        actual topic *name* is free-form and normalized later by
        :func:`~obsidian.manager_ai.topic_canonicalizer.canonicalize_topics`.
        """
        if not isinstance(topics, list):
            raise ValueError(
                f"'topics' must be a list, got {type(topics).__name__}"
            )
        if len(topics) > MAX_TOPICS:
            raise ValueError(
                f"'topics' must have at most {MAX_TOPICS} entries, "
                f"got {len(topics)}"
            )
        for i, entry in enumerate(topics):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"'topics[{i}]' must be an object, "
                    f"got {type(entry).__name__}"
                )
            if not isinstance(entry.get("topic"), str) or not entry["topic"].strip():
                raise ValueError(
                    f"'topics[{i}].topic' must be a non-empty string"
                )
            confidence = entry.get("confidence", 0.5)
            if not isinstance(confidence, (int, float)):
                raise ValueError(
                    f"'topics[{i}].confidence' must be a number, "
                    f"got {type(confidence).__name__}"
                )
            if not 0.0 <= confidence <= 1.0:
                raise ValueError(
                    f"'topics[{i}].confidence' must be between 0 and 1, "
                    f"got {confidence}"
                )

    @staticmethod
    def _convert_to_result(validated: Dict[str, Any]) -> ClassificationResult:
        """Convert a validated dictionary into a :class:`ClassificationResult`.

        Parameters
        ----------
        validated : dict[str, Any]
            The validated dictionary.

        Returns
        -------
        ClassificationResult
            The classification result.
        """
        return ClassificationResult(
            memory_type=MemoryType(validated["memory_type"]),
            confidence=float(validated["confidence"]),
            reason=validated["reason"],
            topics=canonicalize_topics(validated.get("topics", [])),
        )
