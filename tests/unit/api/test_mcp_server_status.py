"""Tests for GET /api/v1/mcp-servers/{name}/status (T-D2/T-883)."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ploston_core.api.routers.mcp_servers import mcp_servers_router
from ploston_core.mcp.types import ServerStatus
from ploston_core.types import ConnectionStatus


def _make_manager(statuses: dict[str, ServerStatus]) -> MagicMock:
    manager = MagicMock()
    manager.get_status = MagicMock(return_value=statuses)
    return manager


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(mcp_servers_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestGetMCPServerStatus:
    """T-D2: GET /api/v1/mcp-servers/{name}/status returns server runtime state."""

    def test_returns_status_for_connected_server(self, app: FastAPI, client: TestClient) -> None:
        statuses = {
            "filesystem": ServerStatus(
                name="filesystem",
                status=ConnectionStatus.CONNECTED,
                tools=["fs_read", "fs_write", "fs_list"],
                last_connected="2026-04-23T18:21:02Z",
            ),
        }
        app.state.mcp_manager = _make_manager(statuses)

        response = client.get("/api/v1/mcp-servers/filesystem/status")

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "filesystem"
        assert body["status"] == "connected"
        assert body["tool_count"] == 3
        assert body["last_connected_at"] == "2026-04-23T18:21:02Z"
        assert body["error"] is None

    def test_returns_404_when_server_missing(self, app: FastAPI, client: TestClient) -> None:
        app.state.mcp_manager = _make_manager({})

        response = client.get("/api/v1/mcp-servers/ghost/status")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_returns_503_when_mcp_manager_unavailable(
        self, app: FastAPI, client: TestClient
    ) -> None:
        app.state.mcp_manager = None

        response = client.get("/api/v1/mcp-servers/filesystem/status")

        assert response.status_code == 503

    def test_reports_error_when_server_errored(self, app: FastAPI, client: TestClient) -> None:
        statuses = {
            "broken": ServerStatus(
                name="broken",
                status=ConnectionStatus.ERROR,
                tools=[],
                last_error="Connection refused",
            ),
        }
        app.state.mcp_manager = _make_manager(statuses)

        response = client.get("/api/v1/mcp-servers/broken/status")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "error"
        assert body["tool_count"] == 0
        assert body["error"] == "Connection refused"
        assert body["last_connected_at"] is None
