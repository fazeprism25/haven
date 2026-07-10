"""Checkpoint Writer for the Haven Conversation Checkpoint subsystem.

Mirrors :class:`obsidian.memory_engine.vault_writer.VaultWriter`'s shape
(a directory, a ``write()`` method, a deterministic filename derived from
the object's own id) but with none of the Obsidian-facing Markdown/YAML
theater: a checkpoint is never meant to be opened as a vault note, so it
is stored as plain JSON.

Wired into ``obsidian.server.main.save_memory`` for conversation-level
duplicate prevention -- see ``obsidian/checkpoint/__init__.py``'s module
docstring for the full scope of what's wired in.
"""

from __future__ import annotations

import json
from pathlib import Path

from obsidian.checkpoint.models import ConversationCheckpoint


class CheckpointWriter:
    """Writes a :class:`ConversationCheckpoint` to a deterministic JSON file.

    The output is deterministic: writing the same checkpoint twice
    produces byte-identical content (keys are sorted, matching
    :class:`~obsidian.checkpoint.store.CheckpointStore`'s expectation that
    re-reading an unmodified file is a no-op).
    """

    def __init__(self, checkpoint_dir: Path) -> None:
        """Initialise the writer with a target checkpoint directory.

        Parameters
        ----------
        checkpoint_dir : Path
            The root directory where checkpoint files will be written.
        """
        self._checkpoint_dir = Path(checkpoint_dir)

    def write(self, checkpoint: ConversationCheckpoint) -> Path:
        """Write *checkpoint* to a deterministic JSON file.

        Parameters
        ----------
        checkpoint : ConversationCheckpoint
            The checkpoint to persist.

        Returns
        -------
        Path
            The absolute path of the written file.
        """
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

        file_path = self._checkpoint_dir / f"{checkpoint.conversation_id}.json"

        content = json.dumps(checkpoint.to_dict(), indent=2, sort_keys=True)
        file_path.write_text(content, encoding="utf-8")

        return file_path
