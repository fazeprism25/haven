"""Immutable domain models for the Haven Ontology (Phase 1).

Phase 1 implements the data model layer only.  The graph, validator,
writer, and retrieval components are out of scope and must not be added
here.

All models are frozen dataclasses.  Identifiers are derived
deterministically using :mod:`obsidian.ontology.identity` so that
duplicate proposals collapse to the same object without database lookups.

Model hierarchy
---------------
::

    Concept          – stable semantic entity (Haven, Claude, DTU …)
    Relationship     – typed edge between two Concepts
    Attachment       – evidence link from a KnowledgeObject to a Concept
    OntologyProposal – proposed graph mutation (never applied directly)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from obsidian.ontology.enums import OntologyRelationshipType, ProposalType
from obsidian.ontology.identity import (
    attachment_id as _derive_attachment_id,
    concept_id as _derive_concept_id,
    relationship_id as _derive_relationship_id,
)


# ---------------------------------------------------------------------------
# Concept
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Concept:
    """A stable semantic entity in the Haven Ontology.

    Concepts represent *named things* – projects, people, technologies,
    organisations, locations – rather than facts about them.
    ``KnowledgeObject`` instances supply evidence *for* Concepts; Concepts
    do not own or embed knowledge themselves.

    Parameters
    ----------
    id : UUID
        Deterministic identifier derived from the canonical *label* via
        :func:`~obsidian.ontology.identity.concept_id`.  Callers should
        use :meth:`from_label` instead of supplying this field directly.
    label : str
        Canonical name of the concept (e.g. ``"Haven"``, ``"Claude"``).
        Used as the stable key for identity derivation.
    aliases : tuple[str, ...]
        Alternative names that also refer to this concept (e.g.
        ``("Claude AI", "Anthropic Claude")``).  Must not contain
        duplicates.
    description : str
        Optional free-text annotation providing context about the concept.
    created_at : datetime
        UTC timestamp when this concept was first registered.

    Raises
    ------
    ValueError
        If *label* is empty or whitespace-only.
    ValueError
        If *aliases* contains duplicate entries.

    Examples
    --------
    >>> c = Concept.from_label("Haven", description="Personal second-brain")
    >>> c.label
    'Haven'
    >>> c.id == Concept.from_label("Haven").id
    True.
    """

    id: UUID
    label: str
    aliases: Tuple[str, ...] = field(default_factory=tuple)
    description: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("label must not be empty or whitespace-only")
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError("aliases must be unique; duplicates are not allowed")

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_label(
        cls,
        label: str,
        aliases: Tuple[str, ...] = (),
        description: str = "",
        created_at: Optional[datetime] = None,
    ) -> "Concept":
        """Create a :class:`Concept` with a deterministic ID.

        The ``id`` is computed via
        :func:`~obsidian.ontology.identity.concept_id` so that the same
        label always yields the same concept, regardless of when or where
        the concept is created.

        Parameters
        ----------
        label : str
            Canonical name of the concept.
        aliases : tuple[str, ...]
            Alternative names.
        description : str
            Optional free-text description.
        created_at : datetime, optional
            Override the creation timestamp (defaults to ``datetime.utcnow()``).

        Returns
        -------
        Concept
            A fully initialised, immutable concept.
        """
        return cls(
            id=_derive_concept_id(label),
            label=label,
            aliases=aliases,
            description=description,
            created_at=created_at if created_at is not None else datetime.utcnow(),
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns
        -------
        dict
            Keys: ``id``, ``label``, ``aliases``, ``description``,
            ``created_at``.
        """
        return {
            "id": str(self.id),
            "label": self.label,
            "aliases": list(self.aliases),
            "description": self.description,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Concept":
        """Reconstruct a :class:`Concept` from a serialised dictionary.

        The stored ``id`` is used as-is; it is *not* re-derived from the
        label.  This preserves round-trip fidelity even if the
        normalisation rules in :func:`~obsidian.ontology.identity.concept_id`
        change in a future version.

        Parameters
        ----------
        data : dict
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        Concept
        """
        return cls(
            id=UUID(data["id"]),
            label=data["label"],
            aliases=tuple(data.get("aliases", [])),
            description=data.get("description", ""),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if "created_at" in data
                else datetime.utcnow()
            ),
        )


