"""config_validate tool handler - validate staged configuration."""

from typing import Any

from ploston_core.config import StagedConfig


async def handle_config_validate(
    arguments: dict[str, Any],
    staged_config: StagedConfig,
) -> dict[str, Any]:
    """Handle config_validate tool call.

    Args:
        arguments: Tool arguments (none required)
        staged_config: StagedConfig instance

    Returns:
        Validation result with errors and warnings
    """
    # Validate the merged config
    validation_result = staged_config.validate()

    # Get staged changes for context
    staged_changes = staged_config.changes

    # Convert ValidationIssue objects to dicts
    errors = [{"path": e.path, "error": e.message} for e in validation_result.errors]
    warnings = [{"path": w.path, "warning": w.message} for w in validation_result.warnings]

    return {
        "valid": validation_result.valid,
        "errors": errors,
        "warnings": warnings,
        "staged_changes_count": len(staged_changes),
    }
