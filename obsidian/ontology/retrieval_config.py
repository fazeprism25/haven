"""Centralised retrieval configuration for Haven Ontology.

All constants that govern concept-aware candidate retrieval live here.
Nothing in this module performs retrieval or computes any scores.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalConfig:
    """Immutable configuration for ontology-based candidate retrieval.

    Retrieval constants
    -------------------
    max_results : int
        Maximum number of KnowledgeObject candidates to return.
        Must be >= 1.
    max_depth : int
        Maximum number of hops when expanding the concept graph.
        Must be >= 1.
    activation_decay : float
        Fraction of activation carried to each neighbour during spreading.
        Must be in the open interval (0.0, 1.0).
    activation_threshold : float
        Spreading stops when a concept's activation falls below this value.
        Must be in the half-open interval (0.0, 1.0].
    minimum_candidate_score : float
        Candidates with a final composite score below this value are dropped.
        Must be in [0.0, 1.0].

    Scoring weights
    ---------------
    Each weight is used when forming a weighted composite score over a
    KnowledgeObject.  Weights are non-negative; normalisation is the
    responsibility of the ranker.

    weight_activation : float
        Weight for the propagated concept activation.
    weight_attachment_relevance : float
        Weight for the direct Attachment-to-Concept relevance score.
        Independent of ``weight_activation``: activation reflects
        propagated graph strength, attachment relevance reflects the
        strength of the direct evidentiary link — they are deliberately
        scored and weighted separately, never merged.
    weight_importance : float
        Weight for the KnowledgeObject importance score.
    weight_confidence : float
        Weight for the KnowledgeObject confidence score.
    weight_recency : float
        Weight for the recency component.
    weight_confirmation_count : float
        Weight for the confirmation-count component.
    weight_keyword_overlap : float
        Weight for the keyword-overlap component (see
        :class:`~obsidian.memory_engine.keyword_candidate_retriever.KeywordCandidateRetriever`'s
        ``keyword_overlap_score``). Independent of ``weight_attachment_relevance``:
        the two measure evidence strength from different retrieval paths
        (keyword vs. ontology) and are deliberately never merged into a
        single term, the same way ``weight_activation`` and
        ``weight_attachment_relevance`` are kept separate.

    Relationship propagation weights
    ---------------------------------
    Each weight controls how much activation a relationship type transmits
    to a neighbour.  Must be in the half-open interval (0.0, 1.0].
    Higher values preserve more activation across that edge type.

    propagation_weight_is_a : float
    propagation_weight_part_of : float
    propagation_weight_uses : float
    propagation_weight_depends_on : float
    propagation_weight_created_by : float
    propagation_weight_located_in : float
    propagation_weight_supports : float
    propagation_weight_related_to : float

    Examples
    --------
    >>> cfg = RetrievalConfig()
    >>> cfg.max_results
    50
    >>> cfg.activation_decay
    0.5
    >>> RetrievalConfig(max_results=0)
    Traceback (most recent call last):
        ...
    ValueError: max_results must be >= 1; got 0
    """

    # ------------------------------------------------------------------
    # Retrieval constants
    # ------------------------------------------------------------------

    max_results: int = 50
    max_depth: int = 3
    activation_decay: float = 0.5
    activation_threshold: float = 0.05
    minimum_candidate_score: float = 0.1

    # ------------------------------------------------------------------
    # Scoring weights
    # ------------------------------------------------------------------

    weight_activation: float = 0.35
    weight_attachment_relevance: float = 0.20
    weight_importance: float = 0.25
    weight_confidence: float = 0.20
    weight_recency: float = 0.15
    weight_confirmation_count: float = 0.05
    weight_keyword_overlap: float = 0.20

    # ------------------------------------------------------------------
    # Relationship propagation weights
    # ------------------------------------------------------------------

    propagation_weight_is_a: float = 0.9
    propagation_weight_part_of: float = 0.8
    propagation_weight_uses: float = 0.7
    propagation_weight_depends_on: float = 0.7
    propagation_weight_created_by: float = 0.5
    propagation_weight_located_in: float = 0.5
    propagation_weight_supports: float = 0.6
    propagation_weight_related_to: float = 0.3

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def __post_init__(self) -> None:
        # --- retrieval constants ---
        if self.max_results < 1:
            raise ValueError(f"max_results must be >= 1; got {self.max_results}")

        if self.max_depth < 1:
            raise ValueError(f"max_depth must be >= 1; got {self.max_depth}")

        if not (0.0 < self.activation_decay < 1.0):
            raise ValueError(
                f"activation_decay must be in (0.0, 1.0); got {self.activation_decay}"
            )

        if not (0.0 < self.activation_threshold <= 1.0):
            raise ValueError(
                "activation_threshold must be in (0.0, 1.0]; "
                f"got {self.activation_threshold}"
            )

        if not (0.0 <= self.minimum_candidate_score <= 1.0):
            raise ValueError(
                "minimum_candidate_score must be in [0.0, 1.0]; "
                f"got {self.minimum_candidate_score}"
            )

        # --- scoring weights (non-negative; no upper bound) ---
        _scoring_weights = {
            "weight_activation": self.weight_activation,
            "weight_attachment_relevance": self.weight_attachment_relevance,
            "weight_importance": self.weight_importance,
            "weight_confidence": self.weight_confidence,
            "weight_recency": self.weight_recency,
            "weight_confirmation_count": self.weight_confirmation_count,
            "weight_keyword_overlap": self.weight_keyword_overlap,
        }
        for name, value in _scoring_weights.items():
            if value < 0.0:
                raise ValueError(f"{name} must be >= 0.0; got {value}")

        # --- propagation weights in (0.0, 1.0] ---
        _propagation_weights = {
            "propagation_weight_is_a": self.propagation_weight_is_a,
            "propagation_weight_part_of": self.propagation_weight_part_of,
            "propagation_weight_uses": self.propagation_weight_uses,
            "propagation_weight_depends_on": self.propagation_weight_depends_on,
            "propagation_weight_created_by": self.propagation_weight_created_by,
            "propagation_weight_located_in": self.propagation_weight_located_in,
            "propagation_weight_supports": self.propagation_weight_supports,
            "propagation_weight_related_to": self.propagation_weight_related_to,
        }
        for name, value in _propagation_weights.items():
            if not (0.0 < value <= 1.0):
                raise ValueError(
                    f"{name} must be in (0.0, 1.0]; got {value}"
                )
