"""Telemetry Store data types and records."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


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
    detail: Optional[str] = None
    retryable: bool = False
    step_id: Optional[str] = None
    tool_name: Optional[str] = None
    cause: Optional["ErrorRecord"] = None  # Cause chain (max depth 3)


@dataclass
class ToolCallRecord:
    """Individual tool call within a step."""

    call_id: str
    tool_name: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    params: Optional[Dict[str, Any]] = None  # May be redacted
    result: Optional[Dict[str, Any]] = None  # May be redacted
    error: Optional[ErrorRecord] = None
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
    skip_reason: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None

    # For tool steps
    tool_name: Optional[str] = None
    tool_params: Optional[Dict[str, Any]] = None  # May be redacted
    tool_result: Optional[Dict[str, Any]] = None  # May be redacted

    # For code steps
    code_hash: Optional[str] = None  # SHA256, never actual code

    # Tool calls within this step
    tool_calls: List[ToolCallRecord] = field(default_factory=list)

    # Error
    error: Optional[ErrorRecord] = None

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
    tool_call_breakdown: Dict[str, int] = field(default_factory=dict)
    total_duration_ms: int = 0
    step_durations_ms: Dict[str, int] = field(default_factory=dict)


@dataclass
class ExecutionRecord:
    """Complete execution record."""

    # Identity
    execution_id: str
    execution_type: ExecutionType

    # For workflow executions
    workflow_id: Optional[str] = None
    workflow_version: Optional[str] = None

    # For direct executions
    tool_name: Optional[str] = None

    # Status
    status: ExecutionStatus = ExecutionStatus.PENDING

    # Timing
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None

    # Inputs/Outputs (may be redacted)
    inputs: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)

    # Error
    error: Optional[ErrorRecord] = None

    # Steps
    steps: List[StepRecord] = field(default_factory=list)

    # Metadata
    source: str = "mcp"  # "mcp" | "rest" | "cli"
    caller_id: Optional[str] = None
    tenant_id: Optional[str] = None  # Premium
    session_id: Optional[str] = None  # Premium: for pattern mining

    # Metrics
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)

    # Logs (in-memory only, not persisted to DB)
    logs: List[Dict[str, Any]] = field(default_factory=list)
