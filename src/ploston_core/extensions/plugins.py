"""Plugin system for Ploston.

Tier packages register their plugins at startup.
"""

from abc import ABC, abstractmethod
from typing import Any


class AELPlugin(ABC):
    """
    Base class for all plugins.

    Plugins can hook into various lifecycle events and provide
    additional functionality.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin name."""
        ...

    @property
    @abstractmethod
    def tier(self) -> str:
        """Plugin tier: 'community' or 'enterprise'."""
        ...

    async def on_startup(self) -> None:
        """Called when server starts."""
        pass

    async def on_shutdown(self) -> None:
        """Called when server stops."""
        pass

    async def on_workflow_start(self, workflow_id: str, context: dict[str, Any]) -> None:
        """Called when a workflow starts."""
        pass

    async def on_workflow_complete(
        self, workflow_id: str, result: Any, context: dict[str, Any]
    ) -> None:
        """Called when a workflow completes."""
        pass

    async def on_workflow_error(
        self, workflow_id: str, error: Exception, context: dict[str, Any]
    ) -> None:
        """Called when a workflow errors."""
        pass


class PluginRegistry:
    """
    Central registry for plugins.

    Tier packages register their plugins at startup.
    """

    _instance: "PluginRegistry | None" = None

    def __init__(self) -> None:
        self._plugins: dict[str, AELPlugin] = {}

    @classmethod
    def get(cls) -> "PluginRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the registry (for testing)."""
        cls._instance = None

    def register(self, plugin: AELPlugin) -> None:
        """Register a plugin."""
        self._plugins[plugin.name] = plugin

    def unregister(self, name: str) -> None:
        """Unregister a plugin."""
        self._plugins.pop(name, None)

    def get_plugin(self, name: str) -> AELPlugin | None:
        """Get plugin by name."""
        return self._plugins.get(name)

    def list_plugins(self, tier: str | None = None) -> list[AELPlugin]:
        """List plugins, optionally filtered by tier."""
        plugins = list(self._plugins.values())
        if tier:
            plugins = [p for p in plugins if p.tier == tier]
        return plugins

    def get_enabled_features(self) -> list[str]:
        """Get list of enabled feature names from plugins."""
        return [p.name for p in self._plugins.values()]

    async def startup_all(self) -> None:
        """Call on_startup for all plugins."""
        for plugin in self._plugins.values():
            await plugin.on_startup()

    async def shutdown_all(self) -> None:
        """Call on_shutdown for all plugins."""
        for plugin in self._plugins.values():
            await plugin.on_shutdown()
