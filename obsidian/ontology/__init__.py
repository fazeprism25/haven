"""Haven Ontology – package public API.

The Ontology subsystem organises ``KnowledgeObject`` instances into a
Concept Graph to improve retrieval quality.  It is an *indexing layer*,
not a storage layer.  ``KnowledgeObjects`` remain the source of truth.

Re-exported here (this module's ``__all__``)
----------------------------------------------
* :class:`~obsidian.ontology.models.Concept` – stable semantic entity.
* :class:`~obsidian.ontology.models.Relationship` – typed edge between
  two Concepts.
* :class:`~obsidian.ontology.models.Attachment` – evidence link from a
  ``KnowledgeObject`` to a Concept.
* :class:`~obsidian.ontology.models.OntologyProposal` – proposed graph
  mutation (never applied directly).
* :mod:`~obsidian.ontology.enums` – ``OntologyRelationshipType`` and
  ``ProposalType``.
* :mod:`~obsidian.ontology.identity` – deterministic UUID factories.
* :mod:`~obsidian.ontology.text_utils` – shared tokeniser for write and
  read paths.

Implemented, but imported directly from their own submodule rather than
re-exported here
-------------------------------------------------------------------------
Graph storage (:class:`~obsidian.ontology.concept_graph.ConceptGraph`),
:class:`~obsidian.ontology.ontology_validator.OntologyValidator`, the
Markdown concept writer/parser, :class:`~obsidian.ontology.ontology_manager.OntologyManager`
(LLM-driven proposal generation), the concept-aware candidate retriever,
and activation spreading are all implemented and live in the read/write
pipeline today — this package's own ``__init__.py`` simply never grew a
curated re-export for them, matching how the rest of the codebase already
imports them (e.g. ``from obsidian.ontology.concept_graph import
ConceptGraph``). Not a scope gap; only a statement about this file's own
import surface.
"""

from __future__ import annotations

from obsidian.ontology.enums import OntologyRelationshipType, ProposalType
from obsidian.ontology.identity import (
    ONTOLOGY_NAMESPACE,
    attachment_id,
    concept_id,
    relationship_id,
)
from obsidian.ontology.models import (
    Attachment,
    Concept,
    OntologyProposal,
    Relationship,
)
from obsidian.ontology.text_utils import (
    STOP_WORDS,
    normalize,
    tokenize,
    tokenize_label,
    tokenize_query,
)

__all__ = [
    # Enums
    "OntologyRelationshipType",
    "ProposalType",
    # Identity helpers
    "ONTOLOGY_NAMESPACE",
    "attachment_id",
    "concept_id",
    "relationship_id",
    # Models
    "Attachment",
    "Concept",
    "OntologyProposal",
    "Relationship",
    # Text utilities
    "STOP_WORDS",
    "normalize",
    "tokenize",
    "tokenize_label",
    "tokenize_query",
]
