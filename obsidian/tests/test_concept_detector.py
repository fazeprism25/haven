"""Unit tests for obsidian.ontology.concept_detector – Phase 2D.

Tests are grouped by concern:

* :class:`TestConceptDetectorInstantiation` — construction and public API surface.
* :class:`TestDetectFromTextBasic` — single/multiple concepts, empty inputs.
* :class:`TestDetectFromTextMultiWord` — multi-word span grouping.
* :class:`TestDetectFromTextFiltering` — stop-word trimming and validity filters.
* :class:`TestDetectFromTextDeduplication` — case-insensitive dedup.
* :class:`TestDetectFromTextOrdering` — first-seen order guarantee.
* :class:`TestDetectFromTextEdgeCases` — punctuation, abbreviations, hyphens.
* :class:`TestDetectFromKnowledgeObject` — integration with KnowledgeObject.
* :class:`TestDetectorDeterminism` — identical inputs → identical output.
* :class:`TestPrivateHelpers` — unit coverage of module-level helpers.

All tests are deterministic.  No fixed UUIDs or timestamps are required
because :class:`ConceptDetector` is stateless and operates only on text.
"""

from __future__ import annotations

import pytest

from obsidian.manager_ai.models import KnowledgeObject
from obsidian.ontology.concept_detector import (
    MIN_LABEL_CHARS,
    ConceptDetector,
    _clean_word,
    _deduplicate,
    _extract_capitalized_spans,
    _is_head_capital,
    _is_valid_label,
    _trim_stop_words,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def detector() -> ConceptDetector:
    return ConceptDetector()


@pytest.fixture
def ko_haven() -> KnowledgeObject:
    return KnowledgeObject(canonical_fact="Siddhartha uses Haven for personal knowledge management")


@pytest.fixture
def ko_empty() -> KnowledgeObject:
    return KnowledgeObject(canonical_fact="")


@pytest.fixture
def ko_lowercase() -> KnowledgeObject:
    return KnowledgeObject(canonical_fact="the quick brown fox jumps over the lazy dog")


# ---------------------------------------------------------------------------
# TestConceptDetectorInstantiation
# ---------------------------------------------------------------------------


class TestConceptDetectorInstantiation:
    def test_instantiates_without_arguments(self) -> None:
        d = ConceptDetector()
        assert isinstance(d, ConceptDetector)

    def test_has_detect_method(self) -> None:
        assert callable(ConceptDetector().detect)

    def test_has_detect_from_text_method(self) -> None:
        assert callable(ConceptDetector().detect_from_text)

    def test_has_min_label_chars_attribute(self) -> None:
        assert hasattr(ConceptDetector, "MIN_LABEL_CHARS")

    def test_min_label_chars_is_at_least_two(self) -> None:
        assert ConceptDetector.MIN_LABEL_CHARS >= 2

    def test_min_label_chars_is_int(self) -> None:
        assert isinstance(ConceptDetector.MIN_LABEL_CHARS, int)

    def test_no_graph_attribute(self) -> None:
        assert not hasattr(ConceptDetector(), "graph")

    def test_no_llm_attribute(self) -> None:
        assert not hasattr(ConceptDetector(), "llm")

    def test_no_vault_attribute(self) -> None:
        assert not hasattr(ConceptDetector(), "vault")


# ---------------------------------------------------------------------------
# TestDetectFromTextBasic
# ---------------------------------------------------------------------------


class TestDetectFromTextBasic:
    def test_returns_list(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Haven is a project")
        assert isinstance(result, list)

    def test_single_concept(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Haven is a project")
        assert "Haven" in result

    def test_two_concepts(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Siddhartha uses Haven daily")
        assert "Siddhartha" in result
        assert "Haven" in result

    def test_multiple_concepts(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Haven uses Claude and Qdrant")
        assert len(result) == 3

    def test_empty_string(self, detector: ConceptDetector) -> None:
        assert detector.detect_from_text("") == []

    def test_whitespace_only(self, detector: ConceptDetector) -> None:
        assert detector.detect_from_text("   ") == []
        assert detector.detect_from_text("\t\n") == []

    def test_all_lowercase(self, detector: ConceptDetector) -> None:
        assert detector.detect_from_text("the quick brown fox") == []

    def test_no_concepts_in_sentence(self, detector: ConceptDetector) -> None:
        assert detector.detect_from_text("it is very fast and efficient") == []

    def test_result_elements_are_strings(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Haven is useful")
        for item in result:
            assert isinstance(item, str)

    def test_single_word_at_start_not_stop_word(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text("Python is a programming language")
        assert "Python" in result


# ---------------------------------------------------------------------------
# TestDetectFromTextMultiWord
# ---------------------------------------------------------------------------


class TestDetectFromTextMultiWord:
    def test_two_word_span(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Memory Engine is core")
        assert "Memory Engine" in result

    def test_three_word_span(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("New York City is large")
        assert "New York City" in result

    def test_multi_word_span_is_single_label(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text("Memory Engine is core")
        assert "Memory" not in result
        assert "Engine" not in result
        assert "Memory Engine" in result

    def test_lowercase_word_breaks_span(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Claude and Haven are tools")
        assert "Claude" in result
        assert "Haven" in result
        assert "Claude and Haven" not in result

    def test_two_separate_spans(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Memory Engine uses Claude for inference")
        assert "Memory Engine" in result
        assert "Claude" in result

    def test_multi_word_concept_adjacent_lowercase(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text(
            "The Memory Engine is part of Haven"
        )
        assert "Memory Engine" in result
        assert "Haven" in result


# ---------------------------------------------------------------------------
# TestDetectFromTextFiltering
# ---------------------------------------------------------------------------


class TestDetectFromTextFiltering:
    def test_leading_the_trimmed(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("The Memory Engine is running")
        assert "Memory Engine" in result
        assert "The Memory Engine" not in result

    def test_leading_a_trimmed(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("A Haven for knowledge")
        assert "Haven" in result
        assert "A Haven" not in result

    def test_pronoun_i_filtered(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("I use Haven every day")
        assert "Haven" in result
        assert "I" not in result

    def test_stop_word_only_span_removed(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("It Is A very fast system")
        assert result == [] or all(
            label not in ("It", "Is", "A") for label in result
        )

    def test_purely_numeric_token_after_clean_skipped(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text("some fact about 42 things")
        assert result == []

    def test_single_char_label_filtered(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("X is unknown")
        assert "X" not in result

    def test_valid_two_char_abbreviation_kept(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text("AI is transforming everything")
        assert "AI" in result

    def test_empty_after_trimming_not_returned(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text("The Is A and or but")
        assert result == []


# ---------------------------------------------------------------------------
# TestContractionFiltering
# ---------------------------------------------------------------------------
#
# A solo capitalized contraction of a stop word (e.g. "I'm", "It's") is not
# a concept — the capitalized-span heuristic has no notion of contractions
# (see the module docstring's "Limitations"), so "I'm building Haven" used
# to detect a bogus "I'm" concept alongside the real "Haven" one. See
# obsidian.ontology.text_utils.strip_clitic.


class TestContractionFiltering:
    def test_im_contraction_filtered(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("I'm building Haven, a personal second-brain system.")
        assert "Haven" in result
        assert "I'm" not in result

    def test_its_contraction_filtered(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("It's Haven's turn to shine.")
        assert "It's" not in result

    def test_thats_contraction_filtered(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("That's a good idea about Haven.")
        assert "That's" not in result
        assert "Haven" in result

    def test_were_contraction_filtered(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("We're shipping Haven soon.")
        assert "We're" not in result
        assert "Haven" in result

    def test_possessive_on_real_concept_not_dropped(
        self, detector: ConceptDetector
    ) -> None:
        # "Haven's" is a possessive on a real entity, not a stop-word
        # contraction — strip_clitic("Haven's") -> "Haven", which is not in
        # STOP_WORDS, so the whole token must survive trimming.
        result = detector.detect_from_text("Haven's retrieval pipeline is deterministic.")
        assert any(label.startswith("Haven") for label in result)

    def test_odd_apostrophe_name_not_mistaken_for_contraction(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text("O'Brien reviewed the design.")
        assert "O'Brien" in result


# ---------------------------------------------------------------------------
# TestDetectFromTextDeduplication
# ---------------------------------------------------------------------------


class TestDetectFromTextDeduplication:
    def test_same_label_twice_appears_once(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text("Haven and Haven again")
        assert result.count("Haven") == 1

    def test_different_cases_collapse_to_first(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text("Haven uses HAVEN for storage")
        assert len(result) == 1

    def test_first_occurrence_casing_preserved(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text("Haven uses HAVEN for storage")
        assert result[0] == "Haven"

    def test_three_occurrences_yields_one(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text(
            "Claude is great and Claude is fast and Claude is smart"
        )
        assert result.count("Claude") == 1

    def test_distinct_labels_not_collapsed(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text("Haven and Claude are different")
        assert len(result) == 2

    def test_dedup_across_spans(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Memory Engine uses Memory Engine")
        assert result.count("Memory Engine") == 1


# ---------------------------------------------------------------------------
# TestDetectFromTextOrdering
# ---------------------------------------------------------------------------


class TestDetectFromTextOrdering:
    def test_first_seen_concept_comes_first(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text("Claude and Haven work together")
        assert result.index("Claude") < result.index("Haven")

    def test_order_reflects_text_position(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text(
            "Siddhartha built Haven using Claude and Qdrant"
        )
        assert result == ["Siddhartha", "Haven", "Claude", "Qdrant"]

    def test_order_not_alphabetical(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Qdrant then Claude")
        assert result[0] == "Qdrant"
        assert result[1] == "Claude"

    def test_multiword_span_order_respected(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text(
            "Memory Engine is powered by Claude"
        )
        assert result.index("Memory Engine") < result.index("Claude")

    def test_dedup_keeps_first_not_last(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text(
            "Claude is used and then CLAUDE again"
        )
        assert result[0] == "Claude"


# ---------------------------------------------------------------------------
# TestDetectFromTextEdgeCases
# ---------------------------------------------------------------------------


class TestDetectFromTextEdgeCases:
    def test_trailing_period_stripped(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("I use Haven.")
        assert "Haven" in result

    def test_trailing_comma_stripped(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Claude, Haven, and Qdrant")
        assert "Claude" in result
        assert "Haven" in result
        assert "Qdrant" in result

    def test_parentheses_stripped(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("The tool (Haven) is useful")
        assert "Haven" in result

    def test_all_caps_abbreviation_captured(
        self, detector: ConceptDetector
    ) -> None:
        result = detector.detect_from_text("LLM is used for inference")
        assert "LLM" in result

    def test_three_letter_abbreviation(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("DTU is located in Copenhagen")
        assert "DTU" in result
        assert "Copenhagen" in result

    def test_hyphenated_token_captured(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Claude-3 is a powerful model")
        assert "Claude-3" in result

    def test_token_with_digit_captured(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("GPT-4 is an LLM")
        assert "GPT-4" in result
        assert "LLM" in result

    def test_multiple_punctuation_marks(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Haven: the best tool!")
        assert "Haven" in result

    def test_single_concept_no_context(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("Haven")
        assert result == ["Haven"]

    def test_concept_at_very_end(self, detector: ConceptDetector) -> None:
        result = detector.detect_from_text("the personal tool is Haven")
        assert "Haven" in result


# ---------------------------------------------------------------------------
# TestDetectFromKnowledgeObject
# ---------------------------------------------------------------------------


class TestDetectFromKnowledgeObject:
    def test_returns_list(
        self, detector: ConceptDetector, ko_haven: KnowledgeObject
    ) -> None:
        result = detector.detect(ko_haven)
        assert isinstance(result, list)

    def test_detects_concepts_from_knowledge_object(
        self, detector: ConceptDetector, ko_haven: KnowledgeObject
    ) -> None:
        result = detector.detect(ko_haven)
        assert "Siddhartha" in result
        assert "Haven" in result

    def test_empty_canonical_fact_returns_empty(
        self, detector: ConceptDetector, ko_empty: KnowledgeObject
    ) -> None:
        assert detector.detect(ko_empty) == []

    def test_all_lowercase_fact_returns_empty(
        self, detector: ConceptDetector, ko_lowercase: KnowledgeObject
    ) -> None:
        assert detector.detect(ko_lowercase) == []

    def test_detect_matches_detect_from_text(
        self, detector: ConceptDetector, ko_haven: KnowledgeObject
    ) -> None:
        via_detect = detector.detect(ko_haven)
        via_text = detector.detect_from_text(ko_haven.canonical_fact)
        assert via_detect == via_text

    def test_knowledge_object_with_abbreviation(
        self, detector: ConceptDetector
    ) -> None:
        # "Claude" and "API" are adjacent capitalized words separated only by
        # lowercase words before them, so they form the single span "Claude API".
        ko = KnowledgeObject(canonical_fact="Haven uses an LLM via the Claude API")
        result = detector.detect(ko)
        assert "Haven" in result
        assert "LLM" in result
        assert "Claude API" in result

    def test_knowledge_object_with_multi_word_concept(
        self, detector: ConceptDetector
    ) -> None:
        ko = KnowledgeObject(
            canonical_fact="The Memory Engine retrieves relevant KnowledgeObjects"
        )
        result = detector.detect(ko)
        assert "Memory Engine" in result

    def test_knowledge_object_with_no_concepts(
        self, detector: ConceptDetector
    ) -> None:
        ko = KnowledgeObject(
            canonical_fact="this fact contains no proper nouns at all"
        )
        assert detector.detect(ko) == []

    def test_detect_does_not_modify_knowledge_object(
        self, detector: ConceptDetector, ko_haven: KnowledgeObject
    ) -> None:
        original_fact = ko_haven.canonical_fact
        detector.detect(ko_haven)
        assert ko_haven.canonical_fact == original_fact


# ---------------------------------------------------------------------------
# TestDetectorDeterminism
# ---------------------------------------------------------------------------


class TestDetectorDeterminism:
    def test_same_text_same_result(self, detector: ConceptDetector) -> None:
        text = "Siddhartha builds Haven using Claude and Qdrant"
        r1 = detector.detect_from_text(text)
        r2 = detector.detect_from_text(text)
        assert r1 == r2

    def test_same_knowledge_object_same_result(
        self, detector: ConceptDetector, ko_haven: KnowledgeObject
    ) -> None:
        r1 = detector.detect(ko_haven)
        r2 = detector.detect(ko_haven)
        assert r1 == r2

    def test_different_text_different_result(
        self, detector: ConceptDetector
    ) -> None:
        r1 = detector.detect_from_text("Haven is great")
        r2 = detector.detect_from_text("Claude is fast")
        assert r1 != r2

    def test_fresh_instance_same_result(self) -> None:
        text = "Haven uses Claude for reasoning"
        r1 = ConceptDetector().detect_from_text(text)
        r2 = ConceptDetector().detect_from_text(text)
        assert r1 == r2

    def test_multiple_calls_stable_ordering(
        self, detector: ConceptDetector
    ) -> None:
        # Lowercase words between concepts ensure three separate spans.
        text = "Claude, Qdrant, and Haven"
        for _ in range(5):
            result = detector.detect_from_text(text)
            assert result == ["Claude", "Qdrant", "Haven"]


# ---------------------------------------------------------------------------
# TestPrivateHelpers
# ---------------------------------------------------------------------------


class TestPrivateHelpers:
    # _clean_word

    def test_clean_word_strips_trailing_period(self) -> None:
        assert _clean_word("Haven.") == "Haven"

    def test_clean_word_strips_trailing_comma(self) -> None:
        assert _clean_word("Haven,") == "Haven"

    def test_clean_word_strips_parentheses(self) -> None:
        assert _clean_word("(Haven)") == "Haven"

    def test_clean_word_preserves_hyphen(self) -> None:
        assert _clean_word("Claude-3") == "Claude-3"

    def test_clean_word_preserves_internal_digits(self) -> None:
        assert _clean_word("GPT-4.") == "GPT-4"

    def test_clean_word_empty_input(self) -> None:
        assert _clean_word("") == ""

    def test_clean_word_all_punct(self) -> None:
        assert _clean_word("...") == ""

    # _is_head_capital

    def test_is_head_capital_uppercase(self) -> None:
        assert _is_head_capital("Haven") is True

    def test_is_head_capital_all_caps(self) -> None:
        assert _is_head_capital("LLM") is True

    def test_is_head_capital_lowercase(self) -> None:
        assert _is_head_capital("haven") is False

    def test_is_head_capital_digit_start(self) -> None:
        assert _is_head_capital("3D") is False

    def test_is_head_capital_empty(self) -> None:
        assert _is_head_capital("") is False

    # _extract_capitalized_spans

    def test_extract_single_span(self) -> None:
        assert _extract_capitalized_spans("Haven is great") == ["Haven"]

    def test_extract_multi_word_span(self) -> None:
        assert _extract_capitalized_spans("Memory Engine is fast") == [
            "Memory Engine"
        ]

    def test_extract_two_spans(self) -> None:
        spans = _extract_capitalized_spans("Claude powers Haven")
        assert spans == ["Claude", "Haven"]

    def test_extract_empty_string(self) -> None:
        assert _extract_capitalized_spans("") == []

    def test_extract_no_capitals(self) -> None:
        assert _extract_capitalized_spans("all lower case") == []

    # _trim_stop_words

    def test_trim_leading_the(self) -> None:
        assert _trim_stop_words("The Memory Engine") == "Memory Engine"

    def test_trim_leading_a(self) -> None:
        assert _trim_stop_words("A Haven") == "Haven"

    def test_trim_single_stop_word(self) -> None:
        assert _trim_stop_words("I") == ""

    def test_trim_no_stop_words(self) -> None:
        assert _trim_stop_words("Haven") == "Haven"

    def test_trim_preserves_middle(self) -> None:
        assert _trim_stop_words("New York") == "New York"

    def test_trim_im_contraction(self) -> None:
        assert _trim_stop_words("I'm") == ""

    def test_trim_its_contraction(self) -> None:
        assert _trim_stop_words("It's") == ""

    def test_trim_possessive_on_real_name_preserved(self) -> None:
        assert _trim_stop_words("Haven's") == "Haven's"

    def test_trim_odd_apostrophe_name_preserved(self) -> None:
        assert _trim_stop_words("O'Brien") == "O'Brien"

    # _is_valid_label

    def test_valid_normal_label(self) -> None:
        assert _is_valid_label("Haven") is True

    def test_valid_abbreviation(self) -> None:
        assert _is_valid_label("AI") is True

    def test_invalid_single_char(self) -> None:
        assert _is_valid_label("X") is False

    def test_invalid_all_digits(self) -> None:
        assert _is_valid_label("42") is False

    def test_invalid_empty(self) -> None:
        assert _is_valid_label("") is False

    # _deduplicate

    def test_dedup_removes_duplicate(self) -> None:
        assert _deduplicate(["Haven", "Haven"]) == ["Haven"]

    def test_dedup_case_insensitive(self) -> None:
        result = _deduplicate(["Haven", "HAVEN"])
        assert result == ["Haven"]

    def test_dedup_preserves_order(self) -> None:
        assert _deduplicate(["Claude", "Haven", "Qdrant"]) == [
            "Claude",
            "Haven",
            "Qdrant",
        ]

    def test_dedup_empty_list(self) -> None:
        assert _deduplicate([]) == []

    def test_dedup_keeps_first_casing(self) -> None:
        result = _deduplicate(["HAVEN", "Haven"])
        assert result == ["HAVEN"]
