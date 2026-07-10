"""Custom exception classes for Obsidian Memory.

All exceptions inherit from :class:`ObsidianError` so callers can
catch a single base type when needed.
"""

from __future__ import annotations

from typing import Any, Optional


class ObsidianError(Exception):
    """Base exception for all Obsidian Memory errors.

    Parameters
    ----------
    message : str
        Human‑readable description of the error.
    field : str, optional
        The name of the field or component that caused the error.
    context : Any, optional
        Additional structured information about the error.
    """

    def __init__(
        self,
        message: str = "An Obsidian Memory error occurred.",
        *,
        field: Optional[str] = None,
        context: Optional[Any] = None,
    ) -> None:
        self.field = field
        self.context = context
        super().__init__(message)

    def __str__(self) -> str:
        parts: list[str] = [self.args[0]]
        if self.field is not None:
            parts.append(f"field={self.field!r}")
        if self.context is not None:
            parts.append(f"context={self.context!r}")
        return " | ".join(parts)


class ValidationError(ObsidianError):
    """Raised when data fails validation.

    Parameters
    ----------
    message : str
        Description of the validation failure.
    field : str, optional
        The name of the field that failed validation.
    context : Any, optional
        Additional structured information about the validation error.
    """

    def __init__(
        self,
        message: str = "Validation failed.",
        *,
        field: Optional[str] = None,
        context: Optional[Any] = None,
    ) -> None:
        super().__init__(message, field=field, context=context)


class ExtractionError(ObsidianError):
    """Raised by the Manager AI when extraction fails.

    Parameters
    ----------
    message : str
        Description of the extraction failure.
    field : str, optional
        The name of the field or component that caused the error.
    context : Any, optional
        Additional structured information about the extraction error.
    """

    def __init__(
        self,
        message: str = "Extraction failed.",
        *,
        field: Optional[str] = None,
        context: Optional[Any] = None,
    ) -> None:
        super().__init__(message, field=field, context=context)


class MemoryEngineError(ObsidianError):
    """Raised by the Memory Engine when processing fails.

    Parameters
    ----------
    message : str
        Description of the processing failure.
    field : str, optional
        The name of the field or component that caused the error.
    context : Any, optional
        Additional structured information about the processing error.
    """

    def __init__(
        self,
        message: str = "Memory Engine operation failed.",
        *,
        field: Optional[str] = None,
        context: Optional[Any] = None,
    ) -> None:
        super().__init__(message, field=field, context=context)


class VaultError(ObsidianError):
    """Raised by the Vault when a storage operation fails.

    Parameters
    ----------
    message : str
        Description of the storage failure.
    field : str, optional
        The name of the field or component that caused the error.
    context : Any, optional
        Additional structured information about the storage error.
    """

    def __init__(
        self,
        message: str = "Vault operation failed.",
        *,
        field: Optional[str] = None,
        context: Optional[Any] = None,
    ) -> None:
        super().__init__(message, field=field, context=context)


class RetrievalError(ObsidianError):
    """Raised by the Context Manager when retrieval fails.

    Parameters
    ----------
    message : str
        Description of the retrieval failure.
    field : str, optional
        The name of the field or component that caused the error.
    context : Any, optional
        Additional structured information about the retrieval error.
    """

    def __init__(
        self,
        message: str = "Retrieval failed.",
        *,
        field: Optional[str] = None,
        context: Optional[Any] = None,
    ) -> None:
        super().__init__(message, field=field, context=context)


class PromptBuilderError(ObsidianError):
    """Raised by the Prompt Builder when prompt construction fails.

    Parameters
    ----------
    message : str
        Description of the prompt construction failure.
    field : str, optional
        The name of the field or component that caused the error.
    context : Any, optional
        Additional structured information about the prompt builder error.
    """

    def __init__(
        self,
        message: str = "Prompt Builder operation failed.",
        *,
        field: Optional[str] = None,
        context: Optional[Any] = None,
    ) -> None:
        super().__init__(message, field=field, context=context)


class ConversationImportError(ObsidianError):
    """Raised by Conversation Importers when import fails.

    Named to avoid shadowing the builtin ``ImportError`` -- several live
    modules (``manager_ai/llm.py``, ``manager_ai/transport_retry.py``,
    ``memory_engine/query_rewriter.py``) rely on catching the *builtin*
    ``ImportError`` for optional-dependency fallbacks; a
    ``from obsidian.core import *`` would have silently shadowed that.

    Parameters
    ----------
    message : str
        Description of the import failure.
    field : str, optional
        The name of the field or component that caused the error.
    context : Any, optional
        Additional structured information about the import error.
    """

    def __init__(
        self,
        message: str = "Import failed.",
        *,
        field: Optional[str] = None,
        context: Optional[Any] = None,
    ) -> None:
        super().__init__(message, field=field, context=context)
