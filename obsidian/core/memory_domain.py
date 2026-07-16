"""Fixed ``MemoryType`` -> ``MemoryDomain`` grouping table.

A deliberate, minimal exception to this package's "no business logic"
rule (see ``obsidian/core/__init__.py``'s module docstring): this table is
a static classification, not behavior -- the same category of fixed
lookup table as ``EntityType``/``MemoryType`` themselves, just factored
out of ``enums.py`` because it is a mapping between two enums rather than
an enum declaration. It follows the same "fixed, total, public" pattern
already established by
:data:`obsidian.memory_engine.coverage_analyzer.MEMORY_TYPE_CATEGORY` and
:data:`obsidian.ontology.retrieval_models._MEMORY_TYPE_ROLE`.

Domain assignment carries no retrieval, ranking, or acceptance meaning --
see those two modules for MemoryType's other, unrelated groupings. It
exists solely so the dashboard (and any future UI) can present memories in
a small number of grouped sections instead of one control per
``MemoryType``.
"""

from __future__ import annotations

from typing import Dict

from obsidian.core.enums import MemoryDomain, MemoryType

#: Fixed, total ``MemoryType`` -> ``MemoryDomain`` mapping. Every
#: ``MemoryType`` member has exactly one entry -- see
#: :func:`obsidian.tests.test_memory_domain` (totality test) for the
#: enforcement of that invariant.
MEMORY_TYPE_DOMAIN: Dict[MemoryType, MemoryDomain] = {
    # Personal -- durable signal about who the user is and what they want.
    MemoryType.PREFERENCE: MemoryDomain.PERSONAL,
    MemoryType.INTEREST: MemoryDomain.PERSONAL,
    MemoryType.TRAIT: MemoryDomain.PERSONAL,
    MemoryType.HABIT: MemoryDomain.PERSONAL,
    MemoryType.SKILL: MemoryDomain.PERSONAL,
    MemoryType.GOAL: MemoryDomain.PERSONAL,
    # Work -- project-state tracking: what's happening and what's next.
    MemoryType.PROJECT: MemoryDomain.WORK,
    MemoryType.TASK: MemoryDomain.WORK,
    MemoryType.DECISION: MemoryDomain.WORK,
    MemoryType.OPEN_QUESTION: MemoryDomain.WORK,
    MemoryType.BLOCKER: MemoryDomain.WORK,
    MemoryType.IMPLEMENTATION_STATE: MemoryDomain.WORK,
    MemoryType.CODE_AREA: MemoryDomain.WORK,
    # Knowledge -- durable knowledge the user holds or has recorded.
    MemoryType.FACT: MemoryDomain.KNOWLEDGE,
    MemoryType.BELIEF: MemoryDomain.KNOWLEDGE,
    MemoryType.PERSON: MemoryDomain.KNOWLEDGE,
    MemoryType.EVENT: MemoryDomain.KNOWLEDGE,
    MemoryType.RULE: MemoryDomain.KNOWLEDGE,
}


def resolve_domain(memory_type: MemoryType) -> MemoryDomain:
    """Return the :class:`MemoryDomain` *memory_type* belongs to.

    Total over every current ``MemoryType`` member -- see
    :data:`MEMORY_TYPE_DOMAIN`. Falls back to :attr:`MemoryDomain.KNOWLEDGE`
    for a hypothetical future ``MemoryType`` member added without a table
    entry, so this function never raises; the totality test is what keeps
    that fallback from silently masking a missed table update.
    """
    return MEMORY_TYPE_DOMAIN.get(memory_type, MemoryDomain.KNOWLEDGE)
