"""Deterministic UUID generation for the Haven Checkpoint subsystem.

Mirrors :mod:`obsidian.ontology.identity`'s existing convention exactly:
every identifier is derived deterministically from its content using UUID
version 5 (SHA-1 name-based), so that:

* The same ``(source, external_key)`` pair -- e.g. the same ChatGPT
  conversation URL -- always produces the same conversation ID, without
  needing a lookup table to check first.
* The same turn, in the same conversation, always produces the same
  event ID across separate processing runs, so evidence/provenance tied
  to that turn stays stable even when the conversation is reprocessed
  later.

Determinism is what lets the server ask "have I seen this conversation
before?" with zero disk I/O: it just recomputes the ID and checks whether
a checkpoint file with that name exists.
"""

from __future__ import annotations

import uuid

from obsidian.core.enums import SourceType

# ---------------------------------------------------------------------------
# Stable namespace UUID for the Haven Checkpoint subsystem
# ---------------------------------------------------------------------------

CHECKPOINT_NAMESPACE: uuid.UUID = uuid.uuid5(
    uuid.NAMESPACE_DNS,
    "haven.obsidian.checkpoint",
)
"""Stable UUID5 namespace for all Haven Checkpoint identifiers.

Derived from :data:`uuid.NAMESPACE_DNS` and the string
``"haven.obsidian.checkpoint"``. Deliberately distinct from
:data:`obsidian.ontology.identity.ONTOLOGY_NAMESPACE` so that a
conversation ID and a concept ID can never collide even if their source
strings happened to match. This value is fixed for the lifetime of the
project; changing it would invalidate every stored checkpoint identifier.
"""


# ---------------------------------------------------------------------------
# Public factories
# ---------------------------------------------------------------------------


def derive_conversation_id(source: SourceType, external_key: str) -> uuid.UUID:
    """Return a deterministic UUID5 for a conversation.

    Parameters
    ----------
    source : SourceType
        The origin system the conversation came from (e.g.
        ``SourceType.CHATGPT``).
    external_key : str
        A stable identifier the source system itself provides for this
        conversation (e.g. ChatGPT's ``location.pathname``, a Slack
        thread's channel+timestamp, an email thread id). Surrounding
        whitespace is stripped before hashing -- incidental whitespace
        from string concatenation should not mint a new identity -- but
        the key is *not* case-folded, unlike
        :func:`obsidian.ontology.identity.concept_id`'s label handling:
        external keys are opaque identifiers from other systems (e.g.
        URL paths), where case may be semantically meaningful, not
        human-entered natural-language labels.

    Returns
    -------
    uuid.UUID
        A deterministic, globally unique identifier for this
        conversation. The same ``(source, external_key)`` pair always
        returns the same UUID.

    Examples
    --------
    >>> a = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
    >>> b = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
    >>> a == b
    True
    >>> c = derive_conversation_id(SourceType.CLAUDE, "/c/abc123")
    >>> a == c
    False
    """
    key = f"{source.value}:{external_key.strip()}"
    return uuid.uuid5(CHECKPOINT_NAMESPACE, key)


def derive_event_id(
    conversation_id: uuid.UUID, turn_index: int, turn_hash: str
) -> uuid.UUID:
    """Return a deterministic UUID5 for one turn within a conversation.

    Parameters
    ----------
    conversation_id : uuid.UUID
        The identifier of the conversation this turn belongs to, as
        returned by :func:`derive_conversation_id`.
    turn_index : int
        The turn's zero-based position within the conversation.
    turn_hash : str
        The content hash of this turn, as returned by
        :func:`obsidian.checkpoint.hashing.turn_hash`. Folding the
        content hash into the key (rather than deriving the ID from
        ``conversation_id``/``turn_index`` alone) means that if a turn at
        the same position is later edited, it deterministically gets a
        *different* event ID -- so evidence tied to the old content is
        never silently conflated with evidence for the edited content.

    Returns
    -------
    uuid.UUID
        A deterministic, globally unique identifier for this turn. The
        same ``(conversation_id, turn_index, turn_hash)`` triple always
        returns the same UUID.

    Examples
    --------
    >>> cid = derive_conversation_id(SourceType.CHATGPT, "/c/abc123")
    >>> derive_event_id(cid, 0, "deadbeef") == derive_event_id(cid, 0, "deadbeef")
    True
    >>> derive_event_id(cid, 0, "deadbeef") == derive_event_id(cid, 1, "deadbeef")
    False
    """
    key = f"{conversation_id}:{turn_index}:{turn_hash}"
    return uuid.uuid5(CHECKPOINT_NAMESPACE, key)
