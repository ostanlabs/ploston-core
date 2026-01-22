"""Tool invoker types."""

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCallResult:
    """Result of a tool invocation (high-level).

    Note: This is different from MCPCallResult in MCP Client Manager.
    - MCPCallResult: Low-level MCP response with raw_response, is_error flag
    - ToolCallResult: High-level result for workflow execution
    """

    success: bool
    output: Any
    duration_ms: int
    tool_name: str
    error: Any = None  # AELError
    structured_content: dict[str, Any] | None = None  # MCP structuredContent field
