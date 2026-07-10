"""Write Trace Store/Writer -- persistence for the Write Inspector.

Mirrors :mod:`obsidian.checkpoint.store`/:mod:`obsidian.checkpoint.writer`'s
"scan-parse-cache" / "deterministic JSON, one file per id" shapes, combined
into a single module since this subsystem's surface is smaller (no
identity/hashing/diff needed -- a trace's id is a plain ``uuid4()``, not
derived from anything).

One deliberate policy difference from ``CheckpointStore``: a checkpoint
file whose ``schema_version`` doesn't match the current version is skipped
outright (a checkpoint is disposable bookkeeping the pipeline can safely
regenerate). A write trace is a diagnostic log with no regenerate path --
skipping an old trace on a version bump would silently blind the Write
Inspector to that run. So :meth:`WriteTraceStore.load` instead always
attempts to hydrate every file via :meth:`WriteTrace.from_dict` (which
tolerates missing/old keys via ``.get(...)`` defaults) and only skips a
file that is genuinely unreadable/unparseable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from obsidian.ontology.write_trace_models import WriteTrace


class WriteTraceStore:
    """Loads and caches every :class:`WriteTrace` found in a directory.

    Parameters
    ----------
    write_trace_dir : Path
        Root directory containing ``WriteTrace`` JSON files, as written by
        :class:`WriteTraceWriter`.
    """

    def __init__(self, write_trace_dir: Path) -> None:
        self._write_trace_dir = Path(write_trace_dir)
        self._by_id: Dict[UUID, WriteTrace] = {}
        self._skipped: List[Tuple[Path, str]] = []

    def load(self) -> None:
        """Discover, parse, and hydrate every write trace in the directory.

        Safe to call more than once: each call re-scans the directory from
        scratch and only replaces the in-memory cache once every file has
        been processed, so a call that raises (the directory exists but is
        not a directory) leaves the previous cache untouched. A missing
        directory is not an error -- it loads as zero traces. A single
        corrupt/unparseable file is skipped rather than aborting the whole
        load (recorded in :meth:`skipped_files`).

        Raises
        ------
        NotADirectoryError
            If ``write_trace_dir`` exists but is not a directory.
        """
        skipped: List[Tuple[Path, str]] = []

        if not self._write_trace_dir.exists():
            self._by_id = {}
            self._skipped = skipped
            return

        if not self._write_trace_dir.is_dir():
            raise NotADirectoryError(
                f"Write trace path is not a directory: {self._write_trace_dir}"
            )

        by_id: Dict[UUID, WriteTrace] = {}
        for path in sorted(self._write_trace_dir.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                skipped.append((path, f"unreadable/invalid JSON: {exc}"))
                continue

            if not isinstance(raw, dict):
                skipped.append((path, "JSON content is not an object"))
                continue

            try:
                trace = WriteTrace.from_dict(raw)
            except (KeyError, ValueError, TypeError) as exc:
                skipped.append((path, f"failed to hydrate: {exc}"))
                continue

            by_id[trace.trace_id] = trace

        self._by_id = by_id
        self._skipped = skipped

    def get(self, trace_id: UUID) -> Optional[WriteTrace]:
        """Return the cached trace for *trace_id*, or ``None`` if absent."""
        return self._by_id.get(trace_id)

    def all(self) -> List[WriteTrace]:
        """Return every cached trace, sorted oldest-``created_at``-first."""
        return sorted(self._by_id.values(), key=lambda t: t.created_at)

    def count(self) -> int:
        """Return the number of cached traces."""
        return len(self._by_id)

    def skipped_files(self) -> List[Tuple[Path, str]]:
        """Return every file skipped during the last :meth:`load` call."""
        return list(self._skipped)


class WriteTraceWriter:
    """Writes a :class:`WriteTrace` to a deterministic JSON file, then prunes.

    Parameters
    ----------
    write_trace_dir : Path
        The root directory where write-trace files are written.
    max_count : int, optional
        Retention policy: after a successful write, the oldest files
        (by ``created_at``) beyond this count are deleted. Defaults to
        ``500``. ``None`` or any value ``<= 0`` disables pruning entirely
        (unlimited retention).
    """

    def __init__(
        self, write_trace_dir: Path, max_count: Optional[int] = 500
    ) -> None:
        self._write_trace_dir = Path(write_trace_dir)
        self._max_count = max_count

    def write(self, trace: WriteTrace) -> Path:
        """Write *trace* to a deterministic JSON file, then prune old traces.

        Returns
        -------
        Path
            The absolute path of the written file.

        Notes
        -----
        The new trace is written first; only after that succeeds does
        pruning run, and pruning failures are caught and swallowed (never
        raised past this method) -- a trace being pruned late is harmless,
        while a write silently failing because pruning threw would not be.
        Pruning only ever deletes files under *write_trace_dir* that this
        writer itself manages; it never touches ``checkpoint_dir`` or
        ``vault_dir``.
        """
        self._write_trace_dir.mkdir(parents=True, exist_ok=True)

        file_path = self._write_trace_dir / f"{trace.trace_id}.json"
        content = json.dumps(trace.to_dict(), indent=2, sort_keys=True)
        file_path.write_text(content, encoding="utf-8")

        try:
            self._prune()
        except Exception:
            pass

        return file_path

    def _prune(self) -> None:
        if self._max_count is None or self._max_count <= 0:
            return

        store = WriteTraceStore(self._write_trace_dir)
        store.load()
        traces = store.all()  # oldest created_at first

        excess = len(traces) - self._max_count
        if excess <= 0:
            return

        for trace in traces[:excess]:
            path = self._write_trace_dir / f"{trace.trace_id}.json"
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
