"""Benchmark harness: drives the real ``POST /memory`` endpoint for both
the old (no ``external_key``) and new (checkpoint + incremental
ingestion) code paths, recording :class:`RequestMetrics` for each call.

Nothing here reimplements or bypasses any part of the pipeline. Every
metric is observed by *wrapping* (never replacing) the real collaborators
``save_memory`` already constructs:

* ``MemoryEngine.query_working_context`` -- timed, to measure Working
  Context retrieval duration.
* ``CheckpointStore.load`` / ``CheckpointWriter.write`` -- timed, to
  measure checkpoint bookkeeping overhead.
* The installed ``ManagerPipeline`` -- wrapped (not subclassed) to record
  how many facts/knowledge objects a call produced, since
  ``SaveMemoryResponse`` only ever reports the first one.

Each wrapper unconditionally delegates to the real implementation; none
of them change behaviour, only add timing/counting.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi.testclient import TestClient

from obsidian.checkpoint.identity import derive_conversation_id
from obsidian.checkpoint.store import CheckpointStore
from obsidian.checkpoint.writer import CheckpointWriter
from obsidian.core.enums import SourceType
from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.pipeline import ManagerPipeline
from obsidian.memory_engine.engine import MemoryEngine
from obsidian.memory_engine.memory_store import MemoryStore

from benchmarks.incremental_ingestion.fake_llm import MarkerLLM
from benchmarks.incremental_ingestion.metrics import RequestMetrics

Turn = Tuple[str, str]


# ---------------------------------------------------------------------------
# Process-wide instrumentation (installed once, additive-only)
# ---------------------------------------------------------------------------


class _Instrumentation:
    def __init__(self) -> None:
        self.working_context_durations: List[float] = []
        self.checkpoint_load_durations: List[float] = []
        self.checkpoint_write_durations: List[float] = []
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        self._installed = True

        original_query = MemoryEngine.query_working_context

        def timed_query(engine_self, raw_query):  # type: ignore[no-untyped-def]
            start = time.perf_counter()
            result = original_query(engine_self, raw_query)
            self.working_context_durations.append(time.perf_counter() - start)
            return result

        MemoryEngine.query_working_context = timed_query  # type: ignore[method-assign]

        original_load = CheckpointStore.load

        def timed_load(store_self):  # type: ignore[no-untyped-def]
            start = time.perf_counter()
            result = original_load(store_self)
            self.checkpoint_load_durations.append(time.perf_counter() - start)
            return result

        CheckpointStore.load = timed_load  # type: ignore[method-assign]

        original_write = CheckpointWriter.write

        def timed_write(writer_self, checkpoint):  # type: ignore[no-untyped-def]
            start = time.perf_counter()
            result = original_write(writer_self, checkpoint)
            self.checkpoint_write_durations.append(time.perf_counter() - start)
            return result

        CheckpointWriter.write = timed_write  # type: ignore[method-assign]

    def reset(self) -> None:
        self.working_context_durations.clear()
        self.checkpoint_load_durations.clear()
        self.checkpoint_write_durations.clear()


_instrumentation = _Instrumentation()
_instrumentation.install()


class _InstrumentedPipeline:
    """Wraps a real ``ManagerPipeline`` to record per-call fact/KO counts."""

    def __init__(self, pipeline: ManagerPipeline) -> None:
        self._pipeline = pipeline
        self.last_facts_count = 0
        self.last_knowledge_count = 0

    def process(self, conversation, existing_knowledge=None, existing_context=None):  # type: ignore[no-untyped-def]
        decisions = self._pipeline.process(
            conversation, existing_knowledge, existing_context=existing_context
        )
        self.last_facts_count = len(decisions)
        self.last_knowledge_count = sum(1 for d in decisions if d.knowledge is not None)
        return decisions


# ---------------------------------------------------------------------------
# Benchmark client
# ---------------------------------------------------------------------------


class BenchmarkClient:
    """One isolated Haven instance (fresh vault/concept/checkpoint directories)."""

    def __init__(self, llm: Optional[MarkerLLM] = None) -> None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="haven_incremental_benchmark_"))
        os.environ["HAVEN_VAULT_DIR"] = str(tmp_dir / "vault")
        os.environ["HAVEN_CONCEPT_DIR"] = str(tmp_dir / "concepts")
        os.environ["HAVEN_CHECKPOINT_DIR"] = str(tmp_dir / "checkpoints")

        # Imported after the env vars are set so lifespan() picks them up.
        from obsidian.server.main import app

        self.client = TestClient(app)
        self.client.__enter__()
        self.llm = llm if llm is not None else MarkerLLM()
        self._install_llm()

    def _install_llm(self) -> None:
        self.client.app.state.manager_pipeline = _InstrumentedPipeline(
            ManagerPipeline(
                extractor=Extractor(llm=self.llm),
                classifier=Classifier(llm=self.llm),
                importance_scorer=ImportanceScorer(llm=self.llm),
                canonical_matcher=CanonicalMatcher(),
                knowledge_updater=KnowledgeUpdater(),
            )
        )

    def vault_object_count(self) -> int:
        store = MemoryStore(self.client.app.state.vault_dir)
        store.load()
        return store.count()

    def vault_facts(self) -> List[str]:
        store = MemoryStore(self.client.app.state.vault_dir)
        store.load()
        return sorted(ko.canonical_fact for ko in store.all())

    def checkpoint_mode(self, source: SourceType, external_key: str) -> Optional[str]:
        store = CheckpointStore(self.client.app.state.checkpoint_dir)
        store.load()
        conversation_id = derive_conversation_id(source, external_key)
        if not store.has(conversation_id):
            return None
        checkpoint = store.get(conversation_id)
        if not checkpoint.processing_history:
            return None
        return checkpoint.processing_history[-1].mode

    def send(
        self,
        label: str,
        pipeline: str,
        turns: List[Turn],
        external_key: Optional[str] = None,
        source: SourceType = SourceType.MANUAL,
    ) -> RequestMetrics:
        """POST one "Remember" click for *turns* and record its metrics."""
        prev_llm_calls = self.llm.call_count
        prev_prompt_count = len(self.llm.extractor_prompts)
        prev_object_count = self.vault_object_count()
        _instrumentation.reset()

        payload: dict = {
            "conversation": [{"role": role, "content": content} for role, content in turns]
        }
        if external_key is not None:
            payload["external_key"] = external_key
            payload["source"] = source.value

        # obsidian.server.main.save_memory/extractor.py carry temporary
        # [Haven][pipeline-debug] print statements (unrelated to this
        # benchmark suite); silenced here purely so hundreds of turns
        # don't flood benchmark output, without touching that code.
        start = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            response = self.client.post("/api/v1/memory", json=payload)
        elapsed = time.perf_counter() - start

        extractor_prompts_this_call = self.llm.extractor_prompts[prev_prompt_count:]
        prompt_text = extractor_prompts_this_call[-1] if extractor_prompts_this_call else ""

        try:
            body = response.json()
        except ValueError:
            body = {}

        checkpoint_mode = (
            self.checkpoint_mode(source, external_key) if external_key is not None else None
        )
        pipeline_obj = self.client.app.state.manager_pipeline

        return RequestMetrics(
            label=label,
            pipeline=pipeline,
            turn_count_sent=len(turns),
            status_code=response.status_code,
            response_status=body.get("status"),
            checkpoint_mode=checkpoint_mode,
            elapsed_seconds=elapsed,
            llm_calls=self.llm.call_count - prev_llm_calls,
            extractor_prompt_chars=len(prompt_text),
            extractor_prompt_words=len(prompt_text.split()),
            extractor_prompt_tokens_est=round(len(prompt_text) / 4),
            facts_extracted=(
                pipeline_obj.last_facts_count if extractor_prompts_this_call else 0
            ),
            knowledge_objects_created=self.vault_object_count() - prev_object_count,
            working_context_queried=bool(_instrumentation.working_context_durations),
            working_context_seconds=(
                _instrumentation.working_context_durations[-1]
                if _instrumentation.working_context_durations
                else None
            ),
            checkpoint_overhead_seconds=(
                sum(_instrumentation.checkpoint_load_durations)
                + sum(_instrumentation.checkpoint_write_durations)
            ),
            vault_object_count_after=self.vault_object_count(),
        )

    def close(self) -> None:
        self.client.__exit__(None, None, None)

    def __enter__(self) -> "BenchmarkClient":
        return self

    def __exit__(self, *exc_info) -> None:  # type: ignore[no-untyped-def]
        self.close()
