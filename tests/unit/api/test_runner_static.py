"""Tests for Runner static endpoints.

Implements S-186: Runner Static Endpoints
- UT-103: GET /runner/install.sh
- UT-104: GET /runner/ca.crt
- UT-105: WebSocket /runner/ws
- UT-120: Config-based runner MCPs
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ploston_core.api.routers.runner_static import runner_static_router
from ploston_core.config.models import (
    AELConfig,
    RunnerDefinition,
    RunnerMCPServerDefinition,
)


@pytest.fixture
def app() -> FastAPI:
    """Create test FastAPI app with runner static router."""
    app = FastAPI()
    app.include_router(runner_static_router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create test client."""
    return TestClient(app)


class TestInstallScript:
    """Tests for GET /runner/install.sh (UT-103)."""

    def test_get_install_script(self, client: TestClient) -> None:
        """Test getting the install script."""
        response = client.get("/runner/install.sh")
        assert response.status_code == 200
        assert "text/x-shellscript" in response.headers["content-type"]

        content = response.text
        assert "#!/bin/bash" in content
        assert "ploston-runner" in content
        assert "--cp" in content
        assert "--token" in content

    def test_install_script_has_uv_support(self, client: TestClient) -> None:
        """Test that install script supports uv package manager."""
        response = client.get("/runner/install.sh")
        content = response.text
        assert "uv tool install" in content

    def test_install_script_has_pip_fallback(self, client: TestClient) -> None:
        """Test that install script falls back to pip."""
        response = client.get("/runner/install.sh")
        content = response.text
        assert "pip install" in content

    def test_install_script_content_disposition(self, client: TestClient) -> None:
        """Test that install script has correct content disposition."""
        response = client.get("/runner/install.sh")
        assert "attachment" in response.headers.get("content-disposition", "")
        assert "install.sh" in response.headers.get("content-disposition", "")


class TestCACertificate:
    """Tests for GET /runner/ca.crt (UT-104)."""

    def test_get_ca_cert_not_configured(self, client: TestClient) -> None:
        """Test getting CA cert when not configured."""
        response = client.get("/runner/ca.crt")
        assert response.status_code == 503
        assert "not configured" in response.text

    def test_get_ca_cert_configured(self, app: FastAPI) -> None:
        """Test getting CA cert when configured."""
        # Configure CA certificate
        app.state.ca_certificate = """-----BEGIN CERTIFICATE-----
MIIBkTCB+wIJAKtest...
-----END CERTIFICATE-----
"""
        client = TestClient(app)

        response = client.get("/runner/ca.crt")
        assert response.status_code == 200
        assert "application/x-pem-file" in response.headers["content-type"]
        assert "BEGIN CERTIFICATE" in response.text


class TestWebSocket:
    """Tests for WebSocket /runner/ws (UT-105)."""

    def test_websocket_not_configured(self, client: TestClient) -> None:
        """Test WebSocket when server not configured."""
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/runner/ws"):
                pass  # Should not reach here

        # Verify it was closed with the expected code
        assert exc_info.value.code == 1013
        assert "not configured" in exc_info.value.reason

    def test_websocket_with_mock_registry(self, app: FastAPI) -> None:
        """Test WebSocket with mock registry configured."""
        from datetime import UTC, datetime
        from unittest.mock import MagicMock

        from ploston_core.runner_management.registry import Runner, RunnerStatus

        # Create a mock runner
        mock_runner = Runner(
            id="runner_test123",
            name="test-runner",
            created_at=datetime.now(UTC),
            status=RunnerStatus.DISCONNECTED,
            available_tools=[],
            mcps={},
        )

        # Create a mock registry
        mock_registry = MagicMock()
        mock_registry.get_by_token.return_value = mock_runner
        mock_registry.set_connected.return_value = mock_runner
        mock_registry.set_disconnected.return_value = mock_runner
        mock_registry.update_heartbeat.return_value = mock_runner
        mock_registry.update_available_tools.return_value = mock_runner
        mock_registry.get.return_value = mock_runner

        app.state.runner_registry = mock_registry

        client = TestClient(app)
        with client.websocket_connect("/runner/ws") as websocket:
            # Send runner/register message (JSON-RPC format)
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "runner/register",
                    "params": {
                        "token": "ploston_runner_testtoken123",
                        "name": "test-runner",
                    },
                }
            )

            # Should receive success response
            data = websocket.receive_json()
            assert data.get("result", {}).get("status") == "ok"

            # Verify registry was called
            mock_registry.get_by_token.assert_called_once()
            mock_registry.set_connected.assert_called_once()


