"""Ontology Manager — orchestrates OntologyProposal generation.

Accepts a :class:`~obsidian.manager_ai.models.KnowledgeObject` and the
current :class:`~obsidian.ontology.concept_graph.ConceptGraph`, returns a
list of :class:`~obsidian.ontology.models.OntologyProposal` objects
describing what should change.  **Never mutates the graph.**

Responsibilities
----------------
* Delegate concept detection to :class:`~obsidian.ontology.concept_detector.ConceptDetector`.
* Inspect live graph state to avoid redundant proposals.
* Produce ``CREATE_CONCEPT``, ``CREATE_RELATIONSHIP``, and
  ``ATTACH_KNOWLEDGE_OBJECT`` proposals in dependency order.

Non-responsibilities (enforced by design)
-----------------------------------------
* No graph mutation — zero calls to ``add_*`` methods.
* No proposal validation — that is Phase 2 (OntologyValidator).
* No semantic / vector retrieval.
* No Markdown I/O.
* No activation spreading.
"""

from __future__ import annotations

from typing import List, Set
from uuid import UUID

from obsidian.manager_ai.models import KnowledgeObject
from obsidian.ontology.category_taxonomy import lookup_category
from obsidian.ontology.concept_detector import ConceptDetector
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.enums import OntologyRelationshipType, ProposalType
from obsidian.ontology.identity import (
    concept_id as _concept_id,
    relationship_id as _relationship_id,
)
from obsidian.ontology.models import OntologyProposal


