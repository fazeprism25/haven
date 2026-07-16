"""Obsidian Memory – Core package.

This package contains the shared data model, enums, value objects,
and error types used by all subsystems of Obsidian Memory.

No business logic lives here.
"""

from obsidian.core.enums import (
    Role,
    SourceType,
    MemoryType,
    MemoryDomain,
    EntityType,
)
from obsidian.core.memory_domain import (
    MEMORY_TYPE_DOMAIN,
    resolve_domain,
)
from obsidian.core.errors import (
    ObsidianError,
    ValidationError,
    ExtractionError,
    MemoryEngineError,
    VaultError,
    RetrievalError,
    PromptBuilderError,
    ConversationImportError,
)
from obsidian.core.types import (
    Attachment,
    Conversation,
    Event,
)
from obsidian.core.value_objects import (
    Entity,
    TopicTag,
)

__all__ = [
    # Enums
    "Role",
    "SourceType",
    "MemoryType",
    "MemoryDomain",
    "EntityType",
    # Memory domain mapping
    "MEMORY_TYPE_DOMAIN",
    "resolve_domain",
    # Errors
    "ObsidianError",
    "ValidationError",
    "ExtractionError",
    "MemoryEngineError",
    "VaultError",
    "RetrievalError",
    "PromptBuilderError",
    "ConversationImportError",
    # Types
    "Attachment",
    "Conversation",
    "Event",
    # Value Objects
    "Entity",
    "TopicTag",
]
