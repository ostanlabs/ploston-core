"""Tests for execution store."""

from datetime import UTC, datetime

import pytest

from ploston_core.api.models import ExecutionDetail, ExecutionStatus, StepSummary
from ploston_core.api.store import InMemoryExecutionStore


def _create_execution(
    execution_id: str,
    workflow_id: str = "test-workflow",
    status: ExecutionStatus = ExecutionStatus.COMPLETED,
) -> ExecutionDetail:
    """Create a test execution."""
    now = datetime.now(UTC)
    return ExecutionDetail(
        execution_id=execution_id,
        workflow_id=workflow_id,
        status=status,
        inputs={"test": "input"},
        started_at=now,
        completed_at=now if status == ExecutionStatus.COMPLETED else None,
        duration_ms=100 if status == ExecutionStatus.COMPLETED else None,
        steps=[
            StepSummary(
                id="step-1",
                tool="test_tool",
                type="tool",
                status=status,
                started_at=now,
                completed_at=now if status == ExecutionStatus.COMPLETED else None,
                duration_ms=50 if status == ExecutionStatus.COMPLETED else None,
            )
        ],
    )


class TestInMemoryExecutionStore:
    """Tests for InMemoryExecutionStore."""

    @pytest.fixture
    def store(self) -> InMemoryExecutionStore:
        """Create a test store."""
        return InMemoryExecutionStore(max_records=10)

    @pytest.mark.asyncio
    async def test_save_and_get(self, store: InMemoryExecutionStore) -> None:
        """Test saving and retrieving an execution."""
        execution = _create_execution("exec-1")
        await store.save(execution)

        retrieved = await store.get("exec-1")
        assert retrieved is not None
        assert retrieved.execution_id == "exec-1"
        assert retrieved.workflow_id == "test-workflow"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store: InMemoryExecutionStore) -> None:
        """Test getting a nonexistent execution."""
        result = await store.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_all(self, store: InMemoryExecutionStore) -> None:
        """Test listing all executions."""
        for i in range(5):
            await store.save(_create_execution(f"exec-{i}"))

        executions, total = await store.list()
        assert total == 5
        assert len(executions) == 5

    @pytest.mark.asyncio
    async def test_list_filter_by_workflow(self, store: InMemoryExecutionStore) -> None:
        """Test filtering by workflow ID."""
        await store.save(_create_execution("exec-1", workflow_id="workflow-a"))
        await store.save(_create_execution("exec-2", workflow_id="workflow-b"))
        await store.save(_create_execution("exec-3", workflow_id="workflow-a"))

        executions, total = await store.list(workflow_id="workflow-a")
        assert total == 2
        assert all(e.workflow_id == "workflow-a" for e in executions)

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, store: InMemoryExecutionStore) -> None:
        """Test filtering by status."""
        await store.save(_create_execution("exec-1", status=ExecutionStatus.COMPLETED))
        await store.save(_create_execution("exec-2", status=ExecutionStatus.FAILED))
        await store.save(_create_execution("exec-3", status=ExecutionStatus.COMPLETED))

        executions, total = await store.list(status=ExecutionStatus.FAILED)
        assert total == 1
        assert executions[0].status == ExecutionStatus.FAILED

    @pytest.mark.asyncio
    async def test_list_pagination(self) -> None:
        """Test pagination."""
        # Use a larger store for pagination test
        store = InMemoryExecutionStore(max_records=20)
        for i in range(15):
            await store.save(_create_execution(f"exec-{i}"))

        # First page
        page1, total = await store.list(page=1, page_size=5)
        assert total == 15
        assert len(page1) == 5

        # Second page
        page2, _ = await store.list(page=2, page_size=5)
        assert len(page2) == 5

        # Third page
        page3, _ = await store.list(page=3, page_size=5)
        assert len(page3) == 5

        # No overlap
        ids1 = {e.execution_id for e in page1}
        ids2 = {e.execution_id for e in page2}
        ids3 = {e.execution_id for e in page3}
        assert ids1.isdisjoint(ids2)
        assert ids2.isdisjoint(ids3)

    @pytest.mark.asyncio
    async def test_lru_eviction(self) -> None:
        """Test LRU eviction when max_records is exceeded."""
        store = InMemoryExecutionStore(max_records=3)

        await store.save(_create_execution("exec-1"))
        await store.save(_create_execution("exec-2"))
        await store.save(_create_execution("exec-3"))

        # All three should exist
        assert await store.get("exec-1") is not None
        assert await store.get("exec-2") is not None
        assert await store.get("exec-3") is not None

        # Add a fourth - should evict exec-1 (oldest)
        await store.save(_create_execution("exec-4"))

        assert await store.get("exec-1") is None
        assert await store.get("exec-2") is not None
        assert await store.get("exec-3") is not None
        assert await store.get("exec-4") is not None

    @pytest.mark.asyncio
    async def test_update_existing(self, store: InMemoryExecutionStore) -> None:
        """Test updating an existing execution."""
        execution = _create_execution("exec-1", status=ExecutionStatus.RUNNING)
        await store.save(execution)

        # Update status
        execution.status = ExecutionStatus.COMPLETED
        await store.save(execution)

        retrieved = await store.get("exec-1")
        assert retrieved is not None
        assert retrieved.status == ExecutionStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_get_logs_empty(self, store: InMemoryExecutionStore) -> None:
        """Test getting logs for execution without logs."""
        await store.save(_create_execution("exec-1"))
        logs = await store.get_logs("exec-1")
        assert logs == []
