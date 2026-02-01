"""Schema Registry - Single source of truth for Ploston configuration schema."""

from __future__ import annotations

import os
from typing import Any


class SchemaRegistry:
    """
    Single source of truth for the Ploston configuration schema.

    Provides schema definitions, example configs, import source info,
    and validation rules for the configuration mode tools.
    """

    @staticmethod
    def get_ploston_schema() -> dict[str, Any]:
        """Return the complete ploston_schema for get_setup_context."""
        return {
            "tools": {
                "mcp_servers": {
                    "_description": "Map of MCP server name to definition",
                    "_pattern": "<server_name>: ServerDefinition",
                    "ServerDefinition": {
                        "command": {
                            "type": "string",
                            "required_when": "transport is stdio",
                            "description": "Command to spawn the server process",
                            "example": "npx",
                        },
                        "args": {
                            "type": "array[string]",
                            "required": False,
                            "description": "Arguments passed to command",
                            "example": ["-y", "@modelcontextprotocol/server-github"],
                        },
                        "url": {
                            "type": "string",
                            "required_when": "transport is http",
                            "description": "Server URL for HTTP transport",
                            "example": "http://localhost:8081",
                        },
                        "transport": {
                            "type": "string",
                            "enum": ["stdio", "http"],
                            "default": "stdio",
                        },
                        "env": {
                            "type": "object[string, string]",
                            "secret_syntax": "${VAR_NAME} or ${VAR_NAME:-default}",
                            "example": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
                        },
                        "timeout": {"type": "integer", "default": 30},
                    },
                },
                "native_tools": {
                    "kafka": {
                        "enabled": {"type": "boolean", "required": True},
                        "bootstrap_servers": {
                            "type": "string",
                            "required": True,
                            "example": "localhost:9092",
                        },
                        "security_protocol": {
                            "type": "string",
                            "enum": ["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"],
                            "default": "PLAINTEXT",
                        },
                        "sasl_mechanism": {
                            "type": "string",
                            "required_when": "security_protocol contains SASL",
                        },
                        "sasl_username": {"type": "string", "secret": True},
                        "sasl_password": {"type": "string", "secret": True},
                    },
                    "firecrawl": {
                        "enabled": {"type": "boolean", "required": True},
                        "base_url": {"type": "string", "required": True},
                        "api_key": {"type": "string", "required": True, "secret": True},
                        "timeout": {"type": "integer", "default": 30},
                    },
                    "ollama": {
                        "enabled": {"type": "boolean", "required": True},
                        "host": {"type": "string", "required": True},
                        "default_model": {"type": "string", "default": "llama3"},
                        "timeout": {"type": "integer", "default": 60},
                    },
                    "filesystem": {
                        "enabled": {"type": "boolean", "required": True},
                        "workspace_dir": {"type": "string", "required": True},
                        "allowed_paths": {"type": "array[string]", "default": []},
                        "denied_paths": {"type": "array[string]", "default": []},
                        "max_file_size": {"type": "integer", "default": 10485760},
                    },
                    "network": {
                        "enabled": {"type": "boolean", "required": True},
                        "timeout": {"type": "integer", "default": 30},
                        "allowed_hosts": {"type": "array[string]", "default": []},
                        "denied_hosts": {"type": "array[string]", "default": []},
                    },
                },
            },
            "workflows": {
                "directory": {"type": "string", "default": "./workflows"},
                "watch": {"type": "boolean", "default": True},
            },
            "logging": {
                "level": {
                    "type": "string",
                    "enum": ["DEBUG", "INFO", "WARNING", "ERROR"],
                    "default": "INFO",
                },
                "format": {
                    "type": "string",
                    "enum": ["COLORED", "JSON", "TEXT"],
                    "default": "COLORED",
                },
            },
            "telemetry": {
                "enabled": {"type": "boolean", "default": True},
                "export": {
                    "otlp": {
                        "enabled": {"type": "boolean", "default": False},
                        "endpoint": {"type": "string"},
                    }
                },
            },
        }

    @staticmethod
    def get_example_config() -> dict[str, Any]:
        """Return a complete working example configuration."""
        return {
            "tools": {
                "mcp_servers": {
                    "github": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-github"],
                        "transport": "stdio",
                        "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
                    },
                    "remote-api": {
                        "url": "http://api-server:8081",
                        "transport": "http",
                        "timeout": 60,
                    },
                },
                "native_tools": {
                    "kafka": {"enabled": True, "bootstrap_servers": "localhost:9092"},
                    "filesystem": {"enabled": True, "workspace_dir": "/workspace"},
                },
            },
            "workflows": {"directory": "./workflows"},
            "logging": {"level": "INFO"},
        }

    @staticmethod
    def get_import_sources() -> dict[str, Any]:
        """Return import sources with conversion rules."""
        return {
            "claude_desktop": {
                "config_locations": {
                    "macos": "~/Library/Application Support/Claude/claude_desktop_config.json",
                    "windows": "%APPDATA%\\Claude\\claude_desktop_config.json",
                    "linux": "~/.config/Claude/claude_desktop_config.json",
                },
                "source_schema": {
                    "mcpServers": {
                        "<name>": {"command": "string", "args": "array", "env": "object"}
                    }
                },
                "conversion": {
                    "path": "mcpServers.<name> → tools.mcp_servers.<name>",
                    "fields": {"command": "command", "args": "args", "env": "env"},
                    "auto_add": {"transport": "stdio"},
                    "transform": "Literal secrets → ${VAR} syntax",
                },
            },
            "cursor": {
                "config_locations": {
                    "macos": "~/.cursor/mcp.json",
                    "windows": "%USERPROFILE%\\.cursor\\mcp.json",
                    "linux": "~/.cursor/mcp.json",
                },
                "conversion": "Same as claude_desktop",
            },
        }

    @staticmethod
    def get_validation_rules() -> dict[str, Any]:
        """Return validation rules for secret detection, etc."""
        return {
            "secret_patterns": ["ghp_", "sk-", "api_key", "password", "token", "secret"],
            "secret_format": "${VAR_NAME}",
            "mcp_server_rules": {
                "stdio_requires": ["command"],
                "http_requires": ["url"],
                "mutex": ["command", "url"],
            },
        }

    @staticmethod
    def get_native_tool_schema(tool: str) -> dict[str, Any] | None:
        """Return schema for a specific native tool.

        Args:
            tool: Native tool name (kafka, firecrawl, ollama, filesystem, network)

        Returns:
            Schema dict or None if tool not found
        """
        schema = SchemaRegistry.get_ploston_schema()
        native_tools = schema.get("tools", {}).get("native_tools", {})
        return native_tools.get(tool)

    @staticmethod
    def get_native_tool_names() -> list[str]:
        """Return list of available native tool names."""
        return ["kafka", "firecrawl", "ollama", "filesystem", "network"]

    @staticmethod
    def get_current_platform() -> str:
        """Get current platform for config location lookup."""
        import platform

        system = platform.system().lower()
        if system == "darwin":
            return "macos"
        elif system == "windows":
            return "windows"
        return "linux"

    @staticmethod
    def expand_config_path(path: str) -> str:
        """Expand a config path with environment variables and ~."""
        # Expand ~ to home directory
        path = os.path.expanduser(path)
        # Expand environment variables
        path = os.path.expandvars(path)
        return path
