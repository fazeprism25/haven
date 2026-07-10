"""Value objects for the Obsidian Memory data model.

Value objects are immutable, have no identity, and are compared
by their attributes.  They are used as components of the larger
data model types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from uuid import UUID, uuid4

from obsidian.core.enums import EntityType, RelationshipType


@dataclass(frozen=True)
class MemoryIdentity:
    """Uniquely identifies a memory across merges and supersedes.

    Parameters
    ----------
    identity_id : UUID
        The stable identity identifier.
    canonical_name : str
        The primary human‑readable name for this identity.
    aliases : tuple[str, ...]
        Alternative names that refer to the same identity.

    Raises
    ------
    ValueError
        If *canonical_name* is empty or *aliases* contains duplicates.
    """

    identity_id: UUID = field(default_factory=uuid4)
    canonical_name: str = ""
    aliases: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.canonical_name:
            raise ValueError("canonical_name must not be empty")
        if len(set(self.aliases)) != len(self.aliases):
            raise ValueError("aliases must be unique")

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        return {
            "identity_id": str(self.identity_id),
            "canonical_name": self.canonical_name,
            "aliases": list(self.aliases),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MemoryIdentity:
        """Create a MemoryIdentity from a dictionary."""
        return cls(
            identity_id=UUID(data["identity_id"]) if isinstance(data.get("identity_id"), str) else data.get("identity_id", uuid4()),
            canonical_name=data.get("canonical_name", ""),
            aliases=tuple(data.get("aliases", [])),
        )


@dataclass(frozen=True)
class Entity:
    """A named entity extracted from memory content.

    Parameters
    ----------
    name : str
        The entity text (e.g. ``"Alice"``, ``"OpenAI"``).
    entity_type : EntityType
        The semantic type of the entity.
    confidence : float
        Confidence score for the extraction (0.0 – 1.0).

    Raises
    ------
    ValueError
        If *name* is empty or *confidence* is outside [0, 1].
    """

    name: str = ""
    entity_type: EntityType = EntityType.OTHER
    confidence: float = 0.5

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("name must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        return {
            "name": self.name,
            "entity_type": self.entity_type.value,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Entity:
        """Create an Entity from a dictionary."""
        return cls(
            name=data.get("name", ""),
            entity_type=EntityType(data["entity_type"]) if "entity_type" in data else EntityType.OTHER,
            confidence=data.get("confidence", 0.5),
        )


@dataclass(frozen=True)
class TemporalContext:
    """Time range during which a memory fact is relevant.

    Parameters
    ----------
    created_at : datetime
        When the fact was first observed.
    updated_at : datetime
        When the fact was last updated.
    mentioned_at : datetime, optional
        When the fact was explicitly mentioned.
    valid_from : datetime, optional
        The beginning of the relevant time range.
    valid_until : datetime, optional
        The end of the relevant time range.

    Raises
    ------
    ValueError
        If *updated_at* < *created_at*, or if both *valid_from* and
        *valid_until* are provided and *valid_until* < *valid_from*.
    """

    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    mentioned_at: Optional[datetime] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.updated_at < self.created_at:
            raise ValueError(
                f"updated_at ({self.updated_at}) must be >= created_at ({self.created_at})"
            )
        if self.valid_from is not None and self.valid_until is not None:
            if self.valid_until < self.valid_from:
                raise ValueError(
                    f"valid_until ({self.valid_until}) must be >= valid_from ({self.valid_from})"
                )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        d: Dict[str, Any] = {
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
        if self.mentioned_at is not None:
            d["mentioned_at"] = self.mentioned_at.isoformat()
        if self.valid_from is not None:
            d["valid_from"] = self.valid_from.isoformat()
        if self.valid_until is not None:
            d["valid_until"] = self.valid_until.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TemporalContext:
        """Create a TemporalContext from a dictionary."""
        return cls(
            created_at=datetime.fromisoformat(data["created_at"]) if "created_at" in data else datetime.utcnow(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if "updated_at" in data else datetime.utcnow(),
            mentioned_at=datetime.fromisoformat(data["mentioned_at"]) if data.get("mentioned_at") else None,
            valid_from=datetime.fromisoformat(data["valid_from"]) if data.get("valid_from") else None,
            valid_until=datetime.fromisoformat(data["valid_until"]) if data.get("valid_until") else None,
        )


@dataclass(frozen=True)
class MemoryMetadata:
    """Metadata associated with a memory.

    Parameters
    ----------
    importance : float
        Importance score (0.0 – 1.0).
    confidence : float
        Confidence score (0.0 – 1.0).
    source : str
        Origin of the memory (e.g. ``"chatgpt"``, ``"manual"``).
    tags : tuple[str, ...]
        User‑defined tags for categorisation.
    created_at : datetime
        When the memory was created.
    updated_at : datetime
        When the memory was last updated.

    Raises
    ------
    ValueError
        If *importance* or *confidence* are outside [0, 1], or if
        *tags* contains duplicates.
    """

    importance: float = 0.5
    confidence: float = 0.5
    source: str = ""
    tags: Tuple[str, ...] = field(default_factory=tuple)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self) -> None:
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("tags must be unique")

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        return {
            "importance": self.importance,
            "confidence": self.confidence,
            "source": self.source,
            "tags": list(self.tags),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MemoryMetadata:
        """Create a MemoryMetadata from a dictionary."""
        return cls(
            importance=data.get("importance", 0.5),
            confidence=data.get("confidence", 0.5),
            source=data.get("source", ""),
            tags=tuple(data.get("tags", [])),
            created_at=datetime.fromisoformat(data["created_at"]) if "created_at" in data else datetime.utcnow(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if "updated_at" in data else datetime.utcnow(),
        )


@dataclass(frozen=True)
class Relationship:
    """A directed, typed connection between two MemoryIdentities.

    Parameters
    ----------
    source_identity : MemoryIdentity
        The "from" identity.
    target_identity : MemoryIdentity
        The "to" identity.
    relationship_type : RelationshipType
        The type of relationship.
    confidence : float
        Confidence score for the relationship (0.0 – 1.0).

    Raises
    ------
    ValueError
        If *source_identity* and *target_identity* are the same
        (by ``identity_id``), or if *confidence* is outside [0, 1].
    """

    source_identity: MemoryIdentity = field(default_factory=MemoryIdentity)
    target_identity: MemoryIdentity = field(default_factory=MemoryIdentity)
    relationship_type: RelationshipType = RelationshipType.RELATED_TO
    confidence: float = 0.5

    def __post_init__(self) -> None:
        if self.source_identity.identity_id == self.target_identity.identity_id:
            raise ValueError("source and target identities must be different")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        return {
            "source_identity": self.source_identity.to_dict(),
            "target_identity": self.target_identity.to_dict(),
            "relationship_type": self.relationship_type.value,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Relationship:
        """Create a Relationship from a dictionary."""
        return cls(
            source_identity=MemoryIdentity.from_dict(data.get("source_identity", {})),
            target_identity=MemoryIdentity.from_dict(data.get("target_identity", {})),
            relationship_type=RelationshipType(data["relationship_type"]) if "relationship_type" in data else RelationshipType.RELATED_TO,
            confidence=data.get("confidence", 0.5),
        )
