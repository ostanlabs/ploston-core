"""MCP Connection - manages single MCP server connection.

Uses the FastMCP client library for proper MCP protocol support,
including automatic handling of notifications like tools/list_changed.
"""

import asyncio
import time
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import datetime
from typing import Any

import mcp.types
from fastmcp.client import Client
from fastmcp.client.messages import MessageHandler

from ploston_core.config.models import MCPServerDefinition
from ploston_core.errors import create_error
from ploston_core.logging.logger import AELLogger
from ploston_core.types import ConnectionStatus, LogLevel, MCPTransport

from .types import MCPCallResult, ServerStatus, ToolSchema

# Type alias for tool change callback
# Callback receives: server_name, list of tools
ToolChangeCallback = Callable[[str, list[ToolSchema]], None]


class _ToolChangeMessageHandler(MessageHandler):
    """Message handler that triggers callback on tool list changes."""

    def __init__(self, on_change: Callable[[], Any]):
        self._on_change = on_change

    async def on_tool_list_changed(
        self, message: mcp.types.ToolListChangedNotification
    ) -> None:
        """Handle tool list changed notification from server."""
        await self._on_change()


class MCPConnection:
    """Single MCP server connection.

    Uses FastMCP Client for proper MCP protocol support including
    automatic handling of notifications like tools/list_changed.
    """

    def __init__(
        self,
        name: str,
        config: MCPServerDefinition,
        logger: AELLogger | None = None,
        on_tools_changed: ToolChangeCallback | None = None,
    ):
        """Initialize MCP connection.

        Args:
            name: Server name
            config: Server configuration
            logger: Optional logger
            on_tools_changed: Optional callback when tools change via notification
        """
        self.name = name
        self.config = config
        self._logger = logger
        self._on_tools_changed = on_tools_changed
        self._status = ConnectionStatus.DISCONNECTED
        self._tools: dict[str, ToolSchema] = {}
        self._last_connected: datetime | None = None
        self._last_error: str | None = None

        # FastMCP client
        self._client: Client | None = None
        self._exit_stack: AsyncExitStack | None = None

    def _log(self, level: LogLevel, message: str, **kwargs: Any) -> None:
        """Log message if logger available."""
        if self._logger:
            self._logger._log(level, f"mcp.{self.name}", message, **kwargs)

    @property
    def status(self) -> ConnectionStatus:
        """Current connection status."""
        return self._status

    def get_status(self) -> ServerStatus:
        """Get detailed status.

        Returns:
            ServerStatus with current state
        """
        return ServerStatus(
            name=self.name,
            status=self._status,
            tools=list(self._tools.keys()),
            error=self._last_error,
            last_connected=self._last_connected.isoformat() if self._last_connected else None,
            last_error=self._last_error,
        )

    def get_tool(self, name: str) -> ToolSchema | None:
        """Get tool schema by name.

        Args:
            name: Tool name

        Returns:
            ToolSchema if found, None otherwise
        """
        return self._tools.get(name)

    def list_tools(self) -> list[ToolSchema]:
        """List all tools from this server.

        Returns:
            List of ToolSchema
        """
        return list(self._tools.values())

    async def connect(
        self,
        max_retries: int = 0,
        initial_delay: float = 1.0,
        max_delay: float = 60.0,
    ) -> None:
        """Establish connection to MCP server.

        For stdio: spawns process and sends initialize.
        For http: validates endpoint and sends initialize.

        Args:
            max_retries: Maximum number of retry attempts (0 = no retries)
            initial_delay: Initial delay between retries in seconds
            max_delay: Maximum delay between retries in seconds

        Raises:
            AELError(TOOL_UNAVAILABLE) if connection fails after all retries
        """
        if self._status == ConnectionStatus.CONNECTED:
            self._log(LogLevel.DEBUG, "Already connected")
            return

        last_error: Exception | None = None
        delay = initial_delay
        attempts = 0

        while attempts <= max_retries:
            if attempts > 0:
                self._log(
                    LogLevel.INFO,
                    f"Retry {attempts}/{max_retries} connecting to MCP server "
                    f"(delay={delay:.1f}s)",
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)  # Exponential backoff

            attempts += 1

            try:
                await self._connect_once()
                return  # Success!
            except Exception as e:
                last_error = e
                if attempts <= max_retries:
                    self._log(
                        LogLevel.WARN,
                        f"Connection attempt {attempts} failed: {e}",
                    )

        # All retries exhausted
        self._status = ConnectionStatus.ERROR
        self._last_error = str(last_error)
        self._log(LogLevel.ERROR, f"Connection failed after {attempts} attempts: {last_error}")
        raise create_error(
            "TOOL_UNAVAILABLE",
            detail=f"Failed to connect to MCP server '{self.name}': {last_error}",
        ) from last_error

    async def _connect_once(self) -> None:
        """Single connection attempt to MCP server using FastMCP Client.

        Raises:
            Exception if connection fails
        """
        self._status = ConnectionStatus.CONNECTING
        self._log(LogLevel.INFO, f"Connecting to MCP server (transport={self.config.transport})")

        try:
            # Determine transport URL/command for FastMCP Client
            transport_source = self._get_transport_source()

            # Create message handler for tool change notifications
            message_handler = _ToolChangeMessageHandler(self._handle_tools_changed)

            # Create FastMCP client with notification handler
            self._client = Client(
                transport=transport_source,
                message_handler=message_handler,
                timeout=self.config.timeout,
                name=f"ploston-{self.name}",
            )

            # Enter the client context (establishes connection)
            self._exit_stack = AsyncExitStack()
            await self._exit_stack.enter_async_context(self._client)

            # Fetch tools
            await self.refresh_tools()

            self._status = ConnectionStatus.CONNECTED
            self._last_connected = datetime.now()
            self._last_error = None
            self._log(LogLevel.INFO, f"Connected successfully ({len(self._tools)} tools)")

        except Exception as e:
            self._status = ConnectionStatus.ERROR
            self._last_error = str(e)
            # Clean up on failure
            if self._exit_stack:
                try:
                    await self._exit_stack.aclose()
                except Exception:
                    pass
                self._exit_stack = None
            self._client = None
            raise

    def _get_transport_source(self) -> str:
        """Get the transport source for FastMCP Client.

        Returns:
            URL for HTTP transport, or command path for stdio transport

        Raises:
            AELError if transport configuration is invalid
        """
        if self.config.transport == MCPTransport.STDIO or self.config.transport == "stdio":
            if not self.config.command:
                raise create_error(
                    "TOOL_UNAVAILABLE",
                    detail=f"No command specified for stdio server '{self.name}'",
                )
            # FastMCP Client accepts file paths for stdio
            # Extract the script path from the command
            cmd_parts = self.config.command.split()
            if not cmd_parts:
                raise create_error(
                    "TOOL_UNAVAILABLE",
                    detail=f"Empty command for stdio server '{self.name}'",
                )
            # Return the command - FastMCP will handle it
            return self.config.command

        elif self.config.transport == MCPTransport.HTTP or self.config.transport == "http":
            if not self.config.url:
                raise create_error(
                    "TOOL_UNAVAILABLE",
                    detail=f"No URL specified for HTTP server '{self.name}'",
                )
            # Normalize URL for FastMCP - it expects the base URL
            url = self.config.url.rstrip("/")
            # FastMCP expects the /mcp endpoint for streamable-http
            if url.endswith("/sse"):
                url = url[:-4] + "/mcp"
            elif not url.endswith("/mcp"):
                url = url + "/mcp"
            return url

        else:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"Transport {self.config.transport} not supported",
            )

    async def _handle_tools_changed(self) -> None:
        """Handle tools/list_changed notification by refreshing tools."""
        try:
            old_tool_count = len(self._tools)
            tools = await self.refresh_tools()
            new_tool_count = len(tools)

            self._log(
                LogLevel.INFO,
                f"Tools refreshed via notification: {old_tool_count} -> {new_tool_count} tools",
            )

            # Notify callback if registered
            if self._on_tools_changed:
                try:
                    self._on_tools_changed(self.name, tools)
                except Exception as e:
                    self._log(LogLevel.WARN, f"Error in tools changed callback: {e}")

        except Exception as e:
            self._log(LogLevel.ERROR, f"Failed to refresh tools after notification: {e}")

    async def disconnect(self) -> None:
        """Gracefully disconnect from MCP server."""
        if self._status == ConnectionStatus.DISCONNECTED:
            return

        self._log(LogLevel.INFO, "Disconnecting from MCP server")

        # Close the FastMCP client context
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception as e:
                self._log(LogLevel.WARN, f"Error during disconnect: {e}")
            self._exit_stack = None

        self._client = None
        self._status = ConnectionStatus.DISCONNECTED
        self._tools.clear()
        self._log(LogLevel.INFO, "Disconnected")

    async def refresh_tools(self) -> list[ToolSchema]:
        """Fetch tool list from server using FastMCP Client.

        Returns:
            List of available tools

        Raises:
            AELError(TOOL_UNAVAILABLE) if server not connected
        """
        if (
            self._status != ConnectionStatus.CONNECTED
            and self._status != ConnectionStatus.CONNECTING
        ):
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"MCP server '{self.name}' not connected",
            )

        if not self._client:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"MCP client not initialized for '{self.name}'",
            )

        try:
            # Use FastMCP client to list tools
            tools_result = await self._client.list_tools()

            # Parse tools from FastMCP response
            self._tools.clear()
            for tool in tools_result:
                tool_schema = ToolSchema(
                    name=tool.name,
                    description=tool.description or "",
                    input_schema=tool.inputSchema if hasattr(tool, "inputSchema") else {},
                    output_schema=None,
                )
                self._tools[tool_schema.name] = tool_schema

            self._log(LogLevel.DEBUG, f"Refreshed {len(self._tools)} tools")
            return list(self._tools.values())

        except Exception as e:
            self._log(LogLevel.ERROR, f"Failed to refresh tools: {e}")
            raise

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        timeout_seconds: int | None = None,
    ) -> MCPCallResult:
        """Call a tool on this MCP server using FastMCP Client.

        Args:
            tool_name: Name of tool to call
            arguments: Tool arguments
            timeout_seconds: Optional timeout override (not used with FastMCP)

        Returns:
            MCPCallResult with content and metadata

        Raises:
            AELError(TOOL_UNAVAILABLE) if not connected
            AELError(TOOL_TIMEOUT) if call times out
            AELError(TOOL_FAILED) if tool returns error
        """
        if self._status != ConnectionStatus.CONNECTED:
            raise create_error(
                "TOOL_UNAVAILABLE",
                tool_name=tool_name,
                detail=f"MCP server '{self.name}' not connected",
            )

        # Check if tool exists
        if tool_name not in self._tools:
            raise create_error(
                "TOOL_REJECTED",
                tool_name=tool_name,
                detail=f"Tool '{tool_name}' not found on server '{self.name}'",
            )

        if not self._client:
            raise create_error(
                "TOOL_UNAVAILABLE",
                tool_name=tool_name,
                detail=f"MCP client not initialized for '{self.name}'",
            )

        start_time = time.time()

        try:
            # Use FastMCP client to call tool
            result = await self._client.call_tool(tool_name, arguments)
            duration_ms = int((time.time() - start_time) * 1000)

            # Extract content from FastMCP result
            # FastMCP returns a list of content items
            text_content = self._extract_fastmcp_content(result)

            # Check if result indicates an error
            is_error = False
            if hasattr(result, "isError"):
                is_error = result.isError

            return MCPCallResult(
                success=not is_error,
                content=text_content,
                raw_response={"result": str(result)},
                duration_ms=duration_ms,
                error=text_content if is_error else None,
                is_error=is_error,
                structured_content=None,
            )

        except asyncio.TimeoutError as e:
            duration_ms = int((time.time() - start_time) * 1000)
            raise create_error(
                "TOOL_TIMEOUT",
                tool_name=tool_name,
                detail=f"Tool call timed out after {timeout_seconds or self.config.timeout}s",
            ) from e
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self._log(LogLevel.ERROR, f"Tool call failed: {e}")
            raise

    def _extract_fastmcp_content(self, result: Any) -> str:
        """Extract text content from FastMCP call_tool result.

        Args:
            result: FastMCP call_tool result (list of content items)

        Returns:
            Concatenated text content
        """
        if not result:
            return ""

        # FastMCP returns a list of content items
        if isinstance(result, list):
            text_parts = []
            for item in result:
                if hasattr(item, "text"):
                    text_parts.append(item.text)
                elif hasattr(item, "type") and item.type == "text":
                    text_parts.append(getattr(item, "text", ""))
                elif isinstance(item, str):
                    text_parts.append(item)
            return "\n".join(text_parts)

        # Single item
        if hasattr(result, "text"):
            return result.text

        return str(result)
