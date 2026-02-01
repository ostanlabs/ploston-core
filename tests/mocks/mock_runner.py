"""MockRunner - Test client that simulates a Local Runner for CP testing.

Implements S-189: Test Infrastructure
- T-542: MockRunner class
- T-543: WebSocket connection methods
- T-544: Registration methods
- T-545: Workflow methods
- T-546: Heartbeat methods
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class MockRunner:
    """Test client that simulates a Local Runner for CP testing.

    This mock runner can:
    - Connect to CP via WebSocket
    - Send registration messages
    - Report tool availability
    - Receive and respond to workflows
    - Send heartbeats

    Example:
        async with MockRunner("ws://localhost:8443", "token123", "test-runner") as runner:
            response = await runner.register()
            assert response["result"]["status"] == "ok"

            await runner.send_availability(["tool1", "tool2"], [])

            workflow = await runner.receive_workflow()
            await runner.send_workflow_result(workflow["id"], {"output": "done"})
    """

    def __init__(self, cp_url: str, token: str, name: str):
        """Initialize MockRunner.

        Args:
            cp_url: WebSocket URL of the Control Plane (e.g., ws://localhost:8443/runner/ws)
            token: Authentication token
            name: Runner name
        """
        self.cp_url = cp_url
        self.token = token
        self.name = name
        self.ws: Any | None = None  # websockets.WebSocketClientProtocol
        self.received_configs: list[dict] = []
        self.received_workflows: list[dict] = []
        self._message_id = 0

    def _next_id(self) -> int:
        """Get next message ID."""
        self._message_id += 1
        return self._message_id

    async def connect(self) -> None:
        """Connect to CP WebSocket."""
        try:
            import websockets
        except ImportError:
            raise ImportError("websockets package required: pip install websockets")

        url = self.cp_url
        if not url.endswith("/runner/ws"):
            url = f"{url.rstrip('/')}/runner/ws"

        logger.info(f"MockRunner connecting to {url}")
        self.ws = await websockets.connect(url)
        logger.info("MockRunner connected")

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        if self.ws:
            await self.ws.close()
            self.ws = None
            logger.info("MockRunner disconnected")

    async def __aenter__(self) -> MockRunner:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.disconnect()

    async def send(self, message: dict) -> None:
        """Send a JSON message."""
        if not self.ws:
            raise RuntimeError("Not connected")
        await self.ws.send(json.dumps(message))

    async def receive(self, timeout: float = 5.0) -> dict:
        """Receive a JSON message."""
        if not self.ws:
            raise RuntimeError("Not connected")
        data = await asyncio.wait_for(self.ws.recv(), timeout)
        return json.loads(data)

    async def register(self) -> dict:
        """Send runner/register message, return response.

        Returns:
            JSON-RPC response with result or error
        """
        msg_id = self._next_id()
        await self.send(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": "runner/register",
                "params": {"token": self.token, "name": self.name},
            }
        )
        return await self.receive()

    async def send_availability(
        self, available: list[str], unavailable: list[str] | None = None
    ) -> None:
        """Send runner/availability notification.

        Args:
            available: List of available tool names
            unavailable: List of unavailable tool names
        """
        await self.send(
            {
                "jsonrpc": "2.0",
                "method": "runner/availability",
                "params": {"available": available, "unavailable": unavailable or []},
            }
        )

    async def receive_config(self, timeout: float = 5.0) -> dict:
        """Wait for config/push message.

        Returns:
            Config params from the message
        """
        msg = await self.receive(timeout)
        if msg.get("method") != "config/push":
            raise ValueError(f"Expected config/push, got {msg.get('method')}")
        self.received_configs.append(msg["params"])
        return msg["params"]

    async def receive_workflow(self, timeout: float = 5.0) -> dict:
        """Wait for workflow/execute message.

        Returns:
            Full workflow message including id and params
        """
        msg = await self.receive(timeout)
        if msg.get("method") != "workflow/execute":
            raise ValueError(f"Expected workflow/execute, got {msg.get('method')}")
        self.received_workflows.append(msg)
        return msg

    async def send_workflow_result(self, request_id: int, result: dict) -> None:
        """Send workflow/result response.

        Args:
            request_id: The id from the workflow/execute request
            result: The workflow execution result
        """
        await self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def send_workflow_error(
        self, request_id: int, code: int, message: str, data: Any = None
    ) -> None:
        """Send workflow error response.

        Args:
            request_id: The id from the workflow/execute request
            code: Error code
            message: Error message
            data: Optional error data
        """
        error = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        await self.send({"jsonrpc": "2.0", "id": request_id, "error": error})

    async def send_heartbeat(self) -> None:
        """Send runner/heartbeat notification."""
        await self.send(
            {
                "jsonrpc": "2.0",
                "method": "runner/heartbeat",
                "params": {"timestamp": datetime.now(UTC).isoformat()},
            }
        )

    async def send_tool_proxy_request(
        self, tool_name: str, arguments: dict, request_id: int | None = None
    ) -> dict:
        """Send tool/proxy request to CP.

        Args:
            tool_name: Name of the tool to invoke
            arguments: Tool arguments
            request_id: Optional request ID (auto-generated if not provided)

        Returns:
            JSON-RPC response
        """
        msg_id = request_id or self._next_id()
        await self.send(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": "tool/proxy",
                "params": {"tool_name": tool_name, "arguments": arguments},
            }
        )
        return await self.receive()
