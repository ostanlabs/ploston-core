"""AEL Plugin Framework.

This module provides the plugin framework for extending AEL functionality.

Usage:
    from ploston_core.plugins import AELPlugin, PluginRegistry
    from ploston_core.plugins.types import RequestContext, StepContext

    class MyPlugin(AELPlugin):
        name = "my-plugin"

        def on_request_received(self, context: RequestContext) -> RequestContext:
            # Modify or inspect the request
            return context

    # Load plugins from config
    registry = PluginRegistry()
    registry.load_plugins(config.plugins)

    # Execute hooks
    result = registry.execute_request_received(context)
"""

from .base import AELPlugin
from .registry import PluginLoadResult, PluginRegistry
from .types import (
    HookResult,
    PluginDecision,
    RequestContext,
    ResponseContext,
    StepContext,
    StepResultContext,
)

__all__ = [
    # Base class
    "AELPlugin",
    # Registry
    "PluginRegistry",
    "PluginLoadResult",
    # Types
    "PluginDecision",
    "HookResult",
    "RequestContext",
    "StepContext",
    "StepResultContext",
    "ResponseContext",
]
