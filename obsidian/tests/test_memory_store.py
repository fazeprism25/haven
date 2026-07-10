"""Unit tests for obsidian.memory_engine.memory_store.MemoryStore.

Test groups
-----------
TestInstantiation           — construction, empty initial state.
TestEmptyVault               — missing dir, path-is-file, empty dir.
TestSingleMemory              — one KnowledgeObject round-trips through the vault.
TestMultipleMemories          — several KnowledgeObjects, correct count/isolation.
TestDeterministicLoading      — filename-sorted parse order; stable across reloads.
TestMalformedMarkdown         — bad YAML, missing fence, missing id, invalid enum.
TestDuplicateIds              — two files resolving to the same KnowledgeObject id.
TestAtomicLoad                — a failed reload leaves the previous cache intact.
TestQueries                   — get/has/all/count, including unknown-id behaviour.
TestEvidenceChainLimitation   — evidence_chain is not persisted by VaultWriter,
                                 so hydration always yields an empty chain.
TestNonMarkdownFiles          — .txt/.json files are silently ignored.
TestNoConceptImports          — module never imports the Ontology subsystem.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from obsidian.core.enums import MemoryType
from obsidian.core.errors import MemoryEngineError
from obsidian.manager_ai.models import EvidenceEntry, KnowledgeObject
from obsidian.memory_engine.memory_store import MemoryStore
from obsidian.memory_engine.vault_writer import VaultWriter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXED_TIME = datetime(2026, 1, 1, 12, 0, 0)


def make_ko(
    fact: str = "Haven uses Claude",
    memory_type: MemoryType = MemoryType.FACT,
    confidence: float = 0.9,
    importance: float = 0.7,
    ko_id: UUID | None = None,
    evidence_chain: list[EvidenceEntry] | None = None,
) -> KnowledgeObject:
    return KnowledgeObject(
        id=ko_id if ko_id is not None else uuid4(),
        canonical_fact=fact,
        memory_type=memory_type,
        confidence=confidence,
        importance=importance,
        evidence_chain=evidence_chain or [],
        valid_from=_FIXED_TIME,
    )


def write(vault_dir: Path, knowledge: KnowledgeObject) -> Path:
    return VaultWriter(vault_dir).write(knowledge)


# ---------------------------------------------------------------------------
# TestInstantiation
# ---------------------------------------------------------------------------


class TestInstantiation:
    def test_instantiates_with_vault_dir(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        assert store is not None

    def test_empty_before_load(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        assert store.count() == 0
        assert store.all() == []

    def test_accepts_path_like_string(self, tmp_path: Path) -> None:
        store = MemoryStore(str(tmp_path))
        store.load()
        assert store.count() == 0


# ---------------------------------------------------------------------------
# TestEmptyVault
# ---------------------------------------------------------------------------


class TestEmptyVault:
    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path / "does_not_exist")
        with pytest.raises(MemoryEngineError, match="not found"):
            store.load()

    def test_path_is_file_raises(self, tmp_path: Path) -> None:
        file_path = tmp_path / "not_a_dir.md"
        file_path.write_text("hello", encoding="utf-8")
        store = MemoryStore(file_path)
        with pytest.raises(MemoryEngineError, match="not a directory"):
            store.load()

    def test_empty_directory_loads_zero(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        store.load()
        assert store.count() == 0
        assert store.all() == []


# ---------------------------------------------------------------------------
# TestSingleMemory
# ---------------------------------------------------------------------------


class TestSingleMemory:
    def test_hydrates_matching_fields(self, tmp_path: Path) -> None:
        ko = make_ko(fact="Haven uses Claude", confidence=0.8, importance=0.6)
        write(tmp_path, ko)

        store = MemoryStore(tmp_path)
        store.load()

        assert store.count() == 1
        hydrated = store.get(ko.id)
        assert hydrated.id == ko.id
        assert hydrated.canonical_fact == "Haven uses Claude"
        assert hydrated.memory_type == MemoryType.FACT
        assert hydrated.confidence == 0.8
        assert hydrated.importance == 0.6
        assert hydrated.valid_from == _FIXED_TIME

    def test_preserves_metadata_dict(self, tmp_path: Path) -> None:
        ko = KnowledgeObject(
            id=uuid4(),
            canonical_fact="fact",
            valid_from=_FIXED_TIME,
            metadata={"source": "test"},
        )
        write(tmp_path, ko)

        store = MemoryStore(tmp_path)
        store.load()

        assert store.get(ko.id).metadata == {"source": "test"}


# ---------------------------------------------------------------------------
# TestMultipleMemories
# ---------------------------------------------------------------------------


class TestMultipleMemories:
    def test_loads_all_files(self, tmp_path: Path) -> None:
        kos = [make_ko(fact=f"fact {i}") for i in range(5)]
        for ko in kos:
            write(tmp_path, ko)

        store = MemoryStore(tmp_path)
        store.load()

        assert store.count() == 5
        loaded_ids = {ko.id for ko in store.all()}
        assert loaded_ids == {ko.id for ko in kos}

    def test_objects_are_isolated(self, tmp_path: Path) -> None:
        ko_a = make_ko(fact="fact A", confidence=0.1)
        ko_b = make_ko(fact="fact B", confidence=0.9)
        write(tmp_path, ko_a)
        write(tmp_path, ko_b)

        store = MemoryStore(tmp_path)
        store.load()

        assert store.get(ko_a.id).confidence == 0.1
        assert store.get(ko_b.id).confidence == 0.9


# ---------------------------------------------------------------------------
# TestDeterministicLoading
# ---------------------------------------------------------------------------


class TestDeterministicLoading:
    def test_reload_is_stable(self, tmp_path: Path) -> None:
        for i in range(4):
            write(tmp_path, make_ko(fact=f"fact {i}"))

        store = MemoryStore(tmp_path)
        store.load()
        first = [ko.id for ko in store.all()]

        store.load()
        second = [ko.id for ko in store.all()]

        assert first == second

    def test_all_returns_sorted_by_id(self, tmp_path: Path) -> None:
        kos = [make_ko(fact=f"fact {i}") for i in range(6)]
        for ko in kos:
            write(tmp_path, ko)

        store = MemoryStore(tmp_path)
        store.load()

        ids = [ko.id for ko in store.all()]
        assert ids == sorted(ids, key=str)

    def test_two_independent_stores_agree(self, tmp_path: Path) -> None:
        for i in range(3):
            write(tmp_path, make_ko(fact=f"fact {i}"))

        store_a = MemoryStore(tmp_path)
        store_a.load()
        store_b = MemoryStore(tmp_path)
        store_b.load()

        assert [ko.id for ko in store_a.all()] == [ko.id for ko in store_b.all()]


# ---------------------------------------------------------------------------
# TestMalformedMarkdown
# ---------------------------------------------------------------------------


class TestMalformedMarkdown:
    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.md"
        bad.write_text("---\nid: [unterminated\n---\nbody\n", encoding="utf-8")

        store = MemoryStore(tmp_path)
        with pytest.raises(MemoryEngineError, match="Failed to parse"):
            store.load()

    def test_missing_closing_fence_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.md"
        bad.write_text("---\nid: abc\nbody without closing fence\n", encoding="utf-8")

        store = MemoryStore(tmp_path)
        with pytest.raises(MemoryEngineError, match="Failed to parse"):
            store.load()

    def test_missing_id_field_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.md"
        bad.write_text(
            "---\ncanonical_fact: 'no id here'\nmemory_type: fact\n---\nbody\n",
            encoding="utf-8",
        )

        store = MemoryStore(tmp_path)
        with pytest.raises(MemoryEngineError, match="missing required frontmatter field 'id'"):
            store.load()

    def test_invalid_memory_type_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.md"
        bad.write_text(
            f"---\nid: '{uuid4()}'\ncanonical_fact: 'fact'\nmemory_type: not_a_real_type\n---\nbody\n",
            encoding="utf-8",
        )

        store = MemoryStore(tmp_path)
        with pytest.raises(MemoryEngineError, match="Failed to hydrate"):
            store.load()

    def test_invalid_id_field_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.md"
        bad.write_text(
            "---\nid: 'not-a-uuid'\ncanonical_fact: 'fact'\n---\nbody\n",
            encoding="utf-8",
        )

        store = MemoryStore(tmp_path)
        with pytest.raises(MemoryEngineError, match="Failed to hydrate"):
            store.load()


# ---------------------------------------------------------------------------
# TestDuplicateIds
# ---------------------------------------------------------------------------


class TestDuplicateIds:
    def test_duplicate_id_across_files_raises(self, tmp_path: Path) -> None:
        ko = make_ko(fact="original")
        path_a = write(tmp_path, ko)
        # Copy the same content to a second, differently-named file.
        path_b = tmp_path / "duplicate_copy.md"
        path_b.write_text(path_a.read_text(encoding="utf-8"), encoding="utf-8")

        store = MemoryStore(tmp_path)
        with pytest.raises(MemoryEngineError, match="Duplicate KnowledgeObject id"):
            store.load()


# ---------------------------------------------------------------------------
# TestAtomicLoad
# ---------------------------------------------------------------------------


class TestAtomicLoad:
    def test_failed_reload_preserves_previous_cache(self, tmp_path: Path) -> None:
        ko = make_ko(fact="good")
        write(tmp_path, ko)

        store = MemoryStore(tmp_path)
        store.load()
        assert store.count() == 1

        # Introduce a malformed file and reload; the load should fail...
        (tmp_path / "bad.md").write_text("---\nid: [unterminated\n---\n", encoding="utf-8")
        with pytest.raises(MemoryEngineError):
            store.load()

        # ...and the previously cached object must still be queryable.
        assert store.count() == 1
        assert store.get(ko.id).canonical_fact == "good"


# ---------------------------------------------------------------------------
# TestQueries
# ---------------------------------------------------------------------------


class TestQueries:
    def test_has_true_for_loaded_id(self, tmp_path: Path) -> None:
        ko = make_ko()
        write(tmp_path, ko)
        store = MemoryStore(tmp_path)
        store.load()
        assert store.has(ko.id) is True

    def test_has_false_for_unknown_id(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        store.load()
        assert store.has(uuid4()) is False

    def test_get_unknown_id_raises_key_error(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        store.load()
        with pytest.raises(KeyError):
            store.get(uuid4())

    def test_all_returns_list_of_knowledge_objects(self, tmp_path: Path) -> None:
        write(tmp_path, make_ko())
        store = MemoryStore(tmp_path)
        store.load()
        result = store.all()
        assert isinstance(result, list)
        assert all(isinstance(ko, KnowledgeObject) for ko in result)


# ---------------------------------------------------------------------------
# TestAllCaching
#
# Performance-audit regression tests. all() previously re-sorted every
# cached KnowledgeObject on every call. It now sorts once per load() and
# caches the result, since a single request can legitimately call all()
# more than once against the same snapshot (HybridCandidateRetriever's
# keyword path, the CONTINUATION category-fallback path, and
# obsidian.server.dashboard's own `memories = memory_store.all()` all do,
# within one request). These tests confirm the cache is transparent: same
# content every time, a fresh list object each call (so a caller mutating
# its own copy can't corrupt the cache), and correctly invalidated by the
# next load().
# ---------------------------------------------------------------------------


class TestAllCaching:
    def test_repeated_calls_return_equal_content(self, tmp_path: Path) -> None:
        for _ in range(5):
            write(tmp_path, make_ko())
        store = MemoryStore(tmp_path)
        store.load()

        first = store.all()
        second = store.all()
        assert first == second
        assert [ko.id for ko in first] == [ko.id for ko in second]

    def test_repeated_calls_return_distinct_list_objects(self, tmp_path: Path) -> None:
        write(tmp_path, make_ko())
        store = MemoryStore(tmp_path)
        store.load()

        first = store.all()
        second = store.all()
        assert first is not second

    def test_mutating_returned_list_does_not_affect_next_call(
        self, tmp_path: Path
    ) -> None:
        write(tmp_path, make_ko())
        write(tmp_path, make_ko())
        store = MemoryStore(tmp_path)
        store.load()

        first = store.all()
        first.clear()

        second = store.all()
        assert len(second) == 2

    def test_reload_with_new_memory_is_reflected_in_all(self, tmp_path: Path) -> None:
        write(tmp_path, make_ko())
        store = MemoryStore(tmp_path)
        store.load()
        assert len(store.all()) == 1

        write(tmp_path, make_ko())
        store.load()
        assert len(store.all()) == 2

    def test_reload_with_fewer_files_shrinks_all(self, tmp_path: Path) -> None:
        path_a = write(tmp_path, make_ko())
        write(tmp_path, make_ko())
        store = MemoryStore(tmp_path)
        store.load()
        assert len(store.all()) == 2

        path_a.unlink()
        store.load()
        assert len(store.all()) == 1

    def test_all_before_any_load_returns_empty_list(self, tmp_path: Path) -> None:
        store = MemoryStore(tmp_path)
        assert store.all() == []


# ---------------------------------------------------------------------------
# TestEvidenceChainLimitation
# ---------------------------------------------------------------------------


class TestEvidenceChainLimitation:
    def test_evidence_chain_not_round_tripped(self, tmp_path: Path) -> None:
        ko = make_ko(
            evidence_chain=[EvidenceEntry(evidence="saw it in chat", confidence=0.9)]
        )
        write(tmp_path, ko)

        store = MemoryStore(tmp_path)
        store.load()

        # VaultWriter does not persist evidence_chain into frontmatter, so
        # hydration cannot recover it. This documents that known gap.
        assert store.get(ko.id).evidence_chain == []


# ---------------------------------------------------------------------------
# TestNonMarkdownFiles
# ---------------------------------------------------------------------------


class TestNonMarkdownFiles:
    def test_non_markdown_files_ignored(self, tmp_path: Path) -> None:
        write(tmp_path, make_ko())
        (tmp_path / "notes.txt").write_text("not a memory", encoding="utf-8")
        (tmp_path / "config.json").write_text("{}", encoding="utf-8")

        store = MemoryStore(tmp_path)
        store.load()

        assert store.count() == 1


# ---------------------------------------------------------------------------
# TestNoConceptImports
# ---------------------------------------------------------------------------


class TestNoConceptImports:
    def test_module_does_not_import_ontology(self) -> None:
        import obsidian.memory_engine.memory_store as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "import obsidian.ontology" not in source
        assert "from obsidian.ontology" not in source
