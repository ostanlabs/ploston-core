"""MCP Client Manager for AEL.

Manages connections to MCP servers via stdio or HTTP transport.
Simplified version focused on tool execution for workflow engine.
"""

import logging
from typing import Any

from fastmcp.client.client import CallToolResult
from mcp.types import TextContent

try:
    from fastmcp import Client, FastMCP
    from fastmcp.client.logging import LogMessage
    from fastmcp.client.transports import StreamableHttpTransport
except ImportError as e:
    raise ImportError("FastMCP is required. Install it with: pip install fastmcp") from e

logger = logging.getLogger(__name__)


def convert_textcontent_list(content_list: list[Any]) -> str:
    """Convert MCP content list to plain text string.

    Args:
        content_list: List of content items from MCP response

    Returns:
        Concatenated text content as string
    """
    if not content_list:
        return ""

    result = []
    for item in content_list:
        if isinstance(item, TextContent):
            result.append(item.text)
        elif isinstance(item, dict) and item.get("type") == "text":
            result.append(item.get("text", ""))
        elif isinstance(item, str):
            result.append(item)

    return "\n".join(result)


class MCPClientManager:
    """Manager for MCP client connections using FastMCP.

    Simplified for AEL workflow execution - focuses on tool operations only.
    """

    def __init__(
        self,
        server_source: str | FastMCP,
        timeout: float = 300.0,
        headers: dict[str, str] | None = None,
        auth: str | Any | None = None,
    ):
        """Initialize MCP client manager.

        Args:
            server_source: Can be:
                - FastMCP instance (in-memory, for testing)
                - File path ending in .py or .js (stdio transport)
                - URL starting with http:// or https:// (HTTP transport)
            timeout: Default timeout for requests in seconds
            headers: Optional HTTP headers for HTTP/HTTPS connections
            auth: Optional authentication (bearer token string or httpx.Auth)
        """
        self.server_source = server_source
        self.client: Client[Any] | None = None
        self.timeout = timeout
        self._headers = headers
        self._auth = auth
        self._connected = False

    @staticmethod
    def _default_log_handler(message: LogMessage) -> None:
        """Default handler for MCP server log messages."""
        level_map = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }
        log_level = level_map.get(message.level.lower(), logging.INFO)
        logger.log(log_level, f"MCP Server: {message.data}")

    async def connect(self) -> None:
        """Establish connection to MCP server."""
        if self._connected:
            logger.debug("Already connected to MCP server")
            return

        try:
            # Check if server_source is an HTTP/HTTPS URL
            if isinstance(self.server_source, str) and (
                self.server_source.startswith("http://")
                or self.server_source.startswith("https://")
            ):
                # Use HTTP transport for URLs
                transport = StreamableHttpTransport(
                    url=self.server_source,
                    headers=self._headers,
                    auth=self._auth,
                )
                self.client = Client(
                    transport,
                    log_handler=self._default_log_handler,
                    timeout=self.timeout,
                )
            else:
                # For FastMCP instance or file paths, let FastMCP auto-detect
                self.client = Client(
                    self.server_source,
                    log_handler=self._default_log_handler,
                    timeout=self.timeout,
                )

            # Enter the client's context manager
            await self.client.__aenter__()  # type: ignore[no-untyped-call]
            self._connected = True

            # Verify connection
            await self.ping()
            logger.info("Successfully connected to MCP server")

        except Exception as e:
            logger.error(f"Failed to connect to MCP server: {e}")
            self._connected = False
            raise ConnectionError(f"MCP connection failed: {e}") from e

    async def disconnect(self) -> None:
        """Close connection to MCP server."""
        if not self._connected or not self.client:
            return

        try:
            await self.client.__aexit__(None, None, None)  # type: ignore[no-untyped-call]
            self._connected = False
            logger.info("Disconnected from MCP server")
        except Exception as e:
            logger.error(f"Error during disconnect: {e}")

    async def __aenter__(self) -> "MCPClientManager":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.disconnect()

    def is_connected(self) -> bool:
        """Check if client is connected."""
        return self._connected and self.client is not None

    async def ping(self) -> bool:
        """Ping the MCP server to verify connection.

        Returns:
            True if server responds, False otherwise
        """
        if not self.client:
            return False

        try:
            await self.client.ping()
            return True
        except Exception as e:
            logger.error(f"Ping failed: {e}")
            return False

    async def list_tools(self) -> list[Any]:
        """List available tools from MCP server.

        Returns:
            List of tool definitions in MCP format

        Raises:
            RuntimeError: If not connected to server
        """
        if not self.is_connected() or self.client is None:
            raise RuntimeError("Not connected to MCP server")

        try:
            tools = await self.client.list_tools()
            logger.debug(f"Retrieved {len(tools)} tools from MCP server")
            return tools
        except Exception as e:
            logger.error(f"Failed to list tools: {e}")
            raise

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None
    ) -> CallToolResult:
        """Execute a tool on the MCP server.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments as dictionary

        Returns:
            Tool execution result with content and metadata

        Raises:
            RuntimeError: If not connected to server
            Exception: If tool execution fails
        """
        if not self.is_connected() or self.client is None:
            raise RuntimeError("Not connected to MCP server")

        arguments = arguments or {}

        try:
            logger.debug(f"Calling tool '{tool_name}' with arguments: {arguments}")
            result = await self.client.call_tool(tool_name, arguments)

            # Add structured_content field for easier text access
            result.structured_content = convert_textcontent_list(result.content)  # type: ignore[assignment]

            logger.debug(f"Tool '{tool_name}' executed successfully")
            return result
        except Exception as e:
            logger.error(f"Failed to execute tool '{tool_name}': {e}")
            raise
