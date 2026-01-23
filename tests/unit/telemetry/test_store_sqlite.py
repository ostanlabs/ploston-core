"""Tests for SQLite telemetry store."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ploston_core.telemetry.store.sqlite import SQLiteTelemetryStore
from ploston_core.telemetry.store.types import (
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


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Create a temporary database path."""
    return str(tmp_path / "test_telemetry.db")


@pytest.fixture
def store(db_path: str) -> SQLiteTelemetryStore:
    """Create a SQLite store for testing."""
    return SQLiteTelemetryStore(db_path=db_path)


@pytest.fixture
def sample_record() -> ExecutionRecord:
    """Create a sample execution record with steps and tool calls."""
    now = datetime.now(UTC)
    return ExecutionRecord(
        execution_id="exec-123",
        execution_type=ExecutionType.WORKFLOW,
        workflow_id="wf-1",
        workflow_version="1.0.0",
        status=ExecutionStatus.COMPLETED,
        started_at=now,
        completed_at=now + timedelta(seconds=5),
        duration_ms=5000,
        inputs={"key": "value"},
        outputs={"result": "success"},
        source="mcp",
        caller_id="caller-1",
        metrics=ExecutionMetrics(total_steps=1, completed_steps=1),
        steps=[
            StepRecord(
                step_id="step-1",
                step_type=StepType.TOOL,
                status=StepStatus.COMPLETED,
                started_at=now,
                completed_at=now + timedelta(seconds=2),
                duration_ms=2000,
                tool_name="file_read",
                tool_params={"path": "/tmp/test.txt"},
                tool_result={"content": "hello"},
                tool_calls=[
                    ToolCallRecord(
                        call_id="call-1",
                        tool_name="file_read",
                        started_at=now,
                        completed_at=now + timedelta(seconds=1),
                        duration_ms=1000,
                        params={"path": "/tmp/test.txt"},
                        result={"content": "hello"},
                        execution_id="exec-123",
                        step_id="step-1",
                        source=ToolCallSource.TOOL_STEP,
                        sequence=0,
                    )
                ],
            )
        ],
    )


class TestSQLiteTelemetryStore:
    """Test SQLiteTelemetryStore."""

    @pytest.mark.asyncio
    async def test_save_and_get(
        self, store: SQLiteTelemetryStore, sample_record: ExecutionRecord
    ) -> None:
        """Test saving and retrieving a record."""
        await store.save_execution(sample_record)
        result = await store.get_execution("exec-123")

        assert result is not None
        assert result.execution_id == "exec-123"
        assert result.workflow_id == "wf-1"
        assert result.status == ExecutionStatus.COMPLETED
        assert len(result.steps) == 1
        assert result.steps[0].step_id == "step-1"
        assert len(result.steps[0].tool_calls) == 1

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store: SQLiteTelemetryStore) -> None:
        """Test getting a nonexistent record."""
        result = await store.get_execution("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_executions(
        self, store: SQLiteTelemetryStore, sample_record: ExecutionRecord
    ) -> None:
        """Test listing executions."""
        await store.save_execution(sample_record)

        records, total = await store.list_executions()
        assert total == 1
        assert len(records) == 1
        assert records[0].execution_id == "exec-123"

    @pytest.mark.asyncio
    async def test_delete_execution(
        self, store: SQLiteTelemetryStore, sample_record: ExecutionRecord
    ) -> None:
        """Test deleting an execution."""
        await store.save_execution(sample_record)
        deleted = await store.delete_execution("exec-123")
        assert deleted is True

        result = await store.get_execution("exec-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_before(self, store: SQLiteTelemetryStore) -> None:
        """Test deleting records before a cutoff."""
        now = datetime.now(UTC)
        for i in range(5):
            record = ExecutionRecord(
                execution_id=f"exec-{i}",
                execution_type=ExecutionType.WORKFLOW,
                started_at=now - timedelta(days=i),
            )
            await store.save_execution(record)

        cutoff = now - timedelta(days=2)
        deleted = await store.delete_before(cutoff)
        assert deleted == 2

    @pytest.mark.asyncio
    async def test_tool_call_stats(
        self, store: SQLiteTelemetryStore, sample_record: ExecutionRecord
    ) -> None:
        """Test getting tool call statistics."""
        await store.save_execution(sample_record)

        stats = await store.get_tool_call_stats()
        assert "file_read" in stats
        assert stats["file_read"]["total"] == 1
        assert stats["file_read"]["success"] == 1

    @pytest.mark.asyncio
    async def test_close(self, store: SQLiteTelemetryStore) -> None:
        """Test closing the store."""
        await store.close()
        # Should not raise
