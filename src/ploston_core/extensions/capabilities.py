"""Capabilities system for Ploston.

Provides server capabilities information for tier detection.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from ploston_core.extensions.feature_flags import FeatureFlagRegistry
from ploston_core.extensions.plugins import PluginRegistry


@dataclass
class Capabilities:
    """
    Server capabilities response.

    Returned by GET /api/v1/capabilities endpoint.
    """

    tier: str  # "community" or "enterprise"
    version: str
    features: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    license: dict[str, Any] | None = None  # Enterprise only

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON response."""
        result = {
            "tier": self.tier,
            "version": self.version,
            "features": self.features,
            "limits": self.limits,
        }
        if self.license is not None:
            result["license"] = self.license
        return result


class CapabilitiesProvider(Protocol):
    """
    Protocol for capabilities provider.

    OSS provides CommunityCapabilitiesProvider.
    Enterprise provides EnterpriseCapabilitiesProvider.
    """

    def get_capabilities(self) -> Capabilities:
        """Return server capabilities."""
        ...


class DefaultCapabilitiesProvider:
    """
    Default (community) capabilities provider.

    Used when no override is registered.
    """

    def __init__(self, version: str = "1.0.0") -> None:
        self._version = version

    def get_capabilities(self) -> Capabilities:
        flags = FeatureFlagRegistry.flags()
        plugins = PluginRegistry.get().get_enabled_features()

        return Capabilities(
            tier="community",
            version=self._version,
            features={
                "workflows": flags.workflows,
                "mcp": flags.mcp,
                "rest_api": flags.rest_api,
                "plugins": plugins,
                "policy": flags.policy,
                "patterns": flags.patterns,
                "synthesis": flags.synthesis,
                "parallel_execution": flags.parallel_execution,
            },
            limits={
                "max_concurrent_executions": flags.max_concurrent_executions,
                "max_workflows": flags.max_workflows,
                "telemetry_retention_days": flags.telemetry_retention_days,
            },
        )


# Global provider (can be overridden by Enterprise)
_capabilities_provider: CapabilitiesProvider | None = None


def get_capabilities_provider() -> CapabilitiesProvider:
    """Get the current capabilities provider."""
    global _capabilities_provider
    if _capabilities_provider is None:
        _capabilities_provider = DefaultCapabilitiesProvider()
    return _capabilities_provider


def set_capabilities_provider(provider: CapabilitiesProvider) -> None:
    """Set the capabilities provider (called by tier package at startup)."""
    global _capabilities_provider
    _capabilities_provider = provider


def reset_capabilities_provider() -> None:
    """Reset the capabilities provider (for testing)."""
    global _capabilities_provider
    _capabilities_provider = None
