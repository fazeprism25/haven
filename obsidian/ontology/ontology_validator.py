"""Ontology Validator — verifies OntologyProposals before graph mutation.

Accepts the current :class:`~obsidian.ontology.concept_graph.ConceptGraph`
and a list of :class:`~obsidian.ontology.models.OntologyProposal` objects
from the Ontology Manager, and returns a :class:`ValidationResult` for every
proposal explaining whether it was accepted or rejected and why.

Responsibilities
----------------
* **Payload schema** — required keys are present and have the right types.
* **Deterministic-ID consistency** — UUID fields are well-formed; derived IDs
  match the canonical identity functions.
* **Duplicate concepts** — rejects ``CREATE_CONCEPT`` whose label-derived UUID
  is already in the graph *or* was already accepted in this batch.
* **Duplicate relationships** — rejects ``CREATE_RELATIONSHIP`` whose derived
  UUID is already in the graph *or* already accepted in this batch.
* **Duplicate attachments** — rejects ``ATTACH_KNOWLEDGE_OBJECT`` whose derived
  UUID is already in the graph *or* already accepted in this batch.
* **Relationship endpoint existence** — both ``source_id`` and ``target_id``
  must exist in the graph *or* have been accepted by a preceding
  ``CREATE_CONCEPT`` in the same batch (dependency-order aware).
* **Attachment target existence** — ``concept_id`` must exist in the graph *or*
  have been accepted by a preceding ``CREATE_CONCEPT`` in the same batch.

Non-responsibilities (enforced by design)
-----------------------------------------
* No graph mutation — zero calls to ``add_*`` methods.
* No Markdown I/O.
* No semantic / vector retrieval.
* No concept detection.
* No activation spreading.

Determinism
-----------
The validator is fully deterministic.  Given identical *proposals* and *graph*,
it always returns the same :class:`ValidationResult` list in the same order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Set
from uuid import UUID

from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.enums import OntologyRelationshipType, ProposalType
from obsidian.ontology.identity import (
    attachment_id as _attachment_id,
    concept_id as _concept_id,
    relationship_id as _relationship_id,
)
from obsidian.ontology.models import OntologyProposal
from obsidian.ontology.text_utils import normalize


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of validating a single :class:`OntologyProposal`.

    Parameters
    ----------
    proposal : OntologyProposal
        The proposal that was evaluated.
    accepted : bool
        ``True`` when the proposal passed all checks; ``False`` otherwise.
    rejection_reason : str
        Human-readable explanation of why the proposal was rejected.
        Empty string when *accepted* is ``True``.
    """

    proposal: OntologyProposal
    accepted: bool
    rejection_reason: str = ""

    def __post_init__(self) -> None:
        if self.accepted and self.rejection_reason:
            raise ValueError(
                "rejection_reason must be empty when accepted is True"
            )


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class OntologyValidator:
    """Validate a list of :class:`OntologyProposal` objects against a
    :class:`ConceptGraph`.

    The validator is **stateless** — instantiate once and reuse freely.
    All graph access is read-only.

    Processing model
    ----------------
    Proposals are evaluated in list order.  Accepted proposals update an
    internal batch-tracking set so that later proposals can reference
    concepts / relationships / attachments introduced earlier in the same
    batch.  Rejected proposals do **not** update the tracking sets, so
    downstream proposals cannot rely on a rejected predecessor.

    Every proposal in the input list appears exactly once in the returned
    results, in the same order.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        proposals: List[OntologyProposal],
        graph: ConceptGraph,
    ) -> List[ValidationResult]:
        """Validate *proposals* against *graph* and return results.

        Parameters
        ----------
        proposals : list[OntologyProposal]
            Ordered proposals to validate (typically from
            :class:`~obsidian.ontology.ontology_manager.OntologyManager`).
        graph : ConceptGraph
            Current graph state.  Read-only; never mutated.

        Returns
        -------
        list[ValidationResult]
            One :class:`ValidationResult` per proposal, in the same order.
            Callers wanting only accepted proposals can filter with::

                [r.proposal for r in results if r.accepted]
        """
        # Batch-level tracking — populated only by accepted proposals so
        # rejected predecessors cannot be referenced by later proposals.
        accepted_concept_ids: Set[UUID] = set()
        accepted_relationship_ids: Set[UUID] = set()
        accepted_attachment_ids: Set[UUID] = set()

        results: List[ValidationResult] = []

        for proposal in proposals:
            pt = proposal.proposal_type

            if pt == ProposalType.CREATE_CONCEPT:
                result = self._validate_create_concept(
                    proposal, graph, accepted_concept_ids
                )
                if result.accepted:
                    label = proposal.payload["label"]
                    accepted_concept_ids.add(_concept_id(label))

            elif pt == ProposalType.CREATE_RELATIONSHIP:
                result = self._validate_create_relationship(
                    proposal, graph, accepted_concept_ids, accepted_relationship_ids
                )
                if result.accepted:
                    src = UUID(proposal.payload["source_id"])
                    tgt = UUID(proposal.payload["target_id"])
                    rt = proposal.payload["relationship_type"]
                    accepted_relationship_ids.add(_relationship_id(src, tgt, rt))

            elif pt == ProposalType.ATTACH_KNOWLEDGE_OBJECT:
                result = self._validate_attach(
                    proposal, graph, accepted_concept_ids, accepted_attachment_ids
                )
                if result.accepted:
                    ko_id = UUID(proposal.payload["knowledge_object_id"])
                    cid = UUID(proposal.payload["concept_id"])
                    accepted_attachment_ids.add(_attachment_id(ko_id, cid))

            else:
                result = ValidationResult(
                    proposal=proposal,
                    accepted=False,
                    rejection_reason=f"Unknown proposal_type: {pt!r}",
                )

            results.append(result)

        return results

    # ------------------------------------------------------------------
    # Per-type validators
    # ------------------------------------------------------------------

    def _validate_create_concept(
        self,
        proposal: OntologyProposal,
        graph: ConceptGraph,
        accepted_concept_ids: Set[UUID],
    ) -> ValidationResult:
        payload = proposal.payload

        # --- Payload schema ---
        reason = _check_create_concept_payload(payload)
        if reason:
            return ValidationResult(proposal=proposal, accepted=False, rejection_reason=reason)

        label: str = payload["label"]
        aliases: list = payload["aliases"]

        # --- Duplicate aliases within the proposal ---
        normalised_aliases = [normalize(a) for a in aliases if isinstance(a, str)]
        if len(normalised_aliases) != len(set(normalised_aliases)):
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason="aliases contain duplicate entries",
            )

        # --- Duplicate concept (graph or batch) ---
        cid = _concept_id(label)
        if graph.has_concept(cid):
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason=(
                    f"Concept with label '{label}' already exists in graph "
                    f"(id={cid})"
                ),
            )
        if cid in accepted_concept_ids:
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason=(
                    f"Concept with label '{label}' duplicated within this batch "
                    f"(id={cid})"
                ),
            )

        return ValidationResult(proposal=proposal, accepted=True)

    def _validate_create_relationship(
        self,
        proposal: OntologyProposal,
        graph: ConceptGraph,
        accepted_concept_ids: Set[UUID],
        accepted_relationship_ids: Set[UUID],
    ) -> ValidationResult:
        payload = proposal.payload

        # --- Payload schema ---
        reason = _check_create_relationship_payload(payload)
        if reason:
            return ValidationResult(proposal=proposal, accepted=False, rejection_reason=reason)

        src = UUID(payload["source_id"])
        tgt = UUID(payload["target_id"])
        rel_type_str: str = payload["relationship_type"]
        confidence: float = payload["confidence"]

        # --- Self-loop ---
        if src == tgt:
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason="source_id and target_id must differ; self-loops are not permitted",
            )

        # --- Confidence range ---
        if not 0.0 <= confidence <= 1.0:
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason=f"confidence must be in [0.0, 1.0]; got {confidence}",
            )

        # --- Valid relationship type ---
        try:
            rel_type = OntologyRelationshipType(rel_type_str)
        except ValueError:
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason=(
                    f"relationship_type '{rel_type_str}' is not a valid "
                    f"OntologyRelationshipType"
                ),
            )

        # --- Endpoint existence ---
        if not _concept_exists(src, graph, accepted_concept_ids):
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason=(
                    f"source concept {src} does not exist in graph or accepted batch"
                ),
            )
        if not _concept_exists(tgt, graph, accepted_concept_ids):
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason=(
                    f"target concept {tgt} does not exist in graph or accepted batch"
                ),
            )

        # --- Duplicate relationship ---
        rid = _relationship_id(src, tgt, rel_type.value)
        if _relationship_in_graph(rid, src, graph):
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason=(
                    f"Relationship {rid} ({rel_type.value} from {src} to {tgt}) "
                    "already exists in graph"
                ),
            )
        if rid in accepted_relationship_ids:
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason=(
                    f"Relationship {rid} ({rel_type.value} from {src} to {tgt}) "
                    "duplicated within this batch"
                ),
            )

        return ValidationResult(proposal=proposal, accepted=True)

    def _validate_attach(
        self,
        proposal: OntologyProposal,
        graph: ConceptGraph,
        accepted_concept_ids: Set[UUID],
        accepted_attachment_ids: Set[UUID],
    ) -> ValidationResult:
        payload = proposal.payload

        # --- Payload schema ---
        reason = _check_attach_payload(payload)
        if reason:
            return ValidationResult(proposal=proposal, accepted=False, rejection_reason=reason)

        ko_id = UUID(payload["knowledge_object_id"])
        cid = UUID(payload["concept_id"])
        relevance: float = payload["relevance"]

        # --- Relevance range ---
        if not 0.0 <= relevance <= 1.0:
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason=f"relevance must be in [0.0, 1.0]; got {relevance}",
            )

        # --- Target concept existence ---
        if not _concept_exists(cid, graph, accepted_concept_ids):
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason=(
                    f"target concept {cid} does not exist in graph or accepted batch"
                ),
            )

        # --- Duplicate attachment ---
        aid = _attachment_id(ko_id, cid)
        if _attachment_in_graph(aid, cid, graph):
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason=(
                    f"Attachment {aid} (ko={ko_id} → concept={cid}) "
                    "already exists in graph"
                ),
            )
        if aid in accepted_attachment_ids:
            return ValidationResult(
                proposal=proposal,
                accepted=False,
                rejection_reason=(
                    f"Attachment {aid} (ko={ko_id} → concept={cid}) "
                    "duplicated within this batch"
                ),
            )

        return ValidationResult(proposal=proposal, accepted=True)


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _concept_exists(
    cid: UUID,
    graph: ConceptGraph,
    accepted_concept_ids: Set[UUID],
) -> bool:
    """Return True if *cid* is present in *graph* or in *accepted_concept_ids*."""
    return graph.has_concept(cid) or cid in accepted_concept_ids


def _relationship_in_graph(rid: UUID, src_id: UUID, graph: ConceptGraph) -> bool:
    """Return True if a relationship with *rid* exists in *graph*.

    Queries via ``graph.relationships(src_id)``; safe when *src_id* is not
    in the graph (returns empty list).
    """
    for rel in graph.relationships(src_id):
        if rel.id == rid:
            return True
    return False


def _attachment_in_graph(aid: UUID, concept_id: UUID, graph: ConceptGraph) -> bool:
    """Return True if an attachment with *aid* exists in *graph*.

    Queries via ``graph.attachments_for_concept(concept_id)``; safe when
    *concept_id* is not in the graph (returns empty list).
    """
    for att in graph.attachments_for_concept(concept_id):
        if att.id == aid:
            return True
    return False


# ---------------------------------------------------------------------------
# Payload schema checkers — return an error string or "" if valid
# ---------------------------------------------------------------------------


def _check_create_concept_payload(payload: dict) -> str:
    """Return an error description if the CREATE_CONCEPT payload is malformed."""
    if "label" not in payload:
        return "CREATE_CONCEPT payload missing required key 'label'"
    label = payload["label"]
    if not isinstance(label, str):
        return f"CREATE_CONCEPT payload 'label' must be a str; got {type(label).__name__}"
    if not label.strip():
        return "CREATE_CONCEPT payload 'label' must be a non-empty, non-whitespace string"

    if "aliases" not in payload:
        return "CREATE_CONCEPT payload missing required key 'aliases'"
    aliases = payload["aliases"]
    if not isinstance(aliases, list):
        return f"CREATE_CONCEPT payload 'aliases' must be a list; got {type(aliases).__name__}"
    for i, alias in enumerate(aliases):
        if not isinstance(alias, str):
            return (
                f"CREATE_CONCEPT payload 'aliases[{i}]' must be a str; "
                f"got {type(alias).__name__}"
            )

    if "description" not in payload:
        return "CREATE_CONCEPT payload missing required key 'description'"
    description = payload["description"]
    if not isinstance(description, str):
        return (
            f"CREATE_CONCEPT payload 'description' must be a str; "
            f"got {type(description).__name__}"
        )

    return ""


def _check_create_relationship_payload(payload: dict) -> str:
    """Return an error description if the CREATE_RELATIONSHIP payload is malformed."""
    for key in ("source_id", "target_id", "relationship_type", "confidence"):
        if key not in payload:
            return f"CREATE_RELATIONSHIP payload missing required key '{key}'"

    for uuid_key in ("source_id", "target_id"):
        val = payload[uuid_key]
        if not isinstance(val, str):
            return (
                f"CREATE_RELATIONSHIP payload '{uuid_key}' must be a UUID string; "
                f"got {type(val).__name__}"
            )
        try:
            UUID(val)
        except (ValueError, AttributeError):
            return f"CREATE_RELATIONSHIP payload '{uuid_key}' is not a valid UUID: {val!r}"

    if not isinstance(payload["relationship_type"], str):
        return (
            "CREATE_RELATIONSHIP payload 'relationship_type' must be a str; "
            f"got {type(payload['relationship_type']).__name__}"
        )

    confidence = payload["confidence"]
    if not isinstance(confidence, (int, float)):
        return (
            f"CREATE_RELATIONSHIP payload 'confidence' must be a number; "
            f"got {type(confidence).__name__}"
        )

    return ""


def _check_attach_payload(payload: dict) -> str:
    """Return an error description if the ATTACH_KNOWLEDGE_OBJECT payload is malformed."""
    for key in ("knowledge_object_id", "concept_id", "relevance"):
        if key not in payload:
            return f"ATTACH_KNOWLEDGE_OBJECT payload missing required key '{key}'"

    for uuid_key in ("knowledge_object_id", "concept_id"):
        val = payload[uuid_key]
        if not isinstance(val, str):
            return (
                f"ATTACH_KNOWLEDGE_OBJECT payload '{uuid_key}' must be a UUID string; "
                f"got {type(val).__name__}"
            )
        try:
            UUID(val)
        except (ValueError, AttributeError):
            return (
                f"ATTACH_KNOWLEDGE_OBJECT payload '{uuid_key}' is not a valid UUID: "
                f"{val!r}"
            )

    relevance = payload["relevance"]
    if not isinstance(relevance, (int, float)):
        return (
            f"ATTACH_KNOWLEDGE_OBJECT payload 'relevance' must be a number; "
            f"got {type(relevance).__name__}"
        )

    return ""
