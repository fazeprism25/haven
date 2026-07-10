"""Haven scoring-contribution ablations for the benchmark runner.

Each class here is :class:`~benchmarks.adapters.haven_adapter.HavenAdapter`
with exactly one ranking weight zeroed via
:func:`dataclasses.replace` over the default
:class:`~obsidian.ontology.retrieval_config.RetrievalConfig`. Running the
suite once per ablation and diffing the resulting ``results_*.json``
against ``results_haven.json`` attributes Haven's pass rate to specific
mechanisms, instead of reporting one opaque aggregate.

What these ablations are (and are not)
--------------------------------------
``HavenAdapter.__init__`` already accepts a ``config: RetrievalConfig``,
and :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`
already tolerates a zero total weight (it emits an all-zero breakdown
rather than dividing by zero). So these subclasses change **no Haven
code** and add no pipeline branch — they only construct the adapter with a
different, already-supported config.

They are **scoring-contribution** ablations, not retrieval ablations: a
candidate found via the ontology path (or keyword path) is still
*retrieved* and still competes; zeroing its weight only removes that
axis's contribution to the composite ``final_score``. This is the honest,
minimal-surface way to ask "how much does this signal move the ranking?"
without reaching into ``HybridCandidateRetriever`` to suppress a retrieval
path — which would be changing Haven's algorithm, explicitly out of scope.
A consequence worth stating in any writeup: because the composite score
also feeds ``minimum_candidate_score`` and the acceptance stage, zeroing a
weight can drop a candidate that now scores below the floor — that is a
real effect of removing the signal, not an artifact.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Optional

from benchmarks.adapters.haven_adapter import HavenAdapter
from obsidian.ontology.retrieval_config import RetrievalConfig


class HavenNoOntologyAdapter(HavenAdapter):
    """Haven with the ontology signals (activation + attachment) zeroed.

    Removes ``weight_activation`` and ``weight_attachment_relevance`` so
    ranking is driven by keyword overlap, importance, confidence, recency,
    and confirmation count alone. The delta against full Haven is the
    ranking value of concept-graph activation.
    """

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None) -> "HavenNoOntologyAdapter":
        return cls(
            config=replace(
                RetrievalConfig(),
                weight_activation=0.0,
                weight_attachment_relevance=0.0,
            )
        )


class HavenNoKeywordAdapter(HavenAdapter):
    """Haven with the keyword-overlap signal zeroed.

    Removes ``weight_keyword_overlap`` so ranking leans on the ontology and
    the intrinsic KnowledgeObject signals. The delta against full Haven is
    the ranking value of lexical overlap.
    """

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None) -> "HavenNoKeywordAdapter":
        return cls(config=replace(RetrievalConfig(), weight_keyword_overlap=0.0))


class HavenNoRecencyAdapter(HavenAdapter):
    """Haven with the recency signal zeroed.

    Removes ``weight_recency``. Most diagnostic on the temporal /
    supersession / contradiction categories: if full Haven only beats this
    ablation there, its wins on "which belief is current" are coming from
    recency, not from the ontology — the same thing the ``recency``
    baseline probes from the outside.
    """

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None) -> "HavenNoRecencyAdapter":
        return cls(config=replace(RetrievalConfig(), weight_recency=0.0))
