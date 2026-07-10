"""Checkpoint Store for the Haven Conversation Checkpoint subsystem.

Loads every :class:`~obsidian.checkpoint.models.ConversationCheckpoint`
out of a checkpoint directory and caches it in memory for fast UUID
lookup, mirroring :class:`obsidian.memory_engine.memory_store.MemoryStore`'s
scan-parse-atomically-swap shape -- with two deliberate policy
differences, both driven by checkpoints being an optimization/bookkeeping
layer rather than authoritative memory content:

* **Missing directory is not an error.** ``MemoryStore`` requires the
  vault directory to already exist, because ``main.py``'s startup always
  creates it first. A checkpoint directory legitimately may not exist yet
  -- "no conversations have ever been processed" is an ordinary state,
  not a misconfiguration -- so a missing directory loads as zero
  checkpoints rather than raising. A path that *exists but is not a
  directory* still raises (that is a genuine misconfiguration, the same
  distinction ``MemoryStore`` draws).
* **A single corrupt/unparseable/version-mismatched file does not abort
  the whole load.** ``MemoryStore`` fails the entire load if any one
  ``KnowledgeObject`` file is bad, because vault content is the source of
  truth and a silent partial load could hide data loss. A checkpoint is
  disposable bookkeeping: worst case from skipping a bad one is that its
  conversation gets fully reprocessed once more, which the existing
  ``CanonicalMatcher`` CONFIRM path already tolerates. So a bad file is
  skipped (recorded in :meth:`CheckpointStore.skipped_files`) and loading
  continues.

Wired into ``obsidian.server.main.save_memory`` for conversation-level
duplicate prevention -- see ``obsidian/checkpoint/__init__.py``'s module
docstring for the full scope of what's wired in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple
from uuid import UUID

from obsidian.checkpoint.models import CURRENT_SCHEMA_VERSION, ConversationCheckpoint


class CheckpointStore:
    """Loads and caches every ``ConversationCheckpoint`` found in a directory.

    Parameters
    ----------
    checkpoint_dir : Path
        Root directory containing ``ConversationCheckpoint`` JSON files, as
        written by :class:`~obsidian.checkpoint.writer.CheckpointWriter`.

    Examples
    --------
    >>> store = CheckpointStore(Path("/path/to/checkpoints"))
    >>> store.load()
    >>> store.count()
    2
    >>> checkpoint = store.get(some_conversation_id)
    >>> all_checkpoints = store.all()
    """

    def __init__(self, checkpoint_dir: Path) -> None:
        self._checkpoint_dir = Path(checkpoint_dir)
        self._by_id: Dict[UUID, ConversationCheckpoint] = {}
        self._skipped: List[Tuple[Path, str]] = []

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Discover, parse, and hydrate every checkpoint in the directory.

        Safe to call more than once: each call re-scans the checkpoint
        directory from scratch. The in-memory cache is only replaced once,
        at the end, from a freshly built dict -- so a call that raises
        (a genuine misconfiguration, see module docstring) leaves the
        previous cache untouched, exactly like
        :meth:`~obsidian.memory_engine.memory_store.MemoryStore.load`'s
        atomic-swap guarantee. Unlike ``MemoryStore``, a single bad file is
        skipped rather than aborting the entire load (see module
        docstring); skipped files are recorded and available via
        :meth:`skipped_files`.

        Raises
        ------
        NotADirectoryError
            If ``checkpoint_dir`` exists but is not a directory. A
            checkpoint directory that simply does not exist yet is *not*
            an error (see module docstring) -- it loads as zero
            checkpoints.
        """
        skipped: List[Tuple[Path, str]] = []

        if not self._checkpoint_dir.exists():
            self._by_id = {}
            self._skipped = skipped
            return

        if not self._checkpoint_dir.is_dir():
            raise NotADirectoryError(
                f"Checkpoint path is not a directory: {self._checkpoint_dir}"
            )

        by_id: Dict[UUID, ConversationCheckpoint] = {}
        for path in sorted(self._checkpoint_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                skipped.append((path, f"unreadable/invalid JSON: {exc}"))
                continue

            if not isinstance(raw, dict):
                skipped.append((path, "JSON content is not an object"))
                continue

            schema_version = raw.get("schema_version")
            if schema_version != CURRENT_SCHEMA_VERSION:
                skipped.append(
                    (
                        path,
                        f"schema_version mismatch (found {schema_version!r}, "
                        f"expected {CURRENT_SCHEMA_VERSION!r})",
                    )
                )
                continue

            try:
                checkpoint = ConversationCheckpoint.from_dict(raw)
            except (KeyError, ValueError, TypeError) as exc:
                skipped.append((path, f"failed to hydrate: {exc}"))
                continue

            by_id[checkpoint.conversation_id] = checkpoint

        # Only replace the cache once every file has been processed.
        self._by_id = by_id
        self._skipped = skipped

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, conversation_id: UUID) -> ConversationCheckpoint:
        """Return the cached checkpoint for *conversation_id*.

        Raises
        ------
        KeyError
            If no checkpoint with that id is cached.
        """
        return self._by_id[conversation_id]

    def has(self, conversation_id: UUID) -> bool:
        """Return ``True`` if a checkpoint with that id is cached."""
        return conversation_id in self._by_id

    def all(self) -> List[ConversationCheckpoint]:
        """Return every cached checkpoint.

        Returns
        -------
        list[ConversationCheckpoint]
            All cached checkpoints, sorted by ``conversation_id`` string
            for deterministic iteration order regardless of file discovery
            or dict ordering.
        """
        return sorted(self._by_id.values(), key=lambda c: str(c.conversation_id))

    def count(self) -> int:
        """Return the number of cached checkpoints."""
        return len(self._by_id)

    def skipped_files(self) -> List[Tuple[Path, str]]:
        """Return every file skipped during the last :meth:`load` call.

        Each entry is ``(path, reason)``. Empty immediately after
        construction (before the first :meth:`load`) and reset at the
        start of every subsequent :meth:`load` call.
        """
        return list(self._skipped)
