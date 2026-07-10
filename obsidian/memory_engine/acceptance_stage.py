"""Acceptance stage for Haven's retrieval pipeline.

Implements ``docs/architecture/ACCEPTANCE_STAGE_DESIGN.md`` in full,
immediately downstream of
:class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`
and upstream of
:class:`~obsidian.memory_engine.deterministic_slot_allocator.DeterministicSlotAllocator`:

::

    RankedCandidate[]  (every scored candidate, unfiltered)
        │
        ▼
    AcceptanceStage        (this module)
        │
        ▼
    AcceptanceDecision[]  (one per input candidate, accepted or not)

Five stages run in order over the full, score-sorted candidate list, per
the design doc's §4 algorithm: an absolute floor (today's
``minimum_candidate_score``, unchanged semantics), abstention (the whole
query's best candidate must clear ``abstention_score`` or nothing is
accepted), a score-gap cut (the largest "real" drop within a lookahead
window), a relative quality floor (anchored to this query's own top
score), and a hard cap. Each stage can only shrink the surviving set;
every rejected candidate is tagged with which stage rejected it.

**Shared-supporting-concept exemption (stage 3).** A gap between two
consecutive candidates that both carry at least one common
``ActivatedConcept.concept_id`` in ``Candidate.supporting_concepts`` is
never treated as a qualifying cut point, regardless of its size — see
:func:`_shares_supporting_concept`. Two candidates the pipeline's own
graph evidence already shows are about the same concept fall through to
stage 4's relative-floor check instead, the same "keep both, let the
fuller context disambiguate" posture already used for same-magnitude
keyword ties (design doc §4.7). See
``docs/architecture/RANKING_FAILURE_INVESTIGATION.md`` §6 (Tier 1) for
the audit that motivated this.

Explicitly out of scope
------------------------
* **No ranking.** ``final_score`` and ``score_breakdown`` are consumed as
  given; this module never computes or adjusts a score. That is
  :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker`'s
  responsibility.
* **No slot allocation.** ``RetrievalConfig.max_results`` is untouched and
  unread here — after this stage runs,
  :class:`~obsidian.memory_engine.deterministic_slot_allocator.DeterministicSlotAllocator`
  still applies its own budget cap downstream, independently (see the
  design doc's §4.6).
* **No context building or formatting.**
* **No mutation.** ``RankedCandidate`` instances are frozen dataclasses
  and are never modified.

Design decisions
-----------------
* **``minimum_candidate_score`` stays on** :class:`~obsidian.ontology.retrieval_config.RetrievalConfig`
  **unchanged.** Stage 1 reuses it exactly as
  :class:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker.rank`
  already did, so callers passing an existing ``RetrievalConfig`` see no
  change to that cutoff's meaning or default. The four new thresholds
  this stage introduces live on :class:`AcceptanceConfig`, a new, separate
  frozen dataclass — kept out of ``RetrievalConfig`` (and therefore out of
  ``obsidian.ontology``) so this stage's tunables are fully additive to
  the existing retrieval/ranking configuration surface.
* **Decisions, not just a filtered list.** :meth:`AcceptanceStage.accept`
  returns one :class:`AcceptanceDecision` per input candidate — accepted
  or not — carrying the exact threshold, gap, and relative-score values
  that produced the decision, so the Retrieval Inspector never has to
  recompute or guess at why a candidate was rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from obsidian.ontology.retrieval_config import RetrievalConfig
from obsidian.ontology.retrieval_models import (
    REJECTION_ACCEPTANCE_CAP_EXCEEDED,
    REJECTION_BELOW_ABSTENTION_FLOOR,
    REJECTION_BELOW_MINIMUM_SCORE,
    REJECTION_BELOW_RELATIVE_FLOOR,
    REJECTION_SCORE_GAP_CUT,
    RankedCandidate,
)


@dataclass(frozen=True)
class AcceptanceConfig:
    """Tunable thresholds for :class:`AcceptanceStage`, additive to ``RetrievalConfig``.

    Every default is taken directly from
    ``docs/architecture/ACCEPTANCE_STAGE_DESIGN.md``'s measured traces (see
    that document's §4.2–§4.5 for how each starting point was derived).

    Parameters
    ----------
    abstention_score : float
        Stage 2. If the best candidate's ``final_score`` falls below this
        value, the whole query abstains (returns no accepted candidates).
        Must be in ``[0.0, 1.0]``.
    min_gap : float
        Stage 3. The minimum "real" drop in ``final_score`` between
        consecutive candidates for :func:`_find_gap_cut` to cut there.
        Must be in ``[0.0, 1.0]``.
    gap_window : int
        Stage 3. How many leading positions :func:`_find_gap_cut` is
        allowed to search for the largest qualifying gap. Must be >= 1.
    relative_floor_ratio : float
        Stage 4. A surviving candidate's ``final_score`` must be at least
        this fraction of the query's top ``final_score`` to survive. Must
        be in ``[0.0, 1.0]``.
    acceptance_max_k : int
        Stage 5. Hard cap on the number of candidates this stage accepts,
        independent of ``RetrievalConfig.max_results``. Must be >= 1.

    Raises
    ------
    ValueError
        If any field is outside its documented range.
    """

    abstention_score: float = 0.25
    min_gap: float = 0.04
    gap_window: int = 10
    relative_floor_ratio: float = 0.55
    acceptance_max_k: int = 8

    def __post_init__(self) -> None:
        if not 0.0 <= self.abstention_score <= 1.0:
            raise ValueError(
                f"abstention_score must be in [0.0, 1.0]; got {self.abstention_score}"
            )
        if not 0.0 <= self.min_gap <= 1.0:
            raise ValueError(f"min_gap must be in [0.0, 1.0]; got {self.min_gap}")
        if self.gap_window < 1:
            raise ValueError(f"gap_window must be >= 1; got {self.gap_window}")
        if not 0.0 <= self.relative_floor_ratio <= 1.0:
            raise ValueError(
                "relative_floor_ratio must be in [0.0, 1.0]; "
                f"got {self.relative_floor_ratio}"
            )
        if self.acceptance_max_k < 1:
            raise ValueError(
                f"acceptance_max_k must be >= 1; got {self.acceptance_max_k}"
            )


@dataclass(frozen=True)
class AcceptanceDecision:
    """The accept/reject verdict for one :class:`RankedCandidate`.

    Parameters
    ----------
    candidate : RankedCandidate
        The candidate this decision is about.
    accepted : bool
        Whether the candidate survived every stage.
    rejection_reason : str, optional
        One of the ``REJECTION_*`` constants from
        ``obsidian.ontology.retrieval_models`` naming the stage that
        rejected this candidate; ``None`` when ``accepted`` is ``True``.
    threshold_used : float, optional
        The threshold value compared against to reject this candidate;
        ``None`` when ``accepted`` is ``True``.
    score_gap : float, optional
        ``candidate.final_score`` minus the next candidate's
        ``final_score`` in the full descending ranking; ``None`` for the
        lowest-ranked candidate.
    relative_score : float
        ``candidate.final_score`` divided by the top ``final_score``
        across every candidate this stage considered (``0.0`` if every
        candidate scored ``0.0``).
    abstained : bool
        ``True`` only when this candidate was rejected because the whole
        query abstained (stage 2).

    Raises
    ------
    ValueError
        If ``accepted``/``rejection_reason`` are inconsistent, or if
        ``abstained`` is ``True`` without the abstention rejection reason.
    """

    candidate: RankedCandidate
    accepted: bool
    rejection_reason: Optional[str]
    threshold_used: Optional[float]
    score_gap: Optional[float]
    relative_score: float
    abstained: bool

    def __post_init__(self) -> None:
        if self.accepted and self.rejection_reason is not None:
            raise ValueError("accepted decision must not carry a rejection_reason")
        if not self.accepted and self.rejection_reason is None:
            raise ValueError("rejected decision must carry a rejection_reason")
        if self.abstained and self.rejection_reason != REJECTION_BELOW_ABSTENTION_FLOOR:
            raise ValueError(
                "abstained decision must carry REJECTION_BELOW_ABSTENTION_FLOOR"
            )


def _shares_supporting_concept(a: RankedCandidate, b: RankedCandidate) -> bool:
    """Return ``True`` if *a* and *b* share at least one supporting concept.

    Compares ``ActivatedConcept.concept_id`` across
    ``candidate.supporting_concepts`` on both sides. Two keyword-only
    candidates (empty ``supporting_concepts`` on either side) never share a
    concept by this definition — this exemption only fires when the
    pipeline's own graph evidence already connects both candidates to the
    same concept, per
    ``docs/architecture/RANKING_FAILURE_INVESTIGATION.md`` §6 (Tier 1).
    """
    a_ids = {ac.concept_id for ac in a.candidate.supporting_concepts}
    if not a_ids:
        return False
    return any(ac.concept_id in a_ids for ac in b.candidate.supporting_concepts)


def _find_gap_cut(survivors: List[RankedCandidate], min_gap: float, window: int) -> int:
    """Return the index to slice *survivors* at (stage 3).

    Mirrors ``docs/architecture/ACCEPTANCE_STAGE_DESIGN.md``'s §4 pseudocode:
    searches the leading ``min(window, len(survivors) - 1)`` consecutive
    gaps for the largest one that is ``>= min_gap``, and cuts immediately
    after it. No sufficiently large gap means no cut (returns
    ``len(survivors)``).

    A gap between two candidates that share a supporting concept (see
    :func:`_shares_supporting_concept`) is never eligible to be the cut
    point, no matter how large — the search simply continues past it to the
    next candidate gap, exactly as if that gap didn't qualify.
    """
    limit = min(window, len(survivors) - 1)
    best_gap, best_index = 0.0, len(survivors)
    for i in range(limit):
        if _shares_supporting_concept(survivors[i], survivors[i + 1]):
            continue
        gap = survivors[i].final_score - survivors[i + 1].final_score
        if gap >= min_gap and gap > best_gap:
            best_gap, best_index = gap, i + 1
    return best_index


class AcceptanceStage:
    """Decides which prefix of a ranked candidate list is trustworthy enough to keep.

    Stateless and reusable across calls to :meth:`accept` with different
    candidate lists or configurations.

    Examples
    --------
    >>> stage = AcceptanceStage()
    >>> decisions = stage.accept(ranked_all, RetrievalConfig(), AcceptanceConfig())
    >>> accepted = [d.candidate for d in decisions if d.accepted]
    """

    def accept(
        self,
        ranked_all: List[RankedCandidate],
        retrieval_config: RetrievalConfig,
        acceptance_config: AcceptanceConfig,
    ) -> List[AcceptanceDecision]:
        """Decide accept/reject for every candidate in *ranked_all*.

        Parameters
        ----------
        ranked_all : list[RankedCandidate]
            Every scored candidate for this query, typically the output of
            :meth:`~obsidian.memory_engine.deterministic_ranker.DeterministicRanker.score_all`
            (unfiltered — this stage needs the full list to do gap
            detection and relative-floor comparison). Not mutated.
        retrieval_config : RetrievalConfig
            Supplies ``minimum_candidate_score`` for stage 1, reused
            unchanged from today's semantics.
        acceptance_config : AcceptanceConfig
            Supplies the four new thresholds for stages 2–5.

        Returns
        -------
        list[AcceptanceDecision]
            One decision per input candidate, in descending
            ``final_score`` order (ties broken as
            :class:`~obsidian.ontology.retrieval_models.RankedCandidate`
            already defines). Empty when *ranked_all* is empty.
        """
        if not ranked_all:
            return []

        survivors = sorted(ranked_all)
        top_score = survivors[0].final_score

        accepted = [False] * len(survivors)
        rejection_reason: List[Optional[str]] = [None] * len(survivors)
        threshold_used: List[Optional[float]] = [None] * len(survivors)
        abstained = [False] * len(survivors)
        relative_score = [
            (c.final_score / top_score) if top_score > 0.0 else 0.0 for c in survivors
        ]
        score_gap: List[Optional[float]] = [
            survivors[i].final_score - survivors[i + 1].final_score
            if i + 1 < len(survivors)
            else None
            for i in range(len(survivors))
        ]

        def _decisions() -> List[AcceptanceDecision]:
            return [
                AcceptanceDecision(
                    candidate=survivors[i],
                    accepted=accepted[i],
                    rejection_reason=rejection_reason[i],
                    threshold_used=threshold_used[i],
                    score_gap=score_gap[i],
                    relative_score=relative_score[i],
                    abstained=abstained[i],
                )
                for i in range(len(survivors))
            ]

        # Stage 1 — absolute floor.
        stage1_pass = [
            i
            for i, c in enumerate(survivors)
            if c.final_score >= retrieval_config.minimum_candidate_score
        ]
        for i in range(len(survivors)):
            if i not in stage1_pass:
                rejection_reason[i] = REJECTION_BELOW_MINIMUM_SCORE
                threshold_used[i] = retrieval_config.minimum_candidate_score
        if not stage1_pass:
            return _decisions()

        # Stage 2 — abstention.
        if survivors[stage1_pass[0]].final_score < acceptance_config.abstention_score:
            for i in stage1_pass:
                rejection_reason[i] = REJECTION_BELOW_ABSTENTION_FLOOR
                threshold_used[i] = acceptance_config.abstention_score
                abstained[i] = True
            return _decisions()

        # Stage 3 — score-gap cut.
        passing = [survivors[i] for i in stage1_pass]
        cut = _find_gap_cut(passing, acceptance_config.min_gap, acceptance_config.gap_window)
        gap_kept = stage1_pass[:cut]
        for i in stage1_pass[cut:]:
            rejection_reason[i] = REJECTION_SCORE_GAP_CUT
            threshold_used[i] = acceptance_config.min_gap

        # Stage 4 — relative quality floor.
        floor = top_score * acceptance_config.relative_floor_ratio
        floor_kept = [i for i in gap_kept if survivors[i].final_score >= floor]
        for i in gap_kept:
            if survivors[i].final_score < floor:
                rejection_reason[i] = REJECTION_BELOW_RELATIVE_FLOOR
                threshold_used[i] = floor

        # Stage 5 — hard cap.
        for position, i in enumerate(floor_kept):
            if position < acceptance_config.acceptance_max_k:
                accepted[i] = True
            else:
                rejection_reason[i] = REJECTION_ACCEPTANCE_CAP_EXCEEDED
                threshold_used[i] = float(acceptance_config.acceptance_max_k)

        return _decisions()
