"""Unit tests for obsidian.ontology.query_resolver.QueryResolver.

Test groups
-----------
TestEmptyQuery          — empty string and whitespace-only inputs.
TestExactLabels         — single-word and multi-word label resolution.
TestAliases             — alias resolution (single-word and multi-word).
TestMultipleConcepts    — queries that mention more than one concept.
TestDuplicateMentions   — same concept mentioned multiple times.
TestNormalizationCase   — lowercase, uppercase, and mixed-case queries.
TestPunctuation         — commas, exclamation marks, and other punctuation.
TestUnknownConcepts     — queries with no matching concepts.
TestDeterministicOrder  — repeated calls and permutation consistency.
TestUnicode             — unicode labels and aliases.
TestGraphSkip           — concept in AliasIndex but absent from ConceptGraph.
TestStopWords           — stop words stripped before token-level resolution.
"""

from __future__ import annotations

import pytest

from obsidian.ontology.alias_index import AliasIndex
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.models import Concept
from obsidian.ontology.query_resolver import QueryResolver


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_concept(label: str, *aliases: str) -> Concept:
    return Concept.from_label(label, aliases=tuple(aliases))


def build_resolver(*concepts: Concept) -> QueryResolver:
    """Build an AliasIndex + ConceptGraph from *concepts* and wrap in a QueryResolver."""
    idx = AliasIndex()
    idx.build(list(concepts))
    graph = ConceptGraph()
    for c in concepts:
        graph.add_concept(c)
    return QueryResolver(idx, graph)


# ---------------------------------------------------------------------------
# TestEmptyQuery
# ---------------------------------------------------------------------------


class TestEmptyQuery:
    def test_empty_string_returns_empty_list(self) -> None:
        resolver = build_resolver(make_concept("Haven"))
        assert resolver.resolve("") == []

    def test_whitespace_only_returns_empty_list(self) -> None:
        resolver = build_resolver(make_concept("Haven"))
        assert resolver.resolve("   ") == []

    def test_tab_only_returns_empty_list(self) -> None:
        resolver = build_resolver(make_concept("Haven"))
        assert resolver.resolve("\t") == []

    def test_newline_only_returns_empty_list(self) -> None:
        resolver = build_resolver(make_concept("Haven"))
        assert resolver.resolve("\n") == []

    def test_empty_with_no_concepts_in_graph_returns_empty(self) -> None:
        resolver = build_resolver()
        assert resolver.resolve("") == []


# ---------------------------------------------------------------------------
# TestExactLabels
# ---------------------------------------------------------------------------


