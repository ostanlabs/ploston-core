"""AEL MCP Client Manager - MCP server connection management."""

from .connection import MCPConnection
from .manager import MCPClientManager
from .protocol import JSONRPCMessage
from .types import MCPCallResult, ServerStatus, ToolSchema

__all__ = [
    # Connection
    "MCPConnection",
    # Manager
    "MCPClientManager",
    # Types
    "ToolSchema",
    "ServerStatus",
    "MCPCallResult",
    # Protocol
    "JSONRPCMessage",
]
