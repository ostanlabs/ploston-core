"""config_get tool handler - read current configuration."""

from typing import Any

from ploston_core.config import ConfigLoader, StagedConfig


def get_nested_value(obj: Any, path: str) -> Any:
    """Get a nested value from an object using dot notation.

    Args:
        obj: Object to get value from (dict or dataclass)
        path: Dot-notation path (e.g., "mcp.servers.github")

    Returns:
        Value at path, or None if not found
    """
    if not path:
        return obj

    parts = path.split(".")
    current = obj

    for part in parts:
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None

    return current


def config_to_dict(obj: Any) -> Any:
    """Convert config object to dict recursively.

    Args:
        obj: Config object (dataclass or dict)

    Returns:
        Dictionary representation
    """
    import dataclasses

    if obj is None:
        return None
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return {k: config_to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [config_to_dict(item) for item in obj]
    return obj


async def handle_config_get(
    arguments: dict[str, Any],
    staged_config: StagedConfig,
    config_loader: ConfigLoader,
) -> dict[str, Any]:
    """Handle config_get tool call.

    Args:
        arguments: Tool arguments with optional 'path'
        staged_config: StagedConfig instance
        config_loader: ConfigLoader instance

    Returns:
        Config value with metadata
    """
    path = arguments.get("path", "")

    # Get merged config (base + staged changes)
    merged_config = staged_config.get_merged()

    # Get value at path
    if path:
        value = get_nested_value(merged_config, path)
    else:
        value = merged_config

    # Convert to dict for JSON serialization
    value = config_to_dict(value)

    # Get source info
    source = config_loader._config_path if hasattr(config_loader, "_config_path") else None

    # Check if there are staged changes
    has_staged_changes = staged_config.has_changes()

    return {
        "path": path or "(root)",
        "value": value,
        "source": source,
        "has_staged_changes": has_staged_changes,
    }
