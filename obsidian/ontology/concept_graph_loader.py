"""Loader that reconstructs a ConceptGraph from concept Markdown files on disk.

The loader discovers all ``.md`` files in a directory, parses each one with
:class:`~obsidian.ontology.concept_parser.ConceptParser`, and assembles a
fully populated :class:`~obsidian.ontology.concept_graph.ConceptGraph`.

Loading is **atomic with respect to unreadable files**: every ``.md`` file is
parsed before any object is inserted into the graph, so a malformed or
unparseable file aborts the whole load and raises
:class:`ConceptGraphLoadError` — that class of failure indicates the file
itself is broken and there is nothing safe to reconstruct from it.

Loading is **resilient with respect to stale cross-references**: a
Relationship or Attachment whose endpoint Concept is missing (for example
because the process that wrote it crashed after persisting one side of a
multi-file write, see :class:`~obsidian.ontology.ontology_pipeline.OntologyPipeline`)
is skipped rather than aborting the load, since the rest of the graph is
still valid and Haven should still start. Each skip is logged individually
via ``logging`` (module logger ``obsidian.ontology.concept_graph_loader``) at
``WARNING`` with the offending id and source file, followed by a summary
line with the total count once loading finishes.

Loading order is **deterministic**: files are processed in lexicographic
filename order.  Objects are inserted in three phases — Concepts first,
Relationships second, Attachments third — so that cross-file relationship
endpoints are always registered before the edge is wired up.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Tuple

from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.concept_parser import ConceptParseError, ConceptParseResult, ConceptParser

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class ConceptGraphLoadError(Exception):
    """Raised when a ConceptGraph cannot be loaded from disk.

    Always includes the filename that triggered the failure so callers can
    identify and fix the offending file without scanning the whole directory.
    """


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class ConceptGraphLoader:
    """Reconstruct a :class:`~obsidian.ontology.concept_graph.ConceptGraph`
    from a directory of concept Markdown files.

    The loader is **stateless** — it holds no mutable state and can be
    reused to load multiple directories sequentially or concurrently.

    Examples
    --------
    >>> from pathlib import Path
    >>> loader = ConceptGraphLoader()
    >>> graph = loader.load(Path("/path/to/concepts"))
    """

    def __init__(self) -> None:
        self._parser = ConceptParser()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, directory: Path) -> ConceptGraph:
        """Discover, parse, and assemble all concept Markdown files in *directory*.

        Non-``.md`` files and subdirectories are silently ignored.

        Parameters
        ----------
        directory : Path
            Directory that contains concept ``.md`` files.

        Returns
        -------
        ConceptGraph
            Graph containing every Concept found across all discovered
            files, plus every Relationship and Attachment whose endpoint
            Concept(s) actually exist. A Relationship or Attachment with a
            missing endpoint is skipped and logged (see the module
            docstring) rather than included half-wired.

        Raises
        ------
        ConceptGraphLoadError
            If *directory* does not exist or is not a directory, or if any
            ``.md`` file fails to parse. A missing Relationship/Attachment
            endpoint does *not* raise — see the module docstring.
        """
        directory = Path(directory)
        if not directory.exists():
            raise ConceptGraphLoadError(f"Directory not found: {directory}")
        if not directory.is_dir():
            raise ConceptGraphLoadError(f"Path is not a directory: {directory}")

        # Discover .md files, sorted by filename for deterministic ordering.
        md_files: List[Path] = sorted(
            (p for p in directory.iterdir() if p.is_file() and p.suffix == ".md"),
            key=lambda p: p.name,
        )

        # --- Phase 0: parse all files before touching the graph (fail-fast) ---
        parsed: List[Tuple[Path, ConceptParseResult]] = []
        for path in md_files:
            try:
                parsed.append((path, self._parser.read(path)))
            except ConceptParseError as exc:
                raise ConceptGraphLoadError(
                    f"Failed to parse '{path.name}': {exc}"
                ) from exc

        # Build into a temporary graph; return it only on full success.
        graph = ConceptGraph()

        # --- Phase 1: all Concepts first so relationship endpoints exist ---
        for _path, result in parsed:
            graph.add_concept(result.concept)

        # --- Phase 2: Relationships (both endpoints must already be present).
        # A missing endpoint means one half of a multi-file write never made
        # it to disk (see the module docstring) -- the rest of the graph is
        # still trustworthy, so this is skipped and logged, not raised.
        skipped_relationships = 0
        for path, result in parsed:
            for relationship in result.relationships:
                try:
                    graph.add_relationship(relationship)
                except KeyError as exc:
                    skipped_relationships += 1
                    logger.warning(
                        "Skipping Relationship %s in '%s': references an "
                        "unknown Concept endpoint: %s",
                        relationship.id,
                        path.name,
                        exc,
                    )
        if skipped_relationships:
            logger.warning(
                "ConceptGraph load: skipped %d relationship(s) with missing "
                "Concept endpoints.",
                skipped_relationships,
            )

        # --- Phase 3: Attachments (concept must already be present). Same
        # stale-cross-reference risk and handling as Phase 2 above. ---
        skipped_attachments = 0
        for path, result in parsed:
            for attachment in result.attachments:
                try:
                    graph.add_attachment(attachment)
                except KeyError as exc:
                    skipped_attachments += 1
                    logger.warning(
                        "Skipping Attachment %s in '%s': references an "
                        "unknown Concept: %s",
                        attachment.id,
                        path.name,
                        exc,
                    )
        if skipped_attachments:
            logger.warning(
                "ConceptGraph load: skipped %d attachment(s) with missing "
                "Concepts.",
                skipped_attachments,
            )

        return graph
