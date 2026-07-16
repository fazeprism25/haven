"""Tests for the confidence wiring in KnowledgeUpdater.

Test groups
-----------
TestNewConfidence            -- KnowledgeDecision.NEW threads
                                 ExtractedFact.confidence into the
                                 resulting KnowledgeObject.confidence
                                 instead of leaving it at the dataclass
                                 default.
TestConfirmConfidence         -- KnowledgeDecision.CONFIRM nudges the
                                 *existing* object's confidence rather
                                 than resetting it to the incoming
                                 fact's confidence.
TestBackwardCompatibility      -- a vault file written before the
                                 ``confidence`` frontmatter field existed
                                 (or with it simply absent) still loads,
                                 defaulting to 0.5.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from uuid import UUID

import yaml

from obsidian.core.enums import MemoryType
from obsidian.core.value_objects import TopicTag
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.models import (
    ClassificationResult,
    EvidenceEntry,
    ExtractedFact,
    ImportanceResult,
    KnowledgeDecision,
    KnowledgeObject,
)
from obsidian.memory_engine.memory_store import MemoryStore


# ---------------------------------------------------------------------------
# TestNewConfidence
# ---------------------------------------------------------------------------


class TestNewConfidence:
    def test_new_knowledge_object_takes_confidence_from_fact(self) -> None:
        fact = ExtractedFact(text="The user lives in Muscat.", confidence=0.82)
        knowledge = KnowledgeUpdater().apply(KnowledgeDecision.NEW, fact, None)
        assert knowledge.confidence == 0.82

    def test_confidence_is_independent_of_classification_confidence(self) -> None:
        fact = ExtractedFact(text="The user prefers tea.", confidence=0.6)
        classification = ClassificationResult(
            memory_type=MemoryType.PREFERENCE, confidence=0.15, reason="stated"
        )
        importance = ImportanceResult(score=0.4, reason="minor")
        knowledge = KnowledgeUpdater().apply(
            KnowledgeDecision.NEW, fact, None, classification, importance
        )
        # classification.confidence (0.15) must not leak into
        # KnowledgeObject.confidence -- only fact.confidence (0.6) should.
        assert knowledge.confidence == 0.6
        assert knowledge.memory_type == MemoryType.PREFERENCE
        assert knowledge.importance == 0.4

    def test_default_extracted_fact_confidence_still_applies(self) -> None:
        # ExtractedFact's own default confidence is 0.5, so a caller that
        # never set it explicitly sees the same value KnowledgeObject's
        # own default already was -- this is a coincidence of the two
        # defaults matching, not a fallback path.
        fact = ExtractedFact(text="The user uses Obsidian.")
        knowledge = KnowledgeUpdater().apply(KnowledgeDecision.NEW, fact, None)
        assert knowledge.confidence == 0.5


# ---------------------------------------------------------------------------
# TestConfirmConfidence
# ---------------------------------------------------------------------------


class TestConfirmConfidence:
    def test_confirm_nudges_existing_confidence_not_fact_confidence(self) -> None:
        existing = KnowledgeObject(
            canonical_fact="The user lives in Muscat.",
            confidence=0.6,
        )
        # A very different incoming confidence must not overwrite the
        # existing object's confidence -- CONFIRM only nudges it by a
        # fixed increment.
        fact = ExtractedFact(text="The user lives in Muscat.", confidence=0.99)
        updated = KnowledgeUpdater().apply(KnowledgeDecision.CONFIRM, fact, existing)
        assert updated.confidence == 0.65  # existing (0.6) + fixed nudge (0.05)

    def test_confirm_clamps_confidence_to_one(self) -> None:
        existing = KnowledgeObject(canonical_fact="The user lives in Muscat.", confidence=0.98)
        fact = ExtractedFact(text="The user lives in Muscat.", confidence=0.1)
        updated = KnowledgeUpdater().apply(KnowledgeDecision.CONFIRM, fact, existing)
        assert updated.confidence == 1.0

    def test_confirm_preserves_id_and_canonical_fact(self) -> None:
        existing = KnowledgeObject(
            canonical_fact="The user lives in Muscat.", confidence=0.6
        )
        fact = ExtractedFact(text="The user lives in Muscat.", confidence=0.9)
        updated = KnowledgeUpdater().apply(KnowledgeDecision.CONFIRM, fact, existing)
        assert updated.id == existing.id
        assert updated.canonical_fact == existing.canonical_fact


# ---------------------------------------------------------------------------
# TestUpdate
# ---------------------------------------------------------------------------


class TestUpdate:
    def _existing(self) -> KnowledgeObject:
        return KnowledgeObject(
            canonical_fact="I work at Google",
            memory_type=MemoryType.FACT,
            confidence=0.6,
            importance=0.4,
            valid_from=datetime(2024, 1, 1),
            valid_until=None,
            confirmation_count=2,
            metadata={"provenance": {"source": "chat"}},
        )

    def test_update_preserves_id(self) -> None:
        existing = self._existing()
        fact = ExtractedFact(text="I work at Google as a staff engineer", confidence=0.9)
        updated = KnowledgeUpdater().apply(KnowledgeDecision.UPDATE, fact, existing)
        assert updated.id == existing.id

    def test_update_overwrites_canonical_fact(self) -> None:
        existing = self._existing()
        fact = ExtractedFact(text="I work at Google as a staff engineer", confidence=0.9)
        updated = KnowledgeUpdater().apply(KnowledgeDecision.UPDATE, fact, existing)
        assert updated.canonical_fact == "I work at Google as a staff engineer"

    def test_update_appends_to_evidence_chain(self) -> None:
        existing = replace(
            self._existing(),
            evidence_chain=[
                EvidenceEntry(evidence="original", confidence=0.6)
            ],
        )
        fact = ExtractedFact(
            text="I work at Google as a staff engineer",
            evidence="refined",
            confidence=0.9,
        )
        updated = KnowledgeUpdater().apply(KnowledgeDecision.UPDATE, fact, existing)
        assert len(updated.evidence_chain) == len(existing.evidence_chain) + 1
        assert updated.evidence_chain[-1].evidence == "refined"
        # The prior evidence is preserved, not discarded.
        assert updated.evidence_chain[0].evidence == "original"

    def test_update_preserves_memory_type_importance_and_validity(self) -> None:
        existing = self._existing()
        # A refinement passes classification/importance for the *new* fact, but
        # UPDATE must keep the existing object's type, importance, and validity
        # window -- it refines wording, it does not reclassify.
        fact = ExtractedFact(text="I work at Google as a staff engineer", confidence=0.9)
        classification = ClassificationResult(
            memory_type=MemoryType.PREFERENCE, confidence=0.9, reason="x"
        )
        importance = ImportanceResult(score=0.99, reason="y")
        updated = KnowledgeUpdater().apply(
            KnowledgeDecision.UPDATE, fact, existing, classification, importance
        )
        assert updated.memory_type == MemoryType.FACT
        assert updated.importance == 0.4
        assert updated.valid_from == existing.valid_from
        assert updated.valid_until is None

    def test_update_preserves_metadata(self) -> None:
        existing = self._existing()
        fact = ExtractedFact(text="I work at Google as a staff engineer", confidence=0.9)
        updated = KnowledgeUpdater().apply(KnowledgeDecision.UPDATE, fact, existing)
        assert updated.metadata == {"provenance": {"source": "chat"}}

    def test_update_combines_confidence_and_increments_confirmation(self) -> None:
        existing = self._existing()  # confidence 0.6, confirmation_count 2
        fact = ExtractedFact(text="I work at Google as a staff engineer", confidence=0.9)
        updated = KnowledgeUpdater().apply(KnowledgeDecision.UPDATE, fact, existing)
        assert updated.confidence == (0.6 + 0.9) / 2.0
        assert updated.confirmation_count == 3

    def test_update_without_existing_raises(self) -> None:
        fact = ExtractedFact(text="anything", confidence=0.9)
        try:
            KnowledgeUpdater().apply(KnowledgeDecision.UPDATE, fact, None)
        except ValueError:
            pass
        else:  # pragma: no cover - fail path
            raise AssertionError("expected ValueError for UPDATE without knowledge")


# ---------------------------------------------------------------------------
# TestTopicsWiring
# ---------------------------------------------------------------------------


class TestTopicsWiring:
    """V2 ontology: ``classification.topics``/``.reason`` reach the
    resulting ``KnowledgeObject`` on NEW, and are carried forward
    unchanged (not re-derived) on CONFIRM/UPDATE/SUPERSEDE, since none of
    those paths re-run classification."""

    def test_new_copies_topics_and_reason_from_classification(self) -> None:
        fact = ExtractedFact(text="The user is watching LlamaIndex.", confidence=0.8)
        classification = ClassificationResult(
            memory_type=MemoryType.INTEREST,
            confidence=0.9,
            reason="The user is following a technology without adopting it.",
            topics=(TopicTag(name="AI", confidence=0.7),),
        )
        knowledge = KnowledgeUpdater().apply(
            KnowledgeDecision.NEW, fact, None, classification
        )
        assert knowledge.topics == (TopicTag(name="AI", confidence=0.7),)
        assert knowledge.metadata["classification_reason"] == (
            "The user is following a technology without adopting it."
        )

    def test_new_without_classification_has_no_topics_or_reason(self) -> None:
        fact = ExtractedFact(text="The user uses Obsidian.", confidence=0.8)
        knowledge = KnowledgeUpdater().apply(KnowledgeDecision.NEW, fact, None)
        assert knowledge.topics == ()
        assert "classification_reason" not in knowledge.metadata

    def test_confirm_preserves_existing_topics(self) -> None:
        existing = KnowledgeObject(
            canonical_fact="The user is watching LlamaIndex.",
            memory_type=MemoryType.INTEREST,
            topics=(TopicTag(name="AI", confidence=0.7),),
        )
        fact = ExtractedFact(text="The user is watching LlamaIndex.", confidence=0.9)
        updated = KnowledgeUpdater().apply(KnowledgeDecision.CONFIRM, fact, existing)
        assert updated.topics == existing.topics

    def test_update_preserves_existing_topics(self) -> None:
        existing = KnowledgeObject(
            canonical_fact="I work at Google",
            topics=(TopicTag(name="Programming", confidence=0.6),),
        )
        fact = ExtractedFact(text="I work at Google as a staff engineer", confidence=0.9)
        updated = KnowledgeUpdater().apply(KnowledgeDecision.UPDATE, fact, existing)
        assert updated.topics == existing.topics

    def test_supersede_preserves_existing_topics(self) -> None:
        existing = KnowledgeObject(
            canonical_fact="Use Chroma.",
            memory_type=MemoryType.DECISION,
            topics=(TopicTag(name="AI", confidence=0.5),),
        )
        fact = ExtractedFact(text="Use Qdrant instead.", confidence=0.9)
        updated = KnowledgeUpdater().apply(KnowledgeDecision.SUPERSEDE, fact, existing)
        assert updated.topics == existing.topics


# ---------------------------------------------------------------------------
# TestBackwardCompatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_vault_file_without_confidence_field_still_loads(self, tmp_path) -> None:
        """A hand-written legacy memory file with no ``confidence`` key at
        all (as every file written before this fix existed would have,
        since VaultWriter always wrote *some* confidence value -- this
        simulates an even older or hand-edited file) must still load,
        defaulting to 0.5 rather than raising.
        """
        legacy_frontmatter = {
            "id": "5f2f6f2a-1c1a-4a3a-9a3a-2f6f2a1c1a4a",
            "title": "The user lives in Muscat.",
            "canonical_fact": "The user lives in Muscat.",
            "memory_type": "fact",
            # "confidence" intentionally omitted.
            "importance": 0.7,
            "valid_from": "2024-01-01T00:00:00",
        }
        content = "---\n" + yaml.safe_dump(legacy_frontmatter) + "---\n\n# Legacy memory\n"
        (tmp_path / "legacy_memory.md").write_text(content, encoding="utf-8")

        store = MemoryStore(tmp_path)
        store.load()
        loaded = store.get(UUID(legacy_frontmatter["id"]))

        assert loaded.confidence == 0.5
        assert loaded.canonical_fact == "The user lives in Muscat."

    def test_round_trip_via_to_dict_from_dict_without_confidence_key(self) -> None:
        data = {
            "id": "5f2f6f2a-1c1a-4a3a-9a3a-2f6f2a1c1a4a",
            "canonical_fact": "The user lives in Muscat.",
            "memory_type": "fact",
        }
        restored = KnowledgeObject.from_dict(data)
        assert restored.confidence == 0.5
