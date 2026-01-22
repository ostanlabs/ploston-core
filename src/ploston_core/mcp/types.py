"""MCP Client Manager types for AEL."""

from dataclasses import dataclass, field
from typing import Any

from ploston_core.types import ConnectionStatus


@dataclass
class ToolSchema:
    """MCP tool schema.

    Represents a tool available from an MCP server.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None


@dataclass
class ServerStatus:
    """Status of an MCP server connection.

    Used for monitoring and diagnostics.
    """

    name: str
    status: ConnectionStatus
    tools: list[str] = field(default_factory=list)
    error: str | None = None
    last_connected: str | None = None
    last_error: str | None = None


@dataclass
class MCPCallResult:
    """Result of an MCP tool call (low-level).

    Note: This is different from ToolCallResult in Tool Invoker.
    MCPCallResult is the raw MCP response; ToolCallResult is the
    higher-level result used by workflow execution.
    """

    success: bool
    content: Any  # Parsed content from response
    raw_response: dict[str, Any]  # Full MCP response
    duration_ms: int
    error: str | None = None
    is_error: bool = False  # MCP isError flag
    structured_content: dict[str, Any] | None = None  # MCP structuredContent field
