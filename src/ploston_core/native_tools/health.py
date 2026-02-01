"""Health Manager for Native Tools Dependencies.

This module provides health checking and monitoring for external dependencies
(Kafka, Ollama, Firecrawl) used by native tools.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import httpx
from prometheus_client import Gauge

logger = logging.getLogger(__name__)

# Prometheus metrics
# Status: 1=healthy, 0=unhealthy, -1=disabled
DEPENDENCY_STATUS_GAUGE = Gauge(
    "native_tools_dependency_status",
    "Health status of native-tools dependencies (1=healthy, 0=unhealthy, -1=disabled)",
    ["dependency"],
)

DEPENDENCY_LATENCY_GAUGE = Gauge(
    "native_tools_dependency_latency_seconds",
    "Latency of last health check for dependencies",
    ["dependency"],
)

TOOLS_AVAILABLE_GAUGE = Gauge(
    "native_tools_tools_available",
    "Number of tools available by status",
    ["status"],  # total, available, degraded
)


class DependencyUnavailableError(Exception):
    """Raised when a tool's dependency is unavailable.

    This error indicates that the tool cannot execute because its required
    dependency (Kafka, Ollama, or Firecrawl) is not healthy.
    """

    def __init__(self, dependency: str, message: str | None = None):
        """Initialize the error.

        Args:
            dependency: Name of the unavailable dependency
            message: Optional custom error message
        """
        self.dependency = dependency
        self.code = "DEPENDENCY_UNAVAILABLE"
        self.retryable = True
        self.message = (
            message
            or f"{dependency.title()} is not available. Check native-tools health endpoint for details."
        )
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "dependency": self.dependency,
                "retryable": self.retryable,
            }
        }


class DependencyStatus(str, Enum):
    """Status of a dependency."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DISABLED = "disabled"


