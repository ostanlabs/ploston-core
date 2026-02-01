"""import_config tool - Import configuration from Claude Desktop or Cursor."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..importer import ConfigImporter

if TYPE_CHECKING:
    from ..staged_config import StagedConfig


async def handle_import_config(
    arguments: dict[str, Any],
    staged_config: StagedConfig,
) -> dict[str, Any]:
    """
    Handle ploston:import_config tool call.

    Imports MCP server configurations from Claude Desktop or Cursor.

    Args:
        arguments: Tool arguments containing source and options
        staged_config: StagedConfig instance

    Returns:
        Result with imported servers, detected secrets, and validation
    """
    source = arguments.get("source")
    if source not in ("claude_desktop", "cursor"):
        return {
            "success": False,
            "error": f"Invalid source: {source}. Must be 'claude_desktop' or 'cursor'",
            "imported": [],
            "skipped": [],
            "secrets_detected": [],
            "errors": [],
            "staged_changes_count": 0,
            "ready_to_apply": False,
        }

    # Get options
    convert_secrets = arguments.get("convert_secrets", True)
    secret_mappings = arguments.get("secret_mappings", {})
    skip_servers = arguments.get("skip_servers", [])
    config_path = arguments.get("config_path")

    # Create importer
    importer = ConfigImporter()

    # Load source config
    source_config = importer.load_source_config(source, config_path)
    if source_config is None:
        default_path = importer.get_source_config_path(source)
        return {
            "success": False,
            "error": f"Could not load {source} config from {config_path or default_path}",
            "imported": [],
            "skipped": [],
            "secrets_detected": [],
            "errors": [],
            "staged_changes_count": 0,
            "ready_to_apply": False,
        }

    # Import the config
    result = importer.import_config(
        source=source,
        config=source_config,
        convert_secrets=convert_secrets,
        secret_mappings=secret_mappings,
        skip_servers=skip_servers,
    )

    # Stage all imported servers
    for name, server_config in result.servers.items():
        path = f"tools.mcp_servers.{name}"
        staged_config.set(path, server_config)

    # Build response
    secrets_detected = [
        {
            "server": s.server,
            "field": s.field,
            "original": s.original,
            "converted_to": s.converted_to,
            "action_required": s.action_required,
        }
        for s in result.secrets_detected
    ]

    errors = [{"server": e.server, "error": e.error} for e in result.errors]

    # Count staged changes
    staged_changes_count = _count_staged_changes(staged_config)

    # Validate all imported servers
    validation = _validate_imported_servers(result.servers, staged_config)

    return {
        "success": True,
        "imported": result.imported,
        "skipped": result.skipped,
        "secrets_detected": secrets_detected,
        "errors": errors,
        "validation": validation,
        "staged_changes_count": staged_changes_count,
        "ready_to_apply": validation["valid"],
    }


def _validate_imported_servers(
    servers: dict[str, dict[str, Any]],
    staged_config: StagedConfig,
) -> dict[str, Any]:
    """Validate all imported servers."""
    import os

    from ..secrets import SecretDetector

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    secret_detector = SecretDetector()

    for name, server_config in servers.items():
        transport = server_config.get("transport", "stdio")

        # Check transport-specific requirements
        if transport == "stdio" and "command" not in server_config:
            errors.append(
                {
                    "code": "MISSING_COMMAND",
                    "field": f"tools.mcp_servers.{name}.command",
                    "message": f"MCP server '{name}' with stdio transport requires 'command' field",
                }
            )
        elif transport == "http" and "url" not in server_config:
            errors.append(
                {
                    "code": "MISSING_URL",
                    "field": f"tools.mcp_servers.{name}.url",
                    "message": f"MCP server '{name}' with http transport requires 'url' field",
                }
            )

        # Check env vars
        env = server_config.get("env", {})
        for key, value in env.items():
            if not isinstance(value, str):
                continue

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
IMPORT_CONFIG_SCHEMA = {
    "name": "ploston:import_config",
    "description": "Import MCP server configurations from Claude Desktop or Cursor. Automatically detects and converts secrets.",
    "inputSchema": {
        "type": "object",
        "required": ["source"],
        "properties": {
            "source": {
                "type": "string",
                "enum": ["claude_desktop", "cursor"],
                "description": "Source to import from",
            },
            "config_path": {
                "type": "string",
                "description": "Custom path to config file (uses default location if not provided)",
            },
            "convert_secrets": {
                "type": "boolean",
                "default": True,
                "description": "Convert detected secrets to ${VAR} syntax",
            },
            "secret_mappings": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Manual mappings from literal value to env var name",
            },
            "skip_servers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Server names to skip during import",
            },
        },
    },
}
