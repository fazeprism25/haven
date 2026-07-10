"""Deterministic hybrid candidate retrieval for the Haven Memory Engine.

This module has exactly one responsibility: orchestrate two independent,
already-implemented retrieval paths and merge their output into a single
deduplicated candidate list.

::

    query
      │
      ├──────────────────────────┐
      ▼                          ▼
    Ontology path            Keyword path
    QueryResolver             KeywordCandidateRetriever
      │
      ▼
    ActivationSpreader
      │
      ▼
    CandidateAssembler
      │                          │
      ▼                          ▼
    Candidate[]              KnowledgeObject[]
      │                          │
      └───────────┬──────────────┘
                   ▼
          HybridCandidateRetriever   (this module)
                   │
                   ▼
          list[Candidate]   (keyword-only hits wrapped as
                              zero-evidence Candidates)

The two paths are genuinely independent: the ontology path only ever
"sees" KnowledgeObjects reachable through a Concept attachment (directly
or via activation spreading across relationships), while the keyword path
searches every KnowledgeObject's ``canonical_fact`` in the vault
regardless of whether it is attached to any Concept at all. Merging them
is what makes retrieval robust to gaps in either the ontology graph or
the query's keyword overlap.

Explicitly out of scope
------------------------
* **No ranking.** Neither path's output is reordered by relevance here;
  every ``Candidate`` keeps whatever ``activation_score``/
  ``attachment_relevance``/``keyword_overlap_score`` its producing path
  already computed, but this module never combines those numbers into a
  final ordering or acts on them beyond attaching them to the right
  ``Candidate`` — that is
  :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s
  responsibility, which this module never even imports. Wrapping a
  keyword-only hit as ``Candidate(supporting_concepts=(),
  attachment_relevance=0.0, activation_score=0.0)`` is not scoring either:
  those two zeros are a fixed, literal encoding of "no ontology evidence
  was found," not a computed relevance judgement. ``keyword_overlap_score``
  is the one number this module *does* carry through unmodified from
  :meth:`~obsidian.memory_engine.keyword_candidate_retriever.KeywordCandidateRetriever.retrieve_with_scores`
  — computing it is that module's responsibility, not this one's; see
  "Merge semantics" below for exactly how it's attached.
* **No slot allocation.** No context budget or truncation.
* **No context building.** No prompt assembly or string formatting.
* **No embeddings, no fuzzy matching.** The ontology path matches via
  exact alias-index lookups (:class:`~obsidian.ontology.query_resolver.QueryResolver`)
  and graph propagation; the keyword path matches via exact token
  equality (:class:`~obsidian.memory_engine.keyword_candidate_retriever.KeywordCandidateRetriever`).
  Nothing here adds similarity scoring on top.
* **No graph or vault mutation.** Only read-only methods are called on
  the :class:`~obsidian.ontology.concept_graph.ConceptGraph` and
  :class:`~obsidian.memory_engine.memory_store.MemoryStore` supplied to
  the constructor.
* **No re-implementation of any orchestrated stage.** This class calls
  :class:`~obsidian.ontology.query_resolver.QueryResolver`,
  :class:`~obsidian.ontology.activation_spreader.ActivationSpreader`,
  :class:`~obsidian.ontology.candidate_assembler.CandidateAssembler`, and
  :class:`~obsidian.memory_engine.keyword_candidate_retriever.KeywordCandidateRetriever`
  exactly as documented by each; none of their internals are duplicated
  or modified here. The only new logic in this module is seed
  construction (turning resolved Concepts into depth-0
  :class:`~obsidian.ontology.retrieval_models.ActivatedConcept` seeds —
  see "Design decisions" below) and the merge/dedup/sort described below.

Merge semantics
---------------
Both paths are run independently against the same ``query`` and their
results merged, keyed by ``KnowledgeObject.id``:

* A ``KnowledgeObject`` found by **both** paths is represented exactly
  once, as the ontology path's
  :class:`~obsidian.ontology.retrieval_models.Candidate` — never as a
  zero-evidence stand-in for the keyword-path hit. The ontology
  ``Candidate`` carries real ``supporting_concepts``/``activation_score``/
  ``attachment_relevance`` evidence the keyword path has no way to
  produce, so it strictly dominates the zero-evidence representation of
  the same fact.
* A ``KnowledgeObject`` found **only** by the ontology path is
  represented as its ``Candidate`` (``has_ontology_evidence`` is ``True``).
* A ``KnowledgeObject`` found **only** by the keyword path is wrapped as
  a zero-*ontology*-evidence ``Candidate`` — ``supporting_concepts=()``,
  ``attachment_relevance=0.0``, ``activation_score=0.0``
  (``has_ontology_evidence`` is ``False``) — so it still reaches
  :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`
  and can be scored on its non-ontology components (importance,
  confidence, recency, confirmation count, and now
  ``keyword_overlap_score``) instead of being dropped.
* **``keyword_overlap_score`` is attached independently of which
  ``Candidate`` wins the id-keyed merge above.** The keyword and ontology
  paths are asked about every id regardless of which one "owns" the final
  representation, so a ``KnowledgeObject`` found by both paths keeps its
  full ontology evidence *and* its keyword-overlap score — neither
  displaces the other, the same independence
  ``weight_activation``/``weight_attachment_relevance`` already have from
  each other in :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`.
  Concretely: the keyword path's :class:`~obsidian.memory_engine.keyword_candidate_retriever.KeywordMatch`
  scores are looked up by id and merged onto whichever ``Candidate``
  (ontology-evidenced or zero-evidence) ends up representing that id,
  via ``dataclasses.replace`` when the winning ``Candidate`` came from the
  ontology path (which has no way to know about keyword evidence when it
  constructs its own ``Candidate`` instances).

Determinism
-----------
* Every candidate/knowledge-object in the merged result is sorted by
  ascending ``str(id)`` of its underlying ``KnowledgeObject`` — the same
  tie-break convention used throughout the memory engine (
  :meth:`~obsidian.memory_engine.memory_store.MemoryStore.all`,
  :class:`~obsidian.ontology.retrieval_models.RankedCandidate`,
  :meth:`~obsidian.ontology.candidate_assembler.CandidateAssembler.assemble`).
  This sort is a deterministic tie-break, not a relevance ranking.
* Both orchestrated retrieval paths are themselves already deterministic
  (documented on their respective modules), and the merge step below
  neither depends on dict/set iteration order nor on which path happened
  to run first.

Design decisions
-----------------
* **Seed construction lives here, not in a lower-level module.**
  :meth:`ActivationSpreader.spread` takes
  :class:`~obsidian.ontology.retrieval_models.ActivatedConcept` seeds, but
  :meth:`QueryResolver.resolve` returns plain
  :class:`~obsidian.ontology.models.Concept` objects. Converting a
  resolved Concept into a seed (``activation_score=1.0``,
  ``activation_depth=0``, ``source_seed=concept.id`` — the exact shape
  :class:`ActivatedConcept`'s own docstring defines for a seed) is a few
  lines of glue that has to live somewhere between the two stages; this
  orchestrator is that "somewhere", since neither
  :mod:`~obsidian.ontology.query_resolver` nor
  :mod:`~obsidian.ontology.activation_spreader` may be modified to add it
  themselves.
* **The merged return type is a single uniform type, ``Candidate``.**
  ``Candidate.supporting_concepts`` is optional (may be ``()``), so a
  keyword-only hit is represented honestly as
  ``Candidate(supporting_concepts=(), attachment_relevance=0.0,
  activation_score=0.0)`` — a real absence of ontology evidence, not an
  invented one; the two float fields carry no fabricated relevance
  judgement, only the fixed "no evidence" value. This lets every
  downstream stage consume a single type instead of branching on
  ``isinstance(item, Candidate)``, while
  :attr:`~obsidian.ontology.retrieval_models.Candidate.has_ontology_evidence`
  still lets a caller that cares distinguish the two cases. Every item in
  the result therefore has a well-defined "underlying KnowledgeObject"
  (``item.knowledge_object``), which is all the merge/sort/dedup logic
  needs.
* **Dependencies the constructor cannot itself construct are passed in;
  everything else is built internally.** ``AliasIndex``,
  ``ConceptGraph``, and ``MemoryStore`` carry the caller's actual data
  (built/loaded ahead of time, matching
  :class:`~obsidian.ontology.candidate_assembler.CandidateAssembler.assemble`'s
  own "already-loaded store" precondition) and so are accepted as
  constructor parameters. ``QueryResolver`` is constructed internally
  from ``alias_index``/``concept_graph`` since it is a thin, stateless
  wrapper over them; ``ActivationSpreader``, ``CandidateAssembler``, and
  ``KeywordCandidateRetriever`` are constructed internally with no
  arguments since all three are documented as stateless and reusable
  across calls to their own methods with different arguments.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple
from uuid import UUID

from obsidian.memory_engine.keyword_candidate_retriever import (
    KeywordCandidateRetriever,
    KeywordMatch,
)
from obsidian.memory_engine.memory_store import MemoryStore
from obsidian.ontology.activation_spreader import ActivationSpreader
from obsidian.ontology.alias_index import AliasIndex
from obsidian.ontology.candidate_assembler import CandidateAssembler
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.query_resolver import QueryResolver
from obsidian.ontology.retrieval_config import RetrievalConfig
from obsidian.ontology.retrieval_models import ActivatedConcept, Candidate


@dataclass(frozen=True)
class CandidateProvenance:
    """Diagnostics-only record of which retrieval path(s) found each candidate.

    Built alongside the same merge loop :meth:`HybridCandidateRetriever.retrieve`
    already runs — it observes that loop's inputs, it does not change which
    candidate wins a merge or in what order the result is returned.

    Parameters
    ----------
    ontology_candidate_count : int
        Number of candidates the ontology path produced for this call,
        before merging with the keyword path.
    keyword_candidate_count : int
        Number of candidates the keyword path produced for this call,
        before merging with the ontology path.
    matched_by_ontology : frozenset[UUID]
        ``KnowledgeObject`` ids the ontology path found.
    matched_by_keyword : frozenset[UUID]
        ``KnowledgeObject`` ids the keyword path found.
    """

    ontology_candidate_count: int
    keyword_candidate_count: int
    matched_by_ontology: FrozenSet[UUID]
    matched_by_keyword: FrozenSet[UUID]


class HybridCandidateRetriever:
    """Combines ontology-based and keyword-based candidate retrieval.

    Parameters
    ----------
    alias_index : AliasIndex
        Pre-built alias index, passed straight through to the internally
        constructed :class:`~obsidian.ontology.query_resolver.QueryResolver`.
    concept_graph : ConceptGraph
        Graph used for concept resolution, activation spreading, and
        attachment evidence collection. Never mutated.
    memory_store : MemoryStore
        Already-:meth:`~obsidian.memory_engine.memory_store.MemoryStore.load`-ed
        store used both to hydrate ontology candidates and as the search
        pool for keyword retrieval. Never mutated.
    config : RetrievalConfig, optional
        Supplies ``max_depth``, ``activation_decay``,
        ``activation_threshold``, and the propagation weights used by
        :class:`~obsidian.ontology.activation_spreader.ActivationSpreader`.
        Defaults to ``RetrievalConfig()`` when omitted. Note that
        ``max_results``/``minimum_candidate_score`` and the scoring
        weights are ranking/allocation concerns this module never applies.

    Examples
    --------
    >>> retriever = HybridCandidateRetriever(alias_index, graph, store)
    >>> results = retriever.retrieve("Haven and Claude")
    >>> [(r.knowledge_object.id, r.has_ontology_evidence) for r in results]
    ... # doctest: +SKIP
    """

    def __init__(
        self,
        alias_index: AliasIndex,
        concept_graph: ConceptGraph,
        memory_store: MemoryStore,
        config: Optional[RetrievalConfig] = None,
    ) -> None:
        self._concept_graph = concept_graph
        self._memory_store = memory_store
        self._config = config if config is not None else RetrievalConfig()

        self._query_resolver = QueryResolver(alias_index, concept_graph)
        self._activation_spreader = ActivationSpreader()
        self._candidate_assembler = CandidateAssembler()
        self._keyword_retriever = KeywordCandidateRetriever()

    def retrieve(self, query: str) -> List[Candidate]:
        """Return the deduplicated union of ontology and keyword candidates for *query*.

        Parameters
        ----------
        query : str
            The raw user query string, passed unchanged to both the
            ontology path (:class:`QueryResolver`) and the keyword path
            (:class:`~obsidian.memory_engine.keyword_candidate_retriever.KeywordCandidateRetriever`).

        Returns
        -------
        list[Candidate]
            One entry per unique ``KnowledgeObject.id`` found by either
            path, sorted by ascending ``str(id)`` of the underlying
            ``KnowledgeObject``. Entries found by the ontology path (with
            or without a matching keyword-path duplicate) carry real
            :class:`~obsidian.ontology.retrieval_models.ActivatedConcept`
            evidence (``has_ontology_evidence`` is ``True``). Entries found
            only by the keyword path are zero-evidence candidates —
            ``supporting_concepts=()``, ``activation_score=0.0``,
            ``attachment_relevance=0.0`` (``has_ontology_evidence`` is
            ``False``). Empty if neither path finds anything.
        """
        return self.retrieve_with_diagnostics(query)[0]

    def retrieve_with_diagnostics(
        self, query: str
    ) -> Tuple[List[Candidate], CandidateProvenance]:
        """Same result as :meth:`retrieve`, plus a :class:`CandidateProvenance` record.

        Runs the exact same resolve/spread/assemble/keyword-match/merge
        sequence as :meth:`retrieve` — indeed :meth:`retrieve` is defined
        as this method's first return value — so the two can never
        disagree about which candidates are returned or in what order.
        The only addition is observing, without altering, which path(s)
        produced each merged candidate.

        Parameters
        ----------
        query : str
            Same contract as :meth:`retrieve`.

        Returns
        -------
        tuple[list[Candidate], CandidateProvenance]
            The identical ``list[Candidate]`` :meth:`retrieve` would
            return, paired with diagnostics describing how many
            candidates each path found and which ``KnowledgeObject`` ids
            each path matched.
        """
        concepts = self._query_resolver.resolve(query)
        seeds = [
            ActivatedConcept(
                concept_id=concept.id,
                activation_score=1.0,
                activation_depth=0,
                source_seed=concept.id,
            )
            for concept in concepts
        ]
        activated_concepts = self._activation_spreader.spread(
            seeds, self._concept_graph, self._config
        )
        ontology_candidates = self._candidate_assembler.assemble(
            activated_concepts, self._concept_graph, self._memory_store
        )

        keyword_matches: List[KeywordMatch] = self._keyword_retriever.retrieve_with_scores(
            query, self._memory_store.all()
        )
        keyword_score_by_id: Dict[UUID, float] = {
            match.knowledge_object.id: match.keyword_overlap_score
            for match in keyword_matches
        }

        merged: Dict[UUID, Candidate] = {}
        for candidate in ontology_candidates:
            merged[candidate.knowledge_object.id] = candidate
        for match in keyword_matches:
            merged.setdefault(
                match.knowledge_object.id,
                Candidate(
                    knowledge_object=match.knowledge_object,
                    supporting_concepts=(),
                    attachment_relevance=0.0,
                    activation_score=0.0,
                    keyword_overlap_score=match.keyword_overlap_score,
                ),
            )
        # The loop above only attaches a keyword score to a *new*
        # zero-evidence Candidate. An id already present from the ontology
        # path (kept, per "Merge semantics" above) still needs its keyword
        # score attached separately, since CandidateAssembler has no way
        # to know about keyword evidence when it builds its own Candidate.
        for ko_id, score in keyword_score_by_id.items():
            existing = merged.get(ko_id)
            if existing is not None and existing.keyword_overlap_score != score:
                merged[ko_id] = dataclasses.replace(existing, keyword_overlap_score=score)

        result = sorted(
            merged.values(), key=lambda candidate: str(candidate.knowledge_object.id)
        )
        provenance = CandidateProvenance(
            ontology_candidate_count=len(ontology_candidates),
            keyword_candidate_count=len(keyword_matches),
            matched_by_ontology=frozenset(
                c.knowledge_object.id for c in ontology_candidates
            ),
            matched_by_keyword=frozenset(keyword_score_by_id),
        )
        return result, provenance
