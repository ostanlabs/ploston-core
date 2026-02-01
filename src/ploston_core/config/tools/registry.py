"""Config Tool Registry - routes config tool calls to handlers."""

from collections.abc import Callable
from typing import Any

from ploston_core.config import ConfigLoader, StagedConfig
from ploston_core.errors import create_error

# Tool schemas for MCP exposure
CONFIG_TOOL_SCHEMAS = [
    {
        "name": "ael:config_get",
        "description": "Read current configuration or specific fields. Use dot-notation path for nested values.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Dot-notation path to config section. Empty/omit for entire config.",
                    "examples": ["tools.mcp_servers", "logging.level", "tools.native_tools.kafka"],
                }
            },
        },
    },
    {
        "name": "ael:config_set",
        "description": """Stage configuration changes. Changes are NOT applied until config_done is called.

Native-tools configuration paths (under tools.native_tools):
- kafka: {enabled, bootstrap_servers, producer, consumer, security_protocol}
- firecrawl: {enabled, base_url, api_key, timeout, max_retries}
- ollama: {enabled, host, default_model, timeout}
- filesystem: {enabled, workspace_dir, allowed_paths, denied_paths, max_file_size}
- network: {enabled, timeout, max_retries, allowed_hosts, denied_hosts}
- data: {enabled, max_data_size}

Use ${VAR} syntax for secrets (e.g., ${KAFKA_SASL_PASSWORD}).""",
        "inputSchema": {
            "type": "object",
            "required": ["path", "value"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Dot-notation path to set",
                    "examples": [
                        "tools.native_tools.kafka.enabled",
                        "tools.native_tools.kafka.bootstrap_servers",
                        "tools.native_tools.firecrawl.base_url",
                        "tools.native_tools.ollama.host",
                        "tools.mcp_servers.github",
                        "logging.level",
                    ],
                },
                "value": {
                    "description": "Value to set - can be object for nested config, string, number, boolean, etc. Use ${VAR} syntax for env var references."
                },
            },
        },
    },
    {
        "name": "ael:config_validate",
        "description": "Validate the current staged configuration without applying it.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ael:config_schema",
        "description": "Get configuration schema documentation with field descriptions and defaults.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "Config section to get schema for. Omit for full schema.",
                    "examples": ["mcp", "logging", "execution"],
                }
            },
        },
    },
    {
        "name": "ael:config_location",
        "description": "Get or set the target location for writing configuration.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["project", "user"],
                    "description": "Where to write config: project (./ael-config.yaml) or user (~/.ael/config.yaml)",
                },
                "path": {
                    "type": "string",
                    "description": "Custom path to write config to (overrides scope)",
                },
            },
        },
    },
    {
        "name": "ael:config_done",
        "description": "Validate, connect to MCP servers, apply staged configuration, and switch to Running Mode.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

CONFIGURE_TOOL_SCHEMA = {
    "name": "ael:configure",
    "description": "Switch back to Configuration Mode to modify settings. Running workflows will continue.",
    "inputSchema": {"type": "object", "properties": {}},
}


class ConfigToolRegistry:
    """Registry for config tools - routes calls to handlers."""

    def __init__(
        self,
        staged_config: StagedConfig,
        config_loader: ConfigLoader,
        mode_manager: Any = None,
        mcp_manager: Any = None,
        redis_store: Any = None,
        runner_registry: Any = None,
    ):
        """Initialize config tool registry.

        Args:
            staged_config: StagedConfig instance for staging changes
            config_loader: ConfigLoader for loading/saving config
            mode_manager: ModeManager for mode transitions
            mcp_manager: MCPClientManager for connecting to MCP servers
            redis_store: Optional RedisConfigStore for publishing config
            runner_registry: Optional PersistentRunnerRegistry for creating runners
        """
        self._staged_config = staged_config
        self._config_loader = config_loader
        self._mode_manager = mode_manager
        self._mcp_manager = mcp_manager
        self._redis_store = redis_store
        self._runner_registry = runner_registry
        self._write_location: str | None = None
        self._handlers = self._register_handlers()

    def _register_handlers(self) -> dict[str, Callable[..., Any]]:
        """Register tool handlers."""
        return {
            "ael:config_get": self._handle_config_get,
            "ael:config_set": self._handle_config_set,
            "ael:config_validate": self._handle_config_validate,
            "ael:config_schema": self._handle_config_schema,
            "ael:config_location": self._handle_config_location,
            "ael:config_done": self._handle_config_done,
            "ael:configure": self._handle_configure,
        }

    def get_for_mcp_exposure(self) -> list[dict[str, Any]]:
        """Return tool schemas for MCP tools/list in config mode."""
        return CONFIG_TOOL_SCHEMAS

    def get_configure_tool_for_mcp_exposure(self) -> dict[str, Any]:
        """Return just ael:configure schema (for running mode)."""
        return CONFIGURE_TOOL_SCHEMA

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Route tool call to handler.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            Tool result
        """
        handler = self._handlers.get(name)
        if not handler:
            raise create_error("TOOL_NOT_FOUND", context={"tool_name": name})
        return await handler(arguments)

    async def _handle_config_get(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle config_get tool call."""
        from .config_get import handle_config_get

        return await handle_config_get(arguments, self._staged_config, self._config_loader)

    async def _handle_config_set(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle config_set tool call."""
        from .config_set import handle_config_set

        return await handle_config_set(arguments, self._staged_config)

    async def _handle_config_validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle config_validate tool call."""
        from .config_validate import handle_config_validate

        return await handle_config_validate(arguments, self._staged_config)

    async def _handle_config_schema(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle config_schema tool call."""
        from .config_schema import handle_config_schema

        return await handle_config_schema(arguments)

    async def _handle_config_location(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle config_location tool call."""
        from .config_location import handle_config_location

        result = await handle_config_location(arguments, self._write_location, self._config_loader)
        if "new_location" in result:
            self._write_location = result["new_location"]
        return result

    async def _handle_config_done(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle config_done tool call."""
        from .config_done import handle_config_done

        return await handle_config_done(
            arguments,
            self._staged_config,
            self._config_loader,
            self._mode_manager,
            self._mcp_manager,
            self._write_location,
            self._redis_store,
            self._runner_registry,
        )

    async def _handle_configure(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle ael:configure tool call."""
        from .configure import handle_configure

        return await handle_configure(arguments, self._mode_manager)
