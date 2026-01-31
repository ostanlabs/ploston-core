"""Tests for PersistentRunnerRegistry.

Implements S-187: Runner Registry Redis Integration
- UT-106: PersistentRunnerRegistry initialization
- UT-107: load_from_redis
- UT-108: create_async with persistence
- UT-109: delete_async with persistence
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ploston_core.config.redis_store import ServiceConfigPayload
from ploston_core.runner_management.persistent_registry import (
    RUNNERS_KEY_PREFIX,
    PersistentRunnerRegistry,
)
from ploston_core.runner_management.registry import RunnerStatus


@pytest.fixture
def mock_config_store():
    """Create a mock RedisConfigStore."""
    store = MagicMock()
    store.connected = True
    store.list_services = AsyncMock(return_value=[])
    store.get_config = AsyncMock(return_value=None)
    store.publish_config = AsyncMock(return_value=True)
    store.delete_config = AsyncMock(return_value=True)
    return store


@pytest.fixture
def registry(mock_config_store):
    """Create a PersistentRunnerRegistry with mock store."""
    return PersistentRunnerRegistry(mock_config_store)


class TestInitialization:
    """Tests for PersistentRunnerRegistry initialization (UT-106)."""

    def test_init_with_config_store(self, mock_config_store):
        """Test initialization with config store."""
        registry = PersistentRunnerRegistry(mock_config_store)
        assert registry.config_store is mock_config_store
        assert registry._loaded is False

    def test_inherits_from_runner_registry(self, registry):
        """Test that it inherits from RunnerRegistry."""
        from ploston_core.runner_management.registry import RunnerRegistry
        assert isinstance(registry, RunnerRegistry)


class TestLoadFromRedis:
    """Tests for load_from_redis (UT-107)."""

    @pytest.mark.asyncio
    async def test_load_empty(self, registry, mock_config_store):
        """Test loading when no runners exist."""
        mock_config_store.list_services.return_value = []
        
        count = await registry.load_from_redis()
        
        assert count == 0
        assert registry._loaded is True

    @pytest.mark.asyncio
    async def test_load_runners(self, registry, mock_config_store):
        """Test loading existing runners."""
        mock_config_store.list_services.return_value = [
            f"{RUNNERS_KEY_PREFIX}:runner1",
            f"{RUNNERS_KEY_PREFIX}:runner2",
            "other:service",  # Should be ignored
        ]
        
        runner1_data = {
            "id": "runner_abc123",
            "name": "runner1",
            "token_hash": "hash1",
            "created_at": "2026-01-31T10:00:00+00:00",
            "mcps": {},
        }
        runner2_data = {
            "id": "runner_def456",
            "name": "runner2",
            "token_hash": "hash2",
            "created_at": "2026-01-31T11:00:00+00:00",
            "mcps": {"mcp1": {"url": "http://localhost"}},
        }
        
        async def get_config_side_effect(service):
            if service == f"{RUNNERS_KEY_PREFIX}:runner1":
                return ServiceConfigPayload(
                    version=1,
                    updated_at=datetime.now(timezone.utc),
                    updated_by="test",
                    config=runner1_data,
                )
            elif service == f"{RUNNERS_KEY_PREFIX}:runner2":
                return ServiceConfigPayload(
                    version=1,
                    updated_at=datetime.now(timezone.utc),
                    updated_by="test",
                    config=runner2_data,
                )
            return None
        
        mock_config_store.get_config.side_effect = get_config_side_effect
        
        count = await registry.load_from_redis()
        
        assert count == 2
        assert registry.get_by_name("runner1") is not None
        assert registry.get_by_name("runner2") is not None
        
        # Verify runtime state is reset
        runner1 = registry.get_by_name("runner1")
        assert runner1.status == RunnerStatus.DISCONNECTED
        assert runner1.last_seen is None
        assert runner1.available_tools == []

    @pytest.mark.asyncio
    async def test_load_not_connected(self, registry, mock_config_store):
        """Test loading when Redis not connected."""
        mock_config_store.connected = False
        
        count = await registry.load_from_redis()
        
        assert count == 0


class TestCreateAsync:
    """Tests for create_async (UT-108)."""

    @pytest.mark.asyncio
    async def test_create_persists_to_redis(self, registry, mock_config_store):
        """Test that create_async persists to Redis."""
        runner, token = await registry.create_async("test-runner")
        
        # Verify in-memory
        assert registry.get_by_name("test-runner") is not None
        
        # Verify Redis call
        mock_config_store.publish_config.assert_called_once()
        call_args = mock_config_store.publish_config.call_args
        assert call_args[0][0] == f"{RUNNERS_KEY_PREFIX}:test-runner"
        
        # Verify persisted data
        persisted_data = call_args[0][1]
        assert persisted_data["id"] == runner.id
        assert persisted_data["name"] == "test-runner"
        assert "token_hash" in persisted_data
        assert "created_at" in persisted_data

    @pytest.mark.asyncio
    async def test_create_with_mcps(self, registry, mock_config_store):
        """Test creating runner with MCPs."""
        mcps = {"mcp1": {"url": "http://localhost:8080"}}
        runner, token = await registry.create_async("test-runner", mcps=mcps)
        
        # Verify MCPs persisted
        call_args = mock_config_store.publish_config.call_args
        persisted_data = call_args[0][1]
        assert persisted_data["mcps"] == mcps

    @pytest.mark.asyncio
    async def test_create_duplicate_raises(self, registry, mock_config_store):
        """Test that creating duplicate raises ValueError."""
        await registry.create_async("test-runner")
        
        with pytest.raises(ValueError, match="already exists"):
            await registry.create_async("test-runner")


class TestDeleteAsync:
    """Tests for delete_async (UT-109)."""

    @pytest.mark.asyncio
    async def test_delete_removes_from_redis(self, registry, mock_config_store):
        """Test that delete_async removes from Redis."""
        runner, _ = await registry.create_async("test-runner")
        mock_config_store.publish_config.reset_mock()
        
        result = await registry.delete_async(runner.id)
        
        assert result is True
        assert registry.get_by_name("test-runner") is None
        mock_config_store.delete_config.assert_called_once_with(
            f"{RUNNERS_KEY_PREFIX}:test-runner"
        )

    @pytest.mark.asyncio
    async def test_delete_by_name_async(self, registry, mock_config_store):
        """Test delete_by_name_async."""
        await registry.create_async("test-runner")
        mock_config_store.publish_config.reset_mock()
        
        result = await registry.delete_by_name_async("test-runner")
        
        assert result is True
        assert registry.get_by_name("test-runner") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, registry, mock_config_store):
        """Test deleting nonexistent runner."""
        result = await registry.delete_async("nonexistent")
        
        assert result is False
        mock_config_store.delete_config.assert_not_called()
