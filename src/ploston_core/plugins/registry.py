"""Plugin registry for loading and managing plugins.

This module provides the PluginRegistry class that handles:
- Loading plugins from builtin, file, and package sources
- Managing plugin lifecycle
- Executing hook chains in priority order
"""

import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from ploston_core.config.models import PluginDefinition

from .base import AELPlugin
from .types import (
    HookResult,
    RequestContext,
    ResponseContext,
    StepContext,
    StepResultContext,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class PluginLoadResult:
    """Result of loading plugins.

    Attributes:
        loaded: List of successfully loaded plugins
        failed: List of (name, error) tuples for failed loads
    """

    loaded: list[AELPlugin] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        """Number of successfully loaded plugins."""
        return len(self.loaded)

    @property
    def failure_count(self) -> int:
        """Number of failed plugin loads."""
        return len(self.failed)


class PluginRegistry:
    """Registry for managing AEL plugins.

    Handles loading plugins from various sources and executing hook chains.

    Attributes:
        plugins: List of loaded plugins sorted by priority
    """

    def __init__(self):
        """Initialize an empty plugin registry."""
        self._plugins: list[AELPlugin] = []

    @property
    def plugins(self) -> list[AELPlugin]:
        """Get list of loaded plugins (sorted by priority)."""
        return self._plugins.copy()

    def load_plugins(self, definitions: list[PluginDefinition]) -> PluginLoadResult:
        """Load plugins from definitions.

        Args:
            definitions: List of plugin definitions from config

        Returns:
            PluginLoadResult with loaded and failed plugins
        """
        result = PluginLoadResult()

        for defn in definitions:
            if not defn.enabled:
                logger.debug(f"Skipping disabled plugin: {defn.name}")
                continue

            try:
                plugin = self._load_plugin(defn)
                if plugin:
                    # Apply config overrides
                    plugin.name = defn.name
                    plugin.priority = defn.priority
                    if hasattr(defn, "fail_open"):
                        plugin.fail_open = getattr(defn, "fail_open", True)
                    result.loaded.append(plugin)
                    logger.info(f"Loaded plugin: {defn.name} (priority={defn.priority})")
            except Exception as e:
                error_msg = str(e)
                result.failed.append((defn.name, error_msg))
                logger.error(f"Failed to load plugin {defn.name}: {error_msg}")

        # Sort by priority (lower = earlier)
        self._plugins = sorted(result.loaded, key=lambda p: p.priority)
        return result

    def _load_plugin(self, defn: PluginDefinition) -> AELPlugin | None:
        """Load a single plugin from definition.

        Args:
            defn: Plugin definition

        Returns:
            Loaded plugin instance or None
        """
        if defn.type == "builtin":
            return self._load_builtin(defn.name, defn.config)
        elif defn.type == "file":
            return self._load_from_file(defn.path, defn.config)
        elif defn.type == "package":
            return self._load_from_package(defn.package, defn.config)
        else:
            raise ValueError(f"Unknown plugin type: {defn.type}")

    def _load_builtin(self, name: str, config: dict[str, Any]) -> AELPlugin:
        """Load a builtin plugin by name."""
        from .builtin import BUILTIN_PLUGINS

        if name not in BUILTIN_PLUGINS:
            raise ValueError(f"Unknown builtin plugin: {name}")

        plugin_class = BUILTIN_PLUGINS[name]
        return plugin_class(config)

    def _load_from_file(self, path: str | None, config: dict[str, Any]) -> AELPlugin:
        """Load a plugin from a Python file."""
        if not path:
            raise ValueError("Plugin path is required for file type")

        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Plugin file not found: {path}")

        # Load module from file
        spec = importlib.util.spec_from_file_location("plugin_module", file_path)
        if not spec or not spec.loader:
            raise ImportError(f"Cannot load plugin from: {path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules["plugin_module"] = module
        spec.loader.exec_module(module)

        # Find plugin class
        plugin_class = self._find_plugin_class(module)
        if not plugin_class:
            raise ValueError(f"No AELPlugin subclass found in: {path}")

        return plugin_class(config)

    def _load_from_package(self, package: str | None, config: dict[str, Any]) -> AELPlugin:
        """Load a plugin from an installed package."""
        if not package:
            raise ValueError("Package name is required for package type")

        try:
            module = importlib.import_module(package)
        except ImportError as e:
            raise ImportError(f"Cannot import plugin package: {package}") from e

        plugin_class = self._find_plugin_class(module)
        if not plugin_class:
            raise ValueError(f"No AELPlugin subclass found in package: {package}")

        return plugin_class(config)

    def _find_plugin_class(self, module: Any) -> type[AELPlugin] | None:
        """Find AELPlugin subclass in a module."""
        for name in dir(module):
            obj = getattr(module, name)
            if (
                isinstance(obj, type)
                and issubclass(obj, AELPlugin)
                and obj is not AELPlugin
            ):
                return obj
        return None

    # Hook execution methods

    def execute_request_received(
        self, context: RequestContext
    ) -> HookResult[RequestContext]:
        """Execute on_request_received hook chain."""
        return self._execute_chain("on_request_received", context)

    def execute_step_before(self, context: StepContext) -> HookResult[StepContext]:
        """Execute on_step_before hook chain."""
        return self._execute_chain("on_step_before", context)

    def execute_step_after(
        self, context: StepResultContext
    ) -> HookResult[StepResultContext]:
        """Execute on_step_after hook chain."""
        return self._execute_chain("on_step_after", context)

    def execute_response_ready(
        self, context: ResponseContext
    ) -> HookResult[ResponseContext]:
        """Execute on_response_ready hook chain."""
        return self._execute_chain("on_response_ready", context)

    def _execute_chain(self, hook_name: str, context: T) -> HookResult[T]:
        """Execute a hook chain across all plugins.

        Args:
            hook_name: Name of the hook method
            context: Context to pass through the chain

        Returns:
            HookResult with final context
        """
        current = context
        any_modified = False

        for plugin in self._plugins:
            try:
                hook = getattr(plugin, hook_name)
                result = hook(current)

                # Normalize result to HookResult
                if isinstance(result, HookResult):
                    current = result.data
                    if result.modified:
                        any_modified = True
                else:
                    # Plugin returned raw context
                    current = result

            except Exception as e:
                if plugin.fail_open:
                    logger.warning(
                        f"Plugin {plugin.name} error in {hook_name} (fail_open=True): {e}"
                    )
                    continue
                else:
                    logger.error(
                        f"Plugin {plugin.name} error in {hook_name} (fail_open=False): {e}"
                    )
                    raise

        return HookResult(data=current, modified=any_modified)
