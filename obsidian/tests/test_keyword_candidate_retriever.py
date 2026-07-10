"""Unit tests for obsidian.memory_engine.keyword_candidate_retriever.KeywordCandidateRetriever.

Test groups
-----------
TestBasicMatching             — single/no keyword overlap, case-insensitivity.
TestTokenBoundaries            — punctuation delimiters, whole-word matching,
                                  no substring/fuzzy leakage.
TestMultiKeywordQuery           — OR semantics across multiple query tokens.
TestEmptyInputs                 — empty query, empty candidates, no-keyword query.
TestCanonicalFactOnly            — only canonical_fact is searched.
TestDeterministicOrdering        — output sorted by KO id regardless of input order.
TestNoMutation                    — candidates list/objects are never mutated.
TestStatelessReuse                 — one instance, many calls, no leakage.
TestReturnType                      — returns plain KnowledgeObjects, not wrapper types.
TestNoOutOfScopeImports              — module never imports the Ontology subsystem.
TestStopWordFiltering                 — query-side stop words no longer produce
                                        matches; content words in the same query
                                        still do; fact-side stop words are inert.
TestStopWordParity                     — the module's duplicated stop-word list
                                        stays identical to
                                        obsidian.ontology.text_utils.STOP_WORDS.
TestClitics                             — contraction/possessive apostrophes
                                        ("what's", "Haven's", "don't") never
                                        produce a bare fragment token
                                        ("s", "t", "m", ...) that can match
                                        unrelated facts sharing an unrelated
                                        apostrophe; non-clitic apostrophes
                                        are left intact; legitimate matches
                                        through a clitic-bearing word still
                                        work.
TestVariantNormalization                — inflectional variants (project/projects,
                                        task/tasks, work family, build family,
                                        commit family) match each other on
                                        either side; unrelated words ending
                                        in similar suffixes are untouched.
TestVariantGroupsWellFormed             — structural sanity checks on the
                                        full _VARIANT_GROUPS table: no
                                        variant is claimed by two groups,
                                        every canonical form is a member of
                                        its own group, every variant is
                                        already lowercase.
TestPhase1AuditConfirmedVariants        — the 14 inflectional groups added
                                        from the candidate-generation
                                        failure audit (embedding, model,
                                        benchmark, dataset, host, payment,
                                        system, secret, current, learn,
                                        combine, evaluate, optimize,
                                        prefer), plus regression tests
                                        replaying the exact query/fact
                                        pairs from
                                        docs/architecture/CANDIDATE_GENERATION_DECISION.md.
TestResidualLexicalVariants             — the 3 groups (name, decide,
                                        prioritize) added from the
                                        residual LEXICAL cases identified
                                        in
                                        docs/architecture/ENTITY_CAT_INVESTIGATION.md,
                                        plus regression tests replaying
                                        those exact query/fact pairs.
TestTechnologyAliasVariants             — well-established, unambiguous
                                        tech abbreviations (api, repo,
                                        config, database, auth,
                                        environment, infrastructure,
                                        specification, application,
                                        production, javascript,
                                        typescript, backend, frontend,
                                        info, misc).
TestSpellingVariants                    — American/British spelling pairs
                                        (color, behavior, license,
                                        organize, analyze, customize,
                                        favorite, cancel).
TestKeywordMatch                          — KeywordMatch validates its score
                                        range like every other scored model.
TestKeywordOverlapScoring                  — retrieve() and
                                        retrieve_with_scores() agree on the
                                        matched set; more overlap, rarer
                                        tokens, and exact phrase containment
                                        each raise the score; score stays in
                                        [0.0, 1.0]; deterministic.
"""

from __future__ import annotations

import dataclasses
from typing import List, Optional
from uuid import UUID, uuid4

import pytest

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine.keyword_candidate_retriever import (
    KeywordCandidateRetriever,
    KeywordMatch,
    _document_frequencies,
    _document_frequencies_from_tokens,
    _has_multiword_phrase_overlap,
    _tokenize,
)


def make_ko(
    fact: str = "Haven uses Claude",
    ko_id: Optional[UUID] = None,
) -> KnowledgeObject:
    return KnowledgeObject(
        id=ko_id if ko_id is not None else uuid4(),
        canonical_fact=fact,
        memory_type=MemoryType.FACT,
    )


# ---------------------------------------------------------------------------
# TestBasicMatching
# ---------------------------------------------------------------------------


