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
        from datetime import datetime, UTC
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
            websocket.send_json({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "runner/register",
                "params": {
                    "token": "ploston_runner_testtoken123",
                    "name": "test-runner",
                }
            })

            # Should receive success response
            data = websocket.receive_json()
            assert data.get("result", {}).get("status") == "ok"

            # Verify registry was called
            mock_registry.get_by_token.assert_called_once()
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
        assert marc_laptop.mcp_servers["filesystem"].args == [
            "@mcp/filesystem", "/Users/marc"
        ]

    def test_ael_config_empty_runners(self) -> None:
        """Test AELConfig with no runners configured."""
        config = AELConfig()
        assert config.runners == {}
