"""config_location tool handler - get/set config write target."""

import os
from pathlib import Path
from typing import Any

from ploston_core.config import ConfigLoader


async def handle_config_location(
    arguments: dict[str, Any],
    current_location: str | None,
    config_loader: ConfigLoader,
) -> dict[str, Any]:
    """Handle config_location tool call.

    Args:
        arguments: Tool arguments with optional 'scope' or 'path'
        current_location: Current write location (if set)
        config_loader: ConfigLoader instance

    Returns:
        Location info and optionally new location
    """
    scope = arguments.get("scope")
    custom_path = arguments.get("path")

    # Get current source
    source = config_loader._config_path if hasattr(config_loader, "_config_path") else None

    # If no arguments, return current info
    if not scope and not custom_path:
        return {
            "current_source": source,
            "write_target": current_location or source or "./ael-config.yaml",
            "available_scopes": ["project", "user"],
        }

    # Set new location
    if custom_path:
        new_location = custom_path
    elif scope == "project":
        new_location = "./ael-config.yaml"
    elif scope == "user":
        new_location = str(Path.home() / ".ael" / "config.yaml")
    else:
        return {
            "error": f"Invalid scope: {scope}",
            "available_scopes": ["project", "user"],
        }

    # Validate path is writable
    try:
        parent = Path(new_location).parent
        if not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        # Check if we can write
        if Path(new_location).exists():
            if not os.access(new_location, os.W_OK):
                return {
                    "error": f"Cannot write to {new_location}: permission denied",
                }
        else:
            if not os.access(str(parent), os.W_OK):
                return {
                    "error": f"Cannot write to directory {parent}: permission denied",
                }
    except Exception as e:
        return {
            "error": f"Invalid path: {e}",
        }

    return {
        "current_source": source,
        "write_target": new_location,
        "new_location": new_location,  # Signal to registry to update
    }
