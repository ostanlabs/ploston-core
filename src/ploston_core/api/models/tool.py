"""Tool REST API models."""

from enum import Enum
from typing import Any

from pydantic import BaseModel


class ToolSource(str, Enum):
    """Tool source enum."""

    MCP = "mcp"
    HTTP = "http"
    SYSTEM = "system"


class ToolSummary(BaseModel):
    """Tool summary for list response."""

    name: str
    source: ToolSource
    server: str | None = None
    description: str | None = None
    category: str | None = None


class ToolDetail(BaseModel):
    """Full tool details with schema."""

    name: str
    source: ToolSource
    server: str | None = None
    description: str | None = None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None


class ToolListResponse(BaseModel):
    """Tool list response."""

    tools: list[ToolSummary]
    total: int


class ToolCallRequest(BaseModel):
    """Direct tool call request."""

    params: dict[str, Any] = {}


class ToolCallResponse(BaseModel):
    """Direct tool call response."""

    tool_name: str
    duration_ms: int
    result: Any


class RefreshServerResult(BaseModel):
    """Single server refresh result."""

    status: str  # "ok" or "error"
    tools: int | None = None
    error: str | None = None


class ToolRefreshResponse(BaseModel):
    """Tool refresh response."""

    refreshed: int
    servers: dict[str, RefreshServerResult]
