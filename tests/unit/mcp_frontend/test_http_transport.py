"""Unit tests for HTTP transport."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from ploston_core.mcp_frontend.http_transport import HTTPTransport


class TestHTTPTransport:
    """Tests for HTTPTransport class."""

    @pytest.fixture
    def message_handler(self):
        """Create mock message handler."""
        handler = AsyncMock()
        handler.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": []},
        }
        return handler

    @pytest.fixture
    def transport(self, message_handler):
        """Create HTTP transport instance."""
        return HTTPTransport(
            message_handler=message_handler,
            host="127.0.0.1",
            port=8080,
            cors_origins=["*"],
        )

    @pytest.fixture
    def client(self, transport):
        """Create test client for the transport."""
        transport.start()
        return TestClient(transport.app)

    # App creation tests

    def test_app_creation(self, transport):
        """Test that app is created correctly."""
        app = transport.app
        assert app is not None
        # App should be cached
        assert transport.app is app

    def test_routes_registered(self, transport):
        """Test that routes are registered."""
        app = transport.app
        routes = [route.path for route in app.routes]
        assert "/mcp" in routes
        assert "/mcp/sse" in routes
        assert "/health" in routes

    # Health endpoint tests

    def test_health_endpoint(self, client):
        """Test health endpoint returns ok."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    # MCP endpoint tests

    def test_mcp_post_valid_request(self, client, message_handler):
        """Test POST /mcp with valid JSON-RPC request."""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        }
        response = client.post("/mcp", json=request)

        assert response.status_code == 200
        message_handler.assert_called_once_with(request)

    def test_mcp_post_invalid_json(self, client):
        """Test POST /mcp with invalid JSON."""
        response = client.post(
            "/mcp",
            content="not valid json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == -32700
        assert "Parse error" in data["error"]["message"]

    def test_mcp_post_notification_returns_204(self, client, message_handler):
        """Test POST /mcp with notification returns 204."""
        message_handler.return_value = None  # Notifications return None

        request = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }
        response = client.post("/mcp", json=request)

        assert response.status_code == 204

    def test_mcp_post_with_session_id(self, client, message_handler):
        """Test POST /mcp with session ID header."""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
        }
        response = client.post(
            "/mcp",
            json=request,
            headers={"X-MCP-Session-ID": "test-session-123"},
        )

        assert response.status_code == 200

    # CORS tests

    def test_cors_headers(self, client):
        """Test CORS headers are set."""
        response = client.options(
            "/mcp",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )

        assert "access-control-allow-origin" in response.headers

    # Session management tests

    def test_session_count_initially_zero(self, transport):
        """Test session count is initially zero."""
        assert transport.session_count == 0

    def test_start_stop(self, transport):
        """Test start and stop methods."""
        assert transport._running is False

        transport.start()
        assert transport._running is True

        transport.stop()
        assert transport._running is False


class TestHTTPTransportNotifications:
    """Tests for HTTP transport notification functionality."""

    @pytest.fixture
    def message_handler(self):
        """Create mock message handler."""
        return AsyncMock(return_value={"jsonrpc": "2.0", "id": 1, "result": {}})

    @pytest.fixture
    def transport(self, message_handler):
        """Create HTTP transport instance."""
        return HTTPTransport(message_handler=message_handler)

    @pytest.mark.asyncio
    async def test_send_notification_to_sessions(self, transport):
        """Test sending notification to all sessions."""
        transport.start()

        # Manually add a session queue
        queue = asyncio.Queue()
        transport._sessions["test-session"] = queue

        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/tools/list_changed",
        }

        await transport.send_notification(notification)

        # Check notification was added to queue
        assert not queue.empty()
        received = await queue.get()
        assert received == notification

    @pytest.mark.asyncio
    async def test_send_notification_to_multiple_sessions(self, transport):
        """Test sending notification to multiple sessions."""
        transport.start()

        # Add multiple session queues
        queue1 = asyncio.Queue()
        queue2 = asyncio.Queue()
        transport._sessions["session-1"] = queue1
        transport._sessions["session-2"] = queue2

        notification = {"jsonrpc": "2.0", "method": "test"}

        await transport.send_notification(notification)

        # Both queues should have the notification
        assert not queue1.empty()
        assert not queue2.empty()


