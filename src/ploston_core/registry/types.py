"""Tool Registry types for AEL."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ploston_core.types import ToolSource, ToolStatus


@dataclass
class ToolDefinition:
    """Complete tool definition.

    Represents a tool available in the registry, including its
    source, schemas, and availability status.
    """

    # Identity (required fields first)
    name: str
    description: str
    source: ToolSource

    # Optional fields (with defaults)
    category: str | None = None  # Tool category for grouping
    server_name: str | None = None  # MCP server name

    # Schemas
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None

    # Status
    status: ToolStatus = ToolStatus.UNKNOWN
    last_seen: datetime | None = None
    error: str | None = None

    def to_mcp_tool(self) -> dict[str, Any]:
        """Convert to MCP tool format for exposure.

        Returns:
            MCP tool dict with name, description, and schemas
        """
        tool: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.output_schema:
            tool["outputSchema"] = self.output_schema
        return tool


@dataclass
class ToolRouter:
    """Routing information for tool invocation.

    Tells the Tool Invoker where to route a tool call.
    """

    source: ToolSource
    server_name: str | None = None


@dataclass
class RefreshResult:
    """Result of tool refresh operation.

    Tracks what changed during a refresh.
    """

    total_tools: int
    added: list[str]
    removed: list[str]
    updated: list[str]
    errors: dict[str, str]  # server_name -> error message
