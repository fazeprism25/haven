"""Deterministic conversation fixtures for the incremental-ingestion benchmarks.

Facts are embedded as machine-readable markers (see
:mod:`benchmarks.incremental_ingestion.fake_llm`) rather than natural
sentences a real LLM would have to parse -- these benchmarks measure how
much of a conversation (and which background context) reaches the
Extractor, not language understanding.
"""

from __future__ import annotations

from typing import List, Tuple

Turn = Tuple[str, str]  # (role, content)


def fact_turn(fact_id: str, text: str) -> Turn:
    return ("user", f"FACT[{fact_id}]: {text}")


def conditional_fact_turn(needle: str, text: str) -> Turn:
    return ("user", f"FACTIF[{needle}]: {text}")


def filler_turn(text: str = "Got it, thanks!") -> Turn:
    return ("assistant", text)


def plain_turn(text: str) -> Turn:
    return ("user", text)


# ---------------------------------------------------------------------------
# Category 1: Duplicate Remember
# ---------------------------------------------------------------------------


def duplicate_remember_conversation() -> List[Turn]:
    """A small, fixed conversation to resend unchanged, repeatedly."""
    return [
        fact_turn("goal", "The user's goal is to apply to MIT."),
        filler_turn("That's a great goal! What's your plan?"),
    ]


# ---------------------------------------------------------------------------
# Category 2: Growing Conversation
# ---------------------------------------------------------------------------


def base_conversation(n_facts: int = 5) -> List[Turn]:
    """A conversation with *n_facts* self-contained facts, one per turn pair."""
    turns: List[Turn] = []
    for i in range(n_facts):
        turns.append(fact_turn(f"base{i}", f"The user's base fact {i} is value_{i}."))
        turns.append(filler_turn())
    return turns


def grown_conversation(base_facts: int, growth_facts: int) -> List[Turn]:
    """*base_conversation(base_facts)* plus *growth_facts* more self-contained facts."""
    turns = base_conversation(base_facts)
    for i in range(growth_facts):
        turns.append(fact_turn(f"grow{i}", f"The user's new fact {i} is grown_value_{i}."))
        turns.append(filler_turn())
    return turns


# ---------------------------------------------------------------------------
# Category 3: Long Conversation
# ---------------------------------------------------------------------------


def long_conversation(total_turns: int) -> List[Turn]:
    """A long conversation with one self-contained fact roughly every 4 turns."""
    turns: List[Turn] = []
    fact_index = 0
    for i in range(total_turns):
        if i % 4 == 0:
            turns.append(
                fact_turn(f"long{fact_index}", f"The user's long-conversation fact {fact_index} is value_{fact_index}.")
            )
            fact_index += 1
        elif i % 4 == 1:
            turns.append(filler_turn())
        else:
            turns.append(plain_turn(f"Some unrelated filler message number {i}."))
    return turns[:total_turns]


def click_boundaries(total_turns: int, step: int = 10) -> List[int]:
    """Turn-count checkpoints for periodic "Remember" clicks up to *total_turns*.

    E.g. ``click_boundaries(25, step=10) == [10, 20, 25]``.
    """
    boundaries = list(range(step, total_turns, step))
    if not boundaries or boundaries[-1] != total_turns:
        boundaries.append(total_turns)
    return boundaries


# ---------------------------------------------------------------------------
# Category 4: Context-dependent updates
# ---------------------------------------------------------------------------


def keyword_anchored_update_conversation() -> List[Turn]:
    """"I'm building Haven" ... "I'm no longer using Python" / "switched to Rust".

    The referent ("Python") stays close enough to the update turn that
    it falls inside the incremental pipeline's anchor window, so the
    Working Context retrieval query it builds still contains the
    keyword "Python" -- a favourable case for the new pipeline.
    """
    return [
        fact_turn("tool", "The user is building Haven."),
        filler_turn("Nice! Tell me more."),
        fact_turn("lang", "The user uses Python."),
        filler_turn("Python's a solid choice."),
        plain_turn("Actually let's benchmark first."),
        filler_turn("Sure, let's do that."),
        conditional_fact_turn("Python", "The user no longer uses their previous programming language."),
        fact_turn("lang2", "The user switched to Rust."),
    ]


def keyword_anchored_update_click_boundaries() -> List[int]:
    return [4, 6, 8]


def keyword_orphaned_update_conversation(filler_count: int = 8) -> List[Turn]:
    """Same shape as the anchored scenario, but with enough neutral filler
    turns between the "uses Python" fact and the later update that the
    referent falls outside the incremental pipeline's fixed-size anchor
    window, and no filler turn shares a keyword with it either -- an
    unfavourable case for the new pipeline, included specifically to let
    the benchmark surface (not hide) a real limitation if one exists.
    """
    turns: List[Turn] = [
        fact_turn("tool", "The user is building Haven."),
        filler_turn("Nice! Tell me more."),
        fact_turn("lang", "The user uses Python."),
        filler_turn("Noted."),
    ]
    for i in range(filler_count):
        turns.append(plain_turn(f"Let's talk about something unrelated, item {i}."))
        turns.append(filler_turn("Sure, sounds good."))
    turns.append(
        conditional_fact_turn("Python", "The user no longer uses their previous programming language.")
    )
    turns.append(fact_turn("lang2", "The user switched to Rust."))
    return turns


def keyword_orphaned_update_click_boundaries(filler_count: int = 8) -> List[int]:
    # First click covers the "uses Python" fact; second (final) click
    # covers everything, including the update pair.
    first_click = 4
    total = 4 + 2 * filler_count + 2
    return [first_click, total]


# ---------------------------------------------------------------------------
# Category 5: Failure cases
# ---------------------------------------------------------------------------


def base_pair_conversation() -> List[Turn]:
    return [
        fact_turn("goal", "The user's goal is to apply to MIT."),
        filler_turn("That's a great goal! What's your plan?"),
    ]


def edited_earlier_turn_conversation() -> List[Turn]:
    """Same shape as ``base_pair_conversation`` but turn 0's content changed."""
    return [
        fact_turn("goal", "The user's goal is to apply to Stanford instead."),
        filler_turn("That's a great goal! What's your plan?"),
    ]


def with_deleted_turn(turns: List[Turn]) -> List[Turn]:
    """Return *turns* with its first turn removed."""
    return turns[1:]


def reordered(turns: List[Turn]) -> List[Turn]:
    """Return *turns* with the first two entries swapped."""
    if len(turns) < 2:
        return list(turns)
    swapped = list(turns)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    return swapped
