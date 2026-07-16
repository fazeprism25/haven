"""Tests for ``obsidian.manager_ai.topic_canonicalizer``.

Test groups
-----------
TestKnownAliases     -- synonyms/spelling variants collapse to the seed
                         vocabulary's canonical display name.
TestNovelTopics       -- a topic outside the alias table is still
                         accepted, Title-Cased.
TestDedupeAndCap      -- entries that canonicalize to the same name merge
                         (keeping the higher confidence); the result never
                         exceeds MAX_TOPICS.
TestDeterministicOrder -- output order is a pure function of the input
                         (descending confidence, alphabetical tie-break).
TestDefensiveHandling  -- malformed entries are skipped, not raised.
"""

from __future__ import annotations

from obsidian.core.value_objects import TopicTag
from obsidian.manager_ai.topic_canonicalizer import (
    MAX_TOPICS,
    canonicalize_topic_name,
    canonicalize_topics,
)


class TestKnownAliases:
    def test_synonyms_collapse_to_canonical_ai(self) -> None:
        assert canonicalize_topic_name("machine learning") == "AI"
        assert canonicalize_topic_name("ML") == "AI"
        assert canonicalize_topic_name("Artificial Intelligence") == "AI"

    def test_case_and_whitespace_insensitive(self) -> None:
        assert canonicalize_topic_name("  AI  ") == "AI"
        assert canonicalize_topic_name("ai") == "AI"

    def test_programming_synonyms(self) -> None:
        assert canonicalize_topic_name("coding") == "Programming"
        assert canonicalize_topic_name("software engineering") == "Programming"


class TestNovelTopics:
    def test_unrecognized_topic_is_title_cased_not_rejected(self) -> None:
        assert canonicalize_topic_name("underwater basket weaving") == (
            "Underwater Basket Weaving"
        )


class TestDedupeAndCap:
    def test_duplicate_canonical_names_merge_keeping_higher_confidence(self) -> None:
        result = canonicalize_topics(
            [
                {"topic": "AI", "confidence": 0.4},
                {"topic": "machine learning", "confidence": 0.9},
            ]
        )
        assert result == (TopicTag(name="AI", confidence=0.9),)

    def test_result_never_exceeds_max_topics(self) -> None:
        raw = [
            {"topic": f"topic{i}", "confidence": 0.1 * i}
            for i in range(10)
        ]
        result = canonicalize_topics(raw)
        assert len(result) == MAX_TOPICS

    def test_keeps_highest_confidence_entries_when_capping(self) -> None:
        raw = [
            {"topic": "AI", "confidence": 0.9},
            {"topic": "Programming", "confidence": 0.8},
            {"topic": "Fitness", "confidence": 0.7},
            {"topic": "Finance", "confidence": 0.1},
        ]
        result = canonicalize_topics(raw)
        assert len(result) == MAX_TOPICS
        assert {t.name for t in result} == {"AI", "Programming", "Fitness"}


class TestDeterministicOrder:
    def test_sorted_by_descending_confidence(self) -> None:
        result = canonicalize_topics(
            [
                {"topic": "Fitness", "confidence": 0.2},
                {"topic": "AI", "confidence": 0.9},
            ]
        )
        assert [t.name for t in result] == ["AI", "Fitness"]

    def test_alphabetical_tie_break(self) -> None:
        result = canonicalize_topics(
            [
                {"topic": "Travel", "confidence": 0.5},
                {"topic": "AI", "confidence": 0.5},
            ]
        )
        assert [t.name for t in result] == ["AI", "Travel"]

    def test_same_input_always_produces_same_output(self) -> None:
        raw = [{"topic": "AI", "confidence": 0.6}, {"topic": "Travel", "confidence": 0.3}]
        assert canonicalize_topics(raw) == canonicalize_topics(raw)


class TestDefensiveHandling:
    def test_empty_input_returns_empty_tuple(self) -> None:
        assert canonicalize_topics([]) == ()

    def test_entry_missing_topic_is_skipped(self) -> None:
        result = canonicalize_topics([{"confidence": 0.9}, {"topic": "AI", "confidence": 0.5}])
        assert result == (TopicTag(name="AI", confidence=0.5),)

    def test_blank_topic_is_skipped(self) -> None:
        result = canonicalize_topics([{"topic": "   ", "confidence": 0.9}])
        assert result == ()

    def test_missing_confidence_defaults(self) -> None:
        result = canonicalize_topics([{"topic": "AI"}])
        assert result == (TopicTag(name="AI", confidence=0.5),)

    def test_out_of_range_confidence_is_clamped(self) -> None:
        result = canonicalize_topics([{"topic": "AI", "confidence": 5.0}])
        assert result[0].confidence == 1.0
        result = canonicalize_topics([{"topic": "AI", "confidence": -5.0}])
        assert result[0].confidence == 0.0
