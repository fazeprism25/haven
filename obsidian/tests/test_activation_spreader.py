"""Unit tests for obsidian.ontology.activation_spreader.ActivationSpreader.

Test groups
-----------
TestBasicPropagation      — single hop, formula correctness, all 8 relationship
                             type weights, seed passthrough.
TestMaxDepthBound          — concepts beyond max_depth are never reached.
TestActivationThreshold    — candidates below threshold are pruned and never
                             expanded further.
TestBidirectionalTraversal — propagation follows edges regardless of their
                             stored source/target direction.
TestCycleSafety             — cyclic graphs terminate and never corrupt the
                             seed's own recorded activation.
TestTieBreakActivation      — rule 1: highest activation wins.
TestTieBreakDepth           — rule 2: exact activation tie -> shallower wins.
TestTieBreakSourceSeed      — rule 3: activation and depth tie -> lexicographically
                             smaller source_seed wins.
TestSeedValidation          — non-zero seed depth raises ValueError.
TestUnknownSeedConcept      — a seed absent from the graph is passed through
                             without expansion.
TestGraphIsNeverMutated     — the ConceptGraph is read-only from this module's
                             perspective.
TestDeterminism              — repeated / reordered calls produce identical
                             results.
TestNoOutOfScopeImports      — module never imports KnowledgeObject/ranking code.
"""

from __future__ import annotations

from typing import List
from uuid import UUID, uuid4

import pytest

from obsidian.ontology.activation_spreader import ActivationSpreader
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.enums import OntologyRelationshipType
from obsidian.ontology.models import Concept, Relationship
from obsidian.ontology.retrieval_config import RetrievalConfig
from obsidian.ontology.retrieval_models import ActivatedConcept


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def concept(label: str) -> Concept:
    return Concept.from_label(label)


def graph_of(*concepts: Concept) -> ConceptGraph:
    g = ConceptGraph()
    for c in concepts:
        g.add_concept(c)
    return g


def link(
    graph: ConceptGraph,
    source: Concept,
    target: Concept,
    rel_type: OntologyRelationshipType = OntologyRelationshipType.RELATED_TO,
) -> Relationship:
    rel = Relationship.create(source.id, target.id, rel_type)
    graph.add_relationship(rel)
    return rel


def seed(c: Concept, activation: float = 1.0) -> ActivatedConcept:
    return ActivatedConcept(
        concept_id=c.id, activation_score=activation, activation_depth=0, source_seed=c.id
    )


def permissive_config(**overrides) -> RetrievalConfig:
    """A RetrievalConfig with a low threshold so pruning doesn't interfere
    with tests that aren't specifically about the threshold."""
    defaults = dict(activation_threshold=0.001, max_depth=5)
    defaults.update(overrides)
    return RetrievalConfig(**defaults)


# ---------------------------------------------------------------------------
# TestBasicPropagation
# ---------------------------------------------------------------------------


class TestBasicPropagation:
    def test_seed_is_present_in_output_unchanged(self) -> None:
        haven = concept("Haven")
        g = graph_of(haven)
        s = seed(haven)

        result = ActivationSpreader().spread([s], g, permissive_config())

        assert result[haven.id] == s

    def test_single_hop_formula(self) -> None:
        haven, claude = concept("Haven"), concept("Claude")
        g = graph_of(haven, claude)
        link(g, haven, claude, OntologyRelationshipType.USES)

        config = permissive_config(activation_decay=0.5)
        result = ActivationSpreader().spread([seed(haven)], g, config)

        expected = 1.0 * 0.5 * config.propagation_weight_uses
        activated = result[claude.id]
        assert activated.activation_score == pytest.approx(expected)
        assert activated.activation_depth == 1
        assert activated.source_seed == haven.id

    @pytest.mark.parametrize(
        "rel_type,attr",
        [
            (OntologyRelationshipType.IS_A, "propagation_weight_is_a"),
            (OntologyRelationshipType.PART_OF, "propagation_weight_part_of"),
            (OntologyRelationshipType.USES, "propagation_weight_uses"),
            (OntologyRelationshipType.DEPENDS_ON, "propagation_weight_depends_on"),
            (OntologyRelationshipType.CREATED_BY, "propagation_weight_created_by"),
            (OntologyRelationshipType.LOCATED_IN, "propagation_weight_located_in"),
            (OntologyRelationshipType.RELATED_TO, "propagation_weight_related_to"),
            (OntologyRelationshipType.SUPPORTS, "propagation_weight_supports"),
        ],
    )
    def test_each_relationship_type_uses_its_own_weight(self, rel_type, attr) -> None:
        a, b = concept("A"), concept("B")
        g = graph_of(a, b)
        link(g, a, b, rel_type)

        config = permissive_config(activation_decay=0.5)
        result = ActivationSpreader().spread([seed(a)], g, config)

        expected = 1.0 * 0.5 * getattr(config, attr)
        assert result[b.id].activation_score == pytest.approx(expected)

    def test_multiple_independent_seeds(self) -> None:
        a, b, c = concept("A"), concept("B"), concept("C")
        g = graph_of(a, b, c)
        link(g, a, b, OntologyRelationshipType.USES)

        result = ActivationSpreader().spread(
            [seed(a), seed(c, activation=0.4)], g, permissive_config()
        )

        assert result[a.id].activation_score == 1.0
        assert result[c.id].activation_score == 0.4
        assert b.id in result

    def test_empty_seeds_yields_empty_result(self) -> None:
        g = graph_of(concept("Lonely"))
        result = ActivationSpreader().spread([], g, permissive_config())
        assert result == {}

    def test_returns_plain_dict_of_activated_concepts(self) -> None:
        haven = concept("Haven")
        g = graph_of(haven)
        result = ActivationSpreader().spread([seed(haven)], g, permissive_config())
        assert isinstance(result, dict)
        for key, value in result.items():
            assert isinstance(key, UUID)
            assert isinstance(value, ActivatedConcept)


