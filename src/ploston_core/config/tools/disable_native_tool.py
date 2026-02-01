"""disable_native_tool tool - Disable a native tool."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..schema_registry import SchemaRegistry

if TYPE_CHECKING:
    from ..staged_config import StagedConfig


async def handle_disable_native_tool(
    arguments: dict[str, Any],
    staged_config: "StagedConfig",
) -> dict[str, Any]:
    """
    Handle ploston:disable_native_tool tool call.
    
    Disables a native tool by setting enabled=false.
    
    Args:
        arguments: Tool arguments containing tool name
        staged_config: StagedConfig instance
        
    Returns:
        Result with success status and staged changes count
    """
    tool = arguments.get("tool")
    if not tool:
        return {
            "success": False,
            "error": "Tool name is required",
            "staged_changes_count": 0,
        }

    # Check if tool is valid
    valid_tools = SchemaRegistry.get_native_tool_names()
    if tool not in valid_tools:
        return {
            "success": False,
            "error": f"Unknown native tool: {tool}. Valid tools: {', '.join(valid_tools)}",
            "staged_changes_count": _count_staged_changes(staged_config),
        }

    # Check if tool exists in merged config
    merged = staged_config.get_merged()
    existing_tools = merged.get("tools", {}).get("native_tools", {})
    
    if tool not in existing_tools:
        return {
            "success": False,
            "error": f"Native tool '{tool}' is not configured",
            "staged_changes_count": _count_staged_changes(staged_config),
        }

    # Stage the change - set enabled to false
    path = f"tools.native_tools.{tool}.enabled"
    staged_config.set(path, False)

    return {
        "success": True,
        "disabled": tool,
        "staged_changes_count": _count_staged_changes(staged_config),
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
DISABLE_NATIVE_TOOL_SCHEMA = {
    "name": "ploston:disable_native_tool",
    "description": "Disable a native tool by setting enabled=false.",
    "inputSchema": {
        "type": "object",
        "required": ["tool"],
        "properties": {
            "tool": {
                "type": "string",
                "enum": ["kafka", "firecrawl", "ollama", "filesystem", "network"],
                "description": "Native tool to disable",
            },
        },
    },
}
