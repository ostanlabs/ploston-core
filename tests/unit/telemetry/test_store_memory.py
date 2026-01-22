"""Tests for in-memory telemetry store."""

from datetime import datetime, timedelta, timezone

import pytest

from ploston_core.telemetry.store.memory import MemoryTelemetryStore
from ploston_core.telemetry.store.types import (
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
def store() -> MemoryTelemetryStore:
    """Create a memory store for testing."""
    return MemoryTelemetryStore(max_records=10)


@pytest.fixture
def sample_record() -> ExecutionRecord:
    """Create a sample execution record."""
    return ExecutionRecord(
        execution_id="exec-123",
        execution_type=ExecutionType.WORKFLOW,
        workflow_id="wf-1",
        status=ExecutionStatus.COMPLETED,
        started_at=datetime.now(timezone.utc),
        inputs={"key": "value"},
    )


class TestMemoryTelemetryStore:
    """Test MemoryTelemetryStore."""

    @pytest.mark.asyncio
    async def test_save_and_get(
        self, store: MemoryTelemetryStore, sample_record: ExecutionRecord
    ) -> None:
        """Test saving and retrieving a record."""
        await store.save_execution(sample_record)
        result = await store.get_execution("exec-123")

        assert result is not None
        assert result.execution_id == "exec-123"
        assert result.workflow_id == "wf-1"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store: MemoryTelemetryStore) -> None:
        """Test getting a nonexistent record."""
        result = await store.get_execution("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_record(
        self, store: MemoryTelemetryStore, sample_record: ExecutionRecord
    ) -> None:
        """Test updating an existing record."""
        await store.save_execution(sample_record)

        sample_record.status = ExecutionStatus.FAILED
        await store.save_execution(sample_record)

        result = await store.get_execution("exec-123")
        assert result is not None
        assert result.status == ExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_lru_eviction(self, store: MemoryTelemetryStore) -> None:
        """Test LRU eviction when max records exceeded."""
        # Add 15 records to a store with max 10
        for i in range(15):
            record = ExecutionRecord(
                execution_id=f"exec-{i}",
                execution_type=ExecutionType.WORKFLOW,
                started_at=datetime.now(timezone.utc),
            )
            await store.save_execution(record)

        # First 5 should be evicted
        for i in range(5):
            result = await store.get_execution(f"exec-{i}")
            assert result is None

        # Last 10 should still exist
        for i in range(5, 15):
            result = await store.get_execution(f"exec-{i}")
            assert result is not None

    @pytest.mark.asyncio
    async def test_list_executions(self, store: MemoryTelemetryStore) -> None:
        """Test listing executions."""
        for i in range(5):
            record = ExecutionRecord(
                execution_id=f"exec-{i}",
                execution_type=ExecutionType.WORKFLOW,
                workflow_id="wf-1",
                started_at=datetime.now(timezone.utc) - timedelta(hours=i),
            )
            await store.save_execution(record)

        records, total = await store.list_executions()
        assert total == 5
        assert len(records) == 5

    @pytest.mark.asyncio
    async def test_list_with_filter(self, store: MemoryTelemetryStore) -> None:
        """Test listing with filters."""
        for i in range(5):
            record = ExecutionRecord(
                execution_id=f"exec-{i}",
                execution_type=ExecutionType.WORKFLOW if i < 3 else ExecutionType.DIRECT,
                started_at=datetime.now(timezone.utc),
            )
            await store.save_execution(record)

        records, total = await store.list_executions(
            execution_type=ExecutionType.WORKFLOW
        )
        assert total == 3

    @pytest.mark.asyncio
    async def test_list_pagination(self, store: MemoryTelemetryStore) -> None:
        """Test pagination."""
        for i in range(5):
            record = ExecutionRecord(
                execution_id=f"exec-{i}",
                execution_type=ExecutionType.WORKFLOW,
                started_at=datetime.now(timezone.utc) - timedelta(hours=i),
            )
            await store.save_execution(record)

        records, total = await store.list_executions(page=1, page_size=2)
        assert total == 5
        assert len(records) == 2

    @pytest.mark.asyncio
    async def test_delete_execution(
        self, store: MemoryTelemetryStore, sample_record: ExecutionRecord
    ) -> None:
        """Test deleting an execution."""
        await store.save_execution(sample_record)
        deleted = await store.delete_execution("exec-123")
        assert deleted is True

        result = await store.get_execution("exec-123")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, store: MemoryTelemetryStore) -> None:
        """Test deleting a nonexistent record."""
        deleted = await store.delete_execution("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_delete_before(self, store: MemoryTelemetryStore) -> None:
        """Test deleting records before a cutoff."""
        now = datetime.now(timezone.utc)
        for i in range(5):
            record = ExecutionRecord(
                execution_id=f"exec-{i}",
                execution_type=ExecutionType.WORKFLOW,
                started_at=now - timedelta(days=i),
            )
            await store.save_execution(record)

        cutoff = now - timedelta(days=2)
        deleted = await store.delete_before(cutoff)
        assert deleted == 2  # exec-3 and exec-4

        records, total = await store.list_executions()
        assert total == 3

