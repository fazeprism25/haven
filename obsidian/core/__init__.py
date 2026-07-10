"""Obsidian Memory – Core package.

This package contains the shared data model, enums, value objects,
validation helpers, and error types used by all subsystems of Obsidian
Memory.

No business logic lives here.
"""

from obsidian.core.enums import (
    RelationshipType,
    Role,
    SourceType,
    MemoryType,
    EntityType,
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
    MemoryIdentity,
    MemoryMetadata,
    Relationship,
    TemporalContext,
)
from obsidian.core.validation import (
    validate_confidence,
    validate_importance,
    validate_non_empty_string,
    validate_unique_strings,
    validate_datetime_order,
    validate_probability,
    validate_uuid,
)

__all__ = [
    # Enums
    "RelationshipType",
    "Role",
    "SourceType",
    "MemoryType",
    "EntityType",
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
    "MemoryIdentity",
    "MemoryMetadata",
    "Relationship",
    "TemporalContext",
    # Validation
    "validate_confidence",
    "validate_importance",
    "validate_non_empty_string",
    "validate_unique_strings",
    "validate_datetime_order",
    "validate_probability",
    "validate_uuid",
]
