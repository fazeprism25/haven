import pathlib
from dataclasses import dataclass, field
from typing import Dict

import yaml


@dataclass
class ParsedMemory:
    """Structured representation of a Haven markdown memory file.

    Attributes
    ----------
    path : pathlib.Path
        Absolute or relative path to the source markdown file.
    metadata : Dict[str, object]
        Dictionary parsed from the YAML frontmatter block.
    body : str
        The remainder of the file after the frontmatter (including any
        leading/trailing blank lines).
    """

    path: pathlib.Path
    metadata: Dict[str, object] = field(default_factory=dict)
    body: str = ""


class MemoryParser:
    """Parses a Haven markdown memory file into a :class:`ParsedMemory`.

    The parser only extracts YAML frontmatter (delimited by ``---``) and
    stores the rest of the file as the body.  No embeddings are built, no
    vault search is performed, and no memory retrieval is attempted.
    """

    def parse(self, path: pathlib.Path) -> ParsedMemory:
        """Read a markdown file and return its parsed representation.

        Parameters
        ----------
        path : pathlib.Path
            Path to the markdown file to parse.

        Returns
        -------
        ParsedMemory
            A dataclass containing the file path, the parsed YAML frontmatter
            (as a plain dictionary), and the remaining markdown body.

        Raises
        ------
        ValueError
            If the YAML frontmatter is malformed and cannot be parsed.
        FileNotFoundError
            If *path* does not exist.
        """
        text = path.read_text(encoding="utf-8")

        metadata: Dict[str, object] = {}
        body = text

        # Detect YAML frontmatter delimited by "---"
        if text.startswith("---"):
            # Find the closing "---"
            end_index = text.find("---", 3)
            if end_index == -1:
                raise ValueError(
                    f"File {path} starts with '---' but has no closing '---' "
                    "to delimit the frontmatter block."
                )
            yaml_block = text[3:end_index]
            body = text[end_index + 3 :]

            if yaml_block.strip():
                try:
                    parsed = yaml.safe_load(yaml_block)
                except yaml.YAMLError as exc:
                    raise ValueError(
                        f"Malformed YAML frontmatter in {path}: {exc}"
                    ) from exc

                if isinstance(parsed, dict):
                    metadata = parsed
                elif parsed is None:
                    metadata = {}
                else:
                    # YAML parsed to a scalar or list – treat as malformed
                    raise ValueError(
                        f"YAML frontmatter in {path} did not produce a mapping; "
                        f"got {type(parsed).__name__} instead."
                    )

        return ParsedMemory(path=path, metadata=metadata, body=body)
