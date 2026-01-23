"""HTTP transport for MCP Frontend.

Implements MCP over HTTP with:
- POST /mcp for JSON-RPC requests
- GET /mcp/sse for server-sent events (notifications)
- GET /metrics for Prometheus metrics (when telemetry enabled)
- Optional REST API mounting for dual-mode operation
"""

import asyncio
import json
import uuid
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route

from ploston_core.telemetry import get_telemetry

if TYPE_CHECKING:
    from fastapi import FastAPI


class HTTPTransport:
    """HTTP transport for MCP server.

    Provides HTTP endpoints for MCP JSON-RPC communication:
    - POST /mcp: Handle JSON-RPC requests
    - GET /mcp/sse: Server-sent events for notifications

    Optionally mounts REST API for dual-mode operation:
    - /api/v1/* REST API endpoints (when rest_app provided)
    """

    def __init__(
        self,
        message_handler: Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any] | None]],
        host: str = "0.0.0.0",
        port: int = 8080,
        cors_origins: list[str] | None = None,
        tls_enabled: bool = False,
        tls_cert_file: str = "",
        tls_key_file: str = "",
        rest_app: "FastAPI | None" = None,
        rest_prefix: str = "/api/v1",
    ):
        """Initialize HTTP transport.

        Args:
            message_handler: Async function to handle JSON-RPC messages
            host: Host to bind to
            port: Port to listen on
            cors_origins: List of allowed CORS origins
            tls_enabled: Whether to enable TLS
            tls_cert_file: Path to TLS certificate file
            tls_key_file: Path to TLS key file
            rest_app: Optional FastAPI app to mount for REST API
            rest_prefix: URL prefix for REST API (default: /api/v1)
        """
        self._message_handler = message_handler
        self._host = host
        self._port = port
        self._cors_origins = cors_origins or ["*"]
        self._tls_enabled = tls_enabled
        self._tls_cert_file = tls_cert_file
        self._tls_key_file = tls_key_file
        self._rest_app = rest_app
        self._rest_prefix = rest_prefix

        # Client session management
        self._sessions: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._running = False
        self._app: Starlette | None = None

    def _create_app(self) -> Starlette:
        """Create Starlette application with routes.

        If rest_app is provided, mounts it at rest_prefix for dual-mode operation.
        """
        middleware = [
            Middleware(
                CORSMiddleware,
                allow_origins=self._cors_origins,
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_headers=["*"],
            )
        ]

        routes: list[Route | Mount] = [
            Route("/mcp", self._handle_mcp_request, methods=["POST"]),
            Route("/mcp/sse", self._handle_sse, methods=["GET"]),
            Route("/health", self._handle_health, methods=["GET"]),
            Route("/metrics", self._handle_metrics, methods=["GET"]),
        ]

        # Mount REST API if provided (dual-mode operation)
        if self._rest_app is not None:
            routes.append(Mount(self._rest_prefix, app=self._rest_app))

        return Starlette(routes=routes, middleware=middleware)

    async def _handle_mcp_request(self, request: Request) -> Response:
        """Handle POST /mcp JSON-RPC requests."""
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error: Invalid JSON"},
                },
                status_code=400,
            )

        # Get or create session ID from header
        session_id = request.headers.get("X-MCP-Session-ID")
        if session_id and session_id not in self._sessions:
            self._sessions[session_id] = asyncio.Queue()

        response = await self._message_handler(body)

        if response is None:
            # Notification - no response needed
            return Response(status_code=204)

        return JSONResponse(response)

    async def _handle_sse(self, request: Request) -> EventSourceResponse:
        """Handle GET /mcp/sse for server-sent events."""
        session_id = request.headers.get("X-MCP-Session-ID") or str(uuid.uuid4())

        if session_id not in self._sessions:
            self._sessions[session_id] = asyncio.Queue()

        async def event_generator():
            """Generate SSE events from session queue."""
            queue = self._sessions[session_id]
            try:
                while self._running:
                    try:
                        # Wait for notification with timeout
                        notification = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield {
                            "event": "message",
                            "data": json.dumps(notification),
                        }
                    except TimeoutError:
                        # Send keepalive
                        yield {"event": "ping", "data": ""}
            finally:
                # Cleanup session on disconnect
                self._sessions.pop(session_id, None)

        return EventSourceResponse(
            event_generator(),
            headers={"X-MCP-Session-ID": session_id},
        )

    async def _handle_health(self, request: Request) -> JSONResponse:
        """Handle GET /health for health checks."""
        return JSONResponse({"status": "ok"})

    async def _handle_metrics(self, request: Request) -> Response:
        """Handle GET /metrics for Prometheus metrics.

        Returns metrics in Prometheus text format if telemetry is enabled.
        """
        telemetry = get_telemetry()
        if telemetry is None or telemetry.get("config") is None:
            return PlainTextResponse(
                "# Telemetry not initialized\n",
                media_type="text/plain; charset=utf-8",
            )

        if not telemetry["config"].enabled or not telemetry["config"].metrics_enabled:
            return PlainTextResponse(
                "# Metrics disabled\n",
                media_type="text/plain; charset=utf-8",
            )

        # The PrometheusMetricReader automatically exposes metrics
        # via its internal HTTP server. For integration with our HTTP transport,
        # we use the generate_latest function from prometheus_client.
        try:
            from prometheus_client import REGISTRY, generate_latest

            metrics_output = generate_latest(REGISTRY)
            return Response(
                content=metrics_output,
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )
        except ImportError:
            return PlainTextResponse(
                "# prometheus_client not available\n",
                media_type="text/plain; charset=utf-8",
            )
        except Exception as e:
            return PlainTextResponse(
                f"# Error generating metrics: {e}\n",
                media_type="text/plain; charset=utf-8",
            )

    async def send_notification(self, notification: dict[str, Any]) -> None:
        """Send notification to all connected SSE clients."""
        for queue in self._sessions.values():
            await queue.put(notification)

    @property
    def app(self) -> Starlette:
        """Get or create the Starlette application."""
        if self._app is None:
            self._app = self._create_app()
        return self._app

    @property
    def session_count(self) -> int:
        """Get the number of active sessions."""
        return len(self._sessions)

    @property
    def rest_api_enabled(self) -> bool:
        """Check if REST API is mounted."""
        return self._rest_app is not None

    @property
    def rest_api_prefix(self) -> str:
        """Get the REST API prefix."""
        return self._rest_prefix

    def start(self) -> None:
        """Mark transport as running."""
        self._running = True

    def stop(self) -> None:
        """Mark transport as stopped."""
        self._running = False
