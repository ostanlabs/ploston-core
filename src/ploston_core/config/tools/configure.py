"""configure tool handler - switch back to configuration mode."""

from typing import Any

from ploston_core.config import Mode
from ploston_core.config.tools.config_schema import CONFIG_SCHEMA


async def handle_configure(
    arguments: dict[str, Any],
    mode_manager: Any,
) -> dict[str, Any]:
    """Handle configure tool call.

    Args:
        arguments: Tool arguments (none required)
        mode_manager: ModeManager instance

    Returns:
        Mode switch result with running workflow info
    """
    if not mode_manager:
        return {
            "success": False,
            "error": "Mode manager not available",
        }

    # Get running workflow count before switching
    running_workflows = mode_manager.running_workflow_count

    # Switch to configuration mode
    mode_manager.set_mode(Mode.CONFIGURATION)

    # Build message with workflow info
    if running_workflows > 0:
        base_message = (
            f"Switched to configuration mode. {running_workflows} workflow(s) still running."
        )
    else:
        base_message = "Switched to configuration mode."

    return {
        "success": True,
        "mode": "configuration",
        "running_workflows": running_workflows,
        "message": base_message,
        "next_steps": (
            "IMPORTANT: Call tools/list to refresh available tools. "
            "Configuration tools are now available: config_get, config_set, config_schema, "
            "add_mcp_server, remove_mcp_server, enable_native_tool, disable_native_tool, "
            "import_config, config_diff, config_validate, config_reset, config_done."
        ),
        "available_config_sections": list(CONFIG_SCHEMA.keys()),
        "hint": "Use config_schema tool to see detailed schema for any section.",
    }
