"""Memory Store for the Haven Memory Engine.

Loads every :class:`~obsidian.manager_ai.models.KnowledgeObject` out of a
vault directory exactly once and caches it in memory for fast UUID lookup
and full iteration.

The Memory Store has exactly one responsibility: turn a directory of
Markdown files written by
:class:`~obsidian.memory_engine.vault_writer.VaultWriter` back into an
in-memory collection of ``KnowledgeObject`` instances.

Design constraints
-------------------
* **No ranking** — candidates are never scored or ordered by relevance.
* **No retrieval** — no querying, keyword matching, or filtering by intent.
* **No slot allocation** — no context budget or slot logic.
* **No context building** — no prompt assembly.
* **No concept awareness** — the Ontology subsystem is out of scope; this
  module never imports from :mod:`obsidian.ontology`.
* **Deterministic** — loading the same vault directory twice yields
  identical results. Files are discovered in lexicographic order
  (delegated to :class:`~obsidian.memory_engine.vault_index.VaultIndex`),
  and any parsed record missing the one field required to guarantee
  determinism (``id``) is rejected rather than silently assigned a
  fresh random UUID.
* **YAML-authoritative** — only the YAML frontmatter parsed by
  :class:`~obsidian.memory_engine.memory_parser.MemoryParser` is used to
  hydrate a ``KnowledgeObject``. The Markdown body is written by
  :class:`VaultWriter` for human readers only and is never parsed here,
  matching the convention already established by
  :mod:`obsidian.ontology.concept_parser`. Note that because
  :class:`VaultWriter` does not persist ``evidence_chain`` into
  frontmatter, hydrated objects always have an empty ``evidence_chain``;
  this is a limitation of the existing write path, not of this loader.
* **Atomic load** — every file in the vault is parsed before the cache is
  replaced. A single failure anywhere leaves the previous cache (if any)
  untouched and raises :class:`~obsidian.core.errors.MemoryEngineError`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from obsidian.core.errors import MemoryEngineError
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine.memory_parser import MemoryParser, ParsedMemory
from obsidian.memory_engine.vault_index import VaultIndex


class MemoryStore:
    """Loads and caches every ``KnowledgeObject`` found in a vault directory.

    Parameters
    ----------
    vault_dir : Path
        Root directory containing ``KnowledgeObject`` Markdown files, as
        written by :class:`~obsidian.memory_engine.vault_writer.VaultWriter`.

    Examples
    --------
    >>> store = MemoryStore(Path("/path/to/vault"))
    >>> store.load()
    >>> store.count()
    3
    >>> ko = store.get(some_uuid)
    >>> all_memories = store.all()
    """

    def __init__(self, vault_dir: Path) -> None:
        self._vault_dir = Path(vault_dir)
        self._index = VaultIndex(self._vault_dir)
        self._parser = MemoryParser()
        self._by_id: Dict[UUID, KnowledgeObject] = {}
        # Cache for all(): built lazily on first call after each load() and
        # invalidated (set back to None) the moment the cache it was built
        # from is replaced. Every call site (HybridCandidateRetriever's
        # keyword path, the CONTINUATION category-fallback path, and the
        # dashboard's own memories = memory_store.all()) can run more than
        # once against the same loaded snapshot within a single request, and
        # each call previously re-sorted every KnowledgeObject from scratch.
        self._all_cache: Optional[List[KnowledgeObject]] = None

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Discover, parse, and hydrate every ``KnowledgeObject`` in the vault.

        Safe to call more than once: each call re-scans the vault directory
        from scratch and atomically replaces the cache on full success.

        Raises
        ------
        MemoryEngineError
            If the vault directory does not exist or is not a directory;
            if any Markdown file fails to parse; if a parsed record is
            missing its ``id`` field; or if two files resolve to the same
            ``KnowledgeObject`` id.
        """
        if not self._vault_dir.exists():
            raise MemoryEngineError(
                f"Vault directory not found: {self._vault_dir}",
                field="vault_dir",
                context=str(self._vault_dir),
            )
        if not self._vault_dir.is_dir():
            raise MemoryEngineError(
                f"Vault path is not a directory: {self._vault_dir}",
                field="vault_dir",
                context=str(self._vault_dir),
            )

        self._index.scan()

        # --- Phase 0: parse every file before touching the cache (fail-fast) ---
        parsed: List[Tuple[Path, ParsedMemory]] = []
        for path in self._index.all_files():
            try:
                parsed.append((path, self._parser.parse(path)))
            except ValueError as exc:
                raise MemoryEngineError(
                    f"Failed to parse '{path.name}': {exc}",
                    field="vault_dir",
                    context=str(path),
                ) from exc

        # --- Phase 1: hydrate KnowledgeObjects; build into a temporary dict ---
        by_id: Dict[UUID, KnowledgeObject] = {}
        for path, memory in parsed:
            if "id" not in memory.metadata:
                raise MemoryEngineError(
                    f"'{path.name}' is missing required frontmatter field 'id'",
                    field="id",
                    context=str(path),
                )
            try:
                knowledge = KnowledgeObject.from_dict(memory.metadata)
            except (KeyError, ValueError, TypeError) as exc:
                raise MemoryEngineError(
                    f"Failed to hydrate KnowledgeObject from '{path.name}': {exc}",
                    field="vault_dir",
                    context=str(path),
                ) from exc

            if knowledge.id in by_id:
                raise MemoryEngineError(
                    f"Duplicate KnowledgeObject id {knowledge.id} found in '{path.name}'",
                    field="id",
                    context=str(path),
                )
            by_id[knowledge.id] = knowledge

        # Only replace the cache once every file has succeeded.
        self._by_id = by_id
        # Invalidate the sorted-all() cache -- it was built (if at all) from
        # the now-replaced _by_id snapshot.
        self._all_cache = None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, knowledge_object_id: UUID) -> KnowledgeObject:
        """Return the cached ``KnowledgeObject`` for *knowledge_object_id*.

        Raises
        ------
        KeyError
            If no ``KnowledgeObject`` with that id is cached.
        """
        return self._by_id[knowledge_object_id]

    def has(self, knowledge_object_id: UUID) -> bool:
        """Return ``True`` if a ``KnowledgeObject`` with that id is cached."""
        return knowledge_object_id in self._by_id

    def all(self) -> List[KnowledgeObject]:
        """Return every cached ``KnowledgeObject``.

        The sorted list is computed once per :meth:`load` and cached; a
        second call before the next :meth:`load` reuses it instead of
        re-sorting the same snapshot again. A fresh copy of the cached list
        is returned each time so a caller mutating its own copy can never
        observe or corrupt the cache.

        Returns
        -------
        list[KnowledgeObject]
            All cached objects, sorted by ``id`` string for deterministic
            iteration order regardless of file discovery or dict ordering.
        """
        if self._all_cache is None:
            self._all_cache = sorted(self._by_id.values(), key=lambda ko: str(ko.id))
        return list(self._all_cache)

    def count(self) -> int:
        """Return the number of cached ``KnowledgeObject`` instances."""
        return len(self._by_id)