class TestExactLabels:
    def test_single_word_label_resolves(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        result = resolver.resolve("Haven")
        assert len(result) == 1
        assert result[0].id == c.id

    def test_multiword_label_resolves_as_whole_phrase(self) -> None:
        c = make_concept("Memory Engine")
        resolver = build_resolver(c)
        result = resolver.resolve("Memory Engine")
        assert len(result) == 1
        assert result[0].id == c.id

    def test_three_word_label_resolves(self) -> None:
        c = make_concept("Technical University Denmark")
        resolver = build_resolver(c)
        result = resolver.resolve("Technical University Denmark")
        assert len(result) == 1
        assert result[0].id == c.id

    def test_unknown_label_returns_empty(self) -> None:
        resolver = build_resolver(make_concept("Haven"))
        assert resolver.resolve("Claude") == []

    def test_returned_concept_has_correct_label(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        result = resolver.resolve("Haven")
        assert result[0].label == "Haven"

    def test_token_matches_single_word_label_in_longer_query(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        result = resolver.resolve("tell me about Haven please")
        assert len(result) == 1
        assert result[0].id == c.id


# ---------------------------------------------------------------------------
# TestAliases
# ---------------------------------------------------------------------------


class TestAliases:
    def test_single_word_alias_resolves(self) -> None:
        c = make_concept("Haven", "Brain")
        resolver = build_resolver(c)
        result = resolver.resolve("Brain")
        assert len(result) == 1
        assert result[0].id == c.id

    def test_multiword_alias_resolves_as_whole_phrase(self) -> None:
        c = make_concept("Haven", "Personal Brain")
        resolver = build_resolver(c)
        result = resolver.resolve("Personal Brain")
        assert len(result) == 1
        assert result[0].id == c.id

    def test_alias_and_label_map_to_same_concept(self) -> None:
        c = make_concept("Haven", "Second Brain")
        resolver = build_resolver(c)
        by_label = resolver.resolve("Haven")
        by_alias = resolver.resolve("Second Brain")
        assert len(by_label) == 1
        assert len(by_alias) == 1
        assert by_label[0].id == by_alias[0].id

    def test_multiple_aliases_all_resolve(self) -> None:
        c = make_concept("Claude", "Claude AI", "Anthropic Claude")
        resolver = build_resolver(c)
        for surface in ("Claude", "Claude AI", "Anthropic Claude"):
            result = resolver.resolve(surface)
            assert len(result) == 1
            assert result[0].id == c.id

    def test_alias_token_resolves_in_longer_query(self) -> None:
        c = make_concept("Claude", "Sonnet")
        resolver = build_resolver(c)
        result = resolver.resolve("I asked Sonnet yesterday")
        assert len(result) == 1
        assert result[0].id == c.id


# ---------------------------------------------------------------------------
# TestMultipleConcepts
# ---------------------------------------------------------------------------


class TestMultipleConcepts:
    def test_two_single_word_concepts_resolved(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        resolver = build_resolver(c1, c2)
        result = resolver.resolve("Haven Claude")
        ids = {c.id for c in result}
        assert c1.id in ids
        assert c2.id in ids

    def test_three_concepts_resolved(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        c3 = make_concept("DTU")
        resolver = build_resolver(c1, c2, c3)
        result = resolver.resolve("Haven Claude DTU")
        ids = {c.id for c in result}
        assert {c1.id, c2.id, c3.id} == ids

    def test_order_follows_first_token_occurrence(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        resolver = build_resolver(c1, c2)
        result = resolver.resolve("Haven Claude")
        assert result[0].id == c1.id
        assert result[1].id == c2.id

    def test_reversed_query_reverses_order(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        resolver = build_resolver(c1, c2)
        result = resolver.resolve("Claude Haven")
        assert result[0].id == c2.id
        assert result[1].id == c1.id

    def test_mixed_label_and_alias_resolved(self) -> None:
        c1 = make_concept("Haven", "Brain")
        c2 = make_concept("Claude")
        resolver = build_resolver(c1, c2)
        result = resolver.resolve("Brain and Claude")
        ids = {c.id for c in result}
        assert c1.id in ids
        assert c2.id in ids


# ---------------------------------------------------------------------------
# TestDuplicateMentions
# ---------------------------------------------------------------------------


class TestDuplicateMentions:
    def test_same_token_twice_yields_one_concept(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        result = resolver.resolve("Haven Haven")
        assert len(result) == 1

    def test_same_token_three_times_yields_one_concept(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        result = resolver.resolve("Haven Haven Haven")
        assert len(result) == 1

    def test_label_and_alias_in_same_query_yields_one_concept(self) -> None:
        c = make_concept("Haven", "Brain")
        resolver = build_resolver(c)
        result = resolver.resolve("Haven Brain")
        assert len(result) == 1
        assert result[0].id == c.id

    def test_whole_phrase_and_token_for_same_concept_yields_one(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        # whole-phrase matches "haven", token pass also produces "haven"
        result = resolver.resolve("Haven")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# TestNormalizationCase
# ---------------------------------------------------------------------------


class TestNormalizationCase:
    def test_all_lowercase_query_resolves(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        assert resolver.resolve("haven")[0].id == c.id

    def test_all_uppercase_query_resolves(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        assert resolver.resolve("HAVEN")[0].id == c.id

    def test_mixed_case_query_resolves(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        assert resolver.resolve("HaVeN")[0].id == c.id

    def test_leading_trailing_whitespace_stripped(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        assert resolver.resolve("  Haven  ")[0].id == c.id

    def test_multiword_label_lowercase_query(self) -> None:
        c = make_concept("Memory Engine")
        resolver = build_resolver(c)
        assert resolver.resolve("memory engine")[0].id == c.id

    def test_multiword_label_uppercase_query(self) -> None:
        c = make_concept("Memory Engine")
        resolver = build_resolver(c)
        assert resolver.resolve("MEMORY ENGINE")[0].id == c.id


# ---------------------------------------------------------------------------
# TestPunctuation
# ---------------------------------------------------------------------------


class TestPunctuation:
    def test_comma_separated_concepts(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        resolver = build_resolver(c1, c2)
        result = resolver.resolve("Haven, Claude")
        ids = {c.id for c in result}
        assert c1.id in ids
        assert c2.id in ids

    def test_exclamation_stripped(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        assert resolver.resolve("Haven!")[0].id == c.id

    def test_question_mark_stripped(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        assert resolver.resolve("Haven?")[0].id == c.id

    def test_parentheses_stripped(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        assert resolver.resolve("(Haven)")[0].id == c.id

    def test_mixed_punctuation_multiple_concepts(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        resolver = build_resolver(c1, c2)
        result = resolver.resolve("Haven; Claude!")
        ids = {c.id for c in result}
        assert c1.id in ids
        assert c2.id in ids

    def test_hyphens_within_label_preserved(self) -> None:
        # tokenize strips hyphens, but whole-phrase normalize preserves them
        # so "GPT-4" stored as "gpt-4" in the index; token "gpt" and "4" separately
        c = make_concept("GPT-4")
        resolver = build_resolver(c)
        # whole-phrase "gpt-4" matches the concept
        result = resolver.resolve("GPT-4")
        assert len(result) == 1
        assert result[0].id == c.id


# ---------------------------------------------------------------------------
# TestUnknownConcepts
# ---------------------------------------------------------------------------


class TestUnknownConcepts:
    def test_completely_unknown_query_returns_empty(self) -> None:
        resolver = build_resolver(make_concept("Haven"))
        assert resolver.resolve("xyzzy") == []

    def test_partial_unknown_returns_only_known(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        result = resolver.resolve("Haven xyzzy")
        assert len(result) == 1
        assert result[0].id == c.id

    def test_stop_words_only_returns_empty(self) -> None:
        resolver = build_resolver(make_concept("Haven"))
        assert resolver.resolve("what is the") == []

    def test_empty_graph_returns_empty(self) -> None:
        resolver = build_resolver()
        assert resolver.resolve("Haven") == []


# ---------------------------------------------------------------------------
# TestDeterministicOrder
# ---------------------------------------------------------------------------


class TestDeterministicOrder:
    def test_same_query_same_result_repeated_calls(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        resolver = build_resolver(c1, c2)
        for _ in range(10):
            result = resolver.resolve("Haven Claude")
            assert [c.id for c in result] == [c1.id, c2.id]

    def test_different_resolvers_same_index_same_result(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        r1 = build_resolver(c1, c2)
        r2 = build_resolver(c1, c2)
        assert [c.id for c in r1.resolve("Haven Claude")] == [
            c.id for c in r2.resolve("Haven Claude")
        ]

    def test_order_is_stable_with_three_concepts(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        c3 = make_concept("DTU")
        resolver = build_resolver(c1, c2, c3)
        result = resolver.resolve("Haven Claude DTU")
        assert result[0].id == c1.id
        assert result[1].id == c2.id
        assert result[2].id == c3.id


# ---------------------------------------------------------------------------
# TestUnicode
# ---------------------------------------------------------------------------


class TestUnicode:
    def test_latin_extended_label(self) -> None:
        c = make_concept("École")
        resolver = build_resolver(c)
        assert resolver.resolve("École")[0].id == c.id

    def test_latin_extended_lowercase_query(self) -> None:
        c = make_concept("École")
        resolver = build_resolver(c)
        assert resolver.resolve("école")[0].id == c.id

    def test_umlaut_label(self) -> None:
        c = make_concept("Über System")
        resolver = build_resolver(c)
        assert resolver.resolve("Über System")[0].id == c.id

    def test_unicode_alias_resolves(self) -> None:
        c = make_concept("School", "École")
        resolver = build_resolver(c)
        assert resolver.resolve("École")[0].id == c.id

    def test_unicode_alias_lowercase_query(self) -> None:
        c = make_concept("School", "École")
        resolver = build_resolver(c)
        assert resolver.resolve("école")[0].id == c.id

    def test_cjk_label_resolves(self) -> None:
        c = make_concept("人工知能")
        resolver = build_resolver(c)
        assert resolver.resolve("人工知能")[0].id == c.id

    def test_mixed_unicode_concepts_each_resolves_individually(self) -> None:
        # tokenize() uses [a-z0-9]+ so non-ASCII chars become word boundaries;
        # "École" in a compound query produces token "cole", not "école".
        # Each concept resolves correctly when queried in isolation.
        c1 = make_concept("Haven")
        c2 = make_concept("École")
        resolver = build_resolver(c1, c2)
        assert resolver.resolve("Haven")[0].id == c1.id
        assert resolver.resolve("École")[0].id == c2.id


# ---------------------------------------------------------------------------
# TestGraphSkip
# ---------------------------------------------------------------------------


class TestGraphSkip:
    def test_concept_in_index_but_not_in_graph_is_skipped(self) -> None:
        c = make_concept("Haven")
        # Build index with concept but leave graph empty
        idx = AliasIndex()
        idx.build([c])
        graph = ConceptGraph()  # c is NOT added to graph
        resolver = QueryResolver(idx, graph)
        assert resolver.resolve("Haven") == []

    def test_one_in_graph_one_not_returns_only_registered(self) -> None:
        c1 = make_concept("Haven")
        c2 = make_concept("Claude")
        idx = AliasIndex()
        idx.build([c1, c2])
        graph = ConceptGraph()
        graph.add_concept(c1)  # only c1 in graph, c2 absent
        resolver = QueryResolver(idx, graph)
        result = resolver.resolve("Haven Claude")
        assert len(result) == 1
        assert result[0].id == c1.id


# ---------------------------------------------------------------------------
# TestStopWords
# ---------------------------------------------------------------------------


class TestStopWords:
    def test_stop_words_do_not_block_resolution(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        result = resolver.resolve("what is Haven")
        assert len(result) == 1
        assert result[0].id == c.id

    def test_query_with_many_stop_words_resolves(self) -> None:
        c = make_concept("Haven")
        resolver = build_resolver(c)
        result = resolver.resolve("can you tell me about Haven please")
        assert len(result) == 1
        assert result[0].id == c.id

    def test_only_stop_words_returns_empty(self) -> None:
        resolver = build_resolver(make_concept("Haven"))
        assert resolver.resolve("what is the") == []

    def test_concept_named_same_as_stop_word_not_resolved_by_token(self) -> None:
        # "the" is a stop word, so a concept labelled "The" cannot be found
        # via token-level lookup (tokenize_query strips it).  The whole-phrase
        # pass also won't match because "the" is the entire query and
        # tokenize_query would strip it, but whole-phrase lookup ("the")
        # WOULD match the label.  We verify this edge case explicitly.
        c = make_concept("The")
        resolver = build_resolver(c)
        # whole-phrase lookup("the") → matches the concept
        result = resolver.resolve("The")
        assert len(result) == 1
        assert result[0].id == c.id
