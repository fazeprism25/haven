"""Unit tests for obsidian.checkpoint.writer.CheckpointWriter.

Test groups
-----------
TestWrite            -- filesystem operations (dir creation, filename,
                        overwrite).
TestDeterminism      -- byte-identical output for unchanged input.
TestStoreRoundTrip   -- a file written by CheckpointWriter loads correctly
                        through CheckpointStore (the actual PR 2
                        contract: writer and store must agree).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from obsidian.checkpoint.models import ConversationCheckpoint
from obsidian.checkpoint.store import CheckpointStore
from obsidian.checkpoint.writer import CheckpointWriter
from obsidian.core.enums import SourceType

_FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_checkpoint(**overrides) -> ConversationCheckpoint:
    defaults = dict(
        conversation_id=uuid4(),
        source=SourceType.CHATGPT,
        external_key="/c/abc123",
        turn_count=2,
        last_processed_turn_index=1,
        turn_hashes=["hash-0", "hash-1"],
        transcript_hash="transcript-hash",
        created_at=_FIXED_TIME,
        last_processed_at=_FIXED_TIME,
    )
    defaults.update(overrides)
    return ConversationCheckpoint(**defaults)


class TestWrite:
    def test_creates_checkpoint_directory(self, tmp_path: Path) -> None:
        checkpoint_dir = tmp_path / "checkpoints"
        writer = CheckpointWriter(checkpoint_dir)

        writer.write(make_checkpoint())

        assert checkpoint_dir.is_dir()

    def test_filename_is_conversation_id(self, tmp_path: Path) -> None:
        checkpoint = make_checkpoint()
        writer = CheckpointWriter(tmp_path)

        path = writer.write(checkpoint)

        assert path.name == f"{checkpoint.conversation_id}.json"
        assert path.exists()

    def test_content_is_valid_json_matching_to_dict(self, tmp_path: Path) -> None:
        checkpoint = make_checkpoint()
        writer = CheckpointWriter(tmp_path)

        path = writer.write(checkpoint)

        parsed = json.loads(path.read_text(encoding="utf-8"))
        assert parsed == checkpoint.to_dict()

    def test_overwrites_existing_file_for_same_conversation(self, tmp_path: Path) -> None:
        conversation_id = uuid4()
        writer = CheckpointWriter(tmp_path)

        first = make_checkpoint(
            conversation_id=conversation_id, turn_count=1, last_processed_turn_index=0, turn_hashes=["a"]
        )
        writer.write(first)

        second = make_checkpoint(
            conversation_id=conversation_id,
            turn_count=3,
            last_processed_turn_index=2,
            turn_hashes=["a", "b", "c"],
        )
        path = writer.write(second)

        parsed = json.loads(path.read_text(encoding="utf-8"))
        assert parsed["turn_count"] == 3

    def test_returns_absolute_path(self, tmp_path: Path) -> None:
        writer = CheckpointWriter(tmp_path)
        path = writer.write(make_checkpoint())
        assert path.is_absolute()


class TestDeterminism:
    def test_same_checkpoint_writes_identical_bytes(self, tmp_path: Path) -> None:
        checkpoint = make_checkpoint()

        writer_a = CheckpointWriter(tmp_path / "a")
        writer_b = CheckpointWriter(tmp_path / "b")
        path_a = writer_a.write(checkpoint)
        path_b = writer_b.write(checkpoint)

        assert path_a.read_text(encoding="utf-8") == path_b.read_text(encoding="utf-8")

    def test_rewriting_unchanged_checkpoint_is_a_no_op(self, tmp_path: Path) -> None:
        checkpoint = make_checkpoint()
        writer = CheckpointWriter(tmp_path)

        path = writer.write(checkpoint)
        first_content = path.read_text(encoding="utf-8")
        writer.write(checkpoint)
        second_content = path.read_text(encoding="utf-8")

        assert first_content == second_content


class TestStoreRoundTrip:
    def test_written_checkpoint_loads_through_store(self, tmp_path: Path) -> None:
        checkpoint = make_checkpoint()
        CheckpointWriter(tmp_path).write(checkpoint)

        store = CheckpointStore(tmp_path)
        store.load()

        assert store.count() == 1
        hydrated = store.get(checkpoint.conversation_id)
        assert hydrated == checkpoint

    def test_multiple_written_checkpoints_all_load(self, tmp_path: Path) -> None:
        writer = CheckpointWriter(tmp_path)
        checkpoints = [make_checkpoint() for _ in range(4)]
        for checkpoint in checkpoints:
            writer.write(checkpoint)

        store = CheckpointStore(tmp_path)
        store.load()

        assert store.count() == 4
        for checkpoint in checkpoints:
            assert store.get(checkpoint.conversation_id) == checkpoint
