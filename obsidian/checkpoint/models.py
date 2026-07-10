"""Data models for the Haven Conversation Checkpoint subsystem.

:class:`ConversationCheckpoint` is the bookkeeping record that lets Haven
answer "have I already processed this conversation, and if so, how much
of it?" -- see the module docstring in ``obsidian/checkpoint/__init__.py``
for how this fits into the wider subsystem. Nothing in this module reads
or writes a file; that is :class:`~obsidian.checkpoint.store.CheckpointStore`
and :class:`~obsidian.checkpoint.writer.CheckpointWriter`'s job.

Both dataclasses follow the exact ``to_dict``/``from_dict`` convention
already used throughout the codebase (see
:class:`obsidian.manager_ai.models.KnowledgeObject`): frozen, immutable,
JSON-serializable, and tolerant of missing optional keys on hydration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from obsidian.core.enums import SourceType

#: The checkpoint schema version this module reads and writes. Bumped only
#: for a breaking change to field meaning or hashing scheme -- purely
#: additive fields never require a bump (see
#: :class:`~obsidian.checkpoint.store.CheckpointStore`'s handling of a
#: version mismatch).
CURRENT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CheckpointRun:
    """One processing pass over a conversation.

    Parameters
    ----------
    processed_at : datetime
        When this processing run happened.
    turn_range : tuple[int, int]
        The half-open ``[start, end)`` range of turn indices processed
        during this run.
    knowledge_object_ids : list[UUID]
        The ``KnowledgeObject`` ids created or touched during this run.
    decision_counts : dict[str, int]
        Count of each :class:`~obsidian.manager_ai.models.KnowledgeDecision`
        value produced during this run (e.g. ``{"new": 2, "confirm": 1}``).
    mode : str
        Which :class:`~obsidian.checkpoint.diff.TurnDiffMode` produced this
        run -- ``"first_run"``, ``"incremental"``, or ``"fallback"``.
        Purely for observability (nothing reads it to make a decision);
        lets checkpoint history answer "was this run's evidence the whole
        conversation, or just the new turns?" after the fact. Records
        written before this field existed (PR 1-3) hydrate it as
        ``"unknown"`` -- they predate incremental ingestion, so there is no
        honest way to backfill which case they were.
    """

    processed_at: datetime = field(default_factory=datetime.utcnow)
    turn_range: Tuple[int, int] = (0, 0)
    knowledge_object_ids: List[UUID] = field(default_factory=list)
    decision_counts: Dict[str, int] = field(default_factory=dict)
    mode: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dictionary."""
        return {
            "processed_at": self.processed_at.isoformat(),
            "turn_range": [self.turn_range[0], self.turn_range[1]],
            "knowledge_object_ids": [str(i) for i in self.knowledge_object_ids],
            "decision_counts": dict(self.decision_counts),
            "mode": self.mode,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckpointRun":
        """Create a CheckpointRun from a dictionary."""
        turn_range_raw = data.get("turn_range", [0, 0])
        return cls(
            processed_at=datetime.fromisoformat(data["processed_at"])
            if isinstance(data.get("processed_at"), str)
            else data.get("processed_at", datetime.utcnow()),
            turn_range=(turn_range_raw[0], turn_range_raw[1]),
            knowledge_object_ids=[
                UUID(i) if isinstance(i, str) else i
                for i in data.get("knowledge_object_ids", [])
            ],
            decision_counts=dict(data.get("decision_counts", {})),
            mode=data.get("mode", "unknown"),
        )


@dataclass(frozen=True)
class ConversationCheckpoint:
    """Bookkeeping record of how much of a conversation has been processed.

    Parameters
    ----------
    schema_version : int
        The schema version this record was written under. See
        :data:`CURRENT_SCHEMA_VERSION`.
    conversation_id : UUID
        The conversation's identifier, as returned by
        :func:`obsidian.checkpoint.identity.derive_conversation_id`.
    source : SourceType
        The origin system the conversation came from.
    external_key : str, optional
        The stable source-system identifier the conversation_id was
        derived from (kept alongside the derived id for human
        readability/debugging; ``None`` when no external key was
        available, e.g. pasted/manual text).
    turn_count : int
        The number of turns seen as of the last processing run.
    last_processed_turn_index : int
        The zero-based index of the last turn included in processing.
        ``-1`` means no turn has been processed yet.
    turn_hashes : list[str]
        Per-turn content hashes, index-aligned with the conversation's
        turns, as returned by :func:`obsidian.checkpoint.hashing.turn_hash`.
        Always exactly ``turn_count`` entries long.
    transcript_hash : str
        Whole-transcript content hash, as returned by
        :func:`obsidian.checkpoint.hashing.transcript_hash`, as of the
        last processing run.
    created_at : datetime
        When this checkpoint was first created.
    last_processed_at : datetime, optional
        When this checkpoint was last updated by a processing run.
        ``None`` if it has never been processed.
    knowledge_object_ids : list[UUID]
        Every ``KnowledgeObject`` id ever produced from this conversation,
        across all processing runs (cumulative).
    processing_history : list[CheckpointRun]
        One entry per processing run, in chronological order.

    Raises
    ------
    ValueError
        If ``schema_version`` is less than 1, if ``turn_count`` or
        ``last_processed_turn_index`` are out of range, or if
        ``turn_hashes`` is not exactly ``turn_count`` entries long.
    """

    schema_version: int = CURRENT_SCHEMA_VERSION
    conversation_id: UUID = field(default_factory=uuid4)
    source: SourceType = SourceType.MANUAL
    external_key: Optional[str] = None

    turn_count: int = 0
    last_processed_turn_index: int = -1
    turn_hashes: List[str] = field(default_factory=list)
    transcript_hash: str = ""

    created_at: datetime = field(default_factory=datetime.utcnow)
    last_processed_at: Optional[datetime] = None
    knowledge_object_ids: List[UUID] = field(default_factory=list)
    processing_history: List[CheckpointRun] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.schema_version < 1:
            raise ValueError("schema_version must be >= 1")
        if self.turn_count < 0:
            raise ValueError("turn_count must be >= 0")
        if self.last_processed_turn_index < -1:
            raise ValueError("last_processed_turn_index must be >= -1")
        if self.last_processed_turn_index >= self.turn_count:
            raise ValueError(
                "last_processed_turn_index must be < turn_count "
                f"(got {self.last_processed_turn_index} >= {self.turn_count})"
            )
        if len(self.turn_hashes) != self.turn_count:
            raise ValueError(
                "turn_hashes must have exactly turn_count entries "
                f"(got {len(self.turn_hashes)} hashes for turn_count="
                f"{self.turn_count})"
            )

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dictionary."""
        return {
            "schema_version": self.schema_version,
            "conversation_id": str(self.conversation_id),
            "source": self.source.value,
            "external_key": self.external_key,
            "turn_count": self.turn_count,
            "last_processed_turn_index": self.last_processed_turn_index,
            "turn_hashes": list(self.turn_hashes),
            "transcript_hash": self.transcript_hash,
            "created_at": self.created_at.isoformat(),
            "last_processed_at": (
                self.last_processed_at.isoformat()
                if self.last_processed_at is not None
                else None
            ),
            "knowledge_object_ids": [str(i) for i in self.knowledge_object_ids],
            "processing_history": [r.to_dict() for r in self.processing_history],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationCheckpoint":
        """Create a ConversationCheckpoint from a dictionary."""
        return cls(
            schema_version=data.get("schema_version", CURRENT_SCHEMA_VERSION),
            conversation_id=(
                UUID(data["conversation_id"])
                if isinstance(data.get("conversation_id"), str)
                else data.get("conversation_id", uuid4())
            ),
            source=(
                SourceType(data["source"])
                if "source" in data
                else SourceType.MANUAL
            ),
            external_key=data.get("external_key"),
            turn_count=data.get("turn_count", 0),
            last_processed_turn_index=data.get("last_processed_turn_index", -1),
            turn_hashes=list(data.get("turn_hashes", [])),
            transcript_hash=data.get("transcript_hash", ""),
            created_at=(
                datetime.fromisoformat(data["created_at"])
                if isinstance(data.get("created_at"), str)
                else data.get("created_at", datetime.utcnow())
            ),
            last_processed_at=(
                datetime.fromisoformat(data["last_processed_at"])
                if isinstance(data.get("last_processed_at"), str)
                else data.get("last_processed_at")
            ),
            knowledge_object_ids=[
                UUID(i) if isinstance(i, str) else i
                for i in data.get("knowledge_object_ids", [])
            ],
            processing_history=[
                CheckpointRun.from_dict(r)
                for r in data.get("processing_history", [])
            ],
        )
