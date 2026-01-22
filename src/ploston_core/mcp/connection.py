"""MCP Connection - manages single MCP server connection."""

import asyncio
import json
import time
from datetime import datetime
from typing import Any

import httpx

from ploston_core.config.models import MCPServerDefinition
from ploston_core.errors import create_error
from ploston_core.logging.logger import AELLogger
from ploston_core.types import ConnectionStatus, LogLevel, MCPTransport

from .protocol import JSONRPCMessage
from .types import MCPCallResult, ServerStatus, ToolSchema


class MCPConnection:
    """Single MCP server connection.

    Manages the lifecycle of an MCP server connection,
    including spawning (for stdio), initialization, and tool calls.
    """

    def __init__(
        self,
        name: str,
        config: MCPServerDefinition,
        logger: AELLogger | None = None,
    ):
        """Initialize MCP connection.

        Args:
            name: Server name
            config: Server configuration
            logger: Optional logger
        """
        self.name = name
        self.config = config
        self._logger = logger
        self._status = ConnectionStatus.DISCONNECTED
        self._tools: dict[str, ToolSchema] = {}
        self._request_id = 0
        self._last_connected: datetime | None = None
        self._last_error: str | None = None

        # Transport-specific state
        # For stdio transport
        self._process: asyncio.subprocess.Process | None = None
        # For HTTP transport
        self._http_client: httpx.AsyncClient | None = None
        self._http_base_url: str | None = None
        self._http_session_id: str | None = None  # MCP session ID from server

    def _next_id(self) -> int:
        """Get next request ID."""
        self._request_id += 1
        return self._request_id

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

    async def connect(self) -> None:
        """Establish connection to MCP server.

        For stdio: spawns process and sends initialize.
        For http: validates endpoint and sends initialize.

        Raises:
            AELError(TOOL_UNAVAILABLE) if connection fails
        """
        if self._status == ConnectionStatus.CONNECTED:
            self._log(LogLevel.DEBUG, "Already connected")
            return

        self._status = ConnectionStatus.CONNECTING
        self._log(LogLevel.INFO, f"Connecting to MCP server (transport={self.config.transport})")

        try:
            if self.config.transport == MCPTransport.STDIO or self.config.transport == "stdio":
                await self._connect_stdio()
            elif self.config.transport == MCPTransport.HTTP or self.config.transport == "http":
                await self._connect_http()
            else:
                raise create_error(
                    "TOOL_UNAVAILABLE",
                    detail=f"Transport {self.config.transport} not supported",
                )

            # Send initialize
            await self._initialize()

            # Fetch tools
            await self.refresh_tools()

            self._status = ConnectionStatus.CONNECTED
            self._last_connected = datetime.now()
            self._last_error = None
            self._log(LogLevel.INFO, f"Connected successfully ({len(self._tools)} tools)")

        except Exception as e:
            self._status = ConnectionStatus.ERROR
            self._last_error = str(e)
            self._log(LogLevel.ERROR, f"Connection failed: {e}")
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"Failed to connect to MCP server '{self.name}': {e}",
            ) from e

    async def _connect_stdio(self) -> None:
        """Connect via stdio transport.

        Raises:
            AELError(TOOL_UNAVAILABLE) if process spawn fails
        """
        if not self.config.command:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"No command specified for stdio server '{self.name}'",
            )

        # Parse command and args
        cmd_parts = self.config.command.split()
        if not cmd_parts:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"Empty command for stdio server '{self.name}'",
            )

        # Build environment - merge with current environment
        import os

        env = {**os.environ, **(self.config.env or {})}

        try:
            # Spawn process
            self._process = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._log(LogLevel.DEBUG, f"Spawned process PID={self._process.pid}")

        except Exception as e:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"Failed to spawn MCP server process: {e}",
            ) from e

    async def _connect_http(self) -> None:
        """Connect via HTTP transport.

        Creates an HTTP client and validates the server endpoint.
        The URL should point to the MCP server's HTTP endpoint (e.g., http://host:port/sse).

        Raises:
            AELError(TOOL_UNAVAILABLE) if connection fails
        """
        if not self.config.url:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"No URL specified for HTTP server '{self.name}'",
            )

        # Parse and validate URL
        url = self.config.url.rstrip("/")
        self._http_base_url = url

        # Determine the MCP endpoint - FastMCP uses /mcp for JSON-RPC
        # If URL ends with /sse, replace with /mcp for JSON-RPC requests
        if url.endswith("/sse"):
            self._http_base_url = url[:-4] + "/mcp"
        elif not url.endswith("/mcp"):
            # Assume it's a base URL, append /mcp
            self._http_base_url = url + "/mcp"

        self._log(LogLevel.DEBUG, f"HTTP endpoint: {self._http_base_url}")

        try:
            # Create HTTP client with timeout
            # FastMCP requires Accept header with both application/json and text/event-stream
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
            )

            # Verify server is reachable by checking health endpoint
            health_url = url.rsplit("/", 1)[0] + "/health"
            try:
                response = await self._http_client.get(health_url)
                if response.status_code == 200:
                    self._log(LogLevel.DEBUG, f"Health check passed: {health_url}")
                else:
                    self._log(LogLevel.WARN, f"Health check returned {response.status_code}")
            except Exception as health_err:
                # Health check is optional, log but continue
                self._log(LogLevel.DEBUG, f"Health check skipped: {health_err}")

        except Exception as e:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"Failed to create HTTP client for '{self.name}': {e}",
            ) from e

    async def _initialize(self) -> None:
        """Send initialize request to MCP server.

        Raises:
            AELError(TOOL_UNAVAILABLE) if initialize fails
        """
        request = JSONRPCMessage.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "ael",
                    "version": "0.1.0",
                },
            },
            id=self._next_id(),
        )

        try:
            response = await self._send_request(request)
            if JSONRPCMessage.is_error(response):
                error = JSONRPCMessage.get_error(response)
                raise create_error(
                    "TOOL_UNAVAILABLE",
                    detail=f"Initialize failed: {error.get('message', 'Unknown error')}",
                )

            self._log(LogLevel.DEBUG, "Initialize successful")

        except Exception as e:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"Initialize request failed: {e}",
            ) from e

    async def _send_request(
        self, request: dict[str, Any], timeout_seconds: int | None = None
    ) -> dict[str, Any]:
        """Send JSON-RPC request and wait for response.

        Dispatches to transport-specific implementation.

        Args:
            request: JSON-RPC request dict
            timeout_seconds: Optional timeout in seconds

        Returns:
            JSON-RPC response dict

        Raises:
            AELError(TOOL_TIMEOUT) if timeout
            AELError(TOOL_UNAVAILABLE) if not connected
        """
        if self.config.transport == MCPTransport.HTTP or self.config.transport == "http":
            return await self._send_request_http(request, timeout_seconds)
        else:
            return await self._send_request_stdio(request, timeout_seconds)

    async def _send_request_stdio(
        self, request: dict[str, Any], timeout_seconds: int | None = None
    ) -> dict[str, Any]:
        """Send JSON-RPC request via stdio transport.

        Args:
            request: JSON-RPC request dict
            timeout_seconds: Optional timeout in seconds

        Returns:
            JSON-RPC response dict

        Raises:
            AELError(TOOL_TIMEOUT) if timeout
            AELError(TOOL_UNAVAILABLE) if not connected
        """
        if not self._process or not self._process.stdin or not self._process.stdout:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"Not connected to MCP server '{self.name}'",
            )

        # Send request
        request_str = json.dumps(request) + "\n"
        self._process.stdin.write(request_str.encode("utf-8"))
        await self._process.stdin.drain()

        # Read response with timeout
        timeout_val = timeout_seconds or 30
        try:
            response_line = await asyncio.wait_for(
                self._process.stdout.readline(),
                timeout=timeout_val,
            )
        except TimeoutError as e:
            raise create_error(
                "TOOL_TIMEOUT",
                detail=f"Request to MCP server '{self.name}' timed out after {timeout_val}s",
            ) from e

        if not response_line:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"MCP server '{self.name}' closed connection",
            )

        # Parse response
        return JSONRPCMessage.parse(response_line)

    async def _send_request_http(
        self, request: dict[str, Any], timeout_seconds: int | None = None
    ) -> dict[str, Any]:
        """Send JSON-RPC request via HTTP transport.

        FastMCP servers return responses in SSE (Server-Sent Events) format.
        We parse the SSE response to extract the JSON-RPC message.

        The server returns a session ID in the mcp-session-id header on initialize,
        which must be included in subsequent requests.

        Args:
            request: JSON-RPC request dict
            timeout_seconds: Optional timeout in seconds

        Returns:
            JSON-RPC response dict

        Raises:
            AELError(TOOL_TIMEOUT) if timeout
            AELError(TOOL_UNAVAILABLE) if not connected
        """
        if not self._http_client or not self._http_base_url:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"Not connected to MCP server '{self.name}'",
            )

        timeout_val = timeout_seconds or self.config.timeout

        try:
            # Build headers - include session ID if we have one
            headers: dict[str, str] = {}
            if self._http_session_id:
                headers["mcp-session-id"] = self._http_session_id

            # Send POST request with JSON-RPC payload
            response = await self._http_client.post(
                self._http_base_url,
                json=request,
                headers=headers,
                timeout=timeout_val,
            )

            if response.status_code != 200:
                raise create_error(
                    "TOOL_UNAVAILABLE",
                    detail=f"HTTP request failed with status {response.status_code}: {response.text}",
                )

            # Capture session ID from response headers (set on initialize)
            session_id = response.headers.get("mcp-session-id")
            if session_id and not self._http_session_id:
                self._http_session_id = session_id
                self._log(LogLevel.DEBUG, f"Captured MCP session ID: {session_id[:8]}...")

            # Parse response - FastMCP returns SSE format
            content_type = response.headers.get("content-type", "")
            response_text = response.text

            if "text/event-stream" in content_type or response_text.startswith("event:"):
                # Parse SSE response
                return self._parse_sse_response(response_text)
            else:
                # Plain JSON response
                return response.json()

        except httpx.TimeoutException as e:
            raise create_error(
                "TOOL_TIMEOUT",
                detail=f"Request to MCP server '{self.name}' timed out after {timeout_val}s",
            ) from e
        except httpx.HTTPError as e:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"HTTP error communicating with MCP server '{self.name}': {e}",
            ) from e

    def _parse_sse_response(self, sse_text: str) -> dict[str, Any]:
        """Parse SSE (Server-Sent Events) response to extract JSON-RPC message.

        SSE format:
            event: message
            data: {"jsonrpc": "2.0", ...}

        Args:
            sse_text: Raw SSE response text

        Returns:
            Parsed JSON-RPC response dict

        Raises:
            AELError(TOOL_UNAVAILABLE) if parsing fails
        """
        # Parse SSE lines to find the data payload
        data_lines = []
        for line in sse_text.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                # Extract data after "data:" prefix
                data_content = line[5:].strip()
                if data_content:
                    data_lines.append(data_content)

        if not data_lines:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"No data found in SSE response: {sse_text[:200]}",
            )

        # Join data lines (in case data spans multiple lines)
        data_str = "".join(data_lines)

        try:
            return json.loads(data_str)
        except json.JSONDecodeError as e:
            raise create_error(
                "TOOL_UNAVAILABLE",
                detail=f"Failed to parse SSE data as JSON: {e}",
            ) from e

    async def disconnect(self) -> None:
        """Gracefully disconnect from MCP server."""
        if self._status == ConnectionStatus.DISCONNECTED:
            return

        self._log(LogLevel.INFO, "Disconnecting from MCP server")

        # Clean up stdio transport
        if self._process:
            try:
                # Terminate process
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                # Force kill if doesn't terminate
                self._process.kill()
                await self._process.wait()
            except Exception as e:
                self._log(LogLevel.WARN, f"Error during disconnect: {e}")

            self._process = None

        # Clean up HTTP transport
        if self._http_client:
            try:
                await self._http_client.aclose()
            except Exception as e:
                self._log(LogLevel.WARN, f"Error closing HTTP client: {e}")

            self._http_client = None
            self._http_base_url = None
            self._http_session_id = None

        self._status = ConnectionStatus.DISCONNECTED
        self._tools.clear()
        self._log(LogLevel.INFO, "Disconnected")

    async def refresh_tools(self) -> list[ToolSchema]:
        """Fetch tool list from server.

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

        request = JSONRPCMessage.request("tools/list", id=self._next_id())

        try:
            response = await self._send_request(request)

            if JSONRPCMessage.is_error(response):
                error = JSONRPCMessage.get_error(response)
                raise create_error(
                    "TOOL_UNAVAILABLE",
                    detail=f"tools/list failed: {error.get('message', 'Unknown error')}",
                )

            result = JSONRPCMessage.get_result(response)
            tools_data = result.get("tools", [])

            # Parse tools
            self._tools.clear()
            for tool_data in tools_data:
                tool = ToolSchema(
                    name=tool_data["name"],
                    description=tool_data.get("description", ""),
                    input_schema=tool_data.get("inputSchema", {}),
                    output_schema=tool_data.get("outputSchema"),
                )
                self._tools[tool.name] = tool

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
        """Call a tool on this MCP server.

        Args:
            tool_name: Name of tool to call
            arguments: Tool arguments
            timeout: Optional timeout override

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
                detail=f"MCP server '{self.name}' not connected",
            )

        # Check if tool exists
        if tool_name not in self._tools:
            raise create_error(
                "TOOL_REJECTED",
                detail=f"Tool '{tool_name}' not found on server '{self.name}'",
            )

        request = JSONRPCMessage.request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments,
            },
            id=self._next_id(),
        )

        start_time = time.time()

        try:
            response = await self._send_request(request, timeout_seconds=timeout_seconds)
            duration_ms = int((time.time() - start_time) * 1000)

            # Check for JSON-RPC error
            if JSONRPCMessage.is_error(response):
                error = JSONRPCMessage.get_error(response)
                error_msg = error.get("message", "Unknown error")
                return MCPCallResult(
                    success=False,
                    content=None,
                    raw_response=response,
                    duration_ms=duration_ms,
                    error=error_msg,
                    is_error=True,
                )

            # Get result
            result = JSONRPCMessage.get_result(response)

            # Check for MCP-level error (isError flag)
            is_error = result.get("isError", False)
            content = result.get("content", [])
            structured_content = result.get("structuredContent")

            # Extract text content
            text_content = self._extract_content(content)

            return MCPCallResult(
                success=not is_error,
                content=text_content,
                raw_response=response,
                duration_ms=duration_ms,
                error=text_content if is_error else None,
                is_error=is_error,
                structured_content=structured_content,
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            self._log(LogLevel.ERROR, f"Tool call failed: {e}")
            raise

    def _extract_content(self, content: list[Any]) -> str:
        """Extract text content from MCP content list.

        Args:
            content: List of content items

        Returns:
            Concatenated text content
        """
        if not content:
            return ""

        result = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    result.append(item.get("text", ""))
            elif isinstance(item, str):
                result.append(item)

        return "\n".join(result)
