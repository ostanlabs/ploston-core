"""WebSocket Server for Runner Connections.

Implements the CP-side WebSocket server that runners connect to.
Handles:
- runner/register messages
- runner/heartbeat messages
- runner/availability messages
- workflow/execute dispatch
- tool/call dispatch
- tool/proxy handling
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
except ImportError:
    websockets = None  # type: ignore
    WebSocketServerProtocol = Any  # type: ignore

from ploston_core.runner_management.registry import (
    RunnerRegistry,
)

logger = logging.getLogger(__name__)


@dataclass
class RunnerConnection:
    """Active runner connection."""
    runner_id: str
    runner_name: str
    websocket: WebSocketServerProtocol
    connected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    pending_requests: dict[int, asyncio.Future] = field(default_factory=dict)
    next_request_id: int = 1


class RunnerWebSocketServer:
    """WebSocket server for runner connections.

    Handles the CP side of the runner protocol:
    - Accepts runner connections
    - Validates tokens
    - Processes JSON-RPC messages
    - Dispatches workflows and tool calls
    """

    def __init__(
        self,
        registry: RunnerRegistry,
        host: str = "0.0.0.0",
        port: int = 8443,
    ) -> None:
        """Initialize the server.

        Args:
            registry: Runner registry for authentication and tracking
            host: Host to bind to
            port: Port to listen on
        """
        if websockets is None:
            raise ImportError("websockets package required: pip install websockets")

        self._registry = registry
        self._host = host
        self._port = port
        self._connections: dict[str, RunnerConnection] = {}  # runner_id -> connection
        self._server: Any = None
        self._running = False

        # Message handlers
        self._handlers: dict[str, Callable] = {
            "runner/register": self._handle_register,
            "runner/heartbeat": self._handle_heartbeat,
            "runner/availability": self._handle_availability,
            "workflow/result": self._handle_workflow_result,
        }

    async def start(self) -> None:
        """Start the WebSocket server."""
        self._running = True
        self._server = await websockets.serve(
            self._handle_connection,
            self._host,
            self._port,
        )
        logger.info(f"Runner WebSocket server started on ws://{self._host}:{self._port}")

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()

        # Disconnect all runners
        for conn in list(self._connections.values()):
            await self._disconnect_runner(conn.runner_id)

        logger.info("Runner WebSocket server stopped")

    async def _handle_connection(self, websocket: WebSocketServerProtocol) -> None:
        """Handle a new WebSocket connection."""
        runner_id: str | None = None

        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    runner_id = await self._process_message(websocket, data, runner_id)
                except json.JSONDecodeError:
                    await self._send_error(websocket, None, -32700, "Parse error")
                except Exception as e:
                    logger.exception(f"Error processing message: {e}")
                    await self._send_error(websocket, None, -32603, str(e))
        except websockets.ConnectionClosed:
            pass
        finally:
            if runner_id:
                await self._disconnect_runner(runner_id)

    async def _process_message(
        self,
        websocket: WebSocketServerProtocol,
        data: dict,
        current_runner_id: str | None,
    ) -> str | None:
        """Process a JSON-RPC message.

        Returns the runner_id if authenticated.
        """
        method = data.get("method")
        params = data.get("params", {})
        msg_id = data.get("id")

        # Handle registration first (before authentication)
        if method == "runner/register":
            return await self._handle_register(websocket, msg_id, params)

        # All other methods require authentication
        if not current_runner_id:
            await self._send_error(websocket, msg_id, -32600, "Not authenticated")
            return None

        # Route to handler
        handler = self._handlers.get(method)
        if handler:
            await handler(websocket, msg_id, params, current_runner_id)
        elif msg_id is not None:
            # It's a response to a request we sent
            await self._handle_response(current_runner_id, data)
        else:
            await self._send_error(websocket, msg_id, -32601, f"Unknown method: {method}")

        return current_runner_id

    async def _handle_register(
        self,
        websocket: WebSocketServerProtocol,
        msg_id: int | None,
        params: dict,
    ) -> str | None:
        """Handle runner/register message."""
        token = params.get("token")
        name = params.get("name")

        if not token or not name:
            await self._send_error(websocket, msg_id, -32602, "Missing token or name")
            return None

        # Validate token
        runner = self._registry.get_by_token(token)
        if not runner:
            await self._send_error(websocket, msg_id, -32001, "Invalid token")
            return None

        if runner.name != name:
            await self._send_error(websocket, msg_id, -32001, "Token/name mismatch")
            return None

        # Register connection
        self._connections[runner.id] = RunnerConnection(
            runner_id=runner.id,
            runner_name=runner.name,
            websocket=websocket,
        )
        self._registry.set_connected(runner.id)

        logger.info(f"Runner '{name}' connected (id={runner.id})")

        # Send success response
        await self._send_response(websocket, msg_id, {"status": "ok"})

        # Push config to runner
        await self._push_config(runner.id)

        return runner.id

    async def _handle_heartbeat(
        self,
        websocket: WebSocketServerProtocol,
        msg_id: int | None,
        params: dict,
        runner_id: str,
    ) -> None:
        """Handle runner/heartbeat message."""
        self._registry.update_heartbeat(runner_id)
        # Heartbeats are notifications, no response needed

    async def _handle_availability(
        self,
        websocket: WebSocketServerProtocol,
        msg_id: int | None,
        params: dict,
        runner_id: str,
    ) -> None:
        """Handle runner/availability message."""
        tools = params.get("tools", [])
        self._registry.update_available_tools(runner_id, tools)

        runner = self._registry.get(runner_id)
        if runner:
            logger.info(f"Runner '{runner.name}' reported {len(tools)} tools")

        # Availability is a notification, no response needed

    async def _handle_workflow_result(
        self,
        websocket: WebSocketServerProtocol,
        msg_id: int | None,
        params: dict,
        runner_id: str,
    ) -> None:
        """Handle workflow/result message."""
        # This is a response to a workflow/execute we sent
        # The actual handling is done via pending_requests
        pass

    async def _handle_response(self, runner_id: str, data: dict) -> None:
        """Handle a response to a request we sent."""
        msg_id = data.get("id")
        conn = self._connections.get(runner_id)

        if conn and msg_id in conn.pending_requests:
            future = conn.pending_requests.pop(msg_id)
            if "error" in data:
                future.set_exception(Exception(data["error"].get("message", "Unknown error")))
            else:
                future.set_result(data.get("result"))

    async def _disconnect_runner(self, runner_id: str) -> None:
        """Handle runner disconnection."""
        conn = self._connections.pop(runner_id, None)
        if conn:
            self._registry.set_disconnected(runner_id)
            logger.info(f"Runner '{conn.runner_name}' disconnected")

            # Cancel pending requests
            for future in conn.pending_requests.values():
                future.cancel()

    async def _push_config(self, runner_id: str) -> None:
        """Push MCP configuration to a runner."""
        runner = self._registry.get(runner_id)
        conn = self._connections.get(runner_id)

        if not runner or not conn:
            return

        await self._send_notification(
            conn.websocket,
            "config/push",
            {"mcps": runner.mcps},
        )

    async def _send_response(
        self,
        websocket: WebSocketServerProtocol,
        msg_id: int | None,
        result: Any,
    ) -> None:
        """Send a JSON-RPC response."""
        response = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": result,
        }
        await websocket.send(json.dumps(response))

    async def _send_error(
        self,
        websocket: WebSocketServerProtocol,
        msg_id: int | None,
        code: int,
        message: str,
    ) -> None:
        """Send a JSON-RPC error response."""
        response = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        }
        await websocket.send(json.dumps(response))

    async def _send_notification(
        self,
        websocket: WebSocketServerProtocol,
        method: str,
        params: dict,
    ) -> None:
        """Send a JSON-RPC notification (no id)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        await websocket.send(json.dumps(notification))

    async def send_workflow_execute(
        self,
        runner_id: str,
        workflow_yaml: str,
        inputs: dict | None = None,
        timeout: float = 300.0,
    ) -> dict:
        """Send workflow/execute to a runner and wait for result.

        Args:
            runner_id: Target runner ID
            workflow_yaml: Workflow YAML definition
            inputs: Optional workflow inputs
            timeout: Timeout in seconds

        Returns:
            Workflow execution result

        Raises:
            ValueError: If runner not connected
            asyncio.TimeoutError: If execution times out
        """
        conn = self._connections.get(runner_id)
        if not conn:
            raise ValueError(f"Runner {runner_id} not connected")

        request_id = conn.next_request_id
        conn.next_request_id += 1

        future: asyncio.Future = asyncio.Future()
        conn.pending_requests[request_id] = future

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "workflow/execute",
            "params": {
                "workflow": workflow_yaml,
                "inputs": inputs or {},
            },
        }

        await conn.websocket.send(json.dumps(request))

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            conn.pending_requests.pop(request_id, None)
            raise

    async def send_tool_call(
        self,
        runner_id: str,
        tool_name: str,
        arguments: dict | None = None,
        timeout: float = 60.0,
    ) -> dict:
        """Send tool/call to a runner and wait for result.

        Args:
            runner_id: Target runner ID
            tool_name: Tool to call
            arguments: Tool arguments
            timeout: Timeout in seconds

        Returns:
            Tool call result
        """
        conn = self._connections.get(runner_id)
        if not conn:
            raise ValueError(f"Runner {runner_id} not connected")

        request_id = conn.next_request_id
        conn.next_request_id += 1

        future: asyncio.Future = asyncio.Future()
        conn.pending_requests[request_id] = future

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tool/call",
            "params": {
                "tool": tool_name,
                "arguments": arguments or {},
            },
        }

        await conn.websocket.send(json.dumps(request))

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            conn.pending_requests.pop(request_id, None)
            raise

    def is_runner_connected(self, runner_id: str) -> bool:
        """Check if a runner is connected."""
        return runner_id in self._connections

    def get_connected_runners(self) -> list[str]:
        """Get list of connected runner IDs."""
        return list(self._connections.keys())
