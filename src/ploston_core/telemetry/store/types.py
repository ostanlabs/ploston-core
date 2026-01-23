"""Telemetry Store data types and records."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

# ─────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────


class ExecutionType(str, Enum):
    """Type of execution."""

    WORKFLOW = "workflow"  # Workflow execution
    DIRECT = "direct"  # Direct tool call (e.g., python_exec)


class ExecutionStatus(str, Enum):
    """Execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    """Step status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class StepType(str, Enum):
    """Step type."""

    TOOL = "tool"
    CODE = "code"


class ToolCallSource(str, Enum):
    """Where the tool call originated."""

    TOOL_STEP = "tool_step"  # From a tool step in workflow
    CODE_BLOCK = "code_block"  # From within python_exec code


# ─────────────────────────────────────────────────────────────────
# Records
# ─────────────────────────────────────────────────────────────────


@dataclass
class ErrorRecord:
    """Error information."""

    code: str
    category: str
    message: str
    detail: str | None = None
    retryable: bool = False
    step_id: str | None = None
    tool_name: str | None = None
    cause: Optional["ErrorRecord"] = None  # Cause chain (max depth 3)


@dataclass
class ToolCallRecord:
    """Individual tool call within a step."""

    call_id: str
    tool_name: str
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    params: dict[str, Any] | None = None  # May be redacted
    result: dict[str, Any] | None = None  # May be redacted
    error: ErrorRecord | None = None
    execution_id: str = ""
    step_id: str = ""
    source: ToolCallSource = ToolCallSource.TOOL_STEP
    sequence: int = 0  # Order within step


@dataclass
class StepRecord:
    """Workflow step execution."""

    step_id: str
    step_type: StepType
    status: StepStatus = StepStatus.PENDING
    skip_reason: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None

    # For tool steps
    tool_name: str | None = None
    tool_params: dict[str, Any] | None = None  # May be redacted
    tool_result: dict[str, Any] | None = None  # May be redacted

    # For code steps
    code_hash: str | None = None  # SHA256, never actual code

    # Tool calls within this step
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    # Error
    error: ErrorRecord | None = None

    # Retry info
    attempt: int = 1
    max_attempts: int = 1


@dataclass
class ExecutionMetrics:
    """Aggregated execution metrics."""

    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    skipped_steps: int = 0
    total_tool_calls: int = 0
    tool_call_breakdown: dict[str, int] = field(default_factory=dict)
    total_duration_ms: int = 0
    step_durations_ms: dict[str, int] = field(default_factory=dict)


@dataclass
class ExecutionRecord:
    """Complete execution record."""

    # Identity
    execution_id: str
    execution_type: ExecutionType

    # For workflow executions
    workflow_id: str | None = None
    workflow_version: str | None = None

    # For direct executions
    tool_name: str | None = None

    # Status
    status: ExecutionStatus = ExecutionStatus.PENDING

    # Timing
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None

    # Inputs/Outputs (may be redacted)
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)

    # Error
    error: ErrorRecord | None = None

    # Steps
    steps: list[StepRecord] = field(default_factory=list)

    # Metadata
    source: str = "mcp"  # "mcp" | "rest" | "cli"
    caller_id: str | None = None
    tenant_id: str | None = None  # Premium
    session_id: str | None = None  # Premium: for pattern mining

    # Metrics
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)

    # Logs (in-memory only, not persisted to DB)
    logs: list[dict[str, Any]] = field(default_factory=list)
