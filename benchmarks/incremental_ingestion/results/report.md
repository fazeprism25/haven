# Incremental Ingestion Benchmark Report

Generated at: 2026-07-05T14:49:58.765194+00:00
Git commit: 042e16e66f5d28605ea3770b7c9080b55ec678ce
Total scenarios: 19

See `README.md` in this directory for full methodology, and `results/results.json` for the raw per-request data behind every number below.

## 1. Duplicate Remember

**duplicate_remember** -- The same 2-turn conversation resent 5 times unchanged, comparing checkpoint short-circuiting (new) against unconditional full reprocessing (old).

| pipeline | send | status | llm_calls | elapsed_s |
|---|---|---|---|---|
| old_full | old_send_1_of_5 | success | 3 | 0.0150 |
| old_full | old_send_2_of_5 | success | 3 | 0.0070 |
| old_full | old_send_3_of_5 | success | 3 | 0.0055 |
| old_full | old_send_4_of_5 | success | 3 | 0.0056 |
| old_full | old_send_5_of_5 | success | 3 | 0.0057 |
| new_incremental | new_send_1_of_5 | success | 3 | 0.0092 |
| new_incremental | new_send_2_of_5 | duplicate | 0 | 0.0038 |
| new_incremental | new_send_3_of_5 | duplicate | 0 | 0.0036 |
| new_incremental | new_send_4_of_5 | duplicate | 0 | 0.0030 |
| new_incremental | new_send_5_of_5 | duplicate | 0 | 0.0040 |

> old_full: 15 total LLM calls across 5 sends (every send reprocesses). new_incremental: 3 total LLM calls, 4 of 4 possible repeat sends short-circuited as checkpoint hits.

## 2. Growing Conversation

| scenario | pipeline | growth click turns | growth click chars | growth click tokens (est.) | llm_calls |
|---|---|---|---|---|---|
| growing_conversation_plus_1 | old_full | 12 | 3080 | 770 | 13 |
| growing_conversation_plus_1 | new_incremental | 12 | 2665 | 666 | 3 |
| growing_conversation_plus_2 | old_full | 14 | 3168 | 792 | 15 |
| growing_conversation_plus_2 | new_incremental | 14 | 2753 | 688 | 5 |
| growing_conversation_plus_5 | old_full | 20 | 3432 | 858 | 21 |
| growing_conversation_plus_5 | new_incremental | 20 | 3017 | 754 | 11 |
| growing_conversation_plus_10 | old_full | 30 | 3872 | 968 | 31 |
| growing_conversation_plus_10 | new_incremental | 30 | 3457 | 864 | 21 |

> [growing_conversation_plus_1] Growth click: old sent 12 turns / 3080 chars to the Extractor; new sent (incrementally) 2665 chars, mode=incremental.
> [growing_conversation_plus_2] Growth click: old sent 14 turns / 3168 chars to the Extractor; new sent (incrementally) 2753 chars, mode=incremental.
> [growing_conversation_plus_5] Growth click: old sent 20 turns / 3432 chars to the Extractor; new sent (incrementally) 3017 chars, mode=incremental.
> [growing_conversation_plus_10] Growth click: old sent 30 turns / 3872 chars to the Extractor; new sent (incrementally) 3457 chars, mode=incremental.

## 3. Long Conversation

| total turns | pipeline | final click chars | final click tokens (est.) | elapsed_s | working_context_s | checkpoint_overhead_s |
|---|---|---|---|---|---|---|
| 25 | old_full | 3793 | 948 | 0.0178 | - | 0 |
| 25 | new_incremental | 2837 | 709 | 0.0150 | 0.0024 | 0.0087 |
| 50 | old_full | 4982 | 1246 | 0.0304 | - | 0 |
| 50 | new_incremental | 3066 | 766 | 0.0306 | 0.0116 | 0.0104 |
| 100 | old_full | 7418 | 1854 | 0.0590 | - | 0 |
| 100 | new_incremental | 3063 | 766 | 0.0550 | 0.0253 | 0.0097 |
| 200 | old_full | 12343 | 3086 | 0.1143 | - | 0 |
| 200 | new_incremental | 3069 | 767 | 0.1095 | 0.0566 | 0.0106 |
| 500 | old_full | 27193 | 6798 | 0.2849 | - | 0 |
| 500 | new_incremental | 3075 | 769 | 0.2606 | 0.1382 | 0.0107 |

