"""Haven Conversation Checkpoint subsystem.

The Checkpoint subsystem lets Haven recognise when a conversation has
already been processed, so re-clicking "Remember" (or a future automatic
poller) does not blindly re-run extraction against content already seen.
It is an *identity and bookkeeping* layer, independent of the Manager AI
pipeline, the vault, and the browser extension -- none of those are
touched by this package.

Scope so far (PR 1 + PR 2 + PR 3 + PR 4)
------------------------------------------
* :mod:`~obsidian.checkpoint.identity` -- deterministic UUID factories for
  conversation and event identifiers, mirroring
  :mod:`obsidian.ontology.identity`'s existing UUID5 convention. (PR 1)
* :mod:`~obsidian.checkpoint.hashing` -- turn-level and transcript-level
  content hashing, used to detect unchanged vs. new vs. edited turns.
  (PR 1)
* :mod:`~obsidian.checkpoint.models` -- :class:`ConversationCheckpoint` and
  :class:`CheckpointRun`, the bookkeeping record itself. (PR 2)
* :mod:`~obsidian.checkpoint.store` -- :class:`CheckpointStore`, loading
  and caching checkpoints from disk, mirroring
  :class:`obsidian.memory_engine.memory_store.MemoryStore`. (PR 2)
* :mod:`~obsidian.checkpoint.writer` -- :class:`CheckpointWriter`,
  persisting a checkpoint as deterministic JSON, mirroring
  :class:`obsidian.memory_engine.vault_writer.VaultWriter`. (PR 2)
* ``obsidian.server.main.save_memory`` -- conversation-level duplicate
  prevention: an unchanged transcript short-circuits the entire Manager AI
  pipeline. (PR 3)
* :mod:`~obsidian.checkpoint.diff` -- :func:`~obsidian.checkpoint.diff.classify_turns`,
  the pure function that tells ``save_memory`` whether a changed
  conversation is a first run, a pure append (only the new turns need to be
  sent to the Extractor), or a fallback (something earlier than the last
  processed turn changed, so the whole conversation must be reprocessed).
  (PR 4)

Out of scope so far
--------------------
* Memory provenance metadata on ``KnowledgeObject``.
* Semantic/embedding-based duplicate matching (``CanonicalMatcher`` still
  does exact normalised-string matching only).

Each of the above lands in a later, separately reviewed PR.
"""

from __future__ import annotations

from obsidian.checkpoint.diff import TurnDiff, TurnDiffMode, classify_turns
from obsidian.checkpoint.hashing import transcript_hash, turn_hash
from obsidian.checkpoint.identity import (
    CHECKPOINT_NAMESPACE,
    derive_conversation_id,
    derive_event_id,
)
from obsidian.checkpoint.models import (
    CURRENT_SCHEMA_VERSION,
    CheckpointRun,
    ConversationCheckpoint,
)
from obsidian.checkpoint.store import CheckpointStore
from obsidian.checkpoint.writer import CheckpointWriter

__all__ = [
    # Identity helpers
    "CHECKPOINT_NAMESPACE",
    "derive_conversation_id",
    "derive_event_id",
    # Hashing helpers
    "transcript_hash",
    "turn_hash",
    # Turn classification
    "TurnDiff",
    "TurnDiffMode",
    "classify_turns",
    # Models
    "CURRENT_SCHEMA_VERSION",
    "CheckpointRun",
    "ConversationCheckpoint",
    # Persistence
    "CheckpointStore",
    "CheckpointWriter",
]
