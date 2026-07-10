"""Knowledge Updater stage of the Manager AI pipeline.

The Knowledge Updater applies a :class:`KnowledgeDecision` to an existing
(or newly created) :class:`KnowledgeObject`.  It is responsible for
maintaining evidence chains, temporal metadata, confidence, and canonical
knowledge integrity.

The updater never performs matching itself.  It only applies one of the
:class:`KnowledgeDecision` values:

- ``NEW``
- ``CONFIRM``
- ``UPDATE``
- ``SUPERSEDE``
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import List, Optional, Tuple

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import (
    ClassificationResult,
    DecisionMetadata,
    DecisionStatus,
    EvidenceEntry,
    ExtractedFact,
    ImportanceResult,
    KnowledgeDecision,
    KnowledgeObject,
    get_decision_metadata,
    with_decision_metadata,
)


class KnowledgeUpdater:
    """Applies a :class:`KnowledgeDecision` to a :class:`KnowledgeObject`.

    Implements all four decisions: ``NEW``, ``CONFIRM``, ``UPDATE``, and
    ``SUPERSEDE``. :class:`~obsidian.manager_ai.pipeline.ManagerPipeline`,
    which orchestrates a full conversation through this class automatically,
    drives ``NEW``, ``CONFIRM``, and ``UPDATE`` (in-place refinement)
    end-to-end. ``SUPERSEDE`` is still not auto-driven -- it must be invoked
    directly (see :meth:`supersede_decision` below and
    ``obsidian/docs/DECISION_MEMORY.md``).
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply(
        self,
        decision: KnowledgeDecision,
        fact: ExtractedFact,
        knowledge: Optional[KnowledgeObject],
        classification: Optional[ClassificationResult] = None,
        importance: Optional[ImportanceResult] = None,
    ) -> KnowledgeObject:
        """Apply *decision* to *knowledge* using the information in *fact*.

        Parameters
        ----------
        decision : KnowledgeDecision
            The decision returned by the :class:`CanonicalMatcher`.
        fact : ExtractedFact
            The extracted fact that triggered the decision.
        knowledge : KnowledgeObject | None
            The existing knowledge object that was matched, or ``None``
            when the decision is ``NEW``.
        classification : ClassificationResult | None
            The :class:`~obsidian.manager_ai.classifier.Classifier` output
            for *fact*, if available. Only consulted for ``NEW`` (a
            ``CONFIRM``/``UPDATE``/``SUPERSEDE`` target already has a
            ``memory_type`` from when it was first created); ``None``
            falls back to :attr:`KnowledgeObject.memory_type`'s default
            (``MemoryType.FACT``), matching this method's pre-existing
            behaviour for callers that don't pass it.
        importance : ImportanceResult | None
            The :class:`~obsidian.manager_ai.importance.ImportanceScorer`
            output for *fact*, if available. Same ``NEW``-only handling
            and default-preserving fallback as *classification*.

        Returns
        -------
        KnowledgeObject
            The resulting knowledge object after the decision has been
            applied.

        Raises
        ------
        ValueError
            If *decision* is ``CONFIRM``, ``UPDATE``, or ``SUPERSEDE``
            and *knowledge* is ``None``.
        """
        if decision == KnowledgeDecision.NEW:
            return self._apply_new(fact, classification, importance)

        if knowledge is None:
            raise ValueError(
                f"Cannot apply decision '{decision.value}' without an "
                f"existing knowledge object."
            )

        if decision == KnowledgeDecision.CONFIRM:
            return self._apply_confirm(fact, knowledge)

        if decision == KnowledgeDecision.UPDATE:
            return self._apply_update(fact, knowledge)

        if decision == KnowledgeDecision.SUPERSEDE:
            return self._apply_supersede(fact, knowledge)

        # Should never reach here
        raise ValueError(f"Unknown decision: {decision}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_new(
        fact: ExtractedFact,
        classification: Optional[ClassificationResult] = None,
        importance: Optional[ImportanceResult] = None,
    ) -> KnowledgeObject:
        """Create a brand‑new :class:`KnowledgeObject` from *fact*.

        The new object uses the fact's text as its ``canonical_fact``,
        records a proper :class:`EvidenceEntry` (source event, evidence
        text, and confidence) in its ``evidence_chain``, and sets
        ``valid_from`` to the current UTC time. ``memory_type``/
        ``importance`` are taken from *classification*/*importance* when
        given, else fall back to :class:`KnowledgeObject`'s own defaults.
        ``confidence`` is always taken from ``fact.confidence`` (the
        Extractor's own confidence that the extracted text is correct) --
        the same field :meth:`_apply_update` already anchors
        ``confidence`` to when refining an existing object.
        ``classification.confidence`` (confidence in the *type*
        assignment, not the fact itself) is deliberately not folded in
        here, matching that existing precedent.
        """
        evidence = EvidenceEntry(
            source_event_id=fact.source_event_id,
            evidence=fact.evidence,
            confidence=fact.confidence,
            timestamp=datetime.utcnow(),
        )
        kwargs: dict = {
            "canonical_fact": fact.text,
            "confidence": fact.confidence,
            "evidence_chain": [evidence],
            "valid_from": datetime.utcnow(),
            "last_confirmed": datetime.utcnow(),
            "confirmation_count": 1,
        }
        if classification is not None:
            kwargs["memory_type"] = classification.memory_type
        if importance is not None:
            kwargs["importance"] = importance.score
        return KnowledgeObject(**kwargs)

    @staticmethod
    def _apply_confirm(
        fact: ExtractedFact,
        knowledge: KnowledgeObject,
    ) -> KnowledgeObject:
        """Confirm an existing knowledge object.

        Increments ``confirmation_count``, updates ``last_confirmed``,
        appends an :class:`EvidenceEntry` to ``evidence_chain``, and
        increases ``confidence`` slightly while clamping to a maximum
        of 1.0.  All other fields remain unchanged.
        """
        new_confidence = min(knowledge.confidence + 0.05, 1.0)

        new_evidence = EvidenceEntry(
            source_event_id=fact.source_event_id,
            evidence=fact.evidence,
            confidence=fact.confidence,
            timestamp=datetime.utcnow(),
        )

        return KnowledgeObject(
            id=knowledge.id,
            canonical_fact=knowledge.canonical_fact,
            memory_type=knowledge.memory_type,
            confidence=new_confidence,
            importance=knowledge.importance,
            evidence_chain=knowledge.evidence_chain + [new_evidence],
            valid_from=knowledge.valid_from,
            valid_until=knowledge.valid_until,
            last_confirmed=datetime.utcnow(),
            confirmation_count=knowledge.confirmation_count + 1,
            metadata=knowledge.metadata,
        )

    @staticmethod
    def _apply_update(
        fact: ExtractedFact,
        knowledge: KnowledgeObject,
    ) -> KnowledgeObject:
        """Update an existing knowledge object with new information.

        Preserves the ``id``, updates ``canonical_fact`` using the new
        extracted fact, appends an :class:`EvidenceEntry`` to the
        ``evidence_chain``, updates ``last_confirmed``, increments
        ``confirmation_count``, and updates ``confidence``
        deterministically by combining the existing confidence with the
        incoming fact confidence without exceeding 1.0.  All other
        fields remain unchanged.
        """
        # Combine confidences: average of existing and incoming,
        # clamped to 1.0
        new_confidence = min(
            (knowledge.confidence + fact.confidence) / 2.0,
            1.0,
        )

        new_evidence = EvidenceEntry(
            source_event_id=fact.source_event_id,
            evidence=fact.evidence,
            confidence=fact.confidence,
            timestamp=datetime.utcnow(),
        )

        return KnowledgeObject(
            id=knowledge.id,
            canonical_fact=fact.text,
            memory_type=knowledge.memory_type,
            confidence=new_confidence,
            importance=knowledge.importance,
            evidence_chain=knowledge.evidence_chain + [new_evidence],
            valid_from=knowledge.valid_from,
            valid_until=knowledge.valid_until,
            last_confirmed=datetime.utcnow(),
            confirmation_count=knowledge.confirmation_count + 1,
            metadata=knowledge.metadata,
        )

    @staticmethod
    def _apply_supersede(
        fact: ExtractedFact,
        knowledge: KnowledgeObject,
    ) -> KnowledgeObject:
        """Supersede an existing knowledge object.

        Archives the existing object by setting ``valid_until`` to the
        current UTC time, then creates a brand‑new :class:`KnowledgeObject`
        with the same ``memory_type``, a fresh :class:`EvidenceEntry``,
        ``valid_from`` set to the current UTC time, and the previous
        object's ``id`` recorded in ``metadata["supersedes"]``.
        """
        # Archive the old object (the caller will persist this separately)
        # We return only the new object; the caller is responsible for
        # persisting the archived version.
        new_evidence = EvidenceEntry(
            source_event_id=fact.source_event_id,
            evidence=fact.evidence,
            confidence=fact.confidence,
            timestamp=datetime.utcnow(),
        )

        new_metadata = dict(knowledge.metadata)
        new_metadata["supersedes"] = str(knowledge.id)

        return KnowledgeObject(
            canonical_fact=fact.text,
            memory_type=knowledge.memory_type,
            evidence_chain=[new_evidence],
            valid_from=datetime.utcnow(),
            last_confirmed=datetime.utcnow(),
            confirmation_count=1,
            metadata=new_metadata,
        )

    # ------------------------------------------------------------------
    # Decision Memory
    # ------------------------------------------------------------------

    @staticmethod
    def supersede_decision(
        fact: ExtractedFact,
        knowledge: KnowledgeObject,
        reason: str = "",
        alternatives_considered: Optional[List[str]] = None,
    ) -> Tuple[KnowledgeObject, KnowledgeObject]:
        """Supersede an existing ``MemoryType.DECISION`` object with a new one.

        Reuses :meth:`_apply_supersede` for the generic archive-and-recreate
        mechanics already shared by every memory type, then layers
        :class:`DecisionMetadata` bookkeeping on top: the new object records
        ``supersedes=knowledge.id`` plus *reason*/*alternatives_considered*;
        the old object's own ``DecisionMetadata`` is updated to
        ``status=SUPERSEDED`` with ``superseded_by`` pointing at the new
        object's id, so the link is queryable from either side.

        Parameters
        ----------
        fact : ExtractedFact
            The fact that triggered the new decision.
        knowledge : KnowledgeObject
            The existing decision being superseded. Must have
            ``memory_type == MemoryType.DECISION``.
        reason : str
            Why the new decision was made.
        alternatives_considered : list[str], optional
            Other options considered before choosing the new decision.

        Returns
        -------
        tuple[KnowledgeObject, KnowledgeObject]
            ``(archived_old, new_decision)``. The caller persists both
            separately (e.g. via ``VaultWriter``) -- this method only
            computes the two resulting objects, matching
            :meth:`_apply_supersede`'s existing division of responsibility.

        Raises
        ------
        ValueError
            If *knowledge* is not ``MemoryType.DECISION``.
        """
        if knowledge.memory_type != MemoryType.DECISION:
            raise ValueError(
                "supersede_decision requires a MemoryType.DECISION knowledge "
                f"object; got {knowledge.memory_type.value}"
            )

        new_ko = KnowledgeUpdater._apply_supersede(fact, knowledge)
        new_ko = with_decision_metadata(
            new_ko,
            DecisionMetadata(
                reason=reason,
                alternatives_considered=list(alternatives_considered or []),
                status=DecisionStatus.ACTIVE,
                supersedes=knowledge.id,
            ),
        )

        old_decision_metadata = get_decision_metadata(knowledge) or DecisionMetadata()
        archived_old = replace(knowledge, valid_until=new_ko.valid_from)
        archived_old = with_decision_metadata(
            archived_old,
            replace(
                old_decision_metadata,
                status=DecisionStatus.SUPERSEDED,
                superseded_by=new_ko.id,
            ),
        )

        return archived_old, new_ko
