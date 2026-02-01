"""enable_native_tool tool - Enable and configure a native tool."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from ..schema_registry import SchemaRegistry
from ..secrets import SecretDetector

if TYPE_CHECKING:
    from ..staged_config import StagedConfig


async def handle_enable_native_tool(
    arguments: dict[str, Any],
    staged_config: "StagedConfig",
) -> dict[str, Any]:
    """
    Handle ploston:enable_native_tool tool call.
    
    Enables and configures a native tool with validation.
    
    Args:
        arguments: Tool arguments containing tool name and config
        staged_config: StagedConfig instance
        
    Returns:
        Result with success status, validation feedback, and staged changes count
    """
    tool = arguments.get("tool")
    if not tool:
        return {
            "success": False,
            "error": "Tool name is required",
            "validation": {
                "valid": False,
                "errors": [{"code": "MISSING_REQUIRED", "field": "tool", "message": "Tool name is required"}],
                "warnings": [],
            },
            "staged_changes_count": 0,
            "ready_to_apply": False,
        }

    # Check if tool is valid
    valid_tools = SchemaRegistry.get_native_tool_names()
    if tool not in valid_tools:
        return {
            "success": False,
            "error": f"Unknown native tool: {tool}. Valid tools: {', '.join(valid_tools)}",
            "validation": {
                "valid": False,
                "errors": [{"code": "UNKNOWN_TOOL", "field": "tool", "message": f"Unknown native tool: {tool}"}],
                "warnings": [],
            },
            "staged_changes_count": 0,
            "ready_to_apply": False,
        }

    # Get tool schema
    tool_schema = SchemaRegistry.get_native_tool_schema(tool)
    
    # Build tool config
    tool_config: dict[str, Any] = {"enabled": True}
    
    # Copy provided config values
    config = arguments.get("config", {})
    for key, value in config.items():
        tool_config[key] = value

    # Stage the change
    path = f"tools.native_tools.{tool}"
    staged_config.set(path, tool_config)

    # Validate the tool config
    validation = _validate_native_tool(tool, tool_config, tool_schema)
    
    # Count staged changes
    staged_changes_count = _count_staged_changes(staged_config)
    
    return {
        "success": True,
        "staged_path": path,
        "validation": validation,
        "staged_changes_count": staged_changes_count,
        "ready_to_apply": validation["valid"],
    }


def _validate_native_tool(
    tool: str,
    tool_config: dict[str, Any],
    tool_schema: dict[str, Any] | None,
) -> dict[str, Any]:
    """Validate a native tool configuration."""
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    
    if not tool_schema:
        return {"valid": True, "errors": [], "warnings": []}
    
    # Check required fields
    for field, field_schema in tool_schema.items():
        if not isinstance(field_schema, dict):
            continue
            
        is_required = field_schema.get("required", False)
        if is_required and field not in tool_config:
            errors.append({
                "code": "MISSING_REQUIRED",
                "field": f"tools.native_tools.{tool}.{field}",
                "message": f"Required field '{field}' is missing for {tool}",
            })
    
    # Check for secrets
    secret_detector = SecretDetector()
    for key, value in tool_config.items():
        if not isinstance(value, str):
            continue
            
        field_schema = tool_schema.get(key, {})
        is_secret_field = isinstance(field_schema, dict) and field_schema.get("secret", False)
        
        # Check for literal secrets
        detection = secret_detector.detect(key, value)
        if detection or is_secret_field:
            if "${" not in value:  # Not using env var syntax
                suggested = detection.suggested_env_var if detection else key.upper()
                warnings.append({
                    "code": "LITERAL_SECRET",
                    "field": f"tools.native_tools.{tool}.{key}",
                    "message": f"Value looks like a secret. Consider using ${{{suggested}}} syntax.",
                    "suggestion": f"${{{suggested}}}",
                })
        
        # Check for unset env var references
        env_refs = secret_detector.extract_env_var_refs(value)
        for env_var in env_refs:
            if env_var not in os.environ:
                warnings.append({
                    "code": "ENV_NOT_SET",
                    "field": f"tools.native_tools.{tool}.{key}",
                    "message": f"Environment variable '{env_var}' is not set",
                })
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def _count_staged_changes(staged_config: "StagedConfig") -> int:
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
ENABLE_NATIVE_TOOL_SCHEMA = {
    "name": "ploston:enable_native_tool",
    "description": "Enable and configure a native tool (kafka, firecrawl, ollama, filesystem, network).",
    "inputSchema": {
        "type": "object",
        "required": ["tool"],
        "properties": {
            "tool": {
                "type": "string",
                "enum": ["kafka", "firecrawl", "ollama", "filesystem", "network"],
                "description": "Native tool to enable",
            },
            "config": {
                "type": "object",
                "description": "Tool-specific configuration. Use get_setup_context to see schema.",
            },
        },
    },
}
