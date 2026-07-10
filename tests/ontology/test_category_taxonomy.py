"""Unit tests for the curated ENTITY_CAT -> CATEGORY taxonomy.

Coverage targets
----------------
* Structural sanity of CATEGORY_TAXONOMY itself (no duplicate normalised
  instance keys, every category label/alias is a non-empty string).
* lookup_category() is case-/whitespace-insensitive and returns None for
  unknown labels.
* Every curated category label documented in
  docs/architecture/ENTITY_CAT_INVESTIGATION.md Task 5 is present with the
  expected instance set.
* The generic words the investigation explicitly excluded ("system",
  "component", "deployment approach") never appear as a taxonomy key,
  category label, or alias.
"""

from __future__ import annotations

from collections import defaultdict

from obsidian.ontology.category_taxonomy import CATEGORY_TAXONOMY, lookup_category
from obsidian.ontology.text_utils import normalize


# ---------------------------------------------------------------------------
# Structural sanity
# ---------------------------------------------------------------------------


class TestCategoryTaxonomyWellFormed:
    def test_no_duplicate_normalised_instance_keys(self):
        seen = set()
        for instance_label in CATEGORY_TAXONOMY:
            key = normalize(instance_label)
            assert key not in seen, f"duplicate normalised instance key: {key!r}"
            seen.add(key)

    def test_every_category_label_non_empty(self):
        for instance_label, entry in CATEGORY_TAXONOMY.items():
            assert entry.label.strip(), f"empty category label for {instance_label!r}"

    def test_every_alias_non_empty_lowercase_single_token(self):
        for instance_label, entry in CATEGORY_TAXONOMY.items():
            for alias in entry.aliases:
                assert alias.strip(), f"empty alias for {instance_label!r}"
                assert alias == alias.lower(), f"alias {alias!r} not lowercase"
                assert " " not in alias, (
                    f"alias {alias!r} for {instance_label!r} contains whitespace — "
                    "QueryResolver's token pass only looks up single tokens"
                )

    def test_no_instance_label_is_also_a_category_label(self):
        instance_keys = {normalize(k) for k in CATEGORY_TAXONOMY}
        category_labels = {normalize(e.label) for e in CATEGORY_TAXONOMY.values()}
        assert not instance_keys & category_labels


# ---------------------------------------------------------------------------
# lookup_category()
# ---------------------------------------------------------------------------


class TestLookupCategory:
    def test_exact_match(self):
        entry = lookup_category("PostgreSQL")
        assert entry is not None
        assert entry.label == "Database"

    def test_case_insensitive(self):
        assert lookup_category("postgresql") == lookup_category("PostgreSQL")
        assert lookup_category("POSTGRESQL") == lookup_category("PostgreSQL")

    def test_whitespace_insensitive(self):
        assert lookup_category("  PostgreSQL  ") == lookup_category("PostgreSQL")

    def test_multi_word_instance_label(self):
        entry = lookup_category("GitHub Actions")
        assert entry is not None
        assert entry.label == "CI/CD Provider"

    def test_unknown_label_returns_none(self):
        assert lookup_category("Haven") is None
        assert lookup_category("Claude") is None
        assert lookup_category("MongoDB") is None  # deliberately not curated (see supersession_basic_033)

    def test_empty_string_returns_none(self):
        assert lookup_category("") is None


# ---------------------------------------------------------------------------
# Coverage of the curated taxonomy against the investigation's Task 5 scope
# ---------------------------------------------------------------------------


class TestInvestigationScope:
    EXPECTED_CATEGORIES = {
        "Laptop": {"ThinkPad"},
        "Eating Plan": {"Mediterranean"},
        "CI/CD Provider": {"GitHub Actions"},
        "Note-Taking App": {"Obsidian"},
        "Background Job System": {"Celery"},
        "Model": {"GPT", "Qwen"},
        "Database": {"PostgreSQL"},
        "Frontend Framework": {"Svelte"},
        "Tracking Tool": {"Linear", "Jira"},
    }

    def test_every_expected_category_present_with_exact_instance_set(self):
        by_category = defaultdict(set)
        for instance_label, entry in CATEGORY_TAXONOMY.items():
            by_category[entry.label].add(instance_label)
        assert dict(by_category) == self.EXPECTED_CATEGORIES

    def test_excluded_generic_nouns_never_appear(self):
        # docs/architecture/ENTITY_CAT_INVESTIGATION.md Task 4/5: these are
        # deliberately excluded as too generic to alias safely.
        forbidden = {"system", "component", "deployment approach"}
        all_labels = {normalize(k) for k in CATEGORY_TAXONOMY}
        all_labels |= {normalize(e.label) for e in CATEGORY_TAXONOMY.values()}
        all_aliases = {a for e in CATEGORY_TAXONOMY.values() for a in e.aliases}
        assert not (all_labels & forbidden)
        assert not (all_aliases & forbidden)

    def test_idiosyncratic_entities_never_curated(self):
        # Acme (company) and Northbeam (startup name) are explicitly out of
        # scope for this curated global taxonomy — see category_taxonomy.py
        # module docstring and ENTITY_CAT_INVESTIGATION.md Task 5.
        assert lookup_category("Acme") is None
        assert lookup_category("Northbeam") is None
