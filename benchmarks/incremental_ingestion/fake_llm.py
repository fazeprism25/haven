"""Marker-based fake LLM for the incremental-ingestion benchmark suite.

Deliberately **not** a language model: it locates literal markers in the
text it is shown and returns exactly the facts those markers describe.
This is a conscious trade-off, not a shortcut -- see this module's
"Why a fake LLM" note below.

Marker syntax (embedded directly in a fixture turn's ``content``):

* ``FACT[<id>]: <text>`` -- always extracted verbatim, unconditionally,
  whenever this turn is part of what the Extractor is shown. Models a
  self-contained statement that needs no prior context to interpret.
* ``FACTIF[<needle>]: <text>`` -- extracted only if the literal string
  ``<needle>`` appears somewhere *else* in what the Extractor was shown
  this call (either in the ``EXISTING CONTEXT`` block or in another turn
  of the same evidence batch) -- never by virtue of its own marker
  syntax, which is excluded from that check. Models a reference that can
  only be resolved if the right background information actually reached
  the Extractor, which is the exact property this benchmark suite exists
  to measure.

Why a fake LLM
--------------
A real LLM's extraction quality depends on its language understanding,
which varies run to run and cannot be pinned down as reproducible
"evidence" the way this benchmark suite needs. What actually changed
between the old and new pipeline is not language understanding -- it's
*how much of the conversation, and which background facts, reach the
Extractor at all*. A marker-based fake LLM isolates exactly that
variable: given identical information, it always makes the identical
extraction decision, so any difference in results between the two
pipelines can only come from what each pipeline actually showed it, not
from LLM noise. See ``README.md``'s "Methodology" section for the fuller
rationale and its limits.
"""

from __future__ import annotations

import json
import re
from typing import List

_FACT_RE = re.compile(r"FACT\[(?P<id>[^\]]+)\]:\s*(?P<text>[^\n]+)")
_FACTIF_RE = re.compile(r"FACTIF\[(?P<needle>[^\]]+)\]:\s*(?P<text>[^\n]+)")


class MarkerLLM:
    """Fake ``LLMInterface`` implementation: extracts exactly what its markers say.

    Parameters
    ----------
    default_memory_type, default_confidence, default_importance
        Canned responses for the Classifier/ImportanceScorer stages --
        their content doesn't matter for these benchmarks (categories 1-3
        and 5 don't inspect memory type or scores; category 4 only
        inspects which facts were saved), only that they're valid,
        stable JSON.
    """

    def __init__(
        self,
        default_memory_type: str = "fact",
        default_confidence: float = 0.9,
        default_importance: float = 0.5,
    ) -> None:
        self.call_count = 0
        self.prompts: List[str] = []
        self._memory_type = default_memory_type
        self._confidence = default_confidence
        self._importance = default_importance

    def generate(self, prompt: str) -> str:
        self.call_count += 1
        self.prompts.append(prompt)

        if "Conversation:\n" in prompt:
            return self._extract(prompt)
        if "Available memory types:" in prompt:
            return json.dumps(
                {
                    "memory_type": self._memory_type,
                    "confidence": self._confidence,
                    "reason": "benchmark-scripted",
                }
            )
        if "Classification:\n" in prompt:
            return json.dumps({"score": self._importance, "reason": "benchmark-scripted"})
        raise AssertionError(f"Unrecognised prompt shape:\n{prompt[:300]}")

    def _extract(self, prompt: str) -> str:
        # Everything before "Conversation:\n" is the fixed instructions
        # plus -- only when the caller supplied existing_context -- the
        # EXISTING CONTEXT block. Everything after is the new-evidence
        # conversation text (or, for a first_run/fallback save, the
        # entire conversation).
        preamble, evidence = prompt.split("Conversation:\n", 1)

        facts = []
        for match in _FACT_RE.finditer(evidence):
            facts.append(
                {"text": match.group("text").strip(), "evidence": "scripted", "confidence": 0.9}
            )

        for match in _FACTIF_RE.finditer(evidence):
            needle = match.group("needle")
            # Exclude only this match's own span so its bracket syntax
            # can't trivially satisfy its own containment check, while
            # still allowing genuine same-batch visibility (a different
            # turn in the same evidence mentioning the needle) to count.
            evidence_excluding_self = evidence[: match.start()] + evidence[match.end() :]
            if needle in preamble or needle in evidence_excluding_self:
                facts.append(
                    {
                        "text": match.group("text").strip(),
                        "evidence": "scripted-resolved",
                        "confidence": 0.85,
                    }
                )
            # else: silently fails to extract -- exactly the property
            # this benchmark suite measures.

        return json.dumps(facts)

    @property
    def extractor_prompts(self) -> List[str]:
        """Every prompt shown to the Extractor (excludes classify/importance calls)."""
        return [p for p in self.prompts if "Conversation:\n" in p]
