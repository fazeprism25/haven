"""Unit tests for obsidian.ontology.retrieval_config.RetrievalConfig.

Scoped to the ``weight_keyword_overlap`` field added alongside
:class:`~obsidian.memory_engine.keyword_candidate_retriever.KeywordMatch`.

Test groups
-----------
TestDefaults                    — default value and independence from
                                    the other six weights.
TestValidation                   — same validation convention as the other
                                    scoring weights (non-negative).
TestKeywordConstruction           — field is settable independently via
                                    keyword argument, doesn't disturb the
                                    other six weights.
"""

from __future__ import annotations

import pytest

from obsidian.ontology.retrieval_config import RetrievalConfig


class TestDefaults:
    def test_default_value(self) -> None:
        cfg = RetrievalConfig()
        assert cfg.weight_keyword_overlap == 0.20

    def test_independent_from_weight_attachment_relevance(self) -> None:
        cfg = RetrievalConfig()
        assert cfg.weight_keyword_overlap == cfg.weight_attachment_relevance
        # Equal by coincidence of chosen defaults, not by any code path
        # that ties them together -- confirmed by overriding one leaving
        # the other untouched in TestKeywordConstruction below.


class TestValidation:
    @pytest.mark.parametrize("value", [-0.01, -1.0])
    def test_rejects_negative_weight(self, value: float) -> None:
        with pytest.raises(ValueError, match="weight_keyword_overlap"):
            RetrievalConfig(weight_keyword_overlap=value)

    @pytest.mark.parametrize("value", [0.0, 0.5, 1.0, 10.0])
    def test_accepts_non_negative_weight(self, value: float) -> None:
        cfg = RetrievalConfig(weight_keyword_overlap=value)
        assert cfg.weight_keyword_overlap == value


class TestKeywordConstruction:
    def test_overriding_leaves_other_weights_untouched(self) -> None:
        cfg = RetrievalConfig(weight_keyword_overlap=0.9)
        assert cfg.weight_keyword_overlap == 0.9
        assert cfg.weight_activation == 0.35
        assert cfg.weight_attachment_relevance == 0.20
        assert cfg.weight_importance == 0.25
        assert cfg.weight_confidence == 0.20
        assert cfg.weight_recency == 0.15
        assert cfg.weight_confirmation_count == 0.05
