"""MCP Frontend type definitions."""

from dataclasses import dataclass


@dataclass
class MCPServerConfig:
    """MCP server configuration."""

    name: str = "ael"
    version: str = "1.0.0"
    expose_workflows: bool = True
    expose_tools: bool = True
