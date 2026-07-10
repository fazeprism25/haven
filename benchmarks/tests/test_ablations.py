"""Tests for :mod:`benchmarks.adapters.ablations`.

These verify only the one thing the ablation subclasses are responsible
for: constructing :class:`HavenAdapter` with the correct single weight
zeroed and every other weight left at its default. The retrieval pipeline
itself is HavenAdapter's/​MemoryEngine's contract, covered elsewhere.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from benchmarks.adapters.ablations import (
    HavenNoKeywordAdapter,
    HavenNoOntologyAdapter,
    HavenNoRecencyAdapter,
)
from benchmarks.adapters.haven_adapter import HavenAdapter
from obsidian.ontology.retrieval_config import RetrievalConfig


def _zeroed_weights(config: RetrievalConfig) -> set[str]:
    default = RetrievalConfig()
    return {
        f.name
        for f in fields(RetrievalConfig)
        if getattr(config, f.name) == 0.0 and getattr(default, f.name) != 0.0
    }


class TestAblationConfigWiring:
    def test_no_ontology_zeros_only_activation_and_attachment(self) -> None:
        adapter = HavenNoOntologyAdapter.from_config({})
        assert isinstance(adapter, HavenAdapter)
        assert _zeroed_weights(adapter._config) == {
            "weight_activation",
            "weight_attachment_relevance",
        }

    def test_no_keyword_zeros_only_keyword_overlap(self) -> None:
        adapter = HavenNoKeywordAdapter.from_config({})
        assert _zeroed_weights(adapter._config) == {"weight_keyword_overlap"}

    def test_no_recency_zeros_only_recency(self) -> None:
        adapter = HavenNoRecencyAdapter.from_config({})
        assert _zeroed_weights(adapter._config) == {"weight_recency"}

    @pytest.mark.parametrize(
        "cls", [HavenNoOntologyAdapter, HavenNoKeywordAdapter, HavenNoRecencyAdapter]
    )
    def test_non_weight_config_matches_default(self, cls) -> None:
        # Only scoring weights change; retrieval constants stay at default.
        adapter = cls.from_config({})
        default = RetrievalConfig()
        for name in ("max_results", "max_depth", "activation_decay",
                     "activation_threshold", "minimum_candidate_score"):
            assert getattr(adapter._config, name) == getattr(default, name)


class TestAblationEndToEnd:
    """The ablated adapter must still run the real pipeline without error."""

    def test_no_recency_still_retrieves_a_present_fact(self) -> None:
        adapter = HavenNoRecencyAdapter.from_config({})
        adapter.add([{"role": "user", "content": "Haven uses Claude for reasoning."}])
        result = adapter.search("What does Haven use?")
        answer = " ".join(mem["memory"] for mem in result.get("results", []))
        assert "Haven uses Claude for reasoning." in answer
