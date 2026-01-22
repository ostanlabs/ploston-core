"""Feature flags for Ploston.

OSS sets defaults, Enterprise overrides with licensed features.
"""

from dataclasses import dataclass, field


@dataclass
class FeatureFlags:
    """
    Feature availability flags.
    
    OSS sets defaults, Enterprise overrides with licensed features.
    """
    
    # Core features (always enabled)
    workflows: bool = True
    mcp: bool = True
    rest_api: bool = True
    
    # Premium features (disabled in OSS)
    policy: bool = False
    patterns: bool = False
    synthesis: bool = False
    parallel_execution: bool = False
    compensation_steps: bool = False
    human_approval: bool = False
    
    # Limits
    max_concurrent_executions: int = 10
    max_workflows: int | None = None  # None = unlimited
    telemetry_retention_days: int = 7
    
    # Plugins
    enabled_plugins: list[str] = field(default_factory=lambda: ["logging", "metrics"])


class FeatureFlagRegistry:
    """
    Singleton for feature flag access.
    
    Tier packages set flags at startup.
    """
    
    _instance: "FeatureFlagRegistry | None" = None
    _flags: FeatureFlags | None = None
    
    @classmethod
    def get(cls) -> "FeatureFlagRegistry":
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._flags = FeatureFlags()  # OSS defaults
        return cls._instance
    
    @classmethod
    def reset(cls) -> None:
        """Reset the registry (for testing)."""
        cls._instance = None
    
    @classmethod
    def set_flags(cls, flags: FeatureFlags) -> None:
        """Set feature flags (called by tier package at startup)."""
        cls.get()._flags = flags
    
    @classmethod
    def flags(cls) -> FeatureFlags:
        """Get current feature flags."""
        return cls.get()._flags or FeatureFlags()
    
    @classmethod
    def is_enabled(cls, feature: str) -> bool:
        """Check if a feature is enabled."""
        flags = cls.flags()
        return getattr(flags, feature, False)
    
    @classmethod
    def get_limit(cls, limit: str) -> int | None:
        """Get a limit value."""
        flags = cls.flags()
        return getattr(flags, limit, None)


# Convenience functions
def is_feature_enabled(feature: str) -> bool:
    """Check if a feature is enabled."""
    return FeatureFlagRegistry.is_enabled(feature)


def get_feature_flags() -> FeatureFlags:
    """Get current feature flags."""
    return FeatureFlagRegistry.flags()