# ---------------------------------------------------------------------------
# Relationship
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Relationship:
    """A typed, directed, deterministic edge between two :class:`Concept` nodes.

    Relationships are directional: a ``"Haven" USES "Claude"`` edge is
    distinct from a ``"Claude" USES "Haven"`` edge.  The identifier is
    derived from ``(source_id, target_id, relationship_type)`` so that
    duplicate proposals produced by separate conversations collapse to a
    single edge in the graph.

    Parameters
    ----------
    id : UUID
        Deterministic identifier.  Use :meth:`create` to construct with
        the correct derived ID.
    source_id : UUID
        :class:`Concept` ID of the *from* end of the edge.
    target_id : UUID
        :class:`Concept` ID of the *to* end of the edge.
    relationship_type : OntologyRelationshipType
        Semantic type of the connection.
    confidence : float
        Confidence that this relationship is correct (0.0 – 1.0).
        Defaults to ``1.0`` for relationships established by the graph.

    Raises
    ------
    ValueError
        If *source_id* equals *target_id* (self-loops are not permitted).
    ValueError
        If *confidence* is outside ``[0.0, 1.0]``.

    Examples
    --------
    >>> from obsidian.ontology.identity import concept_id
    >>> haven = concept_id("Haven")
    >>> claude = concept_id("Claude")
    >>> r = Relationship.create(haven, claude, OntologyRelationshipType.USES)
    >>> r.relationship_type
    <OntologyRelationshipType.USES: 'uses'>.
    """

    id: UUID
    source_id: UUID
    target_id: UUID
    relationship_type: OntologyRelationshipType
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if self.source_id == self.target_id:
            raise ValueError(
                "source_id and target_id must differ; self-loops are not permitted"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0.0, 1.0]; got {self.confidence}"
            )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        source_id: UUID,
        target_id: UUID,
        relationship_type: OntologyRelationshipType,
        confidence: float = 1.0,
    ) -> "Relationship":
        """Create a :class:`Relationship` with a deterministic ID.

        Parameters
        ----------
        source_id : UUID
            Concept ID of the source node.
        target_id : UUID
            Concept ID of the target node.
        relationship_type : OntologyRelationshipType
            Semantic type of the edge.
        confidence : float
            Confidence score (0.0 – 1.0).

        Returns
        -------
        Relationship
        """
        return cls(
            id=_derive_relationship_id(source_id, target_id, relationship_type.value),
            source_id=source_id,
            target_id=target_id,
            relationship_type=relationship_type,
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns
        -------
        dict
            Keys: ``id``, ``source_id``, ``target_id``,
            ``relationship_type``, ``confidence``.
        """
        return {
            "id": str(self.id),
            "source_id": str(self.source_id),
            "target_id": str(self.target_id),
            "relationship_type": self.relationship_type.value,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Relationship":
        """Reconstruct a :class:`Relationship` from a serialised dictionary.

        Parameters
        ----------
        data : dict
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        Relationship
        """
        return cls(
            id=UUID(data["id"]),
            source_id=UUID(data["source_id"]),
            target_id=UUID(data["target_id"]),
            relationship_type=OntologyRelationshipType(data["relationship_type"]),
            confidence=data.get("confidence", 1.0),
        )


# ---------------------------------------------------------------------------
# Attachment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Attachment:
    """Evidence link connecting a ``KnowledgeObject`` to a :class:`Concept`.

    Attachments are the bridge between the Manager AI pipeline output
    (``KnowledgeObject`` instances) and the Concept Graph.  A single
    ``KnowledgeObject`` may be attached to multiple Concepts (e.g. a fact
    about Haven using Claude could attach to both the ``"Haven"`` and
    ``"Claude"`` concepts).

    Attachments prevent Concepts from owning ``KnowledgeObject`` instances
    directly, which preserves the invariant that ``KnowledgeObjects`` are
    the sole source of truth.

    Parameters
    ----------
    id : UUID
        Deterministic identifier derived from ``(knowledge_object_id,
        concept_id)`` via
        :func:`~obsidian.ontology.identity.attachment_id`.  Use
        :meth:`create` to construct with the correct derived ID.
    knowledge_object_id : UUID
        The ``id`` of the ``KnowledgeObject`` serving as evidence.
    concept_id : UUID
        The ``id`` of the :class:`Concept` being supported.
    relevance : float
        Strength of the evidence link (0.0 – 1.0).  Defaults to ``1.0``.

    Raises
    ------
    ValueError
        If *relevance* is outside ``[0.0, 1.0]``.

    Examples
    --------
    >>> from uuid import UUID
    >>> from obsidian.ontology.identity import concept_id
    >>> ko_id = UUID("12345678-1234-5678-1234-567812345678")
    >>> haven = concept_id("Haven")
    >>> a = Attachment.create(ko_id, haven, relevance=0.9)
    >>> a.relevance
    0.9.
    """

    id: UUID
    knowledge_object_id: UUID
    concept_id: UUID
    relevance: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.relevance <= 1.0:
            raise ValueError(
                f"relevance must be in [0.0, 1.0]; got {self.relevance}"
            )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        knowledge_object_id: UUID,
        concept_id: UUID,
        relevance: float = 1.0,
    ) -> "Attachment":
        """Create an :class:`Attachment` with a deterministic ID.

        Parameters
        ----------
        knowledge_object_id : UUID
            ID of the ``KnowledgeObject`` supplying the evidence.
        concept_id : UUID
            ID of the :class:`Concept` being supported.
        relevance : float
            Strength of the evidence link (0.0 – 1.0).

        Returns
        -------
        Attachment
        """
        return cls(
            id=_derive_attachment_id(knowledge_object_id, concept_id),
            knowledge_object_id=knowledge_object_id,
            concept_id=concept_id,
            relevance=relevance,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns
        -------
        dict
            Keys: ``id``, ``knowledge_object_id``, ``concept_id``,
            ``relevance``.
        """
        return {
            "id": str(self.id),
            "knowledge_object_id": str(self.knowledge_object_id),
            "concept_id": str(self.concept_id),
            "relevance": self.relevance,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Attachment":
        """Reconstruct an :class:`Attachment` from a serialised dictionary.

        Parameters
        ----------
        data : dict
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        Attachment
        """
        return cls(
            id=UUID(data["id"]),
            knowledge_object_id=UUID(data["knowledge_object_id"]),
            concept_id=UUID(data["concept_id"]),
            relevance=data.get("relevance", 1.0),
        )


# ---------------------------------------------------------------------------
# OntologyProposal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OntologyProposal:
    """A proposed mutation to the Concept Graph.

    Proposals are the output of the Ontology Manager stage.  They are
    validated by the Ontology Validator (Phase 2) before any graph
    mutation occurs.  A proposal **never** modifies the graph directly.

    The *payload* structure depends on *proposal_type*:

    ``CREATE_CONCEPT``
        ``{"label": str, "aliases": list[str], "description": str}``

    ``CREATE_RELATIONSHIP``
        ``{"source_id": str, "target_id": str,``
        ``"relationship_type": str, "confidence": float}``

    ``ATTACH_KNOWLEDGE_OBJECT``
        ``{"knowledge_object_id": str, "concept_id": str,``
        ``"relevance": float}``

    Parameters
    ----------
    proposal_type : ProposalType
        The category of graph mutation being proposed.
    payload : dict
        The data required to execute the proposal.  Must not be empty.
    reason : str
        Human-readable explanation of why this change was proposed.

    Raises
    ------
    ValueError
        If *payload* is an empty mapping.

    Examples
    --------
    >>> p = OntologyProposal(
    ...     proposal_type=ProposalType.CREATE_CONCEPT,
    ...     payload={"label": "Haven", "aliases": [], "description": ""},
    ...     reason="Detected new entity in conversation",
    ... )
    >>> p.proposal_type
    <ProposalType.CREATE_CONCEPT: 'create_concept'>.
    """

    proposal_type: ProposalType
    payload: Dict[str, Any]
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.payload:
            raise ValueError("payload must not be empty")

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dictionary representation.

        Returns
        -------
        dict
            Keys: ``proposal_type``, ``payload``, ``reason``.
        """
        return {
            "proposal_type": self.proposal_type.value,
            "payload": self.payload,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OntologyProposal":
        """Reconstruct an :class:`OntologyProposal` from a serialised dictionary.

        Parameters
        ----------
        data : dict
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        OntologyProposal
        """
        return cls(
            proposal_type=ProposalType(data["proposal_type"]),
            payload=data["payload"],
            reason=data.get("reason", ""),
        )
