"""Deterministic Markdown writer for :class:`~obsidian.ontology.models.Concept` objects.

Phase 2A / 2B — Markdown persistence layer.

The writer has exactly one responsibility: serialise a
:class:`~obsidian.ontology.models.Concept` (plus its associated
:class:`~obsidian.ontology.models.Relationship` and
:class:`~obsidian.ontology.models.Attachment` objects) into a stable,
human-readable Markdown file with YAML frontmatter.

Design constraints
------------------
* **No graph logic** — the :class:`ConceptGraph` is out of scope here.
* **No retrieval logic** — the writer never reads from storage.
* **Deterministic** — identical inputs always produce byte-identical output.
* **Git-friendly** — sorted keys and sorted collections minimise diff noise.
* **Parser-compatible** — the YAML frontmatter contains all structured data
  needed to reconstruct the Concept and its edges.  The Markdown body is for
  human readers only and is not intended for machine parsing.

Markdown format
---------------
::

    ---
    id: <uuid>
    label: <string>
    aliases:
    - <alias>           # alphabetically sorted
    description: <string>
    created_at: '<ISO datetime>'
    updated_at: '<ISO datetime or null>'
    relationships:
    - id: <uuid>
      source_id: <uuid>
      target_id: <uuid>
      relationship_type: <string>
      confidence: <float>
    attachments:
    - id: <uuid>
      knowledge_object_id: <uuid>
      concept_id: <uuid>
      relevance: <float>
    ---

    # <label>

    <description paragraph – omitted when empty>

    ## Relationships

    - `USES` → [[<target_id>]] (confidence: 1.0000)
    - `PART_OF` ← [[<source_id>]] (confidence: 0.8500)

    ## Attachments

    - [[<knowledge_object_id>|Evidence]] (relevance: 0.9000)

``target_id``/``source_id``/``knowledge_object_id`` are rendered as
Obsidian wiki-links rather than plain text: both Concept notes (filed by
UUID, this same writer) and Memory notes (filed by UUID,
:class:`~obsidian.memory_engine.vault_writer.VaultWriter`) resolve
directly, so a Concept note is a real, clickable jumping-off point to its
neighbouring Concepts and to every Memory that attaches to it — the
reverse of the links :class:`VaultWriter` already emits from the Memory
side.

Sorting rules
-------------
All lists are sorted deterministically before serialisation:

* **Aliases** — alphabetical string order.
* **Relationships** — ``(relationship_type.value, str(source_id), str(target_id))``
  lexicographic.
* **Attachments** — ``str(knowledge_object_id)`` lexicographic.

Direction notation
------------------
In the human-readable body, each relationship entry shows:

* ``→`` when the concept being written is the *source* (outgoing edge).
* ``←`` when the concept being written is the *target* (incoming edge).

The YAML frontmatter always stores the full ``(source_id, target_id)`` pair
so the direction is unambiguous for a future :class:`ConceptParser`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from obsidian.ontology.models import Attachment, Concept, Relationship


# ---------------------------------------------------------------------------
# Module-private sorting helpers
# ---------------------------------------------------------------------------


def _sort_relationships(relationships: List[Relationship]) -> List[Relationship]:
    """Return *relationships* sorted by ``(type, source_id, target_id)``.

    The sort key uses only string values so the result is lexicographically
    stable across Python sessions and platforms.

    Parameters
    ----------
    relationships : list[Relationship]
        Unsorted relationships.

    Returns
    -------
    list[Relationship]
        New list, sorted deterministically.
    """
    return sorted(
        relationships,
        key=lambda r: (
            r.relationship_type.value,
            str(r.source_id),
            str(r.target_id),
        ),
    )


def _sort_attachments(attachments: List[Attachment]) -> List[Attachment]:
    """Return *attachments* sorted by ``str(knowledge_object_id)``.

    Parameters
    ----------
    attachments : list[Attachment]
        Unsorted attachments.

    Returns
    -------
    list[Attachment]
        New list, sorted deterministically.
    """
    return sorted(attachments, key=lambda a: str(a.knowledge_object_id))


# ---------------------------------------------------------------------------
# Module-private rendering helpers
# ---------------------------------------------------------------------------


def _relationship_direction(
    concept: Concept,
    relationship: Relationship,
) -> Tuple[str, str]:
    """Determine direction arrow and the 'other' UUID string for *relationship*.

    Parameters
    ----------
    concept : Concept
        The concept currently being serialised.
    relationship : Relationship
        A relationship that involves *concept* as source or target.

    Returns
    -------
    tuple[str, str]
        ``(direction_arrow, other_concept_id_string)`` where *direction_arrow*
        is ``"→"`` for outgoing edges and ``"←"`` for incoming edges.
    """
    if relationship.source_id == concept.id:
        return "→", str(relationship.target_id)
    return "←", str(relationship.source_id)


def _build_frontmatter(
    concept: Concept,
    relationships: List[Relationship],
    attachments: List[Attachment],
    updated_at: Optional[datetime],
) -> Dict[str, Any]:
    """Build the YAML frontmatter dictionary for *concept*.

    The dictionary is the authoritative machine-readable representation.
    All data required to reconstruct the concept and its edges is stored
    here.  Key order is fixed (``sort_keys=False`` is used by the caller)
    to match the documented format and minimise Git diff noise.

    Parameters
    ----------
    concept : Concept
        The concept to serialise.
    relationships : list[Relationship]
        Already sorted relationships.
    attachments : list[Attachment]
        Already sorted attachments.
    updated_at : datetime, optional
        Most recent modification time (stored as ``null`` when absent).

    Returns
    -------
    dict
        A JSON/YAML-serialisable dictionary.
    """
    return {
        "id": str(concept.id),
        "label": concept.label,
        "aliases": sorted(concept.aliases),
        "description": concept.description,
        "created_at": concept.created_at.isoformat(),
        "updated_at": updated_at.isoformat() if updated_at is not None else None,
        "relationships": [
            {
                "id": str(r.id),
                "source_id": str(r.source_id),
                "target_id": str(r.target_id),
                "relationship_type": r.relationship_type.value,
                "confidence": r.confidence,
            }
            for r in relationships
        ],
        "attachments": [
            {
                "id": str(a.id),
                "knowledge_object_id": str(a.knowledge_object_id),
                "concept_id": str(a.concept_id),
                "relevance": a.relevance,
            }
            for a in attachments
        ],
    }


def _build_body(
    concept: Concept,
    relationships: List[Relationship],
    attachments: List[Attachment],
) -> str:
    """Build the human-readable Markdown body for *concept*.

    The body is NOT intended for machine parsing — the YAML frontmatter is
    the authoritative source for reconstruction.  The body makes the file
    legible in any Markdown viewer or Obsidian vault.

    Parameters
    ----------
    concept : Concept
        The concept to serialise.
    relationships : list[Relationship]
        Already sorted relationships involving this concept.
    attachments : list[Attachment]
        Already sorted attachments linking KnowledgeObjects to this concept.

    Returns
    -------
    str
        Markdown body string (no trailing newline — the caller adds one).
    """
    lines: List[str] = []

    # H1 title
    lines.append(f"# {concept.label}")
    lines.append("")

    # Description paragraph (omitted entirely when empty or whitespace-only)
    if concept.description.strip():
        lines.append(concept.description.strip())
        lines.append("")

    # Relationships section
    lines.append("## Relationships")
    lines.append("")
    if relationships:
        for r in relationships:
            direction, other_id = _relationship_direction(concept, r)
            rel_label = r.relationship_type.value.upper()
            lines.append(
                f"- `{rel_label}` {direction} [[{other_id}]] "
                f"(confidence: {r.confidence:.4f})"
            )
    else:
        lines.append("_None._")
    lines.append("")

    # Attachments section
    lines.append("## Attachments")
    lines.append("")
    if attachments:
        for a in attachments:
            lines.append(
                f"- [[{a.knowledge_object_id}|Evidence]] (relevance: {a.relevance:.4f})"
            )
    else:
        lines.append("_None._")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class ConceptWriter:
    """Serialise a :class:`~obsidian.ontology.models.Concept` into a
    deterministic Markdown file with YAML frontmatter.

    The writer is **stateless** — it holds no configuration and can be
    instantiated without arguments.

    The caller is responsible for supplying all relationships and attachments
    associated with the concept.  The writer does not query a graph; it only
    formats what it is given.

    Public methods
    --------------
    render(concept, *, relationships, attachments, updated_at) → str
        Produce the Markdown string without writing to disk.  Useful for
        testing and for dry-run inspection.

    write(concept, output_directory, *, relationships, attachments, updated_at) → Path
        Render and write to ``{output_directory}/{concept.id}.md``.

    Examples
    --------
    >>> from obsidian.ontology.models import Concept
    >>> writer = ConceptWriter()
    >>> concept = Concept.from_label("Haven", description="Personal second-brain")
    >>> md = writer.render(concept)
    >>> md.startswith("---\\n")
    True
    >>> "# Haven" in md
    True
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(
        self,
        concept: Concept,
        output_directory: Path,
        *,
        relationships: Optional[List[Relationship]] = None,
        attachments: Optional[List[Attachment]] = None,
        updated_at: Optional[datetime] = None,
    ) -> Path:
        """Write *concept* to ``{output_directory}/{concept.id}.md``.

        The filename is the concept's deterministic UUID, ensuring that
        the same concept always maps to the same file path.  Existing files
        are overwritten in place, which keeps Git history linear.

        Parameters
        ----------
        concept : Concept
            The concept to serialise.
        output_directory : Path
            Target directory.  Created (including parents) if absent.
        relationships : list[Relationship], optional
            Relationships that involve this concept as source or target.
            The writer does not validate membership; the caller filters.
        attachments : list[Attachment], optional
            Attachments linking ``KnowledgeObject`` instances to this concept.
        updated_at : datetime, optional
            Most recent time this concept's graph data was modified.
            Serialised as ``null`` when not supplied.

        Returns
        -------
        Path
            Absolute path of the written file.
        """
        output_directory = Path(output_directory).resolve()
        output_directory.mkdir(parents=True, exist_ok=True)

        file_path = output_directory / f"{concept.id}.md"
        content = self.render(
            concept,
            relationships=relationships,
            attachments=attachments,
            updated_at=updated_at,
        )
        file_path.write_text(content, encoding="utf-8")
        return file_path

    def render(
        self,
        concept: Concept,
        *,
        relationships: Optional[List[Relationship]] = None,
        attachments: Optional[List[Attachment]] = None,
        updated_at: Optional[datetime] = None,
    ) -> str:
        """Render *concept* to a Markdown string without touching the filesystem.

        Parameters
        ----------
        concept : Concept
            The concept to serialise.
        relationships : list[Relationship], optional
            Relationships that involve this concept.
        attachments : list[Attachment], optional
            Attachments linking ``KnowledgeObject`` instances to this concept.
        updated_at : datetime, optional
            Most recent modification timestamp.

        Returns
        -------
        str
            Deterministic Markdown string with YAML frontmatter.  The string
            always ends with a single newline character.
        """
        rels = _sort_relationships(relationships or [])
        atts = _sort_attachments(attachments or [])

        frontmatter = _build_frontmatter(concept, rels, atts, updated_at)
        body = _build_body(concept, rels, atts)

        yaml_str = yaml.dump(
            frontmatter,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        return f"---\n{yaml_str}---\n\n{body}\n"
