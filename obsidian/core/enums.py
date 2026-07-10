"""Enumerations for the Obsidian Memory data model.

All enumerations are ``str`` enums so they can be serialized to JSON
natively and compared with plain strings.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    """Role of an event participant in a conversation.

    Values
    ------
    USER : str
        A human user.
    ASSISTANT : str
        An AI assistant.
    SYSTEM : str
        A system message (e.g. instructions).
    TOOL : str
        A tool invocation or result.
    """

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class SourceType(str, Enum):
    """Origin system of a conversation.

    Values
    ------
    CHATGPT : str
        ChatGPT web or API.
    CLAUDE : str
        Claude web or API.
    GEMINI : str
        Google Gemini.
    EMAIL : str
        Email threads.
    SLACK : str
        Slack messages.
    DISCORD : str
        Discord messages.
    WHATSAPP : str
        WhatsApp messages.
    MEETING : str
        Meeting transcription.
    VOICE : str
        Voice transcription.
    OBSIDIAN : str
        An imported Obsidian vault Markdown note.
    MANUAL : str
        Manually entered data.
    """

    CHATGPT = "chatgpt"
    CLAUDE = "claude"
    GEMINI = "gemini"
    EMAIL = "email"
    SLACK = "slack"
    DISCORD = "discord"
    WHATSAPP = "whatsapp"
    MEETING = "meeting"
    VOICE = "voice"
    OBSIDIAN = "obsidian"
    MANUAL = "manual"


class MemoryType(str, Enum):
    """Semantic category of a Memory.

    Values
    ------
    FACT : str
        An objective fact about the world or user.
    PREFERENCE : str
        A user preference or like/dislike.
    BELIEF : str
        A belief or opinion held by the user.
    DECISION : str
        A decision the user has made.
    GOAL : str
        A goal the user is working toward.
    PROJECT : str
        A project the user is involved in.
    PERSON : str
        Information about a person.
    TASK : str
        A task or to‑do item.
    EVENT : str
        A past or future event.
    SKILL : str
        A skill the user possesses.
    RULE : str
        A rule or guideline the user follows.
    BLOCKER : str
        Something currently preventing progress on a project or task.
    IMPLEMENTATION_STATE : str
        What is built, stubbed, or in-progress for a project or component
        -- "done-ness," distinct from ``TASK``'s "to-do."
    CODE_AREA : str
        A file or component relevant to a current focus.
    OPEN_QUESTION : str
        An explicitly unresolved question.

    Notes
    -----
    ``BLOCKER``, ``IMPLEMENTATION_STATE``, ``CODE_AREA``, and
    ``OPEN_QUESTION`` were added to close a knowledge-representation gap
    identified by :mod:`obsidian.memory_engine.coverage_analyzer` -- see
    ``docs/architecture/PROJECT_STATE_KNOWLEDGE_MODEL.md`` for the full
    design rationale. As of their addition, nothing in the write pipeline
    (:class:`~obsidian.manager_ai.extractor.Extractor`'s prompt) asks for
    this kind of content yet, so these types exist for a future write path
    to classify into, not because the Classifier is expected to assign
    them today.
    """

    FACT = "fact"
    PREFERENCE = "preference"
    BELIEF = "belief"
    DECISION = "decision"
    GOAL = "goal"
    PROJECT = "project"
    PERSON = "person"
    TASK = "task"
    EVENT = "event"
    SKILL = "skill"
    RULE = "rule"
    BLOCKER = "blocker"
    IMPLEMENTATION_STATE = "implementation_state"
    CODE_AREA = "code_area"
    OPEN_QUESTION = "open_question"


class RelationshipType(str, Enum):
    """Type of relationship between two Memories.

    Values
    ------
    RELATED_TO : str
        A generic association (no specific semantics).
    DEPENDS_ON : str
        The source Memory depends on the target being true.
    SUPPORTS : str
        The source Memory supports or confirms the target.
    CONTRADICTS : str
        The source Memory contradicts the target.
    PARENT : str
        The source Memory is a parent of the target.
    CHILD : str
        The source Memory is a child of the target.
    REFERENCES : str
        The source Memory references the target.
    BELONGS_TO : str
        The source Memory belongs to the target.
    """

    RELATED_TO = "related_to"
    DEPENDS_ON = "depends_on"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    PARENT = "parent"
    CHILD = "child"
    REFERENCES = "references"
    BELONGS_TO = "belongs_to"


class EntityType(str, Enum):
    """Type of a named entity extracted from memory content.

    Values
    ------
    PERSON : str
        A person.
    ORGANIZATION : str
        An organization or company.
    PLACE : str
        A physical location.
    PRODUCT : str
        A product or service.
    TECHNOLOGY : str
        A technology, framework, or tool.
    FILE : str
        A file or document.
    PROJECT : str
        A project.
    OTHER : str
        Any other entity type.
    """

    PERSON = "person"
    ORGANIZATION = "organization"
    PLACE = "place"
    PRODUCT = "product"
    TECHNOLOGY = "technology"
    FILE = "file"
    PROJECT = "project"
    OTHER = "other"