class TestRunnerWSProxyMode:
    """Tests for CR-2 proxy-mode enforcement on /runner/ws.

    In proxy mode TLS/mTLS is terminated upstream by an ingress (k8s) or a
    bundled reverse-proxy (compose), which forwards the verified runner client
    cert CN in the X-Runner-Client-CN header. The CP must require and verify it.
    In none mode (default) behavior is unchanged (plaintext, header not required).
    """

    @staticmethod
    def _make_app_with_runner(tls_mode: str) -> FastAPI:
        from datetime import UTC, datetime
        from unittest.mock import MagicMock

        from ploston_core.api.config import RESTConfig
        from ploston_core.runner_management.registry import Runner, RunnerStatus

        app = FastAPI()
        app.include_router(runner_static_router)

        mock_runner = Runner(
            id="runner_test123",
            name="test-runner",
            created_at=datetime.now(UTC),
            status=RunnerStatus.DISCONNECTED,
            available_tools=[],
            mcps={},
        )
        mock_registry = MagicMock()
        mock_registry.get_by_token.return_value = mock_runner
        mock_registry.get.return_value = mock_runner

        app.state.runner_registry = mock_registry
        app.state.config = RESTConfig(runner_tls_mode=tls_mode)
        return app, mock_registry

    def test_proxy_mode_missing_header_rejected(self) -> None:
        """proxy mode + no X-Runner-Client-CN -> connection rejected, NOT registered."""
        from starlette.websockets import WebSocketDisconnect

        app, mock_registry = self._make_app_with_runner("proxy")
        client = TestClient(app)

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/runner/ws") as ws:
                ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "runner/register",
                        "params": {
                            "token": "ploston_runner_testtoken123",
                            "name": "test-runner",
                        },
                    }
                )
                # A clear policy error is delivered, then the socket is closed.
                err = ws.receive_json()
                assert "error" in err
                assert "X-Runner-Client-CN" in err["error"]["message"]
                # Next receive observes the close -> WebSocketDisconnect.
                ws.receive_json()

        # Runner must NOT be registered when proxy header is absent.
        mock_registry.set_connected.assert_not_called()

    def test_proxy_mode_cn_mismatch_rejected(self) -> None:
        """proxy mode + header CN != registering runner -> rejected, NOT registered."""
        from starlette.websockets import WebSocketDisconnect

        app, mock_registry = self._make_app_with_runner("proxy")
        client = TestClient(app)

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/runner/ws",
                headers={"X-Runner-Client-CN": "runner-someone-else"},
            ) as ws:
                ws.send_json(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "runner/register",
                        "params": {
                            "token": "ploston_runner_testtoken123",
                            "name": "test-runner",
                        },
                    }
                )
                err = ws.receive_json()
                assert "error" in err
                assert "CN" in err["error"]["message"]
                # Next receive observes the close -> WebSocketDisconnect.
                ws.receive_json()

        mock_registry.set_connected.assert_not_called()

    def test_proxy_mode_cn_match_accepted(self) -> None:
        """proxy mode + matching CN + valid token -> accepted/registered."""
        app, mock_registry = self._make_app_with_runner("proxy")
        mock_registry.set_connected.return_value = None
        client = TestClient(app)

        # EmbeddedCA issues runner certs with CN = f"runner-{runner_name}".
        with client.websocket_connect(
            "/runner/ws",
            headers={"X-Runner-Client-CN": "runner-test-runner"},
        ) as ws:
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "runner/register",
                    "params": {
                        "token": "ploston_runner_testtoken123",
                        "name": "test-runner",
                    },
                }
            )
            data = ws.receive_json()
            assert data.get("result", {}).get("status") == "ok"

        mock_registry.set_connected.assert_called_once()

    def test_none_mode_no_header_accepted(self) -> None:
        """none mode (default) + no header -> accepted as today (no regression)."""
        app, mock_registry = self._make_app_with_runner("none")
        mock_registry.set_connected.return_value = None
        client = TestClient(app)

        with client.websocket_connect("/runner/ws") as ws:
            ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "runner/register",
                    "params": {
                        "token": "ploston_runner_testtoken123",
                        "name": "test-runner",
                    },
                }
            )
            data = ws.receive_json()
            assert data.get("result", {}).get("status") == "ok"

        mock_registry.set_connected.assert_called_once()