class OntologyManager:
    """Orchestrate :class:`~obsidian.ontology.models.OntologyProposal` generation.

    The manager is **stateless** — instantiate once and reuse freely across
    multiple calls.  All graph access is read-only.

    Proposal ordering
    -----------------
    Proposals are returned in dependency order so a sequential applier can
    process them top-to-bottom without look-ahead:

    1. ``CREATE_CONCEPT`` — new concepts must exist before they can be linked
       or attached.
    2. ``CREATE_RELATIONSHIP`` — edges between the (now-created) concepts.
    3. ``ATTACH_KNOWLEDGE_OBJECT`` — link the knowledge object to each concept.

    Relationship semantics
    ----------------------
    Co-detected concepts within the same :class:`KnowledgeObject` are linked
    via :attr:`~obsidian.ontology.enums.OntologyRelationshipType.RELATED_TO`
    as the lowest-assumption edge type.  The direction is chosen by
    lexicographic UUID comparison (smaller UUID is always the source) so that
    the same pair always produces the same directed edge regardless of
    detection order.

    A detected label that also appears in the curated
    :data:`~obsidian.ontology.category_taxonomy.CATEGORY_TAXONOMY` additionally
    gets an :attr:`~obsidian.ontology.enums.OntologyRelationshipType.IS_A` edge
    to its category (e.g. ``PostgreSQL IS_A Database``), with the category
    concept created on first use. See
    ``docs/architecture/ENTITY_CAT_INVESTIGATION.md`` for the scope and
    rationale of this curated bridge — it is separate from, and additive to,
    the ``RELATED_TO`` co-occurrence linking above.
    """

    def __init__(self) -> None:
        self._detector = ConceptDetector()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def propose(
        self,
        knowledge: KnowledgeObject,
        graph: ConceptGraph,
    ) -> List[OntologyProposal]:
        """Return proposals needed to integrate *knowledge* into *graph*.

        Parameters
        ----------
        knowledge : KnowledgeObject
            The knowledge object to analyse.  Only ``canonical_fact``,
            ``id``, ``confidence``, and ``importance`` are read.
        graph : ConceptGraph
            Current state of the concept graph.  Only query methods are
            called; no mutation methods are invoked.

        Returns
        -------
        list[OntologyProposal]
            Ordered proposals in dependency order.  Empty list when no
            concept labels are detected in ``knowledge.canonical_fact``.
        """
        labels = self._detector.detect(knowledge)
        if not labels:
            return []

        # Derive deterministic (label, concept_uuid) pairs — order matches
        # the detector's output order, which is deterministic.
        concept_pairs: List[tuple[str, UUID]] = [
            (label, _concept_id(label)) for label in labels
        ]

        proposals: List[OntologyProposal] = []

        # Phase 1 — CREATE_CONCEPT for every label absent from the graph.
        for label, cid in concept_pairs:
            if not graph.has_concept(cid):
                proposals.append(
                    OntologyProposal(
                        proposal_type=ProposalType.CREATE_CONCEPT,
                        payload={
                            "label": label,
                            "aliases": [],
                            "description": "",
                        },
                        reason=f"Detected new concept '{label}' not present in graph",
                    )
                )

        # Phase 1.5 — curated ENTITY_CAT -> CATEGORY IS_A bridge. For each
        # detected label present in the curated taxonomy, ensure its category
        # Concept exists (created once per batch, even if several detected
        # labels share a category, e.g. GPT/Qwen -> Model) and propose an
        # IS_A edge from the instance to the category. See
        # docs/architecture/ENTITY_CAT_INVESTIGATION.md Task 5 for scope.
        category_links: List[tuple[str, UUID, str, UUID]] = []
        proposed_category_ids: Set[UUID] = set()
        for label, cid in concept_pairs:
            entry = lookup_category(label)
            if entry is None:
                continue
            category_cid = _concept_id(entry.label)
            if (
                not graph.has_concept(category_cid)
                and category_cid not in proposed_category_ids
            ):
                proposals.append(
                    OntologyProposal(
                        proposal_type=ProposalType.CREATE_CONCEPT,
                        payload={
                            "label": entry.label,
                            "aliases": list(entry.aliases),
                            "description": "",
                        },
                        reason=(
                            f"Curated category for detected instance '{label}' "
                            "(ENTITY_CAT ontology bridge)"
                        ),
                    )
                )
                proposed_category_ids.add(category_cid)
            category_links.append((label, cid, entry.label, category_cid))

        existing_is_a_ids = self._existing_relationship_ids(
            graph,
            [cid for _, cid, _, _ in category_links]
            + [category_cid for _, _, _, category_cid in category_links],
        )
        for label, cid, category_label, category_cid in category_links:
            rid = _relationship_id(cid, category_cid, OntologyRelationshipType.IS_A.value)
            if rid in existing_is_a_ids:
                continue
            proposals.append(
                OntologyProposal(
                    proposal_type=ProposalType.CREATE_RELATIONSHIP,
                    payload={
                        "source_id": str(cid),
                        "target_id": str(category_cid),
                        "relationship_type": OntologyRelationshipType.IS_A.value,
                        "confidence": 1.0,
                    },
                    reason=(
                        f"Curated ENTITY_CAT taxonomy: '{label}' is_a "
                        f"'{category_label}'"
                    ),
                )
            )

        # Phase 2 — CREATE_RELATIONSHIP for each pair of detected concepts
        # not already connected by a RELATED_TO edge.
        existing_rel_ids = self._existing_relationship_ids(
            graph, [cid for _, cid in concept_pairs]
        )

        for i in range(len(concept_pairs)):
            for j in range(i + 1, len(concept_pairs)):
                label_a, cid_a = concept_pairs[i]
                label_b, cid_b = concept_pairs[j]

                # Stable direction: lexicographically smaller UUID is source.
                if str(cid_a) <= str(cid_b):
                    src_id, tgt_id = cid_a, cid_b
                    src_label, tgt_label = label_a, label_b
                else:
                    src_id, tgt_id = cid_b, cid_a
                    src_label, tgt_label = label_b, label_a

                rel_type = OntologyRelationshipType.RELATED_TO
                rid = _relationship_id(src_id, tgt_id, rel_type.value)

                if rid not in existing_rel_ids:
                    proposals.append(
                        OntologyProposal(
                            proposal_type=ProposalType.CREATE_RELATIONSHIP,
                            payload={
                                "source_id": str(src_id),
                                "target_id": str(tgt_id),
                                "relationship_type": rel_type.value,
                                "confidence": knowledge.confidence,
                            },
                            reason=(
                                f"Co-occurrence of '{src_label}' and '{tgt_label}' "
                                "in the same knowledge object implies a relationship"
                            ),
                        )
                    )

        # Phase 3 — ATTACH_KNOWLEDGE_OBJECT for each concept not yet linked
        # to this knowledge object.
        already_attached: Set[UUID] = {
            c.id for c in graph.concepts_for_knowledge_object(knowledge.id)
        }

        for label, cid in concept_pairs:
            if cid not in already_attached:
                proposals.append(
                    OntologyProposal(
                        proposal_type=ProposalType.ATTACH_KNOWLEDGE_OBJECT,
                        payload={
                            "knowledge_object_id": str(knowledge.id),
                            "concept_id": str(cid),
                            "relevance": knowledge.importance,
                        },
                        reason=f"Knowledge object mentions concept '{label}'",
                    )
                )

        return proposals

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _existing_relationship_ids(
        self,
        graph: ConceptGraph,
        concept_ids: List[UUID],
    ) -> Set[UUID]:
        """Collect relationship UUIDs for every concept in *concept_ids* present in *graph*.

        Queries both outgoing and incoming edges via
        :meth:`~ConceptGraph.relationships` so direction does not matter.
        Concepts absent from the graph are silently skipped.
        """
        result: Set[UUID] = set()
        for cid in concept_ids:
            if graph.has_concept(cid):
                for rel in graph.relationships(cid):
                    result.add(rel.id)
        return result
