"""Activation spreading for the Haven Ontology Concept Graph.

Implements the "Graph Expansion" stage described in
``docs/architecture/ONTOLOGY_SPEC.md``: starting from a set of seed
Concepts, activation propagates outward across :class:`Relationship` edges,
decaying with distance and with edge type, until it falls below a
configurable threshold or a configurable maximum hop count is reached.

This module has exactly one responsibility: turn seed
:class:`~obsidian.ontology.retrieval_models.ActivatedConcept` objects plus a
:class:`~obsidian.ontology.concept_graph.ConceptGraph` into a
``Dict[UUID, ActivatedConcept]`` of every Concept reached. It does **not**
collect evidence, retrieve ``KnowledgeObjects``, or rank anything — those are
the responsibilities of :mod:`~obsidian.ontology.evidence_collector` and the
future ranking stage.

Algorithm
---------
Spreading proceeds in rounds, one round per hop, bounded by
``config.max_depth``. Round 0 is seed initialisation; round *r* (for
``1 <= r <= max_depth``) expands every Concept that was newly accepted into
the ``best`` table during round *r - 1* (the "frontier").

For a frontier Concept *p* with recorded activation ``best[p]`` and every
:class:`~obsidian.ontology.models.Relationship` touching *p*, a candidate is
generated for the Concept at the other end of the edge::

    candidate.activation_score = best[p].activation_score
                                  * config.activation_decay
                                  * propagation_weight(relationship.relationship_type)
    candidate.activation_depth = best[p].activation_depth + 1
    candidate.source_seed      = best[p].source_seed

Candidates below ``config.activation_threshold`` are discarded immediately
and never enter the frontier for the following round.

This is a **bounded relaxation**, not a naive "visit each Concept once"
breadth-first search: a Concept may be updated more than once across
rounds if a later, deeper candidate turns out to have a *higher* activation
score than the one currently recorded for it (this is possible because
different edges carry different propagation weights — a two-hop path
through strong edges can out-score a one-hop path through a weak one).
Because ``activation_decay`` is strictly less than 1 and every propagation
weight is at most 1, activation strictly decreases along any single path
as it gets longer, which guarantees the relaxation converges within
``max_depth`` rounds; each round only ever re-expands Concepts that were
genuinely improved in the previous round, so the process is still a
level-synchronous, breadth-first expansion — it just does not lock a
Concept's value in on first contact.

Tie-breaking rules (in priority order)
---------------------------------------
Whenever a newly generated candidate and an existing table entry refer to
the same Concept, the winner is chosen by, in order:

1. **Higher activation score wins.** This is the primary signal: an
   ActivatedConcept always represents the strongest evidence found for
   that Concept so far.
2. **If activation scores tie *exactly*** (bit-for-bit float equality — no
   epsilon tolerance, per spec), **the shallower path wins** (smaller
   ``activation_depth``). A shorter explanation for the same strength of
   evidence is preferred.
3. **If activation *and* depth both tie**, **the lexicographically smaller
   ``source_seed`` UUID (compared as its string form) wins.** This can only
   happen when two *simultaneous* candidates are generated for the same
   Concept within the same round (round-processing is level-synchronous,
   so cross-round ties in `activation_depth` cannot occur once rule 2 has
   already been applied) — e.g. two different seed Concepts each propagate
   an edge of the same type and weight to a shared neighbour, producing
   numerically identical activation scores at the same depth. The
   comparison is purely a deterministic, arbitrary tie-breaker with no
   semantic meaning beyond making the result reproducible.

This three-level comparison is applied uniformly both when two seeds map to
the same Concept (round 0) and when two propagated candidates collide
within the same round; see :func:`_is_better`.

Design decisions
-----------------
* **Seeds must have ``activation_depth == 0``.** This is the definition of
  a seed per
  :class:`~obsidian.ontology.retrieval_models.ActivatedConcept`'s own
  docstring ("0 means the concept was itself a seed"), and it is the
  invariant that keeps each round's frontier depth-homogeneous, which in
  turn is what makes tie rule 3's "only within a round" scope hold. A seed
  with nonzero depth would violate that contract, so :meth:`spread` raises
  ``ValueError`` rather than silently reinterpreting it as an offset.
* **Propagation is undirected.** A :class:`Relationship` is a directed
  edge, but "activation spreading" is a symmetric process over the graph's
  connectivity, not a simulation of the relationship's semantic direction.
  This mirrors :meth:`ConceptGraph.neighbors`, which already treats
  outgoing and incoming edges identically for traversal purposes.
* **Only the propagation-weight-per-type is applied, not
  ``Relationship.confidence``.** The task's input list is explicit:
  activation decay, relationship propagation weights, and the activation
  threshold. ``Relationship.confidence`` is a different axis (how sure the
  ontology is that the edge itself is correct) and folding it into the
  activation formula would be introducing a scoring dimension the spec did
  not ask for.
* **The graph is never mutated.** Only read-only query methods
  (:meth:`ConceptGraph.relationships`) are called.
"""

from __future__ import annotations

from typing import Dict, Iterable, List
from uuid import UUID

from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.enums import OntologyRelationshipType
from obsidian.ontology.retrieval_config import RetrievalConfig
from obsidian.ontology.retrieval_models import ActivatedConcept

