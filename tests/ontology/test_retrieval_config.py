"""Unit tests for RetrievalConfig."""

from __future__ import annotations

import dataclasses

import pytest

from obsidian.ontology.retrieval_config import RetrievalConfig


# ---------------------------------------------------------------------------
# Default construction
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_default_construction_succeeds(self) -> None:
        cfg = RetrievalConfig()
        assert cfg is not None

    def test_default_max_results(self) -> None:
        assert RetrievalConfig().max_results == 50

    def test_default_max_depth(self) -> None:
        assert RetrievalConfig().max_depth == 3

    def test_default_activation_decay(self) -> None:
        assert RetrievalConfig().activation_decay == 0.5

    def test_default_activation_threshold(self) -> None:
        assert RetrievalConfig().activation_threshold == 0.05

    def test_default_minimum_candidate_score(self) -> None:
        assert RetrievalConfig().minimum_candidate_score == 0.1

    def test_default_weight_activation(self) -> None:
        assert RetrievalConfig().weight_activation == 0.35

    def test_default_weight_importance(self) -> None:
        assert RetrievalConfig().weight_importance == 0.25

    def test_default_weight_confidence(self) -> None:
        assert RetrievalConfig().weight_confidence == 0.20

    def test_default_weight_recency(self) -> None:
        assert RetrievalConfig().weight_recency == 0.15

    def test_default_weight_confirmation_count(self) -> None:
        assert RetrievalConfig().weight_confirmation_count == 0.05

    def test_default_propagation_weight_is_a(self) -> None:
        assert RetrievalConfig().propagation_weight_is_a == 0.9

    def test_default_propagation_weight_part_of(self) -> None:
        assert RetrievalConfig().propagation_weight_part_of == 0.8

    def test_default_propagation_weight_uses(self) -> None:
        assert RetrievalConfig().propagation_weight_uses == 0.7

    def test_default_propagation_weight_depends_on(self) -> None:
        assert RetrievalConfig().propagation_weight_depends_on == 0.7

    def test_default_propagation_weight_created_by(self) -> None:
        assert RetrievalConfig().propagation_weight_created_by == 0.5

    def test_default_propagation_weight_located_in(self) -> None:
        assert RetrievalConfig().propagation_weight_located_in == 0.5

    def test_default_propagation_weight_supports(self) -> None:
        assert RetrievalConfig().propagation_weight_supports == 0.6

    def test_default_propagation_weight_related_to(self) -> None:
        assert RetrievalConfig().propagation_weight_related_to == 0.3


# ---------------------------------------------------------------------------
# Custom valid construction
# ---------------------------------------------------------------------------


class TestCustomValid:
    def test_custom_max_results(self) -> None:
        cfg = RetrievalConfig(max_results=100)
        assert cfg.max_results == 100

    def test_custom_max_depth(self) -> None:
        cfg = RetrievalConfig(max_depth=5)
        assert cfg.max_depth == 5

    def test_custom_activation_decay(self) -> None:
        cfg = RetrievalConfig(activation_decay=0.8)
        assert cfg.activation_decay == 0.8

    def test_custom_activation_threshold(self) -> None:
        cfg = RetrievalConfig(activation_threshold=1.0)
        assert cfg.activation_threshold == 1.0

    def test_custom_minimum_candidate_score_zero(self) -> None:
        cfg = RetrievalConfig(minimum_candidate_score=0.0)
        assert cfg.minimum_candidate_score == 0.0

    def test_custom_minimum_candidate_score_one(self) -> None:
        cfg = RetrievalConfig(minimum_candidate_score=1.0)
        assert cfg.minimum_candidate_score == 1.0

    def test_max_results_equals_one(self) -> None:
        cfg = RetrievalConfig(max_results=1)
        assert cfg.max_results == 1

    def test_max_depth_equals_one(self) -> None:
        cfg = RetrievalConfig(max_depth=1)
        assert cfg.max_depth == 1

    def test_scoring_weight_zero_is_allowed(self) -> None:
        cfg = RetrievalConfig(weight_activation=0.0)
        assert cfg.weight_activation == 0.0

    def test_scoring_weight_above_one_is_allowed(self) -> None:
        cfg = RetrievalConfig(weight_importance=2.0)
        assert cfg.weight_importance == 2.0

    def test_propagation_weight_at_boundary_one(self) -> None:
        cfg = RetrievalConfig(propagation_weight_is_a=1.0)
        assert cfg.propagation_weight_is_a == 1.0

    def test_all_propagation_weights_customised(self) -> None:
        cfg = RetrievalConfig(
            propagation_weight_is_a=0.1,
            propagation_weight_part_of=0.2,
            propagation_weight_uses=0.3,
            propagation_weight_depends_on=0.4,
            propagation_weight_created_by=0.5,
            propagation_weight_located_in=0.6,
            propagation_weight_supports=0.7,
            propagation_weight_related_to=0.8,
        )
        assert cfg.propagation_weight_is_a == 0.1
        assert cfg.propagation_weight_related_to == 0.8


# ---------------------------------------------------------------------------
# Frozen / immutability
# ---------------------------------------------------------------------------


