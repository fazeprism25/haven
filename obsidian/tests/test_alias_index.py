"""Unit tests for obsidian.ontology.alias_index.AliasIndex.

Test groups
-----------
TestEmptyInput          — empty collection invariants.
TestLabels              — label indexing, case, whitespace.
TestAliases             — alias indexing, case, whitespace.
TestNormalization       — normalization consistency with text_utils.normalize.
TestCaseInsensitive     — exhaustive case-folding coverage.
TestWhitespaceNorm      — leading, trailing, internal whitespace handling.
TestDuplicateAliases    — cross-concept conflicts and within-concept dedup.
TestContains            — contains() truthy/falsy + normalization.
TestSize                — size() counts unique keys, not concepts.
TestRebuild             — rebuild() replaces state atomically.
TestDeterminism         — insertion-order independence across permutations.
TestUnicode             — unicode labels, aliases, and case folding.
"""

from __future__ import annotations

import itertools
from uuid import UUID

import pytest

from obsidian.ontology.alias_index import AliasIndex
from obsidian.ontology.models import Concept
from obsidian.ontology.text_utils import normalize


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_concept(label: str, *aliases: str) -> Concept:
    return Concept.from_label(label, aliases=tuple(aliases))


# ---------------------------------------------------------------------------
# TestEmptyInput
# ---------------------------------------------------------------------------


class TestEmptyInput:
    def test_build_empty_list_does_not_raise(self) -> None:
        idx = AliasIndex()
        idx.build([])

    def test_size_is_zero_after_empty_build(self) -> None:
        idx = AliasIndex()
        idx.build([])
        assert idx.size() == 0

    def test_lookup_on_empty_returns_none(self) -> None:
        idx = AliasIndex()
        idx.build([])
        assert idx.lookup("haven") is None

    def test_contains_on_empty_returns_false(self) -> None:
        idx = AliasIndex()
        idx.build([])
        assert idx.contains("haven") is False

    def test_conflicts_empty_on_empty_build(self) -> None:
        idx = AliasIndex()
        idx.build([])
        assert idx.conflicts == {}

    def test_build_generator_accepted(self) -> None:
        idx = AliasIndex()
        idx.build(c for c in [])  # generator, not list
        assert idx.size() == 0


# ---------------------------------------------------------------------------
# TestLabels
# ---------------------------------------------------------------------------


