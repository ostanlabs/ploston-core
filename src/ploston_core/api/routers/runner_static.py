"""Runner static endpoints.

Implements S-186: Runner Static Endpoints
- GET /runner/install.sh - Installation script
- GET /runner/ca.crt - CA certificate (placeholder)
- WebSocket /runner/ws - Runner WebSocket connection

These endpoints are used by runners to connect to the control plane.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

runner_static_router = APIRouter(prefix="/runner", tags=["runner-static"])

logger = logging.getLogger(__name__)


INSTALL_SCRIPT = """#!/bin/bash
set -e

# Ploston Runner Install Script
# Usage: curl -fsSL https://cp/runner/install.sh | bash -s -- --cp URL --token TOKEN

CP_URL=""
TOKEN=""
NAME=""

# Parse arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --cp)
      CP_URL="$2"
      shift 2
      ;;
    --token)
      TOKEN="$2"
      shift 2
      ;;
    --name)
      NAME="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
done

# Validate required args
if [[ -z "$CP_URL" ]] || [[ -z "$TOKEN" ]]; then
  echo "Usage: $0 --cp <control-plane-url> --token <runner-token> [--name <runner-name>]"
  exit 1
fi

# Install if not present
if ! command -v ploston-runner &> /dev/null; then
  echo "Installing ploston-runner..."
  if command -v uv &> /dev/null; then
    uv tool install ploston-runner
  else
    pip install ploston-runner
  fi
fi

# Build command
CMD="ploston-runner connect --cp-url $CP_URL --token $TOKEN"
if [[ -n "$NAME" ]]; then
  CMD="$CMD --name $NAME"
fi

