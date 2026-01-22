"""Types for workflow engine."""

import asyncio
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar

from ploston_core.errors import create_error
from ploston_core.types import (
    BackoffType,
    ExecutionStatus,
    OnError,
    RetryConfig,
    StepOutput,
    StepStatus,
)

T = TypeVar("T")


@dataclass
class StepResult:
    """Result of a single step execution."""

    step_id: str
    status: StepStatus

    # Timing
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None

    # Output (for completed steps)
    output: Any = None

    # Error info (for failed/skipped steps)
    error: Any = None  # AELError
    skip_reason: str | None = None

    # Retry info
    attempt: int = 1
    max_attempts: int = 1

    def to_step_output(self) -> StepOutput:
        """Convert to StepOutput for template/sandbox context."""
        return StepOutput(
            output=self.output,
            success=self.status == StepStatus.COMPLETED,
            duration_ms=self.duration_ms or 0,
            step_id=self.step_id,
        )


@dataclass
class ExecutionResult:
    """
    Result of a workflow execution.

    Note: This is the workflow-level result, different from
    CodeExecutionResult in the sandbox (code-level result).
    """

    # Identity
    execution_id: str
    workflow_id: str
    workflow_version: str

    # Status
    status: ExecutionStatus

    # Timing
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None

    # Inputs/Outputs
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)

    # Steps
    steps: list[StepResult] = field(default_factory=list)

    # Error (if failed)
    error: Any = None  # AELError

    # Summary
    steps_completed: int = 0
    steps_failed: int = 0
    steps_skipped: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses."""
        return {
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_ms": self.duration_ms,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "steps": [
                {
                    "step_id": s.step_id,
                    "status": s.status.value,
                    "duration_ms": s.duration_ms,
                    "output": s.output,
                    "error": str(s.error) if s.error else None,
                }
                for s in self.steps
            ],
            "error": str(self.error) if self.error else None,
            "steps_completed": self.steps_completed,
            "steps_failed": self.steps_failed,
            "steps_skipped": self.steps_skipped,
        }

    def to_mcp_response(self) -> dict[str, Any]:
        """Format for MCP tools/call response."""
        return {
            "execution_id": self.execution_id,
            "status": self.status.value,
            "outputs": self.outputs,
            "error": str(self.error) if self.error else None,
        }


@dataclass
class ExecutionContext:
    """
    Runtime context for workflow execution.

    Contains inputs, accumulated step outputs,
    and configuration.
    """

    execution_id: str
    workflow: Any  # WorkflowDefinition
    inputs: dict[str, Any]
    config: dict[str, Any]

    # Accumulated step outputs (as StepOutput for template/sandbox use)
    step_outputs: dict[str, StepOutput] = field(default_factory=dict)
    step_results: dict[str, StepResult] = field(default_factory=dict)

    def add_step_result(self, result: StepResult) -> None:
        """Add a step result to the context."""
        self.step_results[result.step_id] = result
        self.step_outputs[result.step_id] = result.to_step_output()

    def get_template_context(self) -> Any:  # TemplateContext
        """Get context for template rendering."""
        from ploston_core.template.types import TemplateContext

        return TemplateContext(
            inputs=self.inputs,
            steps=self.step_outputs,
            config=self.config,
            execution_id=self.execution_id,
        )


@dataclass
class StepExecutionConfig:
    """Effective configuration for a step execution."""

    timeout_seconds: int
    on_error: OnError
    retry: RetryConfig | None = None


# Helper functions


def generate_execution_id() -> str:
    """Generate a unique execution ID."""
    return f"exec-{uuid.uuid4().hex[:12]}"


async def with_timeout[T](coro: Awaitable[T], timeout_seconds: int) -> T:
    """Execute a coroutine with a timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except TimeoutError as err:
        raise create_error(
            "EXECUTION_TIMEOUT",
            timeout_seconds=timeout_seconds,
        ) from err


def calculate_retry_delay(attempt: int, config: RetryConfig) -> float:
    """Calculate delay before next retry attempt."""
    if config.backoff == BackoffType.FIXED:
        return config.delay_seconds

    # Exponential backoff: delay = initial * (2 ^ (attempt - 1))
    delay: float = config.delay_seconds * (2 ** (attempt - 1))

    return delay