class TestRunnerConfigModels:
    """Tests for runner config models (UT-120)."""

    def test_runner_mcp_server_definition_defaults(self) -> None:
        """Test RunnerMCPServerDefinition with defaults."""
        mcp_def = RunnerMCPServerDefinition()
        assert mcp_def.command is None
        assert mcp_def.args == []
        assert mcp_def.url is None
        assert mcp_def.env == {}
        assert mcp_def.timeout == 30

    def test_runner_mcp_server_definition_stdio(self) -> None:
        """Test RunnerMCPServerDefinition for stdio transport."""
        mcp_def = RunnerMCPServerDefinition(
            command="npx",
            args=["-y", "@mcp/filesystem", "/tmp"],
            env={"DEBUG": "1"},
        )
        assert mcp_def.command == "npx"
        assert mcp_def.args == ["-y", "@mcp/filesystem", "/tmp"]
        assert mcp_def.env == {"DEBUG": "1"}

    def test_runner_definition_defaults(self) -> None:
        """Test RunnerDefinition with defaults."""
        runner_def = RunnerDefinition()
        assert runner_def.mcp_servers == {}

    def test_runner_definition_with_mcp_servers(self) -> None:
        """Test RunnerDefinition with MCP servers."""
        runner_def = RunnerDefinition(
            mcp_servers={
                "filesystem": RunnerMCPServerDefinition(
                    command="npx",
                    args=["@mcp/filesystem", "/home/user"],
                ),
                "docker": RunnerMCPServerDefinition(
                    command="npx",
                    args=["@mcp/docker"],
                ),
            }
        )
        assert len(runner_def.mcp_servers) == 2
        assert "filesystem" in runner_def.mcp_servers
        assert "docker" in runner_def.mcp_servers
        assert runner_def.mcp_servers["filesystem"].command == "npx"

    def test_ael_config_runners_field(self) -> None:
        """Test AELConfig with runners field."""
        config = AELConfig(
            runners={
                "marc-laptop": RunnerDefinition(
                    mcp_servers={
                        "filesystem": RunnerMCPServerDefinition(
                            command="npx",
                            args=["@mcp/filesystem", "/Users/marc"],
                        ),
                    }
                ),
                "build-server": RunnerDefinition(
                    mcp_servers={
                        "filesystem": RunnerMCPServerDefinition(
                            command="npx",
                            args=["@mcp/filesystem", "/opt/builds"],
                        ),
                    }
                ),
            }
        )
        assert len(config.runners) == 2
        assert "marc-laptop" in config.runners
        assert "build-server" in config.runners

        marc_laptop = config.runners["marc-laptop"]
        assert "filesystem" in marc_laptop.mcp_servers
        assert marc_laptop.mcp_servers["filesystem"].args == ["@mcp/filesystem", "/Users/marc"]

    def test_ael_config_empty_runners(self) -> None:
        """Test AELConfig with no runners configured."""
        config = AELConfig()
        assert config.runners == {}


class TestPushConfigToConnectedRunners:
    """Tests for push_config_to_connected_runners."""

    @pytest.mark.asyncio
    async def test_push_to_connected_runner(self) -> None:
        """Push sends config/push notification to connected runner."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from ploston_core.api.routers.runner_static import push_config_to_connected_runners

        # Create a mock runner registry
        mock_runner = MagicMock()
        mock_runner.id = "r-1"
        mock_runner.name = "local"
        mock_runner.mcps = {"github": {"command": "docker"}}

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_runner

        # Create a mock connection
        mock_ws = AsyncMock()
        mock_conn = MagicMock()
        mock_conn.websocket = mock_ws

        with (
            patch(
                "ploston_core.api.routers.runner_static._runner_connections",
                {"r-1": mock_conn},
            ),
            patch(
                "ploston_core.api.routers.runner_static._send_notification",
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            results = await push_config_to_connected_runners(mock_registry, ["local"])

        assert results["local"] == "pushed"
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][1] == "config/push"
        assert "github" in call_args[0][2]["mcps"]

    @pytest.mark.asyncio
    async def test_push_skips_non_matching_runner(self) -> None:
        """Push skips runners not in the runner_names list."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from ploston_core.api.routers.runner_static import push_config_to_connected_runners

        mock_runner = MagicMock()
        mock_runner.id = "r-1"
        mock_runner.name = "other-runner"
        mock_runner.mcps = {}

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_runner

        mock_conn = MagicMock()
        mock_conn.websocket = AsyncMock()

        with (
            patch(
                "ploston_core.api.routers.runner_static._runner_connections",
                {"r-1": mock_conn},
            ),
            patch(
                "ploston_core.api.routers.runner_static._send_notification",
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            results = await push_config_to_connected_runners(mock_registry, ["local"])

        assert results == {}
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_push_all_when_names_none(self) -> None:
        """Push to ALL runners when runner_names is None."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from ploston_core.api.routers.runner_static import push_config_to_connected_runners

        mock_runner = MagicMock()
        mock_runner.id = "r-1"
        mock_runner.name = "local"
        mock_runner.mcps = {"fs": {"command": "npx"}}

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_runner

        mock_conn = MagicMock()
        mock_conn.websocket = AsyncMock()

        with (
            patch(
                "ploston_core.api.routers.runner_static._runner_connections",
                {"r-1": mock_conn},
            ),
            patch(
                "ploston_core.api.routers.runner_static._send_notification",
                new_callable=AsyncMock,
            ) as mock_send,
        ):
            results = await push_config_to_connected_runners(mock_registry, None)

        assert results["local"] == "pushed"
        mock_send.assert_called_once()
