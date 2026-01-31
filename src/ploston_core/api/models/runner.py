"""Runner REST API models.

Implements S-184: Runner REST API
- T-529: Runner API models (Pydantic)
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class RunnerStatusEnum(str, Enum):
    """Runner connection status."""

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class RunnerSummary(BaseModel):
    """Runner summary for list response."""

    id: str = Field(description="Internal runner ID")
    name: str = Field(description="Human-readable runner name")
    status: RunnerStatusEnum = Field(description="Connection status")
    last_seen: datetime | None = Field(default=None, description="Last heartbeat timestamp")
    tool_count: int = Field(default=0, description="Number of available tools")


class RunnerDetail(BaseModel):
    """Full runner details."""

    id: str = Field(description="Internal runner ID")
    name: str = Field(description="Human-readable runner name")
    status: RunnerStatusEnum = Field(description="Connection status")
    created_at: datetime = Field(description="Creation timestamp")
    last_seen: datetime | None = Field(default=None, description="Last heartbeat timestamp")
    available_tools: list[str] = Field(default_factory=list, description="Available tool names")
    mcps: dict[str, dict] = Field(default_factory=dict, description="Assigned MCP configurations")


class RunnerCreateRequest(BaseModel):
    """Request to create a new runner."""

    name: str = Field(description="Human-readable runner name", min_length=1, max_length=64)
    mcps: dict[str, dict] | None = Field(default=None, description="Optional MCP configurations")


class RunnerCreateResponse(BaseModel):
    """Response after creating a runner."""

    id: str = Field(description="Internal runner ID")
    name: str = Field(description="Human-readable runner name")
    token: str = Field(description="Authentication token (only shown once)")
    install_command: str = Field(description="Command to install and connect the runner")


class RunnerListResponse(BaseModel):
    """Runner list response."""

    runners: list[RunnerSummary] = Field(description="List of runners")
    total: int = Field(description="Total number of runners")


class RunnerDeleteResponse(BaseModel):
    """Response after deleting a runner."""

    deleted: bool = Field(description="Whether the runner was deleted")
    name: str = Field(description="Name of the deleted runner")