# ---------------------------------------------------------------------------
# TestMaxDepthBound
# ---------------------------------------------------------------------------


class TestMaxDepthBound:
    def _chain(self) -> tuple[ConceptGraph, list[Concept]]:
        nodes = [concept(f"N{i}") for i in range(4)]  # N0 - N1 - N2 - N3
        g = graph_of(*nodes)
        for i in range(3):
            link(g, nodes[i], nodes[i + 1], OntologyRelationshipType.USES)
        return g, nodes

    def test_stops_expanding_beyond_max_depth(self) -> None:
        g, nodes = self._chain()
        config = RetrievalConfig(
            activation_decay=0.5,
            activation_threshold=0.0001,
            max_depth=2,
            propagation_weight_uses=1.0,
        )
        result = ActivationSpreader().spread([seed(nodes[0])], g, config)

        assert nodes[0].id in result
        assert nodes[1].id in result
        assert nodes[2].id in result
        assert nodes[3].id not in result  # would require 3 hops

    def test_max_depth_one_reaches_only_direct_neighbors(self) -> None:
        g, nodes = self._chain()
        config = RetrievalConfig(
            activation_decay=0.5,
            activation_threshold=0.0001,
            max_depth=1,
            propagation_weight_uses=1.0,
        )
        result = ActivationSpreader().spread([seed(nodes[0])], g, config)

        assert set(result) == {nodes[0].id, nodes[1].id}

    def test_reached_concepts_never_exceed_max_depth(self) -> None:
        g, nodes = self._chain()
        config = RetrievalConfig(
            activation_decay=0.9,
            activation_threshold=0.0001,
            max_depth=2,
            propagation_weight_uses=1.0,
        )
        result = ActivationSpreader().spread([seed(nodes[0])], g, config)
        assert all(ac.activation_depth <= 2 for ac in result.values())


# ---------------------------------------------------------------------------
# TestActivationThreshold
# ---------------------------------------------------------------------------


class TestActivationThreshold:
    def test_candidate_below_threshold_is_dropped(self) -> None:
        a, b = concept("A"), concept("B")
        g = graph_of(a, b)
        link(g, a, b, OntologyRelationshipType.USES)

        config = RetrievalConfig(
            activation_decay=0.5,
            propagation_weight_uses=0.1,
            activation_threshold=0.5,  # 1.0 * 0.5 * 0.1 = 0.05 < 0.5
            max_depth=3,
        )
        result = ActivationSpreader().spread([seed(a)], g, config)

        assert a.id in result
        assert b.id not in result

    def test_pruned_candidate_never_propagates_further(self) -> None:
        nodes = [concept(f"N{i}") for i in range(3)]
        g = graph_of(*nodes)
        link(g, nodes[0], nodes[1], OntologyRelationshipType.USES)
        link(g, nodes[1], nodes[2], OntologyRelationshipType.USES)

        config = RetrievalConfig(
            activation_decay=0.5,
            propagation_weight_uses=1.0,
            activation_threshold=0.3,  # N1 = 0.5 (kept), N2 = 0.25 (dropped)
            max_depth=5,
        )
        result = ActivationSpreader().spread([seed(nodes[0])], g, config)

        assert nodes[1].id in result
        assert nodes[2].id not in result

    def test_threshold_boundary_is_inclusive_of_the_floor(self) -> None:
        a, b = concept("A"), concept("B")
        g = graph_of(a, b)
        link(g, a, b, OntologyRelationshipType.USES)

        config = RetrievalConfig(
            activation_decay=0.5,
            propagation_weight_uses=0.5,
            activation_threshold=0.25,  # exactly equal to candidate activation
            max_depth=3,
        )
        result = ActivationSpreader().spread([seed(a)], g, config)
        assert b.id in result
        assert result[b.id].activation_score == pytest.approx(0.25)


