"""The five benchmark categories, each comparing the old (no
``external_key``) pipeline against the new (checkpoint + incremental
ingestion) pipeline on the same fixture conversations.

Every scenario function returns ``List[ScenarioResult]`` and is
self-contained: it constructs its own :class:`BenchmarkClient` instance(s)
(one per pipeline being compared, so their vaults never interfere) and
closes them before returning.
"""

from __future__ import annotations

from typing import List
from unittest.mock import patch

from obsidian.core.enums import SourceType
from obsidian.memory_engine.engine import MemoryEngine

from benchmarks.incremental_ingestion import fixtures as fx
from benchmarks.incremental_ingestion.fake_llm import MarkerLLM
from benchmarks.incremental_ingestion.harness import BenchmarkClient
from benchmarks.incremental_ingestion.metrics import AccuracyComparison, RequestMetrics, ScenarioResult

_KEY = "/c/benchmark"


# ---------------------------------------------------------------------------
# Category 1: Duplicate Remember
# ---------------------------------------------------------------------------


def duplicate_remember_scenarios(repeats: int = 5) -> List[ScenarioResult]:
    turns = fx.duplicate_remember_conversation()
    result = ScenarioResult(
        scenario_id="duplicate_remember",
        category="1_duplicate_remember",
        description=(
            f"The same {len(turns)}-turn conversation resent {repeats} times "
            "unchanged, comparing checkpoint short-circuiting (new) against "
            "unconditional full reprocessing (old)."
        ),
    )

    with BenchmarkClient() as old_client:
        for i in range(repeats):
            result.requests.append(
                old_client.send(f"old_send_{i + 1}_of_{repeats}", "old_full", turns)
            )

    with BenchmarkClient() as new_client:
        for i in range(repeats):
            result.requests.append(
                new_client.send(
                    f"new_send_{i + 1}_of_{repeats}",
                    "new_incremental",
                    turns,
                    external_key=_KEY,
                )
            )

    old_calls = sum(r.llm_calls for r in result.requests if r.pipeline == "old_full")
    new_calls = sum(r.llm_calls for r in result.requests if r.pipeline == "new_incremental")
    checkpoint_hits = sum(
        1
        for r in result.requests
        if r.pipeline == "new_incremental" and r.response_status == "duplicate"
    )
    result.notes.append(
        f"old_full: {old_calls} total LLM calls across {repeats} sends "
        f"(every send reprocesses). new_incremental: {new_calls} total LLM "
        f"calls, {checkpoint_hits} of {repeats - 1} possible repeat sends "
        "short-circuited as checkpoint hits."
    )
    return [result]


# ---------------------------------------------------------------------------
# Category 2: Growing Conversation
# ---------------------------------------------------------------------------


def growing_conversation_scenarios(
    base_facts: int = 5, increments: List[int] = (1, 2, 5, 10)
) -> List[ScenarioResult]:
    results: List[ScenarioResult] = []
    for growth in increments:
        base_turns = fx.base_conversation(base_facts)
        grown_turns = fx.grown_conversation(base_facts, growth)
        scenario = ScenarioResult(
            scenario_id=f"growing_conversation_plus_{growth}",
            category="2_growing_conversation",
            description=(
                f"A {len(base_turns)}-turn base conversation ({base_facts} facts) "
                f"grows by {growth} new fact turn(s); comparing what each "
                "pipeline sends the Extractor for the growth click."
            ),
        )

        with BenchmarkClient() as old_client:
            scenario.requests.append(old_client.send("old_setup", "old_full", base_turns))
            scenario.requests.append(old_client.send("old_growth", "old_full", grown_turns))

        with BenchmarkClient() as new_client:
            scenario.requests.append(
                new_client.send("new_setup", "new_incremental", base_turns, external_key=_KEY)
            )
            scenario.requests.append(
                new_client.send(
                    "new_growth", "new_incremental", grown_turns, external_key=_KEY
                )
            )

        old_growth = next(r for r in scenario.requests if r.label == "old_growth")
        new_growth = next(r for r in scenario.requests if r.label == "new_growth")
        scenario.notes.append(
            f"Growth click: old sent {old_growth.turn_count_sent} turns / "
            f"{old_growth.extractor_prompt_chars} chars to the Extractor; "
            f"new sent (incrementally) {new_growth.extractor_prompt_chars} chars, "
            f"mode={new_growth.checkpoint_mode}."
        )
        results.append(scenario)
    return results


