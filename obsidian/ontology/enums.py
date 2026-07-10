"""Enumerations for the Haven Ontology subsystem.

All enumerations are ``str`` enums so they serialise to JSON natively
and compare equal to plain strings.
"""

from __future__ import annotations

from enum import Enum


class OntologyRelationshipType(str, Enum):
    """Typed edge labels allowed between two :class:`Concept` nodes.

    The set is intentionally small and closed.  Adding new types requires
    a deliberate spec change so that graph traversal semantics remain
    predictable.

    Values
    ------
    IS_A : str
        The source concept is a specialisation of the target
        (``"Memory Engine" IS_A "Software Component"``).
    PART_OF : str
        The source concept is a component of the target
        (``"Slot Allocator" PART_OF "Memory Engine"``).
    USES : str
        The source concept uses or depends on the target at runtime
        (``"Haven" USES "Claude"``).
    DEPENDS_ON : str
        The source concept requires the target to exist or function
        (``"Haven" DEPENDS_ON "Qdrant"``).
    CREATED_BY : str
        The source concept was created by the target agent or entity
        (``"Haven" CREATED_BY "Siddhartha"``).
    LOCATED_IN : str
        The source concept exists within or at the target location
        (``"DTU" LOCATED_IN "Copenhagen"``).
    RELATED_TO : str
        A generic association with no stricter semantics (fallback).
    SUPPORTS : str
        The source concept provides evidence for or reinforces the target
        (``"Benchmark" SUPPORTS "Retrieval Quality"``).
    """

    IS_A = "is_a"
    PART_OF = "part_of"
    USES = "uses"
    DEPENDS_ON = "depends_on"
    CREATED_BY = "created_by"
    LOCATED_IN = "located_in"
    RELATED_TO = "related_to"
    SUPPORTS = "supports"


class ProposalType(str, Enum):
    """Category of an :class:`~obsidian.ontology.models.OntologyProposal`.

    Values
    ------
    CREATE_CONCEPT : str
        Propose that a new :class:`~obsidian.ontology.models.Concept` be
        added to the graph.
    CREATE_RELATIONSHIP : str
        Propose that a new :class:`~obsidian.ontology.models.Relationship`
        edge be added to the graph.
    ATTACH_KNOWLEDGE_OBJECT : str
        Propose that an existing ``KnowledgeObject`` be linked to an
        existing :class:`~obsidian.ontology.models.Concept` via an
        :class:`~obsidian.ontology.models.Attachment`.
    """

    CREATE_CONCEPT = "create_concept"
    CREATE_RELATIONSHIP = "create_relationship"
    ATTACH_KNOWLEDGE_OBJECT = "attach_knowledge_object"
