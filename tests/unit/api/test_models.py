"""Tests for REST API Pydantic models."""

from datetime import datetime, timezone

import pytest

from ploston_core.api.models import (
    ErrorDetail,
    ErrorResponse,
    ExecuteRequest,
    ExecutionDetail,
    ExecutionStatus,
    HealthCheck,
    HealthStatus,
    StepSummary,
    ToolCallRequest,
    ToolCallResponse,
    ToolDetail,
    ToolSource,
    ToolSummary,
    ValidationError,
    ValidationResult,
    WorkflowCreateResponse,
    WorkflowDetail,
    WorkflowListResponse,
    WorkflowStatus,
    WorkflowSummary,
)


class TestHealthModels:
    """Tests for health-related models."""

    def test_health_check(self) -> None:
        """Test HealthCheck model."""
        check = HealthCheck(
            status=HealthStatus.HEALTHY,
            checks={"registry": "ok", "tools": "ok"},
            timestamp=datetime.now(timezone.utc),
        )
        assert check.status == HealthStatus.HEALTHY
        assert check.checks["registry"] == "ok"

    def test_health_status_enum(self) -> None:
        """Test HealthStatus enum values."""
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"
        assert HealthStatus.DEGRADED.value == "degraded"


class TestErrorModels:
    """Tests for error models."""

    def test_error_detail(self) -> None:
        """Test ErrorDetail model."""
        error = ErrorDetail(
            code="TOOL_NOT_FOUND",
            category="TOOL",
            message="Tool not found",
            detail="The tool 'foo' does not exist",
            suggestion="Check available tools with /tools endpoint",
        )
        assert error.code == "TOOL_NOT_FOUND"
        assert error.category == "TOOL"

    def test_error_response(self) -> None:
        """Test ErrorResponse model."""
        response = ErrorResponse(
            error=ErrorDetail(
                code="VALIDATION_ERROR",
                category="VALIDATION",
                message="Invalid input",
            )
        )
        assert response.error.code == "VALIDATION_ERROR"


class TestWorkflowModels:
    """Tests for workflow models."""

    def test_workflow_summary(self) -> None:
        """Test WorkflowSummary model."""
        now = datetime.now(timezone.utc)
        summary = WorkflowSummary(
            id="test-workflow",
            name="test-workflow",
            version="1.0.0",
            description="A test workflow",
            status=WorkflowStatus.ACTIVE,
            tags=["test", "example"],
            inputs=["input1", "input2"],
            created_at=now,
            updated_at=now,
        )
        assert summary.id == "test-workflow"
        assert summary.status == WorkflowStatus.ACTIVE
        assert len(summary.tags) == 2

    def test_workflow_status_enum(self) -> None:
        """Test WorkflowStatus enum values."""
        assert WorkflowStatus.ACTIVE.value == "active"
        assert WorkflowStatus.INACTIVE.value == "inactive"

    def test_validation_result(self) -> None:
        """Test ValidationResult model."""
        result = ValidationResult(
            valid=False,
            errors=[ValidationError(path="steps[0]", message="Missing tool")],
            warnings=[ValidationError(path="inputs", message="No inputs defined")],
        )
        assert result.valid is False
        assert len(result.errors) == 1
        assert len(result.warnings) == 1


class TestExecutionModels:
    """Tests for execution models."""

    def test_execute_request(self) -> None:
        """Test ExecuteRequest model."""
        request = ExecuteRequest(inputs={"name": "test", "count": 5})
        assert request.inputs["name"] == "test"
        assert request.inputs["count"] == 5

    def test_execute_request_empty(self) -> None:
        """Test ExecuteRequest with no inputs."""
        request = ExecuteRequest()
        assert request.inputs == {}

    def test_execution_status_enum(self) -> None:
        """Test ExecutionStatus enum values."""
        assert ExecutionStatus.PENDING.value == "pending"
        assert ExecutionStatus.RUNNING.value == "running"
        assert ExecutionStatus.COMPLETED.value == "completed"
        assert ExecutionStatus.FAILED.value == "failed"
        assert ExecutionStatus.CANCELLED.value == "cancelled"

    def test_step_summary(self) -> None:
        """Test StepSummary model."""
        now = datetime.now(timezone.utc)
        step = StepSummary(
            id="step-1",
            tool="read_file",
            type="tool",
            status=ExecutionStatus.COMPLETED,
            started_at=now,
            completed_at=now,
            duration_ms=150,
        )
        assert step.id == "step-1"
        assert step.tool == "read_file"
        assert step.duration_ms == 150


class TestToolModels:
    """Tests for tool models."""

    def test_tool_source_enum(self) -> None:
        """Test ToolSource enum values."""
        assert ToolSource.MCP.value == "mcp"
        assert ToolSource.HTTP.value == "http"
        assert ToolSource.SYSTEM.value == "system"

    def test_tool_summary(self) -> None:
        """Test ToolSummary model."""
        summary = ToolSummary(
            name="read_file",
            source=ToolSource.MCP,
            server="native-tools",
            description="Read a file from disk",
        )
        assert summary.name == "read_file"
        assert summary.source == ToolSource.MCP

    def test_tool_call_request(self) -> None:
        """Test ToolCallRequest model."""
        request = ToolCallRequest(params={"path": "/tmp/test.txt"})
        assert request.params["path"] == "/tmp/test.txt"

    def test_tool_call_response(self) -> None:
        """Test ToolCallResponse model."""
        response = ToolCallResponse(
            tool_name="read_file",
            duration_ms=50,
            result={"content": "Hello, World!"},
        )
        assert response.tool_name == "read_file"
        assert response.duration_ms == 50

