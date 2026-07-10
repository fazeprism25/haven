"""Turn classification for the Haven Conversation Checkpoint subsystem.

Answers one question, deterministically and without any I/O: given a
conversation's *previous* checkpoint (if any) and its *current* per-turn
hashes (see :mod:`obsidian.checkpoint.hashing`), what changed, and which
turns are genuinely new evidence?

This module never reads a checkpoint from disk and never touches the
Manager AI pipeline -- callers pass in an already-loaded
:class:`~obsidian.checkpoint.models.ConversationCheckpoint` (or ``None``)
and the freshly computed turn hashes for the incoming request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional

from obsidian.checkpoint.models import ConversationCheckpoint

#: The three ways a conversation can relate to its previous checkpoint.
#:
#: ``"first_run"``
#:     No checkpoint exists yet for this conversation -- every turn is new.
#: ``"incremental"``
#:     A checkpoint exists, and every one of its turn hashes is an exact,
#:     in-order prefix of the incoming turn hashes, which are themselves
#:     strictly longer -- a pure append. Only the turns after that prefix
#:     are new evidence.
#: ``"fallback"``
#:     A checkpoint exists, but the incoming turn hashes are not a clean
#:     extension of it (an earlier turn was edited, deleted, reordered, or
#:     truncated). The conversation must be reprocessed from turn 0, since
#:     there is no reliable "new turns only" slice to take.
TurnDiffMode = Literal["first_run", "incremental", "fallback"]


@dataclass(frozen=True)
class TurnDiff:
    """The result of classifying a conversation against its checkpoint.

    Parameters
    ----------
    mode : TurnDiffMode
        Which of the three cases applied. See :data:`TurnDiffMode`.
    new_turn_start_index : int
        The index (into the incoming turn list) where new evidence begins.
        ``0`` for ``"first_run"`` and ``"fallback"`` (the entire
        conversation is evidence); the previous checkpoint's ``turn_count``
        for ``"incremental"`` (only the appended turns are evidence).
    """

    mode: TurnDiffMode
    new_turn_start_index: int


def classify_turns(
    checkpoint: Optional[ConversationCheckpoint],
    new_turn_hashes: List[str],
) -> TurnDiff:
    """Classify an incoming conversation's turns against its prior checkpoint.

    Parameters
    ----------
    checkpoint : ConversationCheckpoint, optional
        The conversation's previously persisted checkpoint, or ``None`` if
        none exists yet.
    new_turn_hashes : list[str]
        Per-turn hashes for *every* turn in the incoming request, in order,
        as returned by :func:`obsidian.checkpoint.hashing.turn_hash`.

    Returns
    -------
    TurnDiff
        The classification and the start index of the new evidence.

    Notes
    -----
    Callers are expected to have already handled the "nothing changed at
    all" case (via a whole-transcript hash comparison) before reaching this
    function -- it only distinguishes *how* a changed conversation changed,
    not whether it changed. Given that, an incoming transcript identical in
    length and content to the checkpoint's would fall through to
    ``"fallback"`` here (its prefix check trivially matches but the
    "strictly longer" requirement fails), which is harmless: callers never
    call this function for that case in practice.

    Examples
    --------
    >>> classify_turns(None, ["a", "b"]).mode
    'first_run'
    """
    if checkpoint is None:
        return TurnDiff(mode="first_run", new_turn_start_index=0)

    old_hashes = checkpoint.turn_hashes
    is_strict_extension = (
        len(new_turn_hashes) > len(old_hashes)
        and new_turn_hashes[: len(old_hashes)] == old_hashes
    )
    if is_strict_extension:
        return TurnDiff(mode="incremental", new_turn_start_index=len(old_hashes))

    return TurnDiff(mode="fallback", new_turn_start_index=0)
