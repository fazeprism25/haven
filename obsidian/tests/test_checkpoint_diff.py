"""Unit tests for obsidian.checkpoint.diff.classify_turns.

Test groups
-----------
TestFirstRun      -- no checkpoint at all.
TestIncremental   -- clean, append-only growth.
TestFallback      -- every way a conversation can change that is *not* a
                     clean append: an earlier edit, a deletion, a
                     truncation, a reordering, and an unchanged-length
                     "duplicate" input (which callers are expected to have
                     already filtered out via a whole-transcript hash
                     comparison, but the function must still behave safely
                     if it ever sees one).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from obsidian.checkpoint.diff import TurnDiff, classify_turns
from obsidian.checkpoint.models import ConversationCheckpoint
from obsidian.core.enums import SourceType

_FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_checkpoint(turn_hashes: list) -> ConversationCheckpoint:
    return ConversationCheckpoint(
        conversation_id=uuid4(),
        source=SourceType.CHATGPT,
        external_key="/c/abc123",
        turn_count=len(turn_hashes),
        last_processed_turn_index=len(turn_hashes) - 1,
        turn_hashes=list(turn_hashes),
        transcript_hash="irrelevant-for-diff",
        created_at=_FIXED_TIME,
        last_processed_at=_FIXED_TIME,
    )


class TestFirstRun:
    def test_no_checkpoint_is_first_run(self) -> None:
        diff = classify_turns(None, ["a", "b", "c"])
        assert diff == TurnDiff(mode="first_run", new_turn_start_index=0)

    def test_no_checkpoint_with_single_turn(self) -> None:
        diff = classify_turns(None, ["a"])
        assert diff.mode == "first_run"
        assert diff.new_turn_start_index == 0

    def test_no_checkpoint_with_empty_turns(self) -> None:
        diff = classify_turns(None, [])
        assert diff.mode == "first_run"
        assert diff.new_turn_start_index == 0


class TestIncremental:
    def test_single_turn_appended(self) -> None:
        checkpoint = make_checkpoint(["a", "b", "c"])
        diff = classify_turns(checkpoint, ["a", "b", "c", "d"])
        assert diff == TurnDiff(mode="incremental", new_turn_start_index=3)

    def test_multiple_turns_appended(self) -> None:
        checkpoint = make_checkpoint(["a", "b"])
        diff = classify_turns(checkpoint, ["a", "b", "c", "d", "e"])
        assert diff == TurnDiff(mode="incremental", new_turn_start_index=2)

    def test_growth_from_single_turn_checkpoint(self) -> None:
        checkpoint = make_checkpoint(["a"])
        diff = classify_turns(checkpoint, ["a", "b"])
        assert diff == TurnDiff(mode="incremental", new_turn_start_index=1)

    def test_growth_from_empty_checkpoint(self) -> None:
        checkpoint = make_checkpoint([])
        diff = classify_turns(checkpoint, ["a"])
        assert diff == TurnDiff(mode="incremental", new_turn_start_index=0)


class TestFallback:
    def test_earlier_turn_edited_even_with_new_turns_appended(self) -> None:
        checkpoint = make_checkpoint(["a", "b", "c"])
        diff = classify_turns(checkpoint, ["a", "x", "c", "d"])
        assert diff == TurnDiff(mode="fallback", new_turn_start_index=0)

    def test_earlier_turn_deleted(self) -> None:
        checkpoint = make_checkpoint(["a", "b", "c"])
        diff = classify_turns(checkpoint, ["a", "c"])
        assert diff == TurnDiff(mode="fallback", new_turn_start_index=0)

    def test_truncation_no_new_turns(self) -> None:
        checkpoint = make_checkpoint(["a", "b", "c"])
        diff = classify_turns(checkpoint, ["a", "b"])
        assert diff == TurnDiff(mode="fallback", new_turn_start_index=0)

    def test_truncation_to_empty(self) -> None:
        checkpoint = make_checkpoint(["a", "b", "c"])
        diff = classify_turns(checkpoint, [])
        assert diff == TurnDiff(mode="fallback", new_turn_start_index=0)

    def test_reordering_same_length(self) -> None:
        checkpoint = make_checkpoint(["a", "b", "c"])
        diff = classify_turns(checkpoint, ["a", "c", "b"])
        assert diff == TurnDiff(mode="fallback", new_turn_start_index=0)

    def test_reordering_with_growth(self) -> None:
        checkpoint = make_checkpoint(["a", "b", "c"])
        diff = classify_turns(checkpoint, ["c", "b", "a", "d"])
        assert diff == TurnDiff(mode="fallback", new_turn_start_index=0)

    def test_unchanged_transcript_is_fallback_not_incremental(self) -> None:
        # Callers are expected to short-circuit an exact transcript-hash
        # match before ever calling classify_turns (see module docstring);
        # this only documents that the function itself does not mistake
        # "identical" for "incremental" if it ever receives one.
        checkpoint = make_checkpoint(["a", "b", "c"])
        diff = classify_turns(checkpoint, ["a", "b", "c"])
        assert diff.mode == "fallback"

    def test_entirely_different_conversation_same_length(self) -> None:
        checkpoint = make_checkpoint(["a", "b"])
        diff = classify_turns(checkpoint, ["x", "y"])
        assert diff == TurnDiff(mode="fallback", new_turn_start_index=0)
