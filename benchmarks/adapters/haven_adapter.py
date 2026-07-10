"""HavenAdapter — drives Haven's real write/read pipelines behind
:class:`~benchmarks.adapters.base.BaseAdapter`, the same interface
:mod:`benchmarks.runners.run_benchmarks` already drives against a
``mem0.Memory`` instance.

``run_benchmark`` (see ``benchmarks/runners/run_benchmarks.py`` and
``benchmarks/RUNNER_SPEC.md``) does exactly four things to a memory
backend, in this order:

1. ``Memory.from_config(config)`` — construct a fresh, isolated instance.
2. ``mem0.delete_all(filters=...)`` — defensive reset (best-effort; the
   runner swallows any exception).
3. ``mem0.add(messages=[...], user_id=..., agent_id=..., infer=False)``
   — once per conversation entry.
4. ``mem0.search(query=..., filters=...)`` — once, returning
   ``{"results": [{"memory": str, ...}, ...]}``; the runner joins every
   result's ``"memory"`` field to build the final answer.

:class:`HavenAdapter` reproduces exactly that surface. Internally, every
write goes through Haven's real write pipeline
(:class:`~obsidian.memory_engine.vault_writer.VaultWriter` +
:class:`~obsidian.ontology.ontology_pipeline.OntologyPipeline`) and every
read goes through the real
:class:`~obsidian.memory_engine.engine.MemoryEngine` retrieval pipeline.
No ontology or memory-engine module is bypassed, reimplemented, or
modified — this file only wires already-existing collaborators together.

Why ``infer=False`` means "store verbatim"
-------------------------------------------
The runner always calls ``add`` with ``infer=False``, mem0's own
convention for "skip LLM fact extraction; store the message text as a
memory as-is". Haven's LLM-driven extraction/classification/importance
stages (the rest of ``obsidian.manager_ai``, deliberately out of scope
for this adapter) are the equivalent stage in Haven's pipeline, so
honouring ``infer=False`` here means each message's ``content`` becomes
one :class:`~obsidian.manager_ai.models.KnowledgeObject` whose
``canonical_fact`` is the raw text, with default confidence/importance —
no extraction, classification, or supersession logic runs.

Why ``search`` returns one raw-text ``"memory"`` entry per accepted candidate
-------------------------------------------------------------------------------
An earlier version of this adapter returned
:meth:`~obsidian.memory_engine.engine.MemoryEngine.query`'s single
formatted context string — with embedded ``type``/``confidence``/
``importance``/``confirmations``/``valid_from``/``valid_until`` (and, for
decisions, ``status``/``supersedes``/``superseded_by``) annotations — as
the sole ``results[0]["memory"]`` entry. That handed the benchmark's LLM
judge a structurally richer answer than every other adapter: mem0 and the
baselines (:mod:`benchmarks.adapters.baselines`) all return raw,
unannotated memory text, with no downstream generation step in this
benchmark to re-render Haven's context down to the same shape (see
``benchmarks/BENCHMARK_AUDIT.md``, Critical-1).

:meth:`search` now calls
:meth:`~obsidian.memory_engine.engine.MemoryEngine.query_with_trace`
instead — already-existing, unmodified public API — and reads its
:class:`~obsidian.ontology.retrieval_models.RetrievalTrace` purely to learn
*which* candidates were accepted and their raw ``canonical_fact`` text.
``RetrievalTrace.candidates`` is produced in
:class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s
final-score order — the same order
:class:`~obsidian.memory_engine.deterministic_slot_allocator.DeterministicSlotAllocator`
re-derives for rendering — so filtering it down to ``accepted`` entries
reproduces exactly the set and order
:class:`~obsidian.memory_engine.context_builder.ContextBuilder` would have
rendered, minus the metadata annotations. No ranking, retrieval, or
formatting stage is modified by this: the trace is a read-only diagnostics
artifact those stages already compute, and only its plain
``canonical_fact`` strings ever reach the runner — never the trace object
itself, which its own docstring says "must NEVER be passed to, or
serialised into, an LLM prompt". This also means Haven now reports one
``results`` entry per accepted candidate instead of always 0 or 1, which
was previously distorting ``retrieved_memories``-count-based diagnostics
(e.g. ``benchmarks/analysis/classify_failure.py``'s ``NO_RETRIEVAL``
heuristic) that assume that count reflects candidates considered.

On-disk state and why it exists
---------------------------------
:class:`~obsidian.memory_engine.memory_store.MemoryStore` only knows how
to hydrate ``KnowledgeObject`` instances by loading Markdown files off
disk, and :class:`~obsidian.ontology.alias_index.AliasIndex` needs the
full set of :class:`~obsidian.ontology.models.Concept` objects to build
its lookup table, which :class:`~obsidian.ontology.concept_graph.ConceptGraph`
has no method to enumerate. Both are read back from the same
vault/concept directories :class:`~obsidian.memory_engine.vault_writer.VaultWriter`
and :class:`~obsidian.ontology.concept_writer.ConceptWriter` (via
``OntologyPipeline``) already write to, using
:class:`~obsidian.memory_engine.memory_store.MemoryStore` and
:class:`~obsidian.ontology.concept_parser.ConceptParser` exactly as
designed. The live :class:`ConceptGraph` itself is *not* reloaded from
disk between calls — ``OntologyPipeline`` mutates the same in-process
graph instance directly, so it is always already current.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks.adapters.base import BaseAdapter
from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.memory_engine.engine import MemoryEngine
from obsidian.memory_engine.memory_store import MemoryStore
from obsidian.memory_engine.query_rewriter import QueryRewriter
from obsidian.memory_engine.vault_writer import VaultWriter
from obsidian.ontology.alias_index import AliasIndex
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.concept_parser import ConceptParser
from obsidian.ontology.models import Concept
from obsidian.ontology.ontology_pipeline import OntologyPipeline
from obsidian.ontology.retrieval_config import RetrievalConfig


class HavenAdapter(BaseAdapter):
    """Benchmark-facing wrapper exposing Haven through a mem0-shaped API.

    Parameters
    ----------
    vault_dir : Path, optional
        Directory for ``KnowledgeObject`` Markdown files. Defaults to a
        fresh temporary directory.
    concept_dir : Path, optional
        Directory for Concept Markdown files. Defaults to a fresh
        temporary directory.
    config : RetrievalConfig, optional
        Retrieval tuning passed straight through to ``MemoryEngine``.
        Defaults to ``RetrievalConfig()``.
    query_rewriter : QueryRewriter, optional
        Passed straight through to ``MemoryEngine``'s own
        ``query_rewriter`` parameter to enable multi-query expansion
        (see :class:`~obsidian.memory_engine.engine.MemoryEngine`).
        Defaults to ``None``, which disables it — :meth:`search` then
        constructs ``MemoryEngine`` exactly as it did before this
        parameter existed.
    """

    def __init__(
        self,
        vault_dir: Optional[Path] = None,
        concept_dir: Optional[Path] = None,
        config: Optional[RetrievalConfig] = None,
        query_rewriter: Optional[QueryRewriter] = None,
    ) -> None:
        root = Path(tempfile.mkdtemp(prefix="haven_benchmark_"))
        self._vault_dir = Path(vault_dir) if vault_dir is not None else root / "vault"
        self._concept_dir = (
            Path(concept_dir) if concept_dir is not None else root / "concepts"
        )
        self._vault_dir.mkdir(parents=True, exist_ok=True)
        self._concept_dir.mkdir(parents=True, exist_ok=True)

        self._config = config if config is not None else RetrievalConfig()

        self._concept_parser = ConceptParser()
        self._vault_writer = VaultWriter(self._vault_dir)
        self._graph = ConceptGraph()
        self._ontology_pipeline = OntologyPipeline(self._graph, self._concept_dir)
        self._memory_store = MemoryStore(self._vault_dir)
        self._alias_index = AliasIndex()
        self._query_rewriter = query_rewriter

    # ------------------------------------------------------------------
    # mem0-compatible construction
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None) -> "HavenAdapter":
        """Construct a fresh, isolated adapter, mirroring ``Memory.from_config``.

        *config* is accepted only for interface compatibility with the
        runner, which builds an embedder/vector_store/llm dict meant for
        mem0's real ``Memory``. Haven's deterministic pipeline needs none
        of those settings, so *config* is otherwise unused: every call
        gets its own fresh vault/concept directory, which is exactly the
        "fresh Mem0 instance (no prior data)" isolation
        ``benchmarks/RUNNER_SPEC.md`` requires between benchmarks.
        """
        return cls()

    # ------------------------------------------------------------------
    # mem0-compatible write path
    # ------------------------------------------------------------------

    def delete_all(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Wipe all state for this adapter instance.

        Mirrors ``mem0.Memory.delete_all`` closely enough for the
        runner's pre-benchmark reset call: removes every persisted
        Markdown file and resets the in-memory graph, alias index, and
        memory store to a blank state. *filters* is accepted for
        signature compatibility but ignored — this adapter is always
        single-tenant per instance.
        """
        for directory in (self._vault_dir, self._concept_dir):
            for path in directory.glob("*.md"):
                path.unlink()

        self._graph = ConceptGraph()
        self._ontology_pipeline = OntologyPipeline(self._graph, self._concept_dir)
        self._memory_store = MemoryStore(self._vault_dir)
        self._alias_index = AliasIndex()
        return {"message": "Memories deleted successfully!"}

    def add(
        self,
        messages: List[Dict[str, str]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        infer: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Write *messages* into Haven, mirroring ``mem0.Memory.add``.

        Parameters
        ----------
        messages : list[dict]
            mem0-shaped messages (``{"role": ..., "content": ...}``); only
            ``content`` is read.
        user_id, agent_id : str, optional
            Accepted for signature compatibility; Haven has no per-tenant
            partitioning in this adapter's scope, so both are ignored.
        infer : bool
            Must be ``False`` for the verbatim-storage contract described
            in the module docstring; accepted (not enforced) so the
            runner's call site never needs to change.

        Returns
        -------
        dict
            ``{"results": [{"id": str, "memory": str, "event": "ADD"}, ...]}``,
            one entry per non-empty message, mirroring mem0's own
            ``add`` response shape.
        """
        results: List[Dict[str, str]] = []
        for message in messages:
            content = message.get("content", "")
            if not content:
                continue

            knowledge = KnowledgeObject(
                canonical_fact=content,
                memory_type=MemoryType.FACT,
            )
            self._vault_writer.write(knowledge)
            self._ontology_pipeline.process(knowledge)

            results.append(
                {
                    "id": str(knowledge.id),
                    "memory": knowledge.canonical_fact,
                    "event": "ADD",
                }
            )

        return {"results": results}

    # ------------------------------------------------------------------
    # mem0-compatible read path
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Answer *query* using ``MemoryEngine``, mirroring ``mem0.Memory.search``.

        Rebuilds the ``AliasIndex`` from the concept files on disk and
        reloads the ``MemoryStore`` from the vault directory — the two
        collaborators whose authoritative state lives on disk — then
        runs the unmodified ``MemoryEngine`` retrieval pipeline. *filters*
        is accepted for signature compatibility but ignored (see
        :meth:`delete_all`).

        Returns
        -------
        dict
            ``{"results": [{"id": str, "memory": canonical_fact}, ...]}``,
            one entry per candidate ``MemoryEngine`` accepted into its
            context, each carrying only that candidate's raw
            ``canonical_fact`` text (see "Why ``search`` returns one
            raw-text ``"memory"`` entry per accepted candidate" above) —
            the exact shape ``run_benchmarks.py`` reads via
            ``mem["memory"] for mem in result.get("results", [])``.
            ``{"results": []}`` when nothing was accepted.
        """
        self._alias_index.rebuild(self._load_concepts())
        self._memory_store.load()

        engine = MemoryEngine(
            self._alias_index,
            self._graph,
            self._memory_store,
            self._config,
            query_rewriter=self._query_rewriter,
        )
        _context, trace = engine.query_with_trace(query)

        results = [
            {
                "id": str(candidate.knowledge_object_id),
                "memory": candidate.canonical_fact,
            }
            for candidate in trace.candidates
            if candidate.accepted
        ]
        return {"results": results}

    # ------------------------------------------------------------------
    # Continuation context (see BaseAdapter.build_continuation_context)
    # ------------------------------------------------------------------

    def build_continuation_context(self, query: str) -> str:
        """Return Haven's real structured prompt for *query*, mirroring
        :meth:`search`'s disk-reload pattern.

        Calls :meth:`~obsidian.memory_engine.engine.MemoryEngine.query_structured`
        -- already-existing, unmodified public API -- and returns its
        rendered XML verbatim, including ``<Guidance>``, ``<ProjectState>``
        (when the query classifies as ``TaskMode.CONTINUATION``), and
        ``<WorkingContext>`` exactly as a real downstream conversation would
        receive it. No ranking, retrieval, or rendering stage is modified by
        this method; it only constructs the same ``MemoryEngine`` instance
        :meth:`search` does and calls a different one of its public methods.
        """
        self._alias_index.rebuild(self._load_concepts())
        self._memory_store.load()

        engine = MemoryEngine(
            self._alias_index,
            self._graph,
            self._memory_store,
            self._config,
            query_rewriter=self._query_rewriter,
        )
        return engine.query_structured(query)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_concepts(self) -> List[Concept]:
        """Return every Concept currently persisted in the concept directory."""
        return [
            self._concept_parser.read(path).concept
            for path in sorted(self._concept_dir.glob("*.md"))
        ]
