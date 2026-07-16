"""Internal models used only by the Manager AI pipeline.

These classes are NOT part of the public Core API.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from obsidian.core.enums import MemoryType
from obsidian.core.value_objects import TopicTag


class SupersessionOperation(str, Enum):
    """Recommended operation for an existing Memory that matches a new fact.

    Values
    ------
    NONE : str
        No action needed.
    UPDATE : str
        Update the existing Memory with the new content.
    SUPERSEDE : str
        Replace the existing Memory entirely.
    ARCHIVE : str
        Archive the existing Memory (it is no longer relevant).
    """

    NONE = "none"
    UPDATE = "update"
    SUPERSEDE = "supersede"
    ARCHIVE = "archive"


class KnowledgeDecision(str, Enum):
    """Decision made by the Canonical Matcher for a knowledge object.

    Values
    ------
    NEW : str
        The fact does not match any existing knowledge object; a new one
        should be created.
    CONFIRM : str
        The fact matches an existing knowledge object; the object's
        confidence should be increased.
    UPDATE : str
        The fact matches an existing knowledge object but provides new
        information; the object's content should be modified.
    SUPERSEDE : str
        The fact contradicts an existing knowledge object; the old object
        should be archived and a new one created.
    """

    NEW = "new"
    CONFIRM = "confirm"
    UPDATE = "update"
    SUPERSEDE = "supersede"


@dataclass(frozen=True)
class EvidenceEntry:
    """A single piece of evidence supporting a :class:`KnowledgeObject`.

    Parameters
    ----------
    source_event_id : UUID
        The unique identifier of the conversation event that provided
        this evidence.
    evidence : str
        A human‑readable explanation of why this fact was extracted.
    confidence : float
        The confidence that this evidence is correct (0.0 – 1.0).
    timestamp : datetime | None
        When this evidence was recorded.  ``None`` means the timestamp
        was not captured.
    """

    source_event_id: UUID = field(default_factory=uuid4)
    evidence: str = ""
    confidence: float = 0.5
    timestamp: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        return {
            "source_event_id": str(self.source_event_id),
            "evidence": self.evidence,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat()
            if self.timestamp is not None
            else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvidenceEntry:
        """Create an EvidenceEntry from a dictionary."""
        return cls(
            source_event_id=UUID(data["source_event_id"])
            if isinstance(data.get("source_event_id"), str)
            else data.get("source_event_id", uuid4()),
            evidence=data.get("evidence", ""),
            confidence=data.get("confidence", 0.5),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if isinstance(data.get("timestamp"), str)
            else data.get("timestamp", None),
        )


@dataclass(frozen=True)
class KnowledgeObject:
    """A single canonical piece of knowledge stored in the vault.

    This is the central data structure that the Manager AI pipeline
    creates, confirms, updates, or supersedes.  Each object represents
    exactly one atomic fact that has been extracted, classified, and
    matched against the existing knowledge base.

    Parameters
    ----------
    id : UUID
        Unique identifier for this knowledge object.
    canonical_fact : str
        The normalised, canonical textual representation of the fact.
    memory_type : MemoryType
        The semantic category of the fact (e.g. FACT, PREFERENCE, GOAL).
    confidence : float
        The current confidence that this fact is correct (0.0 – 1.0).
    importance : float
        The current importance score of this fact (0.0 – 1.0).
    evidence_chain : list[EvidenceEntry]
        Ordered list of evidence entries that contributed to this
        knowledge object, providing full provenance.
    valid_from : datetime
        The timestamp when this knowledge object was first created.
    valid_until : datetime | None
        The timestamp after which this knowledge object is no longer
        considered valid (``None`` means it is still active).
    last_confirmed : datetime | None
        The most recent timestamp at which this object was confirmed
        by an incoming fact (``None`` if never confirmed).
    confirmation_count : int
        The number of times this object has been confirmed by
        independent facts.
    metadata : dict[str, Any]
        Arbitrary key‑value metadata associated with this object.
    topics : tuple[TopicTag, ...]
        Canonicalized topical tags (see
        :func:`obsidian.manager_ai.topic_canonicalizer.canonicalize_topics`),
        at most 3, set once from the originating
        :class:`ClassificationResult` and carried forward unchanged by
        :class:`~obsidian.manager_ai.knowledge_updater.KnowledgeUpdater`'s
        confirm/update/supersede paths (classification does not re-run on
        those paths, so there is nothing new to merge in). Informational
        only -- not read by retrieval, ranking, or acceptance. Empty for
        every ``KnowledgeObject`` created before this field existed.

    Raises
    ------
    ValueError
        If *confidence* or *importance* are outside [0, 1].
    """

    id: UUID = field(default_factory=uuid4)
    canonical_fact: str = ""
    memory_type: MemoryType = MemoryType.FACT
    confidence: float = 0.5
    importance: float = 0.5
    evidence_chain: List[EvidenceEntry] = field(default_factory=list)
    valid_from: datetime = field(default_factory=datetime.utcnow)
    valid_until: Optional[datetime] = None
    last_confirmed: Optional[datetime] = None
    confirmation_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    topics: Tuple[TopicTag, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError("importance must be between 0 and 1")
        object.__setattr__(self, "topics", tuple(self.topics))

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        return {
            "id": str(self.id),
            "canonical_fact": self.canonical_fact,
            "memory_type": self.memory_type.value,
            "confidence": self.confidence,
            "importance": self.importance,
            "evidence_chain": [e.to_dict() for e in self.evidence_chain],
            "valid_from": self.valid_from.isoformat(),
            "valid_until": self.valid_until.isoformat()
            if self.valid_until is not None
            else None,
            "last_confirmed": self.last_confirmed.isoformat()
            if self.last_confirmed is not None
            else None,
            "confirmation_count": self.confirmation_count,
            "metadata": self.metadata,
            "topics": [t.to_dict() for t in self.topics],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> KnowledgeObject:
        """Create a KnowledgeObject from a dictionary."""
        return cls(
            id=UUID(data["id"])
            if isinstance(data.get("id"), str)
            else data.get("id", uuid4()),
            canonical_fact=data.get("canonical_fact", ""),
            memory_type=MemoryType(data["memory_type"])
            if "memory_type" in data
            else MemoryType.FACT,
            confidence=data.get("confidence", 0.5),
            importance=data.get("importance", 0.5),
            evidence_chain=[
                EvidenceEntry.from_dict(e)
                for e in data.get("evidence_chain", [])
            ],
            valid_from=datetime.fromisoformat(data["valid_from"])
            if isinstance(data.get("valid_from"), str)
            else data.get("valid_from", datetime.utcnow()),
            valid_until=datetime.fromisoformat(data["valid_until"])
            if isinstance(data.get("valid_until"), str)
            else data.get("valid_until", None),
            last_confirmed=datetime.fromisoformat(data["last_confirmed"])
            if isinstance(data.get("last_confirmed"), str)
            else data.get("last_confirmed", None),
            confirmation_count=data.get("confirmation_count", 0),
            metadata=data.get("metadata", {}),
            topics=tuple(
                TopicTag.from_dict(t) for t in data.get("topics", [])
            ),
        )


# ---------------------------------------------------------------------------
# Decision Memory
# ---------------------------------------------------------------------------
#
# Decision Memory adds "why" (reason, alternatives considered) and
# supersession lineage (supersedes/superseded_by) to a MemoryType.DECISION
# KnowledgeObject. These fields are meaningful only for decisions -- every
# other memory type would carry them as permanently-empty dead weight if
# they were new KnowledgeObject dataclass fields -- so DecisionMetadata is
# instead stored under KnowledgeObject.metadata["decision"], reusing the
# free-form metadata dict that already exists and already round-trips
# through VaultWriter/MemoryStore with no code changes there. A
# KnowledgeObject with no "decision" metadata key -- every non-decision
# memory, and every decision written before this feature existed -- simply
# has get_decision_metadata() return None; nothing needs migrating.
#
# Decision, Confidence, Created, and Last Confirmed are deliberately not
# duplicated here: they already exist as KnowledgeObject.canonical_fact,
# .confidence, .valid_from, and .last_confirmed respectively.


class DecisionStatus(str, Enum):
    """Lifecycle status of a decision.

    Values
    ------
    ACTIVE : str
        The decision currently stands.
    SUPERSEDED : str
        A newer decision has replaced this one (see ``superseded_by``).
    REVERSED : str
        The decision was reversed without a replacement decision taking
        its place.
    """

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REVERSED = "reversed"


@dataclass(frozen=True)
class DecisionMetadata:
    """Why a decision was made, and its supersession lineage.

    Stored under ``KnowledgeObject.metadata["decision"]`` -- see the
    "Decision Memory" section comment above for why this is not a set of
    dedicated ``KnowledgeObject`` fields.

    Parameters
    ----------
    reason : str
        Why the decision was made.
    alternatives_considered : list[str]
        Other options that were considered and not chosen.
    status : DecisionStatus
        The decision's current lifecycle status.
    supersedes : UUID, optional
        The ``id`` of the ``KnowledgeObject`` this decision replaces, if
        any.
    superseded_by : UUID, optional
        The ``id`` of the ``KnowledgeObject`` that replaced this decision,
        if any.
    """

    reason: str = ""
    alternatives_considered: List[str] = field(default_factory=list)
    status: DecisionStatus = DecisionStatus.ACTIVE
    supersedes: Optional[UUID] = None
    superseded_by: Optional[UUID] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        return {
            "reason": self.reason,
            "alternatives_considered": list(self.alternatives_considered),
            "status": self.status.value,
            "supersedes": str(self.supersedes) if self.supersedes is not None else None,
            "superseded_by": str(self.superseded_by)
            if self.superseded_by is not None
            else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> DecisionMetadata:
        """Create a DecisionMetadata from a dictionary."""
        return cls(
            reason=data.get("reason", ""),
            alternatives_considered=list(data.get("alternatives_considered", [])),
            status=DecisionStatus(data["status"])
            if "status" in data
            else DecisionStatus.ACTIVE,
            supersedes=UUID(data["supersedes"]) if data.get("supersedes") else None,
            superseded_by=UUID(data["superseded_by"])
            if data.get("superseded_by")
            else None,
        )


#: Key under ``KnowledgeObject.metadata`` where a serialised
#: :class:`DecisionMetadata` is stored.
DECISION_METADATA_KEY = "decision"


def get_decision_metadata(knowledge: KnowledgeObject) -> Optional[DecisionMetadata]:
    """Return the :class:`DecisionMetadata` attached to *knowledge*, if any.

    Returns ``None`` when *knowledge* has no ``"decision"`` metadata key --
    the case for every ``KnowledgeObject`` created before this feature
    existed (of any memory type), so existing vault data keeps working
    unchanged.
    """
    raw = knowledge.metadata.get(DECISION_METADATA_KEY)
    if raw is None:
        return None
    return DecisionMetadata.from_dict(raw)


def with_decision_metadata(
    knowledge: KnowledgeObject, decision_metadata: DecisionMetadata
) -> KnowledgeObject:
    """Return a copy of *knowledge* with *decision_metadata* attached.

    Stores it under ``metadata["decision"]`` -- KnowledgeObject's existing
    free-form metadata dict -- so no other ``KnowledgeObject`` consumer
    (VaultWriter, MemoryStore, ontology, retrieval) needs to change to
    keep working.
    """
    new_metadata = dict(knowledge.metadata)
    new_metadata[DECISION_METADATA_KEY] = decision_metadata.to_dict()
    return replace(knowledge, metadata=new_metadata)


@dataclass(frozen=True)
class ExtractedFact:
    """A single fact extracted from a conversation event.

    Parameters
    ----------
    text : str
        The textual content of the fact.
    source_event_id : UUID
        The unique identifier of the event from which this fact was
        extracted.
    evidence : str
        A human‑readable explanation of why this fact was extracted.
    confidence : float
        The confidence that this fact is correct (0.0 – 1.0).

    Raises
    ------
    ValueError
        If *confidence* is outside [0, 1].
    """

    text: str = ""
    source_event_id: UUID = field(default_factory=uuid4)
    evidence: str = ""
    confidence: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        return {
            "text": self.text,
            "source_event_id": str(self.source_event_id),
            "evidence": self.evidence,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExtractedFact:
        """Create an ExtractedFact from a dictionary."""
        return cls(
            text=data.get("text", ""),
            source_event_id=UUID(data["source_event_id"])
            if isinstance(data.get("source_event_id"), str)
            else data.get("source_event_id", uuid4()),
            evidence=data.get("evidence", ""),
            confidence=data.get("confidence", 0.5),
        )


@dataclass(frozen=True)
class ClassificationResult:
    """Output of the Classifier stage.

    Parameters
    ----------
    memory_type : MemoryType
        The semantic category assigned to the fact.
    confidence : float
        The classifier's confidence in this assignment (0.0 – 1.0).
    reason : str
        A human‑readable explanation of why this type -- and these
        ``topics`` -- were chosen.
    topics : tuple[TopicTag, ...]
        Canonicalized topical tags for this fact (see
        :func:`obsidian.manager_ai.topic_canonicalizer.canonicalize_topics`),
        at most 3. Empty when the Classifier's response omitted a
        ``topics`` key or it canonicalized to nothing.

    Raises
    ------
    ValueError
        If *confidence* is outside [0, 1].
    """

    memory_type: MemoryType = MemoryType.FACT
    confidence: float = 0.5
    reason: str = ""
    topics: Tuple[TopicTag, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "topics", tuple(self.topics))

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        return {
            "memory_type": self.memory_type.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "topics": [t.to_dict() for t in self.topics],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ClassificationResult:
        """Create a ClassificationResult from a dictionary."""
        return cls(
            memory_type=MemoryType(data["memory_type"])
            if "memory_type" in data
            else MemoryType.FACT,
            confidence=data.get("confidence", 0.5),
            reason=data.get("reason", ""),
            topics=tuple(
                TopicTag.from_dict(t) for t in data.get("topics", [])
            ),
        )


@dataclass(frozen=True)
class ImportanceResult:
    """Output of the Importance stage.

    Parameters
    ----------
    score : float
        The importance score assigned to the fact (0.0 – 1.0).
    reason : str
        A human‑readable explanation of why this score was assigned.

    Raises
    ------
    ValueError
        If *score* is outside [0, 1].
    """

    score: float = 0.5
    reason: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("score must be between 0 and 1")

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        return {
            "score": self.score,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ImportanceResult:
        """Create an ImportanceResult from a dictionary."""
        return cls(
            score=data.get("score", 0.5),
            reason=data.get("reason", ""),
        )


@dataclass(frozen=True)
class SupersessionResult:
    """Output of the Supersession stage.

    Parameters
    ----------
    matched_identity : UUID
        The identity of the existing Memory that was matched.
    operation : SupersessionOperation
        The recommended operation (none, update, supersede, archive).
    confidence : float
        The confidence in this recommendation (0.0 – 1.0).
    reason : str
        A human‑readable explanation of why this operation was chosen.

    Raises
    ------
    ValueError
        If *confidence* is outside [0, 1].
    """

    matched_identity: UUID = field(default_factory=uuid4)
    operation: SupersessionOperation = SupersessionOperation.NONE
    confidence: float = 0.5
    reason: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        return {
            "matched_identity": str(self.matched_identity),
            "operation": self.operation.value,
            "confidence": self.confidence,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SupersessionResult:
        """Create a SupersessionResult from a dictionary."""
        return cls(
            matched_identity=UUID(data["matched_identity"])
            if isinstance(data.get("matched_identity"), str)
            else data.get("matched_identity", uuid4()),
            operation=SupersessionOperation(data["operation"])
            if "operation" in data
            else SupersessionOperation.NONE,
            confidence=data.get("confidence", 0.5),
            reason=data.get("reason", ""),
        )


@dataclass(frozen=True)
class ExtractionDecision:
    """Accumulated decision data as it moves through the Manager AI pipeline.

    This dataclass is progressively enriched by each stage of the pipeline:

    * The **Extractor** populates ``fact``.
    * The **Classifier** populates ``classification``.
    * The **Importance** stage populates ``importance``.
    * The **Supersession** stage populates ``supersession``.
    * The **CanonicalMatcher** stage (via :class:`ManagerPipeline`)
      populates ``decision`` with the raw :class:`KnowledgeDecision` it
      returned -- independent of whether that decision produced a
      :class:`KnowledgeObject` (e.g. today's pipeline leaves ``UPDATE``/
      ``SUPERSEDE`` unhandled, so ``decision`` can be set while
      ``knowledge`` stays ``None``, honestly reflecting that gap).
    * The **KnowledgeUpdater** stage (via :class:`ManagerPipeline`)
      populates ``knowledge`` with the resulting
      :class:`KnowledgeObject`, when the decision produced one (e.g. a
      ``NEW``/``CONFIRM`` fact does; a decision that matched nothing and
      isn't handled yet does not).

    Parameters
    ----------
    fact : ExtractedFact, optional
        The extracted fact.
    classification : ClassificationResult, optional
        The classification result.
    importance : ImportanceResult, optional
        The importance result.
    supersession : SupersessionResult, optional
        The supersession result.
    decision : KnowledgeDecision, optional
        The raw decision :class:`~obsidian.manager_ai.canonical_matcher.CanonicalMatcher`
        returned for this fact.
    knowledge : KnowledgeObject, optional
        The knowledge object :class:`KnowledgeUpdater` produced for this
        fact, if any.
    """

    fact: Optional[ExtractedFact] = None
    classification: Optional[ClassificationResult] = None
    importance: Optional[ImportanceResult] = None
    supersession: Optional[SupersessionResult] = None
    decision: Optional[KnowledgeDecision] = None
    knowledge: Optional[KnowledgeObject] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serializable dictionary."""
        d: Dict[str, Any] = {}
        if self.fact is not None:
            d["fact"] = self.fact.to_dict()
        if self.classification is not None:
            d["classification"] = self.classification.to_dict()
        if self.importance is not None:
            d["importance"] = self.importance.to_dict()
        if self.supersession is not None:
            d["supersession"] = self.supersession.to_dict()
        if self.decision is not None:
            d["decision"] = self.decision.value
        if self.knowledge is not None:
            d["knowledge"] = self.knowledge.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExtractionDecision:
        """Create an ExtractionDecision from a dictionary."""
        return cls(
            fact=ExtractedFact.from_dict(data["fact"])
            if data.get("fact") is not None
            else None,
            classification=ClassificationResult.from_dict(data["classification"])
            if data.get("classification") is not None
            else None,
            importance=ImportanceResult.from_dict(data["importance"])
            if data.get("importance") is not None
            else None,
            supersession=SupersessionResult.from_dict(data["supersession"])
            if data.get("supersession") is not None
            else None,
            decision=KnowledgeDecision(data["decision"])
            if data.get("decision") is not None
            else None,
            knowledge=KnowledgeObject.from_dict(data["knowledge"])
            if data.get("knowledge") is not None
            else None,
        )