class TestFrozen:
    def test_cannot_set_attribute(self) -> None:
        cfg = RetrievalConfig()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            cfg.max_results = 99  # type: ignore[misc]

    def test_cannot_delete_attribute(self) -> None:
        cfg = RetrievalConfig()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            del cfg.max_results  # type: ignore[misc]

    def test_is_dataclass(self) -> None:
        assert dataclasses.is_dataclass(RetrievalConfig)

    def test_equality_by_value(self) -> None:
        assert RetrievalConfig() == RetrievalConfig()

    def test_inequality_when_different(self) -> None:
        assert RetrievalConfig(max_results=10) != RetrievalConfig(max_results=20)

    def test_hashable(self) -> None:
        cfg = RetrievalConfig()
        assert hash(cfg) == hash(cfg)
        d = {cfg: "value"}
        assert d[cfg] == "value"


# ---------------------------------------------------------------------------
# Invalid max_results
# ---------------------------------------------------------------------------


class TestInvalidMaxResults:
    def test_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="max_results"):
            RetrievalConfig(max_results=0)

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="max_results"):
            RetrievalConfig(max_results=-1)

    def test_large_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="max_results"):
            RetrievalConfig(max_results=-1000)


# ---------------------------------------------------------------------------
# Invalid max_depth
# ---------------------------------------------------------------------------


class TestInvalidMaxDepth:
    def test_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="max_depth"):
            RetrievalConfig(max_depth=0)

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="max_depth"):
            RetrievalConfig(max_depth=-5)


# ---------------------------------------------------------------------------
# Invalid activation_decay
# ---------------------------------------------------------------------------


class TestInvalidActivationDecay:
    def test_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="activation_decay"):
            RetrievalConfig(activation_decay=0.0)

    def test_one_raises(self) -> None:
        with pytest.raises(ValueError, match="activation_decay"):
            RetrievalConfig(activation_decay=1.0)

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="activation_decay"):
            RetrievalConfig(activation_decay=-0.1)

    def test_above_one_raises(self) -> None:
        with pytest.raises(ValueError, match="activation_decay"):
            RetrievalConfig(activation_decay=1.5)


# ---------------------------------------------------------------------------
# Invalid activation_threshold
# ---------------------------------------------------------------------------


class TestInvalidActivationThreshold:
    def test_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="activation_threshold"):
            RetrievalConfig(activation_threshold=0.0)

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="activation_threshold"):
            RetrievalConfig(activation_threshold=-0.01)

    def test_above_one_raises(self) -> None:
        with pytest.raises(ValueError, match="activation_threshold"):
            RetrievalConfig(activation_threshold=1.1)


# ---------------------------------------------------------------------------
# Invalid minimum_candidate_score
# ---------------------------------------------------------------------------


class TestInvalidMinimumCandidateScore:
    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="minimum_candidate_score"):
            RetrievalConfig(minimum_candidate_score=-0.01)

    def test_above_one_raises(self) -> None:
        with pytest.raises(ValueError, match="minimum_candidate_score"):
            RetrievalConfig(minimum_candidate_score=1.01)


# ---------------------------------------------------------------------------
# Invalid scoring weights
# ---------------------------------------------------------------------------


class TestInvalidScoringWeights:
    @pytest.mark.parametrize(
        "field_name",
        [
            "weight_activation",
            "weight_importance",
            "weight_confidence",
            "weight_recency",
            "weight_confirmation_count",
        ],
    )
    def test_negative_raises(self, field_name: str) -> None:
        with pytest.raises(ValueError, match=field_name):
            RetrievalConfig(**{field_name: -0.01})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Invalid propagation weights
# ---------------------------------------------------------------------------


class TestInvalidPropagationWeights:
    @pytest.mark.parametrize(
        "field_name",
        [
            "propagation_weight_is_a",
            "propagation_weight_part_of",
            "propagation_weight_uses",
            "propagation_weight_depends_on",
            "propagation_weight_created_by",
            "propagation_weight_located_in",
            "propagation_weight_supports",
            "propagation_weight_related_to",
        ],
    )
    def test_zero_raises(self, field_name: str) -> None:
        with pytest.raises(ValueError, match=field_name):
            RetrievalConfig(**{field_name: 0.0})  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "field_name",
        [
            "propagation_weight_is_a",
            "propagation_weight_part_of",
            "propagation_weight_uses",
            "propagation_weight_depends_on",
            "propagation_weight_created_by",
            "propagation_weight_located_in",
            "propagation_weight_supports",
            "propagation_weight_related_to",
        ],
    )
    def test_above_one_raises(self, field_name: str) -> None:
        with pytest.raises(ValueError, match=field_name):
            RetrievalConfig(**{field_name: 1.01})  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "field_name",
        [
            "propagation_weight_is_a",
            "propagation_weight_part_of",
            "propagation_weight_uses",
            "propagation_weight_depends_on",
            "propagation_weight_created_by",
            "propagation_weight_located_in",
            "propagation_weight_supports",
            "propagation_weight_related_to",
        ],
    )
    def test_negative_raises(self, field_name: str) -> None:
        with pytest.raises(ValueError, match=field_name):
            RetrievalConfig(**{field_name: -0.5})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Error message quality
# ---------------------------------------------------------------------------


class TestErrorMessages:
    def test_max_results_message_contains_value(self) -> None:
        with pytest.raises(ValueError, match="0"):
            RetrievalConfig(max_results=0)

    def test_activation_decay_message_contains_value(self) -> None:
        with pytest.raises(ValueError, match="1.0"):
            RetrievalConfig(activation_decay=1.0)

    def test_propagation_weight_message_contains_value(self) -> None:
        with pytest.raises(ValueError, match="0.0"):
            RetrievalConfig(propagation_weight_is_a=0.0)
