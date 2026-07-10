"""HavenContinuationAdapter — deterministic, dataset-metadata-driven typed
ingestion for the continuation benchmark only.

Implements the recommended architecture in
``docs/architecture/CONTINUATION_BENCHMARK_INGESTION_DESIGN.md`` §10:
:class:`HavenAdapter`'s ``add()`` stores every conversation turn as
verbatim ``MemoryType.FACT`` (see that adapter's own module docstring), so
:class:`~obsidian.memory_engine.project_state.ProjectStateBuilder` -- the
component the continuation benchmark exists to exercise -- never receives a
``GOAL``/``DECISION``/``TASK``/``BLOCKER``/``RULE``-typed candidate and every
``<ProjectState>`` the benchmark observes is a structurally empty shell
(the ingestion design doc's Critical-1, carried over from
``docs/architecture/CONTINUATION_BENCHMARK_AUDIT.md``).

This subclass fixes that for the continuation benchmark specifically,
following the exact pattern :class:`~benchmarks.adapters.haven_full_adapter.HavenFullAdapter`
already establishes: override only the write path, inherit the read path
(``search``, ``build_continuation_context``, ``delete_all``,
``from_config``) unchanged. Unlike ``HavenFullAdapter``, the write path here
is **not** the real ``ManagerPipeline`` -- see the ingestion design's §0/§2
for why a real LLM classifier still could not produce this benchmark's
central mechanism (write-time decision supersession is a deferred no-op in
``CanonicalMatcher``/``KnowledgeUpdater``; see
``obsidian/docs/TECH_DEBT.md``). Instead, :meth:`add_conversation` reads
each dataset turn's already-authored ``turn_type`` (and ``supersedes_turn``/
``resolves_turn``) and constructs each turn's :class:`KnowledgeObject`
directly, with the exact shape a correctly-classified, correctly-superseded
real write path *would* produce -- deterministically, with **no LLM call**.

This adapter exists only for the continuation benchmark. It is intentionally
**not** registered in ``benchmarks.runners.run_benchmarks.get_adapter_cls``
(see that function and ``benchmarks/runners/run_continuation_benchmarks.py``'s
own local resolver) -- it requires ``turn_type``/``supersedes_turn``/
``resolves_turn`` metadata the base suite's dataset schema doesn't carry, and
adding it to the base suite's registry would risk exactly the
adapter-parity-contract disturbance the ingestion design's §4 warns against.
``HavenAdapter`` and ``HavenFullAdapter`` are untouched by this file: neither
imports it, and no line in either class changes.

turn_type -> MemoryType (§6, no inference)
-------------------------------------------
A fixed, total dictionary lookup, applied deterministically:

- ``architecture_discussion`` / ``implementation`` / ``distractor`` /
  ``rejected_approach`` -> ``FACT`` (background content; §6/§9 keep a
  rejected approach as ``FACT`` rather than inventing a new ``MemoryType``
  for it -- it was never adopted, so it was never "a decision" in Haven's
  own model).
- ``decision`` -> ``DECISION``.
- ``constraint`` -> ``RULE`` (``RULE`` resolves to
  ``ContextCategory.CONSTRAINT`` -- see
  :mod:`obsidian.memory_engine.coverage_analyzer`).
- ``blocker`` -> ``BLOCKER``.
- ``task`` -> ``TASK``.
- ``open_question`` -> ``OPEN_QUESTION``. Not enumerated in the ingestion
  design's own §6 illustrative list (which was drawn from
  ``CONTINUATION_BENCHMARK_DESIGN.md`` §4's worked example, a conversation
  that happens not to include an ``open_question`` turn), but the shipped
  pilot dataset (``benchmarks/datasets_continuation/resume_coding/*.json``)
  uses it in every case, and it has the same direct, unambiguous
  ``MemoryType``/``ContextCategory``/``ProjectState`` field correspondence
  every other entry in this table has -- including it is applying the same
  rule, not adding a new one.
- Any other ``turn_type`` (e.g. ``"note"``, used once in the pilot dataset
  for authorial commentary) falls back to ``FACT`` -- the same treatment
  ``architecture_discussion``/``implementation``/``distractor`` already get.

supersedes_turn / resolves_turn (§6, mirrors KnowledgeUpdater.supersede_decision)
-----------------------------------------------------------------------------------
Ingestion tracks every ingested turn's resulting :class:`KnowledgeObject` by
its dataset ``turn_index``. When a turn carries ``supersedes_turn=N``, the
turn at index ``N``'s :class:`KnowledgeObject` is rewritten (same ``id``,
same vault file) with :func:`~obsidian.manager_ai.models.with_decision_metadata`
set to ``DecisionMetadata(status=SUPERSEDED, superseded_by=<this turn's id>)``,
and its ``valid_until`` is set to this turn's ``valid_from`` -- archiving it
exactly the way
:meth:`~obsidian.manager_ai.knowledge_updater.KnowledgeUpdater.supersede_decision`
archives the object it replaces, minus the LLM call that would otherwise
decide *that* a supersession should happen (the dataset author already made
that call when authoring ``supersedes_turn``). The new (superseding) turn's
own ``KnowledgeObject`` records ``DecisionMetadata(status=ACTIVE,
supersedes=<original turn's id>)``, completing the link from both sides,
mirroring ``supersede_decision``'s own two-sided bookkeeping.

Unlike ``supersede_decision`` itself, this method does not require the
superseded turn's ``memory_type`` to be ``DECISION`` -- ``supersedes_turn``
in the pilot dataset always points at a ``rejected_approach`` turn (``FACT``
per the table above), not a ``decision`` turn, so enforcing that precondition
here would make the mechanism a no-op for every shipped case. Attaching
``DecisionMetadata`` to a non-``DECISION`` object has no effect on
:class:`~obsidian.memory_engine.project_state.ProjectStateBuilder`'s
``decisions``/``superseded_decisions`` split (that split only inspects
``MemoryType.DECISION`` candidates -- see ``project_state.py``), so a
``rejected_approach`` turn superseded this way is excluded from retrieval
entirely via its ``valid_until`` archive, the same outcome (invisible as a
"current" anything) as if it had been routed to ``superseded_decisions``.
When ``supersedes_turn`` instead points at a ``decision``-typed turn (a
pattern the schema supports even though the shipped pilot dataset doesn't
exercise it), the same rewrite makes it land in
``ProjectState.superseded_decisions`` exactly as designed -- see
``benchmarks/tests/test_haven_continuation_adapter.py``'s
``TestSupersessionRoutesToSupersededDecisions`` for that case proven
directly against the real ``ProjectStateBuilder``.

A turn carrying ``resolves_turn=N`` (on a blocker-resolving ``decision``
turn) rewrites turn ``N``'s ``KnowledgeObject`` with ``valid_until`` set to
this turn's ``valid_from`` -- no ``DecisionMetadata`` involved, since a
blocker isn't a decision. ``valid_until`` is the same, already-existing
gate :func:`~obsidian.memory_engine.engine._active_candidates` uses for
every archived memory, so a resolved blocker is excluded from acceptance
with no new exclusion mechanism.

Both ``supersedes_turn`` and ``resolves_turn`` are read only from the
conversation turn dict already given to ``add_conversation`` -- never from
``ground_truth``/``expected`` -- so ingestion cannot see the case's own
answer key (see the ingestion design's §7 "discipline" note).

What this file does not touch
-------------------------------
No import of ``ManagerPipeline``, ``Extractor``, ``Classifier``,
``ImportanceScorer``, ``CanonicalMatcher``, or ``KnowledgeUpdater`` -- there
is no LLM client wiring at all. ``search``, ``build_continuation_context``,
``delete_all``, and ``from_config`` are inherited verbatim from
:class:`~benchmarks.adapters.haven_adapter.HavenAdapter`; the standalone
``add()`` method is inherited too (still HavenAdapter's verbatim-``FACT``
storage) since the runner always prefers ``add_conversation`` when present
and this adapter provides no ``turn_type`` for a single ``add()`` message to
read in the first place.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, List, Optional

from benchmarks.adapters.haven_adapter import HavenAdapter
from obsidian.core.enums import MemoryType
from obsidian.manager_ai.models import (
    DecisionMetadata,
    DecisionStatus,
    KnowledgeObject,
    get_decision_metadata,
    with_decision_metadata,
)

#: turn_type -> MemoryType, per this module's docstring. Fixed and
#: deterministic; ``.get(turn_type, MemoryType.FACT)`` at each call site
#: makes any turn_type absent from this table fall back to ``FACT``.
TURN_TYPE_TO_MEMORY_TYPE: Dict[str, MemoryType] = {
    "architecture_discussion": MemoryType.FACT,
    "implementation": MemoryType.FACT,
    "distractor": MemoryType.FACT,
    "rejected_approach": MemoryType.FACT,
    "decision": MemoryType.DECISION,
    "constraint": MemoryType.RULE,
    "blocker": MemoryType.BLOCKER,
    "task": MemoryType.TASK,
    "open_question": MemoryType.OPEN_QUESTION,
}


class HavenContinuationAdapter(HavenAdapter):
    """HavenAdapter with deterministic, ``turn_type``-driven typed ingestion.

    Only :meth:`add_conversation` is overridden -- see the module docstring
    for exactly what it does and why. Every other method (``search``,
    ``build_continuation_context``, ``delete_all``, ``from_config``, and the
    inherited, unmodified ``add``) is :class:`HavenAdapter`'s own, reused
    verbatim.
    """

    def add_conversation(
        self,
        conversation: List[Dict[str, Any]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ingest *conversation* with deterministic, dataset-driven typing.

        Parameters
        ----------
        conversation : list[dict]
            The dataset's own turn shape -- ``speaker``/``text`` plus the
            dataset-author metadata this adapter (uniquely, among Haven
            adapters) reads: ``turn_type``, ``turn_index``,
            ``supersedes_turn``, ``resolves_turn`` (see
            ``docs/architecture/CONTINUATION_BENCHMARK_DESIGN.md`` §4).
        user_id, agent_id : str, optional
            Accepted for interface compatibility with
            :class:`~benchmarks.adapters.base.BaseAdapter`; unused, exactly
            as in :class:`HavenAdapter`.

        Returns
        -------
        dict
            ``{"results": [{"id": str, "memory": str, "event": "ADD"}, ...]}``,
            one entry per non-empty turn ingested -- a superseded/resolved
            turn's in-place rewrite does not add a second entry, since
            nothing new was added, only an earlier object's validity/
            metadata was updated (mirroring ``HavenFullAdapter``'s own
            "not necessarily one per input turn" note for a different
            reason: here every turn still gets exactly one entry, but a
            *second*, earlier turn's entry is never duplicated).
        """
        results: List[Dict[str, str]] = []
        by_turn_index: Dict[int, KnowledgeObject] = {}

        for turn in conversation:
            content = turn.get("text", "")
            if not content:
                continue

            memory_type = TURN_TYPE_TO_MEMORY_TYPE.get(turn.get("turn_type"), MemoryType.FACT)
            supersedes_turn = turn.get("supersedes_turn")
            resolves_turn = turn.get("resolves_turn")

            original_superseded: Optional[KnowledgeObject] = None
            if memory_type is MemoryType.DECISION and supersedes_turn is not None:
                original_superseded = by_turn_index.get(supersedes_turn)

            knowledge = KnowledgeObject(canonical_fact=content, memory_type=memory_type)
            if original_superseded is not None:
                knowledge = with_decision_metadata(
                    knowledge,
                    DecisionMetadata(
                        status=DecisionStatus.ACTIVE,
                        supersedes=original_superseded.id,
                    ),
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

            turn_index = turn.get("turn_index")
            if turn_index is not None:
                by_turn_index[turn_index] = knowledge

            if original_superseded is not None:
                original_metadata = get_decision_metadata(original_superseded) or DecisionMetadata()
                superseded = replace(original_superseded, valid_until=knowledge.valid_from)
                superseded = with_decision_metadata(
                    superseded,
                    replace(
                        original_metadata,
                        status=DecisionStatus.SUPERSEDED,
                        superseded_by=knowledge.id,
                    ),
                )
                self._vault_writer.write(superseded)
                by_turn_index[supersedes_turn] = superseded

            if memory_type is MemoryType.DECISION and resolves_turn is not None:
                original_blocker = by_turn_index.get(resolves_turn)
                if original_blocker is not None:
                    resolved = replace(original_blocker, valid_until=knowledge.valid_from)
                    self._vault_writer.write(resolved)
                    by_turn_index[resolves_turn] = resolved

        return {"results": results}