class OverallStatus(str, Enum):
    """Overall health status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class DependencyHealth:
    """Health status for a single dependency."""

    status: DependencyStatus
    latency_ms: float | None = None
    last_check: datetime | None = None
    error: str | None = None
    tools_affected: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "status": self.status.value,
            "latency_ms": self.latency_ms,
            "last_check": self.last_check.isoformat() if self.last_check else None,
            "error": self.error,
            "tools_affected": self.tools_affected,
        }


@dataclass
class DependencyConfig:
    """Configuration for a dependency."""

    name: str
    enabled: bool = False
    tools_affected: list[str] = field(default_factory=list)
    # Dependency-specific config
    kafka_bootstrap_servers: str | None = None
    kafka_client_id: str | None = None
    kafka_security_protocol: str | None = None
    kafka_sasl_mechanism: str | None = None
    kafka_sasl_username: str | None = None
    kafka_sasl_password: str | None = None
    ollama_host: str | None = None
    firecrawl_base_url: str | None = None


# Tool to dependency mapping
DEPENDENCY_TOOLS = {
    "kafka": [
        "kafka_publish",
        "kafka_consume",
        "kafka_list_topics",
        "kafka_create_topic",
        "kafka_health",
    ],
    "ollama": [
        "ml_embed_text",
        "ml_text_similarity",
        "ml_classify_text",
    ],
    "firecrawl": [
        "firecrawl_search",
        "firecrawl_map",
        "firecrawl_extract",
        "firecrawl_health",
    ],
}


class HealthManager:
    """Manages health checks for native tools dependencies.

    This class:
    - Tracks health status of Kafka, Ollama, and Firecrawl
    - Runs background health checks at configurable intervals
    - Provides cached health status for the /health endpoint
    - Exposes methods to check if a dependency is healthy before tool execution
    """

    def __init__(
        self,
        check_interval: int = 30,
        check_timeout: int = 5,
    ):
        """Initialize the health manager.

        Args:
            check_interval: Seconds between background health checks
            check_timeout: Timeout in seconds for each health check
        """
        self._check_interval = check_interval
        self._check_timeout = check_timeout
        self._start_time = time.monotonic()

        # Dependency configurations
        self._configs: dict[str, DependencyConfig] = {}

        # Cached health status
        self._health: dict[str, DependencyHealth] = {}

        # Background task
        self._background_task: asyncio.Task | None = None
        self._running = False

        # Callbacks for health changes
        self._on_change_callbacks: list[Callable[[str, DependencyHealth], None]] = []

    def configure_kafka(
        self,
        bootstrap_servers: str,
        client_id: str = "health-check",
        security_protocol: str = "PLAINTEXT",
        sasl_mechanism: str | None = None,
        sasl_username: str | None = None,
        sasl_password: str | None = None,
    ) -> None:
        """Configure Kafka dependency.

        Args:
            bootstrap_servers: Kafka bootstrap servers
            client_id: Client ID for health checks
            security_protocol: Security protocol
            sasl_mechanism: SASL mechanism if using SASL
            sasl_username: SASL username
            sasl_password: SASL password
        """
        enabled = bool(bootstrap_servers and bootstrap_servers != "localhost:9092")
        self._configs["kafka"] = DependencyConfig(
            name="kafka",
            enabled=enabled,
            tools_affected=DEPENDENCY_TOOLS["kafka"],
            kafka_bootstrap_servers=bootstrap_servers,
            kafka_client_id=client_id,
            kafka_security_protocol=security_protocol,
            kafka_sasl_mechanism=sasl_mechanism,
            kafka_sasl_username=sasl_username,
            kafka_sasl_password=sasl_password,
        )
        # Initialize health status
        self._health["kafka"] = DependencyHealth(
            status=DependencyStatus.DISABLED if not enabled else DependencyStatus.UNHEALTHY,
            tools_affected=DEPENDENCY_TOOLS["kafka"],
        )

    def configure_ollama(self, host: str) -> None:
        """Configure Ollama dependency.

        Args:
            host: Ollama API host URL
        """
        enabled = bool(host and host != "http://localhost:11434")
        self._configs["ollama"] = DependencyConfig(
            name="ollama",
            enabled=enabled,
            tools_affected=DEPENDENCY_TOOLS["ollama"],
            ollama_host=host,
        )
        self._health["ollama"] = DependencyHealth(
            status=DependencyStatus.DISABLED if not enabled else DependencyStatus.UNHEALTHY,
            tools_affected=DEPENDENCY_TOOLS["ollama"],
        )

    def configure_firecrawl(self, base_url: str) -> None:
        """Configure Firecrawl dependency.

        Args:
            base_url: Firecrawl API base URL
        """
        enabled = bool(base_url and base_url != "http://localhost:3002")
        self._configs["firecrawl"] = DependencyConfig(
            name="firecrawl",
            enabled=enabled,
            tools_affected=DEPENDENCY_TOOLS["firecrawl"],
            firecrawl_base_url=base_url,
        )
        self._health["firecrawl"] = DependencyHealth(
            status=DependencyStatus.DISABLED if not enabled else DependencyStatus.UNHEALTHY,
            tools_affected=DEPENDENCY_TOOLS["firecrawl"],
        )

    def is_dependency_healthy(self, dependency: str) -> bool:
        """Check if a dependency is healthy.

        Args:
            dependency: Name of the dependency (kafka, ollama, firecrawl)

        Returns:
            True if healthy or disabled, False if unhealthy
        """
        health = self._health.get(dependency)
        if not health:
            return True  # Unknown dependency, assume OK
        return health.status in (DependencyStatus.HEALTHY, DependencyStatus.DISABLED)

    def is_dependency_enabled(self, dependency: str) -> bool:
        """Check if a dependency is enabled (configured).

        Args:
            dependency: Name of the dependency

        Returns:
            True if enabled, False otherwise
        """
        config = self._configs.get(dependency)
        return config.enabled if config else False

    def get_dependency_error(self, dependency: str) -> str | None:
        """Get the error message for an unhealthy dependency.

        Args:
            dependency: Name of the dependency

        Returns:
            Error message or None if healthy
        """
        health = self._health.get(dependency)
        return health.error if health else None

    async def check_kafka(self) -> DependencyHealth:
        """Check Kafka health."""
        config = self._configs.get("kafka")
        if not config or not config.enabled:
            return DependencyHealth(
                status=DependencyStatus.DISABLED,
                tools_affected=DEPENDENCY_TOOLS["kafka"],
            )

        start = time.monotonic()
        try:
            from kafka import KafkaAdminClient

            admin_config = {
                "bootstrap_servers": config.kafka_bootstrap_servers,
                "client_id": config.kafka_client_id,
                "security_protocol": config.kafka_security_protocol,
                "request_timeout_ms": self._check_timeout * 1000,
            }
            if config.kafka_sasl_mechanism:
                admin_config["sasl_mechanism"] = config.kafka_sasl_mechanism
            if config.kafka_sasl_username:
                admin_config["sasl_plain_username"] = config.kafka_sasl_username
            if config.kafka_sasl_password:
                admin_config["sasl_plain_password"] = config.kafka_sasl_password

            # Run in executor to avoid blocking
            loop = asyncio.get_event_loop()
            admin_client = await loop.run_in_executor(
                None, lambda: KafkaAdminClient(**admin_config)
            )
            await loop.run_in_executor(None, admin_client.list_topics)
            await loop.run_in_executor(None, admin_client.close)

            latency = (time.monotonic() - start) * 1000
            return DependencyHealth(
                status=DependencyStatus.HEALTHY,
                latency_ms=round(latency, 2),
                last_check=datetime.now(UTC),
                tools_affected=DEPENDENCY_TOOLS["kafka"],
            )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            return DependencyHealth(
                status=DependencyStatus.UNHEALTHY,
                latency_ms=round(latency, 2),
                last_check=datetime.now(UTC),
                error=str(e),
                tools_affected=DEPENDENCY_TOOLS["kafka"],
            )

    async def check_ollama(self) -> DependencyHealth:
        """Check Ollama health."""
        config = self._configs.get("ollama")
        if not config or not config.enabled:
            return DependencyHealth(
                status=DependencyStatus.DISABLED,
                tools_affected=DEPENDENCY_TOOLS["ollama"],
            )

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._check_timeout) as client:
                response = await client.get(f"{config.ollama_host}/api/tags")
                latency = (time.monotonic() - start) * 1000

                if response.status_code == 200:
                    return DependencyHealth(
                        status=DependencyStatus.HEALTHY,
                        latency_ms=round(latency, 2),
                        last_check=datetime.now(UTC),
                        tools_affected=DEPENDENCY_TOOLS["ollama"],
                    )
                else:
                    return DependencyHealth(
                        status=DependencyStatus.UNHEALTHY,
                        latency_ms=round(latency, 2),
                        last_check=datetime.now(UTC),
                        error=f"HTTP {response.status_code}",
                        tools_affected=DEPENDENCY_TOOLS["ollama"],
                    )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            return DependencyHealth(
                status=DependencyStatus.UNHEALTHY,
                latency_ms=round(latency, 2),
                last_check=datetime.now(UTC),
                error=str(e),
                tools_affected=DEPENDENCY_TOOLS["ollama"],
            )

    async def check_firecrawl(self) -> DependencyHealth:
        """Check Firecrawl health."""
        config = self._configs.get("firecrawl")
        if not config or not config.enabled:
            return DependencyHealth(
                status=DependencyStatus.DISABLED,
                tools_affected=DEPENDENCY_TOOLS["firecrawl"],
            )

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._check_timeout) as client:
                # Try common health endpoints
                for path in ["/health", "/v1/health", "/"]:
                    try:
                        response = await client.get(f"{config.firecrawl_base_url}{path}")
                        if response.status_code < 500:
                            latency = (time.monotonic() - start) * 1000
                            return DependencyHealth(
                                status=DependencyStatus.HEALTHY,
                                latency_ms=round(latency, 2),
                                last_check=datetime.now(UTC),
                                tools_affected=DEPENDENCY_TOOLS["firecrawl"],
                            )
                    except httpx.HTTPError:
                        continue

                latency = (time.monotonic() - start) * 1000
                return DependencyHealth(
                    status=DependencyStatus.UNHEALTHY,
                    latency_ms=round(latency, 2),
                    last_check=datetime.now(UTC),
                    error="No valid health endpoint responded",
                    tools_affected=DEPENDENCY_TOOLS["firecrawl"],
                )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            return DependencyHealth(
                status=DependencyStatus.UNHEALTHY,
                latency_ms=round(latency, 2),
                last_check=datetime.now(UTC),
                error=str(e),
                tools_affected=DEPENDENCY_TOOLS["firecrawl"],
            )

    def _update_prometheus_metrics(self) -> None:
        """Update Prometheus metrics based on current health status."""
        for name, health in self._health.items():
            # Update status gauge: 1=healthy, 0=unhealthy, -1=disabled
            if health.status == DependencyStatus.HEALTHY:
                DEPENDENCY_STATUS_GAUGE.labels(dependency=name).set(1)
            elif health.status == DependencyStatus.UNHEALTHY:
                DEPENDENCY_STATUS_GAUGE.labels(dependency=name).set(0)
            else:  # DISABLED
                DEPENDENCY_STATUS_GAUGE.labels(dependency=name).set(-1)

            # Update latency gauge
            if health.latency_ms is not None:
                DEPENDENCY_LATENCY_GAUGE.labels(dependency=name).set(health.latency_ms / 1000)

        # Update tool counts
        tool_counts = self.get_tool_counts()
        TOOLS_AVAILABLE_GAUGE.labels(status="total").set(tool_counts["total"])
        TOOLS_AVAILABLE_GAUGE.labels(status="available").set(tool_counts["available"])
        TOOLS_AVAILABLE_GAUGE.labels(status="degraded").set(tool_counts["degraded"])

    async def check_all(self) -> None:
        """Run health checks for all configured dependencies."""
        tasks = []
        if "kafka" in self._configs:
            tasks.append(("kafka", self.check_kafka()))
        if "ollama" in self._configs:
            tasks.append(("ollama", self.check_ollama()))
        if "firecrawl" in self._configs:
            tasks.append(("firecrawl", self.check_firecrawl()))

        for name, coro in tasks:
            try:
                health = await coro
                old_health = self._health.get(name)
                self._health[name] = health

                # Notify callbacks if status changed
                if old_health and old_health.status != health.status:
                    for callback in self._on_change_callbacks:
                        try:
                            callback(name, health)
                        except Exception as e:
                            logger.error(f"Health change callback failed: {e}")
            except Exception as e:
                logger.error(f"Health check for {name} failed: {e}")

        # Update Prometheus metrics after all checks
        self._update_prometheus_metrics()

    async def start(self) -> None:
        """Start background health checks."""
        if self._running:
            return

        self._running = True
        self._start_time = time.monotonic()

        # Run initial check
        await self.check_all()

        # Start background task
        self._background_task = asyncio.create_task(self._background_check_loop())
        logger.info(f"Health manager started with {self._check_interval}s interval")

    async def stop(self) -> None:
        """Stop background health checks."""
        self._running = False
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass
            self._background_task = None
        logger.info("Health manager stopped")

    async def _background_check_loop(self) -> None:
        """Background loop for periodic health checks."""
        while self._running:
            try:
                await asyncio.sleep(self._check_interval)
                await self.check_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Background health check failed: {e}")

    def on_change(self, callback: Callable[[str, DependencyHealth], None]) -> None:
        """Register callback for health status changes.

        Args:
            callback: Function called with (dependency_name, new_health) on changes
        """
        self._on_change_callbacks.append(callback)

    def get_overall_status(self) -> OverallStatus:
        """Get overall health status.

        Returns:
            HEALTHY if all deps healthy/disabled, DEGRADED if some unhealthy, UNHEALTHY if critical
        """
        unhealthy_count = sum(
            1 for h in self._health.values() if h.status == DependencyStatus.UNHEALTHY
        )
        if unhealthy_count == 0:
            return OverallStatus.HEALTHY
        return OverallStatus.DEGRADED

    def get_tool_counts(self) -> dict[str, int]:
        """Get tool availability counts.

        Returns:
            Dict with total, available, and degraded tool counts
        """
        total = 0
        degraded = 0
        for dep_name, health in self._health.items():
            tool_count = len(health.tools_affected)
            total += tool_count
            if health.status == DependencyStatus.UNHEALTHY:
                degraded += tool_count
        return {
            "total": total,
            "available": total - degraded,
            "degraded": degraded,
        }

    def get_health_response(self, version: str = "0.1.0") -> dict[str, Any]:
        """Get full health response for /health endpoint.

        Args:
            version: Service version string

        Returns:
            Health response dict matching the spec
        """
        uptime = time.monotonic() - self._start_time
        return {
            "status": self.get_overall_status().value,
            "timestamp": datetime.now(UTC).isoformat(),
            "version": version,
            "uptime_seconds": round(uptime, 2),
            "dependencies": {name: health.to_dict() for name, health in self._health.items()},
            "tools": self.get_tool_counts(),
        }


# Global health manager instance
_health_manager: HealthManager | None = None


def get_health_manager() -> HealthManager:
    """Get the global health manager instance.

    Returns:
        HealthManager instance
    """
    global _health_manager
    if _health_manager is None:
        _health_manager = HealthManager()
    return _health_manager


def reset_health_manager() -> None:
    """Reset the global health manager (for testing)."""
    global _health_manager
    _health_manager = None
