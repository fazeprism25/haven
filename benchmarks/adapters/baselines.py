"""Trivial retrieval baselines behind :class:`~benchmarks.adapters.base.BaseAdapter`.

These exist to contextualize Haven's and mem0's numbers: without a floor
(return everything), a recency heuristic, a pure lexical retriever (BM25),
and a pure dense retriever (embeddings), a pass rate in isolation says
nothing about how much any *mechanism* contributes. In particular
``recency`` is the acid test for the supersession/temporal/contradiction
categories — if "return the single most recent memory" matches Haven
there, those categories are not measuring the ontology.

Every baseline stores the same verbatim text every other adapter stores
under the runner's ``infer=False`` contract (see
``benchmarks/RUNNER_SPEC.md``); they differ *only* in how :meth:`search`
ranks that stored text. None of them import or touch Haven's pipeline, so
they are genuinely independent points of comparison, not Haven with a knob
turned (those are :mod:`benchmarks.adapters.ablations`).

All four are deterministic given the same inserts and query: ranking ties
break by insertion order (a stable sort over an insertion-ordered list),
and the embedding model is a fixed checkpoint run at inference only.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from benchmarks.adapters.base import BaseAdapter

# Number of memories the lexical/dense baselines return. Kept small on
# purpose: these baselines have no notion of "enough" — a fixed top-k is
# the standard way to turn a similarity ranking into an answer, and a
# small k is what exposes precision under noise (see the distractor
# sweep, benchmarks/runners/run_distractor.py). ``recency`` overrides
# this with 1; ``return_all`` ignores it entirely.
DEFAULT_TOP_K = 5

_EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"

# fastembed downloads and initializes a model on construction; doing that
# once per benchmark (a fresh adapter per case) would dominate runtime and
# add nothing, since the model is read-only at inference. Cache by name.
_EMBEDDING_MODELS: Dict[str, Any] = {}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokenization shared by the lexical baseline."""
    return _TOKEN_RE.findall(text.lower())


