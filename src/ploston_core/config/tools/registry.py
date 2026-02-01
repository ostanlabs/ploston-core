"""Config Tool Registry - routes config tool calls to handlers."""

from collections.abc import Callable
from typing import Any

from ploston_core.config import ConfigLoader, StagedConfig
from ploston_core.errors import create_error

from .add_mcp_server import ADD_MCP_SERVER_SCHEMA
from .config_diff import CONFIG_DIFF_SCHEMA
from .config_reset import CONFIG_RESET_SCHEMA
from .disable_native_tool import DISABLE_NATIVE_TOOL_SCHEMA
from .enable_native_tool import ENABLE_NATIVE_TOOL_SCHEMA

# Import new tool schemas
from .get_setup_context import GET_SETUP_CONTEXT_SCHEMA
from .import_config import IMPORT_CONFIG_SCHEMA
from .remove_mcp_server import REMOVE_MCP_SERVER_SCHEMA

# Renamed ploston: versions of existing tools (T-587 to T-590)
PLOSTON_CONFIG_GET_SCHEMA = {
    "name": "ploston:config_get",
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
}

PLOSTON_CONFIG_SET_SCHEMA = {
    "name": "ploston:config_set",
    "description": """Stage configuration changes. Changes are NOT applied until config_done is called.
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
                    "tools.mcp_servers.github",
                    "logging.level",
                ],
            },
            "value": {
                "description": "Value to set - can be object, string, number, boolean. Use ${VAR} for env vars."
            },
        },
    },
}

PLOSTON_CONFIG_DONE_SCHEMA = {
    "name": "ploston:config_done",
    "description": "Validate, connect to MCP servers, apply staged configuration, and switch to Running Mode.",
    "inputSchema": {"type": "object", "properties": {}},
}

PLOSTON_CONFIGURE_SCHEMA = {
    "name": "ploston:configure",
    "description": "Switch back to Configuration Mode to modify settings. Running workflows will continue.",
    "inputSchema": {"type": "object", "properties": {}},
}

# New ploston: prefixed tool schemas (M-059)
PLOSTON_TOOL_SCHEMAS = [
    GET_SETUP_CONTEXT_SCHEMA,
    ADD_MCP_SERVER_SCHEMA,
    ENABLE_NATIVE_TOOL_SCHEMA,
    IMPORT_CONFIG_SCHEMA,
    REMOVE_MCP_SERVER_SCHEMA,
    DISABLE_NATIVE_TOOL_SCHEMA,
    CONFIG_DIFF_SCHEMA,
    CONFIG_RESET_SCHEMA,
    # Renamed existing tools
    PLOSTON_CONFIG_GET_SCHEMA,
    PLOSTON_CONFIG_SET_SCHEMA,
    PLOSTON_CONFIG_DONE_SCHEMA,
    PLOSTON_CONFIGURE_SCHEMA,
]

# Legacy ael: prefixed tool schemas (to be deprecated)
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
            # Legacy ael: prefixed tools
            "ael:config_get": self._handle_config_get,
            "ael:config_set": self._handle_config_set,
            "ael:config_validate": self._handle_config_validate,
            "ael:config_schema": self._handle_config_schema,
            "ael:config_location": self._handle_config_location,
            "ael:config_done": self._handle_config_done,
            "ael:configure": self._handle_configure,
            # New ploston: prefixed tools (M-059)
            "ploston:get_setup_context": self._handle_get_setup_context,
            "ploston:add_mcp_server": self._handle_add_mcp_server,
            "ploston:enable_native_tool": self._handle_enable_native_tool,
            "ploston:import_config": self._handle_import_config,
            "ploston:remove_mcp_server": self._handle_remove_mcp_server,
            "ploston:disable_native_tool": self._handle_disable_native_tool,
            "ploston:config_diff": self._handle_config_diff,
            "ploston:config_reset": self._handle_config_reset,
            # Renamed ploston: versions of existing tools (T-587 to T-590)
            "ploston:config_get": self._handle_config_get,
            "ploston:config_set": self._handle_config_set,
            "ploston:config_done": self._handle_config_done,
            "ploston:configure": self._handle_configure,
        }

    def get_for_mcp_exposure(self, use_ploston_prefix: bool = False) -> list[dict[str, Any]]:
        """Return tool schemas for MCP tools/list in config mode.

        Args:
            use_ploston_prefix: If True, return new ploston: prefixed tools.
                               If False, return legacy ael: prefixed tools.
        """
        if use_ploston_prefix:
            return PLOSTON_TOOL_SCHEMAS
        return CONFIG_TOOL_SCHEMAS

    def get_configure_tool_for_mcp_exposure(self, use_ploston_prefix: bool = False) -> dict[str, Any]:
        """Return just configure schema (for running mode).

        Args:
            use_ploston_prefix: If True, return ploston:configure. If False, return ael:configure.
        """
        if use_ploston_prefix:
            return PLOSTON_CONFIGURE_SCHEMA
        return CONFIGURE_TOOL_SCHEMA

    def get_all_tool_schemas(self) -> list[dict[str, Any]]:
        """Return all tool schemas (both legacy and new)."""
        return CONFIG_TOOL_SCHEMAS + PLOSTON_TOOL_SCHEMAS

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

    # New ploston: prefixed tool handlers (M-059)

    async def _handle_get_setup_context(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle ploston:get_setup_context tool call."""
        from .get_setup_context import handle_get_setup_context

        return await handle_get_setup_context(
            arguments, self._staged_config, self._config_loader, self._mode_manager
        )

    async def _handle_add_mcp_server(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle ploston:add_mcp_server tool call."""
        from .add_mcp_server import handle_add_mcp_server

        return await handle_add_mcp_server(arguments, self._staged_config)

    async def _handle_enable_native_tool(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle ploston:enable_native_tool tool call."""
        from .enable_native_tool import handle_enable_native_tool

        return await handle_enable_native_tool(arguments, self._staged_config)

    async def _handle_import_config(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle ploston:import_config tool call."""
        from .import_config import handle_import_config

        return await handle_import_config(arguments, self._staged_config)

    async def _handle_remove_mcp_server(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle ploston:remove_mcp_server tool call."""
        from .remove_mcp_server import handle_remove_mcp_server

        return await handle_remove_mcp_server(arguments, self._staged_config)

    async def _handle_disable_native_tool(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle ploston:disable_native_tool tool call."""
        from .disable_native_tool import handle_disable_native_tool

        return await handle_disable_native_tool(arguments, self._staged_config)

    async def _handle_config_diff(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle ploston:config_diff tool call."""
        from .config_diff import handle_config_diff

        return await handle_config_diff(arguments, self._staged_config)

    async def _handle_config_reset(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle ploston:config_reset tool call."""
        from .config_reset import handle_config_reset

        return await handle_config_reset(arguments, self._staged_config)
