"""Tests for POST /api/v1/tools/refresh (T-D1/T-882)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ploston_core.api.routers.tools import tool_router
from ploston_core.registry.types import RefreshResult


def _make_registry(total_tools: int = 2, errors: dict | None = None) -> MagicMock:
    registry = MagicMock()
    registry.refresh = AsyncMock(
        return_value=RefreshResult(
            total_tools=total_tools, added=[], removed=[], updated=[], errors=errors or {}
        )
    )
    registry.refresh_server = AsyncMock(
        return_value=RefreshResult(
            total_tools=total_tools, added=[], removed=[], updated=[], errors=errors or {}
        )
    )
    registry.list_tools = MagicMock(return_value=[])
    return registry


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(tool_router, prefix="/api/v1")
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


class TestRefreshTools:
    """T-D1: refresh endpoint accepts ?server= query param."""

    def test_refresh_all_no_server_calls_refresh(self, app: FastAPI, client: TestClient) -> None:
        registry = _make_registry()
        app.state.tool_registry = registry

        response = client.post("/api/v1/tools/refresh")

        assert response.status_code == 200
        registry.refresh.assert_awaited_once()
        registry.refresh_server.assert_not_called()

    def test_refresh_with_server_param_calls_refresh_server(
        self, app: FastAPI, client: TestClient
    ) -> None:
        registry = _make_registry()
        app.state.tool_registry = registry

        response = client.post("/api/v1/tools/refresh?server=filesystem")

        assert response.status_code == 200
        registry.refresh_server.assert_awaited_once_with("filesystem")
        registry.refresh.assert_not_called()

    def test_refresh_with_server_not_found_returns_error_shape(
        self, app: FastAPI, client: TestClient
    ) -> None:
        registry = _make_registry(total_tools=0, errors={"ghost": "Server not found"})
        app.state.tool_registry = registry

        response = client.post("/api/v1/tools/refresh?server=ghost")

        assert response.status_code == 200
        body = response.json()
        assert "ghost" in body["servers"]
        assert body["servers"]["ghost"]["status"] == "error"
        assert body["servers"]["ghost"]["error"] == "Server not found"

    def test_refresh_returns_tool_refresh_response_shape(
        self, app: FastAPI, client: TestClient
    ) -> None:
        registry = _make_registry(total_tools=5)
        app.state.tool_registry = registry

        response = client.post("/api/v1/tools/refresh")

        assert response.status_code == 200
        body = response.json()
        assert body["refreshed"] == 5
        assert "servers" in body
