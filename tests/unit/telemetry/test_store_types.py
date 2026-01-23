"""Tests for telemetry store types."""

from datetime import UTC, datetime

from ploston_core.telemetry.store.types import (
    ErrorRecord,
    ExecutionMetrics,
    ExecutionRecord,
    ExecutionStatus,
    ExecutionType,
    StepRecord,
    StepStatus,
    StepType,
    ToolCallRecord,
    ToolCallSource,
)


class TestEnums:
    """Test enum values."""

    def test_execution_type_values(self) -> None:
        """Test ExecutionType enum values."""
        assert ExecutionType.WORKFLOW.value == "workflow"
        assert ExecutionType.DIRECT.value == "direct"

    def test_execution_status_values(self) -> None:
        """Test ExecutionStatus enum values."""
        assert ExecutionStatus.PENDING.value == "pending"
        assert ExecutionStatus.RUNNING.value == "running"
        assert ExecutionStatus.COMPLETED.value == "completed"
        assert ExecutionStatus.FAILED.value == "failed"
        assert ExecutionStatus.CANCELLED.value == "cancelled"

    def test_step_status_values(self) -> None:
        """Test StepStatus enum values."""
        assert StepStatus.PENDING.value == "pending"
        assert StepStatus.RUNNING.value == "running"
        assert StepStatus.COMPLETED.value == "completed"
        assert StepStatus.FAILED.value == "failed"
        assert StepStatus.SKIPPED.value == "skipped"

    def test_step_type_values(self) -> None:
        """Test StepType enum values."""
        assert StepType.TOOL.value == "tool"
        assert StepType.CODE.value == "code"

    def test_tool_call_source_values(self) -> None:
        """Test ToolCallSource enum values."""
        assert ToolCallSource.TOOL_STEP.value == "tool_step"
        assert ToolCallSource.CODE_BLOCK.value == "code_block"


class TestErrorRecord:
    """Test ErrorRecord dataclass."""

    def test_create_error_record(self) -> None:
        """Test creating an error record."""
        error = ErrorRecord(
            code="E001",
            category="validation",
            message="Invalid input",
            detail="Field 'name' is required",
            retryable=False,
        )
        assert error.code == "E001"
        assert error.category == "validation"
        assert error.message == "Invalid input"
        assert error.detail == "Field 'name' is required"
        assert error.retryable is False

    def test_error_record_with_cause(self) -> None:
        """Test error record with cause chain."""
        cause = ErrorRecord(code="E000", category="system", message="Root cause")
        error = ErrorRecord(
            code="E001",
            category="validation",
            message="Validation failed",
            cause=cause,
        )
        assert error.cause is not None
        assert error.cause.code == "E000"


class TestToolCallRecord:
    """Test ToolCallRecord dataclass."""

    def test_create_tool_call_record(self) -> None:
        """Test creating a tool call record."""
        now = datetime.now(UTC)
        call = ToolCallRecord(
            call_id="call-123",
            tool_name="file_read",
            started_at=now,
            params={"path": "/tmp/test.txt"},
        )
        assert call.call_id == "call-123"
        assert call.tool_name == "file_read"
        assert call.started_at == now
        assert call.params == {"path": "/tmp/test.txt"}
        assert call.source == ToolCallSource.TOOL_STEP


class TestStepRecord:
    """Test StepRecord dataclass."""

    def test_create_step_record(self) -> None:
        """Test creating a step record."""
        step = StepRecord(
            step_id="step-1",
            step_type=StepType.TOOL,
            tool_name="file_read",
        )
        assert step.step_id == "step-1"
        assert step.step_type == StepType.TOOL
        assert step.status == StepStatus.PENDING
        assert step.tool_name == "file_read"
        assert step.tool_calls == []


class TestExecutionMetrics:
    """Test ExecutionMetrics dataclass."""

    def test_create_execution_metrics(self) -> None:
        """Test creating execution metrics."""
        metrics = ExecutionMetrics(
            total_steps=5,
            completed_steps=4,
            failed_steps=1,
            total_tool_calls=10,
        )
        assert metrics.total_steps == 5
        assert metrics.completed_steps == 4
        assert metrics.failed_steps == 1
        assert metrics.total_tool_calls == 10


class TestExecutionRecord:
    """Test ExecutionRecord dataclass."""

    def test_create_execution_record(self) -> None:
        """Test creating an execution record."""
        record = ExecutionRecord(
            execution_id="exec-123",
            execution_type=ExecutionType.WORKFLOW,
            workflow_id="wf-1",
        )
        assert record.execution_id == "exec-123"
        assert record.execution_type == ExecutionType.WORKFLOW
        assert record.workflow_id == "wf-1"
        assert record.status == ExecutionStatus.PENDING
        assert record.steps == []
        assert record.source == "mcp"
