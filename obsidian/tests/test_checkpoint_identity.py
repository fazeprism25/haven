"""Unit tests for obsidian.checkpoint.identity.

Test groups
-----------
TestCheckpointNamespace   -- namespace is a stable, version-5 UUID.
TestDeriveConversationId  -- determinism, uniqueness, whitespace, unicode,
                             empty strings, case sensitivity.
TestDeriveEventId         -- determinism, uniqueness, long conversations.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from obsidian.checkpoint.identity import (
    CHECKPOINT_NAMESPACE,
    derive_conversation_id,
    derive_event_id,
)
from obsidian.checkpoint.hashing import turn_hash
from obsidian.core.enums import SourceType
from obsidian.ontology.identity import ONTOLOGY_NAMESPACE


class TestCheckpointNamespace:
    def test_is_uuid(self) -> None:
        assert isinstance(CHECKPOINT_NAMESPACE, UUID)

    def test_is_version_5(self) -> None:
        assert CHECKPOINT_NAMESPACE.version == 5

    def test_stable_across_calls(self) -> None:
        from obsidian.checkpoint.identity import CHECKPOINT_NAMESPACE as ns2

        assert CHECKPOINT_NAMESPACE == ns2

    def test_distinct_from_ontology_namespace(self) -> None:
        # A conversation ID and a concept ID must never collide even if
        # their source strings happened to match.
        assert CHECKPOINT_NAMESPACE != ONTOLOGY_NAMESPACE


class TestDeriveConversationId:
    def test_returns_uuid(self) -> None:
        assert isinstance(derive_conversation_id(SourceType.CHATGPT, "/c/abc123"), UUID)

    def test_is_version_5(self) -> None:
        assert derive_conversation_id(SourceType.CHATGPT, "/c/abc123").version == 5

    def test_deterministic_same_key(self) -> None:
        a = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        b = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        assert a == b

    def test_different_external_key_differs(self) -> None:
        a = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        b = derive_conversation_id(SourceType.CHATGPT, "/c/xyz789")
        assert a != b

    def test_different_source_differs(self) -> None:
        a = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        b = derive_conversation_id(SourceType.CLAUDE, "/c/abc123")
        assert a != b

    def test_strips_surrounding_whitespace(self) -> None:
        a = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        b = derive_conversation_id(SourceType.CHATGPT, "  /c/abc123  ")
        c = derive_conversation_id(SourceType.CHATGPT, "\t/c/abc123\n")
        assert a == b == c

    def test_is_case_sensitive(self) -> None:
        # Unlike concept_id, external keys are opaque identifiers (e.g.
        # URL paths) where case may be semantically meaningful, so they
        # are NOT case-folded.
        a = derive_conversation_id(SourceType.CHATGPT, "/c/AbC123")
        b = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        assert a != b

    def test_internal_whitespace_is_significant(self) -> None:
        a = derive_conversation_id(SourceType.CHATGPT, "/c/abc 123")
        b = derive_conversation_id(SourceType.CHATGPT, "/c/abc  123")
        assert a != b

    def test_empty_string_produces_uuid(self) -> None:
        assert isinstance(derive_conversation_id(SourceType.CHATGPT, ""), UUID)

    def test_unicode_key_deterministic(self) -> None:
        key = "/c/муscat-🧠-会話"
        a = derive_conversation_id(SourceType.CHATGPT, key)
        b = derive_conversation_id(SourceType.CHATGPT, key)
        assert a == b

    def test_unicode_key_differs_from_ascii(self) -> None:
        a = derive_conversation_id(SourceType.CHATGPT, "/c/abc")
        b = derive_conversation_id(SourceType.CHATGPT, "/c/abç")
        assert a != b

    def test_every_source_type_produces_distinct_ids(self) -> None:
        ids = {
            derive_conversation_id(source, "/c/shared-key")
            for source in SourceType
        }
        assert len(ids) == len(list(SourceType))


class TestDeriveEventId:
    def test_returns_uuid(self) -> None:
        cid = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        assert isinstance(derive_event_id(cid, 0, "deadbeef"), UUID)

    def test_is_version_5(self) -> None:
        cid = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        assert derive_event_id(cid, 0, "deadbeef").version == 5

    def test_deterministic(self) -> None:
        cid = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        a = derive_event_id(cid, 0, "deadbeef")
        b = derive_event_id(cid, 0, "deadbeef")
        assert a == b

    def test_different_conversation_differs(self) -> None:
        cid1 = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        cid2 = derive_conversation_id(SourceType.CHATGPT, "/c/xyz789")
        a = derive_event_id(cid1, 0, "deadbeef")
        b = derive_event_id(cid2, 0, "deadbeef")
        assert a != b

    def test_different_turn_index_differs(self) -> None:
        cid = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        a = derive_event_id(cid, 0, "deadbeef")
        b = derive_event_id(cid, 1, "deadbeef")
        assert a != b

    def test_different_turn_hash_differs(self) -> None:
        # An edited turn (different content -> different turn_hash) at the
        # same position must get a different event id, so evidence tied
        # to the old content is never conflated with the edited content.
        cid = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        a = derive_event_id(cid, 0, "deadbeef")
        b = derive_event_id(cid, 0, "cafebabe")
        assert a != b

    def test_stable_across_reprocessing(self) -> None:
        # Simulates the actual use case: the same turn, hashed the same
        # way, on two separate "requests" (two independent call sites),
        # must resolve to the same event id.
        cid = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        h = turn_hash("user", "I want to apply to MIT")

        first_run = derive_event_id(cid, 2, h)
        second_run = derive_event_id(cid, 2, h)
        assert first_run == second_run

    def test_long_conversation_all_turns_distinct(self) -> None:
        cid = derive_conversation_id(SourceType.CHATGPT, "/c/long-thread")
        ids = {
            derive_event_id(cid, i, turn_hash("user", f"turn {i}"))
            for i in range(2000)
        }
        assert len(ids) == 2000

    def test_large_turn_index(self) -> None:
        cid = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        assert isinstance(derive_event_id(cid, 10_000_000, "deadbeef"), UUID)

    def test_empty_turn_hash_produces_uuid(self) -> None:
        cid = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
        assert isinstance(derive_event_id(cid, 0, ""), UUID)
