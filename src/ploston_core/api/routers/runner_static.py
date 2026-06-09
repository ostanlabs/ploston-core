"""Runner static endpoints.

Implements S-186: Runner Static Endpoints
- GET /runner/install.sh - Installation script
- GET /runner/ca.crt - CA certificate (placeholder)
- WebSocket /runner/ws - Runner WebSocket connection

These endpoints are used by runners to connect to the control plane.
"""

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

runner_static_router = APIRouter(prefix="/runner", tags=["runner-static"])

logger = logging.getLogger(__name__)


# CR-2: in "proxy" runner_tls_mode, TLS/mTLS is terminated UPSTREAM by an
# ingress (k8s) or a bundled reverse-proxy (compose). The trusted proxy verifies
# the runner's client cert (issued by the EmbeddedCA) and forwards the verified
# Common Name to the CP in this header. Its absence in proxy mode means the
# request bypassed the trusted proxy and MUST be rejected.
RUNNER_CLIENT_CN_HEADER = "X-Runner-Client-CN"

# WebSocket close code used when a connection violates the proxy-mode policy
# (missing or mismatched forwarded client identity). 1008 = policy violation.
_WS_POLICY_VIOLATION_CODE = 1008


def _extract_cn(value: str) -> str:
    """Extract the Common Name from a forwarded client-cert identity.

    Trusted proxies forward the verified identity in one of two shapes,
    depending on the proxy:

      * a **bare CN** — nginx-ingress' ``$ssl_client_s_dn_cn``, e.g.
        ``runner-foo``;
      * a **full RFC 4514 subject DN** — Caddy's resolvable
        ``{http.request.tls.client.subject}`` placeholder, e.g.
        ``CN=runner-foo,OU=...,O=Ploston,C=US``.

    (Caddy has no built-in CN-only placeholder, so the bundled compose proxy
    forwards the whole subject DN; V-1 verified this against a live Caddy.)

    Return the CN component for a DN, or the value unchanged when it is not a
    DN. RFC 4514 escaped commas (``\\,``) inside a value are preserved.
    """
    if "=" not in value:
        return value
    for part in re.split(r"(?<!\\),", value):
        key, sep, val = part.partition("=")
        if sep and key.strip().upper() == "CN":
            return val.strip().replace("\\,", ",")
    return value


def _cn_matches_runner(cn: str, runner_name: str, runner_id: str) -> bool:
    """Return True if a forwarded client-cert identity identifies this runner.

    The EmbeddedCA (CR-2) issues runner client certs with
    ``CN = f"runner-{runner_name}"`` (see embedded_ca.generate_runner_cert).
    The forwarded value may be a bare CN or a full subject DN (see
    :func:`_extract_cn`); we normalise to the CN, then accept the raw name or
    id as well as the ``runner-`` prefixed forms to remain robust to how the
    upstream proxy chooses to forward the verified identity.
    """
    if not cn:
        return False
    cn = _extract_cn(cn)
    accepted = {
        runner_name,
        runner_id,
        f"runner-{runner_name}",
        f"runner-{runner_id}",
    }
    return cn in accepted


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

    Serves the live EmbeddedCA cert (CR-2) when one is attached to
    app.state.embedded_ca, falling back to a pre-rendered PEM string in
    app.state.ca_certificate. Runners download this to verify the CP server
    cert during the mTLS handshake.
    """
    # Prefer the live EmbeddedCA (CR-2): always serves the real CA PEM.
    embedded_ca = getattr(request.app.state, "embedded_ca", None)
    if embedded_ca is not None:
        try:
            return PlainTextResponse(
                content=embedded_ca.get_ca_cert_pem().decode(),
                media_type="application/x-pem-file",
            )
        except Exception as e:  # CA not initialized yet
            logger.warning(f"[ws] embedded CA present but not serving cert: {e}")

    # Fallback: a pre-rendered PEM string in app state.
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
    connected_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    pending_requests: dict[int, asyncio.Future] = field(default_factory=dict)
    next_request_id: int = 1
    # H-7: unique per physical connection so a reconnect's cleanup of the OLD
    # socket does not tear down the NEW (live) connection.
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)


# Global connection tracking (per-process)
_runner_connections: dict[str, RunnerConnection] = {}


def _cleanup_runner_connection(
    runner_registry: Any,
    runner_id: str,
    conn: RunnerConnection,
) -> None:
    """Tear down a runner connection, but ONLY if it is still the live one.

    H-7: connections are keyed by runner_id. On reconnect, the new socket
    replaces the old entry in _runner_connections. When the old socket's
    finally-block runs, it must NOT pop/disconnect the new connection. We only
    clean up when the stored connection IS this exact session.
    """
    current = _runner_connections.get(runner_id)
    if current is not conn:
        # A newer connection (reconnect) has already taken over — leave it alone.
        logger.info(
            f"Stale connection cleanup ignored for runner '{conn.runner_name}' "
            f"(session={conn.session_id}); a newer session is live"
        )
        # Still cancel this stale socket's own pending requests.
        for future in conn.pending_requests.values():
            if not future.done():
                future.cancel()
        return

    _runner_connections.pop(runner_id, None)
    runner_registry.set_disconnected(runner_id)
    logger.info(f"Runner '{conn.runner_name}' disconnected")
    for future in conn.pending_requests.values():
        if not future.done():
            future.cancel()


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


async def send_tool_call_to_runner(
    runner_id: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Send tool/call to a runner and wait for result.

    This is used by MCPFrontend to route tool calls to runners
    based on prefix routing (per DEC-123).

    Args:
        runner_id: Target runner ID
        tool_name: Tool to call (without runner prefix)
        arguments: Tool arguments
        timeout: Timeout in seconds

    Returns:
        Tool call result dict with 'output' or 'error'

    Raises:
        ValueError: If runner not connected
        asyncio.TimeoutError: If call times out
    """
    conn = _runner_connections.get(runner_id)
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

    try:
        logger.info(
            f"[trace] cp->runner runner_id={runner_id} id={request_id} "
            f"tool={tool_name} arguments={json.dumps(arguments or {}, default=str)}"
        )
    except Exception:
        pass

    await conn.websocket.send_json(request)

    try:
        return await asyncio.wait_for(future, timeout=timeout)
    except TimeoutError:
        conn.pending_requests.pop(request_id, None)
        raise