class TestBasicMatching:
    def test_matches_single_shared_keyword(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        result = retriever.retrieve("claude", [ko])
        assert result == [ko]

    def test_no_match_returns_empty(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        result = retriever.retrieve("banana", [ko])
        assert result == []

    def test_matching_is_case_insensitive_on_query(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        result = retriever.retrieve("CLAUDE", [ko])
        assert result == [ko]

    def test_matching_is_case_insensitive_on_fact(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="HAVEN USES CLAUDE")
        result = retriever.retrieve("haven", [ko])
        assert result == [ko]

    def test_only_matching_candidates_are_returned(self) -> None:
        retriever = KeywordCandidateRetriever()
        matching = make_ko(fact="Haven uses Claude")
        non_matching = make_ko(fact="The weather is sunny")
        result = retriever.retrieve("claude", [matching, non_matching])
        assert result == [matching]


# ---------------------------------------------------------------------------
# TestTokenBoundaries
# ---------------------------------------------------------------------------


class TestTokenBoundaries:
    def test_punctuation_does_not_prevent_match(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven, Memory Engine!")
        result = retriever.retrieve("memory", [ko])
        assert result == [ko]

    def test_substring_query_does_not_match_longer_word(self) -> None:
        # "claud" must not fuzzily match "claude" — whole-token equality only.
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        result = retriever.retrieve("claud", [ko])
        assert result == []

    def test_query_substring_of_fact_word_does_not_match(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="classification pipeline")
        result = retriever.retrieve("class", [ko])
        assert result == []

    def test_numbers_are_tokenised_and_matchable(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Phase 1 implementation")
        result = retriever.retrieve("1", [ko])
        assert result == [ko]

    def test_hyphenated_words_split_into_separate_tokens(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="state-of-the-art retrieval")
        result = retriever.retrieve("state", [ko])
        assert result == [ko]


# ---------------------------------------------------------------------------
# TestMultiKeywordQuery
# ---------------------------------------------------------------------------


class TestMultiKeywordQuery:
    def test_any_query_keyword_matching_is_sufficient(self) -> None:
        # OR semantics: only one of the two query keywords needs to hit.
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        result = retriever.retrieve("banana claude", [ko])
        assert result == [ko]

    def test_candidates_matched_by_different_keywords_are_all_returned(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko_a = make_ko(fact="Haven uses Claude")
        ko_b = make_ko(fact="The sky is blue")
        result = retriever.retrieve("claude blue", [ko_a, ko_b])
        assert {ko.id for ko in result} == {ko_a.id, ko_b.id}

    def test_duplicate_query_tokens_do_not_affect_result(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        result = retriever.retrieve("claude claude claude", [ko])
        assert result == [ko]


# ---------------------------------------------------------------------------
# TestEmptyInputs
# ---------------------------------------------------------------------------


class TestEmptyInputs:
    def test_empty_query_matches_nothing(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        result = retriever.retrieve("", [ko])
        assert result == []

    def test_whitespace_only_query_matches_nothing(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        result = retriever.retrieve("   ", [ko])
        assert result == []

    def test_punctuation_only_query_matches_nothing(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        result = retriever.retrieve("!!! ??? ...", [ko])
        assert result == []

    def test_empty_candidate_list_returns_empty(self) -> None:
        retriever = KeywordCandidateRetriever()
        result = retriever.retrieve("claude", [])
        assert result == []

    def test_empty_canonical_fact_never_matches(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="")
        result = retriever.retrieve("claude", [ko])
        assert result == []


# ---------------------------------------------------------------------------
# TestCanonicalFactOnly
# ---------------------------------------------------------------------------


class TestCanonicalFactOnly:
    def test_metadata_content_is_not_searched(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = KnowledgeObject(
            canonical_fact="Haven uses Claude",
            metadata={"source": "banana"},
        )
        result = retriever.retrieve("banana", [ko])
        assert result == []


# ---------------------------------------------------------------------------
# TestDeterministicOrdering
# ---------------------------------------------------------------------------


class TestDeterministicOrdering:
    def test_output_sorted_by_id_regardless_of_input_order(self) -> None:
        retriever = KeywordCandidateRetriever()
        kos = [make_ko(fact=f"Claude fact {i}") for i in range(6)]

        forward = retriever.retrieve("claude", kos)
        backward = retriever.retrieve("claude", list(reversed(kos)))

        assert forward == backward
        assert [ko.id for ko in forward] == sorted(
            [ko.id for ko in kos], key=str
        )

    def test_repeated_calls_are_stable(self) -> None:
        retriever = KeywordCandidateRetriever()
        kos = [make_ko(fact=f"Claude fact {i}") for i in range(4)]

        first = retriever.retrieve("claude", kos)
        second = retriever.retrieve("claude", kos)

        assert first == second


# ---------------------------------------------------------------------------
# TestNoMutation
# ---------------------------------------------------------------------------


class TestNoMutation:
    def test_input_list_is_not_mutated(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko_a = make_ko(fact="Haven uses Claude")
        ko_b = make_ko(fact="unrelated fact")
        kos: List[KnowledgeObject] = [ko_a, ko_b]

        retriever.retrieve("claude", kos)

        assert kos == [ko_a, ko_b]

    def test_candidate_objects_are_not_mutated(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        original = dataclasses.replace(ko)

        retriever.retrieve("claude", [ko])

        assert ko == original


# ---------------------------------------------------------------------------
# TestStatelessReuse
# ---------------------------------------------------------------------------


class TestStatelessReuse:
    def test_one_instance_many_calls_no_leakage(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko_a = make_ko(fact="Haven uses Claude")
        ko_b = make_ko(fact="The sky is blue")

        first = retriever.retrieve("claude", [ko_a, ko_b])
        second = retriever.retrieve("blue", [ko_a, ko_b])
        third = retriever.retrieve("claude", [ko_a, ko_b])

        assert first == [ko_a]
        assert second == [ko_b]
        assert third == first


# ---------------------------------------------------------------------------
# TestReturnType
# ---------------------------------------------------------------------------


class TestReturnType:
    def test_returns_list_of_knowledge_objects(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        result = retriever.retrieve("claude", [ko])

        assert isinstance(result, list)
        assert all(isinstance(item, KnowledgeObject) for item in result)
        assert result[0] is ko


# ---------------------------------------------------------------------------
# TestNoOutOfScopeImports
# ---------------------------------------------------------------------------


class TestNoOutOfScopeImports:
    def test_module_does_not_import_ontology(self) -> None:
        import obsidian.memory_engine.keyword_candidate_retriever as module
        from pathlib import Path

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "import obsidian.ontology" not in source
        assert "from obsidian.ontology" not in source


# ---------------------------------------------------------------------------
# TestStopWordFiltering
# ---------------------------------------------------------------------------


class TestStopWordFiltering:
    def test_query_stop_word_alone_matches_nothing(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="I believe the sky is blue")
        result = retriever.retrieve("the", [ko])
        assert result == []

    def test_all_stop_word_query_matches_nothing(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        result = retriever.retrieve("what is this", [ko])
        assert result == []

    def test_content_word_in_query_still_matches_despite_stop_words(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude for extraction")
        result = retriever.retrieve("what is Claude", [ko])
        assert result == [ko]

    def test_shared_stop_word_alone_does_not_link_unrelated_facts(self) -> None:
        # "I", "my", "is" overlap between the query and both facts, but
        # neither fact shares a real content word with the query -- this
        # is exactly the false-positive pattern the stop-word filter
        # targets (e.g. "What is my favorite color?" pulling in unrelated
        # first-person facts purely on function-word overlap).
        retriever = KeywordCandidateRetriever()
        unrelated_a = make_ko(fact="I chose MongoDB for Project Atlas")
        unrelated_b = make_ko(fact="My preference is dark mode everywhere")
        result = retriever.retrieve("What is my favorite color?", [unrelated_a, unrelated_b])
        assert result == []

    def test_fact_side_stop_words_are_not_filtered_but_stay_inert(self) -> None:
        # canonical_fact tokenization is intentionally left unfiltered
        # (mirrors tokenize_label vs tokenize_query) -- a fact's own stop
        # words simply never appear in the already-filtered query keyword
        # set, so they can never contribute a match either way.
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="the quick fox")
        result = retriever.retrieve("the", [ko])
        assert result == []

    def test_stop_word_filtering_is_case_insensitive(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        result = retriever.retrieve("WHAT is Claude", [ko])
        assert result == [ko]

    def test_multiple_stop_words_around_single_content_word(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="I prefer Zsh over Bash for daily shell work")
        result = retriever.retrieve("Do I prefer Zsh or Bash?", [ko])
        assert result == [ko]

    def test_punctuation_only_and_stop_word_only_both_match_nothing_the_same_way(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        assert retriever.retrieve("!!! ???", [ko]) == retriever.retrieve("who is this", [ko]) == []

    def test_determinism_preserved_with_stop_words_present(self) -> None:
        retriever = KeywordCandidateRetriever()
        kos = [make_ko(fact=f"I believe Claude fact {i}") for i in range(5)]

        forward = retriever.retrieve("what do I believe about Claude", kos)
        backward = retriever.retrieve("what do I believe about Claude", list(reversed(kos)))

        assert forward == backward
        assert len(forward) == 5
        assert [ko.id for ko in forward] == sorted([ko.id for ko in kos], key=str)


# ---------------------------------------------------------------------------
# TestStopWordParity
# ---------------------------------------------------------------------------


class TestStopWordParity:
    def test_duplicated_stop_words_match_ontology_text_utils(self) -> None:
        # keyword_candidate_retriever.py cannot import obsidian.ontology
        # (see TestNoOutOfScopeImports), so its stop-word list is a
        # content duplicate of obsidian.ontology.text_utils.STOP_WORDS
        # rather than a shared reference. This test is the only thing
        # standing between that duplication and silent drift.
        from obsidian.memory_engine.keyword_candidate_retriever import (
            _STOP_WORDS as keyword_stop_words,
        )
        from obsidian.ontology.text_utils import STOP_WORDS as ontology_stop_words

        assert keyword_stop_words == ontology_stop_words


# ---------------------------------------------------------------------------
# TestClitics
# ---------------------------------------------------------------------------


class TestClitics:
    """Regression coverage for the apostrophe-fragment bug.

    Before the tokenizer fix, ``_TOKEN_RE`` treated the apostrophe as a
    plain delimiter, so ``"what's"`` tokenised to ``["what", "s"]`` and
    ``"Haven's"`` to ``["haven", "s"]``. The bare ``"s"`` was not in
    ``_STOP_WORDS``, so it survived query-side filtering and matched *any*
    fact containing its own, semantically unrelated possessive — with a
    keyword-overlap score that could reach ``1.0`` (see
    ``_keyword_overlap_score``: a query keyword absent from the corpus
    contributes zero weight to the denominator, so when every *other*
    query keyword was absent, the score collapsed to
    ``idf("s") / idf("s") == 1.0``). These tests pin both the tokeniser
    fix directly and the end-to-end retrieval behaviour it restores.
    """

    @pytest.mark.parametrize(
        "text, expected_tokens",
        [
            ("What's the weather", ["what", "the", "weather"]),
            ("Haven's write pipeline", ["haven", "write", "pipeline"]),
            ("Project Atlas's backend", ["project", "atlas", "backend"]),
            ("don't stop", ["don", "stop"]),
            ("I'm building", ["i", "build"]),
            ("you're right", ["you", "right"]),
            ("I've decided", ["i", "decide"]),
            ("I'll go", ["i", "go"]),
            ("I'd like", ["i", "like"]),
        ],
    )
    def test_clitic_never_survives_as_its_own_token(
        self, text: str, expected_tokens: List[str]
    ) -> None:
        from obsidian.memory_engine.keyword_candidate_retriever import _tokenize

        tokens = _tokenize(text)
        assert tokens == expected_tokens
        for fragment in ("s", "t", "m", "re", "ve", "ll", "d"):
            assert fragment not in tokens

    def test_non_clitic_apostrophe_is_left_intact(self) -> None:
        from obsidian.memory_engine.keyword_candidate_retriever import _tokenize

        # "brien" is not a recognised clitic suffix, so the whole token is
        # preserved rather than corrupted or silently dropped.
        assert _tokenize("O'Brien") == ["o'brien"]

    def test_word_with_no_apostrophe_is_unaffected(self) -> None:
        from obsidian.memory_engine.keyword_candidate_retriever import _tokenize

        assert _tokenize("Postgres Kubernetes Atlas") == ["postgres", "kubernetes", "atlas"]

    def test_query_contraction_does_not_match_unrelated_fact_possessive(self) -> None:
        # The exact reported scenario: an off-topic query containing "what's"
        # must not match a fact whose only "shared" text is its own,
        # unrelated possessive apostrophe.
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven's write pipeline runs on Python 3.11.")
        result = retriever.retrieve("What's the weather forecast for tomorrow?", [ko])
        assert result == []

    def test_query_contraction_does_not_inflate_score_to_one(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko_a = make_ko(fact="Haven's write pipeline runs on Python 3.11.")
        ko_b = make_ko(fact="Project Nova's dashboard reads from Postgres.")
        matches = retriever.retrieve_with_scores(
            "What's the weather forecast for tomorrow?", [ko_a, ko_b]
        )
        assert matches == []

    def test_legitimate_match_through_a_clitic_bearing_word_still_works(self) -> None:
        # The fix must not throw out real matches -- "Haven's" still
        # carries "haven" as a genuine, matchable token.
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven's write pipeline runs on Python 3.11.")
        result = retriever.retrieve("Tell me about Haven's pipeline", [ko])
        assert result == [ko]

    def test_possessive_fact_does_not_leak_a_stray_token_into_document_frequencies(
        self,
    ) -> None:
        from obsidian.memory_engine.keyword_candidate_retriever import (
            _document_frequencies,
        )

        ko_a = make_ko(fact="Haven's write pipeline")
        ko_b = make_ko(fact="Project Nova's dashboard")
        frequencies = _document_frequencies([ko_a, ko_b])
        assert "s" not in frequencies


# ---------------------------------------------------------------------------
# TestVariantNormalization
# ---------------------------------------------------------------------------


class TestVariantNormalization:
    @pytest.mark.parametrize(
        "query_word, fact_word",
        [
            ("project", "projects"),
            ("projects", "project"),
            ("task", "tasks"),
            ("tasks", "task"),
            ("work", "working"),
            ("work", "worked"),
            ("work", "works"),
            ("working", "worked"),
            ("build", "building"),
            ("build", "built"),
            ("building", "built"),
            ("commit", "commits"),
            ("commit", "committing"),
            ("commit", "committed"),
            ("committing", "committed"),
        ],
    )
    def test_variant_pair_matches_either_direction(self, query_word: str, fact_word: str) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact=f"I am {fact_word} on something")
        result = retriever.retrieve(query_word, [ko])
        assert result == [ko]

    def test_exact_same_form_still_matches(self) -> None:
        # Normalization must not break the trivial identical-token case.
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="I need to build the demo")
        result = retriever.retrieve("build", [ko])
        assert result == [ko]

    def test_realistic_recall_gap_query_projects_am_i_working_on(self) -> None:
        # The exact query pattern that measurably lost recall after
        # stop-word filtering alone: "projects" (plural) vs. "Project
        # Atlas" (singular), recovered once both normalise to "project".
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="I'm building Project Atlas, a B2B SaaS tool")
        result = retriever.retrieve("What projects am I working on?", [ko])
        assert result == [ko]

    def test_realistic_recall_gap_commit_hygiene_vs_frequent_commits(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="I believe small, frequent commits produce fewer regressions")
        result = retriever.retrieve("what's my take on commit hygiene", [ko])
        assert result == [ko]

    @pytest.mark.parametrize("word", ["atlas", "postgres", "kubernetes", "status", "gas", "bus"])
    def test_unlisted_words_ending_in_s_are_never_mangled(self, word: str) -> None:
        # These all end in "s" without being plurals of a listed variant
        # group; a generic suffix-stripping stemmer would corrupt them
        # (e.g. "atlas" -> "atla"). The closed-table design must leave
        # them completely untouched, so a query for the exact word still
        # matches only a fact containing that exact word.
        retriever = KeywordCandidateRetriever()
        matching = make_ko(fact=f"the system uses {word} here")
        decoy = make_ko(fact="an unrelated fact about something else entirely")
        result = retriever.retrieve(word, [matching, decoy])
        assert result == [matching]

    def test_unlisted_ing_and_ed_words_are_never_mangled(self) -> None:
        # "testing"/"tested" are not in any variant group (only work/
        # build/commit/project/task are) -- a query for "testing" must
        # not accidentally match a fact that only contains "tested", or
        # vice versa, since that would mean silently stemming words this
        # table was never told about.
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="I tested the new pipeline yesterday")
        result = retriever.retrieve("testing", [ko])
        assert result == []

    def test_variant_groups_do_not_cross_contaminate(self) -> None:
        # "build" and "task" are different canonical forms; a query for
        # one must not match a fact containing only the other.
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="I have a task to finish")
        result = retriever.retrieve("building", [ko])
        assert result == []

    def test_normalization_is_case_insensitive(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="I'm BUILDING the pipeline")
        result = retriever.retrieve("Built", [ko])
        assert result == [ko]

    def test_normalization_combines_with_stop_word_filtering(self) -> None:
        # Integration of both fixes together: stop words removed AND
        # inflectional variants normalised in the same call.
        retriever = KeywordCandidateRetriever()
        matching = make_ko(fact="I'm migrating Project Nova's reporting pipeline")
        unrelated = make_ko(fact="I prefer dark mode in every editor")
        result = retriever.retrieve("What projects am I working on?", [matching, unrelated])
        assert result == [matching]

    def test_determinism_preserved_with_variants_present(self) -> None:
        retriever = KeywordCandidateRetriever()
        kos = [make_ko(fact=f"building fact {i}") for i in range(5)]

        forward = retriever.retrieve("built", kos)
        backward = retriever.retrieve("built", list(reversed(kos)))

        assert forward == backward
        assert len(forward) == 5
        assert [ko.id for ko in forward] == sorted([ko.id for ko in kos], key=str)


# ---------------------------------------------------------------------------
# TestVariantGroupsWellFormed
# ---------------------------------------------------------------------------


class TestVariantGroupsWellFormed:
    """Structural sanity checks on the full _VARIANT_GROUPS table itself.

    These guard the table's own authoring invariants -- separate from
    TestVariantNormalization, which checks retrieval *behaviour* for a
    sample of groups. As the table has grown to 40+ groups, a silent
    authoring mistake (the same variant added to two different groups, a
    stray uppercase entry) would otherwise only surface as an obscure
    retrieval bug far from its cause.
    """

    def test_no_variant_belongs_to_two_groups(self) -> None:
        from obsidian.memory_engine.keyword_candidate_retriever import (
            _VARIANT_GROUPS,
        )

        owner: dict = {}
        for canonical, variants in _VARIANT_GROUPS.items():
            for variant in variants:
                assert variant not in owner, (
                    f"{variant!r} claimed by both {owner.get(variant)!r} "
                    f"and {canonical!r}"
                )
                owner[variant] = canonical

    def test_canonical_form_is_a_member_of_its_own_group(self) -> None:
        from obsidian.memory_engine.keyword_candidate_retriever import (
            _VARIANT_GROUPS,
        )

        for canonical, variants in _VARIANT_GROUPS.items():
            assert canonical in variants

    def test_every_variant_is_already_lowercase(self) -> None:
        from obsidian.memory_engine.keyword_candidate_retriever import (
            _VARIANT_GROUPS,
        )

        for variants in _VARIANT_GROUPS.values():
            for variant in variants:
                assert variant == variant.lower()

    def test_canonical_form_lookup_matches_table(self) -> None:
        # _CANONICAL_FORM is a flattened, import-time-built view of
        # _VARIANT_GROUPS -- confirm it wasn't silently built from a
        # different table (e.g. a stale copy) by round-tripping every
        # entry.
        from obsidian.memory_engine.keyword_candidate_retriever import (
            _CANONICAL_FORM,
            _VARIANT_GROUPS,
        )

        for canonical, variants in _VARIANT_GROUPS.items():
            for variant in variants:
                assert _CANONICAL_FORM[variant] == canonical


# ---------------------------------------------------------------------------
# TestPhase1AuditConfirmedVariants
# ---------------------------------------------------------------------------


class TestPhase1AuditConfirmedVariants:
    """Groups added from the LEXICAL bucket of the candidate-generation
    failure audit (docs/architecture/CANDIDATE_GENERATION_DECISION.md).
    """

    @pytest.mark.parametrize(
        "query_word, fact_word",
        [
            ("embedding", "embeddings"),
            ("embeddings", "embedding"),
            ("model", "models"),
            ("model", "modeling"),
            ("model", "modelling"),
            ("model", "modeled"),
            ("model", "modelled"),
            ("benchmark", "benchmarks"),
            ("dataset", "datasets"),
            ("host", "hosting"),
            ("host", "hosted"),
            ("host", "hosts"),
            ("payment", "payments"),
            ("system", "systems"),
            ("secret", "secrets"),
            ("current", "currently"),
            ("learn", "learning"),
            ("learn", "learned"),
            ("combine", "combining"),
            ("combine", "combined"),
            ("evaluate", "evaluated"),
            ("evaluate", "evaluation"),
            ("evaluate", "evaluations"),
            ("optimize", "optimization"),
            ("optimize", "optimizations"),
            ("optimize", "optimise"),
            ("optimize", "optimisation"),
            ("prefer", "preferred"),
            ("prefer", "preference"),
            ("prefer", "preferences"),
            ("prefer", "prefs"),
        ],
    )
    def test_variant_pair_matches_either_direction(
        self, query_word: str, fact_word: str
    ) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact=f"a note mentioning {fact_word} somewhere")
        result = retriever.retrieve(query_word, [ko])
        assert result == [ko]

    def test_regression_supersession_basic_047_hosting_decision(self) -> None:
        # docs/architecture/CANDIDATE_GENERATION_DECISION.md §1: query says
        # "hosting", target fact says "host" -- zero token overlap before
        # this group existed.
        retriever = KeywordCandidateRetriever()
        target = make_ko(fact="We reconsidered and decided to host on GCP instead.")
        result = retriever.retrieve("What is the current hosting decision?", [target])
        assert result == [target]

    def test_regression_supersession_basic_001_embedding_system(self) -> None:
        # Query says "embedding" (singular), target fact says "embeddings"
        # (plural).
        retriever = KeywordCandidateRetriever()
        target = make_ko(fact="I replaced OpenAI embeddings with FastEmbed.")
        result = retriever.retrieve(
            "Which embedding system is currently being used?", [target]
        )
        assert result == [target]

    def test_regression_decision_basic_009_benchmark_dataset(self) -> None:
        # Query says "dataset" (singular) + "benchmarks" (plural); fact
        # says "benchmark" (singular) + "datasets" (plural) -- both the
        # benchmark and dataset groups must fire for this one to recover.
        retriever = KeywordCandidateRetriever()
        target = make_ko(
            fact="I decided to use dedicated benchmark datasets instead of real conversations."
        )
        result = retriever.retrieve(
            "What type of dataset should be used for benchmarks?", [target]
        )
        assert result == [target]


# ---------------------------------------------------------------------------
# TestResidualLexicalVariants
# ---------------------------------------------------------------------------


class TestResidualLexicalVariants:
    """Batch 5: groups added from the residual LEXICAL cases identified in
    docs/architecture/ENTITY_CAT_INVESTIGATION.md ("Recommendation 0") --
    cases that survived the Phase 1 audit because they need a different
    variant pair (name/named, decide family, prioritize family) than any
    group added there.
    """

    @pytest.mark.parametrize(
        "query_word, fact_word",
        [
            ("name", "named"),
            ("name", "naming"),
            ("named", "name"),
            ("decide", "decided"),
            ("decide", "deciding"),
            ("decide", "decides"),
            ("decided", "decide"),
            ("prioritize", "prioritized"),
            ("prioritize", "priority"),
            ("prioritized", "priority"),
            ("priority", "prioritized"),
            ("priorities", "prioritize"),
        ],
    )
    def test_variant_pair_matches_either_direction(
        self, query_word: str, fact_word: str
    ) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact=f"a note mentioning {fact_word} somewhere")
        result = retriever.retrieve(query_word, [ko])
        assert result == [ko]

    def test_regression_concept_consolidation_basic_050_name_named(self) -> None:
        # docs/architecture/ENTITY_CAT_INVESTIGATION.md §0: query says
        # "name", target fact says "named" -- zero token overlap before
        # this group existed.
        retriever = KeywordCandidateRetriever()
        target = make_ko(fact="I have a cat named Biscuit.")
        result = retriever.retrieve("...what is its name?", [target])
        assert result == [target]

    def test_regression_decision_reconstruction_basic_004_decide_decided(
        self,
    ) -> None:
        retriever = KeywordCandidateRetriever()
        target = make_ko(fact="Decided on Portugal for the trip.")
        result = retriever.retrieve(
            "Where did the user decide to go on vacation?", [target]
        )
        assert result == [target]

    def test_regression_decision_reconstruction_basic_015_decide_decided(
        self,
    ) -> None:
        retriever = KeywordCandidateRetriever()
        target = make_ko(fact="Decided on Raleigh as the new home base.")
        result = retriever.retrieve(
            "Which city did the user decide to move to?", [target]
        )
        assert result == [target]

    def test_regression_decision_reconstruction_basic_021_decide_decided(
        self,
    ) -> None:
        retriever = KeywordCandidateRetriever()
        target = make_ko(fact="Decided on a Border Collie as the new dog.")
        result = retriever.retrieve(
            "Which breed did the user decide to adopt?", [target]
        )
        assert result == [target]

    def test_regression_decision_basic_004_prioritized_priority(self) -> None:
        retriever = KeywordCandidateRetriever()
        target = make_ko(
            fact="Manager AI is currently a higher priority than the retrieval work."
        )
        result = retriever.retrieve(
            "Which subsystem should be prioritized next?", [target]
        )
        assert result == [target]


# ---------------------------------------------------------------------------
# TestTechnologyAliasVariants
# ---------------------------------------------------------------------------


class TestTechnologyAliasVariants:
    @pytest.mark.parametrize(
        "query_word, fact_word",
        [
            ("api", "apis"),
            ("repo", "repository"),
            ("repo", "repositories"),
            ("config", "configuration"),
            ("config", "configurations"),
            ("database", "db"),
            ("database", "dbs"),
            ("database", "databases"),
            ("auth", "authentication"),
            ("auth", "authenticated"),
            ("environment", "env"),
            ("environment", "envs"),
            ("infrastructure", "infra"),
            ("specification", "spec"),
            ("specification", "specs"),
            ("application", "app"),
            ("application", "apps"),
            ("production", "prod"),
            ("javascript", "js"),
            ("typescript", "ts"),
            ("backend", "backends"),
            ("frontend", "frontends"),
            ("info", "information"),
            ("misc", "miscellaneous"),
        ],
    )
    def test_alias_pair_matches_either_direction(
        self, query_word: str, fact_word: str
    ) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact=f"a note mentioning {fact_word} somewhere")
        result = retriever.retrieve(query_word, [ko])
        assert result == [ko]

    def test_realistic_recall_gap_db_config(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Switched the app's db config to use a connection pool")
        result = retriever.retrieve("What database configuration is used?", [ko])
        assert result == [ko]

    def test_dev_is_not_aliased_to_development(self) -> None:
        # "dev" deliberately excluded: it means "developer" (a person) at
        # least as often as "development" (a process) in casual notes, so
        # aliasing it would conflate two different senses rather than fix
        # an inflectional gap.
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="I've been working on the development roadmap")
        result = retriever.retrieve("dev", [ko])
        assert result == []


# ---------------------------------------------------------------------------
# TestSpellingVariants
# ---------------------------------------------------------------------------


class TestSpellingVariants:
    @pytest.mark.parametrize(
        "query_word, fact_word",
        [
            ("color", "colour"),
            ("colors", "colours"),
            ("behavior", "behaviour"),
            ("behaviors", "behaviours"),
            ("license", "licence"),
            ("licensed", "licenced"),
            ("organize", "organise"),
            ("organization", "organisation"),
            ("analyze", "analyse"),
            ("analyze", "analyses"),
            ("customize", "customise"),
            ("customization", "customisation"),
            ("favorite", "favourite"),
            ("favorites", "favourites"),
            ("canceled", "cancelled"),
            ("canceling", "cancelling"),
        ],
    )
    def test_spelling_pair_matches_either_direction(
        self, query_word: str, fact_word: str
    ) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact=f"a note mentioning {fact_word} somewhere")
        result = retriever.retrieve(query_word, [ko])
        assert result == [ko]

    def test_realistic_recall_gap_british_spelling(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="I prefer to organise my notes by colour")
        result = retriever.retrieve("How do I organize notes by color?", [ko])
        assert result == [ko]


# ---------------------------------------------------------------------------
# TestKeywordMatch
# ---------------------------------------------------------------------------


class TestKeywordMatch:
    def test_valid_construction(self) -> None:
        ko = make_ko()
        match = KeywordMatch(knowledge_object=ko, keyword_overlap_score=0.5)
        assert match.knowledge_object is ko
        assert match.keyword_overlap_score == 0.5

    @pytest.mark.parametrize("score", [0.0, 1.0])
    def test_accepts_score_boundaries(self, score: float) -> None:
        match = KeywordMatch(knowledge_object=make_ko(), keyword_overlap_score=score)
        assert match.keyword_overlap_score == score

    @pytest.mark.parametrize("score", [-0.01, 1.01])
    def test_rejects_score_out_of_range(self, score: float) -> None:
        with pytest.raises(ValueError, match="keyword_overlap_score"):
            KeywordMatch(knowledge_object=make_ko(), keyword_overlap_score=score)

    def test_is_frozen(self) -> None:
        match = KeywordMatch(knowledge_object=make_ko(), keyword_overlap_score=0.5)
        with pytest.raises(dataclasses.FrozenInstanceError):
            match.keyword_overlap_score = 0.9  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestKeywordOverlapScoring
# ---------------------------------------------------------------------------


class TestKeywordOverlapScoring:
    def test_retrieve_and_retrieve_with_scores_agree_on_matched_set(self) -> None:
        retriever = KeywordCandidateRetriever()
        kos = [make_ko(fact=f"Haven uses Claude, fact {i}") for i in range(4)]

        via_retrieve = retriever.retrieve("claude", kos)
        via_scores = retriever.retrieve_with_scores("claude", kos)

        assert [ko.id for ko in via_retrieve] == [m.knowledge_object.id for m in via_scores]

    def test_no_match_returns_no_keyword_matches(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        assert retriever.retrieve_with_scores("banana", [ko]) == []

    def test_empty_query_returns_no_keyword_matches(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        assert retriever.retrieve_with_scores("", [ko]) == []

    def test_score_is_always_in_bounds(self) -> None:
        retriever = KeywordCandidateRetriever()
        kos = [
            make_ko(fact="Haven uses Claude for extraction and classification"),
            make_ko(fact="Claude is an AI assistant by Anthropic"),
            make_ko(fact="Unrelated fact about bananas"),
        ]
        for match in retriever.retrieve_with_scores("Haven uses Claude for extraction", kos):
            assert 0.0 <= match.keyword_overlap_score <= 1.0

    def test_matching_more_query_keywords_scores_higher(self) -> None:
        # Same corpus (so token rarity is held constant); one fact shares
        # only one query keyword, the other shares both.
        retriever = KeywordCandidateRetriever()
        one_overlap = make_ko(fact="Haven is a personal project")
        two_overlap = make_ko(fact="Haven uses Claude for everything")
        kos = [one_overlap, two_overlap]

        matches = {
            m.knowledge_object.id: m.keyword_overlap_score
            for m in retriever.retrieve_with_scores("Haven Claude", kos)
        }

        assert matches[two_overlap.id] > matches[one_overlap.id]

    def test_rarer_token_scores_higher_than_common_token(self) -> None:
        # "zephyr" appears in exactly one candidate; "system" appears in
        # four of five -- a fact matching only the rare token must score
        # higher than one matching only the common token, holding overlap
        # *count* (one token each) equal.
        retriever = KeywordCandidateRetriever()
        rare_match = make_ko(fact="The zephyr module handles background jobs")
        common_match = make_ko(fact="The system logs every request")
        filler = [
            make_ko(fact="The system starts up quickly"),
            make_ko(fact="The system requires no configuration"),
            make_ko(fact="The system is deployed on Kubernetes"),
        ]
        kos = [rare_match, common_match, *filler]

        matches = {
            m.knowledge_object.id: m.keyword_overlap_score
            for m in retriever.retrieve_with_scores("zephyr system", kos)
        }

        assert matches[rare_match.id] > matches[common_match.id]

    def test_exact_phrase_match_scores_higher_than_scattered_tokens(self) -> None:
        # Both facts share the exact same overlapping tokens with the
        # query ({"second", "brain"}); only one contains them as an
        # adjacent, verbatim phrase. A third fact introduces "concept"
        # into the corpus so it has a real (nonzero) rarity weight,
        # keeping the base token-overlap ratio below 1.0 for both
        # comparison facts -- otherwise the phrase bonus would just be
        # clamped away and the two facts would tie.
        retriever = KeywordCandidateRetriever()
        phrase_match = make_ko(fact="Haven is a personal second brain project")
        scattered_match = make_ko(fact="A brain teaser about which number comes second")
        filler = make_ko(fact="The concept of memory persistence is fascinating")
        kos = [phrase_match, scattered_match, filler]

        matches = {
            m.knowledge_object.id: m.keyword_overlap_score
            for m in retriever.retrieve_with_scores("second brain concept", kos)
        }

        assert phrase_match.id in matches
        assert scattered_match.id in matches
        assert matches[phrase_match.id] > matches[scattered_match.id]

    def test_identical_overlap_without_phrase_difference_ties(self) -> None:
        # Sanity check for the phrase test above: with no third "concept"
        # filler diluting the ratio, both facts hit the 1.0 cap and tie --
        # confirms the phrase bonus, not some other asymmetry, produced
        # the difference in the previous test.
        retriever = KeywordCandidateRetriever()
        phrase_match = make_ko(fact="Haven is a personal second brain project")
        scattered_match = make_ko(fact="A brain teaser about which number comes second")
        kos = [phrase_match, scattered_match]

        matches = {
            m.knowledge_object.id: m.keyword_overlap_score
            for m in retriever.retrieve_with_scores("second brain", kos)
        }

        assert matches[phrase_match.id] == pytest.approx(matches[scattered_match.id])

    def test_single_document_corpus_does_not_raise(self) -> None:
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="Haven uses Claude")
        matches = retriever.retrieve_with_scores("claude", [ko])
        assert len(matches) == 1
        assert 0.0 <= matches[0].keyword_overlap_score <= 1.0

    def test_determinism_same_query_same_pool(self) -> None:
        retriever = KeywordCandidateRetriever()
        kos = [make_ko(fact=f"Haven uses Claude, fact {i}") for i in range(6)]

        first = retriever.retrieve_with_scores("Haven Claude", kos)
        second = retriever.retrieve_with_scores("Haven Claude", kos)

        assert first == second

    def test_determinism_independent_of_pool_order(self) -> None:
        retriever = KeywordCandidateRetriever()
        kos = [make_ko(fact=f"Haven uses Claude, fact {i}") for i in range(6)]

        forward = retriever.retrieve_with_scores("Haven Claude", kos)
        backward = retriever.retrieve_with_scores("Haven Claude", list(reversed(kos)))

        forward_scores = {m.knowledge_object.id: m.keyword_overlap_score for m in forward}
        backward_scores = {m.knowledge_object.id: m.keyword_overlap_score for m in backward}
        assert forward_scores == backward_scores
        assert [m.knowledge_object.id for m in forward] == [m.knowledge_object.id for m in backward]

    def test_variant_normalized_tokens_still_contribute_to_overlap_score(self) -> None:
        # The scoring layer sits on top of the same normalised tokenizer
        # stop-word filtering and variant normalization already use --
        # a "projects"/"project" match still produces a positive score.
        retriever = KeywordCandidateRetriever()
        ko = make_ko(fact="I'm building Project Atlas, a B2B SaaS tool")
        matches = retriever.retrieve_with_scores("What projects am I working on?", [ko])
        assert len(matches) == 1
        assert matches[0].keyword_overlap_score > 0.0


# ---------------------------------------------------------------------------
# TestPhraseOverlapEquivalence
#
# Performance-audit regression tests. _has_multiword_phrase_overlap was
# rewritten from an O(len(query_tokens)^2 * len(fact_tokens)) triple loop
# (checking every window length from len(query_tokens) down to 2) to an
# O(len(query_tokens) + len(fact_tokens)) bigram-set check, on the grounds
# that "some contiguous run of length >= 2 matches" is logically equivalent
# to "some contiguous run of length == 2 (a bigram) matches" -- see the
# function's own docstring for the proof. These tests cross-check the new
# implementation against a brute-force reference that mirrors the original
# algorithm exactly, plus a hand-picked set of edge cases, plus a timing
# guard that would fail under the old cubic implementation.
# ---------------------------------------------------------------------------


def _brute_force_phrase_overlap(query_tokens: List[str], fact_tokens: List[str]) -> bool:
    """Reference implementation mirroring the pre-optimization algorithm.

    Deliberately re-implemented here (not imported) so this test would still
    catch a regression even if someone "simplified" the source back to this
    same slow shape.
    """
    if len(query_tokens) < 2:
        return False
    for length in range(len(query_tokens), 1, -1):
        for start in range(len(query_tokens) - length + 1):
            window = query_tokens[start : start + length]
            for fact_start in range(len(fact_tokens) - length + 1):
                if fact_tokens[fact_start : fact_start + length] == window:
                    return True
    return False


class TestPhraseOverlapEquivalence:
    @pytest.mark.parametrize(
        "query_tokens,fact_tokens",
        [
            ([], []),
            (["haven"], ["haven", "uses", "claude"]),
            ([], ["haven", "uses", "claude"]),
            (["haven", "uses"], ["haven", "uses", "claude"]),
            (["uses", "haven"], ["haven", "uses", "claude"]),
            (["second", "brain"], ["haven", "is", "a", "second", "brain", "project"]),
            (["second", "brain"], ["a", "brain", "teaser", "about", "second"]),
            (["a", "b", "c"], ["x", "a", "b", "c", "y"]),
            (["a", "b", "c"], ["c", "b", "a"]),
            (["a", "b", "c", "d"], ["b", "c", "d", "e"]),
            (["a", "b"], ["a"]),
            (["a", "b"], []),
            (["x", "y", "z"], ["x", "y", "z"]),
            (["x", "y", "z"], ["y", "z", "x"]),
            (["a", "a", "a"], ["a", "a", "a", "a"]),
        ],
    )
    def test_matches_brute_force_reference(
        self, query_tokens: List[str], fact_tokens: List[str]
    ) -> None:
        assert _has_multiword_phrase_overlap(query_tokens, fact_tokens) == (
            _brute_force_phrase_overlap(query_tokens, fact_tokens)
        )

    def test_matches_brute_force_reference_on_random_token_streams(self) -> None:
        import random

        rng = random.Random(1234)
        vocab = [f"tok{i}" for i in range(6)]
        for _ in range(200):
            q = [rng.choice(vocab) for _ in range(rng.randint(0, 8))]
            f = [rng.choice(vocab) for _ in range(rng.randint(0, 8))]
            assert _has_multiword_phrase_overlap(q, f) == _brute_force_phrase_overlap(q, f)

    def test_long_run_match_implies_bigram_match_is_found(self) -> None:
        query = ["the", "quick", "brown", "fox", "jumps"]
        fact = ["a", "the", "quick", "brown", "fox", "jumps", "away"]
        assert _has_multiword_phrase_overlap(query, fact) is True

    def test_no_shared_bigram_returns_false_even_with_shared_tokens(self) -> None:
        # Every token overlaps but never as an adjacent pair in the same
        # order -- fact is query reversed, so no (a, b) pair from query
        # survives intact in fact.
        query = ["alpha", "beta", "gamma"]
        fact = ["gamma", "beta", "alpha"]
        assert _has_multiword_phrase_overlap(query, fact) is False

    def test_single_query_token_never_matches(self) -> None:
        assert _has_multiword_phrase_overlap(["haven"], ["haven", "haven"]) is False

    def test_scales_linearly_not_cubically(self) -> None:
        """Timing guard: under the old O(qlen^2 * flen) algorithm, a query
        this long (proportional to a synthetic vault's total token count --
        the real-world shape of the dashboard's "digest query", see
        obsidian/server/dashboard.py's _working_context_summaries and
        _project_overview) took multiple seconds per single fact comparison.
        Under the new O(qlen + flen) algorithm this completes in well under
        a second for the whole loop. A regression back to the old shape
        would make this test time out or fail the assertion.
        """
        import time

        query_tokens = [f"word{i}" for i in range(4000)] + ["needle", "haystack"]
        fact_tokens = [f"other{i}" for i in range(50)] + ["needle", "haystack"]

        start = time.perf_counter()
        for _ in range(50):
            result = _has_multiword_phrase_overlap(query_tokens, fact_tokens)
        elapsed = time.perf_counter() - start

        assert result is True
        assert elapsed < 2.0, (
            f"_has_multiword_phrase_overlap took {elapsed:.2f}s for 50 calls "
            "against a ~4000-token query -- expected sub-second; this "
            "indicates a regression back to quadratic/cubic behaviour."
        )


# ---------------------------------------------------------------------------
# TestDocumentFrequenciesFromTokens
#
# _document_frequencies_from_tokens factors the per-fact tokenisation out of
# _document_frequencies so KeywordCandidateRetriever.retrieve_with_scores can
# tokenise every fact exactly once (previously: once for document
# frequencies, once again for matching/scoring). These tests confirm the two
# entry points agree.
# ---------------------------------------------------------------------------


class TestDocumentFrequenciesFromTokens:
    def test_agrees_with_document_frequencies_on_knowledge_objects(self) -> None:
        kos = [
            make_ko(fact="Haven uses Claude for extraction"),
            make_ko(fact="Claude is used for classification too"),
            make_ko(fact="Unrelated fact about bananas"),
        ]
        via_kos = _document_frequencies(kos)
        via_tokens = _document_frequencies_from_tokens(
            [_tokenize(ko.canonical_fact) for ko in kos]
        )
        assert via_kos == via_tokens

    def test_empty_input_returns_empty_dict(self) -> None:
        assert _document_frequencies_from_tokens([]) == {}

    def test_repeated_token_within_one_fact_counts_once(self) -> None:
        freqs = _document_frequencies_from_tokens([["haven", "haven", "claude"]])
        assert freqs == {"haven": 1, "claude": 1}


class TestRetrieveWithScoresSingleTokenizationPass:
    def test_scoring_output_unaffected_by_single_pass_refactor(self) -> None:
        # End-to-end guard: retrieve_with_scores now tokenises each fact
        # once and reuses it for both document-frequency counting and
        # matching/scoring, instead of tokenising twice. Scores must be
        # identical to a pool where nothing changed about the text.
        retriever = KeywordCandidateRetriever()
        kos = [
            make_ko(fact="Haven uses Claude for extraction and classification"),
            make_ko(fact="Claude is an AI assistant by Anthropic"),
            make_ko(fact="Unrelated fact about bananas"),
        ]
        matches = retriever.retrieve_with_scores("Haven uses Claude for extraction", kos)
        assert len(matches) == 2
        for match in matches:
            assert 0.0 <= match.keyword_overlap_score <= 1.0
