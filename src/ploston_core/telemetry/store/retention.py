"""Retention Manager - Periodic cleanup of old telemetry records."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from .base import TelemetryStore
from .config import RetentionConfig

logger = logging.getLogger(__name__)


class RetentionManager:
    """Manages retention policy for telemetry records.

    Periodically deletes records older than the retention period.
    """

    def __init__(
        self,
        store: TelemetryStore,
        config: RetentionConfig,
    ) -> None:
        """Initialize retention manager.

        Args:
            store: Telemetry store to manage
            config: Retention configuration
        """
        self._store = store
        self._config = config
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Start the retention cleanup task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._cleanup_loop())
        logger.info(
            "Retention manager started (retention=%d days, interval=%d seconds)",
            self._config.retention_days,
            self._config.cleanup_interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the retention cleanup task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Retention manager stopped")

    async def _cleanup_loop(self) -> None:
        """Periodic cleanup loop."""
        while self._running:
            try:
                await self._cleanup()
            except Exception as e:
                logger.error("Retention cleanup failed: %s", e)

            try:
                await asyncio.sleep(self._config.cleanup_interval_seconds)
            except asyncio.CancelledError:
                break

    async def _cleanup(self) -> None:
        """Perform cleanup of old records."""
        cutoff = datetime.now(UTC) - timedelta(days=self._config.retention_days)
        deleted = await self._store.delete_before(cutoff)

        if deleted > 0:
            logger.info(
                "Retention cleanup: deleted %d records older than %s",
                deleted,
                cutoff.isoformat(),
            )

    async def cleanup_now(self) -> int:
        """Perform immediate cleanup.

        Returns:
            Number of records deleted
        """
        cutoff = datetime.now(UTC) - timedelta(days=self._config.retention_days)
        return await self._store.delete_before(cutoff)
