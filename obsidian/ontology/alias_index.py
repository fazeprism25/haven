"""Deterministic alias index for O(1) lookup from query text to Concept IDs.

Conflict policy
---------------
When two different Concepts produce the same normalised key (via label or
any alias), the Concept whose UUID string is lexicographically smallest
wins.  Input Concepts are sorted by ``str(concept.id)`` before the index is
built, so the winner is the same regardless of the order in which Concepts
are supplied to :meth:`AliasIndex.build`.

Conflicts are recorded in :attr:`AliasIndex.conflicts` for inspection but
never raise an exception; the winning entry is kept and the losing entry is
silently discarded.  Silent *overwrites* are never performed — the index
slots are allocated in UUID order so no slot is written twice for a
cross-concept conflict.

Design constraints
------------------
* O(1) lookup via a plain ``dict``.
* Normalisation is delegated entirely to
  :func:`~obsidian.ontology.text_utils.normalize` so that write-path and
  read-path keys are always identical.
* No fuzzy matching, embeddings, LLM calls, retrieval logic, graph
  traversal, or Markdown I/O.
* Stateless after construction except :meth:`AliasIndex.rebuild`.
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Tuple
from uuid import UUID

from obsidian.ontology.models import Concept
from obsidian.ontology.text_utils import normalize


class AliasIndex:
    """O(1) lookup from normalised query text to a Concept UUID.

    Build the index once with :meth:`build`, then call :meth:`lookup` or
    :meth:`contains` as many times as needed.  Use :meth:`rebuild` to
    replace the index atomically without creating a new instance.

    Examples
    --------
    >>> idx = AliasIndex()
    >>> c = Concept.from_label("Haven", aliases=("Personal Brain",))
    >>> idx.build([c])
    >>> idx.lookup("haven") == c.id
    True
    >>> idx.lookup("PERSONAL BRAIN") == c.id
    True
    >>> idx.lookup("unknown") is None
    True
    """

    def __init__(self) -> None:
        self._index: Dict[str, UUID] = {}
        # normalised key → (winner_id, loser_id) for detected cross-concept conflicts
        self._conflicts: Dict[str, Tuple[UUID, UUID]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, concepts: Iterable[Concept]) -> None:
        """Build the index from *concepts*.

        Both the label and every alias of each Concept are normalised and
        stored.  On normalised-key conflicts between different Concepts the
        Concept with the lexicographically smallest UUID string wins (see
        module docstring for the full policy).

        Parameters
        ----------
        concepts : iterable of Concept
        """
        self._index = {}
        self._conflicts = {}
        # Sort by UUID string for deterministic conflict resolution.
        sorted_concepts = sorted(concepts, key=lambda c: str(c.id))
        for concept in sorted_concepts:
            # Track keys seen for this concept so within-concept
            # label/alias collisions (same normalised form, same target)
            # are de-duplicated rather than treated as conflicts.
            seen_within: set[str] = set()
            for raw in (concept.label,) + concept.aliases:
                key = normalize(raw)
                if key in seen_within:
                    continue
                seen_within.add(key)
                if key not in self._index:
                    self._index[key] = concept.id
                elif self._index[key] != concept.id:
                    # Cross-concept conflict: the winner is already in the
                    # index (it arrived first because we sorted by UUID).
                    self._conflicts[key] = (self._index[key], concept.id)

    def lookup(self, label: str) -> Optional[UUID]:
        """Return the Concept UUID for *label*, or ``None`` if not found.

        *label* is normalised before lookup so the search is always
        case-insensitive and whitespace-tolerant.

        Parameters
        ----------
        label : str
            Raw query text (label or alias surface form).

        Returns
        -------
        UUID or None
        """
        return self._index.get(normalize(label))

    def contains(self, label: str) -> bool:
        """Return ``True`` if the normalised form of *label* is indexed.

        Parameters
        ----------
        label : str

        Returns
        -------
        bool
        """
        return normalize(label) in self._index

    def rebuild(self, concepts: Iterable[Concept]) -> None:
        """Atomically replace the current index with a new one.

        Builds into a temporary instance first; the current index is
        replaced only after the new build completes without error.

        Parameters
        ----------
        concepts : iterable of Concept
        """
        tmp = AliasIndex()
        tmp.build(concepts)
        self._index = tmp._index
        self._conflicts = tmp._conflicts

    def size(self) -> int:
        """Return the number of normalised keys currently in the index.

        Returns
        -------
        int
        """
        return len(self._index)

    @property
    def conflicts(self) -> Dict[str, Tuple[UUID, UUID]]:
        """Mapping of conflicting normalised keys to ``(winner_id, loser_id)``.

        Returns a shallow copy; mutations do not affect the internal state.

        Returns
        -------
        dict
        """
        return dict(self._conflicts)
