"""config_reset tool - Discard all staged changes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..staged_config import StagedConfig


async def handle_config_reset(
    arguments: dict[str, Any],
    staged_config: "StagedConfig",
) -> dict[str, Any]:
    """
    Handle ploston:config_reset tool call.
    
    Discards all staged changes and resets to base configuration.
    
    Args:
        arguments: Tool arguments (none required)
        staged_config: StagedConfig instance
        
    Returns:
        Result with count of discarded changes
    """
    # Count changes before reset
    changes_before = _count_staged_changes(staged_config)

    # Clear staged changes
    staged_config.clear()
    
    return {
        "success": True,
        "discarded_changes": changes_before,
        "message": f"Discarded {changes_before} staged change(s). Configuration reset to base state.",
    }


def _count_staged_changes(staged_config: "StagedConfig") -> int:
    """Count the number of staged changes."""
    changes = staged_config.changes
    return _count_dict_items(changes)


def _count_dict_items(d: dict[str, Any], count: int = 0) -> int:
    """Recursively count items in a nested dict."""
    for key, value in d.items():
        if isinstance(value, dict):
            count = _count_dict_items(value, count)
        else:
            count += 1
    return count


# Tool schema for MCP exposure
CONFIG_RESET_SCHEMA = {
    "name": "ploston:config_reset",
    "description": "Discard all staged changes and reset to base configuration.",
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}
