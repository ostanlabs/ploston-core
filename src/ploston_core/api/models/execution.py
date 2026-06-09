"""Execution REST API models."""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

from .common import ErrorDetail


class ExecutionStatus(str, Enum):
    """Execution status enum.

    Serves both the workflow-level execution status and (via ``StepSummary``)
    individual step statuses. ``SKIPPED`` is a step-only value (the core
    ``StepStatus`` enum can mark a step skipped) and is included here so step
    statuses round-trip through the REST API faithfully instead of raising a
    ``ValueError`` at the response boundary (BUG R-6).
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ExecuteRequest(BaseModel):
    """Workflow execution request."""

    inputs: dict[str, Any] = {}


class StepSummary(BaseModel):
    """Step execution summary."""

    id: str
    tool: str | None = None
    type: str | None = None  # "tool" or "code"
    status: ExecutionStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    error: str | None = None


class ExecutionSummary(BaseModel):
    """Execution summary for list response."""

    execution_id: str
    workflow_id: str
    status: ExecutionStatus
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None


class ExecutionDetail(BaseModel):
    """Full execution details."""

    execution_id: str
    workflow_id: str
    status: ExecutionStatus
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    error: ErrorDetail | None = None
    steps: list[StepSummary] = []
    runner_id: str | None = None  # DEC-145
    bridge_session_id: str | None = None  # DEC-145


class ExecutionListResponse(BaseModel):
    """Paginated execution list."""

    executions: list[ExecutionSummary]
    total: int
    page: int = 1
    page_size: int = 20
    has_next: bool
    has_prev: bool


class LogEntry(BaseModel):
    """Single log entry."""

    timestamp: datetime
    level: str
    component: str
    step_id: str | None = None
    tool_name: str | None = None
    message: str


class ExecutionLogsResponse(BaseModel):
    """Execution logs response."""

    execution_id: str
    logs: list[LogEntry]
