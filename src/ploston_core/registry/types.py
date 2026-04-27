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
    server_name: str | None = None  # MCP server name
    tags: set[str] = field(default_factory=set)  # Unified tag set (DEC-170)

    # Schemas
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] | None = None

    # Status
    status: ToolStatus = ToolStatus.UNKNOWN
    last_seen: datetime | None = None
    error: str | None = None

    def to_mcp_tool(
        self,
        suggested_output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convert to MCP tool format for exposure.

        Args:
            suggested_output_schema: Optional learned schema (F-088 T-896) to
                inject as ``outputSchema`` when the tool has no MCP-declared
                schema. MCP-declared schemas always take precedence.

        Returns:
            MCP tool dict with name, description, and schemas.
        """
        tool: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.output_schema:
            tool["outputSchema"] = self.output_schema
        elif suggested_output_schema:
            # Tag injected schemas so tooling (e.g. the inspector) can tell
            # them apart from server-declared ones. Shallow-copy to avoid
            # mutating the caller's dict; the ``x-`` prefix is MCP's
            # convention for extension fields and is ignored by agents.
            injected = dict(suggested_output_schema)
            injected.setdefault("x-schema_source", "learned")
            tool["outputSchema"] = injected
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
