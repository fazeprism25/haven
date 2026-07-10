"""Ontology Pipeline — integrates the ontology stages into the write path.

Connects the completed ontology components in the order specified by the
Haven architecture:

    KnowledgeObject
        ↓ OntologyManager   (produce proposals)
        ↓ OntologyValidator (filter to accepted proposals only)
        ↓ ConceptGraph      (apply accepted mutations)
        ↓ ConceptWriter     (persist affected concept files)

This module is the **only** place that mutates the :class:`ConceptGraph`.
All other ontology components are either read-only with respect to the
graph (Manager, Validator) or write to the filesystem only
(:class:`~obsidian.ontology.concept_writer.ConceptWriter`).

Design constraints
------------------
* Preserves existing Manager AI behaviour — the
  :class:`~obsidian.manager_ai.pipeline.ManagerPipeline` is unchanged.
* Does not touch retrieval — no candidate retriever or ranking logic.
* No activation spreading.
* Fully deterministic given the same :class:`ConceptGraph` state and the
  same :class:`~obsidian.manager_ai.models.KnowledgeObject` input.
  (The ``updated_at`` timestamp written into concept files is the only
  non-deterministic field; it reflects wall-clock time of the write.)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Set, Tuple
from uuid import UUID

from obsidian.manager_ai.models import KnowledgeObject
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.concept_writer import ConceptWriter
from obsidian.ontology.enums import OntologyRelationshipType, ProposalType
from obsidian.ontology.identity import concept_id as _concept_id
from obsidian.ontology.models import (
    Attachment,
    Concept,
    OntologyProposal,
    Relationship,
)
from obsidian.ontology.ontology_manager import OntologyManager
from obsidian.ontology.ontology_validator import OntologyValidator, ValidationResult


class OntologyPipeline:
    """Orchestrate the full ontology write flow for a single
    :class:`~obsidian.manager_ai.models.KnowledgeObject`.

    The pipeline is **stateful** — it holds a shared :class:`ConceptGraph`
    instance that accumulates graph mutations across multiple calls.  Pass
    the same pipeline instance for all knowledge objects that should share
    the same ontology.

    Parameters
    ----------
    graph : ConceptGraph
        The live concept graph.  Mutated in place by each call to
        :meth:`process`.
    concept_dir : Path
        Directory where concept Markdown files are written.  Created on
        first write if it does not exist.

    Examples
    --------
    >>> from pathlib import Path
    >>> from obsidian.ontology.concept_graph import ConceptGraph
    >>> from obsidian.manager_ai.models import KnowledgeObject
    >>> graph = ConceptGraph()
    >>> pipeline = OntologyPipeline(graph, Path("/tmp/concepts"))
    >>> ko = KnowledgeObject(canonical_fact="Haven uses Claude")
    >>> paths = pipeline.process(ko)           # writes concept files
    >>> len(paths)                             # Haven + Claude
    2
    """

    def __init__(self, graph: ConceptGraph, concept_dir: Path) -> None:
        self._graph = graph
        self._concept_dir = Path(concept_dir)
        self._manager = OntologyManager()
        self._validator = OntologyValidator()
        self._writer = ConceptWriter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(self, knowledge: KnowledgeObject) -> List[Path]:
        """Run the full ontology pipeline for *knowledge*.

        Steps
        -----
        1. :class:`OntologyManager` generates proposals from
           ``knowledge.canonical_fact``.
        2. :class:`OntologyValidator` filters to accepted proposals only
           (rejecting duplicates and proposals that violate graph
           invariants).
        3. Accepted proposals are applied to :attr:`graph`.
        4. Every concept touched by the accepted proposals is (re)written
           to disk via :class:`~obsidian.ontology.concept_writer.ConceptWriter`.

        Parameters
        ----------
        knowledge : KnowledgeObject
            The knowledge object to integrate into the ontology.

        Returns
        -------
        list[Path]
            Absolute paths of concept files written or updated.  Empty
            when *knowledge* contains no detectable concept labels or when
            all proposals are rejected as duplicates.
        """
        paths, _results = self.process_with_trace(knowledge)
        return paths

    def process_with_trace(
        self, knowledge: KnowledgeObject
    ) -> Tuple[List[Path], List[ValidationResult]]:
        """Run :meth:`process`, additionally returning every validation result.

        Contains exactly the same logic as :meth:`process` -- the only
        difference is returning every :class:`ValidationResult` (accepted
        *and* rejected), rather than discarding the rejected ones after
        filtering to ``accepted``, for a caller building a
        :class:`~obsidian.ontology.write_trace_models.WriteTrace`. This is
        the first place a rejected ontology proposal's ``rejection_reason``
        becomes visible outside this method. :meth:`process` is a thin
        wrapper around this method (see its body) so both share one
        implementation.
        """
        proposals = self._manager.propose(knowledge, self._graph)
        if not proposals:
            return [], []

        results = self._validator.validate(proposals, self._graph)
        accepted = [r.proposal for r in results if r.accepted]
        if not accepted:
            return [], results

        self._apply_to_graph(accepted)
        return self._write_affected_concepts(accepted), results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_to_graph(self, accepted: List[OntologyProposal]) -> None:
        """Apply *accepted* proposals to the graph in list order.

        Proposals must already be in dependency order (concepts before
        relationships, relationships before attachments).  The
        :class:`OntologyManager` always produces them in this order; the
        :class:`OntologyValidator` preserves it.
        """
        for p in accepted:
            if p.proposal_type == ProposalType.CREATE_CONCEPT:
                concept = Concept.from_label(
                    label=p.payload["label"],
                    aliases=tuple(p.payload.get("aliases", [])),
                    description=p.payload.get("description", ""),
                )
                self._graph.add_concept(concept)

            elif p.proposal_type == ProposalType.CREATE_RELATIONSHIP:
                rel = Relationship.create(
                    UUID(p.payload["source_id"]),
                    UUID(p.payload["target_id"]),
                    OntologyRelationshipType(p.payload["relationship_type"]),
                    confidence=float(p.payload["confidence"]),
                )
                self._graph.add_relationship(rel)

            elif p.proposal_type == ProposalType.ATTACH_KNOWLEDGE_OBJECT:
                att = Attachment.create(
                    UUID(p.payload["knowledge_object_id"]),
                    UUID(p.payload["concept_id"]),
                    relevance=float(p.payload["relevance"]),
                )
                self._graph.add_attachment(att)

    def _write_affected_concepts(self, accepted: List[OntologyProposal]) -> List[Path]:
        """Write concept files for every concept touched by *accepted* proposals.

        Collects the concept UUIDs referenced by all accepted proposals,
        then writes one file per unique concept.  Files are written in
        ascending UUID-string order for a deterministic result.

        The ``updated_at`` timestamp is taken once at the start of this
        call and applied uniformly to all concept files written in the
        same batch.
        """
        concept_ids: Set[UUID] = set()
        for p in accepted:
            if p.proposal_type == ProposalType.CREATE_CONCEPT:
                concept_ids.add(_concept_id(p.payload["label"]))
            elif p.proposal_type == ProposalType.CREATE_RELATIONSHIP:
                concept_ids.add(UUID(p.payload["source_id"]))
                concept_ids.add(UUID(p.payload["target_id"]))
            elif p.proposal_type == ProposalType.ATTACH_KNOWLEDGE_OBJECT:
                concept_ids.add(UUID(p.payload["concept_id"]))

        now = datetime.utcnow()
        paths: List[Path] = []
        for cid in sorted(concept_ids, key=str):
            concept = self._graph.get_concept(cid)
            rels = self._graph.relationships(cid)
            atts = self._graph.attachments_for_concept(cid)
            path = self._writer.write(
                concept,
                self._concept_dir,
                relationships=rels,
                attachments=atts,
                updated_at=now,
            )
            paths.append(path)

        return paths
