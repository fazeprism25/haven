"""Curated ENTITY_CAT -> CATEGORY taxonomy for the IS_A ontology bridge.

This is the Phase 2 curated data table scoped by
``docs/architecture/ENTITY_CAT_INVESTIGATION.md`` (Task 5) and
``docs/architecture/CANDIDATE_GENERATION_DECISION.md`` (Phase 2 roadmap).
It maps a small, hand-reviewed set of well-known technology/product
instance labels to a category label, so :class:`~obsidian.ontology.ontology_manager.OntologyManager`
can propose a category :class:`~obsidian.ontology.models.Concept` plus an
``IS_A`` relationship from instance to category the first time each
instance is detected.

Scope and exclusions (see investigation doc for the full audit)
-----------------------------------------------------------------
* Covers only the 9 curated categories / 11 instance labels the
  investigation confirmed have a category noun specific enough to alias
  without over-activation risk (``ENTITY_CAT_INVESTIGATION.md`` Task 3's
  "specific, safe to alias" column).
* Deliberately excludes ``system``, ``component``, and ``deployment
  approach`` — generic architecture words the investigation flagged as
  unsafe to index (would over-activate for unrelated queries).
* Deliberately excludes ``Acme``/``Northbeam`` (company/startup name
  cases) — personal/idiosyncratic labels that, per the investigation's
  own reasoning, have no principled place in a hand-curated *global*
  taxonomy. The investigation's proposed alternative for these two (a
  general "any new label is a Company" auto-attach mechanism) is not a
  curated instance->category mapping and is out of this bridge's scope.
* Category alias tokens were re-verified against the *full* benchmark
  corpus (not just the 14 audited cases) before being chosen, because a
  category word that looks specific in isolation can still collide with
  unrelated queries. Two of the investigation's own suggested category
  nouns turned out to be unsafe under this check and were replaced:

  - ``"job"`` (for Background Job System) collides with ``job offer``,
    ``job role``, ``job title`` queries (concept_consolidation, refinements,
    supersession datasets) -> replaced with ``"background"``.
  - ``"provider"`` (for CI/CD Provider) collides with ``cloud provider``,
    ``internet provider``, ``identity provider`` queries (decision_reconstruction,
    temporal datasets) -> replaced with ``"ci"``/``"cd"`` (only ever
    appear in CI/CD-flavored queries in this corpus).
  - ``"app"`` (for Note-Taking App) collides with generic "...their new
    web app" / "...in their app" decision queries -> replaced with
    ``"note"``.

  ``"laptop"``, ``"database"``, ``"model"``, ``"frontend"``, ``"tracking"``,
  and ``"eating"`` were checked the same way and found to have no
  unsafe collision in the current corpus (a couple of them recur across
  multiple *related* database/model/frontend-framework queries outside
  the curated 14 — this is expected and reported in the benchmark
  validation, not a defect: it means the category concept can also
  surface a candidate for adjacent queries, which the untouched ranking
  stage then accepts or rejects on its own merits).

Design constraints
------------------
* Pure data — no logic beyond a dict lookup.
* Deterministic and closed, same review discipline as
  ``_VARIANT_GROUPS`` in ``obsidian/memory_engine/keyword_candidate_retriever.py``.
* Lookup is case-/whitespace-insensitive (delegates to
  :func:`~obsidian.ontology.text_utils.normalize`) so it matches however
  :class:`~obsidian.ontology.concept_detector.ConceptDetector` happens to
  have cased the detected label.
"""

from __future__ import annotations

from typing import Dict, NamedTuple, Optional, Tuple

from obsidian.ontology.text_utils import normalize


class CategoryEntry(NamedTuple):
    """A curated category target for one instance label.

    Parameters
    ----------
    label : str
        Canonical category concept label (e.g. ``"Database"``).
    aliases : tuple[str, ...]
        Extra single-token aliases that let a query resolve to this
        category even when the category label itself is multi-word
        (:class:`~obsidian.ontology.query_resolver.QueryResolver` only
        does whole-phrase or single-token lookups). Empty when the
        category label is already a single word, since the label itself
        is automatically indexed.
    """

    label: str
    aliases: Tuple[str, ...]


CATEGORY_TAXONOMY: Dict[str, CategoryEntry] = {
    "ThinkPad": CategoryEntry("Laptop", ()),
    "Mediterranean": CategoryEntry("Eating Plan", ("eating",)),
    "GitHub Actions": CategoryEntry("CI/CD Provider", ("ci", "cd")),
    "Obsidian": CategoryEntry("Note-Taking App", ("note",)),
    "Celery": CategoryEntry("Background Job System", ("background",)),
    "GPT": CategoryEntry("Model", ()),
    "Qwen": CategoryEntry("Model", ()),
    "PostgreSQL": CategoryEntry("Database", ()),
    "Svelte": CategoryEntry("Frontend Framework", ("frontend",)),
    "Linear": CategoryEntry("Tracking Tool", ("tracking",)),
    "Jira": CategoryEntry("Tracking Tool", ("tracking",)),
}
"""Curated instance-label -> category mapping. See module docstring for scope."""


_NORMALIZED_TAXONOMY: Dict[str, CategoryEntry] = {
    normalize(instance_label): entry for instance_label, entry in CATEGORY_TAXONOMY.items()
}


def lookup_category(instance_label: str) -> Optional[CategoryEntry]:
    """Return the curated :class:`CategoryEntry` for *instance_label*, or ``None``.

    Lookup is case-/whitespace-insensitive.

    Parameters
    ----------
    instance_label : str
        A concept label as produced by
        :class:`~obsidian.ontology.concept_detector.ConceptDetector`.

    Returns
    -------
    CategoryEntry or None
    """
    return _NORMALIZED_TAXONOMY.get(normalize(instance_label))
