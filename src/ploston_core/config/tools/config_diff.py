"""config_diff tool - Show diff between base and staged configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..staged_config import StagedConfig


async def handle_config_diff(
    arguments: dict[str, Any],
    staged_config: StagedConfig,
) -> dict[str, Any]:
    """
    Handle ploston:config_diff tool call.

    Shows the diff between base configuration and staged changes.

    Args:
        arguments: Tool arguments (none required)
        staged_config: StagedConfig instance

    Returns:
        Diff showing unified diff and structured changes
    """
    # Get the unified diff string from staged config
    unified_diff = staged_config.get_diff()

    # Parse the diff to extract additions, modifications, deletions
    additions: list[dict[str, Any]] = []
    deletions: list[str] = []

    # Parse unified diff lines
    lines = unified_diff.split("\n") if unified_diff else []
    for line in lines:
        if line.startswith("+") and not line.startswith("+++"):
            # Addition
            content = line[1:].strip()
            if content and ":" in content:
                key = content.split(":")[0].strip()
                additions.append({"line": content, "key": key})
        elif line.startswith("-") and not line.startswith("---"):
            # Deletion
            content = line[1:].strip()
            if content and ":" in content:
                key = content.split(":")[0].strip()
                deletions.append(content)

    # Also get structured changes from the changes dict
    changes = staged_config.changes
    structured_changes = _flatten_changes(changes)

    # Count total changes
    total_changes = len(structured_changes)
    has_changes = total_changes > 0 or bool(unified_diff.strip())

    return {
        "has_changes": has_changes,
        "total_changes": total_changes,
        "unified_diff": unified_diff,
        "staged_changes": structured_changes,
    }


def _flatten_changes(changes: dict[str, Any], prefix: str = "") -> list[dict[str, Any]]:
    """Flatten nested changes dict into a list of path/value pairs."""
    result: list[dict[str, Any]] = []
    for key, value in changes.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.extend(_flatten_changes(value, path))
        else:
            result.append({"path": path, "value": value})
    return result


# Tool schema for MCP exposure
CONFIG_DIFF_SCHEMA = {
    "name": "ploston:config_diff",
    "description": "Show diff between base configuration and staged changes.",
    "inputSchema": {
        "type": "object",
        "properties": {},
    },
}
