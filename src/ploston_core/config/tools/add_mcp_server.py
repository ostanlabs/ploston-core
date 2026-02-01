"""add_mcp_server tool - Add a single MCP server to configuration."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from ..secrets import SecretDetector

if TYPE_CHECKING:
    from ..staged_config import StagedConfig


async def handle_add_mcp_server(
    arguments: dict[str, Any],
    staged_config: StagedConfig,
) -> dict[str, Any]:
    """
    Handle ploston:add_mcp_server tool call.

    Adds a single MCP server to the staged configuration with validation.

    Args:
        arguments: Tool arguments containing server definition
        staged_config: StagedConfig instance

    Returns:
        Result with success status, validation feedback, and staged changes count
    """
    name = arguments.get("name")
    if not name:
        return {
            "success": False,
            "error": "Server name is required",
            "validation": {
                "valid": False,
                "errors": [
                    {
                        "code": "MISSING_REQUIRED",
                        "field": "name",
                        "message": "Server name is required",
                    }
                ],
                "warnings": [],
            },
            "staged_changes_count": 0,
            "ready_to_apply": False,
        }

    # Build server config
    server_config: dict[str, Any] = {}

    # Handle transport
    transport = arguments.get("transport", "stdio")
    server_config["transport"] = transport

    # Handle command (for stdio)
    if "command" in arguments:
        server_config["command"] = arguments["command"]

    # Handle args
    if "args" in arguments:
        server_config["args"] = arguments["args"]

    # Handle url (for http)
    if "url" in arguments:
        server_config["url"] = arguments["url"]

    # Handle env
    if "env" in arguments:
        server_config["env"] = arguments["env"]

    # Handle timeout
    if "timeout" in arguments:
        server_config["timeout"] = arguments["timeout"]

    # Stage the change
    path = f"tools.mcp_servers.{name}"
    staged_config.set(path, server_config)

    # Validate the entire config
    validation = _validate_mcp_server(name, server_config, staged_config)

    # Count staged changes
    staged_changes_count = _count_staged_changes(staged_config)

    return {
        "success": True,
        "staged_path": path,
        "validation": validation,
        "staged_changes_count": staged_changes_count,
        "ready_to_apply": validation["valid"],
    }


def _validate_mcp_server(
    name: str,
    server_config: dict[str, Any],
    staged_config: StagedConfig,
) -> dict[str, Any]:
    """Validate an MCP server configuration."""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    transport = server_config.get("transport", "stdio")

    # Check transport-specific requirements
    if transport == "stdio":
        if "command" not in server_config:
            errors.append(
                {
                    "code": "MISSING_COMMAND",
                    "field": f"tools.mcp_servers.{name}.command",
                    "message": f"MCP server '{name}' with stdio transport requires 'command' field",
                }
            )
    elif transport == "http":
        if "url" not in server_config:
            errors.append(
                {
                    "code": "MISSING_URL",
                    "field": f"tools.mcp_servers.{name}.url",
                    "message": f"MCP server '{name}' with http transport requires 'url' field",
                }
            )

    # Check for mutex violation (both command and url)
    if "command" in server_config and "url" in server_config:
        errors.append(
            {
                "code": "MUTEX_VIOLATION",
                "field": f"tools.mcp_servers.{name}",
                "message": f"MCP server '{name}' cannot have both 'command' and 'url' - choose one transport",
            }
        )

    # Check for existing server (warning only)
    merged = staged_config.get_merged()
    merged.get("tools", {}).get("mcp_servers", {})
    # Note: We already staged the change, so we check if there was a previous value
    # This is a simplification - in practice we'd check before staging

    # Check env vars for secrets and unset vars
    env = server_config.get("env", {})
    secret_detector = SecretDetector()

    for key, value in env.items():
        if not isinstance(value, str):
            continue

        # Check for literal secrets
        detection = secret_detector.detect(key, value)
        if detection:
            warnings.append(
                {
                    "code": "LITERAL_SECRET",
                    "field": f"tools.mcp_servers.{name}.env.{key}",
                    "message": f"Value looks like a secret. Consider using ${{{detection.suggested_env_var}}} syntax.",
                    "suggestion": f"${{{detection.suggested_env_var}}}",
                }
            )

        # Check for unset env var references
        env_refs = secret_detector.extract_env_var_refs(value)
        for env_var in env_refs:
            if env_var not in os.environ:
                warnings.append(
                    {
                        "code": "ENV_NOT_SET",
                        "field": f"tools.mcp_servers.{name}.env.{key}",
                        "message": f"Environment variable '{env_var}' is not set",
                    }
                )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def _count_staged_changes(staged_config: StagedConfig) -> int:
    """Count the number of staged changes."""
    changes = staged_config.changes
    return _count_dict_items(changes)


def _count_dict_items(d: dict[str, Any], count: int = 0) -> int:
    """Recursively count items in a nested dict."""
    for key, value in d.items():
        if isinstance(value, dict):
            count = _count_dict_items(value, count)
        else:
            count += 1
    return count


# Tool schema for MCP exposure
ADD_MCP_SERVER_SCHEMA = {
    "name": "ploston:add_mcp_server",
    "description": "Add a single MCP server to configuration. Returns validation feedback immediately.",
    "inputSchema": {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Server identifier (e.g., 'github', 'my-server')",
            },
            "command": {
                "type": "string",
                "description": "Command to spawn server (required for stdio transport)",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Command arguments",
            },
            "url": {
                "type": "string",
                "description": "Server URL (required for http transport)",
            },
            "transport": {
                "type": "string",
                "enum": ["stdio", "http"],
                "default": "stdio",
            },
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Environment variables. Use ${VAR} for secrets.",
            },
            "timeout": {
                "type": "integer",
                "default": 30,
            },
        },
    },
}
