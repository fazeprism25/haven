"""Tests for the Obsidian Markdown -> Conversation adapter.

The adapter is pure and read-only: it walks a vault, turns each note into a
single ``Role.USER`` turn, and builds provenance metadata. It must never
modify a source file, must skip Haven's own directories and Obsidian config,
and must preserve wikilinks so navigation structure survives into extraction.
"""

from __future__ import annotations

from pathlib import Path

from obsidian.core.enums import Role
from obsidian.integrations.obsidian import importer


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_iter_notes_walks_markdown_and_skips_obsidian_and_excluded(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "# A")
    _write(tmp_path / "sub" / "b.md", "# B")
    _write(tmp_path / "notes.txt", "not markdown")
    _write(tmp_path / ".obsidian" / "workspace.md", "obsidian config")
    _write(tmp_path / "vault" / "generated.md", "haven output")

    results = dict(importer.iter_notes(tmp_path, exclude_dirs=[tmp_path / "vault"]))

    assert set(results.keys()) == {"a.md", "sub/b.md"}
    # Relative keys are POSIX-normalised regardless of platform.
    assert "sub/b.md" in results


def test_note_to_turns_strips_frontmatter_prepends_title_keeps_wikilinks(
    tmp_path: Path,
) -> None:
    note = tmp_path / "project.md"
    _write(
        note,
        "---\ntitle: My Project\ntags: [x]\n---\n\n"
        "Working with [[Alice]] on [[Postgres]] migration.\n",
    )

    turns = importer.note_to_turns("project.md", note)

    assert len(turns) == 1
    role, content = turns[0]
    assert role == Role.USER
    # Title from frontmatter is prepended; frontmatter block itself is gone.
    assert content.startswith("My Project")
    assert "title:" not in content
    assert "tags:" not in content
    # Wikilinks are preserved verbatim.
    assert "[[Alice]]" in content
    assert "[[Postgres]]" in content


def test_note_title_falls_back_to_heading_then_stem(tmp_path: Path) -> None:
    with_heading = tmp_path / "h.md"
    _write(with_heading, "# Real Heading\n\nbody")
    assert importer.note_title("h.md", with_heading.read_text(encoding="utf-8")) == "Real Heading"

    plain = tmp_path / "plain-note.md"
    _write(plain, "just body text, no heading")
    assert (
        importer.note_title("plain-note.md", plain.read_text(encoding="utf-8"))
        == "plain-note"
    )


def test_note_to_turns_never_modifies_the_file(tmp_path: Path) -> None:
    note = tmp_path / "immutable.md"
    original = "---\ntitle: Keep Me\n---\n\nOriginal body with [[Link]].\n"
    _write(note, original)
    before = note.read_bytes()

    importer.note_to_turns("immutable.md", note)

    assert note.read_bytes() == before


def test_provenance_has_all_four_fields(tmp_path: Path) -> None:
    prov = importer.provenance("sub/note.md", "My Space")
    assert prov["source"] == "obsidian"
    assert prov["source_file"] == "sub/note.md"
    assert prov["memory_space"] == "My Space"
    # imported_at is an ISO-8601 timestamp string.
    assert "T" in prov["imported_at"]

    # A missing space name degrades to empty string, never a KeyError.
    assert importer.provenance("a.md", None)["memory_space"] == ""
