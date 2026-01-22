"""Built-in AEL plugins.

This module exports the built-in plugins and the BUILTIN_PLUGINS registry.
"""

from .logging import LoggingPlugin
from .metrics import MetricsPlugin

# Registry of builtin plugins by name
BUILTIN_PLUGINS: dict[str, type] = {
    "logging": LoggingPlugin,
    "metrics": MetricsPlugin,
}

__all__ = ["LoggingPlugin", "MetricsPlugin", "BUILTIN_PLUGINS"]
