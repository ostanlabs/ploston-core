"""Tests for telemetry collector."""

import pytest

from ploston_core.telemetry.store.collector import TelemetryCollector
from ploston_core.telemetry.store.config import RedactionConfig, TelemetryStoreConfig
from ploston_core.telemetry.store.memory import MemoryTelemetryStore
from ploston_core.telemetry.store.types import (
    ExecutionStatus,
    ExecutionType,
    StepStatus,
    StepType,
)


@pytest.fixture
def store() -> MemoryTelemetryStore:
    """Create a memory store for testing."""
    return MemoryTelemetryStore(max_records=100)


@pytest.fixture
def config() -> TelemetryStoreConfig:
    """Create a config for testing."""
    return TelemetryStoreConfig(
        enabled=True,
        redaction=RedactionConfig(enabled=True, fields=["password", "secret"]),
    )


@pytest.fixture
def collector(store: MemoryTelemetryStore, config: TelemetryStoreConfig) -> TelemetryCollector:
    """Create a collector for testing."""
    return TelemetryCollector(store=store, config=config)


class TestTelemetryCollector:
    """Test TelemetryCollector."""

    @pytest.mark.asyncio
    async def test_start_execution(
        self, collector: TelemetryCollector, store: MemoryTelemetryStore
    ) -> None:
        """Test starting an execution."""
        exec_id = await collector.start_execution(
            execution_type=ExecutionType.WORKFLOW,
            workflow_id="wf-1",
            inputs={"key": "value"},
        )

        assert exec_id is not None
        record = await store.get_execution(exec_id)
        assert record is not None
        assert record.execution_type == ExecutionType.WORKFLOW
        assert record.workflow_id == "wf-1"
        assert record.status == ExecutionStatus.RUNNING

    @pytest.mark.asyncio
    async def test_end_execution(
        self, collector: TelemetryCollector, store: MemoryTelemetryStore
    ) -> None:
        """Test ending an execution."""
        exec_id = await collector.start_execution(
            execution_type=ExecutionType.WORKFLOW,
            workflow_id="wf-1",
        )

        await collector.end_execution(
            execution_id=exec_id,
            status=ExecutionStatus.COMPLETED,
            outputs={"result": "success"},
        )

        record = await store.get_execution(exec_id)
        assert record is not None
        assert record.status == ExecutionStatus.COMPLETED
        assert record.outputs == {"result": "success"}
        assert record.completed_at is not None
        assert record.duration_ms is not None

    @pytest.mark.asyncio
    async def test_step_lifecycle(
        self, collector: TelemetryCollector, store: MemoryTelemetryStore
    ) -> None:
        """Test step start and end."""
        exec_id = await collector.start_execution(
            execution_type=ExecutionType.WORKFLOW,
            workflow_id="wf-1",
        )

        await collector.start_step(
            execution_id=exec_id,
            step_id="step-1",
            step_type=StepType.TOOL,
            tool_name="file_read",
            tool_params={"path": "/tmp/test.txt"},
        )

        await collector.end_step(
            execution_id=exec_id,
            step_id="step-1",
            status=StepStatus.COMPLETED,
            tool_result={"content": "hello"},
        )

        await collector.end_execution(
            execution_id=exec_id,
            status=ExecutionStatus.COMPLETED,
        )

        record = await store.get_execution(exec_id)
        assert record is not None
        assert len(record.steps) == 1
        assert record.steps[0].step_id == "step-1"
        assert record.steps[0].status == StepStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_tool_call_lifecycle(
        self, collector: TelemetryCollector, store: MemoryTelemetryStore
    ) -> None:
        """Test tool call start and end."""
        exec_id = await collector.start_execution(
            execution_type=ExecutionType.WORKFLOW,
            workflow_id="wf-1",
        )

        await collector.start_step(
            execution_id=exec_id,
            step_id="step-1",
            step_type=StepType.TOOL,
        )

        call_id = await collector.start_tool_call(
            execution_id=exec_id,
            step_id="step-1",
            tool_name="file_read",
            params={"path": "/tmp/test.txt"},
        )

        await collector.end_tool_call(
            execution_id=exec_id,
            step_id="step-1",
            call_id=call_id,
            result={"content": "hello"},
        )

        await collector.end_step(
            execution_id=exec_id,
            step_id="step-1",
            status=StepStatus.COMPLETED,
        )

        await collector.end_execution(
            execution_id=exec_id,
            status=ExecutionStatus.COMPLETED,
        )

        record = await store.get_execution(exec_id)
        assert record is not None
        assert len(record.steps[0].tool_calls) == 1
        assert record.steps[0].tool_calls[0].tool_name == "file_read"
