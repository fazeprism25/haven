"""Shared demo-seeding logic for ``scripts/seed_demo.py`` and the dashboard's
"Import Demo Data"/"Reset Demo" actions (``POST /api/v1/dev/seed_demo``,
``POST /api/v1/dev/reset_demo`` in ``obsidian/server/main.py``).

One implementation, two callers with different execution contexts:

* ``scripts/seed_demo.py`` drives this against a fresh, standalone app
  instance (its own ``TestClient``, its own env vars) for CLI/dev use --
  always the ``haven_data/`` directories.
* ``obsidian.server.main``'s dev endpoints drive this against the
  *already-running* server's own ``app.state``, so seeding always targets
  whichever vault is currently active (the default, or one selected via
  ``POST /api/v1/vault``).

Both callers get the exact same parsing and the exact same deterministic,
marker-based fake LLM -- no API key needed either way. See
``demo/demo_memories.md`` and ``demo/demo_conversations.md`` for the data
itself.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from obsidian.core.enums import MemoryType
from obsidian.manager_ai.canonical_matcher import CanonicalMatcher
from obsidian.manager_ai.classifier import Classifier
from obsidian.manager_ai.extractor import Extractor
from obsidian.manager_ai.importance import ImportanceScorer
from obsidian.manager_ai.knowledge_updater import KnowledgeUpdater
from obsidian.manager_ai.models import KnowledgeObject
from obsidian.manager_ai.pipeline import ManagerPipeline
from obsidian.memory_engine.vault_writer import VaultWriter
from obsidian.ontology.ontology_pipeline import OntologyPipeline

from benchmarks.incremental_ingestion.fake_llm import MarkerLLM

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_MEMORIES_FILE = REPO_ROOT / "demo" / "demo_memories.md"
DEMO_CONVERSATIONS_FILE = REPO_ROOT / "demo" / "demo_conversations.md"

# A second bundled dataset for a demo Memory Space named anything containing
# "personal"/"research" (see dataset_for_space below) -- lets "Import demo
# data" tell a genuinely different story in a second space instead of
# duplicating Haven's own, so switching Memory Spaces visibly changes every
# Dashboard section.
DEMO_MEMORIES_FILE_PERSONAL = REPO_ROOT / "demo" / "demo_memories_personal.md"
DEMO_CONVERSATIONS_FILE_PERSONAL = REPO_ROOT / "demo" / "demo_conversations_personal.md"

_PERSONAL_SPACE_KEYWORDS = ("personal", "research")


def dataset_for_space(space_name: Optional[str]) -> Tuple[Path, Path]:
    """Pick which bundled (memories, conversations) file pair to seed for *space_name*.

    Matches "personal"/"research" (case-insensitive) anywhere in the active
    Memory Space's own name -- e.g. a space named "Personal AI Research" --
    since that's the only per-space signal ``POST /api/v1/dev/seed_demo``
    already has on hand (``app.state.active_space_name``, set by
    ``obsidian.server.main._activate_space``). Any other name, or no name at
    all (an env-managed deployment with Memory Spaces off), falls back to the
    default Haven dataset -- the same dataset every existing caller already
    gets today.
    """
    name = (space_name or "").lower()
    if any(keyword in name for keyword in _PERSONAL_SPACE_KEYWORDS):
        return DEMO_MEMORIES_FILE_PERSONAL, DEMO_CONVERSATIONS_FILE_PERSONAL
    return DEMO_MEMORIES_FILE, DEMO_CONVERSATIONS_FILE


SECTION_MEMORY_TYPES = {
    "Projects": MemoryType.PROJECT,
    "Decisions": MemoryType.DECISION,
    "Beliefs": MemoryType.BELIEF,
    "Preferences": MemoryType.PREFERENCE,
    "Active Tasks": MemoryType.TASK,
    "Technical Stack": MemoryType.FACT,
    "Future Roadmap": MemoryType.GOAL,
    "Blockers": MemoryType.BLOCKER,
    "Open Questions": MemoryType.OPEN_QUESTION,
    "Rules": MemoryType.RULE,
    "Implementation State": MemoryType.IMPLEMENTATION_STATE,
    "Code Areas": MemoryType.CODE_AREA,
    "People": MemoryType.PERSON,
    "Events": MemoryType.EVENT,
    "Skills": MemoryType.SKILL,
    "Interests": MemoryType.INTEREST,
    "Traits": MemoryType.TRAIT,
    "Habits": MemoryType.HABIT,
}

# Matches an optional "(T-90d)" chronology marker at the start of a bullet --
# see parse_bulk_memories below. Lets demo/demo_memories.md stagger facts
# across Haven's real development timeline (idea -> ... -> hackathon
# submission) instead of every bullet landing at the exact same instant.
_DAYS_AGO_RE = re.compile(r"^\(T-(?P<days>\d+)d\)\s*(?P<text>.+)$")

# Stamped onto every bulk-seeded KnowledgeObject's metadata["classification_reason"]
# -- the same field the dashboard's "Why?" inspector reads for a memory's
# provenance (obsidian.server.dashboard.inspect_memory). Bulk facts never go
# through the real Classifier (see seed_bulk_facts's docstring), so leaving
# this unset would render an empty classification panel; this is an honest
# description of that fact, not a fabricated per-fact reasoning trace. The
# memory's actual retrieval ranking/ontology evidence -- which *is* real,
# computed live -- is shown separately, in the same modal's Retrieval/
# Ontology sections.
_BULK_CLASSIFICATION_REASON = (
    "Seeded directly via the demo bulk-import path -- no live Classifier call. "
    "Its memory type comes from this fact's section heading in demo/demo_memories.md. "
    "The Retrieval and Ontology sections below, however, are computed live against "
    "this memory right now, exactly like any other memory in the vault."
)


def parse_bulk_memories(text: str) -> List[Tuple[MemoryType, str, Optional[datetime]]]:
    """Parse ``demo/demo_memories.md``'s ``## Section`` + ``- bullet`` format.

    A bullet may start with a ``(T-90d)`` marker (see ``_DAYS_AGO_RE``) to
    place it 90 days in the past instead of "now" -- stripped from the
    returned fact text either way. The third tuple element is that resolved
    ``valid_from`` (``None`` when no marker is present, meaning "use
    ``KnowledgeObject``'s own default of now").
    """
    memories: List[Tuple[MemoryType, str, Optional[datetime]]] = []
    memory_type = MemoryType.FACT
    now = datetime.utcnow()
    for line in text.splitlines():
        heading = re.match(r"^##\s+(.+)", line)
        if heading:
            memory_type = SECTION_MEMORY_TYPES[heading.group(1).strip()]
            continue
        bullet = re.match(r"^[-*]\s+(.+)", line)
        if bullet:
            raw = bullet.group(1).strip()
            valid_from = None
            days_ago = _DAYS_AGO_RE.match(raw)
            if days_ago:
                valid_from = now - timedelta(days=int(days_ago.group("days")))
                raw = days_ago.group("text").strip()
            memories.append((memory_type, raw, valid_from))
    return memories


def seed_bulk_facts(
    vault_writer: VaultWriter,
    ontology_pipeline: OntologyPipeline,
    memories_file: Path = DEMO_MEMORIES_FILE,
) -> int:
    """Write every fact in *memories_file* through the write path.

    No LLM call, no checkpoints, no write traces -- these facts are
    constructed directly as ``KnowledgeObject``s, exactly like
    ``HavenAdapter.add()``'s "store verbatim" convention. *memories_file*
    defaults to the primary Haven dataset, so every pre-existing caller
    (``scripts/seed_demo.py``) is unaffected; ``obsidian.server.main`` passes
    the space-appropriate file resolved via :func:`dataset_for_space`.
    Returns the number of memories written.
    """
    memories = parse_bulk_memories(memories_file.read_text(encoding="utf-8"))
    for memory_type, canonical_fact, valid_from in memories:
        kwargs = {
            "canonical_fact": canonical_fact,
            "memory_type": memory_type,
            "metadata": {"classification_reason": _BULK_CLASSIFICATION_REASON},
        }
        if valid_from is not None:
            kwargs["valid_from"] = valid_from
        knowledge = KnowledgeObject(**kwargs)
        vault_writer.write(knowledge)
        ontology_pipeline.process(knowledge)
    return len(memories)


# ---------------------------------------------------------------------------
# Scripted conversations -> real ManagerPipeline (via the real POST
# /api/v1/memory contract) -> real write traces + checkpoints
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^##\s+(?P<label>.+?)\s*\[external_key=(?P<key>[\w-]+)\]\s*$")
_CALL_RE = re.compile(r"^###\s+")
_TURN_RE = re.compile(r"^[-*]\s+(?P<role>\w+):\s*(?P<content>.+)$")

# Ordered so a more specific phrase (e.g. "new task is") is checked before a
# more generic one that could also appear in the same sentence.
_MEMORY_TYPE_KEYWORDS: List[Tuple[MemoryType, Tuple[str, ...]]] = [
    (MemoryType.DECISION, ("i decided", "i chose", "i picked")),
    (MemoryType.BLOCKER, ("is blocking", "is blocked on", "the blocker is")),
    (MemoryType.RULE, ("the rule is", "as a rule,")),
    (MemoryType.OPEN_QUESTION, ("open question is", "still not sure whether", "haven't decided whether")),
    (MemoryType.GOAL, ("my goal", "i'm aiming to")),
    (MemoryType.PREFERENCE, ("i prefer",)),
    (MemoryType.PROJECT, ("i'm building", "i'm working on", "i maintain", "i'm leading")),
    (MemoryType.TASK, ("new task is", "task is to", "i need to")),
]

_CLASSIFY_FACT_TEXT_RE = re.compile(r"Fact:\nText: (?P<text>.+)")


def parse_conversations(text: str) -> List[Dict[str, object]]:
    """Parse demo/demo_conversations.md into an ordered list of calls.

    Returns one dict per ``POST /memory``-shaped call, in file order:
    ``{"label": str, "external_key": str, "turns": [(role, content), ...]}``.
    See that file's own "Format" section for the markup this expects.
    """
    calls: List[Dict[str, object]] = []
    current_key = ""
    current_label = ""
    current_turns: List[Tuple[str, str]] = []

    def flush() -> None:
        if current_turns:
            calls.append(
                {
                    "label": current_label,
                    "external_key": current_key,
                    "turns": list(current_turns),
                }
            )

    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            # Flush the previous section's last call under its own (old)
            # key first -- otherwise it silently gets attributed to the
            # user this heading is about to switch to.
            flush()
            current_turns = []
            current_key = heading.group("key")
            current_label = heading.group("label").strip()
            continue
        if _CALL_RE.match(line):
            flush()
            current_turns = []
            continue
        turn = _TURN_RE.match(line)
        if turn:
            current_turns.append((turn.group("role"), turn.group("content").strip()))
    flush()
    return calls


def infer_memory_type(fact_text: str) -> MemoryType:
    """Guess a believable ``MemoryType`` from a scripted fact's own wording.

    ``MarkerLLM`` always classifies every fact identically (its content
    genuinely doesn't matter for the incremental-ingestion benchmarks it
    was built for -- see that module's docstring), which would dump every
    seeded conversation fact into one Dashboard category here. This
    keyword match instead lets each fact land in the category its own
    sentence already implies (see demo_conversations.md's authoring
    convention). Demo-only cosmetics: Haven's real Classifier is untouched.
    """
    text = fact_text.lower()
    for memory_type, keywords in _MEMORY_TYPE_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return memory_type
    return MemoryType.FACT


class ScriptedDemoLLM:
    """Deterministic, content-aware fake LLM for seeding conversation demos.

    Delegates fact *extraction* to a real ``MarkerLLM`` verbatim (imported
    from the benchmark suite, not reimplemented). The only thing added is
    classification: ``MarkerLLM`` returns one fixed ``memory_type`` for
    every fact it's asked to classify, so this instead reads the fact's own
    text out of the Classifier's prompt (its format is fixed -- see
    ``obsidian.manager_ai.classifier.Classifier.build_prompt``) and infers
    a type from it via :func:`infer_memory_type`. Importance scoring is
    left at ``MarkerLLM``'s fixed default -- not needed for a believable
    demo.
    """

    def __init__(self) -> None:
        self._marker_llm = MarkerLLM()

    def generate(self, prompt: str) -> str:
        if "Available memory types:" in prompt:
            match = _CLASSIFY_FACT_TEXT_RE.search(prompt)
            fact_text = match.group("text") if match else ""
            memory_type = infer_memory_type(fact_text)
            return json.dumps(
                {
                    "memory_type": memory_type.value,
                    "confidence": 0.9,
                    "reason": "demo-scripted",
                }
            )
        return self._marker_llm.generate(prompt)


def build_scripted_pipeline() -> ManagerPipeline:
    """A real ``ManagerPipeline`` wired to :class:`ScriptedDemoLLM`.

    Every stage but the LLM boundary is production code, unmodified --
    same trade-off ``benchmarks/incremental_ingestion/fake_llm.py``
    documents for why a scripted LLM is used here instead of a real one.
    """
    llm = ScriptedDemoLLM()
    return ManagerPipeline(
        extractor=Extractor(llm=llm),
        classifier=Classifier(llm=llm),
        importance_scorer=ImportanceScorer(llm=llm),
        canonical_matcher=CanonicalMatcher(),
        knowledge_updater=KnowledgeUpdater(),
    )
