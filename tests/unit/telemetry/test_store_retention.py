"""Tests for retention manager."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ploston_core.telemetry.store.config import RetentionConfig
from ploston_core.telemetry.store.memory import MemoryTelemetryStore
from ploston_core.telemetry.store.retention import RetentionManager
from ploston_core.telemetry.store.types import ExecutionRecord, ExecutionType


@pytest.fixture
def store() -> MemoryTelemetryStore:
    """Create a memory store for testing."""
    return MemoryTelemetryStore(max_records=100)


@pytest.fixture
def config() -> RetentionConfig:
    """Create a retention config for testing."""
    return RetentionConfig(
        retention_days=7,
        cleanup_interval_seconds=1,  # Short interval for testing
    )


@pytest.fixture
def manager(
    store: MemoryTelemetryStore, config: RetentionConfig
) -> RetentionManager:
    """Create a retention manager for testing."""
    return RetentionManager(store=store, config=config)


class TestRetentionManager:
    """Test RetentionManager."""

    @pytest.mark.asyncio
    async def test_cleanup_now(
        self, manager: RetentionManager, store: MemoryTelemetryStore
    ) -> None:
        """Test immediate cleanup."""
        now = datetime.now(timezone.utc)

        # Add old and new records
        for i in range(10):
            record = ExecutionRecord(
                execution_id=f"exec-{i}",
                execution_type=ExecutionType.WORKFLOW,
                started_at=now - timedelta(days=i),
            )
            await store.save_execution(record)

        # Cleanup should delete records older than 7 days
        deleted = await manager.cleanup_now()
        assert deleted == 3  # exec-7, exec-8, exec-9

        records, total = await store.list_executions()
        assert total == 7

    @pytest.mark.asyncio
    async def test_start_stop(self, manager: RetentionManager) -> None:
        """Test starting and stopping the manager."""
        await manager.start()
        assert manager._running is True
        assert manager._task is not None

        await manager.stop()
        assert manager._running is False

    @pytest.mark.asyncio
    async def test_cleanup_loop(
        self, manager: RetentionManager, store: MemoryTelemetryStore
    ) -> None:
        """Test that cleanup loop runs periodically."""
        now = datetime.now(timezone.utc)

        # Add an old record
        record = ExecutionRecord(
            execution_id="old-exec",
            execution_type=ExecutionType.WORKFLOW,
            started_at=now - timedelta(days=10),
        )
        await store.save_execution(record)

        # Start manager and wait for cleanup
        await manager.start()
        await asyncio.sleep(1.5)  # Wait for at least one cleanup cycle
        await manager.stop()

        # Old record should be deleted
        result = await store.get_execution("old-exec")
        assert result is None

    @pytest.mark.asyncio
    async def test_double_start(self, manager: RetentionManager) -> None:
        """Test that double start is safe."""
        await manager.start()
        await manager.start()  # Should not raise
        assert manager._running is True
        await manager.stop()

    @pytest.mark.asyncio
    async def test_double_stop(self, manager: RetentionManager) -> None:
        """Test that double stop is safe."""
        await manager.start()
        await manager.stop()
        await manager.stop()  # Should not raise
        assert manager._running is False

