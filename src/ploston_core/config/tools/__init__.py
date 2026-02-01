"""Config tools for AEL self-configuration."""

from .registry import (
    CONFIG_TOOL_SCHEMAS,
    CONFIGURE_TOOL_SCHEMA,
    ConfigToolRegistry,
    PLOSTON_TOOL_SCHEMAS,
    # Renamed tool schemas
    PLOSTON_CONFIG_GET_SCHEMA,
    PLOSTON_CONFIG_SET_SCHEMA,
    PLOSTON_CONFIG_DONE_SCHEMA,
    PLOSTON_CONFIGURE_SCHEMA,
)

# Export individual tool schemas
from .get_setup_context import GET_SETUP_CONTEXT_SCHEMA
from .add_mcp_server import ADD_MCP_SERVER_SCHEMA
from .enable_native_tool import ENABLE_NATIVE_TOOL_SCHEMA
from .import_config import IMPORT_CONFIG_SCHEMA
from .remove_mcp_server import REMOVE_MCP_SERVER_SCHEMA
from .disable_native_tool import DISABLE_NATIVE_TOOL_SCHEMA
from .config_diff import CONFIG_DIFF_SCHEMA
from .config_reset import CONFIG_RESET_SCHEMA

__all__ = [
    "ConfigToolRegistry",
    "CONFIG_TOOL_SCHEMAS",
    "CONFIGURE_TOOL_SCHEMA",
    "PLOSTON_TOOL_SCHEMAS",
    # Individual tool schemas
    "GET_SETUP_CONTEXT_SCHEMA",
    "ADD_MCP_SERVER_SCHEMA",
    "ENABLE_NATIVE_TOOL_SCHEMA",
    "IMPORT_CONFIG_SCHEMA",
    "REMOVE_MCP_SERVER_SCHEMA",
    "DISABLE_NATIVE_TOOL_SCHEMA",
    "CONFIG_DIFF_SCHEMA",
    "CONFIG_RESET_SCHEMA",
    # Renamed tool schemas
    "PLOSTON_CONFIG_GET_SCHEMA",
    "PLOSTON_CONFIG_SET_SCHEMA",
    "PLOSTON_CONFIG_DONE_SCHEMA",
    "PLOSTON_CONFIGURE_SCHEMA",
]
