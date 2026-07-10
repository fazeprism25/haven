"""Unit tests for obsidian.ontology.ontology_pipeline.OntologyPipeline.

No test file existed for this module before (it was previously exercised
only indirectly via server tests). Added alongside the Write Inspector
feature, whose ``process_with_trace`` is the reason a rejected proposal's
``rejection_reason`` needs test coverage for the first time.

Test groups
-----------
TestProcess           -- process() behaves as documented (paths written,
                         empty on no detectable concepts).
TestProcessWithTrace  -- process_with_trace() returns the same paths as
                         process(), plus every ValidationResult (including
                         rejected ones on a repeat call).
"""

from __future__ import annotations

from pathlib import Path

from obsidian.manager_ai.models import KnowledgeObject
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.ontology_pipeline import OntologyPipeline


def make_knowledge(fact: str = "Haven uses Claude") -> KnowledgeObject:
    return KnowledgeObject(canonical_fact=fact)


# ---------------------------------------------------------------------------
# TestProcess
# ---------------------------------------------------------------------------


class TestProcess:
    def test_writes_concept_files_for_detected_concepts(self, tmp_path: Path) -> None:
        graph = ConceptGraph()
        pipeline = OntologyPipeline(graph, tmp_path)

        paths = pipeline.process(make_knowledge("Haven uses Claude"))

        assert len(paths) == 2  # Haven + Claude
        assert all(p.exists() for p in paths)

    def test_no_detectable_concepts_returns_empty(self, tmp_path: Path) -> None:
        graph = ConceptGraph()
        pipeline = OntologyPipeline(graph, tmp_path)

        paths = pipeline.process(make_knowledge("the quick brown fox"))

        assert paths == []


# ---------------------------------------------------------------------------
# TestProcessWithTrace
# ---------------------------------------------------------------------------


class TestProcessWithTrace:
    def test_paths_match_process(self, tmp_path: Path) -> None:
        graph_a = ConceptGraph()
        graph_b = ConceptGraph()
        knowledge = make_knowledge("Haven uses Claude")

        paths_from_process = OntologyPipeline(graph_a, tmp_path / "a").process(
            knowledge
        )
        paths_from_trace, _results = OntologyPipeline(
            graph_b, tmp_path / "b"
        ).process_with_trace(knowledge)

        assert len(paths_from_trace) == len(paths_from_process)

    def test_first_call_all_accepted(self, tmp_path: Path) -> None:
        graph = ConceptGraph()
        pipeline = OntologyPipeline(graph, tmp_path)

        _paths, results = pipeline.process_with_trace(make_knowledge("Haven uses Claude"))

        assert results  # at least one proposal (two CREATE_CONCEPT + relationship/attachments)
        assert all(r.accepted for r in results)
        assert all(r.rejection_reason == "" for r in results)

    def test_repeat_call_with_identical_knowledge_proposes_nothing_new(
        self, tmp_path: Path
    ) -> None:
        # Verified empirically rather than assumed: OntologyManager itself
        # inspects live graph state and simply does not re-propose
        # CREATE_CONCEPT/CREATE_RELATIONSHIP/ATTACH_KNOWLEDGE_OBJECT for
        # state that already exists (see ontology_manager.py's "avoid
        # redundant proposals" responsibility) -- so a byte-identical
        # repeat call never reaches the Validator's duplicate-rejection
        # path at all; it surfaces as zero proposals, not a rejection.
        graph = ConceptGraph()
        pipeline = OntologyPipeline(graph, tmp_path)
        knowledge = make_knowledge("Haven uses Claude")

        pipeline.process_with_trace(knowledge)  # first call: concepts created
        paths, results = pipeline.process_with_trace(knowledge)  # second: no-op

        assert paths == []
        assert results == []

    def test_rejection_reason_is_visible_when_validator_does_reject(
        self, tmp_path: Path
    ) -> None:
        # Exercises the Validator's duplicate-rejection path directly
        # (OntologyManager never produces this shape on its own -- see the
        # test above) so process_with_trace's plumbing of rejected
        # ValidationResults is covered by something that actually happens,
        # not just asserted against a mock.
        from obsidian.ontology.identity import concept_id
        from obsidian.ontology.models import Concept, OntologyProposal
        from obsidian.ontology.enums import ProposalType
        from obsidian.ontology.ontology_validator import OntologyValidator

        graph = ConceptGraph()
        graph.add_concept(Concept.from_label(label="Haven"))
        duplicate_proposal = OntologyProposal(
            proposal_type=ProposalType.CREATE_CONCEPT,
            payload={"label": "Haven"},
        )

        results = OntologyValidator().validate([duplicate_proposal], graph)

        assert len(results) == 1
        assert results[0].accepted is False
        assert results[0].rejection_reason

    def test_no_detectable_concepts_returns_empty_paths_and_results(
        self, tmp_path: Path
    ) -> None:
        graph = ConceptGraph()
        pipeline = OntologyPipeline(graph, tmp_path)

        paths, results = pipeline.process_with_trace(
            make_knowledge("the quick brown fox")
        )

        assert paths == []
        assert results == []
