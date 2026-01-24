"""AEL Mode Manager - tracks configuration/running mode."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .redis_store import RedisConfigStore

logger = logging.getLogger(__name__)


class Mode(Enum):
    """AEL operating mode."""

    CONFIGURATION = "configuration"
    RUNNING = "running"


class ModeManager:
    """
    Tracks AEL operating mode and notifies on changes.

    AEL operates in one of two mutually exclusive modes:
    - CONFIGURATION: Only config tools available, no workflows
    - RUNNING: All tools and workflows available

    Mode transitions:
    - CONFIGURATION -> RUNNING: via config_done (validates + connects MCP)
    - RUNNING -> CONFIGURATION: via ael:configure

    Optionally persists mode to Redis for recovery after restart.
    """

    def __init__(
        self,
        initial_mode: Mode = Mode.CONFIGURATION,
        redis_store: RedisConfigStore | None = None,
    ):
        """Initialize mode manager.

        Args:
            initial_mode: Initial operating mode (default: CONFIGURATION)
            redis_store: Optional RedisConfigStore for mode persistence
        """
        self._mode = initial_mode
        self._redis_store = redis_store
        self._on_change_callbacks: list[Callable[[Mode], None]] = []
        self._running_workflow_count = 0

    @property
    def mode(self) -> Mode:
        """Get current operating mode.

        Returns:
            Current Mode
        """
        return self._mode

    def is_configuration_mode(self) -> bool:
        """Check if in configuration mode.

        Returns:
            True if in CONFIGURATION mode
        """
        return self._mode == Mode.CONFIGURATION

    def is_running_mode(self) -> bool:
        """Check if in running mode.

        Returns:
            True if in RUNNING mode
        """
        return self._mode == Mode.RUNNING

    def set_mode(self, mode: Mode) -> None:
        """Change mode and notify callbacks.

        Args:
            mode: New mode to set
        """
        if mode != self._mode:
            self._mode = mode

            # Persist to Redis if available (fire and forget)
            if self._redis_store and self._redis_store.connected:
                try:
                    # Use asyncio to run the coroutine
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(self._persist_mode_to_redis(mode))
                    else:
                        loop.run_until_complete(self._persist_mode_to_redis(mode))
                except Exception as e:
                    logger.warning(f"Failed to persist mode to Redis: {e}")

            for callback in self._on_change_callbacks:
                try:
                    callback(mode)
                except Exception:
                    # Don't let callback errors break mode transition
                    pass

    async def _persist_mode_to_redis(self, mode: Mode) -> None:
        """Persist mode to Redis.

        Args:
            mode: Mode to persist
        """
        if self._redis_store:
            await self._redis_store.set_mode(mode.value.upper())

    async def restore_mode_from_redis(self) -> bool:
        """Restore mode from Redis.

        Returns:
            True if mode was restored, False otherwise
        """
        if not self._redis_store or not self._redis_store.connected:
            return False

        try:
            stored_mode = await self._redis_store.get_mode()
            if stored_mode:
                mode = Mode.RUNNING if stored_mode == "RUNNING" else Mode.CONFIGURATION
                self._mode = mode
                logger.info(f"Restored mode from Redis: {mode.value}")
                return True
        except Exception as e:
            logger.warning(f"Failed to restore mode from Redis: {e}")

        return False

    def on_mode_change(self, callback: Callable[[Mode], None]) -> None:
        """Register callback for mode changes.

        Callback receives the new mode when mode changes.

        Args:
            callback: Function to call when mode changes
        """
        self._on_change_callbacks.append(callback)

    def remove_mode_change_callback(self, callback: Callable[[Mode], None]) -> bool:
        """Remove a registered callback.

        Args:
            callback: Callback to remove

        Returns:
            True if callback was found and removed
        """
        try:
            self._on_change_callbacks.remove(callback)
            return True
        except ValueError:
            return False

    def increment_running_workflows(self) -> None:
        """Increment count of running workflows.

        Called when a workflow starts execution.
        """
        self._running_workflow_count += 1

    def decrement_running_workflows(self) -> None:
        """Decrement count of running workflows.

        Called when a workflow completes execution.
        """
        self._running_workflow_count = max(0, self._running_workflow_count - 1)

    @property
    def running_workflow_count(self) -> int:
        """Get count of currently running workflows.

        Returns:
            Number of workflows currently executing
        """
        return self._running_workflow_count

    def can_start_workflow(self) -> bool:
        """Check if new workflows can be started.

        Workflows can only start in RUNNING mode.

        Returns:
            True if workflows can be started
        """
        return self._mode == Mode.RUNNING
