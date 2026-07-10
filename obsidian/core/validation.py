"""Validation helpers for the Obsidian Memory data model.

These functions validate individual field values and raise
:class:`~obsidian.core.errors.ValidationError` (or built‑in
exceptions) when constraints are violated.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence, Tuple
from uuid import UUID

from obsidian.core.errors import ValidationError


def validate_uuid(value: Any, field_name: str = "id") -> UUID:
    """Validate that *value* is a valid UUID.

    Parameters
    ----------
    value : Any
        The value to validate (``UUID`` instance or ``str``).
    field_name : str
        The name of the field being validated (used in error messages).

    Returns
    -------
    UUID
        The validated UUID.

    Raises
    ------
    ValidationError
        If *value* is not a valid UUID.
    """
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            raise ValidationError(
                message=f"{field_name}: invalid UUID string: {value!r}",
                field=field_name,
            )
    raise ValidationError(
        message=f"{field_name}: expected UUID or str, got {type(value).__name__}",
        field=field_name,
    )


def validate_confidence(value: float, field_name: str = "confidence") -> float:
    """Validate that *value* is between 0.0 and 1.0 inclusive.

    Parameters
    ----------
    value : float
        The confidence value to validate.
    field_name : str
        The name of the field being validated.

    Returns
    -------
    float
        The validated confidence value.

    Raises
    ------
    ValidationError
        If *value* is outside the allowed range.
    """
    if not isinstance(value, (int, float)):
        raise ValidationError(
            message=f"{field_name}: expected float, got {type(value).__name__}",
            field=field_name,
        )
    if not (0.0 <= value <= 1.0):
        raise ValidationError(
            message=f"{field_name}: must be between 0.0 and 1.0, got {value}",
            field=field_name,
        )
    return float(value)


def validate_importance(value: float, field_name: str = "importance") -> float:
    """Validate that *value* is between 0.0 and 1.0 inclusive.

    Alias for :func:`validate_confidence` with a different default
    field name.

    Parameters
    ----------
    value : float
        The importance value to validate.
    field_name : str
        The name of the field being validated.

    Returns
    -------
    float
        The validated importance value.

    Raises
    ------
    ValidationError
        If *value* is outside the allowed range.
    """
    return validate_confidence(value, field_name)


def validate_non_empty_string(value: str, field_name: str = "value") -> str:
    """Validate that *value* is a non‑empty string.

    Parameters
    ----------
    value : str
        The string to validate.
    field_name : str
        The name of the field being validated (used in error messages).

    Returns
    -------
    str
        The validated string.

    Raises
    ------
    ValidationError
        If *value* is empty or whitespace‑only.
    """
    if not isinstance(value, str):
        raise ValidationError(
            message=f"{field_name}: expected str, got {type(value).__name__}",
            field=field_name,
        )
    if not value.strip():
        raise ValidationError(
            message=f"{field_name}: must be non‑empty",
            field=field_name,
        )
    return value


def validate_unique_strings(values: Sequence[str], field_name: str = "values") -> Tuple[str, ...]:
    """Validate that *values* contains no duplicate strings.

    Parameters
    ----------
    values : sequence of str
        The strings to validate.
    field_name : str
        The name of the field being validated (used in error messages).

    Returns
    -------
    tuple of str
        The validated strings (as a tuple).

    Raises
    ------
    ValidationError
        If *values* contains duplicate entries.
    """
    if not isinstance(values, (list, tuple)):
        raise ValidationError(
            message=f"{field_name}: expected list or tuple, got {type(values).__name__}",
            field=field_name,
        )
    seen: set = set()
    for item in values:
        if not isinstance(item, str):
            raise ValidationError(
                message=f"{field_name}: expected str, got {type(item).__name__}",
                field=field_name,
            )
        if item in seen:
            raise ValidationError(
                message=f"{field_name}: duplicate value {item!r}",
                field=field_name,
            )
        seen.add(item)
    return tuple(values)


def validate_datetime_order(
    start: datetime,
    end: datetime,
    start_field: str = "start",
    end_field: str = "end",
) -> None:
    """Validate that *start* is not later than *end*.

    Parameters
    ----------
    start : datetime
        The start of the time range.
    end : datetime
        The end of the time range.
    start_field : str
        The name of the start field (used in error messages).
    end_field : str
        The name of the end field (used in error messages).

    Raises
    ------
    ValidationError
        If *start* > *end*.
    """
    if not isinstance(start, datetime):
        raise ValidationError(
            message=f"{start_field}: expected datetime, got {type(start).__name__}",
            field=start_field,
        )
    if not isinstance(end, datetime):
        raise ValidationError(
            message=f"{end_field}: expected datetime, got {type(end).__name__}",
            field=end_field,
        )
    if start > end:
        raise ValidationError(
            message=f"{start_field} ({start}) must not be later than {end_field} ({end})",
            field=start_field,
        )


def validate_probability(value: float, field_name: str = "probability") -> float:
    """Validate that *value* is between 0.0 and 1.0 inclusive.

    Parameters
    ----------
    value : float
        The probability value to validate.
    field_name : str
        The name of the field being validated (used in error messages).

    Returns
    -------
    float
        The validated probability value.

    Raises
    ------
    ValidationError
        If *value* is outside the allowed range.
    """
    if not isinstance(value, (int, float)):
        raise ValidationError(
            message=f"{field_name}: expected float, got {type(value).__name__}",
            field=field_name,
        )
    if not (0.0 <= value <= 1.0):
        raise ValidationError(
            message=f"{field_name}: must be between 0.0 and 1.0, got {value}",
            field=field_name,
        )
    return float(value)
