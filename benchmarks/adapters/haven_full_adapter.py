"""HavenFullAdapter — benchmarks Haven exactly as real users use it.

:class:`~benchmarks.adapters.haven_adapter.HavenAdapter` ("Haven Retrieval")
benchmarks Haven's *read* path (real ``MemoryEngine`` retrieval) against
hand-built ``KnowledgeObject`` instances -- one per message, with no
extraction, classification, importance scoring, or matching. That isolates
retrieval quality, but it never exercises the write path a real "Remember"
click actually drives.

:class:`HavenFullAdapter` closes that gap. Its write path is the real,
unmodified production pipeline:

    Conversation -> Extractor -> Classifier -> ImportanceScorer ->
    CanonicalMatcher -> KnowledgeUpdater -> VaultWriter -> OntologyPipeline

exactly as :func:`obsidian.manager_ai.pipeline.ManagerPipeline.process` and
``obsidian/server/main.py``'s ``save_memory`` drive it. Nothing here
reimplements a stage; this file only wires already-existing collaborators
together, the same way :class:`HavenAdapter` does for the read path.

Why this subclasses HavenAdapter
---------------------------------
The read path -- :meth:`~HavenAdapter.search` -- MUST behave identically to
Haven Retrieval, so it is inherited unchanged rather than re-implemented: a
copy would be one refactor away from silently drifting. ``delete_all`` and
the vault/concept directory setup are likewise inherited verbatim.

Why the write path reloads ``existing`` from disk on every call
------------------------------------------------------------------
Production reloads ``memory_store``/``alias_index`` from disk at the top of
every real HTTP request (see ``obsidian/server/main.py``'s own module
docstring). :meth:`add_conversation` mirrors that exactly -- it reloads
``MemoryStore`` immediately before calling ``ManagerPipeline.process`` --
rather than keeping a persistent in-process list, so CONFIRM/UPDATE
matching in this benchmark sees exactly what a real second request would
see, including the known evidence-chain persistence gap documented in
``obsidian/memory_engine/memory_store.py``.

Why add_conversation, not repeated add() calls
------------------------------------------------
``ManagerPipeline.process`` expects a single ``Conversation`` (see
``obsidian/manager_ai/pipeline.py``) so the Extractor can see every turn at
once, exactly like a real multi-turn "Remember" click. Calling it once per
message -- as ``HavenAdapter.add`` and the runner's original per-entry loop
do -- would show the Extractor one isolated turn at a time, never the real
shape of its input. :meth:`add_conversation` (see
``benchmarks/adapters/base.py``) is the adapter-interface extension that
lets the runner hand over the whole conversation as one unit; :meth:`add`
is kept working (for direct/standalone use and interface compliance) by
delegating to it with a single-entry list.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from benchmarks.adapters.haven_adapter import HavenAdapter
from obsidian.core.enums import Role
from obsidian.core.types import Conversation, Event
from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.llm import ManagerAILLM
from obsidian.manager_ai.pipeline import ManagerPipeline
from obsidian.memory_engine.query_rewriter import QueryRewriter
from obsidian.ontology.retrieval_config import RetrievalConfig


class HavenFullAdapter(HavenAdapter):
    """Benchmark-facing wrapper driving Haven's real write *and* read pipelines.

    Parameters
    ----------
    vault_dir, concept_dir, config, query_rewriter
        Identical meaning to :class:`HavenAdapter`'s constructor -- passed
        straight through so vault/concept setup and the read path are
        exactly what Haven Retrieval uses.
    llm : optional
        The ``LLMInterface`` (``generate(prompt) -> str``) shared by the
        ``Extractor``/``Classifier``/``ImportanceScorer`` stages. Defaults
        to :class:`~obsidian.manager_ai.llm.ManagerAILLM`, the same real
        LLM client ``obsidian/server/main.py`` constructs for production.
        Tests inject a scripted fake here instead of hitting a real model.
    """

    def __init__(
        self,
        vault_dir: Optional[Any] = None,
        concept_dir: Optional[Any] = None,
        config: Optional[RetrievalConfig] = None,
        query_rewriter: Optional[QueryRewriter] = None,
        llm: Optional[Any] = None,
    ) -> None:
        super().__init__(
            vault_dir=vault_dir,
            concept_dir=concept_dir,
            config=config,
            query_rewriter=query_rewriter,
        )
        self._llm = llm if llm is not None else ManagerAILLM()
        self._pipeline = ManagerPipeline(
            extractor=Extractor(llm=self._llm),
            classifier=Classifier(llm=self._llm),
            importance_scorer=ImportanceScorer(llm=self._llm),
            canonical_matcher=CanonicalMatcher(),
            knowledge_updater=KnowledgeUpdater(),
        )

    # ------------------------------------------------------------------
    # mem0-compatible write path -- real ManagerPipeline, not verbatim storage
    # ------------------------------------------------------------------

    def add(
        self,
        messages: List[Dict[str, str]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        infer: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Ingest *messages* as a one-turn conversation through the real pipeline.

        Mirrors :class:`HavenAdapter`'s signature for interface
        compliance and standalone use; the runner itself prefers
        :meth:`add_conversation` (see ``benchmarks/adapters/base.py``) so
        a whole multi-turn conversation reaches the Extractor as one unit,
        the way ``ManagerPipeline`` expects.
        """
        turns = [
            {"speaker": "user", "text": message.get("content", "")}
            for message in messages
        ]
        return self.add_conversation(turns, user_id=user_id, agent_id=agent_id)

    def add_conversation(
        self,
        conversation: List[Dict[str, str]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the real Manager AI pipeline over the whole *conversation* at once.

        Builds a single :class:`~obsidian.core.types.Conversation` (one
        ``Event`` per non-empty entry) and runs
        ``Extractor -> Classifier -> ImportanceScorer -> CanonicalMatcher ->
        KnowledgeUpdater`` on it exactly once via
        ``ManagerPipeline.process`` -- never per-message, since the
        Extractor's whole point is seeing every turn together. Every
        resulting ``KnowledgeObject`` (``NEW``/``CONFIRM``/``UPDATE`` --
        ``SUPERSEDE`` stays the deliberate no-op it is everywhere else in
        Haven; see ``obsidian/docs/TECH_DEBT.md``) is persisted through
        the same ``VaultWriter``/``OntologyPipeline`` calls
        :class:`HavenAdapter` uses, so the on-disk shape the read path
        later loads is identical in kind.

        Returns
        -------
        dict
            ``{"results": [{"id": str, "memory": str, "event": str}, ...]}``,
            one entry per fact that actually produced a persisted
            ``KnowledgeObject`` -- not necessarily one per input turn,
            since a duplicate/refining turn CONFIRMs or UPDATEs an
            existing object rather than adding a new one.
        """
        events = [
            Event(role=Role.USER, content=turn.get("text", ""))
            for turn in conversation
            if turn.get("text")
        ]
        if not events:
            return {"results": []}

        convo = Conversation(events=events)

        self._memory_store.load()
        existing = list(self._memory_store.all())

        decisions = self._pipeline.process(convo, existing_knowledge=existing)

        results: List[Dict[str, str]] = []
        for decision in decisions:
            knowledge = decision.knowledge
            if knowledge is None:
                # SUPERSEDE (or any future decision KnowledgeUpdater doesn't
                # apply): nothing to persist, exactly as everywhere else.
                continue

            self._vault_writer.write(knowledge)
            self._ontology_pipeline.process(knowledge)

            results.append(
                {
                    "id": str(knowledge.id),
                    "memory": knowledge.canonical_fact,
                    "event": decision.decision.value.upper()
                    if decision.decision is not None
                    else "ADD",
                }
            )

        return {"results": results}
