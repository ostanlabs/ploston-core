"""Extension points for Ploston packages.

This module provides the extension points that tier packages (OSS, Enterprise) hook into.
"""

from ploston_core.extensions.capabilities import (
    Capabilities,
    CapabilitiesProvider,
    DefaultCapabilitiesProvider,
    get_capabilities_provider,
    set_capabilities_provider,
)
from ploston_core.extensions.feature_flags import (
    FeatureFlagRegistry,
    FeatureFlags,
)
from ploston_core.extensions.plugins import (
    AELPlugin,
    PluginRegistry,
)

__all__ = [
    # Capabilities
    "Capabilities",
    "CapabilitiesProvider",
    "DefaultCapabilitiesProvider",
    "get_capabilities_provider",
    "set_capabilities_provider",
    # Feature Flags
    "FeatureFlagRegistry",
    "FeatureFlags",
    # Plugins
    "AELPlugin",
    "PluginRegistry",
]