# ---------------------------------------------------------------------------
# Relationship type -> RetrievalConfig propagation-weight attribute
# ---------------------------------------------------------------------------

_PROPAGATION_WEIGHT_ATTR: Dict[OntologyRelationshipType, str] = {
    OntologyRelationshipType.IS_A: "propagation_weight_is_a",
    OntologyRelationshipType.PART_OF: "propagation_weight_part_of",
    OntologyRelationshipType.USES: "propagation_weight_uses",
    OntologyRelationshipType.DEPENDS_ON: "propagation_weight_depends_on",
    OntologyRelationshipType.CREATED_BY: "propagation_weight_created_by",
    OntologyRelationshipType.LOCATED_IN: "propagation_weight_located_in",
    OntologyRelationshipType.RELATED_TO: "propagation_weight_related_to",
    OntologyRelationshipType.SUPPORTS: "propagation_weight_supports",
}


def _propagation_weight(
    relationship_type: OntologyRelationshipType, config: RetrievalConfig
) -> float:
    """Return the configured propagation weight for *relationship_type*."""
    return getattr(config, _PROPAGATION_WEIGHT_ATTR[relationship_type])


def _is_better(candidate: ActivatedConcept, existing: ActivatedConcept) -> bool:
    """Return ``True`` if *candidate* should replace *existing* for the same Concept.

    Implements the three tie-breaking rules, in priority order:
    activation score (higher wins), then activation depth (shallower
    wins), then ``source_seed`` (lexicographically smaller string wins).
    """
    if candidate.activation_score != existing.activation_score:
        return candidate.activation_score > existing.activation_score
    if candidate.activation_depth != existing.activation_depth:
        return candidate.activation_depth < existing.activation_depth
    return str(candidate.source_seed) < str(existing.source_seed)


def _merge(table: Dict[UUID, ActivatedConcept], candidate: ActivatedConcept) -> bool:
    """Insert *candidate* into *table* if it wins over any existing entry.

    Returns
    -------
    bool
        ``True`` if *table* was updated (either a new entry was inserted or
        an existing one was replaced per :func:`_is_better`).
    """
    existing = table.get(candidate.concept_id)
    if existing is None or _is_better(candidate, existing):
        table[candidate.concept_id] = candidate
        return True
    return False


class ActivationSpreader:
    """Propagates activation outward from seed Concepts across a ConceptGraph.

    The spreader is stateless and can be reused across multiple calls to
    :meth:`spread` with different seeds, graphs, or configurations.

    Examples
    --------
    >>> spreader = ActivationSpreader()
    >>> activated = spreader.spread(seeds, graph, RetrievalConfig())
    >>> activated[haven_concept_id].activation_score
    1.0
    """

    def spread(
        self,
        seeds: Iterable[ActivatedConcept],
        graph: ConceptGraph,
        config: RetrievalConfig,
    ) -> Dict[UUID, ActivatedConcept]:
        """Spread activation from *seeds* across *graph*, bounded by *config*.

        Parameters
        ----------
        seeds : Iterable[ActivatedConcept]
            Starting points for activation spreading. Every seed must have
            ``activation_depth == 0``.
        graph : ConceptGraph
            Graph to traverse. Never mutated.
        config : RetrievalConfig
            Supplies ``max_depth``, ``activation_decay``,
            ``activation_threshold``, and the per-relationship-type
            propagation weights.

        Returns
        -------
        dict[UUID, ActivatedConcept]
            Every Concept reached, keyed by Concept id, each mapped to its
            single winning :class:`ActivatedConcept` per the tie-break
            rules documented on this module.

        Raises
        ------
        ValueError
            If any seed has ``activation_depth != 0``.
        """
        seed_list: List[ActivatedConcept] = list(seeds)
        for seed in seed_list:
            if seed.activation_depth != 0:
                raise ValueError(
                    "seed concepts must have activation_depth == 0; "
                    f"got {seed.activation_depth} for concept_id={seed.concept_id}"
                )

        best: Dict[UUID, ActivatedConcept] = {}
        for seed in sorted(seed_list, key=lambda s: str(s.concept_id)):
            _merge(best, seed)

        frontier: Dict[UUID, ActivatedConcept] = dict(best)

        for _round in range(config.max_depth):
            if not frontier:
                break

            candidates: Dict[UUID, ActivatedConcept] = {}
            for concept_id in sorted(frontier, key=str):
                parent = frontier[concept_id]
                for relationship in graph.relationships(concept_id):
                    neighbor_id = (
                        relationship.target_id
                        if relationship.source_id == concept_id
                        else relationship.source_id
                    )
                    weight = _propagation_weight(
                        relationship.relationship_type, config
                    )
                    activation_score = (
                        parent.activation_score * config.activation_decay * weight
                    )
                    if activation_score < config.activation_threshold:
                        continue
                    candidate = ActivatedConcept(
                        concept_id=neighbor_id,
                        activation_score=activation_score,
                        activation_depth=parent.activation_depth + 1,
                        source_seed=parent.source_seed,
                    )
                    _merge(candidates, candidate)

            next_frontier: Dict[UUID, ActivatedConcept] = {}
            for concept_id, candidate in candidates.items():
                if _merge(best, candidate):
                    next_frontier[concept_id] = best[concept_id]

            frontier = next_frontier

        return best
