"""Context builder for Haven Ontology retrieval.

Implements the "Context Builder" stage described in
``docs/architecture/ONTOLOGY_SPEC.md``, immediately downstream of
:mod:`~obsidian.memory_engine.deterministic_slot_allocator` ("Slot
Allocator"):

::

    RankedCandidate[]
        │
        ▼
    ContextBuilder      (this module)
        │
        ▼
    str                 (LLM prompt context)

This module has exactly one responsibility: render an already-allocated
list of :class:`~obsidian.ontology.retrieval_models.RankedCandidate`
objects into the deterministic context string handed to the LLM.

Explicitly out of scope
------------------------
* **No ranking.** Input order is trusted and reproduced exactly; nothing
  here reads, recomputes, or reorders by ``final_score`` or
  ``score_breakdown``. That is
  :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s
  responsibility.
* **No allocation.** No ``max_results``/budget truncation happens here —
  the input list is assumed to already be the allocated slice produced by
  :class:`~obsidian.memory_engine.deterministic_slot_allocator.DeterministicSlotAllocator`.
* **No retrieval.** Nothing is fetched from a
  :class:`~obsidian.memory_engine.memory_store.MemoryStore` or
  :class:`~obsidian.ontology.concept_graph.ConceptGraph`.
* **No graph traversal.** ``Candidate.supporting_concepts`` is never
  inspected or walked; concept/relationship structure is a retrieval
  concern, not a context-formatting concern.
* **No markdown parsing.** ``canonical_fact`` is treated as an opaque
  string and embedded verbatim; this module never parses, interprets, or
  rewrites Markdown syntax that might appear inside it.
* **No mutation.** ``RankedCandidate``, ``Candidate``, and
  ``KnowledgeObject`` are frozen dataclasses and are never modified.

What the rendered context exposes
----------------------------------
For each candidate, exactly six ``KnowledgeObject`` fields are rendered,
per the task's explicit field list:

* ``canonical_fact``
* ``memory_type``
* ``confidence``
* ``importance``
* ``confirmation_count``
* validity dates (``valid_from`` and ``valid_until``)

:class:`~obsidian.ontology.retrieval_models.RetrievalTrace` is never
imported, referenced, or exposed — it is a diagnostics-only artifact per
its own docstring ("must NEVER be passed to, or serialised into, an LLM
prompt"). For the same reason, retrieval-internal fields that are *not*
in the task's field list — ``final_score``, ``score_breakdown``,
``activation_score``, ``attachment_relevance``, ``supporting_concepts``,
``retrieval_metadata``, and ``KnowledgeObject.id``/``metadata`` — are
deliberately left out of the rendered text. They are ranking/retrieval
diagnostics, not facts, and leaking them into the LLM's context would
blur exactly the boundary ``RetrievalTrace`` exists to enforce.

Decision Memory (one narrow, additive exception)
--------------------------------------------------
A candidate whose ``memory_type`` is ``MemoryType.DECISION`` *and* whose
``KnowledgeObject.metadata`` carries a
:class:`~obsidian.manager_ai.models.DecisionMetadata` (see
:func:`~obsidian.manager_ai.models.get_decision_metadata`) gets extra
lines appended after its normal six-field block: ``status``, ``reason``,
``alternatives_considered``, ``supersedes``, and ``superseded_by``. This
is the one deliberate exception to "no metadata is ever rendered" above —
scoped to that one, structured, decision-specific sub-object, never
arbitrary ``metadata`` contents. Every non-decision candidate, and every
``MemoryType.DECISION`` candidate with no ``DecisionMetadata`` attached
(e.g. one written before this feature existed), renders byte-identical to
the six-field format that existed before Decision Memory — this is purely
additive, not a change to the existing contract.

Deterministic formatting
--------------------------
* **Ordering.** Candidates are rendered in input order, one-to-one; the
  list is never sorted. Each block is prefixed with its 1-based input
  position (``[1]``, ``[2]``, ...) purely as a stable, order-derived
  label the LLM can refer back to — it is not a recomputed rank.
* **Floats.** ``confidence`` and ``importance`` are formatted with
  ``:.2f`` (fixed 2 decimal places) so output never depends on
  floating-point representation noise (e.g. ``0.30000000000000004``).
* **Dates.** ``valid_from`` and ``valid_until`` are formatted with
  :meth:`datetime.isoformat`, the same convention used by
  :meth:`~obsidian.manager_ai.models.KnowledgeObject.to_dict`. A ``None``
  ``valid_until`` (still active) renders as the literal ``"none"``, so
  every block has the same fixed shape regardless of expiry state.
* **Empty input.** ``build([])`` returns ``""``.
* **Separator.** Blocks are joined with a single blank line (``"\\n\\n"``)
  between them; there is no trailing separator after the last block.

Because every value plugged into the format is either read verbatim from
immutable input or derived by a pure, order-independent formatting rule,
calling :meth:`ContextBuilder.build` twice with the same input always
produces byte-identical output.
"""

