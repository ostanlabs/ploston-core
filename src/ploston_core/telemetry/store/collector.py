"""Telemetry Collector - Event collector for execution telemetry."""

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from .base import TelemetryStore
from .config import TelemetryStoreConfig
from .redactor import Redactor
from .types import (
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


class TelemetryCollector:
    """Collects execution telemetry and persists to store.

    Provides lifecycle methods for tracking:
    - Execution start/end
    - Step start/end
    - Tool call start/end
    """

    def __init__(
        self,
        store: TelemetryStore,
        config: TelemetryStoreConfig,
    ) -> None:
        """Initialize collector.

        Args:
            store: Telemetry store for persistence
            config: Configuration
        """
        self._store = store
        self._config = config
        self._redactor = Redactor(config.redaction)
        self._active_executions: dict[str, ExecutionRecord] = {}

    # ─────────────────────────────────────────────────────────────────
    # Execution lifecycle
    # ─────────────────────────────────────────────────────────────────

    async def start_execution(
        self,
        execution_type: ExecutionType,
        workflow_id: str | None = None,
        workflow_version: str | None = None,
        tool_name: str | None = None,
        inputs: dict[str, Any] | None = None,
        source: str = "mcp",
        caller_id: str | None = None,
        session_id: str | None = None,
    ) -> str:
        """Start tracking a new execution.

        Args:
            execution_type: Type of execution (workflow or direct)
            workflow_id: Workflow ID (for workflow executions)
            workflow_version: Workflow version
            tool_name: Tool name (for direct executions)
            inputs: Execution inputs (will be redacted)
            source: Source of execution (mcp, rest, cli)
            caller_id: Caller identifier
            session_id: Session identifier

        Returns:
            Execution ID
        """
        execution_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        record = ExecutionRecord(
            execution_id=execution_id,
            execution_type=execution_type,
            workflow_id=workflow_id,
            workflow_version=workflow_version,
            tool_name=tool_name,
            status=ExecutionStatus.RUNNING,
            started_at=now,
            inputs=self._redactor.redact(inputs or {}),
            source=source,
            caller_id=caller_id,
            session_id=session_id,
        )

        self._active_executions[execution_id] = record
        await self._store.save_execution(record)

        return execution_id

    async def end_execution(
        self,
        execution_id: str,
        status: ExecutionStatus,
        outputs: dict[str, Any] | None = None,
        error: ErrorRecord | None = None,
    ) -> None:
        """End an execution.

        Args:
            execution_id: Execution ID
            status: Final status
            outputs: Execution outputs (will be redacted)
            error: Error record if failed
        """
        record = self._active_executions.get(execution_id)
        if not record:
            return

        now = datetime.now(UTC)
        record.status = status
        record.completed_at = now
        record.outputs = self._redactor.redact(outputs or {})
        record.error = error

        if record.started_at:
            record.duration_ms = int((now - record.started_at).total_seconds() * 1000)

        # Calculate metrics
        record.metrics = self._calculate_metrics(record)

        await self._store.save_execution(record)
        del self._active_executions[execution_id]

    # ─────────────────────────────────────────────────────────────────
    # Step lifecycle
    # ─────────────────────────────────────────────────────────────────

    async def start_step(
        self,
        execution_id: str,
        step_id: str,
        step_type: StepType,
        tool_name: str | None = None,
        tool_params: dict[str, Any] | None = None,
        code: str | None = None,
        max_attempts: int = 1,
    ) -> None:
        """Start tracking a step.

        Args:
            execution_id: Parent execution ID
            step_id: Step identifier
            step_type: Type of step
            tool_name: Tool name (for tool steps)
            tool_params: Tool parameters (will be redacted)
            code: Code content (will be hashed, not stored)
            max_attempts: Maximum retry attempts
        """
        record = self._active_executions.get(execution_id)
        if not record:
            return

        step = StepRecord(
            step_id=step_id,
            step_type=step_type,
            status=StepStatus.RUNNING,
            started_at=datetime.now(UTC),
            tool_name=tool_name,
            tool_params=self._redactor.redact(tool_params) if tool_params else None,
            code_hash=hashlib.sha256(code.encode()).hexdigest() if code else None,
            max_attempts=max_attempts,
        )

        record.steps.append(step)

    async def end_step(
        self,
        execution_id: str,
        step_id: str,
        status: StepStatus,
        tool_result: dict[str, Any] | None = None,
        error: ErrorRecord | None = None,
        skip_reason: str | None = None,
    ) -> None:
        """End a step.

        Args:
            execution_id: Parent execution ID
            step_id: Step identifier
            status: Final status
            tool_result: Tool result (will be redacted)
            error: Error record if failed
            skip_reason: Reason if skipped
        """
        record = self._active_executions.get(execution_id)
        if not record:
            return

        step = self._find_step(record, step_id)
        if not step:
            return

        now = datetime.now(UTC)
        step.status = status
        step.completed_at = now
        step.tool_result = self._redactor.redact(tool_result) if tool_result else None
        step.error = error
        step.skip_reason = skip_reason

        if step.started_at:
            step.duration_ms = int((now - step.started_at).total_seconds() * 1000)

    # ─────────────────────────────────────────────────────────────────
    # Tool call lifecycle
    # ─────────────────────────────────────────────────────────────────

    async def start_tool_call(
        self,
        execution_id: str,
        step_id: str,
        tool_name: str,
        params: dict[str, Any] | None = None,
        source: ToolCallSource = ToolCallSource.TOOL_STEP,
    ) -> str:
        """Start tracking a tool call.

        Args:
            execution_id: Parent execution ID
            step_id: Parent step ID
            tool_name: Tool being called
            params: Tool parameters (will be redacted)
            source: Where the call originated

        Returns:
            Call ID
        """
        record = self._active_executions.get(execution_id)
        if not record:
            return ""

        step = self._find_step(record, step_id)
        if not step:
            return ""

        call_id = str(uuid.uuid4())
        call = ToolCallRecord(
            call_id=call_id,
            tool_name=tool_name,
            started_at=datetime.now(UTC),
            params=self._redactor.redact(params) if params else None,
            execution_id=execution_id,
            step_id=step_id,
            source=source,
            sequence=len(step.tool_calls),
        )

        step.tool_calls.append(call)
        return call_id

    async def end_tool_call(
        self,
        execution_id: str,
        step_id: str,
        call_id: str,
        result: dict[str, Any] | None = None,
        error: ErrorRecord | None = None,
    ) -> None:
        """End a tool call.

        Args:
            execution_id: Parent execution ID
            step_id: Parent step ID
            call_id: Call identifier
            result: Tool result (will be redacted)
            error: Error record if failed
        """
        record = self._active_executions.get(execution_id)
        if not record:
            return

        step = self._find_step(record, step_id)
        if not step:
            return

        call = self._find_tool_call(step, call_id)
        if not call:
            return

        now = datetime.now(UTC)
        call.completed_at = now
        call.result = self._redactor.redact(result) if result else None
        call.error = error

        if call.started_at:
            call.duration_ms = int((now - call.started_at).total_seconds() * 1000)

    # ─────────────────────────────────────────────────────────────────
    # Helper methods
    # ─────────────────────────────────────────────────────────────────

    def _find_step(self, record: ExecutionRecord, step_id: str) -> StepRecord | None:
        """Find step by ID."""
        for step in record.steps:
            if step.step_id == step_id:
                return step
        return None

    def _find_tool_call(self, step: StepRecord, call_id: str) -> ToolCallRecord | None:
        """Find tool call by ID."""
        for call in step.tool_calls:
            if call.call_id == call_id:
                return call
        return None

    def _calculate_metrics(self, record: ExecutionRecord) -> ExecutionMetrics:
        """Calculate execution metrics."""
        metrics = ExecutionMetrics()
        metrics.total_steps = len(record.steps)
        metrics.total_duration_ms = record.duration_ms or 0

        for step in record.steps:
            if step.status == StepStatus.COMPLETED:
                metrics.completed_steps += 1
            elif step.status == StepStatus.FAILED:
                metrics.failed_steps += 1
            elif step.status == StepStatus.SKIPPED:
                metrics.skipped_steps += 1

            if step.duration_ms:
                metrics.step_durations_ms[step.step_id] = step.duration_ms

            for call in step.tool_calls:
                metrics.total_tool_calls += 1
                metrics.tool_call_breakdown[call.tool_name] = (
                    metrics.tool_call_breakdown.get(call.tool_name, 0) + 1
                )

        return metrics
