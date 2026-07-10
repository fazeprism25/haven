"""Conversation-side data model types for Obsidian Memory.

Defines the normalised :class:`Conversation`/:class:`Event` schema that
Haven's source integrations (e.g. the ChatGPT extension capture and the
Obsidian vault importer) produce and the ingestion pipeline consumes.

All types are :class:`dataclasses.dataclass` with ``to_dict`` and
``from_dict`` methods for JSON serialization.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from obsidian.core.enums import Role, SourceType
from obsidian.core.value_objects import Entity


# ---------------------------------------------------------------------------
# Attachment (simple value object used by Event)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Attachment:
    """A file or media reference attached to an event.

    Parameters
    ----------
    url : str
        The URL or file path of the attachment.
    mime_type : str
        The MIME type (e.g. ``"image/png"``, ``"application/pdf"``).
    name : str, optional
        A human‑readable name for the attachment.
    size_bytes : int, optional
        The size of the attachment in bytes.
    """

    url: str = ""
    mime_type: str = ""
    name: Optional[str] = None
    size_bytes: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        d: Dict[str, Any] = {
            "url": self.url,
            "mime_type": self.mime_type,
        }
        if self.name is not None:
            d["name"] = self.name
        if self.size_bytes is not None:
            d["size_bytes"] = self.size_bytes
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Attachment:
        """Create an Attachment from a dictionary."""
        return cls(
            url=data.get("url", ""),
            mime_type=data.get("mime_type", ""),
            name=data.get("name"),
            size_bytes=data.get("size_bytes"),
        )


# ---------------------------------------------------------------------------
# Event
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """A single atomic turn within a Conversation.

    Parameters
    ----------
    id : UUID
        Unique identifier for this event.
    role : Role
        The participant role (user, assistant, system, tool).
    content : str
        The text content of the event.
    timestamp : datetime
        When the event occurred.
    source : SourceType
        The origin system (ChatGPT, Claude, Slack, etc.).
    entities : list[Entity]
        Named entities extracted from the content.
    attachments : list[Attachment]
        File or media references attached to this event.
    """

    id: UUID = field(default_factory=uuid4)
    role: Role = Role.USER
    content: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source: SourceType = SourceType.MANUAL
    entities: List[Entity] = field(default_factory=list)
    attachments: List[Attachment] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        return {
            "id": str(self.id),
            "role": self.role.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source.value,
            "entities": [e.to_dict() for e in self.entities],
            "attachments": [a.to_dict() for a in self.attachments],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Event:
        """Create an Event from a dictionary."""
        return cls(
            id=UUID(data["id"]) if isinstance(data.get("id"), str) else data.get("id", uuid4()),
            role=Role(data["role"]) if "role" in data else Role.USER,
            content=data.get("content", ""),
            timestamp=datetime.fromisoformat(data["timestamp"]) if "timestamp" in data else datetime.utcnow(),
            source=SourceType(data["source"]) if "source" in data else SourceType.MANUAL,
            entities=[Entity.from_dict(e) for e in data.get("entities", [])],
            attachments=[Attachment.from_dict(a) for a in data.get("attachments", [])],
        )


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Conversation:
    """A normalized sequence of events from any source.

    Parameters
    ----------
    id : UUID
        Unique identifier for this conversation.
    title : str
        Human‑readable title (may be extracted later).
    source : SourceType
        The origin system (ChatGPT, Claude, Slack, etc.).
    events : list[Event]
        Ordered sequence of events.
    metadata : dict
        Source‑specific metadata (e.g. channel name, email subject).
    """

    id: UUID = field(default_factory=uuid4)
    title: str = ""
    source: SourceType = SourceType.MANUAL
    events: List[Event] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        return {
            "id": str(self.id),
            "title": self.title,
            "source": self.source.value,
            "events": [e.to_dict() for e in self.events],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Conversation:
        """Create a Conversation from a dictionary."""
        return cls(
            id=UUID(data["id"]) if isinstance(data.get("id"), str) else data.get("id", uuid4()),
            title=data.get("title", ""),
            source=SourceType(data["source"]) if "source" in data else SourceType.MANUAL,
            events=[Event.from_dict(e) for e in data.get("events", [])],
            metadata=data.get("metadata", {}),
        )
