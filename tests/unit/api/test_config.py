"""Tests for REST API configuration."""

import pytest

from ploston_core.api.config import APIKeyConfig, RESTConfig


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

