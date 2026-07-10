import pathlib
from typing import List


class VaultIndex:
    """Indexes an Obsidian vault by discovering all Markdown (``.md``) files.

    The class is deliberately minimal: it only locates files and stores their
    paths.  No YAML frontmatter is parsed, no embeddings are built, and no
    memory retrieval is performed.  Later stages can extend the index by
    adding metadata without altering the public API defined here.

    Parameters
    ----------
    vault_dir : pathlib.Path
        The root directory of the Obsidian vault to index.
    """

    def __init__(self, vault_dir: pathlib.Path) -> None:
        self._vault_dir: pathlib.Path = vault_dir
        self._files: List[pathlib.Path] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> None:
        """Recursively walk the vault directory and collect every ``.md`` file.

        The discovered paths are stored internally and can be retrieved via
        :meth:`all_files` or :meth:`count`.
        """
        self._files = sorted(
            p for p in self._vault_dir.rglob("*.md") if p.is_file()
        )

    def all_files(self) -> List[pathlib.Path]:
        """Return the list of all indexed Markdown file paths.

        Returns
        -------
        List[pathlib.Path]
            A (possibly empty) list of paths, sorted lexicographically.
        """
        return list(self._files)

    def count(self) -> int:
        """Return the number of indexed Markdown files.

        Returns
        -------
        int
            The total number of files discovered during the last call to
            :meth:`scan`.
        """
        return len(self._files)
