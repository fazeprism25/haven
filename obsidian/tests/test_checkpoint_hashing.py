"""Unit tests for obsidian.checkpoint.hashing.

Test groups
-----------
TestTurnHash       -- determinism, uniqueness, whitespace sensitivity,
                     unicode, empty strings.
TestTranscriptHash -- determinism, uniqueness, empty/long transcripts,
                     stability (the prefix-comparison property PR 4's
                     incremental ingestion will rely on).
"""

from __future__ import annotations

import hashlib

from obsidian.checkpoint.hashing import transcript_hash, turn_hash


class TestTurnHash:
    def test_returns_hex_digest(self) -> None:
        digest = turn_hash("user", "hello")
        assert isinstance(digest, str)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_deterministic(self) -> None:
        assert turn_hash("user", "hello") == turn_hash("user", "hello")

    def test_matches_expected_sha256(self) -> None:
        expected = hashlib.sha256(b"user:hello").hexdigest()
        assert turn_hash("user", "hello") == expected

    def test_different_content_differs(self) -> None:
        assert turn_hash("user", "hello") != turn_hash("user", "goodbye")

    def test_different_role_differs(self) -> None:
        assert turn_hash("user", "hello") != turn_hash("assistant", "hello")

    def test_role_content_boundary_is_not_ambiguous(self) -> None:
        # "user:" + "hi" must not collide with "use" + "r:hi" -- the ":"
        # join means these two inputs are NOT expected to collide, and
        # they don't, because the whole "role:content" string is hashed,
        # not concatenated role+content without a separator.
        a = turn_hash("user", "hi")
        b = turn_hash("use", "r:hi")
        assert a != b

    def test_whitespace_is_significant(self) -> None:
        # Deliberately NOT normalised: hashing exists to detect edits,
        # including whitespace-only ones, so a trailing space must
        # produce a different hash rather than being silently collapsed.
        assert turn_hash("user", "hello") != turn_hash("user", "hello ")
        assert turn_hash("user", "hello") != turn_hash("user", " hello")
        assert turn_hash("user", "hello world") != turn_hash("user", "hello  world")

    def test_case_is_significant(self) -> None:
        assert turn_hash("user", "Hello") != turn_hash("user", "hello")

    def test_empty_content_produces_digest(self) -> None:
        digest = turn_hash("user", "")
        assert isinstance(digest, str)
        assert len(digest) == 64

    def test_empty_role_produces_digest(self) -> None:
        digest = turn_hash("", "hello")
        assert isinstance(digest, str)
        assert len(digest) == 64

    def test_both_empty_produces_digest(self) -> None:
        digest = turn_hash("", "")
        assert isinstance(digest, str)
        assert len(digest) == 64

    def test_unicode_content_deterministic(self) -> None:
        content = "The user's goal is to apply to MIT 🎓 会話"
        assert turn_hash("user", content) == turn_hash("user", content)

    def test_unicode_content_differs_from_ascii(self) -> None:
        assert turn_hash("user", "Muscat") != turn_hash("user", "Muşcat")

    def test_long_content(self) -> None:
        content = "x" * 100_000
        digest = turn_hash("user", content)
        assert isinstance(digest, str)
        assert len(digest) == 64


class TestTranscriptHash:
    def test_returns_hex_digest(self) -> None:
        digest = transcript_hash(["aaa", "bbb"])
        assert isinstance(digest, str)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_deterministic(self) -> None:
        assert transcript_hash(["aaa", "bbb"]) == transcript_hash(["aaa", "bbb"])

    def test_order_sensitive(self) -> None:
        assert transcript_hash(["aaa", "bbb"]) != transcript_hash(["bbb", "aaa"])

    def test_appending_a_turn_changes_hash(self) -> None:
        base = transcript_hash(["aaa", "bbb"])
        grown = transcript_hash(["aaa", "bbb", "ccc"])
        assert base != grown

    def test_empty_list_produces_digest(self) -> None:
        digest = transcript_hash([])
        assert isinstance(digest, str)
        assert len(digest) == 64
        assert digest == hashlib.sha256(b"").hexdigest()

    def test_single_turn(self) -> None:
        digest = transcript_hash(["aaa"])
        assert isinstance(digest, str)
        assert len(digest) == 64

    def test_no_ambiguous_join_across_boundaries(self) -> None:
        # ["ab", "cd"] must not collide with ["a", "bcd"] purely from
        # naive concatenation -- the separator prevents this.
        assert transcript_hash(["ab", "cd"]) != transcript_hash(["a", "bcd"])

    def test_prefix_recomputation_matches_stored_prefix(self) -> None:
        # The exact property PR 4's incremental ingestion depends on:
        # hashing just the first k turn_hashes today must equal what
        # would have been computed if only those k turns existed at the
        # time, so a stored prefix can be verified against a regrown
        # transcript's recomputed prefix.
        hashes = [turn_hash("user", f"turn {i}") for i in range(10)]
        prefix_stored = transcript_hash(hashes[:5])
        prefix_recomputed = transcript_hash(hashes[:5])
        assert prefix_stored == prefix_recomputed

    def test_long_conversation(self) -> None:
        hashes = [turn_hash("user", f"turn {i}") for i in range(5000)]
        digest = transcript_hash(hashes)
        assert isinstance(digest, str)
        assert len(digest) == 64

    def test_stable_across_repeated_calls_on_long_conversation(self) -> None:
        hashes = [turn_hash("user", f"turn {i}") for i in range(500)]
        assert transcript_hash(hashes) == transcript_hash(hashes)
