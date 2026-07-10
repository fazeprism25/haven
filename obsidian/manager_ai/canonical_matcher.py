"""Canonical Matcher stage of the Manager AI pipeline.

The Canonical Matcher compares an extracted fact against the existing
canonical knowledge objects stored in the vault and returns a
:class:`KnowledgeDecision` that tells the pipeline what to do next.
"""

from __future__ import annotations

import string
from typing import List, Optional, Tuple

from obsidian.manager_ai.models import (
    ExtractedFact,
    KnowledgeDecision,
    KnowledgeObject,
)

_TRAILING_PUNCT = string.punctuation + " "


class CanonicalMatcher:
    """Compares an extracted fact against existing knowledge objects.

    Matching is deliberately conservative and fully deterministic -- no
    semantic similarity, no embeddings, no LLM call:

    * **CONFIRM** on an exact normalised‑lowercase text match.
    * **UPDATE** on a *strict whole‑word prefix extension*: the new fact
      begins with an existing fact's canonical text verbatim (ignoring any
      trailing sentence punctuation on the existing text, e.g. a trailing
      ``"."``) and adds at least one more word (e.g. ``"I work at Google."``
      → ``"I work at Google as a staff engineer."``, or ``"I live in
      Muscat."`` → ``"I live in Muscat, Oman."``). This is the only shape
      of refinement that is safe to detect by text alone -- it *elaborates*
      the prior statement rather than replacing it, so it cannot fire on a
      contradiction (a contradiction rewrites the text, breaking the prefix)
      and the old text's meaning is fully contained in the new one. The
      character immediately following the matched prefix must not be
      alphanumeric (so ``"Google"`` cannot match a prefix of ``"Googleplex"``)
      and the remainder must contain real word content (so appending only
      punctuation, e.g. ``"Google."`` → ``"Google!"``, does not count).
      False negatives (a genuine refinement phrased differently) are
      preferred to false positives (wrongly overwriting an unrelated
      memory), matching the requirement that this stage never silently
      destroy a memory it shouldn't. An archived/superseded object
      (``valid_until`` set) is never an UPDATE target.
    * **NEW** otherwise.

    ``SUPERSEDE`` is intentionally never returned here -- driving true
    write‑time supersession is deferred work (see
    ``obsidian/docs/TECH_DEBT.md``), and detecting a contradiction is exactly
    the semantic judgement this deterministic matcher deliberately does not
    make.

    Parameters
    ----------
    threshold : float, optional
        Reserved for a future semantic‑matching threshold (currently
        unused -- the rules above are purely textual).
    """

    def __init__(self, threshold: float = 0.0) -> None:
        self._threshold: float = threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match(
        self,
        fact: ExtractedFact,
        existing: List[KnowledgeObject],
    ) -> KnowledgeDecision:
        """Return the :class:`KnowledgeDecision` for *fact* (decision only).

        Thin wrapper over :meth:`match_with_target` for callers that only
        need the decision and not which object it matched. See
        :meth:`match_with_target` and the class docstring for the rules.
        """
        return self.match_with_target(fact, existing)[0]

    def match_with_target(
        self,
        fact: ExtractedFact,
        existing: List[KnowledgeObject],
    ) -> Tuple[KnowledgeDecision, Optional[KnowledgeObject]]:
        """Return the :class:`KnowledgeDecision` *and* the object it matched.

        Parameters
        ----------
        fact : ExtractedFact
            The fact that was extracted and classified.
        existing : list[KnowledgeObject]
            The current set of canonical knowledge objects stored in the
            vault.

        Returns
        -------
        tuple[KnowledgeDecision, KnowledgeObject | None]
            - ``(NEW, None)`` -- no match; a new object should be created.
            - ``(CONFIRM, obj)`` -- ``obj`` has an identical canonical fact.
            - ``(UPDATE, obj)`` -- ``obj`` is refined in place by *fact*
              (strict whole‑word prefix extension; see the class docstring).

        The returned object (for CONFIRM/UPDATE) is an element of *existing*,
        so the caller can locate and replace it directly without repeating
        the matching logic.
        """
        normalised_fact = fact.text.strip().lower()

        # Exact match → CONFIRM (first match in list order; unchanged
        # behaviour from before UPDATE existed). Archived objects
        # (valid_until set) are skipped, same as the refinement path below
        # -- restating a fact whose memory was archived/superseded must
        # create a fresh active object, not silently re-confirm the dead one.
        for obj in existing:
            if obj.valid_until is not None:
                continue
            if obj.canonical_fact.strip().lower() == normalised_fact:
                return KnowledgeDecision.CONFIRM, obj

        # Conservative refinement → UPDATE.
        target = self._refinement_target(normalised_fact, existing)
        if target is not None:
            return KnowledgeDecision.UPDATE, target

        # No match found → create a new knowledge object.
        return KnowledgeDecision.NEW, None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _refinement_target(
        normalised_fact: str,
        existing: List[KnowledgeObject],
    ) -> Optional[KnowledgeObject]:
        """Return the object *fact* refines in place, or ``None``.

        A candidate qualifies only when *normalised_fact* starts with its
        normalised ``canonical_fact`` (trailing sentence punctuation on the
        existing text ignored, so a trailing ``"."`` doesn't defeat the
        match) at a whole‑word boundary -- the character right after the
        matched prefix must not be alphanumeric -- and the remainder
        contains real word content, not just punctuation. Archived objects
        (``valid_until`` set), empty facts, and exact matches (handled as
        CONFIRM by the caller) are skipped. When several existing objects
        qualify, the most specific (longest matched prefix) wins; ties break
        on ascending ``id`` string so the result is deterministic regardless
        of *existing*'s order.
        """
        best: Optional[KnowledgeObject] = None
        best_len = -1
        for obj in existing:
            if obj.valid_until is not None:
                continue  # never refine an archived/inactive memory
            normalised_existing = obj.canonical_fact.strip().lower()
            if not normalised_existing or normalised_existing == normalised_fact:
                continue
            prefix = normalised_existing.rstrip(_TRAILING_PUNCT)
            if not prefix or not normalised_fact.startswith(prefix):
                continue
            remainder = normalised_fact[len(prefix) :]
            if not remainder or remainder[0].isalnum():
                continue  # e.g. "Google" is not a whole-word prefix of "Googleplex"
            if not any(char.isalnum() for char in remainder):
                continue  # only punctuation was appended -- not a real refinement
            obj_len = len(prefix)
            if obj_len > best_len or (
                obj_len == best_len
                and (best is None or str(obj.id) < str(best.id))
            ):
                best = obj
                best_len = obj_len
        return best
