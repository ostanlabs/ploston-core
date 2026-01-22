"""CLI formatters for tool registry."""

import json

from .types import ToolDefinition


def format_tool_list(tools: list[ToolDefinition]) -> str:
    """Format tool list for CLI display.

    Args:
        tools: List of tools to format

    Returns:
        Formatted string for CLI output
    """
    if not tools:
        return "No tools found."

    lines = []
    lines.append(f"Found {len(tools)} tool(s):\n")

    for tool in tools:
        status_icon = "✓" if tool.status.value == "available" else "✗"
        source_label = f"[{tool.source.value}]"
        if tool.server_name:
            source_label = f"[{tool.source.value}:{tool.server_name}]"

        lines.append(f"  {status_icon} {tool.name:30} {source_label:20} {tool.description}")

    return "\n".join(lines)


def format_tool_detail(tool: ToolDefinition) -> str:
    """Format tool detail for CLI display.

    Args:
        tool: Tool to format

    Returns:
        Formatted string for CLI output
    """
    lines = []

    # Header
    lines.append(f"Tool: {tool.name}")
    lines.append("=" * 60)

    # Basic info
    lines.append(f"Description: {tool.description}")
    lines.append(f"Source:      {tool.source.value}")
    if tool.server_name:
        lines.append(f"Server:      {tool.server_name}")
    lines.append(f"Status:      {tool.status.value}")

    if tool.last_seen:
        lines.append(f"Last Seen:   {tool.last_seen.isoformat()}")

    if tool.error:
        lines.append(f"Error:       {tool.error}")

    # Input schema
    lines.append("\nInput Schema:")
    lines.append(json.dumps(tool.input_schema, indent=2))

    # Output schema
    if tool.output_schema:
        lines.append("\nOutput Schema:")
        lines.append(json.dumps(tool.output_schema, indent=2))

    return "\n".join(lines)
