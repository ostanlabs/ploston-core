"""remove_mcp_server tool - Remove an MCP server from configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..staged_config import StagedConfig


async def handle_remove_mcp_server(
    arguments: dict[str, Any],
    staged_config: StagedConfig,
) -> dict[str, Any]:
    """
    Handle ploston:remove_mcp_server tool call.

    Removes an MCP server from the staged configuration.

    Args:
        arguments: Tool arguments containing server name
        staged_config: StagedConfig instance

    Returns:
        Result with success status and staged changes count
    """
    name = arguments.get("name")
    if not name:
        return {
            "success": False,
            "error": "Server name is required",
            "staged_changes_count": 0,
        }

    # Check if server exists in merged config
    merged = staged_config.get_merged()
    existing_servers = merged.get("tools", {}).get("mcp_servers", {})

    if name not in existing_servers:
        return {
            "success": False,
            "error": f"MCP server '{name}' not found in configuration",
            "staged_changes_count": _count_staged_changes(staged_config),
        }

    # Stage the removal by setting to None (or a special marker)
    # The staged_config should handle None as a deletion marker
    path = f"tools.mcp_servers.{name}"
    staged_config.set(path, None)

    return {
        "success": True,
        "removed": name,
        "staged_changes_count": _count_staged_changes(staged_config),
    }


def _count_staged_changes(staged_config: StagedConfig) -> int:
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
REMOVE_MCP_SERVER_SCHEMA = {
    "name": "ploston:remove_mcp_server",
    "description": "Remove an MCP server from configuration.",
    "inputSchema": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the server to remove",
            },
        },
    },
}
