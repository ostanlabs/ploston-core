"""ael:configure tool handler - switch back to configuration mode."""

from typing import Any

from ploston_core.config import Mode


async def handle_configure(
    arguments: dict[str, Any],
    mode_manager: Any,
) -> dict[str, Any]:
    """Handle ael:configure tool call.

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

    return {
        "success": True,
        "mode": "configuration",
        "running_workflows": running_workflows,
        "message": (
            f"Switched to configuration mode. {running_workflows} workflow(s) still running."
            if running_workflows > 0
            else "Switched to configuration mode."
        ),
    }
