"""Persistent Runner Registry with Redis storage.

Implements S-187: Runner Registry Redis Integration
- Persists runner data (id, name, token_hash, created_at, mcps) to Redis
- Keeps runtime state (status, last_seen, available_tools) in-memory
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

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

    def __init__(
        self, config_store: RedisConfigStore, config_file_path: str | Path | None = None
    ) -> None:
        """Initialize the persistent registry.

        Args:
            config_store: Redis config store for persistence
            config_file_path: Optional path to config file for immediate updates on delete
        """
        super().__init__()
        self._config_store = config_store
        self._config_file_path = Path(config_file_path) if config_file_path else None
        self._loaded = False

    def set_config_file_path(self, path: str | Path) -> None:
        """Set the config file path for immediate updates on delete."""
        self._config_file_path = Path(path)

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
        """Delete a runner with Redis persistence and config file update."""
        runner = self._runners.get(runner_id)
        if not runner:
            return False

        runner_name = runner.name

        # Delete from Redis first
        await self._delete_runner_from_redis(runner_name)

        # Delete from memory
        deleted = self.delete(runner_id)

        # Update config file to remove the runner
        if deleted and self._config_file_path:
            self._remove_runner_from_config_file(runner_name)

        return deleted

    async def delete_by_name_async(self, name: str) -> bool:
        """Delete a runner by name with Redis persistence and config file update."""
        runner_id = self._name_to_id.get(name)
        if runner_id:
            return await self.delete_async(runner_id)
        return False

    def _remove_runner_from_config_file(self, runner_name: str) -> bool:
        """Remove a runner from the config file.

        Args:
            runner_name: Name of the runner to remove

        Returns:
            True if config file was updated, False otherwise
        """
        if not self._config_file_path or not self._config_file_path.exists():
            logger.warning(
                "Cannot update config file: path not set or file doesn't exist"
            )
            return False

        try:
            # Read current config
            with self._config_file_path.open("r") as f:
                config = yaml.safe_load(f) or {}

            # Remove runner from config
            runners = config.get("runners", {})
            if runner_name not in runners:
                logger.debug(f"Runner '{runner_name}' not in config file, nothing to remove")
                return True

            del runners[runner_name]

            # If runners section is now empty, remove it entirely
            if not runners:
                del config["runners"]
            else:
                config["runners"] = runners

            # Write updated config
            with self._config_file_path.open("w") as f:
                f.write("# ael-config.yaml - Generated by AEL\n")
                f.write("# All values shown, defaults included for reference\n\n")
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)

            logger.info(f"Removed runner '{runner_name}' from config file")
            return True

        except Exception as e:
            logger.error(f"Failed to update config file: {e}")
            return False

    async def sync_from_config(
        self, runners_config: dict[str, dict]
    ) -> dict[str, dict]:
        """Sync runners from config file definition.

        Creates runners defined in config that don't exist yet.
        Does NOT delete runners that are not in config (that's a separate operation).

        Args:
            runners_config: Dict of runner name -> runner definition from config
                           Each definition has 'mcp_servers' key

        Returns:
            Dict of runner name -> {created: bool, token: str | None}
            Token is only returned for newly created runners
        """
        results: dict[str, dict] = {}

        for name, definition in runners_config.items():
            # Check if runner already exists
            existing = self.get_by_name(name)
            if existing:
                results[name] = {"created": False, "token": None}
                logger.debug(f"Runner '{name}' already exists, skipping")
                continue

            # Convert mcp_servers to mcps format expected by registry
            # Config uses RunnerMCPServerDefinition, registry uses dict
            mcps = {}
            mcp_servers = definition.get("mcp_servers", {})
            for mcp_name, mcp_def in mcp_servers.items():
                # mcp_def could be a dataclass or dict
                if hasattr(mcp_def, "__dict__"):
                    # It's a dataclass, convert to dict
                    mcps[mcp_name] = {
                        k: v
                        for k, v in mcp_def.__dict__.items()
                        if v is not None and v != {} and v != []
                    }
                else:
                    mcps[mcp_name] = mcp_def

            # Create the runner
            try:
                runner, token = await self.create_async(name, mcps=mcps if mcps else None)
                results[name] = {"created": True, "token": token}
                logger.info(f"Created runner '{name}' from config")
            except ValueError as e:
                logger.error(f"Failed to create runner '{name}': {e}")
                results[name] = {"created": False, "token": None, "error": str(e)}

        return results

    async def get_token(self, name: str) -> str | None:
        """Get the token for a runner.

        Note: This retrieves the token from Redis storage.
        The token is stored encrypted/hashed, so we need a separate
        mechanism to store the actual token for retrieval.

        For now, this returns None as tokens are only shown once at creation.
        A future enhancement could store encrypted tokens in Redis.
        """
        # Tokens are not stored in retrievable form for security
        # Use regenerate_token to get a new token
        return None

    async def regenerate_token(self, name: str) -> str | None:
        """Regenerate the token for a runner.

        Args:
            name: Runner name

        Returns:
            New token, or None if runner not found
        """
        from .registry import generate_runner_token, hash_token

        runner = self.get_by_name(name)
        if not runner:
            return None

        # Generate new token
        new_token = generate_runner_token()
        new_hash = hash_token(new_token)

        # Update in-memory
        old_hash = runner.token_hash
        runner.token_hash = new_hash

        # Update token index
        if old_hash in self._token_to_id:
            del self._token_to_id[old_hash]
        self._token_to_id[new_hash] = runner.id

        # Persist to Redis
        await self._persist_runner(runner)

        logger.info(f"Regenerated token for runner '{name}'")
        return new_token
