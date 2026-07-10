"""Unit tests for obsidian.checkpoint.models.

Test groups
-----------
TestCheckpointRunSerialisation           -- to_dict/from_dict round trip.
TestCheckpointRunImmutability            -- frozen dataclass.
TestConversationCheckpointDefaults       -- default-constructed checkpoint
                                            is valid.
TestConversationCheckpointValidation     -- every __post_init__ invariant.
TestConversationCheckpointImmutability   -- frozen dataclass.
TestConversationCheckpointSerialisation  -- to_dict/from_dict round trip,
                                            including nested
                                            processing_history and
                                            optional fields.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from uuid import UUID, uuid4

import pytest

from obsidian.checkpoint.models import (
    CURRENT_SCHEMA_VERSION,
    CheckpointRun,
    ConversationCheckpoint,
)
from obsidian.core.enums import SourceType

_FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0)


# ---------------------------------------------------------------------------
# CheckpointRun
# ---------------------------------------------------------------------------


class TestCheckpointRunSerialisation:
    def test_round_trip_defaults(self) -> None:
        run = CheckpointRun()
        restored = CheckpointRun.from_dict(run.to_dict())
        assert restored == run

    def test_round_trip_with_values(self) -> None:
        ko_id = uuid4()
        run = CheckpointRun(
            processed_at=_FIXED_TIME,
            turn_range=(0, 3),
            knowledge_object_ids=[ko_id],
            decision_counts={"new": 2, "confirm": 1},
        )
        restored = CheckpointRun.from_dict(run.to_dict())
        assert restored == run

    def test_to_dict_uses_json_safe_types(self) -> None:
        run = CheckpointRun(
            processed_at=_FIXED_TIME,
            turn_range=(0, 3),
            knowledge_object_ids=[uuid4()],
        )
        d = run.to_dict()
        assert isinstance(d["processed_at"], str)
        assert isinstance(d["turn_range"], list)
        assert all(isinstance(i, str) for i in d["knowledge_object_ids"])

    def test_from_dict_missing_keys_use_defaults(self) -> None:
        run = CheckpointRun.from_dict({})
        assert run.turn_range == (0, 0)
        assert run.knowledge_object_ids == []
        assert run.decision_counts == {}


class TestCheckpointRunImmutability:
    def test_is_frozen(self) -> None:
        run = CheckpointRun()
        with pytest.raises(dataclasses.FrozenInstanceError):
            run.turn_range = (1, 2)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ConversationCheckpoint
# ---------------------------------------------------------------------------


class TestConversationCheckpointDefaults:
    def test_default_constructs_without_error(self) -> None:
        checkpoint = ConversationCheckpoint()
        assert checkpoint.schema_version == CURRENT_SCHEMA_VERSION
        assert checkpoint.turn_count == 0
        assert checkpoint.last_processed_turn_index == -1
        assert checkpoint.turn_hashes == []
        assert checkpoint.knowledge_object_ids == []
        assert checkpoint.processing_history == []
        assert checkpoint.last_processed_at is None
        assert checkpoint.external_key is None


class TestConversationCheckpointValidation:
    def test_schema_version_below_one_raises(self) -> None:
        with pytest.raises(ValueError, match="schema_version"):
            ConversationCheckpoint(schema_version=0)

    def test_negative_turn_count_raises(self) -> None:
        with pytest.raises(ValueError, match="turn_count"):
            ConversationCheckpoint(turn_count=-1, last_processed_turn_index=-1, turn_hashes=[])

    def test_last_processed_turn_index_below_negative_one_raises(self) -> None:
        with pytest.raises(ValueError, match="last_processed_turn_index"):
            ConversationCheckpoint(turn_count=2, last_processed_turn_index=-2, turn_hashes=["a", "b"])

    def test_last_processed_turn_index_equal_to_turn_count_raises(self) -> None:
        with pytest.raises(ValueError, match="last_processed_turn_index"):
            ConversationCheckpoint(turn_count=2, last_processed_turn_index=2, turn_hashes=["a", "b"])

    def test_last_processed_turn_index_beyond_turn_count_raises(self) -> None:
        with pytest.raises(ValueError, match="last_processed_turn_index"):
            ConversationCheckpoint(turn_count=2, last_processed_turn_index=5, turn_hashes=["a", "b"])

    def test_turn_hashes_shorter_than_turn_count_raises(self) -> None:
        with pytest.raises(ValueError, match="turn_hashes"):
            ConversationCheckpoint(turn_count=3, last_processed_turn_index=2, turn_hashes=["a", "b"])

    def test_turn_hashes_longer_than_turn_count_raises(self) -> None:
        with pytest.raises(ValueError, match="turn_hashes"):
            ConversationCheckpoint(turn_count=1, last_processed_turn_index=0, turn_hashes=["a", "b"])

    def test_valid_fully_processed_state_does_not_raise(self) -> None:
        ConversationCheckpoint(turn_count=3, last_processed_turn_index=2, turn_hashes=["a", "b", "c"])

    def test_valid_partially_processed_state_does_not_raise(self) -> None:
        # turn_count reflects turns seen; last_processed_turn_index can sit
        # anywhere in [-1, turn_count).
        ConversationCheckpoint(turn_count=3, last_processed_turn_index=0, turn_hashes=["a", "b", "c"])


class TestConversationCheckpointImmutability:
    def test_is_frozen(self) -> None:
        checkpoint = ConversationCheckpoint()
        with pytest.raises(dataclasses.FrozenInstanceError):
            checkpoint.turn_count = 5  # type: ignore[misc]


class TestConversationCheckpointSerialisation:
    def test_round_trip_defaults(self) -> None:
        checkpoint = ConversationCheckpoint()
        restored = ConversationCheckpoint.from_dict(checkpoint.to_dict())
        assert restored == checkpoint

    def test_round_trip_with_values(self) -> None:
        conversation_id = uuid4()
        ko_id = uuid4()
        run = CheckpointRun(
            processed_at=_FIXED_TIME,
            turn_range=(0, 2),
            knowledge_object_ids=[ko_id],
            decision_counts={"new": 1},
        )
        checkpoint = ConversationCheckpoint(
            schema_version=CURRENT_SCHEMA_VERSION,
            conversation_id=conversation_id,
            source=SourceType.CHATGPT,
            external_key="/c/abc123",
            turn_count=2,
            last_processed_turn_index=1,
            turn_hashes=["hash-0", "hash-1"],
            transcript_hash="whole-transcript-hash",
            created_at=_FIXED_TIME,
            last_processed_at=_FIXED_TIME,
            knowledge_object_ids=[ko_id],
            processing_history=[run],
        )
        restored = ConversationCheckpoint.from_dict(checkpoint.to_dict())
        assert restored == checkpoint

    def test_round_trip_preserves_none_external_key(self) -> None:
        checkpoint = ConversationCheckpoint(external_key=None)
        restored = ConversationCheckpoint.from_dict(checkpoint.to_dict())
        assert restored.external_key is None

    def test_round_trip_preserves_none_last_processed_at(self) -> None:
        checkpoint = ConversationCheckpoint(last_processed_at=None)
        restored = ConversationCheckpoint.from_dict(checkpoint.to_dict())
        assert restored.last_processed_at is None

    def test_to_dict_uses_json_safe_types(self) -> None:
        checkpoint = ConversationCheckpoint(
            turn_count=1, last_processed_turn_index=0, turn_hashes=["a"]
        )
        d = checkpoint.to_dict()
        assert isinstance(d["conversation_id"], str)
        assert isinstance(d["source"], str)
        assert isinstance(d["created_at"], str)
        assert isinstance(d["knowledge_object_ids"], list)
        assert isinstance(d["processing_history"], list)

    def test_from_dict_missing_optional_keys_use_defaults(self) -> None:
        checkpoint = ConversationCheckpoint.from_dict(
            {"conversation_id": str(uuid4())}
        )
        assert checkpoint.schema_version == CURRENT_SCHEMA_VERSION
        assert checkpoint.source == SourceType.MANUAL
        assert checkpoint.turn_count == 0
        assert checkpoint.last_processed_turn_index == -1
        assert checkpoint.turn_hashes == []

    def test_from_dict_accepts_string_uuid(self) -> None:
        cid = uuid4()
        checkpoint = ConversationCheckpoint.from_dict({"conversation_id": str(cid)})
        assert checkpoint.conversation_id == cid
        assert isinstance(checkpoint.conversation_id, UUID)

    def test_from_dict_hydrates_nested_processing_history(self) -> None:
        raw = {
            "conversation_id": str(uuid4()),
            "turn_count": 2,
            "last_processed_turn_index": 1,
            "turn_hashes": ["a", "b"],
            "processing_history": [
                {
                    "processed_at": _FIXED_TIME.isoformat(),
                    "turn_range": [0, 2],
                    "knowledge_object_ids": [],
                    "decision_counts": {"new": 1},
                }
            ],
        }
        checkpoint = ConversationCheckpoint.from_dict(raw)
        assert len(checkpoint.processing_history) == 1
        assert checkpoint.processing_history[0].decision_counts == {"new": 1}