class _VerbatimStoreAdapter(BaseAdapter):
    """Shared write/reset plumbing for the ranking-only baselines.

    Holds inserted memory texts in an insertion-ordered list — the only
    state any of these baselines needs. Subclasses implement :meth:`search`
    (the sole point of difference) and nothing else.
    """

    def __init__(self) -> None:
        self._memories: List[str] = []

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]] = None) -> "_VerbatimStoreAdapter":
        """Fresh, empty instance. *config* is accepted and ignored — these
        baselines need no embedder/vector-store/LLM settings (see
        ``BaseAdapter.from_config``)."""
        return cls()

    def delete_all(self, filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Drop all stored memories. *filters* is ignored (single-tenant)."""
        self._memories = []
        return {"message": "Memories deleted successfully!"}

    def add(
        self,
        messages: List[Dict[str, str]],
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        infer: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Append each non-empty message ``content`` verbatim, newest last."""
        results: List[Dict[str, str]] = []
        for message in messages:
            content = message.get("content", "")
            if not content:
                continue
            self._memories.append(content)
            results.append({"id": str(len(self._memories) - 1), "memory": content, "event": "ADD"})
        return {"results": results}

    @staticmethod
    def _as_results(texts: List[str]) -> Dict[str, Any]:
        """Shape a ranked list of texts into the runner's result schema."""
        return {"results": [{"memory": text} for text in texts]}


class ReturnAllAdapter(_VerbatimStoreAdapter):
    """Return every stored memory, insertion order. The no-retrieval floor.

    Establishes the worst-case precision baseline: a system that never
    filters. It should pass any ``answer_contains``-only case whose fact is
    present, and fail ``must_not_contain`` cases that require dropping a
    superseded memory — which is exactly the signal that separates
    "retrieved the right thing" from "retrieved everything".
    """

    def search(self, query: str, filters: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        return self._as_results(list(self._memories))


class RecencyAdapter(_VerbatimStoreAdapter):
    """Return only the single most recently added memory (top-1).

    The strong, content-free heuristic for a memory system: "the latest
    statement wins". Deliberately top-1, not top-k, so that on
    supersession/temporal/contradiction cases it returns the *new* memory
    and not the old one — the behavior Haven's ontology is meant to earn.
    """

    def search(self, query: str, filters: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        if not self._memories:
            return {"results": []}
        return self._as_results([self._memories[-1]])


class BM25Adapter(_VerbatimStoreAdapter):
    """Pure lexical BM25 retrieval, top-k. No embeddings, no ontology.

    Standard Okapi BM25 (``k1=1.5``, ``b=0.75``) with a non-negative IDF
    (the ``log(1 + …)`` form), so tiny corpora never produce negative
    scores. Only memories with a positive score are returned (a query term
    must actually appear), capped at :data:`DEFAULT_TOP_K`; ties break by
    insertion order. Isolates "how far does plain keyword matching get?"
    """

    _K1 = 1.5
    _B = 0.75

    def search(self, query: str, filters: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        if not self._memories:
            return {"results": []}

        docs = [_tokenize(text) for text in self._memories]
        query_terms = _tokenize(query)
        if not query_terms:
            return {"results": []}

        n_docs = len(docs)
        avgdl = sum(len(doc) for doc in docs) / n_docs

        # Document frequency per term.
        df: Dict[str, int] = {}
        for doc in docs:
            for term in set(doc):
                df[term] = df.get(term, 0) + 1

        scored: List[Tuple[float, int]] = []
        for index, doc in enumerate(docs):
            dl = len(doc)
            score = 0.0
            for term in query_terms:
                tf = doc.count(term)
                if tf == 0:
                    continue
                idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
                denom = tf + self._K1 * (1 - self._B + self._B * dl / avgdl)
                score += idf * (tf * (self._K1 + 1)) / denom
            if score > 0:
                scored.append((score, index))

        # -score for descending relevance; +index keeps ties in insertion order.
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        top = [self._memories[index] for _, index in scored[:DEFAULT_TOP_K]]
        return self._as_results(top)


class EmbeddingAdapter(_VerbatimStoreAdapter):
    """Pure dense retrieval, top-k cosine over a fixed embedding model.

    Uses the same ``BAAI/bge-small-en-v1.5`` checkpoint the mem0 baseline
    is configured with (``benchmarks/runners/run_benchmarks.py``), so this
    is "what a plain vector search over the verbatim text scores" —
    directly comparable to mem0's own embedding retrieval, minus mem0's
    extraction layer. Isolates "how far does dense similarity alone get?"
    """

    def search(self, query: str, filters: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        if not self._memories:
            return {"results": []}

        import numpy as np

        model = self._get_model()
        doc_vectors = np.asarray(list(model.embed(self._memories)), dtype=float)
        query_vector = np.asarray(list(model.embed([query]))[0], dtype=float)

        doc_norms = np.linalg.norm(doc_vectors, axis=1)
        query_norm = np.linalg.norm(query_vector)
        denom = doc_norms * query_norm
        # Guard the degenerate zero-vector case rather than emit nan.
        sims = np.where(denom > 0, doc_vectors @ query_vector / np.where(denom > 0, denom, 1.0), 0.0)

        # Stable descending sort by similarity; ties keep insertion order.
        order = sorted(range(len(self._memories)), key=lambda i: (-float(sims[i]), i))
        top = [self._memories[i] for i in order[:DEFAULT_TOP_K]]
        return self._as_results(top)

    @staticmethod
    def _get_model() -> Any:
        model = _EMBEDDING_MODELS.get(_EMBEDDING_MODEL_NAME)
        if model is None:
            from fastembed import TextEmbedding

            model = TextEmbedding(model_name=_EMBEDDING_MODEL_NAME)
            _EMBEDDING_MODELS[_EMBEDDING_MODEL_NAME] = model
        return model
