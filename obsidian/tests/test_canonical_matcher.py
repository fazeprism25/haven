"""Unit tests for the conservative CanonicalMatcher.

The matcher is deterministic and purely textual (no embeddings, no LLM):

Test groups
-----------
TestExactMatch          -- identical normalised text → CONFIRM, carrying the
                           matched object.
TestRefinementUpdate     -- a strict whole-word prefix *extension* → UPDATE,
                           carrying the object being refined; longest prefix
                           wins; ties break deterministically on id.
TestNoFalsePositives     -- the cases the matcher must NOT treat as an UPDATE
                           (substring-not-whole-word, contradiction, suffix
                           extension, unrelated, archived target). False
                           negatives are preferred to false positives.
TestBackwardCompatibility -- match() still returns a bare KnowledgeDecision and
                           still returns NEW/CONFIRM for the inputs it always
                           did.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.models import (
    ExtractedFact,
    KnowledgeDecision,
    KnowledgeObject,
)


def _ko(fact: str, *, id: UUID | None = None, valid_until: datetime | None = None) -> KnowledgeObject:
    kwargs: dict = {"canonical_fact": fact}
    if id is not None:
        kwargs["id"] = id
    if valid_until is not None:
        kwargs["valid_until"] = valid_until
    return KnowledgeObject(**kwargs)


def _fact(text: str) -> ExtractedFact:
    return ExtractedFact(text=text, confidence=0.9)


# ---------------------------------------------------------------------------
# TestExactMatch
# ---------------------------------------------------------------------------


class TestExactMatch:
    def test_identical_text_confirms(self) -> None:
        existing = [_ko("The user lives in Muscat.")]
        decision, target = CanonicalMatcher().match_with_target(
            _fact("The user lives in Muscat."), existing
        )
        assert decision == KnowledgeDecision.CONFIRM
        assert target is existing[0]

    def test_confirm_is_case_and_whitespace_insensitive(self) -> None:
        existing = [_ko("The user lives in Muscat.")]
        decision, target = CanonicalMatcher().match_with_target(
            _fact("  the USER lives in muscat.  "), existing
        )
        assert decision == KnowledgeDecision.CONFIRM
        assert target is existing[0]

    def test_archived_exact_match_is_never_confirmed(self) -> None:
        # An archived object (valid_until set) is inactive; restating its
        # exact text must create a fresh NEW object, not silently CONFIRM
        # (bump confidence/confirmation_count on) the dead record.
        existing = [
            _ko("The user lives in Muscat.", valid_until=datetime(2020, 1, 1))
        ]
        decision, target = CanonicalMatcher().match_with_target(
            _fact("The user lives in Muscat."), existing
        )
        assert decision == KnowledgeDecision.NEW
        assert target is None

    def test_exact_match_wins_over_a_shorter_prefix(self) -> None:
        # Both an exact match and a shorter whole-word prefix exist; the exact
        # match must win (CONFIRM), never the prefix (UPDATE).
        prefix = _ko("I work at Google")
        exact = _ko("I work at Google as a staff engineer")
        decision, target = CanonicalMatcher().match_with_target(
            _fact("I work at Google as a staff engineer"), [prefix, exact]
        )
        assert decision == KnowledgeDecision.CONFIRM
        assert target is exact


# ---------------------------------------------------------------------------
# TestRefinementUpdate
# ---------------------------------------------------------------------------


class TestRefinementUpdate:
    def test_prefix_extension_updates(self) -> None:
        existing = [_ko("I work at Google")]
        decision, target = CanonicalMatcher().match_with_target(
            _fact("I work at Google as a staff engineer"), existing
        )
        assert decision == KnowledgeDecision.UPDATE
        assert target is existing[0]

    def test_match_returns_decision_only(self) -> None:
        existing = [_ko("I work at Google")]
        decision = CanonicalMatcher().match(
            _fact("I work at Google as a staff engineer"), existing
        )
        assert decision == KnowledgeDecision.UPDATE

    def test_longest_prefix_wins(self) -> None:
        short = _ko("I work")
        long = _ko("I work at Google")
        decision, target = CanonicalMatcher().match_with_target(
            _fact("I work at Google as a staff engineer"), [short, long]
        )
        assert decision == KnowledgeDecision.UPDATE
        assert target is long

    def test_trailing_period_does_not_block_prefix_extension(self) -> None:
        # Extractor canonical facts routinely end with a period; the old
        # strict prefix check ("google." vs "google as") missed this.
        existing = [_ko("I work at Google.")]
        decision, target = CanonicalMatcher().match_with_target(
            _fact("I work at Google as a Staff Engineer."), existing
        )
        assert decision == KnowledgeDecision.UPDATE
        assert target is existing[0]

    def test_comma_boundary_updates(self) -> None:
        # A refinement can be appended after a comma, not just a space.
        existing = [_ko("I live in Muscat.")]
        decision, target = CanonicalMatcher().match_with_target(
            _fact("I live in Muscat, Oman."), existing
        )
        assert decision == KnowledgeDecision.UPDATE
        assert target is existing[0]

    def test_tie_breaks_on_ascending_id(self) -> None:
        # Two objects with identical text (same prefix length) → lowest id.
        low = _ko(
            "I like coffee",
            id=UUID("00000000-0000-0000-0000-000000000001"),
        )
        high = _ko(
            "I like coffee",
            id=UUID("00000000-0000-0000-0000-000000000002"),
        )
        decision, target = CanonicalMatcher().match_with_target(
            _fact("I like coffee a lot"), [high, low]
        )
        assert decision == KnowledgeDecision.UPDATE
        assert target is low


# ---------------------------------------------------------------------------
# TestNoFalsePositives
# ---------------------------------------------------------------------------


class TestNoFalsePositives:
    def test_substring_but_not_whole_word_is_new(self) -> None:
        # "cat" is a character prefix of "category" but not a whole-word one.
        existing = [_ko("cat")]
        decision, target = CanonicalMatcher().match_with_target(
            _fact("category of animals"), existing
        )
        assert decision == KnowledgeDecision.NEW
        assert target is None

    def test_contradiction_is_new_not_update(self) -> None:
        # A negation breaks the prefix ("I like tea" is not a prefix of
        # "I don't like tea"), so the matcher never treats it as a refinement.
        existing = [_ko("I like tea")]
        decision, _ = CanonicalMatcher().match_with_target(
            _fact("I don't like tea"), existing
        )
        assert decision == KnowledgeDecision.NEW

    def test_suffix_extension_is_new_false_negative_is_acceptable(self) -> None:
        # "severely allergic to peanuts" prepends a word, so the old text is
        # not a prefix. We accept this false negative rather than risk a
        # false positive.
        existing = [_ko("allergic to peanuts")]
        decision, _ = CanonicalMatcher().match_with_target(
            _fact("severely allergic to peanuts"), existing
        )
        assert decision == KnowledgeDecision.NEW

    def test_unrelated_fact_is_new(self) -> None:
        existing = [_ko("I work at Google")]
        decision, _ = CanonicalMatcher().match_with_target(
            _fact("My favourite colour is blue"), existing
        )
        assert decision == KnowledgeDecision.NEW

    def test_archived_target_is_never_refined(self) -> None:
        # An object with valid_until set is inactive and must not be an UPDATE
        # target -- the refinement becomes a NEW object instead.
        existing = [
            _ko("I work at Google", valid_until=datetime(2020, 1, 1))
        ]
        decision, target = CanonicalMatcher().match_with_target(
            _fact("I work at Google as a staff engineer"), existing
        )
        assert decision == KnowledgeDecision.NEW
        assert target is None

    def test_empty_existing_fact_is_skipped(self) -> None:
        existing = [_ko("")]
        decision, _ = CanonicalMatcher().match_with_target(
            _fact("anything at all"), existing
        )
        assert decision == KnowledgeDecision.NEW

    def test_contradiction_with_shared_prefix_is_new(self) -> None:
        # "I work at Google" is a whole-word prefix of both, but "Microsoft"
        # diverges from "Google" instead of extending it.
        existing = [_ko("I work at Google.")]
        decision, _ = CanonicalMatcher().match_with_target(
            _fact("I work at Microsoft."), existing
        )
        assert decision == KnowledgeDecision.NEW

    def test_left_google_is_new_not_update(self) -> None:
        existing = [_ko("I work at Google.")]
        decision, _ = CanonicalMatcher().match_with_target(
            _fact("I left Google."), existing
        )
        assert decision == KnowledgeDecision.NEW

    def test_preference_reversal_is_new(self) -> None:
        existing = [_ko("I dislike coffee.")]
        decision, _ = CanonicalMatcher().match_with_target(
            _fact("I like coffee."), existing
        )
        assert decision == KnowledgeDecision.NEW

    def test_character_prefix_across_word_boundary_is_new(self) -> None:
        # "Google" must not match as a prefix of "Googleplex" -- the
        # character right after the match has to be a non-alnum boundary.
        existing = [_ko("I work at Google.")]
        decision, _ = CanonicalMatcher().match_with_target(
            _fact("I work at Googleplex."), existing
        )
        assert decision == KnowledgeDecision.NEW

    def test_punctuation_only_change_is_not_update(self) -> None:
        # Only the trailing punctuation differs -- no real word was added,
        # so this must not be treated as a refinement.
        existing = [_ko("I work at Google.")]
        decision, _ = CanonicalMatcher().match_with_target(
            _fact("I work at Google!"), existing
        )
        assert decision == KnowledgeDecision.NEW

    def test_existing_fact_shorter_than_new_after_stripped_prefix_no_crash(
        self,
    ) -> None:
        # "google" is exactly the stripped prefix of "google." with nothing
        # left over -- must not raise, and must not be treated as an UPDATE.
        existing = [_ko("Google.")]
        decision, _ = CanonicalMatcher().match_with_target(
            _fact("Google"), existing
        )
        assert decision == KnowledgeDecision.NEW


# ---------------------------------------------------------------------------
# TestBackwardCompatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_no_match_is_new(self) -> None:
        assert CanonicalMatcher().match(_fact("brand new fact"), []) == (
            KnowledgeDecision.NEW
        )

    def test_match_return_type_is_a_decision(self) -> None:
        existing = [_ko("The user lives in Muscat.")]
        result = CanonicalMatcher().match(_fact("The user lives in Muscat."), existing)
        assert isinstance(result, KnowledgeDecision)
