"""Deterministic ranking for Haven Ontology retrieval.

Implements the "Ranking" stage described in
``docs/architecture/ONTOLOGY_SPEC.md``, immediately downstream of
:mod:`~obsidian.ontology.candidate_assembler` ("Evidence Collection"):

::

    Candidate[]
        │
        ▼
    DeterministicRanker      (this module)
        │
        ▼
    RankedCandidate[]

This module has exactly one responsibility: turn a list of
:class:`~obsidian.ontology.retrieval_models.Candidate` objects into a
deterministically ordered list of
:class:`~obsidian.ontology.retrieval_models.RankedCandidate` objects, each
carrying a composite ``final_score`` and a complete ``score_breakdown``.

Explicitly out of scope
------------------------
* **No retrieval.** Candidates are consumed as given; nothing is fetched
  from a :class:`~obsidian.memory_engine.memory_store.MemoryStore` or
  :class:`~obsidian.ontology.concept_graph.ConceptGraph`.
* **No allocation.** ``RetrievalConfig.max_results`` is a context-budget
  concern for the future Slot Allocator stage and is never applied here —
  every candidate that clears ``minimum_candidate_score`` is returned.
* **No context building.** No prompt assembly or string formatting.
* **No mutation.** ``Candidate`` instances are frozen dataclasses and are
  never modified; each ``RankedCandidate`` wraps the original ``Candidate``
  unchanged.

Scoring equation
----------------
For a ``Candidate`` *c* with ``ko = c.knowledge_object`` and a reference
time *now* (see "Reference time" below), seven independent raw components
are computed, each normalised to ``[0.0, 1.0]``:

::

    activation_raw       = c.activation_score               (already 0-1)
    attachment_raw        = c.attachment_relevance            (already 0-1)
    keyword_overlap_raw    = c.keyword_overlap_score           (already 0-1)
    importance_raw          = ko.importance                     (already 0-1)
    confidence_raw           = ko.confidence                     (already 0-1)
    confirmation_raw         = ko.confirmation_count
                              / (ko.confirmation_count + 1)
    age_days                = max(0.0, (now - ko.valid_from).total_seconds() / 86400.0)
    recency_raw              = 1.0 / (1.0 + age_days / RECENCY_SCALE_DAYS)

Each raw component is combined with its matching
:class:`~obsidian.ontology.retrieval_config.RetrievalConfig` weight and
normalised by the sum of all seven weights, so the result is a weighted
*average*, not a weighted sum:

::

    W = weight_activation + weight_attachment_relevance + weight_keyword_overlap
        + weight_importance + weight_confidence + weight_recency
        + weight_confirmation_count

    contribution_x = (weight_x * raw_x) / W        for each of the seven x

    final_score = sum(contribution_x for x in the seven components)

``score_breakdown`` holds exactly these seven ``contribution_x`` values,
keyed ``"activation"``, ``"attachment_relevance"``, ``"keyword_overlap"``,
``"importance"``, ``"confidence"``, ``"recency"``, ``"confirmation_count"``.
By construction ``sum(score_breakdown.values()) == final_score``.

If ``W == 0.0`` (every weight configured to zero — a degenerate but
validly-constructed ``RetrievalConfig``), every contribution and
``final_score`` are ``0.0``; nothing distinguishes candidates and none
will clear a positive ``minimum_candidate_score``.

Because each raw component is already within ``[0.0, 1.0]`` and the
weights are non-negative, ``final_score`` is a convex combination of
values in ``[0.0, 1.0]`` and is therefore always in range; it is still
clamped defensively against floating-point rounding before being handed
to :class:`~obsidian.ontology.retrieval_models.RankedCandidate`, whose
constructor would otherwise raise on a value like ``1.0000000000000002``.

Reference time
--------------
Recency requires comparing ``ko.valid_from`` against "now". :meth:`rank`
accepts an optional keyword-only ``now`` so recency scoring is
reproducible in tests without monkeypatching the clock — the same
default-factory convention used by
:class:`~obsidian.manager_ai.models.KnowledgeObject.valid_from` and
:class:`~obsidian.ontology.retrieval_models.RetrievalTrace.created_at`.
When omitted, ``now`` defaults to ``datetime.utcnow()`` at call time.

Design decisions
-----------------
* **``attachment_relevance``, ``activation_score``, and
  ``keyword_overlap_score`` are independent of one another.** They measure
  evidence from three different sources — ``activation_score`` is
  *propagated* graph activation, ``attachment_relevance`` is a *direct*
  Concept-attachment link, ``keyword_overlap_score`` is surface-text token
  overlap with the query — and are scored and weighted separately
  (``weight_activation``, ``weight_attachment_relevance``,
  ``weight_keyword_overlap``), never multiplied or merged into a single
  term. This is the same principle that already separated the first two;
  adding a third evidence source extends it rather than revising it.
* **Weighted average, not weighted sum.** Dividing by the sum of weights
  keeps ``final_score`` bounded in ``[0.0, 1.0]`` regardless of how a
  caller configures the seven weights (they need not sum to 1.0).
* **``recency_raw`` and ``confirmation_raw`` need a normalising shape, but
  the task scopes weights to exactly the ``RetrievalConfig`` fields
  above — no additional tunable constant is added to ``RetrievalConfig``.**
  ``confirmation_raw = n / (n + 1)`` needs no constant at all (0 at
  ``n=0``, monotonically approaching 1). ``recency_raw`` uses one fixed,
  non-configurable module constant, ``RECENCY_SCALE_DAYS`` — the same
  pattern already used by :mod:`~obsidian.ontology.activation_spreader`,
  which hardcodes its tie-break rules directly in code rather than in
  ``RetrievalConfig``.
* **``minimum_candidate_score`` filtering happens here.**
  ``RetrievalConfig.minimum_candidate_score``'s own docstring assigns it
  this exact meaning ("Candidates with a final composite score below this
  value are dropped") and this module is the only stage that computes a
  final composite score, so applying the cutoff anywhere else would mean
  either duplicating the scoring formula or leaking it across a module
  boundary.
* **Ordering and tie-breaking are not reimplemented here.**
  :class:`~obsidian.ontology.retrieval_models.RankedCandidate` already
  defines a total order (descending ``final_score``, ties broken by
  ascending ``str(knowledge_object.id)``) via its rich comparison methods.
  :meth:`rank` simply calls Python's ``sorted()`` over the constructed
  ``RankedCandidate`` instances, so ties are guaranteed deterministic by
  that existing, independently-tested contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from obsidian.ontology.retrieval_config import RetrievalConfig
from obsidian.ontology.retrieval_models import Candidate, RankedCandidate

# Fixed, non-configurable recency scale (in days). Not part of
# RetrievalConfig — see "Design decisions" above. A one-week scale means a
# KnowledgeObject's recency contribution halves roughly every ~7 days of age,
# consistent with Haven's personal-knowledge use case (facts stay salient
# for days-to-weeks, not hours).
RECENCY_SCALE_DAYS: float = 7.0

_COMPONENT_NAMES = (
    "activation",
    "attachment_relevance",
    "keyword_overlap",
    "importance",
    "confidence",
    "recency",
    "confirmation_count",
)


class DeterministicRanker:
    """Ranks :class:`Candidate` objects into scored :class:`RankedCandidate` objects.

    Stateless and reusable across calls to :meth:`rank` with different
    candidate lists, configurations, or reference times.

    Examples
    --------
    >>> ranker = DeterministicRanker()
    >>> ranked = ranker.rank(candidates, RetrievalConfig())
    >>> ranked[0].final_score >= ranked[-1].final_score
    True
    """

    def rank(
        self,
        candidates: List[Candidate],
        config: RetrievalConfig,
        *,
        now: Optional[datetime] = None,
    ) -> List[RankedCandidate]:
        """Score and deterministically order *candidates*.

        Parameters
        ----------
        candidates : list[Candidate]
            Candidates to rank, typically the output of
            :meth:`~obsidian.ontology.candidate_assembler.CandidateAssembler.assemble`.
            Not mutated; never retrieved, hydrated, or re-fetched.
        config : RetrievalConfig
            Supplies the six scoring weights and ``minimum_candidate_score``.
            ``max_results`` is intentionally not applied — that is the
            Slot Allocator's responsibility.
        now : datetime, optional
            Reference time used to compute each candidate's recency
            component. Defaults to ``datetime.utcnow()`` when omitted.

        Returns
        -------
        list[RankedCandidate]
            One ``RankedCandidate`` per input candidate whose ``final_score``
            is at least ``config.minimum_candidate_score``, sorted by
            descending ``final_score`` with ties broken by ascending
            ``str(knowledge_object.id)`` (see
            :class:`~obsidian.ontology.retrieval_models.RankedCandidate`).
        """
        scored = self.score_all(candidates, config, now=now)
        return [rc for rc in scored if rc.final_score >= config.minimum_candidate_score]

    def score_all(
        self,
        candidates: List[Candidate],
        config: RetrievalConfig,
        *,
        now: Optional[datetime] = None,
    ) -> List[RankedCandidate]:
        """Score and deterministically order *every* candidate, unfiltered.

        Identical to :meth:`rank` except ``config.minimum_candidate_score``
        is never applied — every input candidate is represented in the
        output, including ones :meth:`rank` would drop. :meth:`rank` is
        defined in terms of this method (score, then filter), so the two
        can never disagree about a candidate's score or rank position.

        Exists for diagnostics: callers building a
        :class:`~obsidian.ontology.retrieval_models.RetrievalTrace` need to
        report a rejected candidate's score and rank alongside accepted
        ones, which :meth:`rank` alone cannot provide once it has filtered
        them out.

        Parameters
        ----------
        candidates : list[Candidate]
            Same contract as :meth:`rank`.
        config : RetrievalConfig
            Same contract as :meth:`rank`; ``minimum_candidate_score`` is
            read but not applied.
        now : datetime, optional
            Same contract as :meth:`rank`.

        Returns
        -------
        list[RankedCandidate]
            One ``RankedCandidate`` per input candidate, sorted by
            descending ``final_score`` with ties broken by ascending
            ``str(knowledge_object.id)``.
        """
        reference_time = now if now is not None else datetime.utcnow()
        ranked = [self._score(candidate, config, reference_time) for candidate in candidates]
        return sorted(ranked)

    @staticmethod
    def _score(
        candidate: Candidate,
        config: RetrievalConfig,
        now: datetime,
    ) -> RankedCandidate:
        """Compute the composite score and breakdown for a single candidate."""
        ko = candidate.knowledge_object

        age_days = max(0.0, (now - ko.valid_from).total_seconds() / 86400.0)

        raw: Dict[str, float] = {
            "activation": candidate.activation_score,
            "attachment_relevance": candidate.attachment_relevance,
            "keyword_overlap": candidate.keyword_overlap_score,
            "importance": ko.importance,
            "confidence": ko.confidence,
            "recency": 1.0 / (1.0 + age_days / RECENCY_SCALE_DAYS),
            "confirmation_count": ko.confirmation_count / (ko.confirmation_count + 1),
        }
        weights: Dict[str, float] = {
            "activation": config.weight_activation,
            "attachment_relevance": config.weight_attachment_relevance,
            "keyword_overlap": config.weight_keyword_overlap,
            "importance": config.weight_importance,
            "confidence": config.weight_confidence,
            "recency": config.weight_recency,
            "confirmation_count": config.weight_confirmation_count,
        }
        total_weight = sum(weights[name] for name in _COMPONENT_NAMES)

        if total_weight > 0.0:
            score_breakdown = {name: (weights[name] * raw[name]) / total_weight for name in _COMPONENT_NAMES}
        else:
            score_breakdown = {name: 0.0 for name in _COMPONENT_NAMES}

        final_score = sum(score_breakdown[name] for name in _COMPONENT_NAMES)
        final_score = min(1.0, max(0.0, final_score))

        return RankedCandidate(
            candidate=candidate,
            final_score=final_score,
            score_breakdown=score_breakdown,
        )