def get_runner_connection(runner_id: str) -> RunnerConnection | None:
    """Get a runner connection by ID.

    Used to check if a runner is connected before routing.
    """
    return _runner_connections.get(runner_id)


def is_runner_connected(runner_id: str) -> bool:
    """Check if a runner is connected."""
    return runner_id in _runner_connections


async def push_config_to_connected_runners(
    runner_registry: Any,
    runner_names: list[str] | None = None,
    ael_config: Any = None,
) -> dict[str, str]:
    """Push updated MCP config to connected runners.

    For each connected runner whose config may have changed, re-reads
    the runner.mcps from the registry (which includes the latest Redis
    state) and sends a config/push notification over the WebSocket.

    Args:
        runner_registry: RunnerRegistry (or PersistentRunnerRegistry) instance.
        runner_names: Optional list of runner names to push to.
                      If None, pushes to ALL connected runners.
        ael_config: Optional AELConfig for pre-configured MCP merging.

    Returns:
        Dict of runner_name -> status ("pushed" | "not_connected" | error string).
    """
    results: dict[str, str] = {}

    for runner_id, conn in list(_runner_connections.items()):
        runner = runner_registry.get(runner_id)
        if not runner:
            continue

        # Skip if we were given a specific list and this runner isn't in it
        if runner_names is not None and runner.name not in runner_names:
            continue

        # Build MCPs to push: config-based + API-provided (API takes precedence)
        mcps_to_push: dict[str, dict] = {}

        # 1. Get pre-configured MCPs from ael_config.runners
        if ael_config and hasattr(ael_config, "runners"):
            runner_def = ael_config.runners.get(runner.name)
            if runner_def and runner_def.mcp_servers:
                for mcp_name, mcp_def in runner_def.mcp_servers.items():
                    mcps_to_push[mcp_name] = {
                        "command": mcp_def.command,
                        "args": mcp_def.args,
                        "url": mcp_def.url,
                        "env": mcp_def.env,
                        "timeout": mcp_def.timeout,
                    }

        # 2. Merge with API-provided MCPs (these take precedence)
        if runner.mcps:
            mcps_to_push.update(runner.mcps)

        try:
            await _send_notification(
                conn.websocket,
                "config/push",
                {"mcps": mcps_to_push},
            )
            mcp_names = sorted(mcps_to_push.keys())
            logger.info(
                f"[config-push] Pushed config to runner '{runner.name}': "
                f"{len(mcp_names)} MCPs={mcp_names}"
            )
            results[runner.name] = "pushed"
        except Exception as e:
            logger.error(f"[config-push] Failed to push config to runner '{runner.name}': {e}")
            results[runner.name] = f"error: {e}"

    return results


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

    # CR-2: determine the runner TLS mode. In "proxy" mode TLS/mTLS is
    # terminated upstream and the trusted proxy forwards the verified runner
    # client-cert CN in RUNNER_CLIENT_CN_HEADER. In "none" mode (DEFAULT)
    # behavior is unchanged (plaintext, header not required).
    rest_config = getattr(websocket.app.state, "config", None)
    runner_tls_mode = getattr(rest_config, "runner_tls_mode", "none")

    # WebSocket request headers come from the ASGI connection scope; Starlette's
    # WebSocket.headers is a case-insensitive Headers view over that scope, so
    # the lookup works regardless of how the proxy cases the header name.
    forwarded_cn = websocket.headers.get(RUNNER_CLIENT_CN_HEADER)

    # Accept the connection
    await websocket.accept()

    runner_id: str | None = None
    this_conn: RunnerConnection | None = None  # H-7: our own session handle

    try:
        while True:
            # Receive message
            data = await websocket.receive_json()
            logger.info(f"Received message: {data}")

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

                # CR-2 proxy-mode enforcement: the forwarded client-cert CN must
                # be present AND match the registering runner's identity, in
                # ADDITION to the bearer-token check above. Reject (close) on
                # violation BEFORE registering the connection. In "none" mode
                # this block is skipped entirely (unchanged behavior).
                if runner_tls_mode == "proxy":
                    if not forwarded_cn:
                        logger.warning(
                            "Rejecting runner '%s': proxy mode requires %s header "
                            "(request bypassed the trusted proxy)",
                            name,
                            RUNNER_CLIENT_CN_HEADER,
                        )
                        await _send_error(
                            websocket,
                            msg_id,
                            -32001,
                            f"Missing {RUNNER_CLIENT_CN_HEADER} header (proxy mode)",
                        )
                        await websocket.close(
                            code=_WS_POLICY_VIOLATION_CODE,
                            reason=f"Missing {RUNNER_CLIENT_CN_HEADER} (proxy mode)",
                        )
                        return
                    if not _cn_matches_runner(forwarded_cn, runner.name, runner.id):
                        logger.warning(
                            "Rejecting runner '%s': forwarded CN %r does not match "
                            "runner identity (name=%s id=%s)",
                            name,
                            forwarded_cn,
                            runner.name,
                            runner.id,
                        )
                        await _send_error(
                            websocket,
                            msg_id,
                            -32001,
                            "Client cert CN does not match runner identity",
                        )
                        await websocket.close(
                            code=_WS_POLICY_VIOLATION_CODE,
                            reason="Client cert CN mismatch (proxy mode)",
                        )
                        return

                # Register connection (H-7: keep our own session handle)
                runner_id = runner.id
                this_conn = RunnerConnection(
                    runner_id=runner.id,
                    runner_name=runner.name,
                    websocket=websocket,
                )
                _runner_connections[runner_id] = this_conn
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
                # Runner sends {"available": [...], "unavailable": [...]}
                tools = params.get("available", params.get("tools", []))

                # DEC-160: Hard conflict enforcement — reject runner MCP servers
                # that are already registered on the CP.
                tool_registry = getattr(websocket.app.state, "tool_registry", None)
                if tool_registry and tools:
                    all_cp_servers = {
                        t.server_name for t in tool_registry.list_tools() if t.server_name
                    }
                    # Extract MCP server names from reported tools (format: mcp__tool)
                    reported_mcps: set[str] = set()
                    for tool_entry in tools:
                        t_name = runner_registry._get_tool_name(tool_entry)
                        if "__" in t_name:
                            reported_mcps.add(t_name.split("__")[0])

                    conflicts = reported_mcps & all_cp_servers
                    if conflicts:
                        conflict_msg = (
                            f"Runner '{name}' attempted to register MCP server(s) "
                            f"{sorted(conflicts)} which are already registered on the CP. "
                            f"A tool server cannot be registered on both CP and a runner. "
                            f"Register it on the CP (for tools that don't need local access) "
                            f"or on the runner (for tools that do), not both."
                        )
                        logger.warning(
                            "Runner registration conflict",
                            extra={
                                "runner_name": name,
                                "conflicting_servers": sorted(conflicts),
                                "event": "runner_registration_conflict",
                            },
                        )
                        await _send_error(websocket, msg_id, -32001, conflict_msg)
                        continue  # do not update available_tools

                runner_registry.update_available_tools(runner_id, tools)

                # Process structured unavailable list
                unavailable = params.get("unavailable", [])
                if unavailable:
                    runner_registry.update_unavailable_mcps(runner_id, unavailable)

                runner = runner_registry.get(runner_id)
                if runner:
                    logger.info(
                        f"Runner '{runner.name}' reported {len(tools)} tools, "
                        f"{len(unavailable)} unavailable MCPs"
                    )
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
                        await _send_response(
                            websocket,
                            msg_id,
                            {
                                "status": "success",
                                "output": result.output,
                            },
                        )
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
                    # Check for error (must be truthy, not just present)
                    error = data.get("error")
                    if error:
                        future.set_exception(Exception(error.get("message", "Unknown error")))
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
        # Cleanup on disconnect (H-7: session-scoped — never tear down a newer
        # reconnect that has already taken over this runner_id).
        if runner_id and this_conn is not None:
            _cleanup_runner_connection(runner_registry, runner_id, this_conn)
