"""Tests for the V2 ontology's ``MemoryType`` -> ``MemoryDomain`` mapping.

Test groups
-----------
TestTotality       -- MEMORY_TYPE_DOMAIN covers every MemoryType member
                       exactly once, so a future type can't silently go
                       unbucketed.
TestExactGrouping   -- the specific domain each type resolves to matches
                       the user-approved grouping (see
                       obsidian/core/memory_domain.py).
TestResolveDomain   -- resolve_domain() is a pure, deterministic function
                       of its input.
"""

from __future__ import annotations

from obsidian.core.enums import MemoryDomain, MemoryType
from obsidian.core.memory_domain import MEMORY_TYPE_DOMAIN, resolve_domain


class TestTotality:
    def test_every_memory_type_has_exactly_one_domain(self) -> None:
        assert set(MEMORY_TYPE_DOMAIN.keys()) == set(MemoryType)

    def test_every_value_is_a_valid_domain(self) -> None:
        for domain in MEMORY_TYPE_DOMAIN.values():
            assert isinstance(domain, MemoryDomain)


class TestExactGrouping:
    def test_personal_domain(self) -> None:
        expected = {
            MemoryType.PREFERENCE,
            MemoryType.INTEREST,
            MemoryType.TRAIT,
            MemoryType.HABIT,
            MemoryType.SKILL,
            MemoryType.GOAL,
        }
        actual = {t for t, d in MEMORY_TYPE_DOMAIN.items() if d == MemoryDomain.PERSONAL}
        assert actual == expected

    def test_work_domain(self) -> None:
        expected = {
            MemoryType.PROJECT,
            MemoryType.TASK,
            MemoryType.DECISION,
            MemoryType.OPEN_QUESTION,
            MemoryType.BLOCKER,
            MemoryType.IMPLEMENTATION_STATE,
            MemoryType.CODE_AREA,
        }
        actual = {t for t, d in MEMORY_TYPE_DOMAIN.items() if d == MemoryDomain.WORK}
        assert actual == expected

    def test_knowledge_domain(self) -> None:
        expected = {
            MemoryType.FACT,
            MemoryType.BELIEF,
            MemoryType.PERSON,
            MemoryType.EVENT,
            MemoryType.RULE,
        }
        actual = {t for t, d in MEMORY_TYPE_DOMAIN.items() if d == MemoryDomain.KNOWLEDGE}
        assert actual == expected


class TestResolveDomain:
    def test_matches_table_for_every_type(self) -> None:
        for memory_type, domain in MEMORY_TYPE_DOMAIN.items():
            assert resolve_domain(memory_type) == domain

    def test_is_deterministic(self) -> None:
        assert resolve_domain(MemoryType.INTEREST) == resolve_domain(MemoryType.INTEREST)
