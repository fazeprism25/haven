"""BaseAdapter — the interface every benchmark-facing memory system implements.

``benchmarks/runners/run_benchmarks.py`` (see ``benchmarks/RUNNER_SPEC.md``)
drives whatever memory system it's given through exactly four calls, in
this order:

1. ``AdapterCls.from_config(config)`` — construct a fresh, isolated instance.
2. ``adapter.delete_all(filters=...)`` — defensive reset before a benchmark.
3. ``adapter.add(messages=[...], user_id=..., agent_id=..., infer=False)``
   — once per conversation entry.
4. ``adapter.search(query=..., filters=...)`` — once, returning
   ``{"results": [{"memory": str, ...}, ...]}``.

:class:`BaseAdapter` formalizes that surface so the runner can execute any
conforming memory system interchangeably. New backends (Mem0, GraphRAG,
Memobase, Zep, ...) become "trivial to add" by subclassing this and
implementing the four methods below — no runner, dataset, or scoring code
needs to change.

A fifth, concrete (non-abstract) method, :meth:`BaseAdapter.add_conversation`,
lets an adapter ingest an entire conversation as one unit instead of one
``add()`` call per entry. The runner calls it when present (falling back to
the original per-entry loop for backends, like raw ``mem0.Memory``, that
don't have it) — see ``run_benchmark`` in
``benchmarks/runners/run_benchmarks.py``. Its default implementation
reproduces the original per-entry loop exactly, so this is purely additive:
no existing adapter's behavior changes unless it explicitly overrides it.

This module defines the interface only. It contains no retrieval, ranking,
or scoring logic of its own.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseAdapter(ABC):
    """Abstract base class for benchmark-facing memory system adapters."""

    @classmethod
    @abstractmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None) -> "BaseAdapter":
        """Construct a fresh, isolated adapter instance.

        *config* mirrors mem0's ``Memory.from_config`` shape (an
        ``embedder``/``vector_store``/``llm`` dict) for signature
        compatibility with the runner. Implementations that don't need
        those settings may ignore *config* entirely, but every call must
        return an instance with no data carried over from any prior
        instance — this is the isolation the runner relies on between
        benchmarks.
        """
        raise NotImplementedError

    @abstractmethod
    def delete_all(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Wipe all state for this adapter instance.

        Called by the runner as a defensive reset before each benchmark;
        the runner swallows any exception this raises. *filters* mirrors
        mem0's filter dict for signature compatibility and may be ignored
        by single-tenant implementations.
        """
        raise NotImplementedError

    @abstractmethod
    def add(
        self,
        messages: List[Dict[str, str]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        infer: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Write *messages* (mem0-shaped ``{"role": ..., "content": ...}``) into the system.

        Called once per conversation entry with ``infer=False`` — mem0's
        convention for "skip LLM fact extraction; store the message text
        verbatim". Must return ``{"results": [{"memory": str, ...}, ...]}``,
        mirroring mem0's own ``add`` response shape.
        """
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        query: str,
        filters: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Answer *query* and return retrieved memories.

        Must return ``{"results": [{"memory": str, ...}, ...]}``; the
        runner builds its final answer via
        ``" ".join(mem["memory"] for mem in result.get("results", []))``.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Conversation-level ingestion (optional override)
    # ------------------------------------------------------------------

    def add_conversation(
        self,
        conversation: List[Dict[str, str]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ingest a whole conversation, one ``add()`` call per entry by default.

        *conversation* is the dataset's own shape -- a list of
        ``{"speaker": ..., "text": ...}`` dicts (see
        ``benchmarks/RUNNER_SPEC.md``). This default implementation
        reproduces exactly what ``run_benchmark`` used to do inline: one
        ``add()`` call per entry, ``infer=False``. Every adapter that
        doesn't override this method keeps producing byte-for-byte
        identical results to before this method existed.

        Some adapters' write path genuinely needs the whole conversation
        as one unit -- e.g. Haven Full's ``ManagerPipeline``, which
        expects a single ``Conversation``, not isolated messages replayed
        one at a time. Those adapters override this method instead of
        forcing that unit back together from repeated single-message
        ``add()`` calls.
        """
        results: List[Dict[str, Any]] = []
        for turn in conversation:
            response = self.add(
                messages=[{"role": "user", "content": turn.get("text", "")}],
                user_id=user_id,
                agent_id=agent_id,
                infer=False,
            )
            results.extend(response.get("results", []))
        return {"results": results}

    # ------------------------------------------------------------------
    # Continuation context (optional override)
    # ------------------------------------------------------------------

    def build_continuation_context(self, query: str) -> str:
        """Return the context a downstream model would use to resume work.

        See ``docs/architecture/CONTINUATION_BENCHMARK_DESIGN.md`` §3: this
        is a second, additive read path alongside :meth:`search` —
        ``run_benchmarks.py`` never calls it, so no existing category's
        results change. The continuation benchmark
        (``benchmarks/runners/run_continuation_benchmarks.py``) calls it
        instead of :meth:`search` because ``search``'s flat
        ``{"results": [...]}`` shape has already discarded any
        structure/tiering/orientation a backend might apply -- exactly the
        thing that benchmark measures.

        This default implementation falls back to :meth:`search` plus the
        same flat join the existing runner already does
        (``" ".join(mem["memory"] for mem in results)``) -- the "flat
        retrieval" condition every adapter that doesn't override this
        method is scored under, deliberately: a legitimate baseline, not a
        bug. :class:`~benchmarks.adapters.haven_adapter.HavenAdapter`
        overrides it to call ``MemoryEngine.query_structured()`` instead.
        """
        results = self.search(query)
        return " ".join(mem["memory"] for mem in results.get("results", []))
