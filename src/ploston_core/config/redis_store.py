"""Redis-based configuration store for Ploston.

This module provides centralized configuration storage using Redis with
pub/sub notifications for reactive config updates between services.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ServiceConfigPayload(BaseModel):
    """Payload stored in Redis for a service configuration."""

    version: int = Field(ge=1, description="Monotonically increasing version number")
    updated_at: datetime = Field(description="Timestamp of last update")
    updated_by: str = Field(description="Instance ID that made the update")
    config: dict[str, Any] = Field(description="The actual configuration data")


class ConfigChangeNotification(BaseModel):
    """Message published to notify of config changes."""

    type: str = Field(default="config_updated", description="Notification type")
    service: str = Field(description="Service name that was updated")
    version: int = Field(description="New version number")
    updated_at: datetime = Field(description="Timestamp of update")
    updated_by: str = Field(description="Instance ID that made the update")


@dataclass
class RedisConfigStoreOptions:
    """Options for RedisConfigStore."""

    redis_url: str = field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
    key_prefix: str = field(
        default_factory=lambda: os.getenv("REDIS_CONFIG_PREFIX", "ploston:config")
    )
    channel: str = field(
        default_factory=lambda: os.getenv("REDIS_CONFIG_CHANNEL", "ploston:config:changed")
    )
    instance_id: str = field(
        default_factory=lambda: f"ploston-{os.getpid()}"
    )
    connect_timeout: float = 5.0
    socket_timeout: float = 5.0


class RedisConfigStore:
    """Redis-based configuration store with pub/sub support.

    This class handles:
    - Writing configuration to Redis
    - Publishing change notifications
    - Reading configuration from Redis
    - Atomic version increments

    Example:
        store = RedisConfigStore()
        if await store.connect():
            await store.publish_config("native-tools", {"kafka": {"enabled": True}})
            config = await store.get_config("native-tools")
    """

    def __init__(self, options: RedisConfigStoreOptions | None = None):
        """Initialize the Redis config store.

        Args:
            options: Configuration options. Uses defaults from environment if not provided.
        """
        self._options = options or RedisConfigStoreOptions()
        self._client: Any | None = None  # redis.asyncio.Redis
        self._connected = False
        self._instance_id = self._options.instance_id

    @property
    def connected(self) -> bool:
        """Return whether the store is connected to Redis."""
        return self._connected

    async def connect(self) -> bool:
        """Connect to Redis.

        Returns:
            True if connection successful, False otherwise.
        """
        if self._connected:
            return True

        try:
            import redis.asyncio as redis

            self._client = redis.from_url(
                self._options.redis_url,
                socket_connect_timeout=self._options.connect_timeout,
                socket_timeout=self._options.socket_timeout,
                decode_responses=True,
            )

            # Test connection
            await self._client.ping()
            self._connected = True
            logger.info(
                f"Connected to Redis at {self._sanitize_url(self._options.redis_url)}"
            )
            return True

        except ImportError:
            logger.error("redis package not installed. Install with: pip install redis>=5.0.0")
            return False
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        if self._client:
            try:
                await self._client.aclose()
            except Exception as e:
                logger.warning(f"Error closing Redis connection: {e}")
            finally:
                self._client = None
                self._connected = False
                logger.info("Disconnected from Redis")

    # Alias for disconnect
    close = disconnect

    async def publish_config(self, service: str, config: dict[str, Any]) -> bool:
        """Publish configuration for a service to Redis.

        This atomically:
        1. Increments the version counter
        2. Writes the config payload
        3. Publishes a change notification

        Args:
            service: Service name (e.g., "native-tools", "ploston")
            config: Configuration dictionary to store

        Returns:
            True if successful, False otherwise.
        """
        if not self._connected or not self._client:
            logger.error("Cannot publish config: not connected to Redis")
            return False

        try:
            config_key = f"{self._options.key_prefix}:{service}"
            version_key = f"{config_key}:version"

            # Increment version atomically
            version = await self._client.incr(version_key)

            # Build payload
            now = datetime.now(UTC)
            payload = ServiceConfigPayload(
                version=version,
                updated_at=now,
                updated_by=self._instance_id,
                config=config,
            )

            # Write config
            await self._client.set(config_key, payload.model_dump_json())

            # Publish notification
            notification = ConfigChangeNotification(
                service=service,
                version=version,
                updated_at=now,
                updated_by=self._instance_id,
            )
            await self._client.publish(
                self._options.channel, notification.model_dump_json()
            )

            logger.info(f"Published config for {service} (version {version})")
            return True

        except Exception as e:
            logger.error(f"Failed to publish config for {service}: {e}")
            return False

    async def get_config(self, service: str) -> ServiceConfigPayload | None:
        """Read configuration for a service from Redis.

        Args:
            service: Service name to read config for

        Returns:
            ServiceConfigPayload if found, None otherwise.
        """
        if not self._connected or not self._client:
            logger.warning("Cannot get config: not connected to Redis")
            return None

        try:
            key = f"{self._options.key_prefix}:{service}"
            data = await self._client.get(key)
            if data:
                return ServiceConfigPayload.model_validate_json(data)
            return None
        except Exception as e:
            logger.error(f"Failed to get config for {service}: {e}")
            return None

    async def get_mode(self) -> str | None:
        """Get current mode from Redis.

        Returns:
            Mode string ("CONFIGURATION" or "RUNNING") if set, None otherwise.
        """
        if not self._connected or not self._client:
            return None

        try:
            key = f"{self._options.key_prefix.rsplit(':', 1)[0]}:mode"
            return await self._client.get(key)
        except Exception as e:
            logger.error(f"Failed to get mode: {e}")
            return None

    async def set_mode(self, mode: str) -> bool:
        """Set mode in Redis.

        Args:
            mode: Mode string ("CONFIGURATION" or "RUNNING")

        Returns:
            True if successful, False otherwise.
        """
        if not self._connected or not self._client:
            logger.error("Cannot set mode: not connected to Redis")
            return False

        try:
            key = f"{self._options.key_prefix.rsplit(':', 1)[0]}:mode"
            await self._client.set(key, mode)
            # Also publish mode change notification
            await self._client.publish(
                f"{self._options.key_prefix.rsplit(':', 1)[0]}:mode:changed", mode
            )
            logger.info(f"Set mode to {mode}")
            return True
        except Exception as e:
            logger.error(f"Failed to set mode: {e}")
            return False

    async def delete_config(self, service: str) -> bool:
        """Delete configuration for a service from Redis.

        Args:
            service: Service name to delete config for

        Returns:
            True if successful, False otherwise.
        """
        if not self._connected or not self._client:
            return False

        try:
            config_key = f"{self._options.key_prefix}:{service}"
            version_key = f"{config_key}:version"
            await self._client.delete(config_key, version_key)
            logger.info(f"Deleted config for {service}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete config for {service}: {e}")
            return False

    async def list_services(self) -> list[str]:
        """List all services with stored configurations.

        Returns:
            List of service names.
        """
        if not self._connected or not self._client:
            return []

        try:
            pattern = f"{self._options.key_prefix}:*"
            keys = []
            async for key in self._client.scan_iter(match=pattern):
                # Filter out version keys
                if not key.endswith(":version"):
                    service = key.replace(f"{self._options.key_prefix}:", "")
                    keys.append(service)
            return keys
        except Exception as e:
            logger.error(f"Failed to list services: {e}")
            return []

    @staticmethod
    def _sanitize_url(url: str) -> str:
        """Remove password from URL for logging."""
        if "@" in url and ":" in url.split("@")[0]:
            parts = url.split("@")
            return f"{parts[0].rsplit(':', 1)[0]}:***@{parts[1]}"
        return url
