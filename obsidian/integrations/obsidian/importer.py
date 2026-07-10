"""Adapter that turns Obsidian vault Markdown notes into the shape Haven's
existing ingestion pipeline already accepts.

This module is deliberately *only* a Markdown -> Conversation adapter. It
does **not** extract memories, classify, score, hash, checkpoint, or write
anything: every note it produces is handed to the exact same
preview -> review -> commit path a ChatGPT conversation goes through (see
``obsidian.server.main``'s ``preview_memory``/``commit_memory``), so there
is no second ingestion pipeline to keep in sync.

Two design points worth stating explicitly:

* **A note is read, never written.** Every function here opens files
  read-only; the original ``.md`` on disk is never modified (requirement:
  the source vault is left byte-for-byte untouched).
* **The note's vault-relative path is its stable identity.** Callers use it
  verbatim as the ``external_key`` that
  :func:`obsidian.checkpoint.identity.derive_conversation_id` turns into a
  deterministic ``conversation_id`` -- which is what lets the existing
  checkpoint duplicate-detection skip an unchanged note with no LLM call.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from obsidian.core.enums import Role

#: Directory names always pruned during a vault walk regardless of where the
#: walk is rooted. ``.obsidian`` is Obsidian's own config (not user notes),
#: and any dot-directory (``.git``, ``.trash``, Haven's own ``.haven``) is
#: bookkeeping the user never means to import as a memory. Haven's *content*
#: directories (``vault``/``concepts``/``notes``) are not named here -- a
#: real user vault could legitimately contain a folder called "notes" -- so
#: the caller prunes those by absolute path via ``exclude_dirs`` instead (see
#: :func:`iter_notes`).
_ALWAYS_SKIP_DIR = ".obsidian"


def iter_notes(
    root: Path, exclude_dirs: Iterable[Path] = ()
) -> Iterator[Tuple[str, Path]]:
    """Yield every importable Markdown note under *root*, depth-first.

    Parameters
    ----------
    root : Path
        The vault folder to walk.
    exclude_dirs : Iterable[Path]
        Absolute directories to prune entirely (subtree and all). Callers
        pass Haven's own vault-scoped directories here (``vault/``,
        ``concepts/``, ``notes/``, ``.haven/``) so generated memory/concept
        notes are never re-ingested when the import source is the active
        space's own root.

    Yields
    ------
    tuple[str, Path]
        ``(relative_path, absolute_path)`` for each ``*.md`` file. The
        relative path is POSIX-normalised (forward slashes) and is stable
        across platforms and runs, so it is safe to use directly as an
        ``external_key``.
    """
    root = Path(root)
    excluded = {Path(d).resolve() for d in exclude_dirs}

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath).resolve()
        # Prune in place so os.walk never descends into skipped subtrees.
        dirnames[:] = [
            d
            for d in dirnames
            if d != _ALWAYS_SKIP_DIR
            and not d.startswith(".")
            and (current / d).resolve() not in excluded
        ]
        for name in sorted(filenames):
            if not name.lower().endswith(".md"):
                continue
            absolute = Path(dirpath) / name
            relative = absolute.relative_to(root).as_posix()
            yield relative, absolute


def _split_frontmatter(text: str) -> Tuple[Optional[str], str]:
    """Split a note into ``(frontmatter_title, body)``.

    Strips a leading YAML frontmatter block (a ``---`` fence at the very
    top, closed by another ``---`` line) from the body, and best-effort
    extracts its ``title:`` value. Malformed or absent frontmatter is not an
    error -- the whole text is returned as the body with ``None`` title, so a
    note that merely *starts* with a horizontal rule is never mistaken for
    truncated frontmatter.
    """
    if not text.startswith("---"):
        return None, text

    lines = text.splitlines()
    # lines[0] is the opening fence; find the closing fence.
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            frontmatter = lines[1:i]
            body = "\n".join(lines[i + 1 :]).lstrip("\n")
            title: Optional[str] = None
            for fm_line in frontmatter:
                stripped = fm_line.strip()
                if stripped.lower().startswith("title:"):
                    value = stripped[len("title:") :].strip().strip("'\"")
                    title = value or None
                    break
            return title, body

    # No closing fence: treat the whole thing as body, not frontmatter.
    return None, text


def _first_heading(body: str) -> Optional[str]:
    """Return the text of the first ``# H1`` heading in *body*, if any."""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return None


def note_title(relative_path: str, text: str) -> str:
    """Resolve a human-readable title for a note.

    Preference order: frontmatter ``title:`` -> first ``# H1`` heading ->
    the filename stem. Always returns something non-empty.
    """
    fm_title, body = _split_frontmatter(text)
    if fm_title:
        return fm_title
    heading = _first_heading(body)
    if heading:
        return heading
    return Path(relative_path).stem


def note_to_turns(relative_path: str, absolute_path: Path) -> List[Tuple[Role, str]]:
    """Read a note and render it as the single-turn conversation Haven ingests.

    The note becomes one ``Role.USER`` turn whose content is the note title
    followed by the note body (YAML frontmatter stripped, but ``[[wikilinks]]``
    and all other Markdown left intact so navigation structure survives into
    extraction). Reads the file read-only; never writes to it.
    """
    text = Path(absolute_path).read_text(encoding="utf-8")
    _fm_title, body = _split_frontmatter(text)
    title = note_title(relative_path, text)
    content = f"{title}\n\n{body}".strip() if body.strip() else title
    return [(Role.USER, content)]


def provenance(relative_path: str, memory_space: Optional[str]) -> Dict[str, str]:
    """Build the provenance metadata stamped onto every memory from a note.

    Returns the four fields the import feature records on each resulting
    ``KnowledgeObject`` (under ``metadata["provenance"]``): ``source`` (always
    ``"obsidian"``), ``source_file`` (the note's vault-relative path),
    ``imported_at`` (UTC ISO-8601, captured now), and ``memory_space`` (the
    active space's name, or empty string when unknown).
    """
    return {
        "source": "obsidian",
        "source_file": relative_path,
        "imported_at": datetime.utcnow().isoformat(),
        "memory_space": memory_space or "",
    }