class TestLabels:
    def test_label_is_indexed(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("Haven") == c.id

    def test_unknown_label_returns_none(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("Claude") is None

    def test_multiple_concepts_labels_all_indexed(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        c3 = make_concept("DTU")
        idx = AliasIndex()
        idx.build([c1, c2, c3])
        assert idx.lookup("Haven") == c1.id
        assert idx.lookup("Claude") == c2.id
        assert idx.lookup("DTU") == c3.id

    def test_returned_value_is_correct_uuid(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        result = idx.lookup("Haven")
        assert isinstance(result, UUID)
        assert result == c.id


# ---------------------------------------------------------------------------
# TestAliases
# ---------------------------------------------------------------------------


class TestAliases:
    def test_single_alias_indexed(self) -> None:
        c = make_concept("Haven", "Personal Brain")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("Personal Brain") == c.id

    def test_multiple_aliases_all_indexed(self) -> None:
        c = make_concept("Claude", "Claude AI", "Anthropic Claude", "Claude LLM")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("Claude AI") == c.id
        assert idx.lookup("Anthropic Claude") == c.id
        assert idx.lookup("Claude LLM") == c.id

    def test_label_and_aliases_both_map_to_same_id(self) -> None:
        c = make_concept("Haven", "Second Brain", "Personal Brain")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("Haven") == c.id
        assert idx.lookup("Second Brain") == c.id
        assert idx.lookup("Personal Brain") == c.id

    def test_size_counts_label_plus_aliases(self) -> None:
        c = make_concept("Claude", "Claude AI", "Anthropic Claude")
        idx = AliasIndex()
        idx.build([c])
        assert idx.size() == 3  # label + 2 aliases


# ---------------------------------------------------------------------------
# TestNormalization
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_lookup_key_matches_text_utils_normalize(self) -> None:
        c = make_concept("Memory Engine")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup(normalize("Memory Engine")) == c.id

    def test_alias_lookup_key_matches_text_utils_normalize(self) -> None:
        c = make_concept("Haven", "Second Brain")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup(normalize("Second Brain")) == c.id

    def test_multiword_label_preserves_internal_space(self) -> None:
        c = make_concept("Tower of London")
        idx = AliasIndex()
        idx.build([c])
        # normalize keeps internal spaces; "tower of london" is the stored key
        assert idx.lookup("tower of london") == c.id

    def test_label_with_leading_trailing_whitespace(self) -> None:
        # Concept allows labels with surrounding whitespace
        c = make_concept("  SpaCed  ")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("spaced") == c.id
        assert idx.lookup("  SpaCed  ") == c.id

    def test_hyphenated_label_preserved(self) -> None:
        # normalize does not strip hyphens — they remain in the key
        c = make_concept("GPT-4")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("GPT-4") == c.id
        assert idx.lookup("gpt-4") == c.id


# ---------------------------------------------------------------------------
# TestCaseInsensitive
# ---------------------------------------------------------------------------


class TestCaseInsensitive:
    def test_label_all_lowercase(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("haven") == c.id

    def test_label_all_uppercase(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("HAVEN") == c.id

    def test_label_mixed_case(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("HaVeN") == c.id
        assert idx.lookup("hAVEn") == c.id

    def test_alias_case_insensitive(self) -> None:
        c = make_concept("Haven", "Personal Brain")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("personal brain") == c.id
        assert idx.lookup("PERSONAL BRAIN") == c.id
        assert idx.lookup("Personal Brain") == c.id


# ---------------------------------------------------------------------------
# TestWhitespaceNorm
# ---------------------------------------------------------------------------


class TestWhitespaceNorm:
    def test_leading_whitespace_stripped(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("   haven") == c.id

    def test_trailing_whitespace_stripped(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("haven   ") == c.id

    def test_both_sides_stripped(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("  Haven  ") == c.id

    def test_tab_newline_stripped(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("\tHaven\n") == c.id

    def test_alias_whitespace_stripped(self) -> None:
        c = make_concept("Haven", "Personal Brain")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("  Personal Brain  ") == c.id

    def test_contains_strips_whitespace(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.contains("  HAVEN  ") is True


# ---------------------------------------------------------------------------
# TestDuplicateAliases
# ---------------------------------------------------------------------------


class TestDuplicateAliases:
    def test_cross_concept_conflict_deterministic_forward(self) -> None:
        """Both input orders produce the same winner."""
        c1 = make_concept("Haven", "Shared Alias")
        c2 = make_concept("Claude", "Shared Alias")
        idx_fwd = AliasIndex()
        idx_fwd.build([c1, c2])
        idx_rev = AliasIndex()
        idx_rev.build([c2, c1])
        assert idx_fwd.lookup("Shared Alias") == idx_rev.lookup("Shared Alias")

    def test_cross_concept_conflict_winner_is_lower_uuid(self) -> None:
        c1 = make_concept("Haven", "Shared")
        c2 = make_concept("Claude", "Shared")
        idx = AliasIndex()
        idx.build([c1, c2])
        winner = c1.id if str(c1.id) < str(c2.id) else c2.id
        assert idx.lookup("Shared") == winner

    def test_cross_concept_conflict_recorded_in_conflicts(self) -> None:
        c1 = make_concept("Haven", "Shared")
        c2 = make_concept("Claude", "Shared")
        idx = AliasIndex()
        idx.build([c1, c2])
        assert normalize("Shared") in idx.conflicts

    def test_cross_concept_conflict_winner_in_conflicts_first(self) -> None:
        c1 = make_concept("Haven", "Shared")
        c2 = make_concept("Claude", "Shared")
        idx = AliasIndex()
        idx.build([c1, c2])
        winner = c1.id if str(c1.id) < str(c2.id) else c2.id
        loser = c2.id if winner == c1.id else c1.id
        recorded_winner, recorded_loser = idx.conflicts[normalize("Shared")]
        assert recorded_winner == winner
        assert recorded_loser == loser

    def test_within_concept_label_alias_same_normalized_no_crash(self) -> None:
        # "Haven" and "haven" both normalize to "haven"
        c = make_concept("Haven", "haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("haven") == c.id

    def test_within_concept_dedup_not_counted_as_conflict(self) -> None:
        c = make_concept("Haven", "haven")
        idx = AliasIndex()
        idx.build([c])
        assert len(idx.conflicts) == 0

    def test_within_concept_dedup_size_is_one(self) -> None:
        c = make_concept("Haven", "haven")  # both keys normalize to "haven"
        idx = AliasIndex()
        idx.build([c])
        assert idx.size() == 1

    def test_conflict_does_not_suppress_non_conflicting_keys(self) -> None:
        c1 = make_concept("Haven", "Shared", "UniqueA")
        c2 = make_concept("Claude", "Shared", "UniqueB")
        idx = AliasIndex()
        idx.build([c1, c2])
        assert idx.lookup("UniqueA") == c1.id
        assert idx.lookup("UniqueB") == c2.id

    def test_label_vs_alias_cross_concept_conflict(self) -> None:
        """Concept label collides with another concept's alias."""
        c1 = make_concept("Shared Term")
        c2 = make_concept("Claude", "Shared Term")
        idx = AliasIndex()
        idx.build([c1, c2])
        winner = c1.id if str(c1.id) < str(c2.id) else c2.id
        assert idx.lookup("Shared Term") == winner
        assert normalize("Shared Term") in idx.conflicts

    def test_conflicts_returns_copy(self) -> None:
        c1 = make_concept("Haven", "Shared")
        c2 = make_concept("Claude", "Shared")
        idx = AliasIndex()
        idx.build([c1, c2])
        copy = idx.conflicts
        copy["injected"] = (c1.id, c2.id)  # mutate the copy
        assert "injected" not in idx.conflicts


# ---------------------------------------------------------------------------
# TestContains
# ---------------------------------------------------------------------------


class TestContains:
    def test_contains_label_present(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.contains("Haven") is True

    def test_contains_label_case_insensitive(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.contains("haven") is True
        assert idx.contains("HAVEN") is True

    def test_contains_alias_present(self) -> None:
        c = make_concept("Haven", "Personal Brain")
        idx = AliasIndex()
        idx.build([c])
        assert idx.contains("Personal Brain") is True

    def test_contains_absent(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.contains("Claude") is False

    def test_contains_empty_lookup(self) -> None:
        idx = AliasIndex()
        idx.build([])
        assert idx.contains("anything") is False


# ---------------------------------------------------------------------------
# TestSize
# ---------------------------------------------------------------------------


class TestSize:
    def test_size_empty(self) -> None:
        idx = AliasIndex()
        idx.build([])
        assert idx.size() == 0

    def test_size_single_concept_no_aliases(self) -> None:
        idx = AliasIndex()
        idx.build([make_concept("Haven")])
        assert idx.size() == 1

    def test_size_single_concept_with_aliases(self) -> None:
        idx = AliasIndex()
        idx.build([make_concept("Haven", "A", "B", "C")])
        assert idx.size() == 4  # label + 3 aliases

    def test_size_multiple_concepts_no_overlap(self) -> None:
        idx = AliasIndex()
        idx.build([make_concept("Haven", "A"), make_concept("Claude", "B")])
        assert idx.size() == 4  # 2 labels + 2 aliases, no conflicts

    def test_size_with_cross_concept_conflict(self) -> None:
        # "haven", "shared" (conflict → 1 key), "claude" → 3 total
        c1 = make_concept("Haven", "Shared")
        c2 = make_concept("Claude", "Shared")
        idx = AliasIndex()
        idx.build([c1, c2])
        assert idx.size() == 3

    def test_size_within_concept_dedup(self) -> None:
        # "Haven" and "haven" both normalise to "haven" → 1 key only
        c = make_concept("Haven", "haven")
        idx = AliasIndex()
        idx.build([c])
        assert idx.size() == 1


# ---------------------------------------------------------------------------
# TestRebuild
# ---------------------------------------------------------------------------


class TestRebuild:
    def test_rebuild_replaces_previous_index(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        idx = AliasIndex()
        idx.build([c1])
        assert idx.lookup("Haven") == c1.id
        assert idx.lookup("Claude") is None
        idx.rebuild([c2])
        assert idx.lookup("Haven") is None
        assert idx.lookup("Claude") == c2.id

    def test_rebuild_updates_size(self) -> None:
        c1 = make_concept("Haven", "A", "B")
        c2 = make_concept("Claude")
        idx = AliasIndex()
        idx.build([c1])
        assert idx.size() == 3
        idx.rebuild([c2])
        assert idx.size() == 1

    def test_rebuild_empty_clears_index(self) -> None:
        c = make_concept("Haven")
        idx = AliasIndex()
        idx.build([c])
        idx.rebuild([])
        assert idx.size() == 0
        assert idx.lookup("Haven") is None

    def test_rebuild_resets_conflicts(self) -> None:
        c1 = make_concept("Haven", "Shared")
        c2 = make_concept("Claude", "Shared")
        idx = AliasIndex()
        idx.build([c1, c2])
        assert len(idx.conflicts) == 1
        idx.rebuild([make_concept("DTU"), make_concept("Copenhagen")])
        assert len(idx.conflicts) == 0

    def test_rebuild_introduces_new_conflicts(self) -> None:
        idx = AliasIndex()
        idx.build([make_concept("Haven")])
        assert len(idx.conflicts) == 0
        c1 = make_concept("X", "Clash")
        c2 = make_concept("Y", "Clash")
        idx.rebuild([c1, c2])
        assert len(idx.conflicts) == 1

    def test_rebuild_accepts_generator(self) -> None:
        concepts = [make_concept("Haven"), make_concept("Claude")]
        idx = AliasIndex()
        idx.build([])
        idx.rebuild(c for c in concepts)
        assert idx.size() == 2


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_index_regardless_of_insertion_order(self) -> None:
        concepts = [
            make_concept("Haven", "Personal Brain"),
            make_concept("Claude", "Claude AI"),
            make_concept("DTU", "Technical University of Denmark"),
        ]
        snapshots: list[frozenset] = []
        for perm in itertools.permutations(concepts):
            idx = AliasIndex()
            idx.build(list(perm))
            snapshot = frozenset((k, str(v)) for k, v in idx._index.items())
            snapshots.append(snapshot)
        assert all(s == snapshots[0] for s in snapshots), (
            "Index content must be identical regardless of insertion order"
        )

    def test_conflict_winner_same_regardless_of_order(self) -> None:
        c1 = make_concept("Haven", "Shared")
        c2 = make_concept("Claude", "Shared")
        idx1 = AliasIndex()
        idx1.build([c1, c2])
        idx2 = AliasIndex()
        idx2.build([c2, c1])
        assert idx1.lookup("Shared") == idx2.lookup("Shared")

    def test_multiple_builds_produce_identical_results(self) -> None:
        concepts = [make_concept("Haven", "A"), make_concept("Claude", "B")]
        idx = AliasIndex()
        idx.build(concepts)
        snapshot1 = dict(idx._index)
        idx.build(concepts)
        snapshot2 = dict(idx._index)
        assert snapshot1 == snapshot2

    def test_rebuild_idempotent(self) -> None:
        concepts = [make_concept("Haven"), make_concept("Claude")]
        idx = AliasIndex()
        idx.rebuild(concepts)
        snapshot1 = dict(idx._index)
        idx.rebuild(concepts)
        snapshot2 = dict(idx._index)
        assert snapshot1 == snapshot2


# ---------------------------------------------------------------------------
# TestUnicode
# ---------------------------------------------------------------------------


class TestUnicode:
    def test_latin_extended_label(self) -> None:
        c = make_concept("École")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("École") == c.id
        assert idx.lookup("école") == c.id

    def test_unicode_alias(self) -> None:
        c = make_concept("School", "École")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("école") == c.id
        assert idx.lookup("ÉCOLE") == c.id

    def test_umlaut_label(self) -> None:
        c = make_concept("Über System")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("über system") == c.id
        assert idx.lookup("Über System") == c.id

    def test_cjk_characters_pass_through(self) -> None:
        c = make_concept("人工知能")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("人工知能") == c.id

    def test_arabic_label(self) -> None:
        c = make_concept("مرحبا")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("مرحبا") == c.id

    def test_mixed_script_alias(self) -> None:
        c = make_concept("Project Alpha", "Projet α")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup("projet α") == c.id

    def test_unicode_normalization_consistency_with_text_utils(self) -> None:
        c = make_concept("Ångström Unit")
        idx = AliasIndex()
        idx.build([c])
        assert idx.lookup(normalize("Ångström Unit")) == c.id
        assert idx.lookup("ångström unit") == c.id
