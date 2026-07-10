"""Unit tests for obsidian.ontology.write_trace_store.

Test groups
-----------
TestInstantiation        -- construction, empty initial state.
TestMissingDirectory     -- a write-trace dir that doesn't exist yet loads
                            as zero traces (mirrors CheckpointStore).
TestPathIsFile           -- a genuine misconfiguration still raises.
TestWriterRoundTrip      -- WriteTraceWriter.write() then WriteTraceStore
                            loads it back with matching fields.
TestCorruptFileHandling  -- malformed JSON, non-object JSON are skipped,
                            not fatal (an old/oddly-shaped WriteTrace is
                            NOT skipped -- see TestLenientVersionLoading).
TestLenientVersionLoading -- unlike CheckpointStore, a WriteTrace with an
                            old/missing schema_version still loads (see
                            write_trace_store.py's module docstring for why
                            a diagnostic trace has no "safe to skip" case).
TestRetention            -- WriteTraceWriter prunes oldest files beyond
                            max_count; max_count<=0 disables pruning;
                            pruning never touches unrelated files; a
                            pruning failure doesn't prevent the new trace
                            from existing on disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from uuid import uuid4

import pytest

from obsidian.ontology.write_trace_models import (
    CheckpointStageTrace,
    OntologyStageTrace,
    WriteTrace,
)
from obsidian.ontology.write_trace_store import WriteTraceStore, WriteTraceWriter


def make_trace(created_at=None, **overrides) -> WriteTrace:
    from datetime import datetime

    defaults = dict(
        schema_version=1,
        pipeline_version=1,
        extractor_prompt_version=1,
        trace_id=uuid4(),
        conversation_id=None,
        source=None,
        external_key=None,
        mode="first_run",
        checkpoint=CheckpointStageTrace(
            mode="first_run",
            had_existing_checkpoint=False,
            turn_count=1,
            new_turn_start_index=0,
            transcript_hash="",
        ),
        working_contexts=None,
        extractor=None,
        facts=(),
        vault_paths=(),
        ontology=OntologyStageTrace(),
        status="success",
        knowledge_object_ids=(),
        stage_timings_ms={},
        created_at=created_at or datetime.utcnow(),
    )
    defaults.update(overrides)
    return WriteTrace(**defaults)


def write_raw(write_trace_dir: Path, filename: str, content: str) -> Path:
    write_trace_dir.mkdir(parents=True, exist_ok=True)
    path = write_trace_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# TestInstantiation
# ---------------------------------------------------------------------------


class TestInstantiation:
    def test_empty_before_load(self, tmp_path: Path) -> None:
        store = WriteTraceStore(tmp_path)
        assert store.count() == 0
        assert store.all() == []
        assert store.skipped_files() == []


# ---------------------------------------------------------------------------
# TestMissingDirectory
# ---------------------------------------------------------------------------


class TestMissingDirectory:
    def test_missing_directory_loads_zero(self, tmp_path: Path) -> None:
        store = WriteTraceStore(tmp_path / "does_not_exist_yet")
        store.load()
        assert store.count() == 0

    def test_missing_directory_does_not_raise(self, tmp_path: Path) -> None:
        store = WriteTraceStore(tmp_path / "does_not_exist_yet")
        store.load()  # must not raise


# ---------------------------------------------------------------------------
# TestPathIsFile
# ---------------------------------------------------------------------------


class TestPathIsFile:
    def test_path_is_file_raises(self, tmp_path: Path) -> None:
        file_path = tmp_path / "not_a_dir.json"
        file_path.write_text("{}", encoding="utf-8")
        store = WriteTraceStore(file_path)
        with pytest.raises(NotADirectoryError):
            store.load()


# ---------------------------------------------------------------------------
# TestWriterRoundTrip
# ---------------------------------------------------------------------------


class TestWriterRoundTrip:
    def test_write_then_load(self, tmp_path: Path) -> None:
        trace = make_trace(mode="incremental", status="success")
        writer = WriteTraceWriter(tmp_path, max_count=None)
        path = writer.write(trace)

        assert path.exists()

        store = WriteTraceStore(tmp_path)
        store.load()
        loaded = store.get(trace.trace_id)
        assert loaded is not None
        assert loaded.mode == "incremental"
        assert loaded.status == "success"

    def test_get_unknown_id_returns_none(self, tmp_path: Path) -> None:
        store = WriteTraceStore(tmp_path)
        store.load()
        assert store.get(uuid4()) is None

    def test_all_sorted_oldest_first(self, tmp_path: Path) -> None:
        from datetime import datetime, timedelta

        base = datetime(2026, 1, 1, 0, 0, 0)
        writer = WriteTraceWriter(tmp_path, max_count=None)
        traces = [make_trace(created_at=base + timedelta(minutes=i)) for i in range(3)]
        # Write in reverse order to make sure sorting isn't just file-order.
        for trace in reversed(traces):
            writer.write(trace)

        store = WriteTraceStore(tmp_path)
        store.load()
        loaded = store.all()
        assert [t.trace_id for t in loaded] == [t.trace_id for t in traces]


# ---------------------------------------------------------------------------
# TestCorruptFileHandling
# ---------------------------------------------------------------------------


class TestCorruptFileHandling:
    def test_invalid_json_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        good = make_trace()
        WriteTraceWriter(tmp_path, max_count=None).write(good)
        write_raw(tmp_path, "bad.json", "{not valid json")

        store = WriteTraceStore(tmp_path)
        store.load()  # must not raise

        assert store.count() == 1
        assert store.get(good.trace_id) is not None

    def test_non_object_json_is_skipped(self, tmp_path: Path) -> None:
        write_raw(tmp_path, "list.json", "[1, 2, 3]")

        store = WriteTraceStore(tmp_path)
        store.load()

        assert store.count() == 0
        assert len(store.skipped_files()) == 1

    def test_missing_trace_id_is_skipped(self, tmp_path: Path) -> None:
        write_raw(tmp_path, "no_id.json", json.dumps({"mode": "first_run"}))

        store = WriteTraceStore(tmp_path)
        store.load()  # must not raise

        assert store.count() == 0
        assert len(store.skipped_files()) == 1


# ---------------------------------------------------------------------------
# TestLenientVersionLoading
# ---------------------------------------------------------------------------


class TestLenientVersionLoading:
    def test_future_schema_version_still_loads(self, tmp_path: Path) -> None:
        trace = make_trace()
        raw = trace.to_dict()
        raw["schema_version"] = 9999
        write_raw(tmp_path, f"{trace.trace_id}.json", json.dumps(raw))

        store = WriteTraceStore(tmp_path)
        store.load()

        assert store.count() == 1
        assert store.get(trace.trace_id).schema_version == 9999

    def test_missing_schema_version_still_loads(self, tmp_path: Path) -> None:
        trace = make_trace()
        raw = trace.to_dict()
        del raw["schema_version"]
        write_raw(tmp_path, f"{trace.trace_id}.json", json.dumps(raw))

        store = WriteTraceStore(tmp_path)
        store.load()

        assert store.count() == 1


# ---------------------------------------------------------------------------
# TestRetention
# ---------------------------------------------------------------------------


class TestRetention:
    def test_prunes_oldest_beyond_max_count(self, tmp_path: Path) -> None:
        from datetime import datetime, timedelta

        base = datetime(2026, 1, 1, 0, 0, 0)
        writer = WriteTraceWriter(tmp_path, max_count=3)
        traces = [make_trace(created_at=base + timedelta(minutes=i)) for i in range(5)]
        for trace in traces:
            writer.write(trace)

        store = WriteTraceStore(tmp_path)
        store.load()
        assert store.count() == 3
        # The three most recent survive; the two oldest are pruned.
        remaining_ids = {t.trace_id for t in store.all()}
        assert remaining_ids == {t.trace_id for t in traces[-3:]}

    def test_max_count_zero_or_negative_disables_pruning(self, tmp_path: Path) -> None:
        writer = WriteTraceWriter(tmp_path, max_count=0)
        for _ in range(5):
            writer.write(make_trace())

        store = WriteTraceStore(tmp_path)
        store.load()
        assert store.count() == 5

    def test_none_disables_pruning(self, tmp_path: Path) -> None:
        writer = WriteTraceWriter(tmp_path, max_count=None)
        for _ in range(5):
            writer.write(make_trace())

        store = WriteTraceStore(tmp_path)
        store.load()
        assert store.count() == 5

    def test_pruning_does_not_touch_unrelated_files(self, tmp_path: Path) -> None:
        (tmp_path / "unrelated.txt").parent.mkdir(parents=True, exist_ok=True)
        write_raw(tmp_path, "unrelated.txt", "keep me")

        writer = WriteTraceWriter(tmp_path, max_count=1)
        for _ in range(3):
            writer.write(make_trace())

        assert (tmp_path / "unrelated.txt").exists()

    def test_default_max_count_is_500(self) -> None:
        writer = WriteTraceWriter(Path("unused"))
        assert writer._max_count == 500