# ---------------------------------------------------------------------------
# TestBidirectionalTraversal
# ---------------------------------------------------------------------------


class TestBidirectionalTraversal:
    def test_propagation_follows_incoming_edges_too(self) -> None:
        a, b = concept("A"), concept("B")
        g = graph_of(a, b)
        link(g, a, b, OntologyRelationshipType.USES)  # A -> B stored direction

        # Seed the *target* of the stored edge; activation must still reach A.
        result = ActivationSpreader().spread([seed(b)], g, permissive_config())

        assert a.id in result
        assert result[a.id].activation_depth == 1


# ---------------------------------------------------------------------------
# TestCycleSafety
# ---------------------------------------------------------------------------


class TestCycleSafety:
    def test_cycle_terminates_and_preserves_seed(self) -> None:
        a, b, c = concept("A"), concept("B"), concept("C")
        g = graph_of(a, b, c)
        link(g, a, b, OntologyRelationshipType.USES)
        link(g, b, c, OntologyRelationshipType.USES)
        link(g, c, a, OntologyRelationshipType.USES)  # closes the cycle

        config = permissive_config(activation_decay=0.5, max_depth=10)
        result = ActivationSpreader().spread([seed(a)], g, config)

        # The seed's own recorded activation must never be overwritten by
        # activation looping back around the cycle.
        assert result[a.id].activation_score == 1.0
        assert result[a.id].activation_depth == 0


# ---------------------------------------------------------------------------
# TestTieBreakActivation
# ---------------------------------------------------------------------------


class TestTieBreakActivation:
    def test_higher_activation_path_wins(self) -> None:
        a, target = concept("A"), concept("Target")
        g = graph_of(a, target)
        # Two edges of different types from the same parent to the same
        # neighbour, within the same round -> different weights.
        link(g, a, target, OntologyRelationshipType.USES)  # weight 0.7 (default)
        link(g, a, target, OntologyRelationshipType.RELATED_TO)  # weight 0.3 (default)

        config = permissive_config(activation_decay=0.5)
        result = ActivationSpreader().spread([seed(a)], g, config)

        expected = 1.0 * config.activation_decay * config.propagation_weight_uses
        assert result[target.id].activation_score == pytest.approx(expected)


# ---------------------------------------------------------------------------
# TestTieBreakDepth
# ---------------------------------------------------------------------------


class TestTieBreakDepth:
    def test_exact_activation_tie_prefers_shallower_path(self) -> None:
        # Path 1 (1 hop): S1 --RELATED_TO(w=0.5)--> T   => 1.0*0.5*0.5 = 0.25, depth 1
        # Path 2 (2 hops): S2 --IS_A(w=1.0)--> M --IS_A(w=1.0)--> T
        #                  => 1.0*0.5*1.0 = 0.5 (M, depth1); 0.5*0.5*1.0 = 0.25 (T, depth2)
        # Both resolve to exactly 0.25 for T, at different depths.
        s1, s2, m, t = concept("S1"), concept("S2"), concept("M"), concept("T")
        g = graph_of(s1, s2, m, t)
        link(g, s1, t, OntologyRelationshipType.RELATED_TO)
        link(g, s2, m, OntologyRelationshipType.IS_A)
        link(g, m, t, OntologyRelationshipType.IS_A)

        config = RetrievalConfig(
            activation_decay=0.5,
            activation_threshold=0.0001,
            max_depth=5,
            propagation_weight_related_to=0.5,
            propagation_weight_is_a=1.0,
        )
        result = ActivationSpreader().spread([seed(s1), seed(s2)], g, config)

        winner = result[t.id]
        assert winner.activation_score == pytest.approx(0.25)
        assert winner.activation_depth == 1
        assert winner.source_seed == s1.id


# ---------------------------------------------------------------------------
# TestTieBreakSourceSeed
# ---------------------------------------------------------------------------


