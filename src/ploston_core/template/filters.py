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


def filter_string(value: Any) -> str:
    """Convert value to string. Returns empty string for None."""
    return str(value) if value is not None else ""


def filter_int(value: Any) -> int | None:
    """Convert value to int. Returns None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def filter_float(value: Any) -> float | None:
    """Convert value to float. Returns None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def filter_tojson(value: Any) -> str:
    """Serialize value to JSON string (Jinja2 alias for json)."""
    return json.dumps(value)


def filter_join(value: Any, separator: str = "") -> str:
    """Join iterable with separator. Returns empty string for None."""
    if value is None:
        return ""
    return separator.join(str(x) for x in value)


def filter_keys(value: Any) -> list:
    """Return list of dict keys. Returns empty list for non-dicts."""
    return list(value.keys()) if isinstance(value, dict) else []


def filter_values(value: Any) -> list:
    """Return list of dict values. Returns empty list for non-dicts."""
    return list(value.values()) if isinstance(value, dict) else []


def filter_first(value: Any) -> Any:
    """Return first element of sequence. Returns None if empty or not indexable."""
    if value:
        try:
            return value[0]
        except (IndexError, KeyError, TypeError):
            return None
    return None


def filter_last(value: Any) -> Any:
    """Return last element of sequence. Returns None if empty or not indexable."""
    if value:
        try:
            return value[-1]
        except (IndexError, KeyError, TypeError):
            return None
    return None


# Registry of available filters
FILTERS: dict[str, Any] = {
    "length": filter_length,
    "default": filter_default,
    "json": filter_json,
    "string": filter_string,
    "int": filter_int,
    "float": filter_float,
    "tojson": filter_tojson,
    "join": filter_join,
    "keys": filter_keys,
    "values": filter_values,
    "first": filter_first,
    "last": filter_last,
}
