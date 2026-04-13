"""Adapter to translate TelemetryStore records to REST API models.

Bridges the gap between the canonical TelemetryStore types
(ExecutionRecord, StepRecord) and the REST API Pydantic models
(ExecutionDetail, ExecutionSummary, StepSummary).

Created as part of DEC-148 (retire ExecutionStore).
"""

from __future__ import annotations

from ploston_core.api.models import (
    ErrorDetail,
    ExecutionDetail,
    ExecutionStatus,
    ExecutionSummary,
    StepSummary,
)
from ploston_core.telemetry.store.types import (
    ErrorRecord,
    ExecutionRecord,
    StepRecord,
)


def to_execution_detail(record: ExecutionRecord) -> ExecutionDetail:
    """Convert a TelemetryStore ExecutionRecord to a REST API ExecutionDetail.

    Args:
        record: Telemetry store execution record.

    Returns:
        REST API execution detail model.
    """
    return ExecutionDetail(
        execution_id=record.execution_id,
        workflow_id=record.workflow_id or record.tool_name or "direct",
        status=ExecutionStatus(record.status.value),
        started_at=record.started_at,
        completed_at=record.completed_at,
        duration_ms=record.duration_ms,
        inputs=record.inputs,
        outputs=record.outputs,
        error=_to_error_detail(record.error),
        steps=[_to_step_summary(s) for s in record.steps],
        runner_id=record.runner_id,  # DEC-145
        bridge_session_id=record.bridge_session_id,  # DEC-145
    )


def to_execution_summary(record: ExecutionRecord) -> ExecutionSummary:
    """Convert a TelemetryStore ExecutionRecord to a REST API ExecutionSummary.

    Args:
        record: Telemetry store execution record.

    Returns:
        REST API execution summary model.
    """
    return ExecutionSummary(
        execution_id=record.execution_id,
        workflow_id=record.workflow_id or record.tool_name or "direct",
        status=ExecutionStatus(record.status.value),
        started_at=record.started_at,
        completed_at=record.completed_at,
        duration_ms=record.duration_ms,
    )


def _to_step_summary(step: StepRecord) -> StepSummary:
    """Convert a TelemetryStore StepRecord to a REST API StepSummary.

    Args:
        step: Telemetry store step record.

    Returns:
        REST API step summary model.
    """
    return StepSummary(
        id=step.step_id,
        tool=step.tool_name,
        type=step.step_type.value,
        status=ExecutionStatus(step.status.value),
        started_at=step.started_at,
        completed_at=step.completed_at,
        duration_ms=step.duration_ms,
        error=step.error.message if step.error else None,
    )


def _to_error_detail(error: ErrorRecord | None) -> ErrorDetail | None:
    """Convert a TelemetryStore ErrorRecord to a REST API ErrorDetail.

    Args:
        error: Telemetry store error record, or None.

    Returns:
        REST API error detail model, or None.
    """
    if error is None:
        return None
    return ErrorDetail(
        code=error.code,
        category=error.category,
        message=error.message,
    )
