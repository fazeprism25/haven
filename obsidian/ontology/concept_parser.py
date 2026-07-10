"""Deterministic Markdown parser for :class:`~obsidian.ontology.models.Concept` files.

Phase 2C — Markdown parsing layer.

The parser has exactly one responsibility: deserialise a Markdown file
produced by :class:`~obsidian.ontology.concept_writer.ConceptWriter` back
into a :class:`~obsidian.ontology.models.Concept` (plus its associated
:class:`~obsidian.ontology.models.Relationship` and
:class:`~obsidian.ontology.models.Attachment` objects).

Design constraints
------------------
* **No graph logic** — the :class:`ConceptGraph` is out of scope here.
* **No write logic** — the parser never writes to storage.
* **Deterministic** — identical inputs always produce identical output.
* **YAML-authoritative** — the Markdown body is ignored; only the YAML
  frontmatter is parsed.  The body exists for human readers only.
* **Fail-fast** — any structural or semantic deviation from the expected
  format raises :class:`ConceptParseError` with a descriptive message.

Round-trip guarantee
--------------------
For any :class:`~obsidian.ontology.models.Concept` *c*, relationships *rels*,
attachments *atts*, and optional *updated_at*::

    writer = ConceptWriter()
    parser = ConceptParser()
    result = parser.parse(
        writer.render(c, relationships=rels, attachments=atts, updated_at=ts)
    )
    assert result.concept == c_with_sorted_aliases
    assert result.relationships == writer_sorted_rels
    assert result.attachments == writer_sorted_atts
    assert result.updated_at == ts

The writer sorts aliases (alphabetically), relationships
``(relationship_type, source_id, target_id)`` lexicographically, and
attachments by ``knowledge_object_id``.  The parser returns objects in the
same order as the YAML frontmatter, so a second write call on the parsed
result is byte-identical to the first.

Expected Markdown format
------------------------
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
    ...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import UUID

import yaml

from obsidian.ontology.enums import OntologyRelationshipType
from obsidian.ontology.models import Attachment, Concept, Relationship


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class ConceptParseError(ValueError):
    """Raised when a Concept Markdown file cannot be parsed.

    Always raised with a descriptive message identifying the exact field or
    structural problem.  Inherits from :class:`ValueError` so callers can
    catch it alongside other validation errors.
    """


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ConceptParseResult:
    """Container returned by :meth:`ConceptParser.parse` and
    :meth:`ConceptParser.read`.

    All objects inside are fully reconstructed, immutable domain instances.
    The ``relationships`` and ``attachments`` lists preserve the order from
    the YAML frontmatter (which matches the deterministic sort applied by
    the writer).

    Parameters
    ----------
    concept : Concept
        The reconstructed :class:`~obsidian.ontology.models.Concept`.
    relationships : list[Relationship]
        Relationships from the file, in writer-sorted order.
    attachments : list[Attachment]
        Attachments from the file, in writer-sorted order.
    updated_at : datetime, optional
        Most recent modification time, or ``None`` when not recorded.
    """

    concept: Concept
    relationships: List[Relationship] = field(default_factory=list)
    attachments: List[Attachment] = field(default_factory=list)
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Parser class
# ---------------------------------------------------------------------------


class ConceptParser:
    """Deserialise a Markdown file written by
    :class:`~obsidian.ontology.concept_writer.ConceptWriter` back into
    domain objects.

    The parser is **stateless** — it holds no configuration and can be
    instantiated without arguments.

    Public methods
    --------------
    parse(text) → ConceptParseResult
        Parse a Markdown string in memory.
    read(path) → ConceptParseResult
        Read and parse a ``.md`` file from disk.

    Raises
    ------
    ConceptParseError
        On any structural or semantic problem in the input.

    Examples
    --------
    >>> from obsidian.ontology.concept_writer import ConceptWriter
    >>> from obsidian.ontology.concept_parser import ConceptParser
    >>> from obsidian.ontology.models import Concept
    >>> writer = ConceptWriter()
    >>> parser = ConceptParser()
    >>> concept = Concept.from_label("Haven", description="Personal second-brain")
    >>> result = parser.parse(writer.render(concept))
    >>> result.concept.label
    'Haven'
    >>> result.updated_at is None
    True
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self, path: Path) -> ConceptParseResult:
        """Read and parse a Concept Markdown file from *path*.

        Parameters
        ----------
        path : Path
            Absolute or relative path to the ``.md`` file.

        Returns
        -------
        ConceptParseResult
            Fully reconstructed domain objects.

        Raises
        ------
        ConceptParseError
            If the file does not exist, cannot be read, or is malformed.
        """
        path = Path(path)
        if not path.exists():
            raise ConceptParseError(f"File not found: {path}")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConceptParseError(f"Cannot read {path}: {exc}") from exc
        return self.parse(text)

    def parse(self, text: str) -> ConceptParseResult:
        """Parse a Concept Markdown string produced by
        :class:`~obsidian.ontology.concept_writer.ConceptWriter`.

        Only the YAML frontmatter is parsed.  The Markdown body after the
        closing ``---`` fence is ignored.

        Parameters
        ----------
        text : str
            Full Markdown document as a string.

        Returns
        -------
        ConceptParseResult
            Fully reconstructed domain objects.

        Raises
        ------
        ConceptParseError
            On any structural or semantic deviation from the expected format.
        """
        yaml_text = self._extract_frontmatter(text)
        fm = self._load_yaml(yaml_text)
        self._assert_required_keys(fm)
        concept = self._build_concept(fm)
        relationships = self._build_relationships(fm)
        attachments = self._build_attachments(fm)
        updated_at = self._build_updated_at(fm)
        return ConceptParseResult(
            concept=concept,
            relationships=relationships,
            attachments=attachments,
            updated_at=updated_at,
        )

    # ------------------------------------------------------------------
    # Private — document splitting
    # ------------------------------------------------------------------

    def _extract_frontmatter(self, text: str) -> str:
        """Return the raw YAML text between the opening and closing fences.

        The expected structure is ``---\\n{yaml}---\\n{body}``.
        Splitting ``text`` on ``"---\\n"`` with ``maxsplit=2`` yields
        ``["", yaml_content, rest_of_document]``.

        Parameters
        ----------
        text : str
            Full Markdown document.

        Returns
        -------
        str
            The YAML content between the ``---`` fences (exclusive).

        Raises
        ------
        ConceptParseError
            If the document does not start with ``---\\n`` or is missing
            the closing fence.
        """
        if not text.startswith("---\n"):
            raise ConceptParseError(
                "Document does not begin with '---\\n'; "
                "not a valid Concept Markdown file"
            )
        parts = text.split("---\n", 2)
        if len(parts) < 3:
            raise ConceptParseError(
                "Document is missing the closing '---' frontmatter fence"
            )
        return parts[1]

    # ------------------------------------------------------------------
    # Private — YAML loading
    # ------------------------------------------------------------------

    def _load_yaml(self, yaml_text: str) -> Dict:
        """Load *yaml_text* with :func:`yaml.safe_load`.

        Parameters
        ----------
        yaml_text : str
            Raw YAML string extracted from between the fences.

        Returns
        -------
        dict
            Parsed mapping.

        Raises
        ------
        ConceptParseError
            If the YAML is syntactically invalid or does not parse to a
            mapping.
        """
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise ConceptParseError(
                f"Frontmatter YAML is syntactically invalid: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ConceptParseError(
                f"Frontmatter must be a YAML mapping; "
                f"got {type(data).__name__!r}"
            )
        return data

    def _assert_required_keys(self, fm: Dict) -> None:
        """Verify *fm* contains all required top-level keys.

        Parameters
        ----------
        fm : dict
            Parsed frontmatter dictionary.

        Raises
        ------
        ConceptParseError
            If any required key is absent.
        """
        required = {"id", "label", "created_at"}
        missing = required - fm.keys()
        if missing:
            raise ConceptParseError(
                f"Frontmatter is missing required field(s): "
                f"{sorted(missing)}"
            )

    # ------------------------------------------------------------------
    # Private — scalar parsers
    # ------------------------------------------------------------------

    def _parse_uuid(self, value: object, field_path: str) -> UUID:
        """Parse *value* as a UUID string.

        Parameters
        ----------
        value : object
            Raw value from the YAML document.
        field_path : str
            Dotted path used in error messages (e.g. ``"id"`` or
            ``"relationships[0].source_id"``).

        Returns
        -------
        UUID

        Raises
        ------
        ConceptParseError
            If *value* is not a string or is not a valid UUID.
        """
        if not isinstance(value, str):
            raise ConceptParseError(
                f"Field '{field_path}' must be a UUID string; "
                f"got {type(value).__name__!r}"
            )
        try:
            return UUID(value)
        except ValueError as exc:
            raise ConceptParseError(
                f"Field '{field_path}' is not a valid UUID: {value!r}"
            ) from exc

    def _parse_datetime(self, value: object, field_path: str) -> datetime:
        """Parse *value* as an ISO 8601 datetime.

        PyYAML may return a :class:`str` or a :class:`datetime` depending
        on whether the value is quoted in the YAML source.  Both are
        accepted.

        Parameters
        ----------
        value : object
            Raw value from the YAML document.
        field_path : str
            Dotted path used in error messages.

        Returns
        -------
        datetime

        Raises
        ------
        ConceptParseError
            If *value* cannot be interpreted as a datetime.
        """
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError as exc:
                raise ConceptParseError(
                    f"Field '{field_path}' is not a valid ISO datetime: "
                    f"{value!r}"
                ) from exc
        raise ConceptParseError(
            f"Field '{field_path}' must be an ISO datetime string; "
            f"got {type(value).__name__!r}"
        )

    def _parse_float(self, value: object, field_path: str) -> float:
        """Parse *value* as a float.

        Parameters
        ----------
        value : object
            Raw value from the YAML document.
        field_path : str
            Dotted path used in error messages.

        Returns
        -------
        float

        Raises
        ------
        ConceptParseError
            If *value* is not a numeric type.
        """
        if not isinstance(value, (int, float)):
            raise ConceptParseError(
                f"Field '{field_path}' must be a number; "
                f"got {type(value).__name__!r}"
            )
        return float(value)

    # ------------------------------------------------------------------
    # Private — object builders
    # ------------------------------------------------------------------

    def _build_concept(self, fm: Dict) -> Concept:
        """Reconstruct a :class:`~obsidian.ontology.models.Concept` from *fm*.

        The stored ``id`` is used as-is so the round-trip is exact even if
        normalisation rules in
        :func:`~obsidian.ontology.identity.concept_id` change.

        Parameters
        ----------
        fm : dict
            Parsed frontmatter dictionary.

        Returns
        -------
        Concept

        Raises
        ------
        ConceptParseError
            On any field-level error.
        """
        concept_uuid = self._parse_uuid(fm["id"], "id")

        label = fm["label"]
        if not isinstance(label, str) or not label.strip():
            raise ConceptParseError(
                f"Field 'label' must be a non-empty string; got {label!r}"
            )

        aliases_raw = fm.get("aliases") or []
        if not isinstance(aliases_raw, list):
            raise ConceptParseError(
                f"Field 'aliases' must be a list; "
                f"got {type(aliases_raw).__name__!r}"
            )
        for idx, alias in enumerate(aliases_raw):
            if not isinstance(alias, str):
                raise ConceptParseError(
                    f"aliases[{idx}] must be a string; "
                    f"got {type(alias).__name__!r}"
                )
        aliases: Tuple[str, ...] = tuple(aliases_raw)

        description_raw = fm.get("description")
        description: str = "" if description_raw is None else str(description_raw)

        created_at = self._parse_datetime(fm["created_at"], "created_at")

        try:
            return Concept(
                id=concept_uuid,
                label=label,
                aliases=aliases,
                description=description,
                created_at=created_at,
            )
        except ValueError as exc:
            raise ConceptParseError(
                f"Cannot construct Concept: {exc}"
            ) from exc

    def _build_relationships(self, fm: Dict) -> List[Relationship]:
        """Reconstruct :class:`~obsidian.ontology.models.Relationship` objects.

        Parameters
        ----------
        fm : dict
            Parsed frontmatter dictionary.

        Returns
        -------
        list[Relationship]
            In the same order as the YAML frontmatter.

        Raises
        ------
        ConceptParseError
            On any field-level error.
        """
        raw = fm.get("relationships") or []
        if not isinstance(raw, list):
            raise ConceptParseError(
                f"Field 'relationships' must be a list; "
                f"got {type(raw).__name__!r}"
            )

        _REQUIRED = {"id", "source_id", "target_id", "relationship_type", "confidence"}
        result: List[Relationship] = []

        for idx, entry in enumerate(raw):
            if not isinstance(entry, dict):
                raise ConceptParseError(
                    f"relationships[{idx}] must be a mapping; "
                    f"got {type(entry).__name__!r}"
                )
            missing = _REQUIRED - entry.keys()
            if missing:
                raise ConceptParseError(
                    f"relationships[{idx}] is missing required field(s): "
                    f"{sorted(missing)}"
                )

            rel_id = self._parse_uuid(entry["id"], f"relationships[{idx}].id")
            source_id = self._parse_uuid(
                entry["source_id"], f"relationships[{idx}].source_id"
            )
            target_id = self._parse_uuid(
                entry["target_id"], f"relationships[{idx}].target_id"
            )

            rel_type_raw = entry["relationship_type"]
            if not isinstance(rel_type_raw, str):
                raise ConceptParseError(
                    f"relationships[{idx}].relationship_type must be a string; "
                    f"got {type(rel_type_raw).__name__!r}"
                )
            try:
                rel_type = OntologyRelationshipType(rel_type_raw)
            except ValueError:
                valid = [e.value for e in OntologyRelationshipType]
                raise ConceptParseError(
                    f"relationships[{idx}].relationship_type {rel_type_raw!r} "
                    f"is not a recognised relationship type; "
                    f"must be one of {valid}"
                )

            confidence = self._parse_float(
                entry["confidence"], f"relationships[{idx}].confidence"
            )

            try:
                rel = Relationship(
                    id=rel_id,
                    source_id=source_id,
                    target_id=target_id,
                    relationship_type=rel_type,
                    confidence=confidence,
                )
            except ValueError as exc:
                raise ConceptParseError(
                    f"Cannot construct relationships[{idx}]: {exc}"
                ) from exc

            result.append(rel)

        return result

    def _build_attachments(self, fm: Dict) -> List[Attachment]:
        """Reconstruct :class:`~obsidian.ontology.models.Attachment` objects.

        Parameters
        ----------
        fm : dict
            Parsed frontmatter dictionary.

        Returns
        -------
        list[Attachment]
            In the same order as the YAML frontmatter.

        Raises
        ------
        ConceptParseError
            On any field-level error.
        """
        raw = fm.get("attachments") or []
        if not isinstance(raw, list):
            raise ConceptParseError(
                f"Field 'attachments' must be a list; "
                f"got {type(raw).__name__!r}"
            )

        _REQUIRED = {"id", "knowledge_object_id", "concept_id", "relevance"}
        result: List[Attachment] = []

        for idx, entry in enumerate(raw):
            if not isinstance(entry, dict):
                raise ConceptParseError(
                    f"attachments[{idx}] must be a mapping; "
                    f"got {type(entry).__name__!r}"
                )
            missing = _REQUIRED - entry.keys()
            if missing:
                raise ConceptParseError(
                    f"attachments[{idx}] is missing required field(s): "
                    f"{sorted(missing)}"
                )

            att_id = self._parse_uuid(entry["id"], f"attachments[{idx}].id")
            ko_id = self._parse_uuid(
                entry["knowledge_object_id"],
                f"attachments[{idx}].knowledge_object_id",
            )
            att_concept_id = self._parse_uuid(
                entry["concept_id"], f"attachments[{idx}].concept_id"
            )
            relevance = self._parse_float(
                entry["relevance"], f"attachments[{idx}].relevance"
            )

            try:
                att = Attachment(
                    id=att_id,
                    knowledge_object_id=ko_id,
                    concept_id=att_concept_id,
                    relevance=relevance,
                )
            except ValueError as exc:
                raise ConceptParseError(
                    f"Cannot construct attachments[{idx}]: {exc}"
                ) from exc

            result.append(att)

        return result

    def _build_updated_at(self, fm: Dict) -> Optional[datetime]:
        """Extract the optional ``updated_at`` field from *fm*.

        Parameters
        ----------
        fm : dict
            Parsed frontmatter dictionary.

        Returns
        -------
        datetime or None
            ``None`` when the field is absent or explicitly ``null``.
        """
        value = fm.get("updated_at")
        if value is None:
            return None
        return self._parse_datetime(value, "updated_at")