> [long_conversation_25] Final click at 25 turns: old prompt 3793 chars (~948 tokens est.), 0.0178s; new prompt 2837 chars (~709 tokens est.), 0.0150s, mode=incremental, working_context_seconds=0.0024383999407291412, checkpoint_overhead_seconds=0.00874.
> [long_conversation_50] Final click at 50 turns: old prompt 4982 chars (~1246 tokens est.), 0.0304s; new prompt 3066 chars (~766 tokens est.), 0.0306s, mode=incremental, working_context_seconds=0.01162940007634461, checkpoint_overhead_seconds=0.01042.
> [long_conversation_100] Final click at 100 turns: old prompt 7418 chars (~1854 tokens est.), 0.0590s; new prompt 3063 chars (~766 tokens est.), 0.0550s, mode=incremental, working_context_seconds=0.025315299979411066, checkpoint_overhead_seconds=0.00965.
> [long_conversation_200] Final click at 200 turns: old prompt 12343 chars (~3086 tokens est.), 0.1143s; new prompt 3069 chars (~767 tokens est.), 0.1095s, mode=incremental, working_context_seconds=0.056632000021636486, checkpoint_overhead_seconds=0.01064.
> [long_conversation_500] Final click at 500 turns: old prompt 27193 chars (~6798 tokens est.), 0.2849s; new prompt 3075 chars (~769 tokens est.), 0.2606s, mode=incremental, working_context_seconds=0.1381977000273764, checkpoint_overhead_seconds=0.01074.

## 4. Context-dependent updates

| scenario | match | missing_in_new | extra_in_new |
|---|---|---|---|
| context_update_anchored_fact | False | ['The user no longer uses their previous programming language.'] | [] |
| context_update_anchored_decision | True | [] | [] |
| context_update_orphaned_fact | False | ['The user no longer uses their previous programming language.'] | [] |
| context_update_orphaned_decision | True | [] | [] |

**context_update_anchored_fact** -- "I'm building Haven" ... "no longer uses Python" / "switched to Rust", with the referent inside the incremental pipeline's anchor window. Referent fact classified as memory_type='fact'.
> MISMATCH: new_incremental did not extract the same facts as old_full. Missing in new: ['The user no longer uses their previous programming language.']. Extra in new: [].

**context_update_anchored_decision** -- "I'm building Haven" ... "no longer uses Python" / "switched to Rust", with the referent inside the incremental pipeline's anchor window. Referent fact classified as memory_type='decision'.
> MATCH: both pipelines saved the identical fact set.

**context_update_orphaned_fact** -- Same shape, but the referent falls outside the anchor window and shares no keywords with anything nearby. Referent fact classified as memory_type='fact'.
> MISMATCH: new_incremental did not extract the same facts as old_full. Missing in new: ['The user no longer uses their previous programming language.']. Extra in new: [].

**context_update_orphaned_decision** -- Same shape, but the referent falls outside the anchor window and shares no keywords with anything nearby. Referent fact classified as memory_type='decision'.
> MATCH: both pipelines saved the identical fact set.

## 5. Failure cases

**failure_edited_earlier_turn** -- Turn 0's content changes between two sends of the same conversation_id.
> mode=fallback (expected 'fallback'); status=200/success (expected 200/success, no crash).

**failure_deleted_turn** -- An earlier turn is removed entirely between two sends.
> mode=fallback (expected 'fallback'); status=200/success (expected 200/success, no crash).

**failure_reordered_turns** -- The first two turns swap order between two sends.
> mode=fallback (expected 'fallback'); status=200/success (expected 200/success, no crash).

**failure_empty_working_context** -- An incremental click whose new turns share no keywords with anything in the vault -- Working Context retrieval runs but has nothing relevant to surface.
> checkpoint_mode=first_run -- this still reads click 1's *persisted* checkpoint (mode='first_run'), not click 2's: a 422 never writes a checkpoint (documented PR 3 behaviour), even though click 2 was correctly classified as 'incremental' in memory and its evidence was correctly sliced to just the 2 new turns (see extractor_prompt_chars=2674 vs. the 4-turn prompt this would have been without slicing). working_context_queried=True (expected True); 'EXISTING CONTEXT' in prompt=False (expected False -- nothing relevant to surface, section omitted); status=422/None (expected 422/None -- filler-only new evidence extracts nothing, a pre-existing, unrelated contract from PR 3, not a PR 4 regression).

**failure_working_context_retrieval_error** -- MemoryEngine.query_working_context raises during an incremental click -- verifying the documented best-effort fallback to existing_context=None rather than a failed request.
> mode=incremental (expected 'incremental'); status=200/success (expected 200/success -- the save must still succeed on the new evidence alone); knowledge_objects_created=1 (expected >= 1).
