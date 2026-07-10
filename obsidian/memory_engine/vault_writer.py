"""Vault Writer stage of the Manager AI pipeline.

The VaultWriter persists a :class:`KnowledgeObject` as a deterministic
Markdown file with YAML frontmatter, formatted so the vault directory is
directly openable as an Obsidian vault.

Frontmatter
-----------
Every field :meth:`KnowledgeObject.from_dict` already reads (``id``,
``canonical_fact``, ``memory_type``, ``confidence``, ``importance``,
``valid_from``, ``valid_until``, ``last_confirmed``, ``confirmation_count``,
``metadata``) is written exactly as before — nothing is renamed, reordered,
or removed, so :class:`~obsidian.memory_engine.memory_parser.MemoryParser`
and :class:`~obsidian.memory_engine.memory_store.MemoryStore` keep parsing
these files unchanged. Two Obsidian-native keys are added alongside them:

* ``tags`` — a single hierarchical tag, ``memory/<memory_type>`` (e.g.
  ``memory/decision``), so memories are browsable by type in Obsidian's
  tag pane and graph filters.
* ``aliases`` — the memory's own ``canonical_fact``, so the note (whose
  filename is its UUID, not its text) can still be found and linked to by
  its actual content in Obsidian's link autocomplete and search.
* ``title`` — the memory's own ``canonical_fact``, verbatim. The filename
  is a UUID, not readable text, so without this key every note's Obsidian
  Properties pane and file list would show only the UUID; this gives it a
  readable title instead.

All three are additive: ``KnowledgeObject.from_dict`` ignores unknown
frontmatter keys, so old vault files (without them) and new vault files
(with them) parse identically — see ``obsidian/tests/test_vault_writer.py``
for the backwards-compatibility tests covering this.

Body — Title, Decision, Related Concepts / Relationships
----------------------------------------------------------
The body opens with ``# <canonical_fact>`` — the same text as the
``title``/``aliases`` frontmatter keys — so the note reads as a titled
document rather than starting mid-heading-hierarchy at ``##``.

For a memory carrying :class:`~obsidian.manager_ai.models.DecisionMetadata`
(``knowledge.metadata["decision"]``, read via
:func:`~obsidian.manager_ai.models.get_decision_metadata`), the body also
gains a ``## Decision`` section: status, reason, alternatives considered,
and — the point of the section — ``supersedes``/``superseded_by`` each
rendered as a bare ``[[<uuid>]]`` wiki-link. Both sides of a supersession
pair are always already-written ``{uuid}.md`` files by the time either one
is written (see ``obsidian/manager_ai/knowledge_updater.py``'s
``supersede_decision``, which returns and expects the caller to persist
both the archived old decision and the new one), so the link always
resolves. This section is omitted entirely — not a placeholder — for any
memory without decision metadata, which is every non-decision memory and
every decision written before this feature existed.

When constructed with a ``concept_graph`` (optional; ``None`` by default,
matching every existing caller), the Markdown body gains two further
sections built entirely from whatever
:class:`~obsidian.ontology.concept_graph.ConceptGraph` state already
exists at write time — no ontology extraction, scoring, proposal, or graph
mutation happens here, only read-only lookups. A Concept is considered
"related" to *knowledge* by either of two signals:

* **Already attached** — ``concept_graph.concepts_for_knowledge_object``
  returns it, the same lookup
  :func:`obsidian.server.dashboard._ontology_for_memory` already uses for
  the Retrieval Inspector. Every caller of this module calls
  ``vault_writer.write()`` *before* ``ontology_pipeline.process()``
  populates the graph for that exact memory (see
  ``obsidian/server/main.py``'s documented call order), so this signal is
  only ever non-empty for a memory being *rewritten* after its own
  ontology processing already ran once — e.g. during Decision Memory
  supersession.
* **Mentioned** — the Concept's label or an alias already appears as a
  whole word/phrase (case-insensitive) in ``knowledge.canonical_fact``,
  checked against every Concept ``concept_graph.all_concepts()`` already
  knows about. This is what makes a brand-new memory link to Concepts
  *other* memories already introduced, immediately, in its very first
  write — it never creates a Concept, Attachment, or Relationship; it only
  decides which already-real Concepts are worth a Markdown link.

Related Concepts lists the union of both signals; Relationships then
lists every Relationship touching any of them
(``concept_graph.relationships``, deduplicated by relationship id), so
the note also links one hop out to neighbouring Concepts.

Concept notes are filed by UUID (see
:meth:`~obsidian.ontology.concept_writer.ConceptWriter.write`), so links
use Obsidian's piped syntax, ``[[<uuid>|<Label>]]``: the link still
resolves and appears as a real edge in Obsidian's graph view, while the
rendered text shows the human-readable label rather than a raw UUID.

Without a ``concept_graph`` — or when neither signal finds anything (e.g.
the very first memory to ever mention a brand-new Concept) — both
sections render a plain "nothing yet" placeholder instead of a link list.
That is expected, not an error: a vault fills in with links incrementally
as the same Concepts recur across later memories, exactly like Haven's
ontology graph itself grows over time. No synchronization, background
job, or file watcher backfills links into already-written notes — this
module only ever reacts to an explicit :meth:`VaultWriter.write` call.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

import yaml

from obsidian.manager_ai.models import (
    DecisionMetadata,
    EvidenceEntry,
    KnowledgeObject,
    get_decision_metadata,
)
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.models import Concept, Relationship
from obsidian.ontology.text_utils import normalize


def _mentions(surface_form: str, normalized_text: str) -> bool:
    """Return ``True`` if *surface_form* appears as a whole word/phrase in *normalized_text*.

    Both sides are matched case-insensitively via
    :func:`~obsidian.ontology.text_utils.normalize`; word-boundary anchors
    avoid matching a short label inside an unrelated longer word (e.g.
    label ``"AI"`` must not match inside ``"said"``).
    """
    pattern = r"\b" + re.escape(normalize(surface_form)) + r"\b"
    return re.search(pattern, normalized_text) is not None


def _wiki_link(concept_id: UUID, label: str) -> str:
    """Return an Obsidian piped wiki-link resolving to *concept_id*, displayed as *label*.

    Concept notes are filed by UUID (see
    :meth:`~obsidian.ontology.concept_writer.ConceptWriter.write`), so a
    plain ``[[label]]`` link would not resolve. The piped form
    ``[[<uuid>|<label>]]`` resolves correctly while still rendering the
    human-readable label. ``|``/``[[``/``]]`` are stripped from *label*
    first since they would otherwise break the link's own syntax.
    """
    safe_label = label.replace("|", "/").replace("[[", "").replace("]]", "")
    return f"[[{concept_id}|{safe_label}]]"


class VaultWriter:
    """Writes a :class:`KnowledgeObject` to a deterministic Markdown file.

    The output is deterministic: writing the same object twice produces
    identical content except where timestamps intentionally differ
    (``valid_from``, ``valid_until``, ``last_confirmed``, and the
    ``timestamp`` field of each :class:`EvidenceEntry``), and except for the
    Related Concepts/Relationships sections, which reflect whatever the
    supplied ``concept_graph`` contains at call time (see module docstring).
    """

    def __init__(
        self, vault_dir: Path, concept_graph: Optional[ConceptGraph] = None
    ) -> None:
        """Initialise the writer with a target vault directory.

        Parameters
        ----------
        vault_dir : Path
            The root directory where memory files will be written.
        concept_graph : ConceptGraph, optional
            Read-only source for the Markdown body's Related
            Concepts/Relationships sections (see module docstring). When
            ``None`` (the default — every pre-existing caller), those
            sections render a "nothing yet" placeholder and the rest of
            the file is byte-for-byte identical to before this parameter
            existed.
        """
        self._vault_dir = vault_dir
        self._concept_graph = concept_graph

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, knowledge: KnowledgeObject) -> Path:
        """Write *knowledge* to a deterministic Markdown file.

        Parameters
        ----------
        knowledge : KnowledgeObject
            The knowledge object to persist.

        Returns
        -------
        Path
            The absolute path of the written file.
        """
        # Ensure the vault directory exists
        self._vault_dir.mkdir(parents=True, exist_ok=True)

        # Build the file path using the object's id
        file_path = self._vault_dir / f"{knowledge.id}.md"

        # Build the YAML frontmatter dictionary
        frontmatter = self._build_frontmatter(knowledge)

        # Build the markdown body
        body = self._build_body(knowledge)

        # Combine frontmatter and body
        content = self._render(frontmatter, body)

        # Write the file
        file_path.write_text(content, encoding="utf-8")

        return file_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_frontmatter(knowledge: KnowledgeObject) -> Dict[str, Any]:
        """Build the YAML frontmatter dictionary for *knowledge*.

        The dictionary contains all persistent metadata required to
        reconstruct the :class:`KnowledgeObject`, plus ``tags``/``aliases``
        for Obsidian (see module docstring).
        """
        frontmatter: Dict[str, Any] = {
            "id": str(knowledge.id),
            "title": knowledge.canonical_fact,
            "canonical_fact": knowledge.canonical_fact,
            "memory_type": knowledge.memory_type.value,
            "confidence": knowledge.confidence,
            "importance": knowledge.importance,
            "valid_from": knowledge.valid_from.isoformat(),
            "valid_until": (
                knowledge.valid_until.isoformat()
                if knowledge.valid_until is not None
                else None
            ),
            "last_confirmed": (
                knowledge.last_confirmed.isoformat()
                if knowledge.last_confirmed is not None
                else None
            ),
            "confirmation_count": knowledge.confirmation_count,
            "metadata": knowledge.metadata,
            "tags": [f"memory/{knowledge.memory_type.value}"],
            "aliases": [knowledge.canonical_fact],
        }
        return frontmatter

    def _build_body(self, knowledge: KnowledgeObject) -> str:
        """Build the human‑readable markdown body for *knowledge*.

        The body opens with a title heading, then contains the canonical
        fact, the evidence chain, an optional Decision section, and the
        Obsidian-facing Related Concepts/Relationships sections (see
        module docstring).
        """
        lines: List[str] = []

        # Title heading
        lines.append(f"# {knowledge.canonical_fact}")
        lines.append("")

        # Canonical fact heading
        lines.append("## Canonical Fact")
        lines.append("")
        lines.append(knowledge.canonical_fact)
        lines.append("")

        # Evidence chain heading
        lines.append("## Evidence Chain")
        lines.append("")

        if not knowledge.evidence_chain:
            lines.append("_No evidence recorded._")
            lines.append("")
        else:
            for idx, entry in enumerate(knowledge.evidence_chain, start=1):
                lines.append(f"### Evidence {idx}")
                lines.append("")
                lines.append(f"- **Source Event ID**: `{entry.source_event_id}`")
                lines.append(f"- **Evidence**: {entry.evidence}")
                lines.append(f"- **Confidence**: {entry.confidence}")
                lines.append(
                    f"- **Timestamp**: {entry.timestamp.isoformat()}"
                    if entry.timestamp is not None
                    else "- **Timestamp**: _not recorded_"
                )
                lines.append("")

        lines.extend(self._build_decision_section(knowledge))
        lines.extend(self._build_obsidian_sections(knowledge))

        return "\n".join(lines)

    @staticmethod
    def _build_decision_section(knowledge: KnowledgeObject) -> List[str]:
        """Build the "Decision" body section, or nothing at all.

        Omitted entirely — not a placeholder — for any memory without
        :class:`~obsidian.manager_ai.models.DecisionMetadata` (every
        non-decision memory, and every decision written before this
        feature existed). See module docstring.
        """
        decision: Optional[DecisionMetadata] = get_decision_metadata(knowledge)
        if decision is None:
            return []

        lines: List[str] = ["## Decision", ""]
        lines.append(f"- **Status**: {decision.status.value}")
        if decision.reason.strip():
            lines.append(f"- **Reason**: {decision.reason}")
        if decision.alternatives_considered:
            lines.append("- **Alternatives Considered**:")
            for alternative in decision.alternatives_considered:
                lines.append(f"  - {alternative}")
        if decision.supersedes is not None:
            lines.append(f"- **Supersedes**: [[{decision.supersedes}]]")
        if decision.superseded_by is not None:
            lines.append(f"- **Superseded By**: [[{decision.superseded_by}]]")
        lines.append("")

        return lines

    def _related_concepts(self, knowledge: KnowledgeObject) -> Optional[List[Concept]]:
        """Return the Concepts related to *knowledge* (attached or mentioned), or ``None``.

        ``None`` means no ``concept_graph`` was supplied at all; an empty
        list means a graph was supplied but neither signal (see module
        docstring) found anything for this KnowledgeObject yet. Sorted by
        Concept UUID string for determinism.
        """
        if self._concept_graph is None:
            return None

        related: Dict[UUID, Concept] = {
            concept.id: concept
            for concept in self._concept_graph.concepts_for_knowledge_object(
                knowledge.id
            )
        }

        normalized_text = normalize(knowledge.canonical_fact)
        for concept in self._concept_graph.all_concepts():
            if concept.id in related:
                continue
            surface_forms = (concept.label,) + concept.aliases
            if any(_mentions(form, normalized_text) for form in surface_forms):
                related[concept.id] = concept

        return sorted(related.values(), key=lambda c: str(c.id))

    def _relationships_for(self, concepts: List[Concept]) -> List[Relationship]:
        """Return every Relationship touching any of *concepts*, deduplicated.

        Mirrors :func:`obsidian.server.dashboard._ontology_for_memory`'s
        dedup-by-id pattern exactly, since a relationship between two of
        *concepts* would otherwise be counted (and linked) twice.
        """
        assert self._concept_graph is not None
        seen: Dict[UUID, Relationship] = {}
        for concept in concepts:
            for relationship in self._concept_graph.relationships(concept.id):
                seen.setdefault(relationship.id, relationship)
        return sorted(seen.values(), key=lambda r: str(r.id))

    def _build_obsidian_sections(self, knowledge: KnowledgeObject) -> List[str]:
        """Build the "Related Concepts"/"Relationships" body sections.

        See module docstring: both degrade to a placeholder line, never an
        error, when ``concept_graph`` is unavailable or has nothing yet
        for this KnowledgeObject.
        """
        lines: List[str] = []
        concepts = self._related_concepts(knowledge)

        lines.append("## Related Concepts")
        lines.append("")
        if concepts is None:
            lines.append("_No concept graph was supplied when this note was written._")
        elif not concepts:
            lines.append("_No concepts attached yet._")
        else:
            for concept in concepts:
                lines.append(f"- {_wiki_link(concept.id, concept.label)}")
        lines.append("")

        lines.append("## Relationships")
        lines.append("")
        if concepts is None:
            lines.append("_No concept graph was supplied when this note was written._")
        elif not concepts:
            lines.append("_No concepts attached yet._")
        else:
            relationships = self._relationships_for(concepts)
            if not relationships:
                lines.append("_No relationships among attached concepts._")
            else:
                for relationship in relationships:
                    source = self._concept_graph.get_concept(relationship.source_id)
                    target = self._concept_graph.get_concept(relationship.target_id)
                    lines.append(
                        f"- {_wiki_link(source.id, source.label)} "
                        f"**{relationship.relationship_type.value}** "
                        f"{_wiki_link(target.id, target.label)} "
                        f"(confidence: {relationship.confidence:.4f})"
                    )
        lines.append("")

        return lines

    @staticmethod
    def _render(frontmatter: Dict[str, Any], body: str) -> str:
        """Render the YAML frontmatter and markdown body into a single string.

        The frontmatter is serialised with ``yaml.dump`` using a
        deterministic style (no aliases, no default flow style).
        """
        yaml_str = yaml.dump(
            frontmatter,
            default_flow_style=False,
            sort_keys=False,
            allow_unicode=True,
        )
        return f"---\n{yaml_str}---\n\n{body}\n"