class TestTieBreakSourceSeed:
    def test_identical_activation_and_depth_prefers_smaller_seed_uuid(self) -> None:
        alpha, beta, target = concept("Alpha"), concept("Beta"), concept("Target")
        g = graph_of(alpha, beta, target)
        link(g, alpha, target, OntologyRelationshipType.RELATED_TO)
        link(g, beta, target, OntologyRelationshipType.RELATED_TO)

        config = permissive_config(activation_decay=0.5)
        result = ActivationSpreader().spread([seed(alpha), seed(beta)], g, config)

        winner = result[target.id]
        expected_seed = min(str(alpha.id), str(beta.id))
        assert str(winner.source_seed) == expected_seed
        assert winner.activation_depth == 1

    def test_symmetric_regardless_of_seed_list_order(self) -> None:
        alpha, beta, target = concept("Alpha"), concept("Beta"), concept("Target")
        g = graph_of(alpha, beta, target)
        link(g, alpha, target, OntologyRelationshipType.RELATED_TO)
        link(g, beta, target, OntologyRelationshipType.RELATED_TO)

        config = permissive_config(activation_decay=0.5)
        forward = ActivationSpreader().spread([seed(alpha), seed(beta)], g, config)
        reverse = ActivationSpreader().spread([seed(beta), seed(alpha)], g, config)

        assert forward[target.id] == reverse[target.id]


# ---------------------------------------------------------------------------
# TestSeedValidation
# ---------------------------------------------------------------------------


class TestSeedValidation:
    def test_nonzero_seed_depth_raises(self) -> None:
        haven = concept("Haven")
        g = graph_of(haven)
        bad_seed = ActivatedConcept(
            concept_id=haven.id, activation_score=1.0, activation_depth=1, source_seed=haven.id
        )
        with pytest.raises(ValueError, match="activation_depth == 0"):
            ActivationSpreader().spread([bad_seed], g, permissive_config())


# ---------------------------------------------------------------------------
# TestUnknownSeedConcept
# ---------------------------------------------------------------------------


class TestUnknownSeedConcept:
    def test_seed_not_in_graph_passes_through_without_expansion(self) -> None:
        g = ConceptGraph()  # empty graph
        floating_id = uuid4()
        floating_seed = ActivatedConcept(
            concept_id=floating_id, activation_score=1.0, activation_depth=0, source_seed=floating_id
        )
        result = ActivationSpreader().spread([floating_seed], g, permissive_config())
        assert result == {floating_id: floating_seed}


# ---------------------------------------------------------------------------
# TestGraphIsNeverMutated
# ---------------------------------------------------------------------------


class TestGraphIsNeverMutated:
    def test_query_results_unchanged_after_spread(self) -> None:
        a, b, c = concept("A"), concept("B"), concept("C")
        g = graph_of(a, b, c)
        link(g, a, b, OntologyRelationshipType.USES)
        link(g, b, c, OntologyRelationshipType.SUPPORTS)

        ids = [a.id, b.id, c.id]
        before_rels = {cid: g.relationships(cid) for cid in ids}
        before_neighbors = {cid: g.neighbors(cid) for cid in ids}
        before_has = {cid: g.has_concept(cid) for cid in ids}

        ActivationSpreader().spread(
            [seed(a)], g, permissive_config(activation_decay=0.5)
        )

        after_rels = {cid: g.relationships(cid) for cid in ids}
        after_neighbors = {cid: g.neighbors(cid) for cid in ids}
        after_has = {cid: g.has_concept(cid) for cid in ids}

        assert before_rels == after_rels
        assert before_neighbors == after_neighbors
        assert before_has == after_has


# ---------------------------------------------------------------------------
# TestDeterminism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_repeated_calls_are_identical(self) -> None:
        a, b, c = concept("A"), concept("B"), concept("C")
        g = graph_of(a, b, c)
        link(g, a, b, OntologyRelationshipType.USES)
        link(g, b, c, OntologyRelationshipType.PART_OF)

        config = permissive_config(activation_decay=0.6)
        spreader = ActivationSpreader()

        first = spreader.spread([seed(a)], g, config)
        second = spreader.spread([seed(a)], g, config)

        assert first == second

    def test_shuffled_seed_order_is_irrelevant(self) -> None:
        a, b, c = concept("A"), concept("B"), concept("C")
        g = graph_of(a, b, c)
        link(g, a, c, OntologyRelationshipType.USES)
        link(g, b, c, OntologyRelationshipType.USES)

        config = permissive_config(activation_decay=0.5)
        seeds: List[ActivatedConcept] = [seed(a, 0.9), seed(b, 0.9)]

        forward = ActivationSpreader().spread(seeds, g, config)
        backward = ActivationSpreader().spread(list(reversed(seeds)), g, config)

        assert forward == backward


# ---------------------------------------------------------------------------
# TestNoOutOfScopeImports
# ---------------------------------------------------------------------------


class TestNoOutOfScopeImports:
    def test_module_does_not_import_knowledge_or_ranking_code(self) -> None:
        import obsidian.ontology.activation_spreader as module
        from pathlib import Path

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "obsidian.manager_ai" not in source
        assert "EvidenceCollector" not in source
        assert "RankedCandidate" not in source
