"""Tests for REST API configuration."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ploston_core.api.config import APIKeyConfig, RESTConfig
from ploston_core.api.routers.config import config_router


class TestConfigDiffEndpoint:
    """Tests for GET /config/diff endpoint."""

    @pytest.fixture
    def app(self) -> FastAPI:
        """Create test FastAPI app with config router."""
        app = FastAPI()
        app.include_router(config_router, prefix="/api/v1")
        return app

    @pytest.fixture
    def client(self, app: FastAPI) -> TestClient:
        """Create test client."""
        return TestClient(app)

    def test_diff_not_in_config_mode(self, app: FastAPI, client: TestClient) -> None:
        """Test diff returns empty when not in config mode."""
        # Set up mode manager in running mode
        mode_manager = MagicMock()
        mode_manager.is_configuration_mode.return_value = False
        app.state.mode_manager = mode_manager

        response = client.get("/api/v1/config/diff")
        assert response.status_code == 200
        data = response.json()
        assert data["in_config_mode"] is False
        assert data["has_changes"] is False
        assert data["diff"] == ""

    def test_diff_in_config_mode_no_changes(self, app: FastAPI, client: TestClient) -> None:
        """Test diff in config mode with no changes."""
        # Set up mode manager in config mode
        mode_manager = MagicMock()
        mode_manager.is_configuration_mode.return_value = True
        app.state.mode_manager = mode_manager

        # Set up staged config with no changes
        staged_config = MagicMock()
        staged_config.has_changes.return_value = False
        staged_config.get_diff.return_value = ""
        app.state.staged_config = staged_config

        response = client.get("/api/v1/config/diff")
        assert response.status_code == 200
        data = response.json()
        assert data["in_config_mode"] is True
        assert data["has_changes"] is False
        assert data["diff"] == ""

    def test_diff_in_config_mode_with_changes(self, app: FastAPI, client: TestClient) -> None:
        """Test diff in config mode with staged changes."""
        # Set up mode manager in config mode
        mode_manager = MagicMock()
        mode_manager.is_configuration_mode.return_value = True
        app.state.mode_manager = mode_manager

        # Set up staged config with changes
        staged_config = MagicMock()
        staged_config.has_changes.return_value = True
        staged_config.get_diff.return_value = "--- current\n+++ staged\n@@ -1 +1 @@\n-old\n+new"
        app.state.staged_config = staged_config

        response = client.get("/api/v1/config/diff")
        assert response.status_code == 200
        data = response.json()
        assert data["in_config_mode"] is True
        assert data["has_changes"] is True
        assert "--- current" in data["diff"]
        assert "+++ staged" in data["diff"]

    def test_diff_no_staged_config(self, app: FastAPI, client: TestClient) -> None:
        """Test diff returns error when staged config not available."""
        # Set up mode manager in config mode
        mode_manager = MagicMock()
        mode_manager.is_configuration_mode.return_value = True
        app.state.mode_manager = mode_manager
        # Don't set staged_config

        response = client.get("/api/v1/config/diff")
        assert response.status_code == 503
        assert "not available" in response.json()["detail"]


class TestAPIKeyConfig:
    """Tests for APIKeyConfig."""

    def test_default_scopes(self) -> None:
        """Test default scopes are set."""
        config = APIKeyConfig(name="test", key="secret123")
        assert config.scopes == ["read", "write", "execute"]

    def test_custom_scopes(self) -> None:
        """Test custom scopes."""
        config = APIKeyConfig(name="test", key="secret123", scopes=["read"])
        assert config.scopes == ["read"]


class TestRESTConfig:
    """Tests for RESTConfig."""

    def test_defaults(self) -> None:
        """Test default values."""
        config = RESTConfig()
        assert config.host == "0.0.0.0"
        assert config.port == 8080
        assert config.prefix == "/api/v1"
        assert config.title == "AEL REST API"
        assert config.version == "1.0.0"
        assert config.docs_enabled is True
        assert config.require_auth is False
        assert config.rate_limiting_enabled is False
        assert config.cors_enabled is True
        assert config.cors_origins == ["*"]

    def test_custom_values(self) -> None:
        """Test custom configuration."""
        config = RESTConfig(
            host="127.0.0.1",
            port=9000,
            prefix="/v2",
            require_auth=True,
            rate_limiting_enabled=True,
            requests_per_minute=50,
        )
        assert config.host == "127.0.0.1"
        assert config.port == 9000
        assert config.prefix == "/v2"
        assert config.require_auth is True
        assert config.rate_limiting_enabled is True
        assert config.requests_per_minute == 50

    def test_api_keys_list(self) -> None:
        """Test API keys configuration."""
        keys = [
            APIKeyConfig(name="admin", key="admin-key", scopes=["read", "write", "execute"]),
            APIKeyConfig(name="reader", key="reader-key", scopes=["read"]),
        ]
        config = RESTConfig(api_keys=keys)
        assert len(config.api_keys) == 2
        assert config.api_keys[0].name == "admin"
        assert config.api_keys[1].name == "reader"

    def test_execution_store_config(self) -> None:
        """Test execution store configuration."""
        config = RESTConfig(
            execution_store_max_records=500,
            execution_store_sqlite_path="/tmp/executions.db",
        )
        assert config.execution_store_max_records == 500
        assert config.execution_store_sqlite_path == "/tmp/executions.db"
