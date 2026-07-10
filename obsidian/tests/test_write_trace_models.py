"""Unit tests for obsidian.ontology.write_trace_models.

Test groups
-----------
TestRoundTrip           -- WriteTrace/FactTrace/etc. survive to_dict/from_dict.
TestDefaults            -- optional fields default sensibly when omitted
                           from a dict (e.g. an older/partial record).
TestVersionMetadata     -- schema_version/pipeline_version/extractor_prompt_version
                           round-trip and tolerate absence.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from obsidian.core.enums import MemoryType, SourceType
from obsidian.manager_ai.models import KnowledgeDecision, SupersessionOperation
from obsidian.ontology.write_trace_models import (
    CURRENT_WRITE_TRACE_SCHEMA_VERSION,
    CheckpointStageTrace,
    ExtractorStageTrace,
    FactTrace,
    OntologyProposalTrace,
    OntologyStageTrace,
    WriteTrace,
)


def make_fact_trace(index: int = 0) -> FactTrace:
    return FactTrace(
        fact_index=index,
        fact_text="The user lives in Muscat.",
        evidence="User stated their location directly.",
        confidence=0.9,
        memory_type=MemoryType.FACT,
        classification_confidence=0.85,
        classification_reason="Identity fact.",
        importance_score=0.7,
        importance_reason="Durable personal fact.",
        decision=KnowledgeDecision.NEW,
        knowledge_object_id=uuid4(),
    )


def make_write_trace(**overrides) -> WriteTrace:
    defaults = dict(
        schema_version=CURRENT_WRITE_TRACE_SCHEMA_VERSION,
        pipeline_version=1,
        extractor_prompt_version=1,
        trace_id=uuid4(),
        conversation_id=uuid4(),
        source=SourceType.MANUAL,
        external_key="/c/abc",
        mode="first_run",
        checkpoint=CheckpointStageTrace(
            mode="first_run",
            had_existing_checkpoint=False,
            turn_count=1,
            new_turn_start_index=0,
            transcript_hash="hash-1",
        ),
        working_contexts=None,
        extractor=ExtractorStageTrace(
            prompt="extract this", raw_response="[]", fact_count=1
        ),
        facts=(make_fact_trace(0),),
        vault_paths=("vault/fact1.md",),
        ontology=OntologyStageTrace(
            validation_results=(
                OntologyProposalTrace(
                    proposal_type="create_concept", accepted=True
                ),
            ),
            concept_paths=("concepts/muscat.md",),
        ),
        status="success",
        knowledge_object_ids=(uuid4(),),
        stage_timings_ms={"total": 12.5},
        created_at=datetime(2026, 7, 5, 12, 0, 0),
    )
    defaults.update(overrides)
    return WriteTrace(**defaults)


# ---------------------------------------------------------------------------
# TestRoundTrip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_write_trace_round_trips(self) -> None:
        trace = make_write_trace()
        restored = WriteTrace.from_dict(trace.to_dict())

        assert restored.trace_id == trace.trace_id
        assert restored.conversation_id == trace.conversation_id
        assert restored.mode == trace.mode
        assert restored.status == trace.status
        assert restored.knowledge_object_ids == trace.knowledge_object_ids
        assert restored.stage_timings_ms == trace.stage_timings_ms
        assert restored.checkpoint == trace.checkpoint
        assert restored.extractor == trace.extractor
        assert restored.facts == trace.facts
        assert restored.ontology == trace.ontology

    def test_fact_trace_round_trips(self) -> None:
        fact = make_fact_trace(2)
        restored = FactTrace.from_dict(fact.to_dict())
        assert restored == fact

    def test_fact_trace_with_supersession_round_trips(self) -> None:
        matched_id = uuid4()
        fact = FactTrace(
            fact_index=0,
            fact_text="I work at Google as a staff engineer",
            evidence="stated",
            confidence=0.9,
            memory_type=MemoryType.FACT,
            decision=KnowledgeDecision.UPDATE,
            knowledge_object_id=matched_id,
            supersession_operation=SupersessionOperation.UPDATE,
            supersession_matched_identity=matched_id,
            supersession_reason="Conservative in-place refinement; previous: 'I work at Google'",
        )
        restored = FactTrace.from_dict(fact.to_dict())
        assert restored == fact
        assert restored.supersession_operation == SupersessionOperation.UPDATE
        assert restored.supersession_matched_identity == matched_id
        assert "I work at Google" in restored.supersession_reason

    def test_ontology_stage_trace_round_trips_rejected_proposal(self) -> None:
        stage = OntologyStageTrace(
            validation_results=(
                OntologyProposalTrace(
                    proposal_type="create_concept",
                    accepted=False,
                    rejection_reason="duplicate concept",
                ),
            ),
            concept_paths=(),
        )
        restored = OntologyStageTrace.from_dict(stage.to_dict())
        assert restored == stage
        assert restored.validation_results[0].accepted is False
        assert restored.validation_results[0].rejection_reason == "duplicate concept"

    def test_working_contexts_are_plain_dicts_not_reparsed(self) -> None:
        # WriteTrace deliberately stores working_contexts as opaque
        # already-projected dicts (see module docstring) -- no dataclass
        # hydration should happen for this field.
        trace = make_write_trace(
            working_contexts=[{"title": "Haven", "current_goal": "Ship PR5"}]
        )
        restored = WriteTrace.from_dict(trace.to_dict())
        assert restored.working_contexts == [
            {"title": "Haven", "current_goal": "Ship PR5"}
        ]


# ---------------------------------------------------------------------------
# TestDefaults
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_extractor_none_when_duplicate_short_circuit(self) -> None:
        trace = make_write_trace(mode="duplicate", status="duplicate", extractor=None, facts=())
        restored = WriteTrace.from_dict(trace.to_dict())
        assert restored.extractor is None
        assert restored.facts == ()

    def test_missing_optional_dict_keys_hydrate_to_defaults(self) -> None:
        minimal = {
            "trace_id": str(uuid4()),
        }
        restored = WriteTrace.from_dict(minimal)
        assert restored.conversation_id is None
        assert restored.source is None
        assert restored.external_key is None
        assert restored.mode == "first_run"
        assert restored.working_contexts is None
        assert restored.extractor is None
        assert restored.facts == ()
        assert restored.vault_paths == ()
        assert restored.status == "success"
        assert restored.knowledge_object_ids == ()
        assert restored.stage_timings_ms == {}
        assert restored.checkpoint.decision_counts == {}

    def test_fact_trace_missing_optional_fields(self) -> None:
        restored = FactTrace.from_dict(
            {"fact_index": 0, "fact_text": "x", "evidence": "y", "confidence": 0.5}
        )
        assert restored.memory_type is None
        assert restored.decision is None
        assert restored.knowledge_object_id is None
        assert restored.supersession_operation is None
        assert restored.supersession_matched_identity is None
        assert restored.supersession_reason is None


# ---------------------------------------------------------------------------
# TestVersionMetadata
# ---------------------------------------------------------------------------


class TestVersionMetadata:
    def test_version_fields_round_trip(self) -> None:
        trace = make_write_trace(pipeline_version=3, extractor_prompt_version=7)
        restored = WriteTrace.from_dict(trace.to_dict())
        assert restored.schema_version == CURRENT_WRITE_TRACE_SCHEMA_VERSION
        assert restored.pipeline_version == 3
        assert restored.extractor_prompt_version == 7

    def test_missing_version_fields_default_without_raising(self) -> None:
        restored = WriteTrace.from_dict({"trace_id": str(uuid4())})
        assert restored.schema_version == CURRENT_WRITE_TRACE_SCHEMA_VERSION
        assert restored.pipeline_version == 0
        assert restored.extractor_prompt_version == 0
