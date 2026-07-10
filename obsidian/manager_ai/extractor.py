"""Extractor stage of the Manager AI pipeline.

The Extractor is the first stage.  Given a :class:`Conversation` it
produces a list of :class:`ExtractedFact` objects.  It does **not**
classify, score importance, determine relationships, update memories,
or perform supersession.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol
from uuid import UUID

from obsidian.core.errors import ExtractionError
from obsidian.core.types import Conversation
from obsidian.manager_ai.models import ExtractedFact
from obsidian.manager_ai.transport_retry import generate_with_transport_retry
from obsidian.ontology.retrieval_models import WorkingContext

logger = logging.getLogger(__name__)

#: Verbose per-stage extraction logging (conversation text, raw LLM output,
#: per-candidate accept/reject reasons) -- disabled by default since it can
#: log full conversation content. Set ``HAVEN_DEBUG_PIPELINE_LOGGING=true``
#: to enable while diagnosing extraction issues.
_DEBUG_PIPELINE_LOGGING = os.environ.get(
    "HAVEN_DEBUG_PIPELINE_LOGGING", "false"
).strip().lower() in ("true", "1", "yes")

#: Bumped whenever ``build_prompt``'s template wording changes materially.
#: Purely observational -- nothing in this module reads it to make a
#: decision; it exists so a persisted trace (see
#: :mod:`obsidian.ontology.write_trace_models`) can record whether a
#: stale ``raw_response`` is still comparable to today's prompt.
EXTRACTOR_PROMPT_VERSION = 1


@dataclass(frozen=True)
class ExtractionTrace:
    """The Extractor's prompt, raw LLM output, and resulting facts.

    A manager_ai-internal counterpart to
    :class:`~obsidian.ontology.write_trace_models.ExtractorStageTrace` --
    this one carries the actual :class:`ExtractedFact` list (so
    :meth:`ManagerPipeline.process_with_trace` can keep processing them),
    where the persisted trace only needs a count. The caller
    (``obsidian/server/main.py``) is responsible for projecting this into
    an ``ExtractorStageTrace`` -- including applying the
    ``HAVEN_WRITE_TRACE_CAPTURE_LLM_IO`` redaction toggle, which is a
    server-layer concern this module has no knowledge of.
    """

    prompt: str
    raw_response: str
    facts: List[ExtractedFact]


# ---------------------------------------------------------------------------
# LLM interface (dependency injection)
# ---------------------------------------------------------------------------


class LLMInterface(Protocol):
    """Minimal interface for the LLM used by the Extractor.

    The concrete implementation (e.g. Qwen, OpenAI) will be injected
    at runtime.
    """

    def generate(self, prompt: str) -> str:
        """Return the raw text response for *prompt*."""
        ...


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


@dataclass
class Extractor:
    """First stage of the Manager AI pipeline.

    Parameters
    ----------
    llm : LLMInterface
        The language model used to extract facts from a conversation.
    """

    llm: LLMInterface

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(
        self,
        conversation: Conversation,
        existing_context: Optional[List[WorkingContext]] = None,
    ) -> List[ExtractedFact]:
        """Extract candidate facts from *conversation*.

        Parameters
        ----------
        conversation : Conversation
            The normalised conversation to analyse -- the new evidence.
            Every candidate fact must be grounded in this argument alone;
            see *existing_context* below for how the two stay distinct.
        existing_context : list[WorkingContext], optional
            Background information already known and saved in Haven (see
            :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_working_context`),
            used only to help resolve references inside *conversation* --
            never itself a source of extracted facts. ``None`` (the
            default) reproduces this method's behaviour exactly as it was
            before this parameter existed.

        Returns
        -------
        list[ExtractedFact]
            Deduplicated list of extracted facts.
        """
        return self.extract_with_trace(
            conversation, existing_context=existing_context
        ).facts

    def extract_with_trace(
        self,
        conversation: Conversation,
        existing_context: Optional[List[WorkingContext]] = None,
    ) -> ExtractionTrace:
        """Run :meth:`extract`, additionally returning the prompt/raw output.

        Contains exactly the same logic as :meth:`extract` -- the only
        difference is returning the prompt and raw LLM response (otherwise
        only debug-logged, and only when ``HAVEN_DEBUG_PIPELINE_LOGGING`` is
        enabled) alongside the parsed facts, for a caller building a
        :class:`~obsidian.ontology.write_trace_models.WriteTrace`.
        :meth:`extract` is a thin wrapper around this method (see its
        body) so both share one implementation.

        Raises
        ------
        ExtractionError
            If the LLM response is still unparseable/invalid after one
            repair retry (see :meth:`build_repair_prompt`). Never
            fabricates facts to paper over a bad response -- the caller
            (:class:`~obsidian.manager_ai.pipeline.ManagerPipeline`) is
            expected to treat this as "no facts extracted", not retry
            again or crash the request.

        Notes
        -----
        Each of the (up to two) calls to the LLM below is itself wrapped in
        :func:`~obsidian.manager_ai.transport_retry.generate_with_transport_retry`,
        which silently retries once on a transient network/timeout failure
        before this method ever sees a response to parse -- a distinct
        concern from the repair retry below, which only fires once a
        response *was* received but was unusable.
        """
        if _DEBUG_PIPELINE_LOGGING:
            events_text = "\n".join(
                f"[{e.role.value}] {e.content}" for e in conversation.events
            )
            logger.debug(
                "1. Conversation passed to Extractor (%d events):\n%s\n",
                len(conversation.events),
                events_text,
            )

        prompt = self.build_prompt(conversation, existing_context=existing_context)
        raw_response = generate_with_transport_retry(self.llm, prompt)
        if _DEBUG_PIPELINE_LOGGING:
            logger.debug("2. Extractor raw LLM output:\n%r\n", raw_response)

        try:
            parsed = self.parse_response(raw_response)
            validated = self._validate_parsed(parsed)
        except ValueError as first_error:
            # One repair retry, mirroring Classifier.classify: re-ask with
            # the invalid response and the concrete error quoted back. A
            # second failure gives up on this whole extraction call --
            # never fabricate facts to paper over a bad response.
            if _DEBUG_PIPELINE_LOGGING:
                logger.debug(
                    "4. Parsing/validation failed -- retrying once with a "
                    "repair prompt: %s",
                    first_error,
                )
            repair_response = generate_with_transport_retry(
                self.llm, self.build_repair_prompt(raw_response, first_error)
            )
            try:
                parsed = self.parse_response(repair_response)
                validated = self._validate_parsed(parsed)
            except ValueError as second_error:
                if _DEBUG_PIPELINE_LOGGING:
                    logger.debug(
                        "4. Repair retry also failed -- ALL candidates "
                        "rejected before classification: %s",
                        second_error,
                    )
                raise ExtractionError(
                    "Extractor could not parse a usable response after "
                    f"one repair retry: {second_error}",
                    context={
                        "prompt": prompt,
                        "raw_response": raw_response,
                        "repair_response": repair_response,
                    },
                ) from second_error
            raw_response = repair_response

        rejected_by_validation = len(parsed) - len(validated)
        if rejected_by_validation and _DEBUG_PIPELINE_LOGGING:
            logger.debug(
                "4. %d candidate(s) rejected by validation",
                rejected_by_validation,
            )

        facts = self._convert_to_facts(validated, conversation)
        deduped = self.deduplicate(facts)
        rejected_by_dedup = len(facts) - len(deduped)
        if rejected_by_dedup and _DEBUG_PIPELINE_LOGGING:
            logger.debug(
                "4. %d duplicate(s) rejected before classification",
                rejected_by_dedup,
            )

        if _DEBUG_PIPELINE_LOGGING:
            if deduped:
                logger.debug(
                    "3. Extracted candidate facts after validation+dedup (%d):",
                    len(deduped),
                )
                for fact in deduped:
                    logger.debug(
                        "3.   text=%r confidence=%s evidence=%r",
                        fact.text,
                        fact.confidence,
                        fact.evidence,
                    )
            else:
                logger.debug(
                    "3. Extracted candidate facts: NONE -- the Extractor's "
                    "LLM call returned zero candidates for this conversation."
                )

        return ExtractionTrace(
            prompt=prompt, raw_response=raw_response, facts=deduped
        )

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def build_prompt(
        self,
        conversation: Conversation,
        existing_context: Optional[List[WorkingContext]] = None,
    ) -> str:
        """Construct the extraction prompt for *conversation*.

        The prompt instructs the LLM to return a JSON array of objects
        with the keys ``text``, ``evidence``, and ``confidence``.

        Parameters
        ----------
        conversation : Conversation
            The new evidence -- rendered under a ``NEW EVIDENCE`` heading
            when *existing_context* is supplied, or under the original
            ``Conversation:`` heading (unchanged) otherwise.
        existing_context : list[WorkingContext], optional
            When omitted, or when it renders no content (see
            :meth:`_render_existing_context`), the returned prompt is
            byte-for-byte identical to what this method returned before
            this parameter existed. When it renders non-empty content, an
            ``EXISTING CONTEXT`` section is prepended, clearly separated
            from -- and explicitly subordinate to -- the conversation
            evidence.
        """
        events_text = "\n".join(
            f"[{e.role.value}] {e.content}" for e in conversation.events
        )

        existing_context_preamble = ""
        rendered_context = (
            self._render_existing_context(existing_context)
            if existing_context
            else ""
        )
        if rendered_context:
            existing_context_preamble = (
                "EXISTING CONTEXT (background only -- information already "
                "known and saved in Haven, from earlier in this "
                "conversation or from other memories):\n"
                f"{rendered_context}\n\n"
                "Treat the above as prior knowledge only. Never extract a "
                "fact solely because it appears in Existing Context. Only "
                "extract facts that are supported by the NEW EVIDENCE "
                "below. If New Evidence confirms, updates, contradicts, or "
                "elaborates on something in Existing Context, extract only "
                "the new information -- not a restatement of what is "
                "already listed above.\n\n"
                "NEW EVIDENCE (extract candidate facts only from this "
                "section):\n"
            )

        prompt = (
            "You are an expert memory extractor for a personal long-term "
            "memory system. Your task is to extract candidate facts from "
            "the following conversation that are worth remembering about "
            "the USER specifically -- not general knowledge the assistant "
            "explained.\n\n"
            f"{existing_context_preamble}"
            "Conversation:\n"
            f"{events_text}\n\n"
            "The bracketed [user]/[assistant]/[system]/[tool] tags above "
            "are speaker role labels, not names. Never treat the word "
            "\"user\" or \"assistant\" itself as a person's name in an "
            "extracted fact -- refer to the human speaker as \"the "
            "user\".\n\n"
            "What to extract, in priority order:\n"
            "1. Facts about the user's identity, life, or circumstances "
            "(e.g. where they live, work, or study).\n"
            "2. The user's own preferences, opinions, or beliefs.\n"
            "3. Decisions the user has made.\n"
            "4. Goals or projects the user is working on.\n"
            "5. Durable knowledge the user has explicitly adopted as "
            "their own -- they state a conclusion, a choice, or "
            "something they now know and intend to keep in mind -- not "
            "just information the assistant happened to explain.\n\n"
            "What to ignore:\n"
            "- General explanations, tutorials, or reference material "
            "the assistant provided (e.g. how a protocol, algorithm, or "
            "concept works), unless the user explicitly says they "
            "learned it, adopted it, or it changed one of their own "
            "plans or beliefs. A long technical explanation should "
            "usually yield ZERO extracted facts unless the user makes a "
            "personal statement within it.\n"
            "- Small talk, greetings, and filler with no durable "
            "content.\n\n"
            "Normalize phrasing so the same underlying fact is always "
            "worded the same way, even when the user's exact words "
            "differ. Use these templates when they fit, filling in only "
            "the specific detail:\n"
            '- Identity/fact: "The user <verb>s <detail>." '
            '(e.g. "The user lives in Muscat.")\n'
            '- Preference: "The user prefers <X> over <Y>." or "The '
            'user prefers <X>."\n'
            '- Decision: "The user decided to <decision>."\n'
            '- Goal: "The user\'s goal is to <goal>."\n'
            '- Project: "The user is working on a project called <Name> '
            'to <purpose>."\n'
            "Two conversations describing the same underlying project, "
            "preference, or fact in different words should produce "
            "nearly identical canonical text, so they can be recognised "
            "as the same memory later.\n\n"
            "Return a JSON array of objects.  Each object must have exactly "
            "three keys:\n"
            '- "text": the normalized factual statement (string)\n'
            '- "evidence": a short explanation of why this is a fact (string)\n'
            '- "confidence": a float between 0.0 and 1.0 indicating how '
            "confident you are that this is a correct fact\n\n"
            "If nothing in the conversation is worth remembering about "
            "the user, return an empty JSON array: []\n\n"
            "Return ONLY the JSON array, no additional text."
        )
        return prompt

    def build_repair_prompt(self, previous_response: str, error: Exception) -> str:
        """Construct the one-shot repair prompt for a rejected extraction response.

        Used by :meth:`extract_with_trace` after the first response failed
        to parse as JSON or failed validation. Quotes back the LLM's own
        invalid response and the concrete error, then re-states the exact
        output contract from :meth:`build_prompt` so the retry has every
        chance of succeeding without repeating the full extraction
        instructions (what to extract/ignore, normalization templates,
        etc.) -- those don't change on retry, only the output shape does.
        """
        return (
            "Your previous response could not be parsed and was "
            "rejected.\n\n"
            f"Reason: {error}\n\n"
            "Your previous (invalid) response was:\n"
            f"{previous_response}\n\n"
            "Return a JSON array of objects. Each object must have exactly "
            "three keys:\n"
            '- "text": the normalized factual statement (string)\n'
            '- "evidence": a short explanation of why this is a fact '
            "(string)\n"
            '- "confidence": a float between 0.0 and 1.0 indicating how '
            "confident you are that this is a correct fact\n\n"
            "If nothing is worth remembering, return an empty JSON array: "
            "[]\n\n"
            "Return ONLY the JSON array, no additional text."
        )

    def parse_response(self, raw_response: str) -> List[Dict[str, Any]]:
        """Parse the LLM response into a list of dictionaries.

        Parameters
        ----------
        raw_response : str
            The raw text returned by the LLM.

        Returns
        -------
        list[dict[str, Any]]
            Parsed JSON objects.

        Raises
        ------
        ValueError
            If the response cannot be parsed as a JSON array.
        """
        # Try to locate the JSON array inside the response (the LLM may
        # wrap it in markdown fences or add extra commentary).
        text = raw_response.strip()
        # Remove markdown code fences if present
        if text.startswith("```"):
            # find the first newline after the opening fence
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline:]
            # remove trailing fence
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

        if not isinstance(parsed, list):
            raise ValueError(
                "LLM response is not a JSON array; "
                f"got {type(parsed).__name__}"
            )

        return parsed

    def deduplicate(
        self, facts: List[ExtractedFact]
    ) -> List[ExtractedFact]:
        """Remove duplicate extracted facts.

        Two facts are considered duplicates if they have the same
        ``text`` (case‑sensitive) and the same ``source_event_id``.
        The first occurrence is kept.
        """
        seen: set[tuple[str, UUID]] = set()
        result: List[ExtractedFact] = []
        for fact in facts:
            key = (fact.text, fact.source_event_id)
            if key not in seen:
                seen.add(key)
                result.append(fact)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _render_existing_context(contexts: List[WorkingContext]) -> str:
        """Render *contexts* as a compact, plain-text background block.

        A deliberately narrow projection of :class:`WorkingContext` --
        only ``title`` and the four :class:`WorkingContextState` summary
        fields (goal, recent decisions, pending tasks, open questions),
        each read verbatim from the already-assembled
        :class:`~obsidian.ontology.retrieval_models.RankedCandidate`
        objects. Confidence/importance/valid_until and the full role
        buckets are intentionally left out: those exist to help a
        downstream *answering* LLM weigh conflicting memories (see
        :class:`~obsidian.memory_engine.structured_prompt_builder.StructuredPromptBuilder`),
        which is a different job than helping the Extractor resolve a
        reference.

        A context with nothing in any of the four fields (i.e. retrieval
        found nothing relevant) contributes no lines. Returns ``""`` when
        every context is empty this way, which is the signal
        :meth:`build_prompt` uses to omit the ``EXISTING CONTEXT`` section
        entirely rather than emitting an empty-looking scaffold.
        """
        lines: List[str] = []
        for context in contexts:
            state = context.state
            parts: List[str] = []
            if state.current_goal is not None:
                parts.append(
                    f"goal: {state.current_goal.candidate.knowledge_object.canonical_fact}"
                )
            if state.recent_decisions:
                decisions = "; ".join(
                    rc.candidate.knowledge_object.canonical_fact
                    for rc in state.recent_decisions
                )
                parts.append(f"recent decisions: {decisions}")
            if state.pending_tasks:
                tasks = "; ".join(
                    rc.candidate.knowledge_object.canonical_fact
                    for rc in state.pending_tasks
                )
                parts.append(f"pending tasks: {tasks}")
            if state.open_questions:
                questions = "; ".join(
                    rc.candidate.knowledge_object.canonical_fact
                    for rc in state.open_questions
                )
                parts.append(f"open questions: {questions}")

            if not parts:
                continue

            lines.append(f"- {context.title}:")
            lines.extend(f"    {part}" for part in parts)

        return "\n".join(lines)

    @staticmethod
    def _validate_parsed(
        parsed: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Validate that each dictionary has the required keys and types.

        Parameters
        ----------
        parsed : list[dict[str, Any]]
            The parsed JSON array.

        Returns
        -------
        list[dict[str, Any]]
            The same list, filtered to only contain valid entries.

        Raises
        ------
        ValueError
            If any entry is missing a required key or has an invalid type.
        """
        required_keys = {"text", "evidence", "confidence"}
        validated: List[Dict[str, Any]] = []
        for i, entry in enumerate(parsed):
            if not isinstance(entry, dict):
                raise ValueError(
                    f"Entry {i} is not a dict: {type(entry).__name__}"
                )
            missing = required_keys - set(entry.keys())
            if missing:
                raise ValueError(
                    f"Entry {i} is missing required keys: {missing}"
                )
            if not isinstance(entry["text"], str):
                raise ValueError(
                    f"Entry {i} 'text' must be a string, "
                    f"got {type(entry['text']).__name__}"
                )
            if not isinstance(entry["evidence"], str):
                raise ValueError(
                    f"Entry {i} 'evidence' must be a string, "
                    f"got {type(entry['evidence']).__name__}"
                )
            confidence = entry["confidence"]
            if not isinstance(confidence, (int, float)):
                raise ValueError(
                    f"Entry {i} 'confidence' must be a number, "
                    f"got {type(confidence).__name__}"
                )
            if not 0.0 <= confidence <= 1.0:
                raise ValueError(
                    f"Entry {i} 'confidence' must be between 0 and 1, "
                    f"got {confidence}"
                )
            validated.append(entry)
        return validated

    @staticmethod
    def _convert_to_facts(
        validated: List[Dict[str, Any]],
        conversation: Conversation,
    ) -> List[ExtractedFact]:
        """Convert validated dictionaries into :class:`ExtractedFact` objects.

        The ``source_event_id`` is taken from the **last** event in the
        conversation (the most recent turn).  This is a simplification;
        a future version may map each fact to the specific event that
        triggered it.
        """
        if not conversation.events:
            return []

        # Use the last event's id as the source for all extracted facts.
        source_event_id = conversation.events[-1].id

        facts: List[ExtractedFact] = []
        for entry in validated:
            fact = ExtractedFact(
                text=entry["text"],
                source_event_id=source_event_id,
                evidence=entry["evidence"],
                confidence=float(entry["confidence"]),
            )
            facts.append(fact)
        return facts
