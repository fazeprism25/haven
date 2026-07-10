"""Structured metrics for the incremental-ingestion benchmark suite.

Every benchmark scenario produces a :class:`ScenarioResult`, a flat list
of per-request :class:`RequestMetrics` plus optional free-text notes and
an optional :class:`AccuracyComparison` (only populated by the
context-dependent-update scenarios). :func:`write_results` serialises
every scenario, plus a metadata block, to one JSON file -- raw enough to
be re-plotted later without rerunning anything.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class RequestMetrics:
    """Everything measured for one simulated "Remember" click.

    Parameters
    ----------
    label : str
        Human-readable identifier for this click within its scenario
        (e.g. ``"click_3_of_3"``).
    pipeline : str
        ``"old_full"`` or ``"new_incremental"``.
    turn_count_sent : int
        Number of turns in the HTTP request body (the *whole*
        conversation as the caller sees it, not the evidence slice the
        Extractor actually saw).
    status_code : int
        HTTP status of the response.
    response_status : str, optional
        ``SaveMemoryResponse.status`` (``"success"``/``"duplicate"``), or
        ``None`` if the request errored before a body was produced.
    checkpoint_mode : str, optional
        The checkpoint's ``processing_history[-1].mode`` after this
        request, or ``None`` for the old/full pipeline (which never
        writes a checkpoint) or before any checkpoint exists.
    elapsed_seconds : float
        Wall-clock time for the whole HTTP call, using the scripted fake
        LLM -- see ``README.md``'s methodology section for what this
        does and does not measure.
    llm_calls : int
        Number of ``LLMInterface.generate`` calls made *during this
        request* (Extractor + Classifier + ImportanceScorer combined).
    extractor_prompt_chars, extractor_prompt_words, extractor_prompt_tokens_est : int
        Size of the prompt the Extractor was actually given this
        request (0 if the pipeline was never invoked, e.g. a duplicate
        short-circuit). ``_tokens_est`` is ``chars / 4``, a common rough
        English-text approximation -- not a real tokenizer count (this
        repo has no tokenizer dependency; see README for why that's
        an acceptable trade-off here).
    facts_extracted : int
        Number of candidate facts the Extractor returned this request.
    knowledge_objects_created : int
        Net growth in the vault's ``KnowledgeObject`` count this
        request (CONFIRM decisions that update an existing object
        don't count; only net-new objects do).
    working_context_queried : bool
        Whether ``MemoryEngine.query_working_context`` was called this
        request (true only for the new pipeline's incremental branch).
    working_context_seconds : float, optional
        Wall-clock duration of that call, or ``None`` if not queried.
    checkpoint_overhead_seconds : float
        Combined wall-clock time of ``CheckpointStore.load`` and
        ``CheckpointWriter.write`` this request (``0.0`` for the
        old/full pipeline, which never touches either).
    vault_object_count_after : int
        Total ``KnowledgeObject`` count in the vault after this
        request.
    """

    label: str
    pipeline: str
    turn_count_sent: int
    status_code: int
    response_status: Optional[str]
    checkpoint_mode: Optional[str]
    elapsed_seconds: float
    llm_calls: int
    extractor_prompt_chars: int
    extractor_prompt_words: int
    extractor_prompt_tokens_est: int
    facts_extracted: int
    knowledge_objects_created: int
    working_context_queried: bool
    working_context_seconds: Optional[float]
    checkpoint_overhead_seconds: float
    vault_object_count_after: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AccuracyComparison:
    """Fact-set comparison between the old and new pipeline for one scenario.

    Parameters
    ----------
    old_facts, new_facts : list[str]
        Every canonical fact text saved by each pipeline, in save order.
    missing_in_new, extra_in_new : list[str]
        Facts the old pipeline saved that the new one didn't, and vice
        versa.
    match : bool
        ``True`` iff ``old_facts`` and ``new_facts`` are the same set
        (order-independent).
    """

    old_facts: List[str]
    new_facts: List[str]
    missing_in_new: List[str]
    extra_in_new: List[str]
    match: bool

    @classmethod
    def compare(cls, old_facts: List[str], new_facts: List[str]) -> "AccuracyComparison":
        old_set, new_set = set(old_facts), set(new_facts)
        return cls(
            old_facts=list(old_facts),
            new_facts=list(new_facts),
            missing_in_new=sorted(old_set - new_set),
            extra_in_new=sorted(new_set - old_set),
            match=(old_set == new_set),
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ScenarioResult:
    """One benchmark scenario's full set of measured requests.

    Parameters
    ----------
    scenario_id, category, description : str
        Identification for the report.
    requests : list[RequestMetrics]
        Every click issued for this scenario, across both pipelines.
    accuracy : AccuracyComparison, optional
        Only populated by scenarios that explicitly compare extracted
        knowledge between pipelines (category 4).
    notes : list[str]
        Free-text observations worth surfacing verbatim in the report
        (e.g. "incremental pipeline failed to resolve the Python
        reference -- see accuracy.missing_in_new").
    """

    scenario_id: str
    category: str
    description: str
    requests: List[RequestMetrics] = field(default_factory=list)
    accuracy: Optional[AccuracyComparison] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "category": self.category,
            "description": self.description,
            "requests": [r.to_dict() for r in self.requests],
            "accuracy": self.accuracy.to_dict() if self.accuracy is not None else None,
            "notes": list(self.notes),
        }


def write_results(
    scenarios: List[ScenarioResult], path: Path, metadata: Dict[str, Any]
) -> None:
    """Serialise every scenario plus *metadata* to one JSON file at *path*."""
    payload = {
        "metadata": metadata,
        "scenarios": [s.to_dict() for s in scenarios],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
