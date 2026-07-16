"""Value objects for the Obsidian Memory data model.

Value objects are immutable, have no identity, and are compared
by their attributes.  They are used as components of the larger
data model types.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from obsidian.core.enums import EntityType


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
class TopicTag:
    """A canonicalized topical label attached to a memory.

    Produced by :func:`obsidian.manager_ai.topic_canonicalizer.canonicalize_topics`
    from the Classifier's free-text ``topics`` output -- ``name`` is always
    the canonical display form (e.g. ``"AI"``), never a raw synonym like
    ``"machine learning"``, and ``confidence`` is the Classifier's own
    confidence in that specific tag, independent of ``confidence`` on the
    ``ExtractedFact``/``ClassificationResult`` (memory-type) it accompanies.
    Not (yet) used in retrieval, ranking, or acceptance -- purely
    informational and explainability metadata.

    Parameters
    ----------
    name : str
        The canonical topic label (e.g. ``"AI"``, ``"Fitness"``).
    confidence : float
        Confidence that this topic genuinely applies (0.0 – 1.0).

    Raises
    ------
    ValueError
        If *name* is empty or *confidence* is outside [0, 1].
    """

    name: str = ""
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
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TopicTag:
        """Create a TopicTag from a dictionary."""
        return cls(
            name=data.get("name", ""),
            confidence=data.get("confidence", 0.5),
        )