class TestHTTPTransportConfiguration:
    """Tests for HTTP transport configuration."""

    def test_default_configuration(self):
        """Test default configuration values."""
        transport = HTTPTransport(message_handler=AsyncMock())

        assert transport._host == "0.0.0.0"
        assert transport._port == 8080
        assert transport._cors_origins == ["*"]
        assert transport._tls_enabled is False

    def test_custom_configuration(self):
        """Test custom configuration values."""
        transport = HTTPTransport(
            message_handler=AsyncMock(),
            host="127.0.0.1",
            port=9000,
            cors_origins=["http://localhost:3000"],
            tls_enabled=True,
            tls_cert_file="/path/to/cert.pem",
            tls_key_file="/path/to/key.pem",
        )

        assert transport._host == "127.0.0.1"
        assert transport._port == 9000
        assert transport._cors_origins == ["http://localhost:3000"]
        assert transport._tls_enabled is True
        assert transport._tls_cert_file == "/path/to/cert.pem"
        assert transport._tls_key_file == "/path/to/key.pem"


class TestHTTPTransportDualMode:
    """Tests for dual-mode operation (MCP + REST API)."""

    @pytest.fixture
    def message_handler(self):
        """Create mock message handler."""
        handler = AsyncMock()
        handler.return_value = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"tools": []},
        }
        return handler

    @pytest.fixture
    def mock_rest_app(self):
        """Create a mock FastAPI app for testing."""
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/health")
        def rest_health():
            return {"status": "ok", "source": "rest"}

        @app.get("/workflows")
        def list_workflows():
            return {"workflows": []}

        return app

    def test_rest_api_not_enabled_by_default(self, message_handler):
        """Test that REST API is not enabled by default."""
        transport = HTTPTransport(message_handler=message_handler)
        assert transport.rest_api_enabled is False
        assert transport.rest_api_prefix == "/api/v1"

    def test_rest_api_enabled_with_app(self, message_handler, mock_rest_app):
        """Test that REST API is enabled when app is provided."""
        transport = HTTPTransport(
            message_handler=message_handler,
            rest_app=mock_rest_app,
            rest_prefix="/api/v1",
        )
        assert transport.rest_api_enabled is True
        assert transport.rest_api_prefix == "/api/v1"

    def test_rest_api_custom_prefix(self, message_handler, mock_rest_app):
        """Test custom REST API prefix."""
        transport = HTTPTransport(
            message_handler=message_handler,
            rest_app=mock_rest_app,
            rest_prefix="/custom/api",
        )
        assert transport.rest_api_prefix == "/custom/api"

    def test_dual_mode_routes_registered(self, message_handler, mock_rest_app):
        """Test that both MCP and REST routes are registered."""
        transport = HTTPTransport(
            message_handler=message_handler,
            rest_app=mock_rest_app,
            rest_prefix="/api/v1",
        )
        transport.start()
        app = transport.app

        # Check MCP routes
        route_paths = [route.path for route in app.routes]
        assert "/mcp" in route_paths
        assert "/health" in route_paths

        # Check REST API mount
        mount_paths = [route.path for route in app.routes if hasattr(route, "app")]
        assert "/api/v1" in mount_paths

    def test_dual_mode_mcp_endpoint_works(self, message_handler, mock_rest_app):
        """Test that MCP endpoint works in dual mode."""
        transport = HTTPTransport(
            message_handler=message_handler,
            rest_app=mock_rest_app,
            rest_prefix="/api/v1",
        )
        transport.start()
        client = TestClient(transport.app)

        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert response.status_code == 200

    def test_dual_mode_rest_endpoint_works(self, message_handler, mock_rest_app):
        """Test that REST API endpoint works in dual mode."""
        transport = HTTPTransport(
            message_handler=message_handler,
            rest_app=mock_rest_app,
            rest_prefix="/api/v1",
        )
        transport.start()
        client = TestClient(transport.app)

        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "rest"

    def test_dual_mode_health_endpoint_is_mcp(self, message_handler, mock_rest_app):
        """Test that /health returns MCP health, not REST health."""
        transport = HTTPTransport(
            message_handler=message_handler,
            rest_app=mock_rest_app,
            rest_prefix="/api/v1",
        )
        transport.start()
        client = TestClient(transport.app)

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        # MCP health doesn't have "source" field
        assert "source" not in data
        assert data["status"] == "ok"
