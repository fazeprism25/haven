"""Importance stage of the Manager AI pipeline.

The Importance stage is the third stage.  Given an :class:`ExtractedFact`
and a :class:`ClassificationResult` it produces an
:class:`ImportanceResult`.  It does **not** extract memories, classify
memories, determine relationships, perform supersession, or write
ExtractionReports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict

from obsidian.manager_ai.models import (
    ClassificationResult,
    ExtractedFact,
    ImportanceResult,
)
from obsidian.manager_ai.transport_retry import LLMInterface, generate_with_transport_retry


class ImportanceScoringError(Exception):
    """A single fact's importance could not be scored from a usable response.

    Raised by :meth:`ImportanceScorer.score` only after it has already
    retried once with an explicit repair prompt (see that method) and the
    LLM *still* returned an unusable response -- unparseable JSON, missing
    fields, or an out-of-range/non-numeric ``score``. It is deliberately a
    distinct type, not a bare :class:`ValueError`, so the pipeline can catch
    exactly this "give up on this one fact" signal and skip it while still
    scoring every other fact in the same conversation -- one bad fact never
    aborts a whole note (see
    :meth:`~obsidian.manager_ai.pipeline.ManagerPipeline.extract_classify_score`).
    """


# ---------------------------------------------------------------------------
# ImportanceScorer
# ---------------------------------------------------------------------------


@dataclass
class ImportanceScorer:
    """Third stage of the Manager AI pipeline.

    Parameters
    ----------
    llm : LLMInterface
        The language model used to score the importance of a fact.
    """

    llm: LLMInterface

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(
        self,
        fact: ExtractedFact,
        classification: ClassificationResult,
    ) -> ImportanceResult:
        """Score the importance of *fact* given its *classification*.

        The LLM is asked once; if its response is unusable -- unparseable,
        missing a required field, or carrying an out-of-range/non-numeric
        ``score`` -- it is asked **exactly once more** with a repair prompt
        that names the invalid response, the concrete validation error, and
        the valid schema (see :meth:`build_repair_prompt`). Only if that
        second attempt is also unusable does this raise
        :class:`ImportanceScoringError`, the signal the pipeline uses to
        skip this one fact rather than abort the whole conversation.

        Each of the (up to two) calls above is itself wrapped in
        :func:`~obsidian.manager_ai.transport_retry.generate_with_transport_retry`,
        which silently retries once on a transient network/timeout failure
        before this method ever sees a response to parse -- a distinct
        concern from the repair retry above, which only fires once a
        response *was* received but was unusable.

        Parameters
        ----------
        fact : ExtractedFact
            The fact to score.
        classification : ClassificationResult
            The classification of the fact.

        Returns
        -------
        ImportanceResult
            The importance result.

        Raises
        ------
        ImportanceScoringError
            If the LLM still returns an invalid/unusable response after the
            one repair retry.
        """
        raw_response = generate_with_transport_retry(
            self.llm, self.build_prompt(fact, classification)
        )
        try:
            validated = self._validate_parsed(self.parse_response(raw_response))
        except ValueError as first_error:
            # One repair retry: re-ask with the invalid response and the
            # concrete validation error called out, plus the valid schema
            # spelled out in full. A second failure is a per-fact skip
            # (ImportanceScoringError), never a crash.
            repair_response = generate_with_transport_retry(
                self.llm,
                self.build_repair_prompt(
                    fact, classification, raw_response, first_error
                ),
            )
            try:
                validated = self._validate_parsed(
                    self.parse_response(repair_response)
                )
            except ValueError as second_error:
                raise ImportanceScoringError(
                    f"Could not score importance for fact {fact.text!r} "
                    f"after one repair retry: {second_error}"
                ) from second_error
        return self._convert_to_result(validated)

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def build_prompt(
        self,
        fact: ExtractedFact,
        classification: ClassificationResult,
    ) -> str:
        """Construct the importance scoring prompt.

        The prompt instructs the LLM to return a JSON object with the
        keys ``score`` and ``reason``.
        """
        prompt = (
            "You are an expert memory importance scorer.  Your task is to "
            "determine how important the following fact is for long‑term "
            "retention in a personal memory system.\n\n"
            "Score consistently against these anchor points -- the same "
            "kind of fact should always land near the same score, "
            "regardless of exact wording:\n"
            '- ~0.2-0.3: a minor, easily-changed preference or one-off '
            'detail (e.g. "The user enjoys badminton.")\n'
            '- ~0.5-0.6: a useful but non-critical fact about tools, '
            'habits, or context (e.g. "The user uses Obsidian.")\n'
            '- ~0.7-0.8: a stable identity fact unlikely to change soon '
            '(e.g. "The user lives in Muscat.", "The user works at Acme '
            'Corp.")\n'
            '- ~0.9-1.0: a core, defining fact -- an active project, a '
            'major goal, or a strongly stated decision or preference '
            'central to how the user works (e.g. "The user is working '
            'on a project called Haven to reconstruct working '
            'context.")\n\n'
            "Fact:\n"
            f"Text: {fact.text}\n"
            f"Evidence: {fact.evidence}\n"
            f"Confidence: {fact.confidence}\n\n"
            "Classification:\n"
            f"Memory type: {classification.memory_type.value}\n"
            f"Classification confidence: {classification.confidence}\n"
            f"Reason: {classification.reason}\n\n"
            "Return a JSON object with exactly two keys:\n"
            '- "score": a float between 0.0 and 1.0 indicating the '
            "importance for long‑term retention\n"
            '- "reason": a non‑empty string explaining why this score '
            "was assigned\n\n"
            "Return ONLY the JSON object, no additional text."
        )
        return prompt

    def build_repair_prompt(
        self,
        fact: ExtractedFact,
        classification: ClassificationResult,
        previous_response: str,
        error: Exception,
    ) -> str:
        """Construct the one-shot repair prompt for a rejected importance score.

        Used by :meth:`score` after the first response failed validation.
        Unlike :meth:`build_prompt`, it quotes back the LLM's own invalid
        response and the concrete validation error, then re-states the valid
        schema (``score`` as a float between 0.0 and 1.0, ``reason`` as a
        non-empty string) and insists on it exactly -- no missing keys, no
        out-of-range or non-numeric ``score``.
        """
        return (
            "Your previous importance-scoring response was rejected and "
            f"could not be used.\n\nReason: {error}\n\n"
            "Your previous (invalid) response was:\n"
            f"{previous_response}\n\n"
            "You MUST return a JSON object with exactly these two keys, "
            "with these exact types:\n"
            '- "score": a float between 0.0 and 1.0 (inclusive)\n'
            '- "reason": a non-empty string\n\n'
            "Fact:\n"
            f"Text: {fact.text}\n"
            f"Evidence: {fact.evidence}\n"
            f"Confidence: {fact.confidence}\n\n"
            "Classification:\n"
            f"Memory type: {classification.memory_type.value}\n"
            f"Classification confidence: {classification.confidence}\n"
            f"Reason: {classification.reason}\n\n"
            "Return ONLY the JSON object, no additional text."
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
        required_keys = {"score", "reason"}
        missing = required_keys - set(parsed.keys())
        if missing:
            raise ValueError(
                f"Parsed object is missing required keys: {missing}"
            )

        score = parsed["score"]
        if not isinstance(score, (int, float)):
            raise ValueError(
                "'score' must be a number, "
                f"got {type(score).__name__}"
            )
        if not 0.0 <= score <= 1.0:
            raise ValueError(
                f"'score' must be between 0 and 1, got {score}"
            )

        reason = parsed["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(
                "'reason' must be a non‑empty string, "
                f"got {type(reason).__name__!r}"
            )

        return parsed

    @staticmethod
    def _convert_to_result(validated: Dict[str, Any]) -> ImportanceResult:
        """Convert a validated dictionary into an :class:`ImportanceResult`.

        Parameters
        ----------
        validated : dict[str, Any]
            The validated dictionary.

        Returns
        -------
        ImportanceResult
            The importance result.
        """
        return ImportanceResult(
            score=float(validated["score"]),
            reason=validated["reason"],
        )