from __future__ import annotations

from typing import List

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject, get_decision_metadata
from obsidian.ontology.retrieval_models import RankedCandidate

# Fixed placeholder for a still-active KnowledgeObject (valid_until is
# None). Keeps every rendered block the same shape regardless of expiry
# state -- see "Deterministic formatting" above.
_NO_EXPIRY = "none"


class ContextBuilder:
    """Renders allocated :class:`RankedCandidate` objects into an LLM context string.

    Stateless and reusable across calls to :meth:`build` with different
    candidate lists.

    Examples
    --------
    >>> builder = ContextBuilder()
    >>> context = builder.build(ranked_candidates)  # doctest: +SKIP
    >>> context.startswith("[1]")  # doctest: +SKIP
    True
    """

    def build(self, ranked_candidates: List[RankedCandidate]) -> str:
        """Render *ranked_candidates* into a deterministic context string.

        Parameters
        ----------
        ranked_candidates : list[RankedCandidate]
            Candidates to render, typically the output of
            :meth:`~obsidian.memory_engine.deterministic_slot_allocator.DeterministicSlotAllocator.allocate`.
            Not mutated; not re-sorted; not truncated. Rendered in the
            exact order given.

        Returns
        -------
        str
            The formatted context string, one block per candidate in
            input order, separated by a blank line. ``""`` when
            *ranked_candidates* is empty.
        """
        blocks = [
            self._format_candidate(index, ranked_candidate)
            for index, ranked_candidate in enumerate(ranked_candidates, start=1)
        ]
        return "\n\n".join(blocks)

    @staticmethod
    def _format_candidate(index: int, ranked_candidate: RankedCandidate) -> str:
        """Format a single candidate as a fixed-field-order block.

        ``canonical_fact`` is embedded verbatim (see "No markdown parsing"
        above), so a fact containing embedded newlines will span more than
        the three lines this format otherwise produces — that is expected,
        not an error, since the field is never rewritten or escaped.
        """
        ko = ranked_candidate.candidate.knowledge_object

        valid_until = ko.valid_until.isoformat() if ko.valid_until is not None else _NO_EXPIRY

        block = (
            f"[{index}] {ko.canonical_fact}\n"
            f"    type: {ko.memory_type.value} | "
            f"confidence: {ko.confidence:.2f} | "
            f"importance: {ko.importance:.2f} | "
            f"confirmations: {ko.confirmation_count}\n"
            f"    valid_from: {ko.valid_from.isoformat()} | "
            f"valid_until: {valid_until}"
        )

        decision_lines = ContextBuilder._format_decision_fields(ko)
        if decision_lines:
            block += "\n" + decision_lines
        return block

    @staticmethod
    def _format_decision_fields(ko: KnowledgeObject) -> str:
        """Render ``DecisionMetadata`` fields for a decision candidate, if any.

        Returns ``""`` for every non-``MemoryType.DECISION`` candidate and
        for a decision candidate with no ``DecisionMetadata`` attached —
        see "Decision Memory (one narrow, additive exception)" in the
        module docstring.
        """
        if ko.memory_type != MemoryType.DECISION:
            return ""
        metadata = get_decision_metadata(ko)
        if metadata is None:
            return ""

        lines = [f"    status: {metadata.status.value}"]
        if metadata.reason:
            lines.append(f"    reason: {metadata.reason}")
        if metadata.alternatives_considered:
            lines.append(
                "    alternatives_considered: "
                + ", ".join(metadata.alternatives_considered)
            )
        if metadata.supersedes is not None:
            lines.append(f"    supersedes: {metadata.supersedes}")
        if metadata.superseded_by is not None:
            lines.append(f"    superseded_by: {metadata.superseded_by}")
        return "\n".join(lines)
