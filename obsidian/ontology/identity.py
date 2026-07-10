"""Deterministic UUID generation for the Haven Ontology.

All identifiers in the ontology are derived deterministically from their
content using UUID version 5 (SHA-1 name-based).  This means:

* The same concept label always produces the same concept ID.
* The same (source, target, relationship_type) triple always produces the
  same relationship ID.
* The same (knowledge_object_id, concept_id) pair always produces the
  same attachment ID.

Determinism prevents duplicate nodes from accumulating in the graph when
the same entity is mentioned across multiple conversations.
"""

from __future__ import annotations

import uuid

from obsidian.ontology.text_utils import normalize


# ---------------------------------------------------------------------------
# Stable namespace UUID for the Haven Ontology
# ---------------------------------------------------------------------------

ONTOLOGY_NAMESPACE: uuid.UUID = uuid.uuid5(
    uuid.NAMESPACE_DNS,
    "haven.obsidian.ontology",
)
"""Stable UUID5 namespace for all Haven Ontology identifiers.

Derived from :data:`uuid.NAMESPACE_DNS` and the string
``"haven.obsidian.ontology"``.  This value is fixed for the lifetime of
the project; changing it would invalidate every stored identifier.
"""


# ---------------------------------------------------------------------------
# Public factories
# ---------------------------------------------------------------------------


def concept_id(label: str) -> uuid.UUID:
    """Return a deterministic UUID5 for a :class:`~obsidian.ontology.models.Concept`.

    The label is normalised (stripped and lowercased) before hashing so
    that ``"Haven"``, ``"haven"``, and ``"  Haven  "`` all map to the
    same identifier.

    Parameters
    ----------
    label : str
        The canonical label of the concept (e.g. ``"Haven"``).

    Returns
    -------
    uuid.UUID
        A deterministic, globally unique identifier for this concept.

    Examples
    --------
    >>> concept_id("Haven") == concept_id("haven")
    True
    >>> concept_id("Haven") == concept_id("Claude")
    False
    """
    return uuid.uuid5(ONTOLOGY_NAMESPACE, normalize(label))


def relationship_id(source_id: uuid.UUID, target_id: uuid.UUID, rel_type: str) -> uuid.UUID:
    """Return a deterministic UUID5 for a :class:`~obsidian.ontology.models.Relationship`.

    The key is the concatenation ``"<source_id>:<target_id>:<rel_type>"``
    so that direction and type are both encoded in the identifier.

    Parameters
    ----------
    source_id : uuid.UUID
        The UUID of the source :class:`~obsidian.ontology.models.Concept`.
    target_id : uuid.UUID
        The UUID of the target :class:`~obsidian.ontology.models.Concept`.
    rel_type : str
        The string value of the relationship type (e.g. ``"uses"``).

    Returns
    -------
    uuid.UUID
        A deterministic, globally unique identifier for this relationship.

    Examples
    --------
    >>> from uuid import UUID
    >>> src = UUID("12345678-1234-5678-1234-000000000001")
    >>> tgt = UUID("12345678-1234-5678-1234-000000000002")
    >>> relationship_id(src, tgt, "uses") == relationship_id(src, tgt, "uses")
    True
    >>> relationship_id(src, tgt, "uses") != relationship_id(tgt, src, "uses")
    True
    """
    key = f"{source_id}:{target_id}:{rel_type}"
    return uuid.uuid5(ONTOLOGY_NAMESPACE, key)


def attachment_id(knowledge_object_id: uuid.UUID, concept_id: uuid.UUID) -> uuid.UUID:
    """Return a deterministic UUID5 for an :class:`~obsidian.ontology.models.Attachment`.

    The key is the concatenation ``"<knowledge_object_id>:<concept_id>"``
    so that swapping the two values produces a different identifier.

    Parameters
    ----------
    knowledge_object_id : uuid.UUID
        The UUID of the ``KnowledgeObject`` supplying the evidence.
    concept_id : uuid.UUID
        The UUID of the :class:`~obsidian.ontology.models.Concept` being
        supported.

    Returns
    -------
    uuid.UUID
        A deterministic, globally unique identifier for this attachment.

    Examples
    --------
    >>> from uuid import UUID
    >>> ko = UUID("12345678-1234-5678-1234-000000000001")
    >>> c  = UUID("12345678-1234-5678-1234-000000000002")
    >>> attachment_id(ko, c) == attachment_id(ko, c)
    True
    """
    key = f"{knowledge_object_id}:{concept_id}"
    return uuid.uuid5(ONTOLOGY_NAMESPACE, key)