# Run (foreground, blocks until stopped)
echo "Connecting to $CP_URL..."
exec $CMD
"""


@runner_static_router.get(
    "/install.sh",
    response_class=PlainTextResponse,
    summary="Get runner installation script",
    description="Returns a shell script that installs and connects ploston-runner.",
)
async def get_install_script() -> PlainTextResponse:
    """Return the runner installation script.

    No authentication required.
    """
    return PlainTextResponse(
        content=INSTALL_SCRIPT,
        media_type="text/x-shellscript",
        headers={"Content-Disposition": "attachment; filename=install.sh"},
    )


@runner_static_router.get(
    "/ca.crt",
    response_class=PlainTextResponse,
    summary="Get CA certificate",
    description="Returns the control plane's CA certificate in PEM format.",
)
async def get_ca_certificate(request: Request) -> PlainTextResponse:
    """Return the CA certificate.

    No authentication required.

    Note: In production, this should return the actual CA certificate
    used for TLS. For now, returns a placeholder message.
    """
    # Check if CA cert is configured in app state
    ca_cert = getattr(request.app.state, "ca_certificate", None)

    if ca_cert:
        return PlainTextResponse(
            content=ca_cert,
            media_type="application/x-pem-file",
        )

    # Return placeholder if not configured
    return PlainTextResponse(
        content="# CA certificate not configured\n# Configure TLS to enable this endpoint\n",
        media_type="text/plain",
        status_code=503,
    )


@dataclass
class RunnerConnection:
    """Active runner WebSocket connection."""
    runner_id: str
    runner_name: str
    websocket: WebSocket
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    pending_requests: dict[int, asyncio.Future] = field(default_factory=dict)
    next_request_id: int = 1


# Global connection tracking (per-process)
_runner_connections: dict[str, RunnerConnection] = {}


async def _send_response(websocket: WebSocket, msg_id: int | None, result: Any) -> None:
    """Send JSON-RPC response."""
    response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
    await websocket.send_json(response)


async def _send_error(websocket: WebSocket, msg_id: int | None, code: int, message: str) -> None:
    """Send JSON-RPC error response."""
    response = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
    await websocket.send_json(response)


async def _send_notification(websocket: WebSocket, method: str, params: dict) -> None:
    """Send JSON-RPC notification."""
    notification = {"jsonrpc": "2.0", "method": method, "params": params}
    await websocket.send_json(notification)


@runner_static_router.websocket("/ws")
async def runner_websocket(websocket: WebSocket) -> None:
    """WebSocket endpoint for runner connections.

    Runners connect here and authenticate via the first message
    (runner/register with token). See LOCAL_RUNNER_IMPL_SPEC S-176.
    """
    # Get runner registry from app state
    runner_registry = getattr(websocket.app.state, "runner_registry", None)

    if runner_registry is None:
        await websocket.close(code=1013, reason="Runner registry not configured")
        return

    # Accept the connection
    await websocket.accept()

    runner_id: str | None = None

    try:
        while True:
            # Receive message
            data = await websocket.receive_json()

            method = data.get("method")
            params = data.get("params", {})
            msg_id = data.get("id")

            # Handle registration (must be first message)
            if method == "runner/register":
                token = params.get("token")
                name = params.get("name")

                if not token or not name:
                    await _send_error(websocket, msg_id, -32602, "Missing token or name")
                    continue

                # Validate token
                runner = runner_registry.get_by_token(token)
                if not runner:
                    await _send_error(websocket, msg_id, -32001, "Invalid token")
                    continue

                if runner.name != name:
                    await _send_error(websocket, msg_id, -32001, "Token/name mismatch")
                    continue

                # Register connection
                runner_id = runner.id
                _runner_connections[runner_id] = RunnerConnection(
                    runner_id=runner.id,
                    runner_name=runner.name,
                    websocket=websocket,
                )
                runner_registry.set_connected(runner_id)

                logger.info(f"Runner '{name}' connected (id={runner_id})")
                await _send_response(websocket, msg_id, {"status": "ok"})

                # Build MCPs to push: config-based + API-provided (API takes precedence)
                mcps_to_push: dict[str, dict] = {}

                # 1. Get pre-configured MCPs from ael_config.runners
                ael_config = getattr(websocket.app.state, "ael_config", None)
                if ael_config and hasattr(ael_config, "runners"):
                    runner_def = ael_config.runners.get(name)
                    if runner_def and runner_def.mcp_servers:
                        for mcp_name, mcp_def in runner_def.mcp_servers.items():
                            # Convert dataclass to dict for JSON serialization
                            mcps_to_push[mcp_name] = {
                                "command": mcp_def.command,
                                "args": mcp_def.args,
                                "url": mcp_def.url,
                                "env": mcp_def.env,
                                "timeout": mcp_def.timeout,
                            }
                        logger.info(
                            f"Runner '{name}' has {len(runner_def.mcp_servers)} "
                            "pre-configured MCPs from config"
                        )

                # 2. Merge with API-provided MCPs (these take precedence)
                if runner.mcps:
                    mcps_to_push.update(runner.mcps)

                # Push config to runner
                await _send_notification(websocket, "config/push", {"mcps": mcps_to_push})
                continue

            # All other methods require authentication
            if not runner_id:
                await _send_error(websocket, msg_id, -32600, "Not authenticated")
                continue

            # Handle heartbeat
            if method == "runner/heartbeat":
                runner_registry.update_heartbeat(runner_id)
                # Heartbeats are notifications, no response needed
                continue

            # Handle availability
            if method == "runner/availability":
                tools = params.get("tools", [])
                runner_registry.update_available_tools(runner_id, tools)
                runner = runner_registry.get(runner_id)
                if runner:
                    logger.info(f"Runner '{runner.name}' reported {len(tools)} tools")
                continue

            # Handle tool/proxy - runner proxying a tool call to CP
            if method == "tool/proxy":
                tool_invoker = getattr(websocket.app.state, "tool_invoker", None)
                if tool_invoker is None:
                    await _send_error(websocket, msg_id, -32603, "Tool invoker not configured")
                    continue

                tool_name = params.get("tool")
                tool_args = params.get("args", {})

                if not tool_name:
                    await _send_error(websocket, msg_id, -32602, "Missing tool name")
                    continue

                try:
                    logger.info(f"Proxying tool '{tool_name}' for runner '{runner_id}'")
                    result = await tool_invoker.invoke(
                        tool_name=tool_name,
                        params=tool_args,
                    )

                    if result.success:
                        await _send_response(websocket, msg_id, {
                            "status": "success",
                            "output": result.output,
                        })
                    else:
                        await _send_error(
                            websocket,
                            msg_id,
                            -32000,
                            str(result.error) if result.error else "Tool execution failed",
                        )
                except Exception as e:
                    logger.exception(f"Tool proxy failed: {e}")
                    await _send_error(websocket, msg_id, -32000, f"Tool execution failed: {e}")
                continue

            # Handle response to our requests
            if msg_id is not None and runner_id in _runner_connections:
                conn = _runner_connections[runner_id]
                if msg_id in conn.pending_requests:
                    future = conn.pending_requests.pop(msg_id)
                    if "error" in data:
                        future.set_exception(Exception(data["error"].get("message", "Unknown error")))
                    else:
                        future.set_result(data.get("result"))
                    continue

            # Unknown method
            await _send_error(websocket, msg_id, -32601, f"Unknown method: {method}")

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.exception(f"WebSocket error: {e}")
    finally:
        # Cleanup on disconnect
        if runner_id:
            conn = _runner_connections.pop(runner_id, None)
            if conn:
                runner_registry.set_disconnected(runner_id)
                logger.info(f"Runner '{conn.runner_name}' disconnected")
                # Cancel pending requests
                for future in conn.pending_requests.values():
                    future.cancel()
