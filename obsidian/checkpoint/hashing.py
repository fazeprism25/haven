"""Content hashing for the Haven Checkpoint subsystem.

These hashes let the server answer two questions cheaply, without an LLM
call: "has this exact turn already been processed?" and "has anything at
all changed since I last processed this conversation?".

Unlike :func:`obsidian.ontology.identity.concept_id` -- which strips and
lowercases labels because they are human-entered natural-language text
where incidental case/whitespace shouldn't mint a new identity -- the
hashes here are deliberately **not** normalised. A turn's content is
hashed byte-for-byte: the entire purpose of these hashes is to detect
when a turn has changed at all (including a user editing or regenerating
an earlier message), so collapsing whitespace or case would silently
hide real edits.
"""

from __future__ import annotations

import hashlib
from typing import List

# Joins per-turn hashes before hashing the whole transcript. Turn hashes
# are hex digests (charset ``[0-9a-f]``), so any separator outside that
# charset is unambiguous; the ASCII "unit separator" control character is
# used for clarity that this is a structural join, not user content.
_TRANSCRIPT_JOIN_SEPARATOR = "\x1f"


def turn_hash(role: str, content: str) -> str:
    """Return a deterministic SHA-256 hex digest for one conversation turn.

    Parameters
    ----------
    role : str
        The turn's role, as a plain string (e.g. ``Role.USER.value``,
        matching the convention
        :func:`obsidian.ontology.identity.relationship_id` already uses
        for its ``rel_type`` parameter).
    content : str
        The turn's raw text content, unmodified.

    Returns
    -------
    str
        A 64-character SHA-256 hex digest of ``"{role}:{content}"``. The
        same ``(role, content)`` pair always returns the same digest.

    Examples
    --------
    >>> turn_hash("user", "hello") == turn_hash("user", "hello")
    True
    >>> turn_hash("user", "hello") == turn_hash("assistant", "hello")
    False
    """
    payload = f"{role}:{content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def transcript_hash(turn_hashes: List[str]) -> str:
    """Return a deterministic SHA-256 hex digest for a whole transcript.

    Parameters
    ----------
    turn_hashes : list[str]
        The ordered list of per-turn hashes, as returned by
        :func:`turn_hash`, for every turn in the conversation.

    Returns
    -------
    str
        A 64-character SHA-256 hex digest over the ordered
        ``turn_hashes``. The same sequence always returns the same
        digest; an empty list is a valid input and returns the digest of
        an empty transcript, not an error.

    Examples
    --------
    >>> transcript_hash(["aaa", "bbb"]) == transcript_hash(["aaa", "bbb"])
    True
    >>> transcript_hash(["aaa", "bbb"]) == transcript_hash(["aaa", "bbb", "ccc"])
    False
    """
    payload = _TRANSCRIPT_JOIN_SEPARATOR.join(turn_hashes).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
