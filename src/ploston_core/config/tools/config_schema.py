"""config_schema tool handler - get configuration schema documentation."""

from typing import Any

# Schema documentation for each config section
CONFIG_SCHEMA = {
    "server": {
        "description": "HTTP server settings (for REST API mode)",
        "fields": {
            "host": {
                "type": "string",
                "default": "0.0.0.0",
                "description": "Host to bind to",
            },
            "port": {
                "type": "integer",
                "default": 8080,
                "description": "Port to listen on",
            },
        },
    },
    "mcp": {
        "description": "MCP server connections",
        "fields": {
            "servers": {
                "type": "object",
                "description": "Map of server name to server config",
                "additionalProperties": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Command to start MCP server",
                        },
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Command arguments",
                        },
                        "env": {
                            "type": "object",
                            "description": "Environment variables for server",
                        },
                    },
                },
            },
        },
    },
    "tools": {
        "description": "Tool configuration",
        "fields": {
            "timeout": {
                "type": "integer",
                "default": 30,
                "description": "Default tool timeout in seconds",
            },
            "retry_count": {
                "type": "integer",
                "default": 3,
                "description": "Number of retries for failed tool calls",
            },
        },
    },
    "workflows": {
        "description": "Workflow configuration",
        "fields": {
            "directory": {
                "type": "string",
                "default": "./workflows",
                "description": "Directory to load workflow YAML files from",
            },
            "hot_reload": {
                "type": "boolean",
                "default": True,
                "description": "Watch for workflow file changes",
            },
        },
    },
    "execution": {
        "description": "Workflow execution settings",
        "fields": {
            "max_concurrent": {
                "type": "integer",
                "default": 10,
                "description": "Maximum concurrent workflow executions",
            },
            "default_timeout": {
                "type": "integer",
                "default": 300,
                "description": "Default workflow timeout in seconds",
            },
        },
    },
    "python_exec": {
        "description": "Python execution sandbox settings",
        "fields": {
            "enabled": {
                "type": "boolean",
                "default": True,
                "description": "Enable python_exec tool",
            },
            "timeout": {
                "type": "integer",
                "default": 30,
                "description": "Execution timeout in seconds",
            },
            "allowed_imports": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Allowed Python imports",
            },
        },
    },
    "logging": {
        "description": "Logging configuration",
        "fields": {
            "level": {
                "type": "string",
                "enum": ["DEBUG", "INFO", "WARNING", "ERROR"],
                "default": "INFO",
                "description": "Log level",
            },
            "format": {
                "type": "string",
                "default": "json",
                "description": "Log format (json or text)",
            },
        },
    },
    "plugins": {
        "description": "Plugin configuration",
        "fields": {
            "enabled": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of enabled plugins",
            },
        },
    },
    "security": {
        "description": "Security settings",
        "fields": {
            "require_auth": {
                "type": "boolean",
                "default": False,
                "description": "Require authentication for API",
            },
        },
    },
    "telemetry": {
        "description": "Telemetry and metrics",
        "fields": {
            "enabled": {
                "type": "boolean",
                "default": False,
                "description": "Enable telemetry collection",
            },
        },
    },
}


async def handle_config_schema(
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Handle config_schema tool call.

    Args:
        arguments: Tool arguments with optional 'section'

    Returns:
        Schema documentation
    """
    section = arguments.get("section")

    if section:
        if section not in CONFIG_SCHEMA:
            return {
                "error": f"Unknown section: {section}",
                "available_sections": list(CONFIG_SCHEMA.keys()),
            }
        return {
            "section": section,
            "schema": CONFIG_SCHEMA[section],
        }

    return {
        "sections": list(CONFIG_SCHEMA.keys()),
        "schema": CONFIG_SCHEMA,
    }
