"""Unit tests for obsidian.checkpoint.store.CheckpointStore.

Test groups
-----------
TestInstantiation        -- construction, empty initial state.
TestMissingDirectory     -- a checkpoint dir that doesn't exist yet loads
                            as zero checkpoints (deliberate deviation from
                            MemoryStore -- see store.py's module
                            docstring).
TestPathIsFile           -- a genuine misconfiguration still raises.
TestSingleCheckpoint     -- one checkpoint round-trips through the store.
TestMultipleCheckpoints  -- several checkpoints, correct count/isolation.
TestCorruptFileHandling  -- malformed JSON, non-object JSON, invalid
                            fields are skipped, not fatal.
TestSchemaVersionHandling -- a mismatched schema_version is skipped, not
                            fatal.
TestAtomicReplacement    -- a failed reload leaves the previous cache
                            intact; a successful reload fully replaces it.
TestQueries              -- get/has/all/count, including unknown-id
                            behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from obsidian.checkpoint.models import CURRENT_SCHEMA_VERSION, ConversationCheckpoint
from obsidian.checkpoint.store import CheckpointStore
from obsidian.core.enums import SourceType


def make_checkpoint(
    external_key: str = "/c/abc123",
    turn_count: int = 2,
    last_processed_turn_index: int = 1,
) -> ConversationCheckpoint:
    return ConversationCheckpoint(
        conversation_id=uuid4(),
        source=SourceType.CHATGPT,
        external_key=external_key,
        turn_count=turn_count,
        last_processed_turn_index=last_processed_turn_index,
        turn_hashes=[f"hash-{i}" for i in range(turn_count)],
        transcript_hash="transcript-hash",
    )


def write_raw(checkpoint_dir: Path, filename: str, content: str) -> Path:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    path = checkpoint_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


def write_checkpoint(checkpoint_dir: Path, checkpoint: ConversationCheckpoint) -> Path:
    return write_raw(
        checkpoint_dir,
        f"{checkpoint.conversation_id}.json",
        json.dumps(checkpoint.to_dict()),
    )


# ---------------------------------------------------------------------------
# TestInstantiation
# ---------------------------------------------------------------------------


class TestInstantiation:
    def test_empty_before_load(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        assert store.count() == 0
        assert store.all() == []
        assert store.skipped_files() == []


# ---------------------------------------------------------------------------
# TestMissingDirectory
# ---------------------------------------------------------------------------


class TestMissingDirectory:
    def test_missing_directory_loads_zero(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path / "does_not_exist_yet")
        store.load()
        assert store.count() == 0
        assert store.all() == []

    def test_missing_directory_does_not_raise(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path / "does_not_exist_yet")
        store.load()  # must not raise


# ---------------------------------------------------------------------------
# TestPathIsFile
# ---------------------------------------------------------------------------


class TestPathIsFile:
    def test_path_is_file_raises(self, tmp_path: Path) -> None:
        file_path = tmp_path / "not_a_dir.json"
        file_path.write_text("{}", encoding="utf-8")
        store = CheckpointStore(file_path)
        with pytest.raises(NotADirectoryError):
            store.load()


# ---------------------------------------------------------------------------
# TestSingleCheckpoint
# ---------------------------------------------------------------------------


class TestSingleCheckpoint:
    def test_hydrates_matching_fields(self, tmp_path: Path) -> None:
        checkpoint = make_checkpoint(external_key="/c/abc123", turn_count=3, last_processed_turn_index=2)
        write_checkpoint(tmp_path, checkpoint)

        store = CheckpointStore(tmp_path)
        store.load()

        assert store.count() == 1
        hydrated = store.get(checkpoint.conversation_id)
        assert hydrated.external_key == "/c/abc123"
        assert hydrated.turn_count == 3
        assert hydrated.last_processed_turn_index == 2
        assert hydrated.source == SourceType.CHATGPT


# ---------------------------------------------------------------------------
# TestMultipleCheckpoints
# ---------------------------------------------------------------------------


class TestMultipleCheckpoints:
    def test_loads_all_files(self, tmp_path: Path) -> None:
        checkpoints = [make_checkpoint(external_key=f"/c/{i}") for i in range(5)]
        for checkpoint in checkpoints:
            write_checkpoint(tmp_path, checkpoint)

        store = CheckpointStore(tmp_path)
        store.load()

        assert store.count() == 5
        loaded_ids = {c.conversation_id for c in store.all()}
        assert loaded_ids == {c.conversation_id for c in checkpoints}

    def test_checkpoints_are_isolated(self, tmp_path: Path) -> None:
        a = make_checkpoint(external_key="/c/a", turn_count=1, last_processed_turn_index=0)
        b = make_checkpoint(external_key="/c/b", turn_count=4, last_processed_turn_index=3)
        write_checkpoint(tmp_path, a)
        write_checkpoint(tmp_path, b)

        store = CheckpointStore(tmp_path)
        store.load()

        assert store.get(a.conversation_id).turn_count == 1
        assert store.get(b.conversation_id).turn_count == 4

    def test_all_returns_sorted_by_conversation_id(self, tmp_path: Path) -> None:
        checkpoints = [make_checkpoint(external_key=f"/c/{i}") for i in range(6)]
        for checkpoint in checkpoints:
            write_checkpoint(tmp_path, checkpoint)

        store = CheckpointStore(tmp_path)
        store.load()

        ids = [str(c.conversation_id) for c in store.all()]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# TestCorruptFileHandling
# ---------------------------------------------------------------------------


class TestCorruptFileHandling:
    def test_invalid_json_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        good = make_checkpoint(external_key="/c/good")
        write_checkpoint(tmp_path, good)
        write_raw(tmp_path, "bad.json", "{not valid json")

        store = CheckpointStore(tmp_path)
        store.load()  # must not raise

        assert store.count() == 1
        assert store.get(good.conversation_id).external_key == "/c/good"

    def test_invalid_json_is_recorded_in_skipped_files(self, tmp_path: Path) -> None:
        bad_path = write_raw(tmp_path, "bad.json", "{not valid json")

        store = CheckpointStore(tmp_path)
        store.load()

        skipped_paths = [p for p, _reason in store.skipped_files()]
        assert bad_path in skipped_paths

    def test_non_object_json_is_skipped(self, tmp_path: Path) -> None:
        write_raw(tmp_path, "list.json", "[1, 2, 3]")

        store = CheckpointStore(tmp_path)
        store.load()

        assert store.count() == 0
        assert len(store.skipped_files()) == 1

    def test_invalid_field_value_is_skipped(self, tmp_path: Path) -> None:
        # turn_hashes shorter than turn_count -> ConversationCheckpoint
        # raises ValueError during hydration -> skipped, not fatal.
        write_raw(
            tmp_path,
            "invalid.json",
            json.dumps(
                {
                    "schema_version": CURRENT_SCHEMA_VERSION,
                    "conversation_id": str(uuid4()),
                    "turn_count": 3,
                    "last_processed_turn_index": 2,
                    "turn_hashes": ["only-one"],
                }
            ),
        )

        store = CheckpointStore(tmp_path)
        store.load()  # must not raise

        assert store.count() == 0
        assert len(store.skipped_files()) == 1

    def test_multiple_bad_files_all_skipped_good_ones_still_load(
        self, tmp_path: Path
    ) -> None:
        good = make_checkpoint(external_key="/c/good")
        write_checkpoint(tmp_path, good)
        write_raw(tmp_path, "bad1.json", "not json at all")
        write_raw(tmp_path, "bad2.json", "[]")

        store = CheckpointStore(tmp_path)
        store.load()

        assert store.count() == 1
        assert len(store.skipped_files()) == 2

    def test_non_markdown_unrelated_files_are_ignored(self, tmp_path: Path) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "notes.txt").write_text("not a checkpoint", encoding="utf-8")

        store = CheckpointStore(tmp_path)
        store.load()

        assert store.count() == 0
        assert store.skipped_files() == []


# ---------------------------------------------------------------------------
# TestSchemaVersionHandling
# ---------------------------------------------------------------------------


class TestSchemaVersionHandling:
    def test_future_schema_version_is_skipped(self, tmp_path: Path) -> None:
        write_raw(
            tmp_path,
            "future.json",
            json.dumps(
                {
                    "schema_version": CURRENT_SCHEMA_VERSION + 1,
                    "conversation_id": str(uuid4()),
                    "turn_count": 0,
                    "last_processed_turn_index": -1,
                    "turn_hashes": [],
                }
            ),
        )

        store = CheckpointStore(tmp_path)
        store.load()  # must not raise

        assert store.count() == 0
        assert len(store.skipped_files()) == 1

    def test_missing_schema_version_is_skipped(self, tmp_path: Path) -> None:
        write_raw(
            tmp_path,
            "no_version.json",
            json.dumps({"conversation_id": str(uuid4())}),
        )

        store = CheckpointStore(tmp_path)
        store.load()

        assert store.count() == 0
        assert len(store.skipped_files()) == 1

    def test_matching_schema_version_loads(self, tmp_path: Path) -> None:
        checkpoint = make_checkpoint()
        write_checkpoint(tmp_path, checkpoint)

        store = CheckpointStore(tmp_path)
        store.load()

        assert store.count() == 1


# ---------------------------------------------------------------------------
# TestAtomicReplacement
# ---------------------------------------------------------------------------


class TestAtomicReplacement:
    def test_reload_reflects_newly_added_checkpoint(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.load()
        assert store.count() == 0

        write_checkpoint(tmp_path, make_checkpoint())
        store.load()
        assert store.count() == 1

    def test_reload_reflects_removed_checkpoint(self, tmp_path: Path) -> None:
        checkpoint = make_checkpoint()
        path = write_checkpoint(tmp_path, checkpoint)

        store = CheckpointStore(tmp_path)
        store.load()
        assert store.count() == 1

        path.unlink()
        store.load()
        assert store.count() == 0

    def test_failed_reload_leaves_previous_cache_intact(self, tmp_path: Path) -> None:
        checkpoint_dir = tmp_path / "checkpoints"
        write_checkpoint(checkpoint_dir, make_checkpoint(external_key="/c/first"))

        store = CheckpointStore(checkpoint_dir)
        store.load()
        assert store.count() == 1
        previous_ids = {c.conversation_id for c in store.all()}

        # Replace the directory with a plain file -- a genuine
        # misconfiguration that must raise, per TestPathIsFile.
        import shutil

        shutil.rmtree(checkpoint_dir)
        checkpoint_dir.write_text("oops, now a file", encoding="utf-8")

        with pytest.raises(NotADirectoryError):
            store.load()

        # The cache from the last *successful* load must be untouched.
        assert store.count() == 1
        assert {c.conversation_id for c in store.all()} == previous_ids

    def test_two_independent_stores_agree(self, tmp_path: Path) -> None:
        for i in range(3):
            write_checkpoint(tmp_path, make_checkpoint(external_key=f"/c/{i}"))

        store_a = CheckpointStore(tmp_path)
        store_a.load()
        store_b = CheckpointStore(tmp_path)
        store_b.load()

        ids_a = {c.conversation_id for c in store_a.all()}
        ids_b = {c.conversation_id for c in store_b.all()}
        assert ids_a == ids_b


# ---------------------------------------------------------------------------
# TestQueries
# ---------------------------------------------------------------------------


class TestQueries:
    def test_get_unknown_id_raises_key_error(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.load()
        with pytest.raises(KeyError):
            store.get(uuid4())

    def test_has_unknown_id_returns_false(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.load()
        assert store.has(uuid4()) is False

    def test_has_known_id_returns_true(self, tmp_path: Path) -> None:
        checkpoint = make_checkpoint()
        write_checkpoint(tmp_path, checkpoint)

        store = CheckpointStore(tmp_path)
        store.load()

        assert store.has(checkpoint.conversation_id) is True