# ---------------------------------------------------------------------------
# Category 3: Long Conversation
# ---------------------------------------------------------------------------


def long_conversation_scenarios(
    sizes: List[int] = (25, 50, 100, 200, 500), step: int = 10
) -> List[ScenarioResult]:
    results: List[ScenarioResult] = []
    for total_turns in sizes:
        turns = fx.long_conversation(total_turns)
        boundaries = fx.click_boundaries(total_turns, step=step)
        scenario = ScenarioResult(
            scenario_id=f"long_conversation_{total_turns}",
            category="3_long_conversation",
            description=(
                f"A {total_turns}-turn conversation, remembered every {step} "
                f"turns ({len(boundaries)} clicks), comparing prompt size / "
                "latency / retrieval overhead scaling."
            ),
        )

        with BenchmarkClient() as old_client:
            for i, boundary in enumerate(boundaries):
                scenario.requests.append(
                    old_client.send(
                        f"old_click_{i + 1}_of_{len(boundaries)}_turns_{boundary}",
                        "old_full",
                        turns[:boundary],
                    )
                )

        with BenchmarkClient() as new_client:
            for i, boundary in enumerate(boundaries):
                scenario.requests.append(
                    new_client.send(
                        f"new_click_{i + 1}_of_{len(boundaries)}_turns_{boundary}",
                        "new_incremental",
                        turns[:boundary],
                        external_key=_KEY,
                    )
                )

        old_final = [r for r in scenario.requests if r.pipeline == "old_full"][-1]
        new_final = [r for r in scenario.requests if r.pipeline == "new_incremental"][-1]
        scenario.notes.append(
            f"Final click at {total_turns} turns: old prompt "
            f"{old_final.extractor_prompt_chars} chars "
            f"(~{old_final.extractor_prompt_tokens_est} tokens est.), "
            f"{old_final.elapsed_seconds:.4f}s; new prompt "
            f"{new_final.extractor_prompt_chars} chars "
            f"(~{new_final.extractor_prompt_tokens_est} tokens est.), "
            f"{new_final.elapsed_seconds:.4f}s, mode={new_final.checkpoint_mode}, "
            f"working_context_seconds={new_final.working_context_seconds}, "
            f"checkpoint_overhead_seconds={new_final.checkpoint_overhead_seconds:.5f}."
        )
        results.append(scenario)
    return results


# ---------------------------------------------------------------------------
# Category 4: Context-dependent updates
# ---------------------------------------------------------------------------


def _run_context_dependent_scenario(
    scenario_id: str,
    description: str,
    turns: List[fx.Turn],
    boundaries: List[int],
    memory_type: str,
) -> ScenarioResult:
    scenario = ScenarioResult(scenario_id=scenario_id, category="4_context_dependent_updates", description=description)

    old_llm = MarkerLLM(default_memory_type=memory_type)
    with BenchmarkClient(llm=old_llm) as old_client:
        for i, boundary in enumerate(boundaries):
            scenario.requests.append(
                old_client.send(
                    f"old_click_{i + 1}_of_{len(boundaries)}", "old_full", turns[:boundary]
                )
            )
        old_facts = old_client.vault_facts()

    new_llm = MarkerLLM(default_memory_type=memory_type)
    with BenchmarkClient(llm=new_llm) as new_client:
        for i, boundary in enumerate(boundaries):
            scenario.requests.append(
                new_client.send(
                    f"new_click_{i + 1}_of_{len(boundaries)}",
                    "new_incremental",
                    turns[:boundary],
                    external_key=_KEY,
                )
            )
        new_facts = new_client.vault_facts()

    scenario.accuracy = AccuracyComparison.compare(old_facts, new_facts)
    if not scenario.accuracy.match:
        scenario.notes.append(
            "MISMATCH: new_incremental did not extract the same facts as "
            f"old_full. Missing in new: {scenario.accuracy.missing_in_new}. "
            f"Extra in new: {scenario.accuracy.extra_in_new}."
        )
    else:
        scenario.notes.append("MATCH: both pipelines saved the identical fact set.")
    return scenario


