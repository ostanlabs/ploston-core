"""MCP Client Manager - manages all MCP server connections."""

import asyncio
from typing import Any

from ploston_core.config.models import ToolsConfig
from ploston_core.errors import ErrorFactory, create_error
from ploston_core.logging.logger import AELLogger
from ploston_core.types import LogLevel

from .connection import MCPConnection
from .types import MCPCallResult, ServerStatus, ToolSchema


class MCPClientManager:
    """Manages all MCP server connections.

    Creates and maintains connections based on config.
    Provides unified interface for tool discovery and calls.
    """

    def __init__(
        self,
        config: ToolsConfig,
        logger: AELLogger | None = None,
        error_factory: ErrorFactory | None = None,
    ):
        """Initialize MCP client manager.

        Args:
            config: Tools configuration
            logger: Optional logger
            error_factory: Optional error factory
        """
        self._connections: dict[str, MCPConnection] = {}
        self._config = config
        self._logger = logger
        self._error_factory = error_factory

    def _log(self, level: LogLevel, message: str, **kwargs: Any) -> None:
        """Log message if logger available."""
        if self._logger:
            self._logger._log(level, "mcp.manager", message, **kwargs)

    async def connect_all(self) -> dict[str, ServerStatus]:
        """Connect to all configured MCP servers.

        Connects in parallel. Failures are logged but don't stop others.

        Returns:
            Dict of server name to status
        """
        if not self._config.mcp_servers:
            self._log(LogLevel.INFO, "No MCP servers configured")
            return {}

        self._log(LogLevel.INFO, f"Connecting to {len(self._config.mcp_servers)} MCP servers")

        # Create connections
        for name, server_config in self._config.mcp_servers.items():
            if name not in self._connections:
                self._connections[name] = MCPConnection(
                    name=name,
                    config=server_config,
                    logger=self._logger,
                )

        # Connect in parallel
        tasks = []
        for name, conn in self._connections.items():
            tasks.append(self._connect_one(name, conn))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Build status dict
        status_dict = {}
        for name, _result in zip(self._connections.keys(), results, strict=True):
            conn = self._connections[name]
            status_dict[name] = conn.get_status()

        # Log summary
        connected = sum(1 for s in status_dict.values() if s.status.value == "connected")
        self._log(LogLevel.INFO, f"Connected to {connected}/{len(status_dict)} servers")

        return status_dict

    async def _connect_one(self, name: str, conn: MCPConnection) -> None:
        """Connect to one server, catching exceptions.

        Args:
            name: Server name
            conn: Connection instance
        """
        try:
            await conn.connect()
        except Exception as e:
            self._log(LogLevel.ERROR, f"Failed to connect to '{name}': {e}")

    async def disconnect_all(self) -> None:
        """Disconnect from all servers."""
        self._log(LogLevel.INFO, "Disconnecting from all MCP servers")

        tasks = []
        for conn in self._connections.values():
            tasks.append(conn.disconnect())

        await asyncio.gather(*tasks, return_exceptions=True)

        self._connections.clear()
        self._log(LogLevel.INFO, "Disconnected from all servers")

    async def refresh_all(self) -> dict[str, list[ToolSchema]]:
        """Refresh tools from all connected servers.

        Returns:
            Dict of server name to tool list
        """
        self._log(LogLevel.INFO, "Refreshing tools from all servers")

        tasks = []
        names = []
        for name, conn in self._connections.items():
            if conn.status.value == "connected":
                tasks.append(conn.refresh_tools())
                names.append(name)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Build tools dict
        tools_dict: dict[str, list[ToolSchema]] = {}
        for name, result in zip(names, results, strict=True):
            if isinstance(result, Exception):
                self._log(LogLevel.ERROR, f"Failed to refresh tools from '{name}': {result}")
                tools_dict[name] = []
            elif isinstance(result, list):
                tools_dict[name] = result

        total_tools = sum(len(tools) for tools in tools_dict.values())
        self._log(LogLevel.INFO, f"Refreshed {total_tools} tools from {len(tools_dict)} servers")

        return tools_dict

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout_seconds: int | None = None,
    ) -> MCPCallResult:
        """Call a tool on a specific server.

        Args:
            server_name: Name of MCP server
            tool_name: Name of tool
            arguments: Tool arguments
            timeout: Optional timeout

        Returns:
            MCPCallResult

        Raises:
            AELError(TOOL_UNAVAILABLE) if server not found or not connected
        """
        conn = self.get_connection(server_name)
        if not conn:
            raise create_error(
                "TOOL_UNAVAILABLE",
                tool_name=tool_name,
                detail=f"MCP server '{server_name}' not found",
            )

        return await conn.call_tool(tool_name, arguments, timeout_seconds)

    def get_connection(self, name: str) -> MCPConnection | None:
        """Get connection by server name.

        Args:
            name: Server name

        Returns:
            MCPConnection if found, None otherwise
        """
        return self._connections.get(name)

    def list_connections(self) -> list[MCPConnection]:
        """List all connections.

        Returns:
            List of MCPConnection instances
        """
        return list(self._connections.values())

    def get_all_tools(self) -> dict[str, list[ToolSchema]]:
        """Get all tools from all connected servers.

        Returns:
            Dict of server name to tool list
        """
        tools_dict = {}
        for name, conn in self._connections.items():
            if conn.status.value == "connected":
                tools_dict[name] = conn.list_tools()
        return tools_dict

    def get_status(self) -> dict[str, ServerStatus]:
        """Get status of all connections.

        Returns:
            Dict of server name to ServerStatus
        """
        return {name: conn.get_status() for name, conn in self._connections.items()}

    async def on_config_change(self, new_config: ToolsConfig) -> None:
        """Handle config change.

        - Disconnect removed servers
        - Connect new servers
        - Reconnect changed servers

        Args:
            new_config: New tools configuration
        """
        self._log(LogLevel.INFO, "Handling config change")

        old_servers = set(self._connections.keys())
        new_servers = set(new_config.mcp_servers.keys())

        # Servers to remove
        to_remove = old_servers - new_servers
        for name in to_remove:
            self._log(LogLevel.INFO, f"Removing server '{name}'")
            conn = self._connections.pop(name)
            await conn.disconnect()

        # Servers to add
        to_add = new_servers - old_servers
        for name in to_add:
            self._log(LogLevel.INFO, f"Adding server '{name}'")
            conn = MCPConnection(
                name=name,
                config=new_config.mcp_servers[name],
                logger=self._logger,
            )
            self._connections[name] = conn
            await self._connect_one(name, conn)

        # Servers to check for changes
        to_check = old_servers & new_servers
        for name in to_check:
            old_config = self._config.mcp_servers[name]
            new_server_config = new_config.mcp_servers[name]

            # Check if config changed (simple comparison)
            if (
                old_config.command != new_server_config.command
                or old_config.env != new_server_config.env
            ):
                self._log(LogLevel.INFO, f"Reconnecting server '{name}' (config changed)")
                conn = self._connections[name]
                await conn.disconnect()

                # Create new connection
                new_conn = MCPConnection(
                    name=name,
                    config=new_server_config,
                    logger=self._logger,
                )
                self._connections[name] = new_conn
                await self._connect_one(name, new_conn)

        # Update config
        self._config = new_config
        self._log(LogLevel.INFO, "Config change complete")
