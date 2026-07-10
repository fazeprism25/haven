"""Query resolver for Haven Ontology.

Resolves a raw user query into an ordered list of Concept objects by
looking up the full normalised query and each individual token against
an AliasIndex, then fetching the matched Concepts from a ConceptGraph.

Resolution passes
-----------------
1. Whole-phrase pass – the entire normalised query is looked up as one
   key.  Catches concepts whose label or alias is an exact
   (case-/whitespace-insensitive) match for the complete query string
   (e.g. query ``"Memory Engine"`` → concept *Memory Engine*).

2. Token pass – the query is tokenised with stop words removed and each
   token is looked up individually.  Catches single-word concept labels
   and aliases appearing inside a longer query.

Concepts are collected in first-found order and deduplicated by UUID.
Concepts present in the AliasIndex but absent from the ConceptGraph are
silently skipped.

Design constraints
------------------
* No LLM calls, embeddings, fuzzy matching, graph traversal, activation
  spreading, keyword fallback, ranking, or Markdown I/O.
* Deterministic – the same (query, index, graph) triple always produces
  the same ordered list.
* Text normalisation delegated entirely to
  :mod:`obsidian.ontology.text_utils` so write-path and read-path keys
  are always identical.
"""

from __future__ import annotations

from typing import List
from uuid import UUID

from obsidian.ontology.alias_index import AliasIndex
from obsidian.ontology.concept_graph import ConceptGraph
from obsidian.ontology.models import Concept
from obsidian.ontology.text_utils import tokenize_query


class QueryResolver:
    """Resolves a raw user query to an ordered list of :class:`~obsidian.ontology.models.Concept` objects.

    Parameters
    ----------
    alias_index : AliasIndex
        Pre-built alias index mapping normalised text to Concept UUIDs.
    concept_graph : ConceptGraph
        Graph used to fetch :class:`Concept` objects by UUID.

    Examples
    --------
    >>> resolver = QueryResolver(alias_index, concept_graph)
    >>> concepts = resolver.resolve("Haven and Claude")
    >>> [c.label for c in concepts]
    ['Haven', 'Claude']
    """

    def __init__(self, alias_index: AliasIndex, concept_graph: ConceptGraph) -> None:
        self._alias_index = alias_index
        self._concept_graph = concept_graph

    def resolve(self, query: str) -> List[Concept]:
        """Resolve *query* to an ordered list of :class:`Concept` objects.

        Parameters
        ----------
        query : str
            Raw user query string.

        Returns
        -------
        list[Concept]
            Resolved concepts in first-found order, deduplicated by UUID.
            Returns an empty list if nothing resolves.
        """
        seen: set[UUID] = set()
        result: list[Concept] = []

        def _add(concept_id: UUID) -> None:
            if concept_id in seen:
                return
            if not self._concept_graph.has_concept(concept_id):
                return
            seen.add(concept_id)
            result.append(self._concept_graph.get_concept(concept_id))

        # Pass 1: whole normalised query (AliasIndex.lookup normalises internally)
        whole_id = self._alias_index.lookup(query)
        if whole_id is not None:
            _add(whole_id)

        # Pass 2: individual content-bearing tokens, stop words removed
        for token in tokenize_query(query):
            token_id = self._alias_index.lookup(token)
            if token_id is not None:
                _add(token_id)

        return result
