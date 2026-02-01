"""get_setup_context tool - Get complete context for configuring Ploston."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..schema_registry import SchemaRegistry

if TYPE_CHECKING:
    from ..staged_config import StagedConfig
    from ..loader import ConfigLoader
    from ..mode_manager import ModeManager


async def handle_get_setup_context(
    arguments: dict[str, Any],
    staged_config: "StagedConfig",
    config_loader: "ConfigLoader",
    mode_manager: "ModeManager | None" = None,
) -> dict[str, Any]:
    """
    Handle ploston:get_setup_context tool call.
    
    Returns complete context for configuring Ploston including:
    - Current status (mode, existing config)
    - Ploston configuration schema
    - Example configuration
    - Import sources (Claude Desktop, Cursor)
    - Validation rules
    
    Args:
        arguments: Tool arguments (none required)
        staged_config: StagedConfig instance
        config_loader: ConfigLoader instance
        mode_manager: Optional ModeManager for mode info
        
    Returns:
        Complete setup context
    """
    # Get current status
    status = _get_status(staged_config, config_loader, mode_manager)
    
    # Get current config if exists
    current_config = _get_current_config(staged_config)
    
    # Get schema, examples, and rules from registry
    ploston_schema = SchemaRegistry.get_ploston_schema()
    example_config = SchemaRegistry.get_example_config()
    import_sources = SchemaRegistry.get_import_sources()
    validation_rules = SchemaRegistry.get_validation_rules()
    
    return {
        "status": status,
        "current_config": current_config,
        "ploston_schema": ploston_schema,
        "example_config": example_config,
        "import_sources": import_sources,
        "validation_rules": validation_rules,
    }


def _get_status(
    staged_config: "StagedConfig",
    config_loader: "ConfigLoader",
    mode_manager: "ModeManager | None",
) -> dict[str, Any]:
    """Get current status information."""
    from ..mode_manager import Mode
    
    # Determine mode
    mode = "configuration"
    if mode_manager:
        mode = mode_manager.mode.value
    
    # Check if config exists
    has_existing_config = False
    try:
        config = config_loader.get()
        # Check if it has any meaningful content
        has_existing_config = bool(config.tools.mcp_servers) or bool(config.workflows.directory != "./workflows")
    except Exception:
        pass
    
    # Get config path
    config_path = str(staged_config.target_path)
    
    return {
        "mode": mode,
        "has_existing_config": has_existing_config,
        "config_path": config_path,
    }


def _get_current_config(staged_config: "StagedConfig") -> dict[str, Any] | None:
    """Get current configuration if exists."""
    try:
        merged = staged_config.get_merged()
        if merged:
            return merged
    except Exception:
        pass
    return None


# Tool schema for MCP exposure
GET_SETUP_CONTEXT_SCHEMA = {
    "name": "ploston:get_setup_context",
    "description": """Get complete configuration context including Ploston schema, example config, 
import sources for Claude Desktop/Cursor, and validation rules. 
IMPORTANT: Call this first before any other configuration tools.""",
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}
