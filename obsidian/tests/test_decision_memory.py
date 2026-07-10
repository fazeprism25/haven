"""Tests for Decision Memory: DecisionMetadata/DecisionStatus, the
get_decision_metadata/with_decision_metadata helpers, and
KnowledgeUpdater.supersede_decision.

Test groups
-----------
TestDecisionStatus                  — enum values.
TestDecisionMetadataDefaults         — default construction.
TestDecisionMetadataRoundTrip        — to_dict/from_dict, including UUID
                                        and enum handling.
TestGetDecisionMetadata              — absent vs. present metadata key.
TestWithDecisionMetadata              — attaching metadata returns a new,
                                        immutable KnowledgeObject.
TestBackwardCompatibility            — pre-existing decisions (and
                                        non-decision types) with no
                                        "decision" metadata key still work.
TestVaultRoundTrip                    — DecisionMetadata survives an
                                        unmodified VaultWriter -> MemoryStore
                                        round trip (no code changes needed
                                        in either module).
TestSupersedeDecision                 — KnowledgeUpdater.supersede_decision
                                        behaviour: cross-linking, status,
                                        type guard.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.models import (
    DecisionMetadata,
    DecisionStatus,
    ExtractedFact,
    KnowledgeObject,
    get_decision_metadata,
    with_decision_metadata,
)
from obsidian.memory_engine.memory_store import MemoryStore
from obsidian.memory_engine.vault_writer import VaultWriter


# ---------------------------------------------------------------------------
# TestDecisionStatus
# ---------------------------------------------------------------------------


class TestDecisionStatus:
    def test_values(self) -> None:
        assert DecisionStatus.ACTIVE.value == "active"
        assert DecisionStatus.SUPERSEDED.value == "superseded"
        assert DecisionStatus.REVERSED.value == "reversed"


# ---------------------------------------------------------------------------
# TestDecisionMetadataDefaults
# ---------------------------------------------------------------------------


class TestDecisionMetadataDefaults:
    def test_defaults(self) -> None:
        metadata = DecisionMetadata()
        assert metadata.reason == ""
        assert metadata.alternatives_considered == []
        assert metadata.status == DecisionStatus.ACTIVE
        assert metadata.supersedes is None
        assert metadata.superseded_by is None


# ---------------------------------------------------------------------------
# TestDecisionMetadataRoundTrip
# ---------------------------------------------------------------------------


class TestDecisionMetadataRoundTrip:
    def test_to_dict_serialises_uuids_and_enum_as_strings(self) -> None:
        old_id = uuid4()
        new_id = uuid4()
        metadata = DecisionMetadata(
            reason="Qdrant fits our filtering needs better.",
            alternatives_considered=["Chroma", "Pinecone"],
            status=DecisionStatus.SUPERSEDED,
            supersedes=old_id,
            superseded_by=new_id,
        )
        data = metadata.to_dict()
        assert data == {
            "reason": "Qdrant fits our filtering needs better.",
            "alternatives_considered": ["Chroma", "Pinecone"],
            "status": "superseded",
            "supersedes": str(old_id),
            "superseded_by": str(new_id),
        }

    def test_to_dict_none_ids_stay_none(self) -> None:
        assert DecisionMetadata().to_dict()["supersedes"] is None
        assert DecisionMetadata().to_dict()["superseded_by"] is None

    def test_from_dict_round_trip(self) -> None:
        original = DecisionMetadata(
            reason="Because X.",
            alternatives_considered=["Y", "Z"],
            status=DecisionStatus.REVERSED,
            supersedes=uuid4(),
            superseded_by=uuid4(),
        )
        restored = DecisionMetadata.from_dict(original.to_dict())
        assert restored == original

    def test_from_dict_defaults_missing_keys(self) -> None:
        restored = DecisionMetadata.from_dict({})
        assert restored == DecisionMetadata()

    def test_from_dict_parses_uuid_strings(self) -> None:
        old_id = uuid4()
        restored = DecisionMetadata.from_dict({"supersedes": str(old_id)})
        assert restored.supersedes == old_id
        assert isinstance(restored.supersedes, UUID)


# ---------------------------------------------------------------------------
# TestGetDecisionMetadata
# ---------------------------------------------------------------------------


class TestGetDecisionMetadata:
    def test_absent_metadata_key_returns_none(self) -> None:
        ko = KnowledgeObject(memory_type=MemoryType.DECISION, metadata={})
        assert get_decision_metadata(ko) is None

    def test_absent_for_non_decision_type_too(self) -> None:
        ko = KnowledgeObject(memory_type=MemoryType.FACT, metadata={})
        assert get_decision_metadata(ko) is None

    def test_present_metadata_key_is_reconstructed(self) -> None:
        metadata = DecisionMetadata(reason="Because.", status=DecisionStatus.ACTIVE)
        ko = KnowledgeObject(
            memory_type=MemoryType.DECISION,
            metadata={"decision": metadata.to_dict()},
        )
        assert get_decision_metadata(ko) == metadata


# ---------------------------------------------------------------------------
# TestWithDecisionMetadata
# ---------------------------------------------------------------------------


class TestWithDecisionMetadata:
    def test_attaches_metadata_under_decision_key(self) -> None:
        ko = KnowledgeObject(memory_type=MemoryType.DECISION)
        metadata = DecisionMetadata(reason="Because.")

        updated = with_decision_metadata(ko, metadata)

        assert updated.metadata["decision"] == metadata.to_dict()
        assert get_decision_metadata(updated) == metadata

    def test_original_object_untouched(self) -> None:
        ko = KnowledgeObject(memory_type=MemoryType.DECISION)
        with_decision_metadata(ko, DecisionMetadata(reason="Because."))
        assert ko.metadata == {}

    def test_preserves_other_metadata_keys(self) -> None:
        ko = KnowledgeObject(
            memory_type=MemoryType.DECISION, metadata={"supersedes": "some-id"}
        )
        updated = with_decision_metadata(ko, DecisionMetadata(reason="Because."))
        assert updated.metadata["supersedes"] == "some-id"
        assert "decision" in updated.metadata


# ---------------------------------------------------------------------------
# TestBackwardCompatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_pre_existing_decision_without_metadata_still_constructs(self) -> None:
        ko = KnowledgeObject(
            canonical_fact="Build Manager AI first.",
            memory_type=MemoryType.DECISION,
        )
        assert get_decision_metadata(ko) is None
        assert ko.to_dict()["memory_type"] == "decision"

    def test_round_trip_via_to_dict_from_dict_without_decision_metadata(self) -> None:
        ko = KnowledgeObject(
            canonical_fact="Use FastEmbed.", memory_type=MemoryType.DECISION
        )
        restored = KnowledgeObject.from_dict(ko.to_dict())
        assert restored == ko
        assert get_decision_metadata(restored) is None


# ---------------------------------------------------------------------------
# TestVaultRoundTrip
# ---------------------------------------------------------------------------


class TestVaultRoundTrip:
    def test_decision_metadata_survives_vault_writer_and_memory_store(
        self, tmp_path
    ) -> None:
        metadata = DecisionMetadata(
            reason="Determinism matters more than flexibility here.",
            alternatives_considered=["Use an LLM judge"],
            status=DecisionStatus.ACTIVE,
        )
        ko = with_decision_metadata(
            KnowledgeObject(
                canonical_fact="Keep retrieval fully deterministic.",
                memory_type=MemoryType.DECISION,
            ),
            metadata,
        )

        VaultWriter(tmp_path).write(ko)

        store = MemoryStore(tmp_path)
        store.load()
        loaded = store.get(ko.id)

        assert get_decision_metadata(loaded) == metadata

    def test_legacy_decision_with_no_decision_metadata_still_loads(
        self, tmp_path
    ) -> None:
        ko = KnowledgeObject(
            canonical_fact="Build Manager AI first.",
            memory_type=MemoryType.DECISION,
        )

        VaultWriter(tmp_path).write(ko)

        store = MemoryStore(tmp_path)
        store.load()
        loaded = store.get(ko.id)

        assert get_decision_metadata(loaded) is None
        assert loaded.canonical_fact == "Build Manager AI first."


# ---------------------------------------------------------------------------
# TestSupersedeDecision
# ---------------------------------------------------------------------------


class TestSupersedeDecision:
    def _fact(self, text: str = "New decision text.") -> ExtractedFact:
        return ExtractedFact(text=text, evidence="stated directly", confidence=0.9)

    def test_rejects_non_decision_knowledge(self) -> None:
        ko = KnowledgeObject(canonical_fact="Not a decision.", memory_type=MemoryType.FACT)
        with pytest.raises(ValueError):
            KnowledgeUpdater.supersede_decision(self._fact(), ko)

    def test_new_object_supersedes_old_id(self) -> None:
        old = KnowledgeObject(
            canonical_fact="Use Chroma.", memory_type=MemoryType.DECISION
        )
        archived_old, new_decision = KnowledgeUpdater.supersede_decision(
            self._fact("Use Qdrant."),
            old,
            reason="Better filtering support.",
            alternatives_considered=["Pinecone"],
        )

        new_meta = get_decision_metadata(new_decision)
        assert new_meta is not None
        assert new_meta.supersedes == old.id
        assert new_meta.status == DecisionStatus.ACTIVE
        assert new_meta.reason == "Better filtering support."
        assert new_meta.alternatives_considered == ["Pinecone"]
        assert new_decision.canonical_fact == "Use Qdrant."

    def test_old_object_is_archived_and_cross_linked(self) -> None:
        old = KnowledgeObject(
            canonical_fact="Use Chroma.", memory_type=MemoryType.DECISION
        )
        archived_old, new_decision = KnowledgeUpdater.supersede_decision(
            self._fact("Use Qdrant."), old
        )

        assert archived_old.id == old.id
        assert archived_old.valid_until is not None

        old_meta = get_decision_metadata(archived_old)
        assert old_meta is not None
        assert old_meta.status == DecisionStatus.SUPERSEDED
        assert old_meta.superseded_by == new_decision.id

    def test_old_objects_prior_decision_metadata_is_preserved(self) -> None:
        old = with_decision_metadata(
            KnowledgeObject(canonical_fact="Use Chroma.", memory_type=MemoryType.DECISION),
            DecisionMetadata(reason="Simplicity."),
        )
        archived_old, _ = KnowledgeUpdater.supersede_decision(
            self._fact("Use Qdrant."), old
        )

        old_meta = get_decision_metadata(archived_old)
        assert old_meta.reason == "Simplicity."
        assert old_meta.status == DecisionStatus.SUPERSEDED

    def test_original_knowledge_object_is_not_mutated(self) -> None:
        old = KnowledgeObject(
            canonical_fact="Use Chroma.", memory_type=MemoryType.DECISION
        )
        KnowledgeUpdater.supersede_decision(self._fact("Use Qdrant."), old)

        assert old.valid_until is None
        assert get_decision_metadata(old) is None
