"""Template filter implementations."""

import json
from typing import Any


def filter_length(value: Any) -> int:
    """Return length of string, list, or dict.

    Args:
        value: Value to get length of

    Returns:
        Length of value

    Raises:
        TypeError: If value doesn't support len()
    """
    return len(value)


def filter_default(value: Any, default: Any) -> Any:
    """Return default if value is None.

    Args:
        value: Value to check
        default: Default value to return if value is None

    Returns:
        value if not None, else default
    """
    return value if value is not None else default


def filter_json(value: Any) -> str:
    """Serialize value to JSON string.

    Args:
        value: Value to serialize

    Returns:
        JSON string representation
    """
    return json.dumps(value)


# Registry of available filters
FILTERS: dict[str, Any] = {
    "length": filter_length,
    "default": filter_default,
    "json": filter_json,
}
