"""Classifier stage of the Manager AI pipeline.

The Classifier is the second stage.  Given an :class:`ExtractedFact` it
produces a :class:`ClassificationResult`.  It does **not** extract
memories, score importance, determine relationships, perform
supersession, or write ExtractionReports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Protocol

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import ClassificationResult, ExtractedFact
from obsidian.manager_ai.transport_retry import generate_with_transport_retry


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
# LLM interface (dependency injection)
# ---------------------------------------------------------------------------


class LLMInterface(Protocol):
    """Minimal interface for the LLM used by the Classifier.

    The concrete implementation (e.g. Qwen, OpenAI) will be injected
    at runtime.
    """

    def generate(self, prompt: str) -> str:
        """Return the raw text response for *prompt*."""
        ...


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

        The prompt instructs the LLM to return a JSON object with the
        keys ``memory_type``, ``confidence``, and ``reason``.
        """
        prompt = (
            "You are an expert memory classifier.  Your task is to classify "
            "the following fact into one of the predefined memory types.\n\n"
            "Fact:\n"
            f"Text: {fact.text}\n"
            f"Evidence: {fact.evidence}\n"
            f"Confidence: {fact.confidence}\n\n"
            "Available memory types:\n"
            + "\n".join(f"- {t.value}" for t in MemoryType)
            + "\n\n"
            "Return a JSON object with exactly three keys:\n"
            '- "memory_type": one of the available types (string)\n'
            '- "confidence": a float between 0.0 and 1.0 indicating how '
            "confident you are in this classification\n"
            '- "reason": a short explanation of why this type was chosen '
            "(string)\n\n"
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
        synonyms, no invented categories (the ``"interest"`` failure mode).
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
            "Return ONLY a JSON object with exactly three keys: "
            '"memory_type" (one of the values above, verbatim), "confidence" '
            '(a float between 0.0 and 1.0), and "reason" (a string). No '
            "other text."
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

        return parsed

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
        )
