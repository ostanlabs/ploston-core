"""MCP Frontend - Expose AEL as an MCP server."""

from .http_transport import BridgeContext, HTTPTransport, bridge_context
from .server import MCPFrontend
from .types import MCPServerConfig

__all__ = [
    "MCPFrontend",
    "MCPServerConfig",
    "HTTPTransport",
    "BridgeContext",
    "bridge_context",
]