def context_dependent_update_scenarios() -> List[ScenarioResult]:
    results: List[ScenarioResult] = []

    for memory_type in ("fact", "decision"):
        results.append(
            _run_context_dependent_scenario(
                scenario_id=f"context_update_anchored_{memory_type}",
                description=(
                    "\"I'm building Haven\" ... \"no longer uses Python\" / "
                    "\"switched to Rust\", with the referent inside the "
                    f"incremental pipeline's anchor window. Referent fact "
                    f"classified as memory_type={memory_type!r}."
                ),
                turns=fx.keyword_anchored_update_conversation(),
                boundaries=fx.keyword_anchored_update_click_boundaries(),
                memory_type=memory_type,
            )
        )

    for memory_type in ("fact", "decision"):
        results.append(
            _run_context_dependent_scenario(
                scenario_id=f"context_update_orphaned_{memory_type}",
                description=(
                    "Same shape, but the referent falls outside the anchor "
                    "window and shares no keywords with anything nearby. "
                    f"Referent fact classified as memory_type={memory_type!r}."
                ),
                turns=fx.keyword_orphaned_update_conversation(),
                boundaries=fx.keyword_orphaned_update_click_boundaries(),
                memory_type=memory_type,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Category 5: Failure cases
# ---------------------------------------------------------------------------


def failure_case_scenarios() -> List[ScenarioResult]:
    results: List[ScenarioResult] = []

    # -- edited earlier turn --------------------------------------------
    scenario = ScenarioResult(
        scenario_id="failure_edited_earlier_turn",
        category="5_failure_cases",
        description="Turn 0's content changes between two sends of the same conversation_id.",
    )
    with BenchmarkClient() as client:
        scenario.requests.append(
            client.send("click_1_original", "new_incremental", fx.base_pair_conversation(), external_key=_KEY)
        )
        scenario.requests.append(
            client.send(
                "click_2_edited",
                "new_incremental",
                fx.edited_earlier_turn_conversation(),
                external_key=_KEY,
            )
        )
    last = scenario.requests[-1]
    scenario.notes.append(
        f"mode={last.checkpoint_mode} (expected 'fallback'); "
        f"status={last.status_code}/{last.response_status} (expected 200/success, no crash)."
    )
    results.append(scenario)

    # -- deleted turn -----------------------------------------------------
    scenario = ScenarioResult(
        scenario_id="failure_deleted_turn",
        category="5_failure_cases",
        description="An earlier turn is removed entirely between two sends.",
    )
    base = fx.base_conversation(3)
    with BenchmarkClient() as client:
        scenario.requests.append(client.send("click_1_full", "new_incremental", base, external_key=_KEY))
        scenario.requests.append(
            client.send(
                "click_2_deleted_turn",
                "new_incremental",
                fx.with_deleted_turn(base),
                external_key=_KEY,
            )
        )
    last = scenario.requests[-1]
    scenario.notes.append(
        f"mode={last.checkpoint_mode} (expected 'fallback'); "
        f"status={last.status_code}/{last.response_status} (expected 200/success, no crash)."
    )
    results.append(scenario)

    # -- reordered turns ----------------------------------------------------
    scenario = ScenarioResult(
        scenario_id="failure_reordered_turns",
        category="5_failure_cases",
        description="The first two turns swap order between two sends.",
    )
    base = fx.base_conversation(3)
    with BenchmarkClient() as client:
        scenario.requests.append(client.send("click_1_full", "new_incremental", base, external_key=_KEY))
        scenario.requests.append(
            client.send(
                "click_2_reordered", "new_incremental", fx.reordered(base), external_key=_KEY
            )
        )
    last = scenario.requests[-1]
    scenario.notes.append(
        f"mode={last.checkpoint_mode} (expected 'fallback'); "
        f"status={last.status_code}/{last.response_status} (expected 200/success, no crash)."
    )
    results.append(scenario)

    # -- empty Working Context (first incremental click into an unrelated topic) --
    scenario = ScenarioResult(
        scenario_id="failure_empty_working_context",
        category="5_failure_cases",
        description=(
            "An incremental click whose new turns share no keywords with "
            "anything in the vault -- Working Context retrieval runs but "
            "has nothing relevant to surface."
        ),
    )
    with BenchmarkClient() as client:
        first = fx.base_conversation(1)
        scenario.requests.append(client.send("click_1_setup", "new_incremental", first, external_key=_KEY))
        grown = first + [
            fx.plain_turn("Completely unrelated small talk about the weather today."),
            fx.filler_turn("Sure, it's nice out."),
        ]
        scenario.requests.append(
            client.send("click_2_incremental", "new_incremental", grown, external_key=_KEY)
        )
        last_prompt = client.llm.extractor_prompts[-1] if client.llm.extractor_prompts else ""
    last = scenario.requests[-1]
    scenario.notes.append(
        f"checkpoint_mode={last.checkpoint_mode} -- this still reads click 1's "
        "*persisted* checkpoint (mode='first_run'), not click 2's: a 422 never "
        "writes a checkpoint (documented PR 3 behaviour), even though click 2 "
        "was correctly classified as 'incremental' in memory and its evidence "
        "was correctly sliced to just the 2 new turns (see "
        f"extractor_prompt_chars={last.extractor_prompt_chars} vs. the 4-turn "
        "prompt this would have been without slicing). "
        f"working_context_queried={last.working_context_queried} (expected True); "
        f"'EXISTING CONTEXT' in prompt={'EXISTING CONTEXT' in last_prompt} "
        "(expected False -- nothing relevant to surface, section omitted); "
        f"status={last.status_code}/{last.response_status} (expected 422/None -- "
        "filler-only new evidence extracts nothing, a pre-existing, unrelated "
        "contract from PR 3, not a PR 4 regression)."
    )
    results.append(scenario)

    # -- Working Context retrieval failure -----------------------------------
    scenario = ScenarioResult(
        scenario_id="failure_working_context_retrieval_error",
        category="5_failure_cases",
        description=(
            "MemoryEngine.query_working_context raises during an incremental "
            "click -- verifying the documented best-effort fallback to "
            "existing_context=None rather than a failed request."
        ),
    )
    with BenchmarkClient() as client:
        base = fx.base_conversation(2)
        scenario.requests.append(client.send("click_1_setup", "new_incremental", base, external_key=_KEY))
        grown = fx.grown_conversation(2, 1)
        with patch.object(
            MemoryEngine,
            "query_working_context",
            side_effect=RuntimeError("simulated Working Context outage"),
        ):
            scenario.requests.append(
                client.send(
                    "click_2_retrieval_fails", "new_incremental", grown, external_key=_KEY
                )
            )
    last = scenario.requests[-1]
    scenario.notes.append(
        f"mode={last.checkpoint_mode} (expected 'incremental'); "
        f"status={last.status_code}/{last.response_status} (expected 200/success -- "
        "the save must still succeed on the new evidence alone); "
        f"knowledge_objects_created={last.knowledge_objects_created} (expected >= 1)."
    )
    results.append(scenario)

    return results


def all_scenarios() -> List[ScenarioResult]:
    scenarios: List[ScenarioResult] = []
    scenarios += duplicate_remember_scenarios()
    scenarios += growing_conversation_scenarios()
    scenarios += long_conversation_scenarios()
    scenarios += context_dependent_update_scenarios()
    scenarios += failure_case_scenarios()
    return scenarios
