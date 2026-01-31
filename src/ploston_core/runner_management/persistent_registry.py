"""Persistent Runner Registry with Redis storage.

Implements S-187: Runner Registry Redis Integration
- Persists runner data (id, name, token_hash, created_at, mcps) to Redis
- Keeps runtime state (status, last_seen, available_tools) in-memory
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from .registry import (
    Runner,
    RunnerRegistry,
    RunnerStatus,
)

if TYPE_CHECKING:
    from ploston_core.config.redis_store import RedisConfigStore

logger = logging.getLogger(__name__)

# Redis key prefix for runners
RUNNERS_KEY_PREFIX = "runners"


class PersistentRunnerRegistry(RunnerRegistry):
    """Runner registry with Redis persistence.

    Extends RunnerRegistry to persist runner data to Redis while keeping
    runtime state (status, last_seen, available_tools) in-memory.

    Key structure: ploston:config:runners:<name> -> {id, name, token_hash, created_at, mcps}
    """

    def __init__(self, config_store: RedisConfigStore) -> None:
        """Initialize the persistent registry.

        Args:
            config_store: Redis config store for persistence
        """
        super().__init__()
        self._config_store = config_store
        self._loaded = False

    @property
    def config_store(self) -> RedisConfigStore:
        """Get the config store."""
        return self._config_store

    async def load_from_redis(self) -> int:
        """Load all runners from Redis into memory.

        Returns:
            Number of runners loaded
        """
        if not self._config_store.connected:
            logger.warning("Cannot load runners: Redis not connected")
            return 0

        try:
            # List all runner keys
            services = await self._config_store.list_services()
            runner_services = [s for s in services if s.startswith(f"{RUNNERS_KEY_PREFIX}:")]

            count = 0
            for service in runner_services:
                payload = await self._config_store.get_config(service)
                if payload and payload.config:
                    runner = self._runner_from_dict(payload.config)
                    if runner:
                        self._runners[runner.id] = runner
                        self._name_to_id[runner.name] = runner.id
                        self._token_to_id[runner.token_hash] = runner.id
                        count += 1

            self._loaded = True
            logger.info(f"Loaded {count} runners from Redis")
            return count

        except Exception as e:
            logger.error(f"Failed to load runners from Redis: {e}")
            return 0

    def _runner_from_dict(self, data: dict) -> Runner | None:
        """Create a Runner from a dictionary."""
        try:
            created_at = data.get("created_at")
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)

            return Runner(
                id=data["id"],
                name=data["name"],
                created_at=created_at,
                token_hash=data.get("token_hash", ""),
                mcps=data.get("mcps", {}),
                # Runtime state defaults
                status=RunnerStatus.DISCONNECTED,
                last_seen=None,
                available_tools=[],
            )
        except (KeyError, ValueError) as e:
            logger.error(f"Invalid runner data: {e}")
            return None

    def _runner_to_persistent_dict(self, runner: Runner) -> dict:
        """Convert runner to dict for Redis (persistent fields only)."""
        return {
            "id": runner.id,
            "name": runner.name,
            "token_hash": runner.token_hash,
            "created_at": runner.created_at.isoformat(),
            "mcps": runner.mcps,
        }

    async def _persist_runner(self, runner: Runner) -> bool:
        """Persist a runner to Redis."""
        if not self._config_store.connected:
            logger.warning(f"Cannot persist runner {runner.name}: Redis not connected")
            return False

        service_key = f"{RUNNERS_KEY_PREFIX}:{runner.name}"
        data = self._runner_to_persistent_dict(runner)
        return await self._config_store.publish_config(service_key, data)

    async def _delete_runner_from_redis(self, name: str) -> bool:
        """Delete a runner from Redis."""
        if not self._config_store.connected:
            return False

        service_key = f"{RUNNERS_KEY_PREFIX}:{name}"
        return await self._config_store.delete_config(service_key)

    async def create_async(
        self, name: str, mcps: dict[str, dict] | None = None
    ) -> tuple[Runner, str]:
        """Create a new runner with Redis persistence.

        Args:
            name: Human-readable runner name
            mcps: Optional MCP configurations

        Returns:
            Tuple of (Runner, token) - token is only returned once

        Raises:
            ValueError: If name already exists
        """
        # Use parent's create for in-memory
        runner, token = self.create(name, mcps)

        # Persist to Redis
        await self._persist_runner(runner)

        return runner, token

    async def delete_async(self, runner_id: str) -> bool:
        """Delete a runner with Redis persistence."""
        runner = self._runners.get(runner_id)
        if not runner:
            return False

        # Delete from Redis first
        await self._delete_runner_from_redis(runner.name)

        # Then delete from memory
        return self.delete(runner_id)

    async def delete_by_name_async(self, name: str) -> bool:
        """Delete a runner by name with Redis persistence."""
        runner_id = self._name_to_id.get(name)
        if runner_id:
            return await self.delete_async(runner_id)
        return False
