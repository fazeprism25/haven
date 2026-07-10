"""ManagerPipeline that orchestrates the existing Manager AI stages.

The pipeline coordinates the following stages in order:

1. **Extractor** – extracts candidate facts from a conversation.
2. **Classifier** – classifies each extracted fact.
3. **ImportanceScorer** – scores the importance of each classified fact.
4. **CanonicalMatcher** – compares each fact against existing knowledge.
5. **KnowledgeUpdater** – applies the match decision to create/update
   :class:`KnowledgeObject` instances.

The pipeline does **not** contain any business logic belonging to
individual stages; it only calls them in the correct order and
combines their outputs into :class:`ExtractionDecision` objects.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Optional, Tuple

from obsidian.core.errors import ExtractionError
from obsidian.core.types import Conversation
from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier, ClassificationError
from obsidian.manager_ai.extractor import Extractor, ExtractionTrace
from obsidian.manager_ai.importance import ImportanceScorer, ImportanceScoringError
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.models import (
    ClassificationResult,
    ExtractedFact,
    ExtractionDecision,
    ImportanceResult,
    KnowledgeDecision,
    KnowledgeObject,
    SupersessionOperation,
    SupersessionResult,
)
from obsidian.ontology.retrieval_models import WorkingContext

#: Bumped whenever this pipeline's stage order or matching/apply logic
#: changes materially. Purely observational -- nothing in this module reads
#: it to make a decision; it exists so a persisted trace (see
#: :mod:`obsidian.ontology.write_trace_models`) can record which pipeline
#: behaviour produced it.
#:
#: History
#: -------
#: * ``1`` -- only ``NEW``/``CONFIRM`` driven end-to-end.
#: * ``2`` -- ``UPDATE`` wired into :meth:`ManagerPipeline.match_and_apply`
#:   (in-place refinement); ``SUPERSEDE`` still intentionally deferred.
PIPELINE_VERSION = 2


@dataclass
class ManagerPipeline:
    """Orchestrates the Manager AI pipeline stages.

    Parameters
    ----------
    extractor : Extractor
        The first stage – extracts candidate facts from a conversation.
    classifier : Classifier
        The second stage – classifies each extracted fact.
    importance_scorer : ImportanceScorer
        The third stage – scores the importance of each classified fact.
    canonical_matcher : CanonicalMatcher
        The fourth stage – compares each fact against existing knowledge.
    knowledge_updater : KnowledgeUpdater
        The fifth stage – applies the match decision to create or update
        :class:`KnowledgeObject` instances.
    """

    extractor: Extractor
    classifier: Classifier
    importance_scorer: ImportanceScorer
    canonical_matcher: CanonicalMatcher
    knowledge_updater: KnowledgeUpdater

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process(
        self,
        conversation: Conversation,
        existing_knowledge: Optional[List[KnowledgeObject]] = None,
        existing_context: Optional[List[WorkingContext]] = None,
    ) -> List[ExtractionDecision]:
        """Run the full Manager AI pipeline on *conversation*.

        The pipeline:

        1. Extracts candidate facts using the :class:`Extractor`.
        2. Classifies every extracted fact using the :class:`Classifier`.
        3. Scores the importance of every classified fact using the
           :class:`ImportanceScorer`.
        4. Matches each fact against *existing_knowledge* using the
           :class:`CanonicalMatcher`.
        5. Applies the match decision using the :class:`KnowledgeUpdater`.
        6. Combines the outputs into :class:`ExtractionDecision` objects.
        7. Returns the list of :class:`ExtractionDecision` objects.

        Parameters
        ----------
        conversation : Conversation
            The normalised conversation to process.
        existing_knowledge : list[KnowledgeObject] | None
            The current set of canonical knowledge objects stored in the
            vault.  If ``None``, the matcher will treat every fact as
            ``NEW``.
        existing_context : list[WorkingContext] | None
            Background information already known and saved in Haven,
            passed straight through to the :class:`Extractor` (see
            :meth:`Extractor.extract`) to help it resolve references in
            *conversation* -- never itself a source of extracted facts,
            and never read by any other stage. ``None`` (the default)
            reproduces this method's behaviour exactly as it was before
            this parameter existed.

        Returns
        -------
        list[ExtractionDecision]
            One decision per extracted fact, each containing the
            extracted fact, its classification, its importance score,
            and the resulting :class:`KnowledgeObject` (if applicable).

        Notes
        -----
        Steps 2 and 3 (classify, then score importance) run concurrently
        across facts: each fact's classify -> score chain only reads that
        fact (and, for scoring, that fact's own classification) and never
        another fact's result or ``existing``, so the per-fact chains have
        no data dependency on each other and can overlap their LLM network
        calls. Steps 4-5 (match, then apply) are *not* parallelized -- they
        read and mutate ``existing`` as they go (a ``NEW`` fact is appended
        so a later, identical fact in the same conversation can ``CONFIRM``
        against it), so that part stays strictly sequential, in extraction
        order, exactly as before.
        """
        decisions, _extraction_trace = self.process_with_trace(
            conversation,
            existing_knowledge=existing_knowledge,
            existing_context=existing_context,
        )
        return decisions

    def process_with_trace(
        self,
        conversation: Conversation,
        existing_knowledge: Optional[List[KnowledgeObject]] = None,
        existing_context: Optional[List[WorkingContext]] = None,
    ) -> Tuple[List[ExtractionDecision], ExtractionTrace]:
        """Run :meth:`process`, additionally returning the Extractor's trace.

        Contains exactly the same logic as :meth:`process` -- the only
        difference is calling :meth:`Extractor.extract_with_trace` instead
        of :meth:`Extractor.extract`, so the prompt and raw LLM response
        (otherwise discarded) are available to a caller building a
        :class:`~obsidian.ontology.write_trace_models.WriteTrace`.
        :meth:`process` is a thin wrapper around this method (see its
        body) so both share one implementation.

        A thin composition of :meth:`extract_classify_score` (the LLM
        stages) and :meth:`match_and_apply` (the deterministic matching
        stage) -- see those methods' docstrings. Splitting this method's
        previous single body into two reusable pieces lets a caller (e.g.
        the Memory Review preview/commit endpoints) run the LLM stages
        once, let the user edit the result, then run only the
        deterministic matching stage a second time against the edited
        facts -- without ever invoking the LLM twice.
        """
        scored_facts, extractor_trace = self.extract_classify_score(
            conversation, existing_context=existing_context
        )
        existing: List[KnowledgeObject] = (
            existing_knowledge if existing_knowledge is not None else []
        )
        decisions = self.match_and_apply(scored_facts, existing)
        return decisions, extractor_trace

    def extract_classify_score(
        self,
        conversation: Conversation,
        existing_context: Optional[List[WorkingContext]] = None,
    ) -> Tuple[
        List[Tuple[ExtractedFact, ClassificationResult, ImportanceResult]],
        ExtractionTrace,
    ]:
        """Run the Extractor, Classifier, and ImportanceScorer -- the LLM stages.

        This is every stage of :meth:`process_with_trace` that calls the
        LLM (:class:`Extractor`, :class:`Classifier`,
        :class:`ImportanceScorer`), and nothing else -- no
        :class:`CanonicalMatcher`/:class:`KnowledgeUpdater` matching, and no
        dependency on ``existing_knowledge``. Isolating exactly this subset
        is what lets a caller run the LLM once, hand the result to a user
        for review/editing, and defer matching to :meth:`match_and_apply`
        until after the user has had a chance to edit -- so an edit never
        triggers a second LLM call.

        A fact the :class:`Classifier` cannot classify into a valid
        :class:`~obsidian.core.enums.MemoryType` even after its one repair
        retry (:class:`~obsidian.manager_ai.classifier.ClassificationError`),
        or that the :class:`ImportanceScorer` cannot score even after its
        own repair retry
        (:class:`~obsidian.manager_ai.importance.ImportanceScoringError`),
        is **skipped**, not raised: it is simply absent from the returned
        list while every other fact is classified and scored normally. This
        is what keeps one bad fact from aborting a whole note -- the
        remaining memories still reach Memory Review.

        If the :class:`Extractor` itself cannot produce a usable response
        even after its own repair retry
        (:class:`~obsidian.core.errors.ExtractionError`), the whole
        conversation is treated as having produced **zero** extracted
        facts -- not an error. The request still completes normally with
        an empty result, exactly as if the LLM had legitimately found
        nothing worth remembering; it never raises out to the caller.

        Returns
        -------
        tuple[list[tuple[ExtractedFact, ClassificationResult, ImportanceResult]], ExtractionTrace]
            One ``(fact, classification, importance)`` triple per
            *successfully classified* extracted fact, in extraction order,
            plus the Extractor's trace (which still lists every extracted
            fact, skipped ones included).
        """
        try:
            extractor_trace = self.extractor.extract_with_trace(
                conversation, existing_context=existing_context
            )
        except ExtractionError:
            # The Extractor's LLM call never produced a usable response,
            # even after its own repair retry. Never fabricate facts and
            # never abort the request: treat this exactly like a
            # legitimate zero-fact extraction so the rest of the pipeline
            # (and the caller) proceeds normally.
            extractor_trace = ExtractionTrace(prompt="", raw_response="", facts=[])
        facts: List[ExtractedFact] = extractor_trace.facts

        def _classify_and_score(
            fact: ExtractedFact,
        ) -> Optional[Tuple[ClassificationResult, ImportanceResult]]:
            try:
                classification = self.classifier.classify(fact)
            except ClassificationError:
                # This one fact could not be classified even after a repair
                # retry; skip it so the remaining facts still reach the
                # caller. Never abort the whole conversation/note.
                return None
            try:
                importance = self.importance_scorer.score(fact, classification)
            except ImportanceScoringError:
                # Same per-fact skip as ClassificationError above: this
                # fact's importance couldn't be scored even after a repair
                # retry, so drop just this fact and keep processing the rest.
                return None
            return classification, importance

        if facts:
            # Capped, not len(facts): an unbounded pool fires one
            # simultaneous classify+score chain per extracted fact, so a
            # fact-heavy note can trigger a burst large enough to draw an
            # LLM provider rate limit (a 429 isn't a transient
            # TRANSPORT_EXCEPTIONS case and isn't a ValueError, so it isn't
            # caught as a per-fact error -- it would propagate and abort
            # the whole request, defeating the "one bad fact never aborts a
            # whole note" design above).
            with ThreadPoolExecutor(max_workers=min(len(facts), 8)) as executor:
                classify_score_results = list(
                    executor.map(_classify_and_score, facts)
                )
        else:
            classify_score_results = []

        scored_facts = [
            (fact, result[0], result[1])
            for fact, result in zip(facts, classify_score_results)
            if result is not None
        ]
        return scored_facts, extractor_trace

    def match_and_apply(
        self,
        scored_facts: List[
            Tuple[ExtractedFact, ClassificationResult, ImportanceResult]
        ],
        existing: List[KnowledgeObject],
    ) -> List[ExtractionDecision]:
        """Run CanonicalMatcher/KnowledgeUpdater over already-scored facts.

        This is every stage of :meth:`process_with_trace` after the LLM
        stages -- no LLM call happens here, only the deterministic,
        pure-Python :class:`CanonicalMatcher`/:class:`KnowledgeUpdater`
        pair. *existing* is mutated in place (a ``NEW`` fact is appended so
        a later, identical fact in *scored_facts* can ``CONFIRM`` against
        it), exactly as :meth:`process_with_trace` always has. Because
        this stage is cheap and side-effect-free with respect to the LLM,
        it is safe for a caller to invoke it more than once for the same
        conversation (e.g. once against a stale ``existing`` and again
        against a freshly-reloaded one) without violating "never run the
        LLM twice".

        Parameters
        ----------
        scored_facts : list[tuple[ExtractedFact, ClassificationResult, ImportanceResult]]
            Output of :meth:`extract_classify_score`, or an edited/
            user-added equivalent built without calling the LLM.
        existing : list[KnowledgeObject]
            The current set of canonical knowledge objects; mutated in
            place as facts are matched/applied.

        Returns
        -------
        list[ExtractionDecision]
            One decision per input triple, in order.
        """
        decisions: List[ExtractionDecision] = []

        for fact, classification, importance in scored_facts:
            decision, target = self.canonical_matcher.match_with_target(
                fact, existing
            )

            matched_knowledge: Optional[KnowledgeObject] = None
            supersession: Optional[SupersessionResult] = None
            if decision == KnowledgeDecision.NEW:
                matched_knowledge = self.knowledge_updater.apply(
                    decision, fact, None, classification, importance
                )
                existing.append(matched_knowledge)
            elif decision == KnowledgeDecision.CONFIRM and target is not None:
                matched_knowledge = self.knowledge_updater.apply(
                    decision, fact, target
                )
                existing[existing.index(target)] = matched_knowledge
            elif decision == KnowledgeDecision.UPDATE and target is not None:
                # In-place refinement: KnowledgeUpdater preserves target.id,
                # overwrites canonical_fact, and carries the evidence chain,
                # valid_from/valid_until, memory_type, importance, and
                # metadata forward (see _apply_update). Replacing the object
                # in ``existing`` by the same id keeps a later identical fact
                # in this conversation CONFIRMing against the refined text.
                previous_fact = target.canonical_fact
                matched_knowledge = self.knowledge_updater.apply(
                    decision, fact, target
                )
                existing[existing.index(target)] = matched_knowledge
                # Record the refinement in the designated SupersessionResult
                # slot so the write trace can explain exactly what changed.
                supersession = SupersessionResult(
                    matched_identity=matched_knowledge.id,
                    operation=SupersessionOperation.UPDATE,
                    confidence=fact.confidence,
                    reason=(
                        "Conservative in-place refinement of prior memory "
                        f"{matched_knowledge.id}; previous canonical_fact: "
                        f"{previous_fact!r}"
                    ),
                )
            else:
                # SUPERSEDE is intentionally not handled here -- the matcher
                # never returns it today (see CanonicalMatcher) and driving
                # write-time supersession is deferred (obsidian/docs/
                # TECH_DEBT.md). This branch is unreachable in practice; it
                # leaves ``matched_knowledge`` None so nothing is persisted.
                pass

            extraction_decision = ExtractionDecision(
                fact=fact,
                classification=classification,
                importance=importance,
                supersession=supersession,
                decision=decision,
                knowledge=matched_knowledge,
            )
            decisions.append(extraction_decision)

        return decisions
